"""
Build the geo.db SQLite from the Census ZCTA-to-county relationship file.

Input:  templates/property_analysis/data/census_zcta_county_2020.txt
  (downloaded once from https://www2.census.gov/geo/docs/maps-data/data/rel2020/zcta520/tab20_zcta520_county20_natl.txt)
Output: templates/property_analysis/data/geo.db

Schema:
  zip_county(zip TEXT, county_fips TEXT, county_name TEXT,
             state_fips TEXT, state TEXT, land_area_m2 INTEGER)
  metadata(key TEXT PRIMARY KEY, value TEXT)

Lookup pattern (in geo.py):
  - Single match: high-confidence county.
  - Multiple matches: pick the row with the highest land_area_m2 (dominant county).
    Also return alternates so the caller can flag the ambiguity.

Run annually after the Census refresh:
    python -m scripts.property_analysis.build_geo_db
"""
from __future__ import annotations

import csv
import hashlib
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "templates" / "property_analysis" / "data"
CENSUS_TXT = DATA_DIR / "census_zcta_county_2020.txt"
DB_PATH = DATA_DIR / "geo.db"

# State FIPS -> 2-letter abbreviation. Covers all 50 states + DC + territories.
STATE_FIPS_TO_ABBR: dict[str, str] = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO",
    "09": "CT", "10": "DE", "11": "DC", "12": "FL", "13": "GA", "15": "HI",
    "16": "ID", "17": "IL", "18": "IN", "19": "IA", "20": "KS", "21": "KY",
    "22": "LA", "23": "ME", "24": "MD", "25": "MA", "26": "MI", "27": "MN",
    "28": "MS", "29": "MO", "30": "MT", "31": "NE", "32": "NV", "33": "NH",
    "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND", "39": "OH",
    "40": "OK", "41": "OR", "42": "PA", "44": "RI", "45": "SC", "46": "SD",
    "47": "TN", "48": "TX", "49": "UT", "50": "VT", "51": "VA", "53": "WA",
    "54": "WV", "55": "WI", "56": "WY",
    "60": "AS", "66": "GU", "69": "MP", "72": "PR", "78": "VI",
}


def file_fingerprint(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()[:16]


def build_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        DROP TABLE IF EXISTS zip_county;
        DROP TABLE IF EXISTS metadata;

        CREATE TABLE zip_county (
            zip          TEXT NOT NULL,
            county_fips  TEXT NOT NULL,
            county_name  TEXT NOT NULL,
            state_fips   TEXT NOT NULL,
            state        TEXT NOT NULL,
            land_area_m2 INTEGER,
            PRIMARY KEY (zip, county_fips)
        );
        CREATE INDEX idx_zip_county_zip ON zip_county(zip);
        CREATE INDEX idx_zip_county_state ON zip_county(state);

        CREATE TABLE metadata (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
    """)


def load_census(conn: sqlite3.Connection) -> tuple[int, int]:
    """Return (rows_inserted, unique_zips)."""
    if not CENSUS_TXT.exists():
        raise FileNotFoundError(
            f"Census source file not found at {CENSUS_TXT}. "
            "Download it first via the script in this folder."
        )

    rows_inserted = 0
    seen_zips: set[str] = set()
    batch: list[tuple] = []

    with CENSUS_TXT.open("r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter="|")
        for row in reader:
            zip_code = (row.get("GEOID_ZCTA5_20") or "").strip()
            county_fips = (row.get("GEOID_COUNTY_20") or "").strip()
            if not zip_code or not county_fips:
                continue
            zip_code = zip_code.zfill(5)
            county_fips = county_fips.zfill(5)
            state_fips = county_fips[:2]
            state_abbr = STATE_FIPS_TO_ABBR.get(state_fips, "")
            county_name = (row.get("NAMELSAD_COUNTY_20") or "").strip()
            land_area = row.get("AREALAND_PART") or "0"
            try:
                land_area_int = int(land_area)
            except (ValueError, TypeError):
                land_area_int = 0

            batch.append((zip_code, county_fips, county_name,
                          state_fips, state_abbr, land_area_int))
            seen_zips.add(zip_code)
            if len(batch) >= 1000:
                conn.executemany(
                    "INSERT OR REPLACE INTO zip_county VALUES (?,?,?,?,?,?)",
                    batch,
                )
                rows_inserted += len(batch)
                batch.clear()
    if batch:
        conn.executemany(
            "INSERT OR REPLACE INTO zip_county VALUES (?,?,?,?,?,?)",
            batch,
        )
        rows_inserted += len(batch)
    return rows_inserted, len(seen_zips)


def write_metadata(conn: sqlite3.Connection, rows: int, unique_zips: int) -> None:
    md = [
        ("built_at", datetime.utcnow().isoformat(timespec="seconds") + "Z"),
        ("source", "Census Bureau ZCTA-County relationship 2020"),
        ("source_url",
         "https://www2.census.gov/geo/docs/maps-data/data/rel2020/zcta520/tab20_zcta520_county20_natl.txt"),
        ("vintage", "2020"),
        ("rows", str(rows)),
        ("unique_zips", str(unique_zips)),
        ("source_sha256_16", file_fingerprint(CENSUS_TXT)),
    ]
    conn.executemany("INSERT INTO metadata VALUES (?, ?)", md)


def build() -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(DB_PATH)
    try:
        build_schema(conn)
        print(f"Loading {CENSUS_TXT.name}...")
        rows, unique_zips = load_census(conn)
        print(f"  Rows inserted: {rows:,}")
        print(f"  Unique ZIPs:   {unique_zips:,}")
        write_metadata(conn, rows, unique_zips)
        conn.commit()
    finally:
        conn.close()
    size_mb = DB_PATH.stat().st_size / 1024 / 1024
    print(f"\nBuilt {DB_PATH} ({size_mb:.2f} MB)")

    # Sanity check
    conn = sqlite3.connect(DB_PATH)
    print("\nSanity check:")
    for zip_code in ("15215", "72202", "40601", "19103", "99999"):
        rows = conn.execute(
            "SELECT zip, county_name, state, land_area_m2 FROM zip_county WHERE zip=? "
            "ORDER BY land_area_m2 DESC",
            (zip_code,),
        ).fetchall()
        if rows:
            print(f"  ZIP {zip_code}: {len(rows)} county match(es)")
            for r in rows[:3]:
                print(f"    -> {r[1]} ({r[2]})  land={r[3]:,}")
        else:
            print(f"  ZIP {zip_code}: NO MATCH")
    conn.close()


if __name__ == "__main__":
    build()
