"""Task h2-30b: pure geography helpers for location/eligibility/workauth parsers.

All data is static, standard-library only. No network, no database, no AI.
"""

from __future__ import annotations

from typing import Mapping, Set

# ---------------------------------------------------------------------------
# Country table: name/alias (lowercased) -> ISO 3166-1 alpha-2.
# At least 100 entries. Common aliases: USA, U.S., UK, UAE.
# Two-letter codes ONLY when upper-case in the input, so "in"/"it" never match.
# ---------------------------------------------------------------------------
_COUNTRY_TABLE: dict[str, str] = {
    # Core required + common
    "afghanistan": "AF",
    "albania": "AL",
    "algeria": "DZ",
    "andorra": "AD",
    "angola": "AO",
    "antigua and barbuda": "AG",
    "argentina": "AR",
    "armenia": "AM",
    "australia": "AU",
    "austria": "AT",
    "azerbaijan": "AZ",
    "bahamas": "BS",
    "bahrain": "BH",
    "bangladesh": "BD",
    "barbados": "BB",
    "belarus": "BY",
    "belgium": "BE",
    "belize": "BZ",
    "benin": "BJ",
    "bhutan": "BT",
    "bolivia": "BO",
    "bosnia and herzegovina": "BA",
    "botswana": "BW",
    "brazil": "BR",
    "brunei": "BN",
    "bulgaria": "BG",
    "burkina faso": "BF",
    "burundi": "BI",
    "cabo verde": "CV",
    "cambodia": "KH",
    "cameroon": "CM",
    "canada": "CA",
    "central african republic": "CF",
    "chad": "TD",
    "chile": "CL",
    "china": "CN",
    "colombia": "CO",
    "comoros": "KM",
    "congo": "CG",
    "costa rica": "CR",
    "croatia": "HR",
    "cuba": "CU",
    "cyprus": "CY",
    "czech republic": "CZ",
    "czechia": "CZ",
    "democratic republic of the congo": "CD",
    "denmark": "DK",
    "djibouti": "DJ",
    "dominica": "DM",
    "dominican republic": "DO",
    "ecuador": "EC",
    "egypt": "EG",
    "el salvador": "SV",
    "equatorial guinea": "GQ",
    "eritrea": "ER",
    "estonia": "EE",
    "eswatini": "SZ",
    "ethiopia": "ET",
    "fiji": "FJ",
    "finland": "FI",
    "france": "FR",
    "gabon": "GA",
    "gambia": "GM",
    "georgia": "GE",
    "germany": "DE",
    "ghana": "GH",
    "greece": "GR",
    "grenada": "GD",
    "guatemala": "GT",
    "guinea": "GN",
    "guinea-bissau": "GW",
    "guyana": "GY",
    "haiti": "HT",
    "honduras": "HN",
    "hungary": "HU",
    "iceland": "IS",
    "india": "IN",
    "indonesia": "ID",
    "iran": "IR",
    "iraq": "IQ",
    "ireland": "IE",
    "israel": "IL",
    "italy": "IT",
    "jamaica": "JM",
    "japan": "JP",
    "jordan": "JO",
    "kazakhstan": "KZ",
    "kenya": "KE",
    "kiribati": "KI",
    "korea": "KR",
    "kuwait": "KW",
    "kyrgyzstan": "KG",
    "laos": "LA",
    "latvia": "LV",
    "lebanon": "LB",
    "lesotho": "LS",
    "liberia": "LR",
    "libya": "LY",
    "liechtenstein": "LI",
    "lithuania": "LT",
    "luxembourg": "LU",
    "madagascar": "MG",
    "malawi": "MW",
    "malaysia": "MY",
    "maldives": "MV",
    "mali": "ML",
    "malta": "MT",
    "marshall islands": "MH",
    "mauritania": "MR",
    "mauritius": "MU",
    "mexico": "MX",
    "micronesia": "FM",
    "moldova": "MD",
    "monaco": "MC",
    "mongolia": "MN",
    "montenegro": "ME",
    "morocco": "MA",
    "mozambique": "MZ",
    "myanmar": "MM",
    "namibia": "NA",
    "nauru": "NR",
    "nepal": "NP",
    "netherlands": "NL",
    "new zealand": "NZ",
    "nicaragua": "NI",
    "niger": "NE",
    "nigeria": "NG",
    "north macedonia": "MK",
    "norway": "NO",
    "oman": "OM",
    "pakistan": "PK",
    "palau": "PW",
    "panama": "PA",
    "papua new guinea": "PG",
    "paraguay": "PY",
    "peru": "PE",
    "philippines": "PH",
    "poland": "PL",
    "portugal": "PT",
    "qatar": "QA",
    "romania": "RO",
    "russia": "RU",
    "rwanda": "RW",
    "saint kitts and nevis": "KN",
    "saint lucia": "LC",
    "saint vincent and the grenadines": "VC",
    "samoa": "WS",
    "san marino": "SM",
    "sao tome and principe": "ST",
    "saudi arabia": "SA",
    "senegal": "SN",
    "serbia": "RS",
    "seychelles": "SC",
    "sierra leone": "SL",
    "singapore": "SG",
    "slovakia": "SK",
    "slovenia": "SI",
    "solomon islands": "SB",
    "somalia": "SO",
    "south africa": "ZA",
    "south korea": "KR",
    "south sudan": "SS",
    "spain": "ES",
    "sri lanka": "LK",
    "sudan": "SD",
    "suriname": "SR",
    "sweden": "SE",
    "switzerland": "CH",
    "syria": "SY",
    "taiwan": "TW",
    "tajikistan": "TJ",
    "tanzania": "TZ",
    "thailand": "TH",
    "timor-leste": "TL",
    "togo": "TG",
    "tonga": "TO",
    "trinidad and tobago": "TT",
    "tunisia": "TN",
    "turkey": "TR",
    "turkiye": "TR",
    "tuvalu": "TV",
    "uganda": "UG",
    "ukraine": "UA",
    "united arab emirates": "AE",
    "uae": "AE",
    "united kingdom": "GB",
    "uk": "GB",
    "great britain": "GB",
    "england": "GB",
    "scotland": "GB",
    "wales": "GB",
    "northern ireland": "GB",
    "united states": "US",
    "usa": "US",
    "u.s.": "US",
    "u.s.a.": "US",
    "uruguay": "UY",
    "uzbekistan": "UZ",
    "vanuatu": "VU",
    "vatican city": "VA",
    "venezuela": "VE",
    "vietnam": "VN",
    "yemen": "YE",
    "zambia": "ZM",
    "zimbabwe": "ZW",
}

_ISO_CODES: set[str] = set(_COUNTRY_TABLE.values())


def resolve_country(name_or_code: str) -> str | None:
    """Return ISO 3166-1 alpha-2 code for a country name or alias.

    - Names/aliases are matched case-insensitively.
    - Two-letter codes are accepted ONLY when the input token is upper-case
      (e.g. "US", "IN", "GB") so that words like "in" or "it" never match.
    - Returns None for unknown inputs.
    """
    if not isinstance(name_or_code, str):
        return None
    key = name_or_code.strip()
    if not key:
        return None
    # Two-letter upper-case code check first (exact match)
    # Only accept if the input token is already uppercase (e.g., "US", "IN")
    if len(key) == 2 and key.isupper() and key in _ISO_CODES:
        return key
    # Name/alias lookup (case-insensitive)
    return _COUNTRY_TABLE.get(key.lower())


# ---------------------------------------------------------------------------
# Region mappings: region keyword (lowercased) -> set of member ISO codes.
# ---------------------------------------------------------------------------

# EU / European Union: 27 members as of 2024
_EU_MEMBERS: Set[str] = {
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR",
    "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL",
    "PL", "PT", "RO", "SK", "SI", "ES", "SE",
}

_EMEA_MEMBERS: Set[str] = {
    # Europe (all EU + others commonly in EMEA)
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR",
    "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL",
    "PL", "PT", "RO", "SK", "SI", "ES", "SE",
    "AL", "AD", "BA", "BY", "FO", "GI", "GG", "IM", "IS", "JE",
    "LI", "MC", "MD", "ME", "MK", "NO", "RS", "SM", "CH", "UA",
    "GB", "VA", "XK",
    # Middle East
    "AE", "BH", "CY", "EG", "IL", "IQ", "IR", "JO", "KW", "LB",
    "OM", "PS", "QA", "SA", "SY", "TR", "YE",
    # Africa (common EMEA subset)
    "DZ", "AO", "BJ", "BW", "BF", "BI", "CM", "CV", "CF", "TD",
    "KM", "CG", "CD", "DJ", "EG", "GQ", "ER", "SZ", "ET", "GA",
    "GM", "GH", "GN", "GW", "CI", "KE", "LS", "LR", "LY", "MG",
    "MW", "ML", "MR", "MU", "MA", "MZ", "NA", "NE", "NG", "RW",
    "ST", "SN", "SC", "SL", "SO", "ZA", "SS", "SD", "TZ", "TG",
    "TN", "UG", "ZM", "ZW",
}

_APAC_MEMBERS: Set[str] = {
    "AU", "BN", "KH", "CN", "CK", "FJ", "PF", "HK", "IN", "ID",
    "JP", "KI", "KP", "KR", "LA", "MO", "MY", "MV", "MH", "FM",
    "MN", "MM", "NR", "NP", "NZ", "NU", "NF", "MP", "PK", "PW",
    "PG", "PH", "PN", "WS", "SG", "SB", "LK", "TW", "TH", "TL",
    "TO", "TV", "VU", "VN", "WF",
}

_LATAM_MEMBERS: Set[str] = {
    "AR", "BO", "BR", "CL", "CO", "CR", "CU", "DO", "EC", "SV",
    "GT", "HT", "HN", "MX", "NI", "PA", "PY", "PE", "PR", "UY",
    "VE",
}

_NORTH_AMERICA_MEMBERS: Set[str] = {
    "US", "CA",  # MX excluded per spec
}

REGIONS: Mapping[str, Set[str]] = {
    "eu": _EU_MEMBERS,
    "european union": _EU_MEMBERS,
    "emea": _EMEA_MEMBERS,
    "apac": _APAC_MEMBERS,
    "asia-pacific": _APAC_MEMBERS,
    "asia pacific": _APAC_MEMBERS,
    "latam": _LATAM_MEMBERS,
    "latin america": _LATAM_MEMBERS,
    "north america": _NORTH_AMERICA_MEMBERS,
}


# ---------------------------------------------------------------------------
# Indian cities (for inferring country IN from bare city names)
# ---------------------------------------------------------------------------
INDIA_CITIES: Set[str] = {
    "bengaluru", "bangalore", "mumbai", "delhi", "new delhi",
    "gurgaon", "gurugram", "noida", "hyderabad", "pune",
    "chennai", "kolkata", "ahmedabad",
}


# ---------------------------------------------------------------------------
# US states: name (lowercased) and code (uppercased) -> "US"
# Used to infer country US from "City, ST" patterns.
# ---------------------------------------------------------------------------
US_STATES: dict[str, str] = {
    # Full names
    "alabama": "US", "alaska": "US", "arizona": "US", "arkansas": "US",
    "california": "US", "colorado": "US", "connecticut": "US", "delaware": "US",
    "florida": "US", "georgia": "US", "hawaii": "US", "idaho": "US",
    "illinois": "US", "indiana": "US", "iowa": "US", "kansas": "US",
    "kentucky": "US", "louisiana": "US", "maine": "US", "maryland": "US",
    "massachusetts": "US", "michigan": "US", "minnesota": "US", "mississippi": "US",
    "missouri": "US", "montana": "US", "nebraska": "US", "nevada": "US",
    "new hampshire": "US", "new jersey": "US", "new mexico": "US", "new york": "US",
    "north carolina": "US", "north dakota": "US", "ohio": "US", "oklahoma": "US",
    "oregon": "US", "pennsylvania": "US", "rhode island": "US", "south carolina": "US",
    "south dakota": "US", "tennessee": "US", "texas": "US", "utah": "US",
    "vermont": "US", "virginia": "US", "washington": "US", "west virginia": "US",
    "wisconsin": "US", "wyoming": "US",
    "district of columbia": "US", "d.c.": "US", "dc": "US",
    # Two-letter codes (uppercase only in practice, but we store lowercase for lookup)
    "al": "US", "ak": "US", "az": "US", "ar": "US", "ca": "US", "co": "US",
    "ct": "US", "de": "US", "fl": "US", "ga": "US", "hi": "US", "id": "US",
    "il": "US", "in": "US", "ia": "US", "ks": "US", "ky": "US", "la": "US",
    "me": "US", "md": "US", "ma": "US", "mi": "US", "mn": "US", "ms": "US",
    "mo": "US", "mt": "US", "ne": "US", "nv": "US", "nh": "US", "nj": "US",
    "nm": "US", "ny": "US", "nc": "US", "nd": "US", "oh": "US", "ok": "US",
    "or": "US", "pa": "US", "ri": "US", "sc": "US", "sd": "US", "tn": "US",
    "tx": "US", "ut": "US", "vt": "US", "va": "US", "wa": "US", "wv": "US",
    "wi": "US", "wy": "US", "dc": "US",
}


__all__ = [
    "resolve_country",
    "REGIONS",
    "INDIA_CITIES",
    "US_STATES",
    "_COUNTRY_TABLE",
    "_ISO_CODES",
]