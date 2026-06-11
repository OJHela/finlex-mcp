"""
tools_finlex.py – Finlex MCP tools (8 tools).

All tools return plain strings. Use Finnish/Swedish search terms only.
Citation format for statutes: "number/year" e.g. "55/2001".
Court codes: "okv" = Chancellor of Justice, "dpo" = Data Protection Ombudsman.

Statute text strategy: the consolidated CURRENT version (statute-consolidated
@latest, amendments applied) is always preferred; the original as-enacted text
is the fallback. Every response states which version it contains.
"""

import re
from typing import List, Optional, Tuple

from finlex_client import (
    NotFoundError,
    build_outline_from_xml,
    fetch_doc_xml,
    fetch_judgment_xml,
    fetch_statute_xml,
    format_result,
    list_judgments,
    list_statutes,
    parse_akn_xml,
    search_sections_in_xml,
)


# ---------------------------------------------------------------------------
# Statute name → citation index
# Keys: lowercase Finnish name or common abbreviation
# Values: "number/year" citation string
# ---------------------------------------------------------------------------

STATUTE_INDEX = {
    # Constitutional & fundamental
    "perustuslaki": "731/1999",
    "suomen perustuslaki": "731/1999",
    # Criminal
    "rikoslaki": "39/1889",
    "rl": "39/1889",
    "esitutkintalaki": "805/2011",
    "pakkokeinolaki": "806/2011",
    "poliisilaki": "872/2011",
    # Procedure
    "oikeudenkäymiskaari": "4/1734",
    "ok": "4/1734",
    "laki oikeudenkäynnistä rikosasioissa": "689/1997",
    "rol": "689/1997",
    # Commercial
    "kauppakaari": "3/1734",
    "osakeyhtiölaki": "624/2006",
    "oyl": "624/2006",
    "laki avoimesta yhtiöstä ja kommandiittiyhtiöstä": "389/1988",
    "kilpailulaki": "948/2011",
    "kuluttajansuojalaki": "38/1978",
    "ksl": "38/1978",
    "laki saatavien perinnästä": "513/1999",
    "perintälaki": "513/1999",
    "yhdistyslaki": "503/1989",
    "säätiölaki": "487/2015",
    # Employment & social security
    "työsopimuslaki": "55/2001",
    "tsl": "55/2001",
    "työturvallisuuslaki": "738/2002",
    "työaikalaki": "872/2019",
    "vuosilomalaki": "162/2005",
    "yhteistoimintalaki": "1333/2021",
    "ytl": "1333/2021",
    "laki yhteistoiminnasta yrityksissä": "1333/2021",
    "laki yksityisyyden suojasta työelämässä": "759/2004",
    "työttömyysturvalaki": "1290/2002",
    "ttl": "1290/2002",
    "työttömyyskassalaki": "603/1984",
    "palkkaturvalaki": "866/1998",
    "sairausvakuutuslaki": "1224/2004",
    "svl": "1224/2004",
    # Pension
    "yrittäjien eläkelaki": "1272/2006",
    "yrittäjän eläkelaki": "1272/2006",
    "yel": "1272/2006",
    "maatalousyrittäjien eläkelaki": "1280/2006",
    "myel": "1280/2006",
    "työntekijän eläkelaki": "395/2006",
    "tyel": "395/2006",
    "kansaneläkelaki": "568/2007",
    "kel": "568/2007",
    # Administrative
    "hallintolaki": "434/2003",
    "hl": "434/2003",
    "hallintoprosessilaki": "808/2019",
    "laki oikeudenkäynnistä hallintoasioissa": "808/2019",
    "laki viranomaisten toiminnan julkisuudesta": "621/1999",
    "julkisuuslaki": "621/1999",
    "kuntalaki": "410/2015",
    # Data protection & privacy
    "tietosuojalaki": "1050/2018",
    "henkilötietolaki": "523/1999",
    # Family & persons
    "avioliittolaki": "234/1929",
    "vanhemmuuslaki": "775/2021",
    "isyyslaki": "11/2015",
    "laki lapsen huollosta ja tapaamisoikeudesta": "361/1983",
    "perintökaari": "40/1965",
    "pk": "40/1965",
    "holhoustoimilaki": "442/1999",
    "edunvalvonta": "442/1999",
    # Social welfare & health
    "lastensuojelulaki": "417/2007",
    "lsl": "417/2007",
    "sosiaalihuoltolaki": "1301/2014",
    "shl": "1301/2014",
    "laki sosiaalihuollon asiakkaan asemasta ja oikeuksista": "812/2000",
    "asiakaslaki": "812/2000",
    "terveydenhuoltolaki": "1326/2010",
    "laki potilaan asemasta ja oikeuksista": "785/1992",
    "potilaslaki": "785/1992",
    "mielenterveyslaki": "1116/1990",
    "päihdehuoltolaki": "41/1986",
    "varhaiskasvatuslaki": "540/2018",
    # Education
    "perusopetuslaki": "628/1998",
    "pol": "628/1998",
    "lukiolaki": "714/2018",
    "oppivelvollisuuslaki": "1214/2020",
    "ammattikorkeakoululaki": "932/2014",
    "yliopistolaki": "558/2009",
    # Immigration & citizenship
    "ulkomaalaislaki": "301/2004",
    "kansalaisuuslaki": "359/2003",
    # Property & housing
    "asunto-osakeyhtiölaki": "1599/2009",
    "aoyl": "1599/2009",
    "asuinhuoneiston vuokraamisesta annettu laki": "481/1995",
    "huoneenvuokralaki": "481/1995",
    "asuntokauppalaki": "843/1994",
    "maankäyttö- ja rakennuslaki": "132/1999",
    "mrl": "132/1999",
    "rakentamislaki": "751/2023",
    "kiinteistönmuodostamislaki": "554/1995",
    "maakaari": "540/1995",
    # Environment & traffic
    "ympäristönsuojelulaki": "527/2014",
    "jätelaki": "646/2011",
    "tieliikennelaki": "729/2018",
    "järjestyslaki": "612/2003",
    "pelastuslaki": "379/2011",
    # Insolvency & enforcement
    "konkurssilaki": "120/2004",
    "ulosottokaari": "705/2007",
    "uk": "705/2007",
    "laki yksityishenkilön velkajärjestelystä": "57/1993",
    "velkajärjestelylaki": "57/1993",
    # Tax
    "tuloverolaki": "1535/1992",
    "tvl": "1535/1992",
    "arvonlisäverolaki": "1501/1993",
    "avl": "1501/1993",
    "laki elinkeinotulon verottamisesta": "360/1968",
    "evl": "360/1968",
    # Procurement
    "laki julkisista hankinnoista ja käyttöoikeussopimuksista": "1397/2016",
    "hankintalaki": "1397/2016",
    # Non-discrimination & equality
    "yhdenvertaisuuslaki": "1325/2014",
    "tasa-arvolaki": "609/1986",
    # Contracts & damages
    "oikeustoimilaki": "228/1929",
    "laki varallisuusoikeudellisista oikeustoimista": "228/1929",
    "vahingonkorvauslaki": "412/1974",
    "vkl": "412/1974",
    "velkakirjalaki": "622/1947",
    "korkolaki": "633/1982",
}


def _find_in_index(query: str) -> List[tuple]:
    """
    Return list of (name, citation) tuples matching the query.
    Tries exact match first, then substring match.
    """
    q = query.strip().lower()
    # Exact match
    if q in STATUTE_INDEX:
        return [(q, STATUTE_INDEX[q])]
    # Substring match across all keys and values
    results = []
    for name, citation in STATUTE_INDEX.items():
        if q in name or q in citation:
            results.append((name, citation))
    # Deduplicate by citation
    seen = set()
    unique = []
    for name, citation in results:
        if citation not in seen:
            seen.add(citation)
            unique.append((name, citation))
    return unique


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_citation(citation: str) -> Optional[Tuple[int, int]]:
    """Parse 'number/year' (or 'year/number') → (number, year). None if invalid."""
    citation = citation.strip()
    m = re.match(r"^(\d+)[/\-](\d{4})$", citation)
    if m:
        number, year = int(m.group(1)), int(m.group(2))
        if 1700 <= year <= 2100:
            return number, year
    m2 = re.match(r"^(\d{4})[/\-](\d+)$", citation)
    if m2:
        year, number = int(m2.group(1)), int(m2.group(2))
        if 1700 <= year <= 2100:
            return number, year
    return None


VERSION_CURRENT = "Versio: ajantasainen konsolidoitu teksti (muutokset mukana)"
VERSION_ORIGINAL = "Versio: alkuperäinen säädös – myöhemmät muutokset EIVÄT näy tässä tekstissä"


async def _fetch_statute_xml_best(year: int, number: int, lang: str) -> Tuple[str, str]:
    """
    Fetch statute XML, preferring the consolidated CURRENT text.
    Returns (xml, version_note). Raises NotFoundError if neither version exists.
    """
    try:
        xml = await fetch_statute_xml(year, number, lang, "statute-consolidated")
        return xml, VERSION_CURRENT
    except NotFoundError:
        xml = await fetch_statute_xml(year, number, lang, "statute")
        return xml, VERSION_ORIGINAL


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
# Tool 1: find_statute
# ---------------------------------------------------------------------------

async def find_statute(query: str) -> str:
    """
    Look up a Finnish statute citation by name or abbreviation.
    Use this FIRST when you know the name of a law but not its citation number.
    Do NOT use search_statutes for name-based lookup — it only lists by year.

    Args:
        query: Finnish statute name or abbreviation e.g. "Työttömyysturvalaki", "YEL", "rikoslaki"

    Returns: Matching citations. Then call search_statute_text or get_outline.

    Examples:
        find_statute("työttömyysturvalaki") → citation 1290/2002
        find_statute("YEL")                → citation 1272/2006
        find_statute("rikoslaki")          → citation 39/1889
        find_statute("lastensuojelu")      → citation 417/2007
    """
    matches = _find_in_index(query)
    if not matches:
        return (
            f'No statute found for "{query}".\n'
            "Try the Finnish legal name, e.g. \"Työttömyysturvalaki\", \"Rikoslaki\", \"YEL\".\n"
            "If the law is not in the index, use search_statutes(year, year) to browse by year."
        )
    lines = [f'Statute lookup: "{query}"', ""]
    for name, citation in matches[:8]:
        lines.append(f"  {citation}  –  {name}")
    lines.append("")
    if len(matches) == 1:
        cit = matches[0][1]
        lines.append(f'Find sections by keyword: search_statute_text("{cit}", "avainsana")')
        lines.append(f'See structure:            get_outline("{cit}")')
        lines.append(f'Fetch text:               get_statute("{cit}")')
    else:
        lines.append("Use get_outline(citation) to see sections of the correct statute.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 2: search_statute_text
# ---------------------------------------------------------------------------

async def search_statute_text(citation: str, keyword: str, lang: str = "fin") -> str:
    """
    Search for a keyword inside a statute. Returns the sections that contain it,
    with their addresses — the FASTEST way to find the right section.

    Args:
        citation: "number/year" e.g. "1290/2002"
        keyword: Finnish/Swedish search word e.g. "yrittäjä" (English finds nothing)
        lang: "fin" (default) or "swe"

    Returns: Matching section addresses + snippets.
             Then call get_statute(citation, section="ADDRESS").

    Example:
        search_statute_text("1290/2002", "yrittäjä")
        → 1:6  Yrittäjä — "…Yrittäjäksi katsotaan tässä laissa…"
        → then: get_statute("1290/2002", section="1:6")
    """
    parsed_cit = _parse_citation(citation)
    if not parsed_cit:
        return f'ERROR: Invalid citation "{citation}". Use "number/year" e.g. "1290/2002".'
    number, year = parsed_cit

    try:
        xml, version_note = await _fetch_statute_xml_best(year, number, lang)
    except NotFoundError:
        return f'ERROR: Statute "{citation}" not found.'
    except Exception as e:
        return f"ERROR: {e}"

    result = search_sections_in_xml(xml, keyword)
    if result.get("error"):
        return f"ERROR: {result['error']}"

    title = result.get("title", "")
    matches = result.get("matches", [])
    total = result.get("total", 0)

    if not matches:
        return (
            f'No sections containing "{keyword}" in {citation}'
            + (f" – {title}" if title else "") + ".\n"
            "Check spelling (Finnish inflections matter: try the word stem, "
            'e.g. "yrittäj" instead of "yrittäjät").'
        )

    lines = [f'Search "{keyword}" in {citation}' + (f" – {title}" if title else "")]
    lines.append(version_note)
    shown = len(matches)
    lines.append(f"Matching sections: {total}" + (f" (showing {shown})" if total > shown else ""))
    lines.append("")
    for m in matches:
        head = f"  {m['address']}" + (f"  {m['heading']}" if m["heading"] else "")
        lines.append(head)
        lines.append(f"      {m['snippet']}")
    lines.append("")
    lines.append(f'Fetch a section: get_statute("{citation}", section="{matches[0]["address"]}")')
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 3: get_outline
# ---------------------------------------------------------------------------

async def get_outline(citation: str, lang: str = "fin") -> str:
    """
    Fetch the chapter/section structure of a statute WITHOUT the full body text.
    Use this to navigate a long statute when you don't have a keyword to search.
    Returns section addresses; then call get_statute(citation, section="ADDRESS").

    Args:
        citation: "number/year" e.g. "1290/2002"
        lang: "fin" (default) or "swe"

    Example:
        get_outline("1290/2002")  → section list for Työttömyysturvalaki
        → then: get_statute("1290/2002", section="1:6")
    """
    parsed_cit = _parse_citation(citation)
    if not parsed_cit:
        return f'ERROR: Invalid citation "{citation}". Use "number/year" e.g. "1290/2002".'
    number, year = parsed_cit

    try:
        xml, version_note = await _fetch_statute_xml_best(year, number, lang)
    except NotFoundError:
        return f'ERROR: Statute "{citation}" not found.'
    except Exception as e:
        return f"ERROR: {e}"

    result = build_outline_from_xml(xml)
    if not result.get("outline"):
        return f'ERROR: No section structure found in "{citation}". Try get_statute("{citation}").'

    lines = []
    if result.get("title"):
        lines.append(f"{citation} – {result['title']}")
    lines.append(version_note)
    lines.append("")
    lines.append(result["outline"])
    lines.append(f'\nFetch a section: get_statute("{citation}", section="ADDRESS")')
    lines.append(f'Search by keyword instead: search_statute_text("{citation}", "avainsana")')
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 4: get_statute
# ---------------------------------------------------------------------------

async def get_statute(
    citation: str,
    section: Optional[str] = None,
    chunk: int = 1,
    lang: str = "fin",
) -> str:
    """
    Fetch a Finnish statute by citation. Returns the consolidated CURRENT text
    (amendments applied) when available. Long documents are paginated.

    Args:
        citation: "number/year" e.g. "55/2001" (Employment Contracts Act)
        section: Optional section address:
                 - plain number "3" for laws with continuous numbering
                 - "CHAPTER:SECTION" e.g. "21:1" for laws numbered per chapter
                   (standard Finnish citation RL 21:1 → section="21:1")
        chunk: Page number for long documents (default 1). Increment if response shows [PART 1/N].
        lang: "fin" (default) or "swe"

    Examples:
        get_statute("55/2001")               → Employment Contracts Act, part 1
        get_statute("55/2001", chunk=2)      → part 2
        get_statute("731/1999", section="6") → Constitution § 6
        get_statute("39/1889", section="21:1") → Penal Code chapter 21 § 1 (tappo)
        get_statute("1290/2002", section="1:6") → Unemployment Security Act 1:6 (yrittäjä)
    """
    parsed_cit = _parse_citation(citation)
    if not parsed_cit:
        return f'ERROR: Invalid citation "{citation}". Use "number/year" e.g. "55/2001".'
    number, year = parsed_cit

    try:
        xml, version_note = await _fetch_statute_xml_best(year, number, lang)
    except NotFoundError:
        return f'ERROR: Statute "{citation}" not found.'
    except Exception as e:
        return f"ERROR: {e}"

    parsed = parse_akn_xml(xml, section_filter=section, chunk=chunk, extra_header=version_note)

    # Degenerate consolidated doc → retry with original text
    if not parsed.get("error") and len(parsed.get("text", "")) < 50 and version_note == VERSION_CURRENT:
        try:
            xml2 = await fetch_statute_xml(year, number, lang, "statute")
            parsed2 = parse_akn_xml(xml2, section_filter=section, chunk=chunk,
                                    extra_header=VERSION_ORIGINAL)
            if len(parsed2.get("text", "")) > len(parsed.get("text", "")):
                parsed = parsed2
        except Exception:
            pass

    next_call = f'get_statute("{citation}", chunk={chunk + 1})'
    return format_result(parsed, next_call=next_call)


# ---------------------------------------------------------------------------
# Tool 5: search_decisions
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
        items = await list_judgments(
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
# Tool 6: get_decision
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
        xml = await fetch_judgment_xml(judgment_type, year, number, "fin")
        parsed = parse_akn_xml(xml, chunk=chunk)
        next_call = f"get_decision({year}, {number}, court=\"{court}\", chunk={chunk + 1})"
        return format_result(parsed, next_call=next_call)
    except NotFoundError:
        return f'ERROR: Decision {year}/{number} (court="{court}") not found.'
    except Exception as e:
        return f'ERROR: Decision {year}/{number} (court="{court}") not found. {e}'


# ---------------------------------------------------------------------------
# Tool 7: get_proposal
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
        xml = await fetch_doc_xml("government-proposal", year, number, lang)
        parsed = parse_akn_xml(xml, chunk=chunk)
        next_call = f"get_proposal({year}, {number}, chunk={chunk + 1})"
        return format_result(parsed, next_call=next_call)
    except NotFoundError:
        return f"ERROR: Government proposal HE {number}/{year} not found."
    except Exception as e:
        return f"ERROR: Government proposal HE {number}/{year} not found. {e}"


# ---------------------------------------------------------------------------
# Tool 8: search_statutes
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
    NOTE: cannot search by name — use find_statute for that.

    Args:
        start_year: First year (e.g. 2024)
        end_year: Last year (e.g. 2024)
        statute_type: "act" (default), "decree", "decision", "announcement", "official-regulation"
        lang: "fin" (default) or "swe"
        page: Page number, 10 results per page

    Example: search_statutes(2024, 2024) → list of "number/year" citations
    """
    try:
        items = await list_statutes(
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
