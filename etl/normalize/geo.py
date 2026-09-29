"""Free-text location → `country`, `countries_all`, `remote_scope`.

Built here for RemoteOK, Jobicy and the three ATS providers, and **only** for those. Himalayas
is not in this module's remit: its `locationRestrictions` is a controlled list of 222 country
names and `himalayas.map_source_fields` already resolves it, and the task file is explicit
that a second country table for it would drift from the first. `normalizer.py` calls that
function instead. Everything below exists because the other five sources publish location
*strings*, which is a different problem: `"Remote - US"`, `"SIHO - Columbus, IN"`,
`"Remote - Ireland; Remote - United Kingdom"` and `"US FL Miami - Remote"` are four
grammatical shapes that all have to become one alpha-2 code.

`normalization.md` §3.4 gives the order and the tables. The one place this module has to
make a judgement the document leaves implicit is the **relative order of the two-letter rule
and the country table**: §3.4 lists `COUNTRY_INDEX` (which holds the alpha-2 codes) at step 5
and describes the ambiguity at step 6, but reading step 5 first would resolve `IN` to India
before the guard ever ran — which is precisely the failure the document spends a paragraph
warning about. So two-letter tokens are resolved **state → province → country**, and
non-two-letter tokens are resolved most-specific-table-first. `test_normalizer.py` pins
`'SIHO - Columbus, IN'` to `US` because that is the row the contract names.

Three things are deliberately *not* inferred:

* **`global` is never produced here.** `vocabularies.md` §3 requires a source assertion, and
  this module has only a string. A posting whose location did not parse is `unknown` and is
  counted in `country_unresolved_count`; calling it `global` is what would inflate the
  flagship India number. The two legitimate `global` routes — Jobicy `jobGeo == "Anywhere"`
  and an empty Himalayas `locationRestrictions` — are step 1, handled by the source adapters.
* **A region is not a country.** `LATAM`, `APAC`, `EMEA`, `Europe` resolve to nothing and are
  counted unresolved, per §3.4 step 3.
* **A two-letter token is not a country if it is also a state or province.** `IN` is Indiana,
  `CA` is California, `BC` is British Columbia.
"""

from __future__ import annotations

import re
from typing import Iterable, Mapping

from .text import collapse_whitespace, fold

#: `normalization.md` §3.6's LLM cap is unrelated to geography; this is §3.4's audit cap,
#: shared with `himalayas.RULE_MAX_CHARS` so one rule-string length means one thing repo-wide.
RULE_MAX_CHARS = 160


# --------------------------------------------------------------------- encoding repair
def repair_encoding(text: str) -> tuple[str, bool]:
    """`(text, repaired)` after one `latin-1` → UTF-8 re-decode attempt.

    §3.4 step 1: some RemoteOK locations are UTF-8 bytes misread as Latin-1, and one round
    trip recovers the text — `Islamabad, Islamabad, Islāmābād, Pakistan`. The flag is
    returned so `location_encoding_repaired` is a fact about what happened rather than a
    comment in the code.

    The attempt is skipped when it cannot help, and specifically **never guesses the other
    direction**. A string already decoded as Latin-1 from a Latin-1 source (Greenhouse's
    `Reykjav\xedk`) is not mojibake, and re-encoding it to UTF-8 and back would corrupt a
    correct string in order to make the flag say `1`.
    """
    raw = str(text or "")
    try:
        candidate = raw.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return raw, False
    # A successful round trip that changes nothing is not a repair; reporting it as one
    # would make the column a measure of "is this string ASCII" rather than of damage.
    if candidate == raw:
        return raw, False
    return candidate, True


# -------------------------------------------------------------------------- the tables
#: `COUNTRY_INDEX` — country name and the aliases these five sources actually spell that way,
#: folded to a lookup key, → ISO 3166-1 alpha-2.
#:
#: There is one ISO registry, so the *names* here necessarily overlap the 222-name table in
#: `himalayas.py`; that is not the duplication the task file warns about. What is different,
#: and is the reason this table is not `himalayas.HIMALAYAS_COUNTRY` re-exported, is the
#: alias layer and the folding: these sources write `USA`, `U.S.`, `England`, `Salford,
#: England`, `Tokyo, Japan`, `India` inside a sentence, and a free-text resolver has to cope
#: with the shape of the string, not just the name in it. `test_normalizer.py` asserts the
#: two tables agree on every code they share, so a divergence in either is caught rather
#: than discovered in a published number.
COUNTRY_INDEX: Mapping[str, str] = {
    # -- the countries the charter tracks, plus everything the captures contain ---------
    "india": "IN",
    "united states": "US",
    "united states of america": "US",
    "united kingdom": "GB",
    "uk": "GB",
    "u k": "GB",
    "great britain": "GB",
    "britain": "GB",
    "england": "GB",
    "scotland": "GB",
    "wales": "GB",
    "northern ireland": "GB",
    "cayman islands": "KY",
    "bermuda": "BM",
    "ireland": "IE",
    "canada": "CA",
    "germany": "DE",
    "japan": "JP",
    "australia": "AU",
    "new zealand": "NZ",
    "mexico": "MX",
    "ukraine": "UA",
    "czechia": "CZ",
    "czech republic": "CZ",
    "slovakia": "SK",
    "argentina": "AR",
    "spain": "ES",
    "philippines": "PH",
    "poland": "PL",
    # -- the rest of ISO 3166-1, by common short name ---------------------------------
    "afghanistan": "AF", "albania": "AL", "algeria": "DZ", "andorra": "AD", "angola": "AO",
    "anguilla": "AI", "antigua and barbuda": "AG", "aruba": "AW", "austria": "AT",
    "azerbaijan": "AZ", "bahamas": "BS", "bahrain": "BH", "bangladesh": "BD",
    "barbados": "BB", "belarus": "BY", "belgium": "BE", "belize": "BZ", "benin": "BJ",
    "bhutan": "BT", "bolivia": "BO", "bosnia and herzegovina": "BA", "botswana": "BW",
    "brazil": "BR", "brunei": "BN", "bulgaria": "BG", "burkina faso": "BF", "burundi": "BI",
    "cabo verde": "CV", "cameroon": "CM", "central african republic": "CF", "chad": "TD",
    "chile": "CL", "china": "CN", "colombia": "CO", "comoros": "KM", "congo": "CG",
    "congo the democratic republic of the": "CD", "democratic republic of the congo": "CD",
    "dr congo": "CD", "costa rica": "CR", "croatia": "HR", "cuba": "CU", "curacao": "CW",
    "cyprus": "CY", "denmark": "DK", "djibouti": "DJ", "dominica": "DM",
    "dominican republic": "DO", "ecuador": "EC", "egypt": "EG", "el salvador": "SV",
    "equatorial guinea": "GQ", "eritrea": "ER", "estonia": "EE", "eswatini": "SZ",
    "ethiopia": "ET", "faroe islands": "FO", "finland": "FI", "france": "FR",
    "french guiana": "GF", "gabon": "GA", "gambia": "GM", "georgia": "GE", "ghana": "GH",
    "gibraltar": "GI", "greece": "GR", "greenland": "GL", "grenada": "GD",
    "guadeloupe": "GP", "guatemala": "GT", "guernsey": "GG", "guinea": "GN",
    "guinea bissau": "GW", "guyana": "GY", "haiti": "HT", "holy see": "VA",
    "vatican": "VA", "honduras": "HN", "hong kong": "HK", "hungary": "HU",
    "iceland": "IS", "indonesia": "ID", "iran": "IR", "iraq": "IQ", "israel": "IL",
    "italy": "IT", "jamaica": "JM", "jordan": "JO", "kazakhstan": "KZ", "kenya": "KE",
    "kosovo": "XK", "kuwait": "KW", "kyrgyzstan": "KG", "laos": "LA", "latvia": "LV",
    "lebanon": "LB", "lesotho": "LS", "liberia": "LR", "libya": "LY",
    "liechtenstein": "LI", "lithuania": "LT", "luxembourg": "LU", "macao": "MO",
    "madagascar": "MG", "malawi": "MW", "malaysia": "MY", "maldives": "MV", "mali": "ML",
    "malta": "MT", "martinique": "MQ", "mauritania": "MR", "mauritius": "MU", "mayotte": "YT",
    "moldova": "MD", "monaco": "MC", "mongolia": "MN", "montenegro": "ME",
    "morocco": "MA", "mozambique": "MZ", "myanmar": "MM", "burma": "MM", "namibia": "NA",
    "nepal": "NP", "netherlands": "NL", "holland": "NL", "nicaragua": "NI", "niger": "NE",
    "nigeria": "NG", "north korea": "KP", "north macedonia": "MK", "macedonia": "MK",
    "norway": "NO", "oman": "OM", "pakistan": "PK", "palestine": "PS",
    "state of palestine": "PS", "panama": "PA", "papua new guinea": "PG", "paraguay": "PY",
    "peru": "PE", "portugal": "PT", "puerto rico": "PR", "qatar": "QA", "romania": "RO",
    "russia": "RU", "russian federation": "RU", "rwanda": "RW", "reunion": "RE",
    "saint lucia": "LC", "st lucia": "LC", "samoa": "WS", "san marino": "SM",
    "saudi arabia": "SA", "senegal": "SN", "serbia": "RS", "seychelles": "SC",
    "sierra leone": "SL", "singapore": "SG", "slovenia": "SI", "solomon islands": "SB",
    "somalia": "SO", "south africa": "ZA", "south korea": "KR", "korea": "KR",
    "republic of korea": "KR", "south sudan": "SS", "sri lanka": "LK", "sudan": "SD",
    "suriname": "SR", "sweden": "SE", "switzerland": "CH", "syria": "SY",
    "taiwan": "TW", "tajikistan": "TJ", "tanzania": "TZ", "thailand": "TH", "timor leste": "TL",
    "togo": "TG", "tonga": "TO", "trinidad and tobago": "TT", "tunisia": "TN",
    "turkey": "TR", "turkiye": "TR", "turkmenistan": "TM", "uganda": "UG", "ukraine ": "UA",
    "united arab emirates": "AE", "uae": "AE", "uruguay": "UY", "uzbekistan": "UZ",
    "vanuatu": "VU", "vietnam": "VN", "yemen": "YE", "zambia": "ZM", "zimbabwe": "ZW",
    # -- the abbreviations and possessives these five feeds actually emit ----------------
    # Every spelling below is one a captured string or a quoted contract example contains;
    # the trailing-dot forms are from ATS locations that end a sentence fragment with a full
    # stop ("San Francisco, California."). `remote` is deliberately **not** here: it is a
    # qualifier word in `NO_PLACE_TOKENS`, and a table that can also answer it is a table
    # that will one day answer it instead of declining.
    "usa": "US", "u s a": "US", "u s": "US", "u s.": "US", "us": "US", "america": "US",
    "united states of america.": "US", "canada.": "CA", "u k.": "GB", "uk.": "GB",
    "india.": "IN", "netherlands.": "NL", "spain.": "ES", "france.": "FR",
    "philippines.": "PH", "argentina.": "AR", "ireland.": "IE", "england.": "GB",
    "scotland.": "GB", "japan.": "JP", "poland.": "PL", "australia.": "AU",
    "germany.": "DE", "new zealand.": "NZ", "mexico.": "MX", "ukraine.": "UA",
    "czechia.": "CZ", "slovakia.": "SK", "brazil.": "BR", "chile.": "CL",
    "colombia.": "CO", "peru.": "PE", "nepal.": "NP", "sri lanka.": "LK",
    "indonesia.": "ID", "thailand.": "TH", "vietnam.": "VN", "malaysia.": "MY",
    "singapore.": "SG", "hong kong.": "HK", "nigeria.": "NG", "kenya.": "KE",
    "egypt.": "EG", "morocco.": "MA", "israel.": "IL", "turkey.": "TR",
    "portugal.": "PT", "sweden.": "SE", "norway.": "NO", "denmark.": "DK",
    "finland.": "FI", "iceland.": "IS",
}


#: US postal abbreviations → `US`. §3.4's block list, in full, plus the rest of the postal
#: codes, because a partial table would let `OR` fall through to a country lookup and put an
#: Oregon role in Costa Rica.
STATE_TOKEN: Mapping[str, str] = {
    "al": "US", "ak": "US", "az": "US", "ar": "US", "ca": "US", "co": "US", "ct": "US",
    "de": "US", "dc": "US", "fl": "US", "ga": "US", "hi": "US", "id": "US", "il": "US",
    "in": "US", "ia": "US", "ks": "US", "ky": "US", "la": "US", "me": "US", "md": "US",
    "ma": "US", "mi": "US", "mn": "US", "ms": "US", "mo": "US", "mt": "US", "ne": "US",
    "nv": "US", "nh": "US", "nj": "US", "nm": "US", "ny": "US", "nc": "US", "nd": "US",
    "oh": "US", "ok": "US", "or": "US", "pa": "US", "ri": "US", "sc": "US", "sd": "US",
    "tn": "US", "tx": "US", "ut": "US", "vt": "US", "va": "US", "wa": "US", "wv": "US",
    "wi": "US", "wy": "US",
}

#: Canadian province and territory abbreviations → `CA`.
#:
#: `BC` is the measured case (`'Vancouver, BC, Canada'` → `CA` via this table, with `Canada`
#: also resolving to `CA` and the two de-duplicating). Read *before* the country table so
#: `CA` itself is California, not Canada.
PROVINCE_TOKEN: Mapping[str, str] = {
    "ab": "CA", "bc": "CA", "mb": "CA", "nb": "CA", "nl": "CA", "ns": "CA", "nt": "CA",
    "nu": "CA", "on": "CA", "pe": "CA", "qc": "CA", "sk": "CA", "yt": "CA",
}

#: Subdivision *names* → code. §3.4's `SUBDIVISIONS`, the third table in the lookup order and
#: the one that turns `"Austin, Texas, United States"` into `US` on its own account when the
#: country token is missing.
SUBDIVISIONS: Mapping[str, str] = {
    # US states, DC and the inhabited territories
    "alabama": "US", "alaska": "US", "arizona": "US", "arkansas": "US", "california": "US",
    "colorado": "US", "connecticut": "US", "delaware": "US", "district of columbia": "US",
    "florida": "US", "georgia": "US", "hawaii": "US", "idaho": "US", "illinois": "US",
    "indiana": "US", "iowa": "US", "kansas": "US", "kentucky": "US", "louisiana": "US",
    "maine": "US", "maryland": "US", "massachusetts": "US", "michigan": "US",
    "minnesota": "US", "mississippi": "US", "missouri": "US", "montana": "US",
    "nebraska": "US", "nevada": "US", "new hampshire": "US", "new jersey": "US",
    "new mexico": "US", "new york": "US", "north carolina": "US", "north dakota": "US",
    "ohio": "US", "oklahoma": "US", "oregon": "US", "pennsylvania": "US",
    "puerto rico": "PR", "rhode island": "US", "south carolina": "US", "south dakota": "US",
    "tennessee": "US", "texas": "US", "utah": "US", "vermont": "US", "virginia": "US",
    "washington": "US", "west virginia": "US", "wisconsin": "US", "wyoming": "US",
    # Canada
    "alberta": "CA", "british columbia": "CA", "manitoba": "CA", "new brunswick": "CA",
    "newfoundland and labrador": "CA", "nova scotia": "CA", "northwest territories": "CA",
    "nunavut": "CA", "ontario": "CA", "prince edward island": "CA", "quebec": "CA",
    "saskatchewan": "CA", "yukon": "CA",
    # UK constituent countries
    "england": "GB", "scotland": "GB", "wales": "GB", "northern ireland": "GB",
    "great britain": "GB", "britain": "GB",
    # Australia
    "new south wales": "AU", "queensland": "AU", "south australia": "AU",
    "tasmania": "AU", "victoria": "AU", "western australia": "AU",
    "northern territory": "AU", "australian capital territory": "AU",
    # the rest of Europe, the regions these boards actually name
    "bavaria": "DE", "ile de france": "FR", "catalonia": "ES", "andalusia": "ES",
    "flanders": "BE", "wallonia": "BE", "lombardy": "IT", "tuscany": "IT", "zeeland": "NL",
}

#: `CITY_COUNTRY` — the curated city table §3.4 calls for.
#:
#: "The table is data, not logic, and is versioned with the contract." It is here because a
#: city with no country token is otherwise unresolvable: `Agra` and `Dehradun` carry no
#: country, and adding them took India from 2/99 to 4/99 on the committed RemoteOK capture —
#: the contract's own headline example of the table earning its place.
#:
#: Entries are (a) every city in the six committed captures, so a re-capture of the same
#: boards resolves, and (b) the major tech hubs of the three charter countries, because that
#: is where a remote posting actually sits. **Ambiguous names are resolved once, deliberately
#: and visibly.** `newcastle` → `GB` is that table's cost: a Newcastle, New Brunswick
#: posting would be filed as UK. The alternative is leaving it unresolved and losing the row
#: from every country count; the contract chose the same trade for `Agra` and `Dehradun`.
CITY_COUNTRY: Mapping[str, str] = {
    # -- measured in the committed captures -------------------------------------------
    "agra": "IN", "dehradun": "IN", "hyderabad": "IN", "columbus": "US", "austin": "US",
    "mexico city": "MX", "melbourne": "AU", "newcastle": "GB", "salford": "GB",
    "redwood city": "US", "tokyo": "JP", "warsaw": "PL", "reykjavik": "IS",
    "san francisco": "US", "philadelphia": "US", "chicago": "US", "miami": "US",
    "la cienega": "US", "aventura": "US", "concord": "US", "richmond": "US",
    "cherry hill": "US", "new york": "US", "new york city": "US", "munich": "DE",
    "vancouver": "CA", "boston": "US", "seoul": "KR", "islamabad": "PK",
    # -- India, the charter's flagship market ------------------------------------------
    "bangalore": "IN", "bengaluru": "IN", "mumbai": "IN", "bombay": "IN", "delhi": "IN",
    "new delhi": "IN", "gurugram": "IN", "gurgaon": "IN", "noida": "IN", "pune": "IN",
    "chennai": "IN", "madras": "IN", "kolkata": "IN", "jaipur": "IN", "lucknow": "IN",
    "chandigarh": "IN", "indore": "IN", "ahmedabad": "IN", "kochi": "IN",
    "coimbatore": "IN", "bhubaneswar": "IN", "nagpur": "IN", "vadodara": "IN",
    "visakhapatnam": "IN", "trivandrum": "IN", "thiruvananthapuram": "IN", "mysuru": "IN",
    "mysore": "IN", "coorg": "IN", "haryana": "IN", "telangana": "IN", "andhra pradesh": "IN",
    "karnataka": "IN", "maharashtra": "IN", "kerala": "IN", "tamil nadu": "IN",
    "west bengal": "IN", "gujarat": "IN", "rajasthan": "IN", "uttar pradesh": "IN",
    "punjab": "IN", "odisha": "IN", "assam": "IN", "jharkhand": "IN", "chhattisgarh": "IN",
    # -- United States ------------------------------------------------------------------
    "seattle": "US", "portland": "US", "denver": "US", "atlanta": "US",
    "los angeles": "US", "san diego": "US", "dallas": "US", "houston": "US",
    "washington": "US", "washington dc": "US", "mountain view": "US", "palo alto": "US",
    "menlo park": "US", "sunnyvale": "US", "santa clara": "US", "bellevue": "US",
    "brooklyn": "US", "cambridge": "US", "nashville": "US", "raleigh": "US",
    "minneapolis": "US", "detroit": "US", "phoenix": "US", "philadelphia pa": "US",
    "salt lake city": "US", "las vegas": "US", "pittsburgh": "US", "baltimore": "US",
    "ann arbor": "US", "austin tx": "US", "brookline": "US", "newark": "US",
    "jersey city": "US", "stamford": "US", "raleigh nc": "US", "charlotte": "US",
    "charlotte nc": "US", "pittsburgh pa": "US", "boise": "US", "honolulu": "US",
    "albuquerque": "US", "tucson": "US", "omaha": "US", "kansas city": "US",
    "st louis": "US", "saint louis": "US", "buffalo": "US", "rochester": "US",
    "orlando": "US", "tampa": "US", "jacksonville": "US", "columbus ohio": "US",
    "sacramento": "US", "fresno": "US", "tulsa": "US", "wichita": "US", "el paso": "US",
    # -- United Kingdom / Ireland --------------------------------------------------------
    "london": "GB", "manchester": "GB", "edinburgh": "GB", "glasgow": "GB",
    "bristol": "GB", "birmingham": "GB", "liverpool": "GB", "leeds": "GB", "yorkshire": "GB",
    "brighton": "GB", "oxford": "GB", "cambridge uk": "GB", "nottingham": "GB",
    "leicester": "GB", "sheffield": "GB", "belfast": "GB", "cardiff": "GB",
    "aberdeen": "GB", "bournemouth": "GB", "portsmouth": "GB", "sunderland": "GB",
    "coventry": "GB", "derby": "GB", "plymouth": "GB", "ipswich": "GB",
    "dublin": "IE", "cork": "IE", "galway": "IE",
    # -- Canada, and the rest of Europe --------------------------------------------------
    "toronto": "CA", "ottawa": "CA", "montreal": "CA", "calgary": "CA", "edmonton": "CA",
    "winnipeg": "CA", "quebec city": "CA", "hamilton": "CA", "halifax": "CA",
    "berlin": "DE", "hamburg": "DE", "munich de": "DE", "frankfurt": "DE", "cologne": "DE",
    "stuttgart": "DE", "dusseldorf": "DE", "leipzig": "DE",
    "paris": "FR", "lyon": "FR", "marseille": "FR", "toulouse": "FR", "bordeaux": "FR",
    "amsterdam": "NL", "rotterdam": "NL", "utrecht": "NL", "the hague": "NL",
    "brussels": "BE", "antwerp": "BE", "ghent": "BE",
    "madrid": "ES", "barcelona": "ES", "valencia": "ES", "seville": "ES", "bilbao": "ES",
    "lisbon": "PT", "porto": "PT", "milan": "IT", "rome": "IT", "turin": "IT", "naples": "IT",
    "florence": "IT", "bologna": "IT",
    "zurich": "CH", "geneva": "CH", "basel": "CH", "bern": "CH", "lausanne": "CH",
    "vienna": "AT", "zurich ch": "CH", "prague": "CZ", "brno": "CZ",
    "bratislava": "SK", "budapest": "HU", "warsaw pl": "PL", "krakow": "PL",
    "krakow poland": "PL", "wroclaw": "PL", "poznan": "PL", "gdansk": "PL",
    "bucharest": "RO", "sofia": "BG", "belgrade": "RS", "zagreb": "HR", "ljubljana": "SI",
    "athens": "GR", "istanbul": "TR", "ankara": "TR", "moscow": "RU", "kyiv": "UA",
    "kiev": "UA", "riga": "LV", "vilnius": "LT", "tallinn": "EE", "helsinki": "FI",
    "oslo": "NO", "stockholm": "SE", "gothenburg": "SE", "copenhagen": "DK",
    "reykjavik is": "IS",
    # -- the rest of the world -----------------------------------------------------------
    "sydney": "AU", "brisbane": "AU", "perth": "AU", "adelaide": "AU", "canberra": "AU",
    "auckland": "NZ", "wellington": "NZ", "christchurch": "NZ",
    "sao paulo": "BR", "rio de janeiro": "BR", "buenos aires": "AR", "cordoba": "AR",
    "santiago": "CL", "bogota": "CO", "lima": "PE", "montevideo": "UY",
    "guadalajara": "MX", "monterrey": "MX", "cape town": "ZA", "johannesburg": "ZA",
    "singapore sg": "SG", "hong kong hk": "HK", "taipei": "TW",
    "seoul kr": "KR", "busan": "KR", "karachi": "PK", "lahore": "PK",
    "dhaka": "BD", "kathmandu": "NP", "colombo": "LK", "kabul": "AF",
    "dubai": "AE", "abu dhabi": "AE", "doha": "QA", "riyadh": "SA", "tel aviv": "IL",
    "cairo": "EG", "lagos": "NG", "abuja": "NG", "nairobi": "KE", "accra": "GH",
    "casablanca": "MA", "tunis": "TN", "algiers": "DZ", "kampala": "UG",
    "johannesburg za": "ZA", "cape town za": "ZA",
}

#: A named region, which §3.4 step 3 rejects outright. Not a country, and no region maps to
#: an alpha-2 code — `LATAM` is a dozen countries, so reading it as one would be a guess with
#: no evidence behind it. These are counted in `country_unresolved_count` rather than
#: dropped, because the size of this bucket is a data-quality number worth publishing.
REGION_TOKENS = frozenset({
    "latam", "latin america", "apac", "asia pacific", "emea", "europe", "european union",
    "eu", "americas", "north america", "south america", "central america", "asia", "africa",
    "middle east", "anz", "emea region", "global regions", "international region",
    "greater metropolitan area", "gma",
})

#: Words that carry no place, split from `REGION_TOKENS` only so the report can say which
#: kind of non-answer it got. Both end in `country = NULL`.
#:
#: §3.4 step 4 strips these at **word level**, and the list is built from the strings the
#: committed captures actually contain — `remote`, `remoto`, `select`, `locations` — plus
#: the worldwide words that are an explicit no-place assertion. A word is added here only if
#: some captured or contract-quoted string needs it; `virtual` and `anywhere`-as-a-city do
#: not appear, and adding speculative words is how a table starts eating real place names.
NO_PLACE_TOKENS = frozenset({
    "remote", "remoto", "anywhere", "worldwide", "globally", "global", "international",
    "select", "locations", "location", "based", "only",
})


# --------------------------------------------------------------------------- splitting
#: `§3.4` step 2 splits on `,` and `" - "`. The other two separators are the ATS providers'
#: own shapes, both measured in the committed degenerate files and named in
#: `tasks/f1-07.md`'s acceptance criteria: `;` joins a Greenhouse board's multiple locations
#: (`'Remote - Ireland; Remote - United Kingdom'`) and `:` follows the country in
#: `'Remote - US: Select locations'`.
#:
#: The dash is split on **whitespace on at least one side**, never on a bare `-`. That
#: admits both `" - "` and the half-spaced `"US FL Miami - Remote"`, and keeps
#: `'Uluberia-II,'` — one of the seven token forms the contract lists as unresolvable, and
#: only because it is junk — from being shredded into two components that are each junk too.
_SPLIT = re.compile(r"\s*[;:]\s*|\s+-\s+|\s+-\s|\s*,\s*")


def split_components(raw: str) -> list[str]:
    """The location string's place components, in source order, empties dropped.

    §3.4 step 2's "drop empty trailing components" is what keeps `'Greater Newcastle Area, '`
    from producing a final empty string that a table lookup would then have to reject. Source
    order is preserved throughout: `country` is "first resolved, in a documented order"
    (schema.md §3.1) and the documented order is the order the source wrote.
    """
    return [part.strip() for part in _SPLIT.split(raw or "") if part and part.strip()]


def _strip_qualifiers(component: str) -> str:
    """§3.4 step 4: drop remote-scope words from a component, keeping what is left.

    Word level, not whole-string, because the sources produce both shapes:
    `"Remote - US"` splits into a component that is nothing but a qualifier, and
    `"Select USA Remote Locations"` is three qualifier words around one place name inside a
    single component. Whole-string matching silently drops both.
    """
    return " ".join(word for word in component.split() if fold(word) not in NO_PLACE_TOKENS)


# -------------------------------------------------------------------------- the lookup
def _lookup_component(component: str) -> tuple[str | None, str]:
    """`(code, how)` for one component, or `(None, why_it_failed)`.

    The order is §3.4 step 5 — most specific table first — with the two-letter rule of step 6
    hoisted above `COUNTRY_INDEX` for the reason in the module docstring: `COUNTRY_INDEX`
    holds the alpha-2 codes, so a two-letter token read against it first would resolve `IN`
    to India and never reach the guard that exists to stop exactly that.
    """
    folded = fold(component)
    if not folded:
        return None, "empty"
    if folded in REGION_TOKENS:
        return None, f"region:{folded}"
    if folded in NO_PLACE_TOKENS:
        return None, f"no_place:{folded}"

    is_two_letters = len(folded) == 2 and folded.isalpha()

    if not is_two_letters:
        # Most specific first. `CITY_COUNTRY` is ahead of `SUBDIVISIONS` so `"New York"` is
        # the city and `"Texas"` is the state; both resolve to `US`, and the order only
        # matters for a name that is in both.
        for table, label in ((CITY_COUNTRY, "city"), (SUBDIVISIONS, "subdivision")):
            if folded in table:
                return table[folded], f"{label}:{folded}->{table[folded]}"
        if folded in COUNTRY_INDEX:
            return COUNTRY_INDEX[folded], f"country:{folded}->{COUNTRY_INDEX[folded]}"
    else:
        if folded in STATE_TOKEN:
            return "US", f"us_state:{folded}->US"
        if folded in PROVINCE_TOKEN:
            return "CA", f"ca_province:{folded}->CA"
        if folded in COUNTRY_INDEX:
            return COUNTRY_INDEX[folded], f"country:{folded}->{COUNTRY_INDEX[folded]}"

    # A multi-word component that is not itself a table key is tried as its own words. This
    # is what resolves `"US FL Miami"`, which no table has a row for and which is a real
    # Greenhouse string in the committed capture. Left-to-right, first hit wins, so the
    # leading country or state is preferred — `US FL Miami` is US by the first word alone,
    # and `Greater Newcastle Area` is GB by the only word that names a place.
    for word in folded.split():
        if word in NO_PLACE_TOKENS or word in REGION_TOKENS:
            continue
        if not (len(word) == 2 and word.isalpha()):
            if word in CITY_COUNTRY:
                return CITY_COUNTRY[word], f"city_word:{word}->{CITY_COUNTRY[word]}"
            if word in SUBDIVISIONS:
                return SUBDIVISIONS[word], f"subdivision_word:{word}->{SUBDIVISIONS[word]}"
            if word in COUNTRY_INDEX:
                return COUNTRY_INDEX[word], f"country_word:{word}->{COUNTRY_INDEX[word]}"
            continue
        if word in STATE_TOKEN:
            return "US", f"us_state_word:{word}->US"
        if word in PROVINCE_TOKEN:
            return "CA", f"ca_province_word:{word}->CA"
        if word in COUNTRY_INDEX:
            return COUNTRY_INDEX[word], f"country_word:{word}->{COUNTRY_INDEX[word]}"

    return None, f"unmatched:{folded}"


def _rule(body: str, suffix: str = "") -> str:
    """`rule:<body>)` with the body cut so the whole string fits `RULE_MAX_CHARS`.

    Only the body is truncated, never the delimiters, so an audit string is always parseable
    by eye — the same rule and the same reason as `himalayas._rule`.
    """
    prefix = "rule:free_text_location("
    room = RULE_MAX_CHARS - len(prefix) - len(suffix) - 1
    return f"{prefix}{body[:room]}){suffix}"


def resolve_free_text(raw: str | None) -> dict:
    """One free-text location string → the contract's four location columns, plus the rule.

    Steps 1 to 7 of §3.4 in order:

    1. encoding repair, recording the flag
    2. split on `,` / `" - "` (and the ATS `;` / `:`), dropping empty components
    3. reject region tokens
    4. strip remote-scope words at word level
    5. look up most-specific table first
    6. two-letter tokens are ambiguous and read state/province first
    7. first resolved country is `country`; all resolved, de-duplicated and
       order-preserved are `countries_all`; neither is set when nothing resolved

    A component is tried **as written first, then with its qualifiers stripped**, because
    each order rescues a class the other breaks: stripping first turns `Redwood City` into
    nothing, and trying as written only turns `Remote UK` into a row that has no meaning.
    Both orders were measured by the contract and each breaks a class the other rescues.

    `remote_scope` here is `country_restricted` or `unknown`, never `global` — see the module
    docstring. `unresolved_components` is an audit extra, not a column; it is what
    `country_unresolved_count` is a count of.
    """
    original = "" if raw is None else str(raw)
    repaired, was_repaired = repair_encoding(original)

    codes: list[str] = []
    trace: list[str] = []
    unresolved: list[str] = []
    components = split_components(repaired)
    if not components:
        # A blank location is a measured, meaningful state: RemoteOK serves `""` on 36 of 99
        # captured rows. It gets its own trace token so the report can distinguish "the
        # source published nothing" from "we failed to parse something it published" — the
        # two are different data-quality findings and only one of them is ours.
        trace.append("blank_location")
    for component in components:
        code, how = _lookup_component(component)
        if code is not None:
            trace.append(how)
            if code not in codes:
                codes.append(code)
            continue
        # Tried as written first, then with its qualifiers stripped: each order rescues a
        # class the other breaks (`Redwood City` vs `Remote UK`).
        stripped = _strip_qualifiers(component)
        if stripped and stripped != component:
            code, how = _lookup_component(stripped)
            if code is not None:
                trace.append(how)
                if code not in codes:
                    codes.append(code)
                continue
        trace.append(how)
        unresolved.append(component)

    if codes:
        return {
            "country": codes[0],
            "countries_all": codes,
            "remote_scope": "country_restricted",
            "location_raw": collapse_whitespace(repaired),
            "location_encoding_repaired": 1 if was_repaired else 0,
            "unresolved_components": unresolved,
            "rule": _rule(";".join(trace)),
        }
    return {
        "country": None,
        "countries_all": None,
        "remote_scope": "unknown",
        "location_raw": collapse_whitespace(repaired),
        "location_encoding_repaired": 1 if was_repaired else 0,
        "unresolved_components": unresolved,
        "rule": _rule(";".join(trace), "->unknown"),
    }


def structured_locations(values: Iterable[str | None]) -> list[str]:
    """The location strings an ATS posting published, in its own order, blanks dropped.

    Greenhouse's `location.name` and Lever's `categories.location` are single strings, so
    this is `[]` or a one-element list. Ashby publishes `location` plus a
    `secondaryLocations[]` of objects, each with its own `location` and a
    `postalAddress.addressCountry`; a posting listed in New York and San Francisco belongs
    in both countries' coverage, which is what `countries_all` exists for (schema.md §3.2).
    Order is the source's, so `country` is the posting's primary location.
    """
    return [str(value).strip() for value in values if value and str(value).strip()]
