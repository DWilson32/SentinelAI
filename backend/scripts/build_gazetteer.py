"""Build the place index the geocoder reads, from GeoNames dump files.

Inputs, from https://download.geonames.org/export/dump/ (CC BY 4.0):
countryInfo.txt, admin1CodesASCII.txt and cities15000.txt (unzipped).

    python scripts/build_gazetteer.py DIR_WITH_THE_THREE_FILES

Writes app/data/gazetteer.json.gz. Only what headlines name is kept:
countries, first-level regions, and cities that are a capital, a regional
seat or have at least 100,000 people.
"""

import csv
import gzip
import json
import sys
from collections import defaultdict
from pathlib import Path

OUTPUT = Path(__file__).resolve().parents[1] / "app" / "data" / "gazetteer.json.gz"
MIN_CITY_POPULATION = 100_000
REGION_SUFFIXES = (
    " State",
    " Region",
    " Province",
    " Governorate",
    " Oblast",
    " District",
    " Department",
    " Prefecture",
    " County",
    " Division",
    " Territory",
    " City",
)

csv.field_size_limit(10**7)


def read_tsv(path: Path):
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.reader(handle, delimiter="\t", quoting=csv.QUOTE_NONE):
            if row and not row[0].startswith("#"):
                yield row


def region_names(name: str, ascii_name: str) -> list[str]:
    names = {name, ascii_name}
    for value in list(names):
        for suffix in REGION_SUFFIXES:
            if value.endswith(suffix) and len(value) > len(suffix) + 2:
                names.add(value[: -len(suffix)])
    return sorted(names)


def main(source: Path) -> None:
    cities = []
    by_country = defaultdict(list)
    by_region = defaultdict(list)
    for row in read_tsv(source / "cities15000.txt"):
        city = {
            "name": row[1],
            "ascii": row[2],
            "lat": round(float(row[4]), 4),
            "lon": round(float(row[5]), 4),
            "code": row[7],
            "country": row[8],
            "admin1": row[10],
            "pop": int(row[14] or 0),
        }
        cities.append(city)
        by_country[city["country"]].append(city)
        by_region[f"{city['country']}.{city['admin1']}"].append(city)

    countries = {}
    for row in read_tsv(source / "countryInfo.txt"):
        iso, name, capital = row[0], row[4].strip(), row[5].strip()
        members = by_country.get(iso)
        if not members:
            continue  # no city of 15,000+: nowhere to put a marker
        # Capital as the country's point: always on land and a real place,
        # unlike a geometric centroid, which can fall in the sea.
        anchor = next((c for c in members if capital and capital in (c["name"], c["ascii"])), None)
        anchor = anchor or max(members, key=lambda c: c["pop"])
        countries[iso] = {"name": name, "lat": anchor["lat"], "lon": anchor["lon"]}

    regions = []
    for row in read_tsv(source / "admin1CodesASCII.txt"):
        code, name, ascii_name = row[0], row[1], row[2]
        members = by_region.get(code)
        if not members or code.split(".")[0] not in countries:
            continue
        seats = [c for c in members if c["code"] == "PPLA"]
        seat = max(seats or members, key=lambda c: c["pop"])
        regions.append(
            {
                "label": name,
                "names": region_names(name, ascii_name),
                "country": code.split(".")[0],
                "lat": seat["lat"],
                "lon": seat["lon"],
                "pop": seat["pop"],
            }
        )

    kept = [
        {
            "label": c["name"],
            "names": sorted({c["name"], c["ascii"]}),
            "country": c["country"],
            "lat": c["lat"],
            "lon": c["lon"],
            "pop": c["pop"],
        }
        for c in cities
        if c["country"] in countries and (c["code"] in ("PPLC", "PPLA") or c["pop"] >= MIN_CITY_POPULATION)
    ]

    data = {
        "source": "GeoNames (geonames.org), CC BY 4.0",
        "countries": countries,
        "regions": regions,
        "cities": kept,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(OUTPUT, "wt", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, separators=(",", ":"))
    print(f"{len(countries)} countries, {len(regions)} regions, {len(kept)} cities -> {OUTPUT} ({OUTPUT.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
