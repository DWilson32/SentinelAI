"""Find the place a headline is about, for reports that arrive without
coordinates.

News feeds file every story under "Global"; the place is usually in the
headline. Names come from GeoNames (app/data/gazetteer.json.gz, built by
scripts/build_gazetteer.py) plus the curated lists below.

The rules lean towards precision: a report stays unlocated rather than being
put in the wrong place. Each was prompted by a live headline:

* The publisher is dropped first, whether it trails the headline ("... -
  Israel National News") or leads it ("The New York Times. At least 50 ...").
* Adjectives are not places: "Libya-style partition", "Iran-backed militia".
* Demonyms name actors, not locations: "Russian shelling attack in Kyiv" is in
  Kyiv. They only help choose between same-named places.
* The US is a party to most international stories ("US ceasefire plan for
  Sudan"), so it loses to any other place named.
* The most specific place wins: city, then region or sea, then country. Among
  places equally specific, one after "in", "near" and the like wins, but that
  never lifts a country over a region: "in Myanmar's Rakhine state" is Rakhine.
* Names that are also common words or first names (Nice, Mobile, Victoria,
  David, Batman) are not matched at all.
"""

import gzip
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data" / "gazetteer.json.gz"

COUNTRY_ALIASES = {
    "US": "US",
    "U.S.": "US",
    "USA": "US",
    "U.S.A.": "US",
    "America": "US",
    "United States of America": "US",
    "Washington": "US",
    "UK": "GB",
    "U.K.": "GB",
    "Britain": "GB",
    "Great Britain": "GB",
    "England": "GB",
    "Scotland": "GB",
    "Wales": "GB",
    "Northern Ireland": "GB",
    "Burma": "MM",
    "Palestine": "PS",
    "Palestinian territories": "PS",
    "DR Congo": "CD",
    "DRC": "CD",
    "Congo": "CD",
    "Congo-Brazzaville": "CG",
    "Côte d'Ivoire": "CI",
    "Cote d'Ivoire": "CI",
    "Türkiye": "TR",
    "Turkiye": "TR",
    "Czech Republic": "CZ",
    "UAE": "AE",
    "Emirates": "AE",
    "Saudi": "SA",
    "Netherlands": "NL",
    "Holland": "NL",
    "Timor-Leste": "TL",
    "East Timor": "TL",
    "Viet Nam": "VN",
    "Macedonia": "MK",
    "Swaziland": "SZ",
    "Cape Verde": "CV",
    "Macau": "MO",
    "Vatican City": "VA",
    "The Gambia": "GM",
    "The Bahamas": "BS",
    "Russian Federation": "RU",
}

# Used only to choose between same-named places, never as the location.
DEMONYMS = {
    "American": "US",
    "Afghan": "AF",
    "Algerian": "DZ",
    "Argentine": "AR",
    "Armenian": "AM",
    "Australian": "AU",
    "Azerbaijani": "AZ",
    "Bahraini": "BH",
    "Bangladeshi": "BD",
    "Belarusian": "BY",
    "Bolivian": "BO",
    "Brazilian": "BR",
    "British": "GB",
    "Burmese": "MM",
    "Burundian": "BI",
    "Cambodian": "KH",
    "Canadian": "CA",
    "Chadian": "TD",
    "Chilean": "CL",
    "Chinese": "CN",
    "Colombian": "CO",
    "Congolese": "CD",
    "Cuban": "CU",
    "Ecuadorian": "EC",
    "Egyptian": "EG",
    "Emirati": "AE",
    "Eritrean": "ER",
    "Ethiopian": "ET",
    "Filipino": "PH",
    "French": "FR",
    "Georgian": "GE",
    "German": "DE",
    "Greek": "GR",
    "Haitian": "HT",
    "Indian": "IN",
    "Indonesian": "ID",
    "Iranian": "IR",
    "Iraqi": "IQ",
    "Israeli": "IL",
    "Italian": "IT",
    "Japanese": "JP",
    "Jordanian": "JO",
    "Kenyan": "KE",
    "Kuwaiti": "KW",
    "Lebanese": "LB",
    "Libyan": "LY",
    "Malaysian": "MY",
    "Malian": "ML",
    "Mexican": "MX",
    "Moroccan": "MA",
    "Mozambican": "MZ",
    "Nepali": "NP",
    "Nigerian": "NG",
    "North Korean": "KP",
    "Omani": "OM",
    "Pakistani": "PK",
    "Palestinian": "PS",
    "Peruvian": "PE",
    "Qatari": "QA",
    "Russian": "RU",
    "Rwandan": "RW",
    "Saudi Arabian": "SA",
    "Serbian": "RS",
    "Somali": "SO",
    "South African": "ZA",
    "South Korean": "KR",
    "South Sudanese": "SS",
    "Spanish": "ES",
    "Sri Lankan": "LK",
    "Sudanese": "SD",
    "Syrian": "SY",
    "Taiwanese": "TW",
    "Thai": "TH",
    "Tunisian": "TN",
    "Turkish": "TR",
    "Ugandan": "UG",
    "Ukrainian": "UA",
    "Venezuelan": "VE",
    "Vietnamese": "VN",
    "Yemeni": "YE",
    "Zimbabwean": "ZW",
}

# Seas and straits that conflict and shipping stories are about; GeoNames'
# populated-place dump has none. Approximate centres.
FEATURES = {
    "Strait of Hormuz": (26.57, 56.25),
    "Red Sea": (20.0, 38.5),
    "Gulf of Aden": (12.5, 48.0),
    "Bab el-Mandeb": (12.58, 43.33),
    "Black Sea": (43.4, 34.0),
    "Sea of Azov": (46.1, 36.6),
    "South China Sea": (12.0, 113.0),
    "Taiwan Strait": (24.3, 119.5),
    "Persian Gulf": (26.9, 51.4),
    "Golan Heights": (33.0, 35.75),
}
FEATURE_ALIASES = {"Hormuz": "Strait of Hormuz", "Golan": "Golan Heights"}

# Older spellings and wider names, as (GeoNames name, country, kind).
PLACE_ALIASES = {
    "Kiev": ("Kyiv", "UA", "city"),
    "Odessa": ("Odesa", "UA", "city"),
    "Kharkov": ("Kharkiv", "UA", "city"),
    "Gaza City": ("Gaza", "PS", "city"),
    "Darfur": ("Northern Darfur", "SD", "region"),
    "Donbas": ("Donetsk", "UA", "region"),
    "Donbass": ("Donetsk", "UA", "region"),
    "Sinai": ("North Sinai", "EG", "region"),
}

# GeoNames names that headlines use as ordinary words or first names, and
# region names that are only a compass direction or a generic term.
NOT_PLACES = {
    "Augusta", "Austin", "Batman", "Bismarck", "Capital", "Center", "Central", "Centre",
    "Charleston", "Charlotte", "Cheyenne", "Coast", "Columbia", "Concord", "David",
    "Delta", "Douglas", "Dover", "East", "Eastern", "Elizabeth", "Enterprise",
    "Florence", "Free State", "Gilbert", "Hamilton", "Helena", "Henderson", "Hope",
    "Independence", "Irving", "Jackson", "Kingston", "Lafayette", "Lakes", "Lincoln",
    "Madison", "Mary", "Middle", "Mission", "Mobile", "Montgomery", "Nice", "Normal",
    "North", "North West", "Northern", "Olympia", "Orange", "Paradise", "Pierre",
    "Plateau", "Providence", "Raleigh", "Reading", "Red Sea", "Regina", "Richmond",
    "Rivers", "Salem", "South", "Southern", "Split", "Surprise", "Unity", "Upper",
    "Victoria", "Wellington", "West", "Western",
}

# Newspapers named after places, stripped before matching.
PUBLICATIONS = (
    "New York Times", "Washington Post", "Wall Street Journal", "Los Angeles Times",
    "Jerusalem Post", "Times of Israel", "Kyiv Independent", "Kyiv Post", "Moscow Times",
    "Tehran Times", "Japan Times", "Straits Times", "China Daily", "Arab News", "Gulf News",
    "Israel National News", "Bangkok Post", "Hindustan Times", "Times of India", "Irish Times",
    "Sydney Morning Herald", "Hong Kong Free Press", "South China Morning Post", "Kenya News",
)

LOCATIVES = {"in", "near", "at", "across", "inside", "outside", "into", "on", "over", "around", "throughout", "toward", "towards", "off"}
SPECIFICITY = {"city": 4, "region": 3, "feature": 3, "country": 1}
PRECISION = {"city": "city", "region": "region", "feature": "region", "country": "country"}
# A party to most international stories, rarely the place they happen.
ACTORS = {"US"}

_PUBLISHER_SEPARATOR = re.compile(r"\s[-–—|]\s")
_EDGE_PUNCTUATION = "\"“”‘’'()[]{},;:!?«»*"


@dataclass(frozen=True)
class Place:
    label: str
    latitude: float
    longitude: float
    precision: str  # "city", "region" or "country"
    country: str | None  # ISO code; None for a sea or strait


@dataclass(frozen=True)
class _Candidate:
    kind: str
    label: str
    country: str | None
    latitude: float
    longitude: float
    population: int = 0
    demonym: bool = False


@dataclass(frozen=True)
class _Token:
    text: str
    adjectival: bool = False


def locate(text: str) -> Place | None:
    """The place a headline is about, or None when it names none reliably."""
    index = _index()
    tokens = _tokens(_without_publisher(text))

    mentions: list[tuple[int, list[_Candidate], bool]] = []
    position = 0
    while position < len(tokens):
        for size in (4, 3, 2, 1):
            group = tokens[position : position + size]
            if len(group) < size:
                continue
            form = " ".join(token.text for token in group)
            if form and form in index:
                if not group[-1].adjectival:
                    locative = position > 0 and tokens[position - 1].text.lower() in LOCATIVES
                    mentions.append((position, index[form], locative))
                position += size
                break
        else:
            position += 1

    context = {c.country for _, candidates, _ in mentions for c in candidates if c.kind == "country"}
    scored = []
    for position, candidates, locative in mentions:
        candidate = _pick(candidates, context)
        if candidate.demonym:
            continue
        score = SPECIFICITY[candidate.kind] + (1.0 if locative else 0.0)
        if candidate.kind == "country" and candidate.country in ACTORS:
            score -= 2.0
        if position == 0 and len(mentions) > 1:
            score -= 0.5  # a leading capital or country is usually the subject: "Moscow says ..."
        scored.append((score, -position, candidate))
    if not scored:
        return None
    score, _, best = max(scored, key=lambda item: (item[0], item[1]))
    if score <= 0:
        return None
    return Place(best.label, best.latitude, best.longitude, PRECISION[best.kind], best.country)


def _pick(candidates: list[_Candidate], context: set[str | None]) -> _Candidate:
    """The most specific reading of a name; among same-named places, one in a
    country the text also names, unless it is far smaller than the largest."""
    kind = max(candidates, key=lambda c: SPECIFICITY[c.kind]).kind
    same = [c for c in candidates if c.kind == kind]
    largest = max(same, key=lambda c: c.population)
    local = [c for c in same if c.country in context and c.population * 10 >= largest.population]
    return max(local, key=lambda c: c.population) if local else largest


def _without_publisher(title: str) -> str:
    parts = _PUBLISHER_SEPARATOR.split(title)
    if len(parts) > 1 and len(parts[-1].split()) <= 6:
        title = " | ".join(parts[:-1])
    for publication in PUBLICATIONS:
        title = title.replace(publication, " | ")
    return title


def _tokens(text: str) -> list[_Token]:
    index = _index()
    tokens: list[_Token] = []
    for raw in text.split():
        word = raw.strip(_EDGE_PUNCTUATION)
        if word.endswith(("'s", "’s")):
            word = word[:-2]
        if word not in index and word.endswith("."):
            word = word[:-1]
        if "-" in word and word not in index:
            # "US-Iran" names two places; "Libya-style" and "Iran-backed" name none.
            parts = word.split("-")
            for i, part in enumerate(parts):
                following = parts[i + 1] if i + 1 < len(parts) else ""
                tokens.append(_Token(part, adjectival=following[:1].islower()))
            continue
        tokens.append(_Token(word))
    return tokens


@lru_cache(maxsize=1)
def _index() -> dict[str, list[_Candidate]]:
    with gzip.open(DATA, "rt", encoding="utf-8") as handle:
        data = json.load(handle)
    countries = data["countries"]
    index: dict[str, list[_Candidate]] = defaultdict(list)

    def country(iso: str, demonym: bool = False) -> _Candidate:
        entry = countries[iso]
        return _Candidate("country", entry["name"], iso, entry["lat"], entry["lon"], demonym=demonym)

    for iso, entry in countries.items():
        index[entry["name"]].append(country(iso))
    for alias, iso in COUNTRY_ALIASES.items():
        index[alias].append(country(iso))
    for demonym, iso in DEMONYMS.items():
        for form in (demonym, f"{demonym}s"):
            index[form].append(country(iso, demonym=True))
    # A country's own name never means a region or city of the same name.
    country_forms = set(index)

    for name, (lat, lon) in FEATURES.items():
        index[name].append(_Candidate("feature", name, None, lat, lon))
    for alias, name in FEATURE_ALIASES.items():
        index[alias].extend(index[name])

    def usable(form: str) -> bool:
        return len(form) > 3 and form not in NOT_PLACES and form not in country_forms and form not in FEATURES

    for kind, entries in (("region", data["regions"]), ("city", data["cities"])):
        for entry in entries:
            label = f"{entry['label']}, {countries[entry['country']]['name']}"
            candidate = _Candidate(kind, label, entry["country"], entry["lat"], entry["lon"], entry["pop"])
            for form in entry["names"]:
                if usable(form):
                    index[form].append(candidate)

    for alias, (name, iso, kind) in PLACE_ALIASES.items():
        index[alias].extend(c for c in index.get(name, []) if c.country == iso and c.kind == kind)
    return dict(index)
