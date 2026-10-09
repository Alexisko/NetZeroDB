"""
export_web_data.py
Exports the prepared Shiny datasets (data_prepared/*.rds) to compact JSON files
consumed by the static website in docs/.

Input:  data_prepared/sbti_targets.rds
        data_prepared/uk_ccc_subsector.rds
        data_prepared/snbc_emissions.rds
        data_prepared/iea_emissions.rds
        data_prepared/sbti_nzdpu_matched.rds
        R/utils/sector_mapping.R           (harmonized sector taxonomy, parsed)
Output: docs/data/*.json
        docs/data/sbti_wording/NN.json     (target wording, sharded by company)

Run from the project root, after the R processing scripts:
    pip install -r scripts/requirements-web.txt
    python scripts/export_web_data.py

Raw values are preserved; the only transformations are type coercion,
dictionary-encoding of repeated strings (to keep files small) and dropping
columns the website does not use.
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pyreadr

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data_prepared"
OUT = ROOT / "docs" / "data"
WORDING_SHARDS = 32


# ── Helpers ───────────────────────────────────────────────────────────────────

def read_rds(name: str) -> pd.DataFrame:
    return pyreadr.read_r(str(SRC / f"{name}.rds"))[None]


def clean(v, digits: int | None = None):
    """Convert a pandas/numpy scalar to a JSON-safe Python value (NA -> None)."""
    if v is None or v is pd.NA or v is pd.NaT:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if hasattr(v, "item"):
        v = v.item()
    if isinstance(v, float):
        if math.isnan(v):
            return None
        if digits is not None:
            v = round(v, digits)
        if v.is_integer() and abs(v) < 1e15:
            return int(v)
    return v


def as_int(series: pd.Series) -> list:
    return [None if pd.isna(v) else int(float(v)) for v in series]


def as_num(series: pd.Series, digits: int = 6) -> list:
    return [clean(float(v), digits) if not pd.isna(v) else None for v in series]


def as_str(series: pd.Series) -> list:
    return [None if pd.isna(v) else str(v) for v in series]


def as_bool(series: pd.Series) -> list:
    return [None if pd.isna(v) else bool(v) for v in series]


def dict_encode(series: pd.Series) -> tuple[list, list]:
    """Return (levels, codes) for a string column; NA is encoded as -1."""
    values = as_str(series)
    levels = sorted({v for v in values if v is not None})
    lookup = {v: i for i, v in enumerate(levels)}
    codes = [-1 if v is None else lookup[v] for v in values]
    return levels, codes


def write_json(obj, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    print(f"  wrote {path.relative_to(ROOT)}  ({path.stat().st_size / 1024:,.0f} KB)")


# ── Sector mapping (parsed from the R tribble — single source of truth) ───────

def parse_sector_mapping() -> list[dict]:
    text = (ROOT / "R" / "utils" / "sector_mapping.R").read_text(encoding="utf-8")
    block = text.split("sector_mapping <- tribble(", 1)[1].split("\n)", 1)[0]
    row_re = re.compile(
        r'^\s*"([^"]+)",\s*"([^"]+)",\s*"([^"]+)",\s*(?:"([^"]*)"|NA),?\s*$'
    )
    rows = []
    for line in block.splitlines():
        m = row_re.match(line)
        if m:
            rows.append({
                "source": m.group(1),
                "original_sector": m.group(2),
                "harmonized_sector": m.group(3),
                "harmonized_subsector": m.group(4),
            })
    if not rows:
        raise RuntimeError("Could not parse sector_mapping tribble")
    return rows


# ── Exports ───────────────────────────────────────────────────────────────────

def export_sbti() -> dict:
    df = read_rds("sbti_targets")

    company_levels, company_codes = dict_encode(df["company_name"])
    cols = {}
    levels = {"company_name": company_levels}
    cols["company_name"] = company_codes
    for c in ["sector", "location", "organization_type", "scope", "type",
              "target_classification_short", "commitment_status"]:
        levels[c], cols[c] = dict_encode(df[c])
    cols["sbti_id"] = as_int(df["sbti_id"])
    cols["base_year"] = as_int(df["base_year"])
    cols["target_year"] = as_int(df["target_year"])
    cols["target_value"] = as_num(df["target_value"], 6)

    write_json({"n": len(df), "levels": levels, "cols": cols},
               OUT / "sbti_targets.json")

    # Target wording — coalesce(target_wording, full_target_language), as in the
    # Shiny table. Sharded by company code so a page only fetches what it shows.
    wording = df["target_wording"].where(df["target_wording"].notna(),
                                         df["full_target_language"])
    shards: dict[int, dict] = {i: {} for i in range(WORDING_SHARDS)}
    for row, (code, text) in enumerate(zip(company_codes, wording)):
        if code < 0 or pd.isna(text):
            continue
        shards[code % WORDING_SHARDS][row] = str(text)
    for i, shard in shards.items():
        write_json(shard, OUT / "sbti_wording" / f"{i:02d}.json")

    return {
        "rows": len(df),
        "companies": int(df["company_name"].nunique()),
        "date_updated_max": str(pd.to_datetime(df["date_updated"]).max().date()),
    }


def export_uk_ccc() -> dict:
    df = read_rds("uk_ccc_subsector")
    assert (df["variable_unit"] == "MtCO2e").all()
    levels, cols = {}, {}
    for c in ["scenario", "sector", "subsector"]:
        levels[c], cols[c] = dict_encode(df[c])
    cols["year"] = as_int(df["year"])
    cols["value"] = as_num(df["value"], 6)
    write_json({"n": len(df), "unit": "MtCO2e", "levels": levels, "cols": cols},
               OUT / "uk_ccc_subsector.json")
    return {"rows": len(df), "years": [int(df.year.min()), int(df.year.max())]}


def export_records(name: str, columns: list[str], out_name: str) -> dict:
    df = read_rds(name)
    records = [
        {c: clean(r[c], 6) for c in columns}
        for _, r in df.iterrows()
    ]
    write_json(records, OUT / f"{out_name}.json")
    return {"rows": len(df)}


def export_matched() -> dict:
    df = read_rds("sbti_nzdpu_matched")
    cols = {
        "sbti_id": as_int(df["sbti_id"]),
        "nz_id": as_int(df["nz_id"]),
        "company_name": as_str(df["company_name"]),
        "match_method": as_str(df["match_method"]),
        "sector": as_str(df["sector"]),
        "sics_sector": as_str(df["sics_sector"]),
        "scope": as_str(df["scope"]),
        "base_year": as_int(df["base_year"]),
        "target_year": as_int(df["target_year"]),
        "target_value": as_num(df["target_value"], 6),
        "reporting_year": as_int(df["reporting_year"]),
        "emissions_tco2e": as_num(df["emissions_tco2e"], 3),
        "base_emissions": as_num(df["base_emissions"], 3),
        "emissions_index": as_num(df["emissions_index"], 4),
        "target_index": as_num(df["target_index"], 4),
        "on_track": as_bool(df["on_track"]),
    }
    write_json({"n": len(df), "cols": cols}, OUT / "sbti_nzdpu_matched.json")
    return {
        "rows": len(df),
        "companies": int(df["nz_id"].nunique()),
        "reporting_years": [int(df.reporting_year.min()), int(df.reporting_year.max())],
    }


def main() -> None:
    print("Exporting web data to", OUT.relative_to(ROOT))
    meta = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d")}
    meta["sbti"] = export_sbti()
    meta["uk_ccc"] = export_uk_ccc()
    meta["snbc"] = export_records(
        "snbc_emissions",
        ["source", "scenario", "sector", "year", "value", "unit", "period_label"],
        "snbc_emissions",
    )
    meta["iea"] = export_records(
        "iea_emissions",
        ["source", "scenario", "sector", "year", "value", "unit"],
        "iea_emissions",
    )
    meta["matched"] = export_matched()
    mapping = parse_sector_mapping()
    write_json(mapping, OUT / "sector_mapping.json")
    meta["sector_mapping"] = {"rows": len(mapping)}
    write_json(meta, OUT / "meta.json")


if __name__ == "__main__":
    main()
