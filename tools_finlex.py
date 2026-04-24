"""
tools_finlex.py – Finlex MCP -työkalut (6 kpl).

Kaikki työkalut ovat asynkronisia funktioita, jotka palauttavat merkkijonon.
Finlex API:n User-Agent on pakollinen jokaisessa pyynnössä.

Huomio dokumenttityypeistä:
  Säädökset (act):
    - statute            = lait ja asetukset (Suomen säädöskokoelma)
    - statute-consolidated = konsolidoitu versio (voimassa oleva teksti)
  Ratkaisut (judgment):
    - chancellor-of-justice-decision       = Oikeuskanslerinviraston päätökset
    - data-protection-ombudsman-decision   = Tietosuojavaltuutetun päätökset
  Asiakirjat (doc):
    - government-proposal = Hallituksen esitykset (HE)
"""

import re
from typing import Optional

from finlex_client import (
    CHUNK_SIZE,
    fetch_doc_xml,
    fetch_judgment_xml,
    fetch_statute_xml,
    format_result,
    list_docs,
    list_judgments,
    list_statutes,
    parse_akn_xml,
)


# ---------------------------------------------------------------------------
# Apufunktiot
# ---------------------------------------------------------------------------

def _uri_to_metadata(uri: str) -> dict:
    """
    Pura Finlex-URI:sta vuosi, numero ja kieli.
    Esim: .../akn/fi/act/statute/2024/123/fin@ -> {year:2024, number:123, lang:fin}
    """
    parts = uri.rstrip("/").split("/")
    result = {}
    try:
        lang_ver = parts[-1]
        lang = lang_ver.replace("%40", "").replace("@", "").strip()
        result["lang"] = lang if lang else "fin"
        number = parts[-2]
        year = parts[-3]
        result["number"] = number
        result["year"] = year
        doc_type = parts[-4]
        result["doc_type"] = doc_type
    except IndexError:
        pass
    return result


# ---------------------------------------------------------------------------
# Työkalu 1: Hae säädösluettelo
# ---------------------------------------------------------------------------

async def search_statutes(
    start_year: int,
    end_year: int,
    statute_type: str = "act",
    lang: str = "fin",
    page: int = 1,
) -> str:
    """
    Hae säädösluettelo Finlexistä vuosivälin perusteella.

    Palauttaa listan säädöksistä (URI, vuosi, numero, kieli, tila).
    Käytä tätä työkalua, kun haluat selata tiettynä vuonna annettuja säädöksiä.

    Parametrit:
        start_year: Aloitusvuosi (esim. 2024)
        end_year: Lopetusvu​osi (esim. 2024)
        statute_type: Säädöstyyppi. Tuetut arvot:
            - "act" = laki
            - "decree" = asetus
            - "decision" = päätös
            - "announcement" = ilmoitus
            - "official-regulation" = virallinen asetus
            (Muut: announcement, budget, confirmation, declaration, instructions,
             letter, list, notice, order, rules-of-procedure, statement)
        lang: Kielikoodi. "fin" = suomi (oletus), "swe" = ruotsi
        page: Sivunumero hakutuloksissa (oletus: 1, sivukoko: 10)

    Palauttaa: Merkkijono, jossa luettelo säädöksistä (URI ja tila).

    Esimerkki: search_statutes(start_year=2024, end_year=2024, statute_type="act")
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
        if not items:
            return f"Ei tuloksia haulla: vuodet {start_year}–{end_year}, tyyppi '{statute_type}', kieli '{lang}'."

        lines = [f"Säädöshaku: {start_year}–{end_year}, tyyppi={statute_type}, kieli={lang}, sivu={page}",
                 f"Tuloksia: {len(items)} kpl\n"]
        for item in items:
            uri = item.get("akn_uri", "")
            status = item.get("status", "")
            meta = _uri_to_metadata(uri)
            year = meta.get("year", "?")
            number = meta.get("number", "?")
            item_lang = meta.get("lang", "?")
            lines.append(f"  {number}/{year} ({item_lang}) – {status}")
            lines.append(f"    URI: {uri}")

        lines.append(f"\nHae säädöksen teksti: get_statute_text(year=VUOSI, number=NUMERO)")
        return "\n".join(lines)

    except Exception as e:
        return f"Virhe säädöshaossa: {e}"


# ---------------------------------------------------------------------------
# Työkalu 2: Hae säädöksen teksti
# ---------------------------------------------------------------------------

async def get_statute_text(
    year: int,
    number: int,
    lang: str = "fin",
    section: Optional[str] = None,
    chunk: int = 1,
) -> str:
    """
    Hae yksittäisen säädöksen teksti Finlexistä.

    Hakee säädöksen vuoden ja numeron perusteella (Suomen säädöskokoelman viittausmuoto).

    PITKÄT DOKUMENTIT – SIVUTUS:
    Pitkät säädökset palautetaan osissa (chunk). Vastauksessa näkyy esim.
    "[OSA 1/4]", jolloin hae loput osilla chunk=2, chunk=3, chunk=4.
    Jokainen osa on noin 20 000 merkkiä.

    YKSITTÄINEN PYKÄLÄ:
    Hae tietty pykälä section-parametrilla, esim. section="3" tai section="3 §".
    Tällöin palautetaan vain kyseinen pykälä ilman sivutusta.

    Parametrit:
        year: Säädöksen antamisvuosi (esim. 2001)
        number: Säädöksen numero (esim. 55)
        lang: Kielikoodi – "fin" = suomi (oletus), "swe" = ruotsi
        section: Valinnainen pykäläfiltteri (esim. "3" tai "3 §").
                 Jos annettu, palautetaan vain kyseinen pykälä/luku.
        chunk: Osan numero kokonaisdokumentin sivutuksessa (oletus: 1).
               Käytetään vain, kun section ei ole annettu.

    Palauttaa: Säädöksen teksti tai pyydetty pykälä merkkijonona.

    Esimerkki:
        get_statute_text(year=2001, number=55)              → Työsopimuslaki (osa 1)
        get_statute_text(year=2001, number=55, chunk=2)     → Työsopimuslaki (osa 2)
        get_statute_text(year=2001, number=55, section="3") → Vain 3 § Työsopimuslaista
        get_statute_text(year=1889, number=39)              → Rikoslaki
        get_statute_text(year=1999, number=731)             → Suomen perustuslaki
    """
    def _fetch_and_parse(doc_type: str) -> Optional[dict]:
        try:
            xml_content = fetch_statute_xml(year, number, lang, doc_type)
            return parse_akn_xml(xml_content, section_filter=section, chunk=chunk)
        except Exception:
            return None

    parsed = _fetch_and_parse("statute")

    # Fall back to consolidated version if original is empty or not found
    if parsed is None or len(parsed.get("text", "")) < 50:
        parsed2 = _fetch_and_parse("statute-consolidated")
        if parsed2 and len(parsed2.get("text", "")) > len(parsed.get("text", "") if parsed else ""):
            parsed = parsed2

    if parsed is None:
        return f"Säädöstä {number}/{year} ei löydy."

    return format_result(parsed)


# ---------------------------------------------------------------------------
# Työkalu 3: Hae säädös viittauksen perusteella
# ---------------------------------------------------------------------------

async def get_statute_by_citation(
    citation: str,
    lang: str = "fin",
    section: Optional[str] = None,
    chunk: int = 1,
) -> str:
    """
    Hae säädös suomalaisen säädösviittauksen perusteella (muoto numero/vuosi).

    Tunnistaa automaattisesti "numero/vuosi" -muodon (esim. "55/2001") ja
    hakee kyseisen säädöksen tekstin Finlexistä.

    Parametrit:
        citation: Säädösviittaus muodossa "numero/vuosi" (esim. "55/2001", "731/1999")
        lang: Kielikoodi – "fin" = suomi (oletus), "swe" = ruotsi
        section: Valinnainen pykäläfiltteri (esim. "3" tai "3 §").
        chunk: Osan numero sivutuksessa (oletus: 1).

    Palauttaa: Säädöksen teksti tai virheilmoitus.

    Esimerkkejä:
        get_statute_by_citation("55/2001")                      → Työsopimuslaki (osa 1)
        get_statute_by_citation("55/2001", section="3")         → Vain 3 § Työsopimuslaista
        get_statute_by_citation("731/1999")                     → Suomen perustuslaki
        get_statute_by_citation("39/1889")                      → Rikoslaki
        get_statute_by_citation("417/2007")                     → Lastensuojelulaki
    """
    citation = citation.strip()
    match = re.match(r"^(\d+)[/\-](\d{4})$", citation)
    if match:
        number = int(match.group(1))
        year = int(match.group(2))
    else:
        match2 = re.match(r"^(\d{4})[/\-](\d+)$", citation)
        if match2:
            year = int(match2.group(1))
            number = int(match2.group(2))
        else:
            return (
                f"Virheellinen säädösviittaus: '{citation}'. "
                "Käytä muotoa 'numero/vuosi', esim. '55/2001'."
            )

    return await get_statute_text(year=year, number=number, lang=lang, section=section, chunk=chunk)


# ---------------------------------------------------------------------------
# Työkalu 4: Hae oikeusratkaisuja
# ---------------------------------------------------------------------------

async def search_case_law(
    court: str = "chancellor-of-justice",
    start_year: Optional[int] = None,
    end_year: Optional[int] = None,
    page: int = 1,
) -> str:
    """
    Hae oikeusratkaisuja Finlexistä.

    HUOM: Finlexin avoin data -rajapinta sisältää tällä hetkellä seuraavat ratkaisutyypit:
      - "chancellor-of-justice" = Oikeuskanslerin ratkaisut (OKV)
      - "data-protection"       = Tietosuojavaltuutetun päätökset

    KKO:n (Korkein oikeus) ja KHO:n (Korkein hallinto-oikeus) ennakkopäätökset
    EIVÄT ole saatavilla tämän rajapinnan kautta.

    Parametrit:
        court: Tuomioistuimen/viranomaisen lyhenne:
            - "chancellor-of-justice" = Oikeuskansleri (oletus)
            - "data-protection"       = Tietosuojavaltuutettu
        start_year: Aloitusvuosi (valinnainen)
        end_year: Lopetus​vuosi (valinnainen)
        page: Sivunumero (oletus: 1)

    Palauttaa: Lista ratkaisuista URI:neen ja metatietoineen.

    Esimerkki:
        search_case_law(court="chancellor-of-justice", start_year=2024, end_year=2024)
        search_case_law(court="data-protection", start_year=2023, end_year=2024)
    """
    court_map = {
        "chancellor-of-justice": "chancellor-of-justice-decision",
        "okv": "chancellor-of-justice-decision",
        "oikeuskansleri": "chancellor-of-justice-decision",
        "chancellor": "chancellor-of-justice-decision",
        "data-protection": "data-protection-ombudsman-decision",
        "tietosuoja": "data-protection-ombudsman-decision",
        "tso": "data-protection-ombudsman-decision",
        "ombudsman": "data-protection-ombudsman-decision",
        "kko": None,
        "kho": None,
        "ho": None,
        "hao": None,
    }

    judgment_type = court_map.get(court.lower())
    if judgment_type is None:
        if court.lower() in ("kko", "kho", "ho", "hao"):
            return (
                f"Tuomioistuimen '{court}' ratkaisut eivät ole saatavilla Finlexin avoin data -rajapinnan kautta.\n"
                "Saatavilla olevat ratkaisutyypit:\n"
                "  - 'chancellor-of-justice' = Oikeuskanslerin ratkaisut\n"
                "  - 'data-protection' = Tietosuojavaltuutetun päätökset\n\n"
                "KKO:n ja KHO:n ennakkopäätökset löytyvät osoitteesta: https://www.finlex.fi/fi/oikeus/"
            )
        judgment_type = court

    try:
        items = list_judgments(
            judgment_type=judgment_type,
            start_year=start_year,
            end_year=end_year,
            page=page,
            limit=10,
        )
        if not items:
            return (
                f"Ei ratkaisuja haulla: tuomioistuin='{court}', "
                f"vuodet={start_year}–{end_year}, sivu={page}."
            )

        lines = [
            f"Ratkaisuhaku: {judgment_type}",
            f"Vuodet: {start_year or '(kaikki)'}–{end_year or '(kaikki)'}",
            f"Tuloksia: {len(items)} kpl\n",
        ]
        for item in items:
            uri = item.get("akn_uri", "")
            status = item.get("status", "")
            meta = _uri_to_metadata(uri)
            year = meta.get("year", "?")
            number = meta.get("number", "?")
            lines.append(f"  Ratkaisu {year}/{number} – {status}")
            lines.append(f"    URI: {uri}")

        lines.append(
            f"\nHae ratkaisun teksti: get_decision_text(year=VUOSI, number=NUMERO, court='{court}')"
        )
        return "\n".join(lines)

    except Exception as e:
        return f"Virhe ratkaisuhaussa: {e}"


# ---------------------------------------------------------------------------
# Työkalu 5: Hae oikeuspäätös
# ---------------------------------------------------------------------------

async def get_decision_text(
    year: int,
    number: int,
    court: str = "chancellor-of-justice",
    chunk: int = 1,
) -> str:
    """
    Hae yksittäisen oikeuspäätöksen tai viranomaispäätöksen teksti Finlexistä.

    Hakee päätöksen vuoden ja numeron perusteella.

    PITKÄT DOKUMENTIT – SIVUTUS:
    Jos päätös on pitkä, vastauksessa näkyy esim. "[OSA 1/3]".
    Hae loput osilla chunk=2, chunk=3 jne.

    Parametrit:
        year: Päätöksen vuosi (esim. 2025)
        number: Päätöksen numero (esim. 11017)
        court: Tuomioistuin/viranomainen:
            - "chancellor-of-justice" = Oikeuskansleri (oletus)
            - "data-protection"       = Tietosuojavaltuutettu
        chunk: Osan numero sivutuksessa (oletus: 1).

    Palauttaa: Päätöksen teksti merkkijonona.

    Esimerkki:
        get_decision_text(year=2025, number=11017, court="chancellor-of-justice")
        get_decision_text(year=2025, number=11017, court="chancellor-of-justice", chunk=2)
        get_decision_text(year=2025, number=2464, court="data-protection")

    HUOM: KKO:n ja KHO:n päätökset eivät ole saatavilla tässä rajapinnassa.
    """
    court_map = {
        "chancellor-of-justice": "chancellor-of-justice-decision",
        "okv": "chancellor-of-justice-decision",
        "oikeuskansleri": "chancellor-of-justice-decision",
        "chancellor": "chancellor-of-justice-decision",
        "data-protection": "data-protection-ombudsman-decision",
        "tietosuoja": "data-protection-ombudsman-decision",
        "tso": "data-protection-ombudsman-decision",
        "ombudsman": "data-protection-ombudsman-decision",
    }

    judgment_type = court_map.get(court.lower(), court)

    try:
        xml_content = fetch_judgment_xml(judgment_type, year, number, "fin")
        parsed = parse_akn_xml(xml_content, chunk=chunk)
        return format_result(parsed)
    except Exception as e:
        return f"Päätöstä {year}/{number} (tyyppi: {judgment_type}) ei löydy. Virhe: {e}"


# ---------------------------------------------------------------------------
# Työkalu 6: Hae hallituksen esitys
# ---------------------------------------------------------------------------

async def get_government_proposal(
    year: int,
    number: int,
    lang: str = "fin",
    chunk: int = 1,
) -> str:
    """
    Hae hallituksen esityksen (HE) teksti Finlexistä.

    Hallituksen esitykset ovat lainvalmistelun perusteluasiakirjoja, joissa
    selitetään lain tarkoitus, vaikutukset ja yksityiskohtaiset perustelut.

    PITKÄT DOKUMENTIT – SIVUTUS:
    Hallituksen esitykset ovat usein hyvin pitkiä. Vastauksessa näkyy esim.
    "[OSA 1/6]". Hae loput osilla chunk=2, chunk=3 jne.

    Parametrit:
        year: Hallituksen esityksen vuosi (esim. 2024)
        number: Hallituksen esityksen numero ilman "HE"-etuliitettä (esim. 215)
        lang: Kielikoodi – "fin" = suomi (oletus), "swe" = ruotsi
        chunk: Osan numero sivutuksessa (oletus: 1).

    Palauttaa: Hallituksen esityksen teksti merkkijonona.

    Esimerkkejä:
        get_government_proposal(year=2024, number=215)          → HE 215/2024 (osa 1)
        get_government_proposal(year=2024, number=215, chunk=2) → HE 215/2024 (osa 2)

    Viittausmuoto: "HE numero/vuosi" esim. "HE 215/2024"
    """
    try:
        xml_content = fetch_doc_xml("government-proposal", year, number, lang)
        parsed = parse_akn_xml(xml_content, chunk=chunk)
        return format_result(parsed)
    except Exception as e:
        return f"Hallituksen esitystä HE {number}/{year} ei löydy. Virhe: {e}"
