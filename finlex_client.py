"""
finlex_client.py – HTTP client for the Finlex avoin data API v1.

Base URL: https://opendata.finlex.fi/finlex/avoindata/v1
All responses are Akoma Ntoso XML (namespace http://docs.oasis-open.org/legaldocml/ns/akn/3.0).
User-Agent header is MANDATORY on every request.

Valid document types (from API spec):
  act:      statute | statute-consolidated | statute-foreign-language-translation | statute-sami-translation
  judgment: chancellor-of-justice-decision | data-protection-ombudsman-decision
  doc:      government-proposal | collective-agreement-general-applicability-decision |
            legal-literature-references | tax-treaty-consolidated | trade-union-center-agreement |
            treaty-metadata | treaty | authority-regulation

Key API facts discovered empirically:
  - statute-consolidated requires a version tag: {lang}@latest returns the CURRENT
    consolidated text (amendments applied). Plain {lang}@ returns 404 for consolidated.
  - Original statutes use plain {lang}@.
  - Some old statutes need a -001 number suffix (e.g. 39-001 for Rikoslaki 39/1889).
"""

import asyncio
import re
import time
import xml.etree.ElementTree as ET
from collections import OrderedDict
from typing import Dict, Iterator, List, Optional, Tuple, Union

import httpx

BASE_URL = "https://opendata.finlex.fi/finlex/avoindata/v1"

HEADERS = {
    "User-Agent": "finlex-mcp/2.0",
    "Accept-Encoding": "gzip",
}

TIMEOUT = 30.0
CHUNK_SIZE = 20_000  # Characters per chunk — keeps responses well inside context limits
CHAR_LIMIT = CHUNK_SIZE  # Backwards-compatible alias

AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
FINLEX_NS = "http://data.finlex.fi/schema/finlex"
NS = {"akn": AKN_NS, "finlex": FINLEX_NS}


class NotFoundError(Exception):
    """Document does not exist in the Finlex API (404, cached)."""


# ---------------------------------------------------------------------------
# In-memory TTL + LRU cache
# Documents are immutable for hours (statutes change rarely); caching makes
# the outline → section → chunk workflow cost ONE network fetch instead of
# one per tool call, and absorbs rate-limit pressure.
# ---------------------------------------------------------------------------

_CACHE_MAX = 48          # ~48 XML docs ≈ tens of MB worst case
_TTL_DOC = 6 * 3600      # statute/judgment/proposal XML
_TTL_LIST = 15 * 60      # list endpoints (new decisions appear)
_TTL_MISS = 3600         # negative (404) entries

_MISS = object()
_cache: "OrderedDict[str, Tuple[float, object]]" = OrderedDict()


def _cache_get(key: str):
    item = _cache.get(key)
    if item is None:
        return _MISS
    expires, value = item
    if time.time() > expires:
        _cache.pop(key, None)
        return _MISS
    _cache.move_to_end(key)
    return value


def _cache_put(key: str, value, ttl: float) -> None:
    _cache[key] = (time.time() + ttl, value)
    _cache.move_to_end(key)
    while len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)


# ---------------------------------------------------------------------------
# Low-level async HTTP — shared client per event loop (connection pooling)
# ---------------------------------------------------------------------------

_clients: Dict[int, httpx.AsyncClient] = {}


def _client() -> httpx.AsyncClient:
    loop_id = id(asyncio.get_running_loop())
    cl = _clients.get(loop_id)
    if cl is None or cl.is_closed:
        cl = httpx.AsyncClient(timeout=TIMEOUT, headers=HEADERS)
        _clients[loop_id] = cl
    return cl


async def _get(url: str, params: Optional[dict] = None, accept: str = "application/xml") -> httpx.Response:
    """GET with retries on HTTP 429."""
    cl = _client()
    for attempt in range(3):
        r = await cl.get(url, params=params, headers={"Accept": accept})
        if r.status_code == 429:
            await asyncio.sleep(2 ** attempt)
            continue
        r.raise_for_status()
        return r
    raise RuntimeError(f"Rate limited after 3 attempts: {url}")


async def _get_xml_cached(url: str) -> str:
    """Fetch a document URL with caching. Raises NotFoundError on cached 404."""
    cached = _cache_get(url)
    if cached is not _MISS:
        if cached is None:
            raise NotFoundError(url)
        return cached
    try:
        r = await _get(url)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            _cache_put(url, None, _TTL_MISS)
            raise NotFoundError(url)
        raise
    _cache_put(url, r.text, _TTL_DOC)
    return r.text


async def _get_json_cached(url: str, params: Optional[dict] = None) -> Union[List, dict]:
    """GET JSON list endpoint with caching."""
    if params is None:
        params = {}
    params["format"] = "json"
    key = url + "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    cached = _cache_get(key)
    if cached is not _MISS:
        return cached
    r = await _get(url, params=params, accept="application/json")
    data = r.json()
    _cache_put(key, data, _TTL_LIST)
    return data


# ---------------------------------------------------------------------------
# XML helpers
# ---------------------------------------------------------------------------

def _tag(local: str) -> str:
    """Return Clark-notation tag name."""
    return f"{{{AKN_NS}}}{local}"


def _localname(elem: ET.Element) -> str:
    return elem.tag.rsplit("}", 1)[-1]


def _get_text_recursive(elem: ET.Element) -> str:
    """Recursively extract all text from an XML element."""
    parts = []
    if elem.text and elem.text.strip():
        parts.append(elem.text.strip())
    for child in elem:
        parts.append(_get_text_recursive(child))
        if child.tail and child.tail.strip():
            parts.append(child.tail.strip())
    return " ".join(p for p in parts if p)


def _norm_num(s: str) -> str:
    """
    Normalize a chapter/section number for comparison.
    '3 §' → '3', '5 a §' → '5a', '21 luku' → '21', '2 kap.' → '2'
    """
    s = s.lower().replace("§", "")
    for word in ("luku", "kapitlet", "kap.", "kap"):
        s = s.replace(word, "")
    return re.sub(r"[\s.]+", "", s)


# Section filter "21:1" or "21.1" = chapter 21, section 1 (Finnish citation RL 21:1)
_CHAPTER_SECTION_RE = re.compile(
    r"^\s*(\d+\s*[a-zA-Z]?)\s*[:.]\s*(\d+\s*[a-zA-Z]?)\s*§?\s*$"
)


def _split_section_filter(f: str) -> Tuple[Optional[str], str]:
    """'21:1' → ('21', '1');  '6' / '6 §' → (None, '6')"""
    m = _CHAPTER_SECTION_RE.match(f)
    if m:
        return _norm_num(m.group(1)), _norm_num(m.group(2))
    return None, _norm_num(f)


def _walk_sections(doc_elem: ET.Element) -> Iterator[Tuple[Optional[str], str, ET.Element]]:
    """
    Yield (chapter_norm, chapter_label, section_elem) in document order.
    Chapter context comes from the nearest preceding chapter/hcontainer with a num.
    """
    current_norm: Optional[str] = None
    current_label = ""
    for elem in doc_elem.iter():
        tag = _localname(elem)
        if tag in ("chapter", "hcontainer"):
            num_el = elem.find(_tag("num"))
            head_el = elem.find(_tag("heading"))
            if num_el is not None and num_el.text:
                current_norm = _norm_num(num_el.text)
                parts = [num_el.text.strip()]
                if head_el is not None and head_el.text:
                    parts.append(head_el.text.strip())
                current_label = " – ".join(parts)
        elif tag == "section":
            yield current_norm, current_label, elem


def _section_num(elem: ET.Element) -> str:
    num_el = elem.find(_tag("num"))
    return num_el.text.strip() if num_el is not None and num_el.text else ""


def _section_heading(elem: ET.Element) -> str:
    head_el = elem.find(_tag("heading"))
    return head_el.text.strip() if head_el is not None and head_el.text else ""


def _has_duplicate_numbering(doc_elem: ET.Element) -> bool:
    """True when the same section number appears in more than one chapter."""
    seen: Dict[str, Optional[str]] = {}
    for chap, _label, sec in _walk_sections(doc_elem):
        num = _norm_num(_section_num(sec))
        if not num:
            continue
        if num in seen and seen[num] != chap:
            return True
        seen[num] = chap
    return False


def _find_section(
    doc_elem: ET.Element, section_filter: str
) -> Tuple[Optional[ET.Element], str, List[str]]:
    """
    Find a section by filter ('6', '6 §', '21:1').
    Returns (element, chapter_label, other_chapters_with_same_number).
    """
    chap_target, sec_target = _split_section_filter(section_filter)
    matches: List[Tuple[Optional[str], str, ET.Element]] = []
    all_chapters_with_num: List[str] = []

    for chap, label, sec in _walk_sections(doc_elem):
        if _norm_num(_section_num(sec)) == sec_target:
            if chap is not None and chap not in all_chapters_with_num:
                all_chapters_with_num.append(chap)
            if chap_target is None or chap == chap_target:
                matches.append((chap, label, sec))

    if not matches:
        return None, "", all_chapters_with_num

    chap, label, elem = matches[0]
    others = [c for c in all_chapters_with_num if c != chap]
    return elem, label, others


def _find_chapter(doc_elem: ET.Element, chapter_filter: str) -> Optional[ET.Element]:
    """Find the first <chapter>/<hcontainer> whose num matches chapter_filter."""
    target = _norm_num(chapter_filter)
    for tag_name in ("chapter", "hcontainer"):
        for elem in doc_elem.iter(_tag(tag_name)):
            num_el = elem.find(_tag("num"))
            if num_el is not None and num_el.text and _norm_num(num_el.text) == target:
                return elem
    return None


def _list_addresses(doc_elem: ET.Element, limit: int = 60) -> Tuple[List[str], int]:
    """Return (chapter-qualified section addresses, total count)."""
    dup = _has_duplicate_numbering(doc_elem)
    out: List[str] = []
    total = 0
    for chap, _label, sec in _walk_sections(doc_elem):
        num = _norm_num(_section_num(sec))
        if not num:
            continue
        total += 1
        if len(out) < limit:
            out.append(f"{chap}:{num}" if dup and chap else num)
    return out, total


# ---------------------------------------------------------------------------
# Text extraction (body rendering)
# ---------------------------------------------------------------------------

def _extract_text_from_element(elem: ET.Element, indent: int = 0) -> List[str]:
    """
    Walk an AKN element tree and return list of human-readable text lines.
    Handles: chapter, section, subsection, paragraph, content, p, heading,
    num, hcontainer, header, judgmentBody, decision, mainBody, introduction, rationale.
    """
    lines = []
    prefix = "  " * indent
    tag = _localname(elem)

    if tag in ("chapter", "hcontainer"):
        num_el = elem.find(_tag("num"))
        head_el = elem.find(_tag("heading"))
        label = ""
        if num_el is not None and num_el.text:
            label += num_el.text.strip()
        if head_el is not None and head_el.text:
            label += (" – " if label else "") + head_el.text.strip()
        if label:
            lines.append(f"\n{prefix}{'=' * 40}")
            lines.append(f"{prefix}{label.upper()}")
            lines.append(f"{prefix}{'=' * 40}")
        for child in elem:
            lines.extend(_extract_text_from_element(child, indent))

    elif tag == "section":
        num_el = elem.find(_tag("num"))
        head_el = elem.find(_tag("heading"))
        label = ""
        if num_el is not None and num_el.text:
            label += num_el.text.strip()
        if head_el is not None and head_el.text:
            label += ("  " if label else "") + head_el.text.strip()
        if label:
            lines.append(f"\n{prefix}{label}")
        for child in elem:
            if child.tag not in (_tag("num"), _tag("heading")):
                lines.extend(_extract_text_from_element(child, indent + 1))

    elif tag == "subsection":
        for child in elem:
            lines.extend(_extract_text_from_element(child, indent + 1))

    elif tag == "paragraph":
        num_el = elem.find(_tag("num"))
        num = num_el.text.strip() if num_el is not None and num_el.text else ""
        content = ""
        for child in elem:
            if child.tag != _tag("num"):
                content += _get_text_recursive(child) + " "
        text = (num + "  " + content.strip()).strip()
        if text:
            lines.append(f"{prefix}{text}")

    elif tag in ("content", "intro"):
        text = _get_text_recursive(elem).strip()
        if text:
            lines.append(f"{prefix}{text}")

    elif tag == "p":
        text = _get_text_recursive(elem).strip()
        if text:
            lines.append(f"{prefix}{text}")

    elif tag in ("heading",):
        text = _get_text_recursive(elem).strip()
        if text:
            lines.append(f"\n{prefix}--- {text} ---")

    elif tag in ("judgmentBody", "mainBody", "body"):
        for child in elem:
            lines.extend(_extract_text_from_element(child, indent))

    elif tag in ("decision", "introduction", "rationale", "tblock"):
        head_el = elem.find(_tag("heading"))
        if head_el is not None:
            heading_text = _get_text_recursive(head_el).strip()
            if heading_text:
                lines.append(f"\n{prefix}--- {heading_text} ---")
        for child in elem:
            if child.tag != _tag("heading"):
                lines.extend(_extract_text_from_element(child, indent + 1))

    elif tag == "header":
        for child in elem:
            lines.extend(_extract_text_from_element(child, indent))

    elif tag in ("preface", "preamble"):
        for child in elem:
            lines.extend(_extract_text_from_element(child, indent))

    elif tag == "formula":
        text = _get_text_recursive(elem).strip()
        if text:
            lines.append(f"\n{prefix}{text}")

    elif tag == "table":
        lines.append(f"{prefix}[Taulukko – katso alkuperäinen dokumentti]")

    elif tag in ("meta", "references", "identification", "proprietary",
                 "classification", "keyword", "signatures", "conclusions"):
        pass  # skip metadata

    else:
        # Fallback: recurse into unknown containers
        for child in elem:
            lines.extend(_extract_text_from_element(child, indent))

    return lines


def _build_outline(doc_elem: ET.Element) -> str:
    """
    Compact section/chapter index. When section numbers repeat across chapters
    (most large Finnish codes), entries use the CHAPTER:SECTION address that
    get_statute's section= parameter accepts (e.g. '21:1' for RL 21:1).
    """
    dup = _has_duplicate_numbering(doc_elem)
    lines: List[str] = []
    if dup:
        lines.append('(osoitemuoto LUKU:PYKÄLÄ — esim. section="21:1")')

    last_label = None
    for chap, label, sec in _walk_sections(doc_elem):
        if label and label != last_label:
            lines.append(f"[{label}]")
            last_label = label
        num_raw = _section_num(sec)
        if not num_raw:
            continue
        heading = _section_heading(sec)
        if dup and chap:
            addr = f"{chap}:{_norm_num(num_raw)}"
        else:
            addr = num_raw
        lines.append(f"  {addr}" + (f"  {heading}" if heading else ""))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Keyword search inside a statute
# ---------------------------------------------------------------------------

def search_sections_in_xml(xml_content: str, keyword: str, max_results: int = 15) -> dict:
    """
    Search all sections for a keyword (case-insensitive).
    Returns {"title", "matches": [{"address", "heading", "snippet"}], "total"}.
    """
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as e:
        return {"error": f"XML parse error: {e}", "matches": [], "total": 0}

    doc_elem = root.find(_tag("act")) or root.find(_tag("judgment")) or root.find(_tag("doc"))
    if doc_elem is None:
        doc_elem = root

    title = ""
    preface = doc_elem.find(_tag("preface"))
    if preface is not None:
        t = preface.find(f".//{_tag('docTitle')}")
        if t is not None:
            title = (t.text or "").strip()

    dup = _has_duplicate_numbering(doc_elem)
    needle = keyword.casefold()
    matches = []
    total = 0

    for chap, _label, sec in _walk_sections(doc_elem):
        text = _get_text_recursive(sec)
        idx = text.casefold().find(needle)
        if idx < 0:
            continue
        total += 1
        if len(matches) >= max_results:
            continue
        num = _norm_num(_section_num(sec))
        addr = f"{chap}:{num}" if dup and chap else num
        start = max(0, idx - 60)
        snippet = ("…" if start > 0 else "") + text[start: idx + 90].strip() + "…"
        matches.append({
            "address": addr,
            "heading": _section_heading(sec),
            "snippet": snippet,
        })

    return {"title": title, "matches": matches, "total": total}


# ---------------------------------------------------------------------------
# AKN parsing
# ---------------------------------------------------------------------------

def parse_akn_xml(
    xml_content: str,
    section_filter: Optional[str] = None,
    chunk: int = 1,
    extra_header: Optional[str] = None,
) -> dict:
    """
    Parse Akoma Ntoso XML and return a dict with:
      - title / number / year / date_issued / language / doc_type
      - text: str      (requested chunk, or the filtered section)
      - chunk / total_chunks / total_length / truncated
      - outline: str   (section index, only on chunk 1 of multi-chunk docs)

    section_filter accepts '6', '6 §' and chapter-qualified '21:1' (RL 21:1).
    """
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as e:
        return {"error": f"XML parse error: {e}", "text": "", "chunk": 1,
                "total_chunks": 1, "total_length": 0, "truncated": False}

    act_el = root.find(_tag("act"))
    judgment_el = root.find(_tag("judgment"))
    doc_el = root.find(_tag("doc"))

    doc_elem = act_el if act_el is not None else (judgment_el if judgment_el is not None else doc_el)
    if doc_elem is None:
        doc_elem = root

    doc_type = "act" if act_el is not None else ("judgment" if judgment_el is not None else "doc")

    # Metadata
    meta = {}
    identification = doc_elem.find(f".//{_tag('identification')}")
    if identification is not None:
        frbrwork = identification.find(_tag("FRBRWork"))
        if frbrwork is not None:
            frbrdate = frbrwork.find(_tag("FRBRdate"))
            meta["date_issued"] = frbrdate.get("date", "") if frbrdate is not None else ""
            frbrnumber = frbrwork.find(_tag("FRBRnumber"))
            meta["number"] = frbrnumber.get("value", "") if frbrnumber is not None else ""
            meta["year"] = meta["date_issued"][:4] if meta.get("date_issued") else ""

        frbrexpr = identification.find(_tag("FRBRExpression"))
        if frbrexpr is not None:
            lang_el = frbrexpr.find(_tag("FRBRlanguage"))
            meta["language"] = lang_el.get("language", "") if lang_el is not None else ""
            for d in frbrexpr.findall(_tag("FRBRdate")):
                if d.get("name") == "dateIssued":
                    meta["date_issued"] = d.get("date", "")
                    meta["year"] = meta["date_issued"][:4] if meta["date_issued"] else ""

    # Title and document number
    title = ""
    doc_number = ""
    preface = doc_elem.find(_tag("preface"))
    if preface is not None:
        docnum_el = preface.find(f".//{_tag('docNumber')}")
        if docnum_el is not None:
            doc_number = (docnum_el.text or "").strip()
        doctitle_el = preface.find(f".//{_tag('docTitle')}")
        if doctitle_el is not None:
            title = (doctitle_el.text or "").strip()

    header = doc_elem.find(_tag("header"))
    if header is not None and not title:
        doctitle_el = header.find(f".//{_tag('docTitle')}")
        if doctitle_el is not None:
            title = (doctitle_el.text or "").strip()

    header_lines = []
    if doc_number:
        header_lines.append(f"Säädösnumero: {doc_number}")
    if title:
        header_lines.append(f"Otsikko: {title}")
    if meta.get("date_issued"):
        header_lines.append(f"Annettu: {meta['date_issued']}")
    if meta.get("language"):
        lang_map = {"fin": "suomi", "swe": "ruotsi"}
        header_lines.append(f"Kieli: {lang_map.get(meta['language'], meta['language'])}")
    if extra_header:
        header_lines.append(extra_header)

    base = {
        "title": title,
        "number": doc_number or meta.get("number", ""),
        "year": meta.get("year", ""),
        "date_issued": meta.get("date_issued", ""),
        "language": meta.get("language", ""),
        "doc_type": doc_type,
    }

    # --- Section filtering ---
    if section_filter:
        matched, chapter_label, other_chapters = _find_section(doc_elem, section_filter)
        if matched is None:
            # Whole-chapter request? ('2 luku' / bare chapter number)
            matched = _find_chapter(doc_elem, section_filter)
            chapter_label = ""

        if matched is None:
            addresses, total = _list_addresses(doc_elem)
            addr_str = ", ".join(addresses)
            if total > len(addresses):
                addr_str += f" … (yhteensä {total} pykälää — käytä get_outline)"
            return dict(base, **{
                "error": (
                    f"Pykälää/lukua '{section_filter}' ei löydy dokumentista. "
                    f"Saatavilla: {addr_str or '(ei pykäliä)'}"
                ),
                "text": "", "chunk": 1, "total_chunks": 1,
                "total_length": 0, "truncated": False,
            })

        if chapter_label:
            header_lines.append(f"Luku: {chapter_label}")

        section_lines = _extract_text_from_element(matched)
        full_text = "\n".join(header_lines) + "\n\n" + "\n".join(section_lines)
        full_text = full_text.strip()

        if other_chapters:
            chap_target, sec_target = _split_section_filter(section_filter)
            if chap_target is None:
                alts = ", ".join(f'"{c}:{sec_target}"' for c in other_chapters[:10])
                full_text += (
                    f"\n\n[HUOM: pykälänumero {sec_target} § esiintyy myös muissa luvuissa. "
                    f"Muut osoitteet: {alts}]"
                )

        return dict(base, **{
            "text": full_text, "chunk": 1, "total_chunks": 1,
            "total_length": len(full_text), "truncated": False,
        })

    # --- Full document with chunked pagination ---
    text_lines = []
    body_el = doc_elem.find(_tag("body"))
    judgment_body_el = doc_elem.find(_tag("judgmentBody"))
    main_body_el = doc_elem.find(_tag("mainBody"))

    for container in [preface, body_el, judgment_body_el, main_body_el]:
        if container is not None:
            text_lines.extend(_extract_text_from_element(container))

    full_text = "\n".join(header_lines) + "\n\n" + "\n".join(text_lines)
    full_text = full_text.strip()

    total_length = len(full_text)
    total_chunks = max(1, (total_length + CHUNK_SIZE - 1) // CHUNK_SIZE)
    chunk = max(1, min(chunk, total_chunks))
    start = (chunk - 1) * CHUNK_SIZE
    chunk_text = full_text[start: start + CHUNK_SIZE]

    # Attach compact section outline to chunk 1 of multi-chunk documents so
    # the model can navigate directly to a section instead of paging through.
    outline = ""
    if chunk == 1 and total_chunks > 1:
        outline = _build_outline(doc_elem)

    return dict(base, **{
        "text": chunk_text,
        "chunk": chunk,
        "total_chunks": total_chunks,
        "total_length": total_length,
        "truncated": total_chunks > 1,
        "outline": outline,
    })


def build_outline_from_xml(xml_content: str) -> dict:
    """Parse XML and return title + compact section outline. No body text extracted."""
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as e:
        return {"error": str(e), "outline": ""}

    doc_elem = root.find(_tag("act")) or root.find(_tag("judgment")) or root.find(_tag("doc"))
    if doc_elem is None:
        doc_elem = root

    title = ""
    doc_number = ""
    preface = doc_elem.find(_tag("preface"))
    if preface is not None:
        docnum_el = preface.find(f".//{_tag('docNumber')}")
        if docnum_el is not None:
            doc_number = (docnum_el.text or "").strip()
        doctitle_el = preface.find(f".//{_tag('docTitle')}")
        if doctitle_el is not None:
            title = (doctitle_el.text or "").strip()

    return {"title": title, "number": doc_number, "outline": _build_outline(doc_elem)}


# ---------------------------------------------------------------------------
# Public API functions (all async, all cached)
# ---------------------------------------------------------------------------

async def list_statutes(
    start_year: Optional[int] = None,
    end_year: Optional[int] = None,
    statute_type: Optional[str] = None,
    lang_and_version: Optional[str] = None,
    page: int = 1,
    limit: int = 10,
    doc_type: str = "statute",
) -> List[dict]:
    """
    Return a list of statute URIs from the /act/{doc_type}/list endpoint.
    Each item: {"akn_uri": str, "status": str}
    """
    url = f"{BASE_URL}/akn/fi/act/{doc_type}/list"
    params: dict = {"page": page, "limit": min(limit, 10)}
    if start_year:
        params["startYear"] = start_year
    if end_year:
        params["endYear"] = end_year
    if statute_type:
        params["typeStatute"] = statute_type
    if lang_and_version:
        params["langAndVersion"] = lang_and_version
    return await _get_json_cached(url, params)


async def fetch_statute_xml(
    year: int,
    number: Union[int, str],
    lang: str = "fin",
    doc_type: str = "statute",
) -> str:
    """
    Fetch full statute XML as string (cached).
    - statute-consolidated uses {lang}@latest → the CURRENT consolidated text.
    - original statute uses {lang}@.
    - Old statutes may need a -001 number suffix (e.g. 39-001 for Rikoslaki).
    """
    version = f"{lang}@latest" if doc_type == "statute-consolidated" else f"{lang}@"
    url = f"{BASE_URL}/akn/fi/act/{doc_type}/{year}/{number}/{version}"
    try:
        return await _get_xml_cached(url)
    except NotFoundError:
        url2 = f"{BASE_URL}/akn/fi/act/{doc_type}/{year}/{number}-001/{version}"
        return await _get_xml_cached(url2)


async def list_judgments(
    judgment_type: str,
    start_year: Optional[int] = None,
    end_year: Optional[int] = None,
    page: int = 1,
    limit: int = 10,
) -> List[dict]:
    """
    Return a list of judgment URIs.
    Valid judgment_type values:
      - chancellor-of-justice-decision
      - data-protection-ombudsman-decision
    """
    url = f"{BASE_URL}/akn/fi/judgment/{judgment_type}/list"
    params: dict = {"page": page, "limit": min(limit, 10)}
    if start_year:
        params["startYear"] = start_year
    if end_year:
        params["endYear"] = end_year
    return await _get_json_cached(url, params)


async def fetch_judgment_xml(
    judgment_type: str,
    year: int,
    number: int,
    lang: str = "fin",
) -> str:
    """Fetch full judgment XML as string (cached)."""
    url = f"{BASE_URL}/akn/fi/judgment/{judgment_type}/{year}/{number}/{lang}@"
    return await _get_xml_cached(url)


async def list_docs(
    doc_type: str,
    start_year: Optional[int] = None,
    end_year: Optional[int] = None,
    page: int = 1,
    limit: int = 10,
) -> List[dict]:
    """
    Return a list of document URIs.
    Valid doc_type values: government-proposal, collective-agreement-general-applicability-decision,
    legal-literature-references, tax-treaty-consolidated, trade-union-center-agreement,
    treaty-metadata, treaty, authority-regulation
    """
    url = f"{BASE_URL}/akn/fi/doc/{doc_type}/list"
    params: dict = {"page": page, "limit": min(limit, 10)}
    if start_year:
        params["startYear"] = start_year
    if end_year:
        params["endYear"] = end_year
    return await _get_json_cached(url, params)


async def fetch_doc_xml(
    doc_type: str,
    year: int,
    number: int,
    lang: str = "fin",
) -> str:
    """Fetch full document XML as string (cached)."""
    url = f"{BASE_URL}/akn/fi/doc/{doc_type}/{year}/{number}/{lang}@"
    return await _get_xml_cached(url)


def format_result(parsed: dict, next_call: str = "") -> str:
    """Format a parsed AKN result into a clean string for the MCP tool response."""
    if "error" in parsed:
        return f"ERROR: {parsed['error']}"
    text = parsed.get("text", "")
    chunk = parsed.get("chunk", 1)
    total_chunks = parsed.get("total_chunks", 1)
    outline = parsed.get("outline", "")
    if total_chunks > 1:
        if chunk < total_chunks:
            continuation = f" | NEXT: {next_call}" if next_call else ""
            footer = f"\n\n[PART {chunk}/{total_chunks}{continuation}]"
        else:
            footer = f"\n\n[PART {chunk}/{total_chunks} — END]"
        # Append outline on chunk 1 so model can jump to any section directly.
        # Cap its size so huge codes (Rikoslaki ~900 sections) don't bloat the response.
        if chunk == 1 and outline:
            if len(outline) > 4000:
                outline = outline[:4000] + "\n  … (index truncated — call get_outline(citation) for the full structure)"
            footer += f"\n\nSECTION INDEX (use section= param to fetch one section):\n{outline}"
        text += footer
    return text
