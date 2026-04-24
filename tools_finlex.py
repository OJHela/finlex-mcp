"""
tools_finlex.py – Finlex MCP tools (5 tools).

All tools return plain strings. Use Finnish/Swedish search terms only.
Citation format for statutes: "number/year" e.g. "55/2001".
Court codes: "okv" = Chancellor of Justice, "dpo" = Data Protection Ombudsman.
"""

import re
from typing import Optional

from finlex_client import (
    build_outline_from_xml,
    fetch_doc_xml,
    fetch_judgment_xml,
    fetch_statute_xml,
    format_result,
    list_judgments,
    list_statutes,
    parse_akn_xml,
)


def _uri_meta(uri: str) -> dict:
    parts = uri.rstrip("/").split("/")
    try:
        lang = parts[-1].replace("%40", "").replace("@", "").strip() or "fin"
        return {"number": parts[-2], "year": parts[-3], "lang": lang}
    except IndexError:
        return {}


COURT_MAP = {
    "okv": "chancellor-of-justice-decision",
    "chancellor-of-justice": "chancellor-of-justice-decision",
    "oikeuskansleri": "chancellor-of-justice-decision",
    "dpo": "data-protection-ombudsman-decision",
    "data-protection": "data-protection-ombudsman-decision",
    "tietosuoja": "data-protection-ombudsman-decision",
}

UNAVAILABLE_COURTS = {"kko", "kho", "ho", "hao"}


# ---------------------------------------------------------------------------
# Tool 1: search_statutes
# ---------------------------------------------------------------------------

async def search_statutes(
    start_year: int,
    end_year: int,
    statute_type: str = "act",
    lang: str = "fin",
    page: int = 1,
) -> str:
    """
    List statutes from Finlex for a year range. Returns citations to use with get_statute.

    Args:
        start_year: First year (e.g. 2024)
        end_year: Last year (e.g. 2024)
        statute_type: "act" (default), "decree", "decision", "announcement", "official-regulation"
        lang: "fin" (default) or "swe"
        page: Page number, 10 results per page

    Example: search_statutes(2024, 2024) → list of "number/year" citations
    """
    try:
        items = list_statutes(
            start_year=start_year,
            end_year=end_year,
            statute_type=statute_type,
            lang_and_version=f"{lang}@",
            page=page,
            limit=10,
        )
    except Exception as e:
        return f"ERROR: {e}"

    if not items:
        return f"No results: years {start_year}–{end_year}, type={statute_type}, lang={lang}."

    lines = [f"Statutes {start_year}–{end_year} | type={statute_type} | lang={lang} | page={page}"]
    for item in items:
        m = _uri_meta(item.get("akn_uri", ""))
        status = item.get("status", "")
        lines.append(f"  {m.get('number','?')}/{m.get('year','?')} [{status}]")

    lines.append(f"\nFetch text: get_statute(\"NUMBER/YEAR\")")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 2: get_outline
# ---------------------------------------------------------------------------

async def get_outline(citation: str, lang: str = "fin") -> str:
    """
    Fetch the chapter/section structure of a statute WITHOUT the full body text.
    Use this first when you need to navigate a long statute to a specific section.
    Returns section numbers and headings; then call get_statute(citation, section="N").

    Args:
        citation: "number/year" e.g. "1290/2002"
        lang: "fin" (default) or "swe"

    Example:
        get_outline("1290/2002")  → section list for Työttömyysturvalaki
        → then: get_statute("1290/2002", section="6")
    """
    citation = citation.strip()
    m = re.match(r"^(\d+)[/\-](\d{4})$", citation)
    if m:
        number, year = int(m.group(1)), int(m.group(2))
    else:
        m2 = re.match(r"^(\d{4})[/\-](\d+)$", citation)
        if m2:
            year, number = int(m2.group(1)), int(m2.group(2))
        else:
            return f'ERROR: Invalid citation "{citation}". Use "number/year" e.g. "1290/2002".'

    for doc_type in ("statute", "statute-consolidated"):
        try:
            xml = fetch_statute_xml(year, number, lang, doc_type)
            result = build_outline_from_xml(xml)
            if result.get("outline"):
                lines = []
                if result.get("title"):
                    lines.append(f"{citation} – {result['title']}")
                lines.append("")
                lines.append(result["outline"])
                lines.append(f'\nFetch a section: get_statute("{citation}", section="N")')
                lines.append(f'Fetch full text: get_statute("{citation}")')
                return "\n".join(lines)
        except Exception:
            continue

    return f'ERROR: Statute "{citation}" not found.'


# ---------------------------------------------------------------------------
# Tool 3: get_statute
# ---------------------------------------------------------------------------

async def get_statute(
    citation: str,
    section: Optional[str] = None,
    chunk: int = 1,
    lang: str = "fin",
) -> str:
    """
    Fetch a Finnish statute by citation. Handles long documents via chunked pagination.

    Args:
        citation: "number/year" e.g. "55/2001" (Employment Contracts Act)
        section: Optional paragraph number e.g. "3" or "3 §" — returns only that section
        chunk: Page number for long documents (default 1). Increment if response shows [PART 1/N].
        lang: "fin" (default) or "swe"

    Examples:
        get_statute("55/2001")           → Employment Contracts Act, part 1
        get_statute("55/2001", chunk=2)  → part 2
        get_statute("55/2001", section="3")  → only § 3
        get_statute("731/1999")          → Constitution of Finland
        get_statute("39/1889")           → Penal Code
    """
    citation = citation.strip()
    m = re.match(r"^(\d+)[/\-](\d{4})$", citation)
    if m:
        number, year = int(m.group(1)), int(m.group(2))
    else:
        m2 = re.match(r"^(\d{4})[/\-](\d+)$", citation)
        if m2:
            year, number = int(m2.group(1)), int(m2.group(2))
        else:
            return f'ERROR: Invalid citation "{citation}". Use "number/year" e.g. "55/2001".'

    def _try(doc_type: str) -> Optional[dict]:
        try:
            xml = fetch_statute_xml(year, number, lang, doc_type)
            return parse_akn_xml(xml, section_filter=section, chunk=chunk)
        except Exception:
            return None

    parsed = _try("statute")
    if not parsed or len(parsed.get("text", "")) < 50:
        parsed2 = _try("statute-consolidated")
        if parsed2 and len(parsed2.get("text", "")) > len((parsed or {}).get("text", "")):
            parsed = parsed2

    if not parsed:
        return f'ERROR: Statute "{citation}" not found.'

    next_call = f'get_statute("{citation}", chunk={chunk + 1})'
    return format_result(parsed, next_call=next_call)


# ---------------------------------------------------------------------------
# Tool 3: search_decisions
# ---------------------------------------------------------------------------

async def search_decisions(
    court: str = "okv",
    start_year: Optional[int] = None,
    end_year: Optional[int] = None,
    page: int = 1,
) -> str:
    """
    List decisions from Finlex. Returns year/number pairs to use with get_decision.

    Args:
        court: "okv" = Chancellor of Justice (default), "dpo" = Data Protection Ombudsman
        start_year: Optional filter
        end_year: Optional filter
        page: Page number, 10 results per page

    Note: Supreme Court (KKO) and Supreme Administrative Court (KHO) are NOT available.

    Example: search_decisions("dpo", 2024, 2024)
    """
    if court.lower() in UNAVAILABLE_COURTS:
        return (
            f'ERROR: "{court}" decisions are not available via Finlex open data API.\n'
            'Available courts: "okv" (Chancellor of Justice), "dpo" (Data Protection Ombudsman).'
        )

    judgment_type = COURT_MAP.get(court.lower(), court)

    try:
        items = list_judgments(
            judgment_type=judgment_type,
            start_year=start_year,
            end_year=end_year,
            page=page,
            limit=10,
        )
    except Exception as e:
        return f"ERROR: {e}"

    if not items:
        return f"No decisions: court={court}, years={start_year}–{end_year}, page={page}."

    lines = [f"Decisions | court={court} | {start_year or 'any'}–{end_year or 'any'} | page={page}"]
    for item in items:
        m = _uri_meta(item.get("akn_uri", ""))
        status = item.get("status", "")
        lines.append(f"  {m.get('year','?')}/{m.get('number','?')} [{status}]")

    lines.append(f'\nFetch text: get_decision(YEAR, NUMBER, court="{court}")')
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 4: get_decision
# ---------------------------------------------------------------------------

async def get_decision(
    year: int,
    number: int,
    court: str = "okv",
    chunk: int = 1,
) -> str:
    """
    Fetch a single court or authority decision.

    Args:
        year: Decision year e.g. 2025
        number: Decision number e.g. 11017
        court: "okv" = Chancellor of Justice (default), "dpo" = Data Protection Ombudsman
        chunk: Page number for long documents (default 1)

    Examples:
        get_decision(2025, 11017)               → OKV decision
        get_decision(2025, 2464, court="dpo")   → Data protection decision
        get_decision(2025, 11017, chunk=2)      → page 2
    """
    judgment_type = COURT_MAP.get(court.lower(), court)
    try:
        xml = fetch_judgment_xml(judgment_type, year, number, "fin")
        parsed = parse_akn_xml(xml, chunk=chunk)
        next_call = f"get_decision({year}, {number}, court=\"{court}\", chunk={chunk + 1})"
        return format_result(parsed, next_call=next_call)
    except Exception as e:
        return f'ERROR: Decision {year}/{number} (court="{court}") not found. {e}'


# ---------------------------------------------------------------------------
# Tool 5: get_proposal
# ---------------------------------------------------------------------------

async def get_proposal(
    year: int,
    number: int,
    lang: str = "fin",
    chunk: int = 1,
) -> str:
    """
    Fetch a Finnish government proposal (hallituksen esitys, HE).

    Args:
        year: Year e.g. 2024
        number: Proposal number without "HE" prefix e.g. 215
        lang: "fin" (default) or "swe"
        chunk: Page number for long documents (default 1). Government proposals are often very long.

    Examples:
        get_proposal(2024, 215)          → HE 215/2024, part 1
        get_proposal(2024, 215, chunk=2) → part 2
    """
    try:
        xml = fetch_doc_xml("government-proposal", year, number, lang)
        parsed = parse_akn_xml(xml, chunk=chunk)
        next_call = f"get_proposal({year}, {number}, chunk={chunk + 1})"
        return format_result(parsed, next_call=next_call)
    except Exception as e:
        return f"ERROR: Government proposal HE {number}/{year} not found. {e}"
