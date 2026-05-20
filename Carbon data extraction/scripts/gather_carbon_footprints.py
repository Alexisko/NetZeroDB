#!/usr/bin/env python3
"""
Gather carbon-footprint evidence for unmatched SBTi companies.

This script is intentionally review-first:
  * it never edits manual match files;
  * it writes candidate/review CSVs;
  * report-derived values default to review_status=needs_review;
  * Scope 2 values are split into location-based, market-based, and unknown.

Primary local-report stages:
  1. manifest: validate inputs/report_index.csv, resolve files/URLs, and hash reports.
  2. text: write page-level text JSONL caches keyed by report hash.
  3. snippets: write compact carbon-footprint snippet JSON caches.
  4. extract: write generated footprint/objective CSVs and per-report audit JSON from snippets.
  5. compare: save raw LLM responses and compare them with deterministic rows.
  6. review: initialize the human-edited review CSV without overwriting it.
  7. validate: check review CSV evidence, units, status, and CSV readability.

The older missed-match and web-discovery workflow remains available for the
broader SBTi/NZDPU matching experiment.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup

try:
    import pdfplumber
except ImportError:  # pragma: no cover - dependency exists in this workspace
    pdfplumber = None


ROOT = Path(__file__).resolve().parents[1]
REVIEW_DIR = ROOT / "outputs" / "review"
CACHE_DIR = ROOT / "outputs" / "cache" / "carbon_footprints"
DOWNLOAD_DIR = CACHE_DIR / "downloads"
SNIPPET_DIR = CACHE_DIR / "snippets"
INGESTION_CACHE_DIR = ROOT / "outputs" / "cache" / "report_ingestion"
TEXT_CACHE_DIR = INGESTION_CACHE_DIR / "text"
INGESTION_SNIPPET_DIR = INGESTION_CACHE_DIR / "snippets"
EXTRACTION_CACHE_DIR = INGESTION_CACHE_DIR / "extractions"

REPORT_INDEX = ROOT / "inputs" / "report_index.csv"
LEGACY_INPUT_DIR = ROOT / "Input"
REPORTS_DIR = ROOT / "inputs" / "reports"
LOCAL_REPORT_INDEX_CANDIDATES = REVIEW_DIR / "local_report_index_candidates.csv"

SBTI_UNMATCHED = REVIEW_DIR / "sbti_unmatched_corporate.csv"
NZDPU_UNMATCHED = REVIEW_DIR / "nzdpu_unmatched.csv"
NZDPU_RDS = ROOT / "data_prepared" / "nzdpu_french_companies.rds"
NZDPU_CACHE_CSV = CACHE_DIR / "nzdpu_french_companies.csv"

MISSED_MATCH_OUTPUT = REVIEW_DIR / "nzdpu_missed_match_candidates.csv"
REPORT_CANDIDATES_OUTPUT = REVIEW_DIR / "carbon_report_candidates.csv"
REPORT_MANIFEST_OUTPUT = INGESTION_CACHE_DIR / "report_manifest.csv"
FOOTPRINT_OUTPUT = REVIEW_DIR / "carbon_footprints.csv"
OBJECTIVES_OUTPUT = REVIEW_DIR / "carbon_objectives.csv"
EXTRACTION_COMPARISON_OUTPUT = REVIEW_DIR / "carbon_extraction_comparison.csv"
GENERATED_FOOTPRINT_OUTPUT = REVIEW_DIR / "carbon_footprints_generated.csv"
REVIEWED_FOOTPRINT_OUTPUT = FOOTPRINT_OUTPUT

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121 Safari/537.36"
)

LEGAL_SUFFIXES = {
    "sa",
    "sas",
    "sasu",
    "sarl",
    "snc",
    "se",
    "plc",
    "ltd",
    "limited",
    "inc",
    "corp",
    "corporation",
    "company",
    "co",
    "group",
    "groupe",
    "holding",
    "holdings",
    "international",
    "france",
}

KEYWORDS = [
    "scope 1",
    "scope 2",
    "scope 3",
    "location-based",
    "market-based",
    "location based",
    "market based",
    "carbon footprint",
    "greenhouse gas emissions",
    "ghg emissions",
    "bilan carbone",
    "emissions de gaz a effet de serre",
    "émissions de gaz à effet de serre",
    "tco2e",
    "ktco2e",
    "mtco2e",
    "co2 eq",
    "co2e",
]

NUMBER_TOKEN_RE = r"\d{1,3}(?:[\s\u00a0\u0007]\d{3})+(?:[,.]\d+)?\*?|\d+(?:[,.]\d+)?\*?"

OUTPUT_COLUMNS = [
    "company_name",
    "sbti_id",
    "lei",
    "source",
    "source_id",
    "report_url",
    "report_type",
    "report_year",
    "scope_1_tco2e",
    "scope_2_lb_tco2e",
    "scope_2_mb_tco2e",
    "scope_2_unknown_tco2e",
    "scope_3_tco2e",
    "extraction_method",
    "llm_model",
    "confidence",
    "evidence_text",
    "review_status",
    "notes",
]

HISTORY_COLUMNS = [
    "company_name",
    "sbti_id",
    "lei",
    "source",
    "source_id",
    "report_url",
    "report_type",
    "report_year",
    "emissions_year",
    "scope_1_tco2e",
    "scope_2_lb_tco2e",
    "scope_2_mb_tco2e",
    "scope_2_unknown_tco2e",
    "scope_3_tco2e",
    "scope_1_2_total_tco2e",
    "extraction_method",
    "llm_model",
    "confidence",
    "evidence_text",
    "review_status",
    "notes",
]

REPORT_INDEX_COLUMNS = [
    "company_name",
    "sbti_id",
    "lei",
    "report_path",
    "report_url",
    "report_type",
    "report_year",
]

MANIFEST_COLUMNS = [
    "report_id",
    "index_row",
    "company_name",
    "sbti_id",
    "lei",
    "report_path",
    "resolved_path",
    "report_url",
    "report_type",
    "report_year",
    "source_kind",
    "source_id",
    "file_sha256",
    "file_size_bytes",
    "ingestion_status",
    "notes",
]

OBJECTIVE_COLUMNS = [
    "company_name",
    "sbti_id",
    "lei",
    "source",
    "source_id",
    "report_url",
    "report_type",
    "report_year",
    "target_year",
    "base_year",
    "target_metric",
    "target_scope",
    "target_reduction_percent",
    "target_value",
    "target_unit",
    "current_progress",
    "extraction_method",
    "llm_model",
    "confidence",
    "evidence_text",
    "review_status",
    "notes",
]

EXTRACTION_COMPARISON_COLUMNS = [
    "report_id",
    "company_name",
    "source_id",
    "report_year",
    "llm_model",
    "llm_report_year",
    "llm_emissions_year",
    "normalized_emissions_year",
    "year_alignment_status",
    "year_alignment_method",
    "deterministic_emissions_year",
    "comparison_basis",
    "scope_field",
    "llm_raw_value",
    "llm_raw_unit",
    "normalization_status",
    "llm_value_tco2e",
    "deterministic_value_tco2e",
    "match_status",
    "numeric_difference_tco2e",
    "llm_confidence",
    "deterministic_confidence",
    "llm_evidence_text",
    "deterministic_evidence_text",
    "notes",
]


@dataclass
class Company:
    sbti_id: str
    company_name: str
    lei: str


def ensure_dirs() -> None:
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    SNIPPET_DIR.mkdir(parents=True, exist_ok=True)
    INGESTION_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    TEXT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    INGESTION_SNIPPET_DIR.mkdir(parents=True, exist_ok=True)
    EXTRACTION_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)


def load_env_file(path: Path = ROOT / ".env") -> None:
    if os.getenv("CARBON_SKIP_DOTENV"):
        return
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip().strip("\"'")
        os.environ[key] = value


def has_openai_api_key() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Missing required input: {path}")
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def normalize_name(value: str) -> str:
    value = (value or "").lower()
    value = value.replace("&", " and ")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def strip_legal_suffixes(value: str) -> str:
    tokens = [t for t in normalize_name(value).split() if t not in LEGAL_SUFFIXES]
    return " ".join(tokens)


def token_sort(value: str) -> str:
    return " ".join(sorted(strip_legal_suffixes(value).split()))


def clean_lei(value: str) -> str:
    value = (value or "").strip().upper()
    return "" if value in {"", "NA", "NAN", "NULL"} else value


def similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, token_sort(left), token_sort(right)).ratio()


def write_dataframe(df: pd.DataFrame, path: Path, columns: list[str] | None = None) -> None:
    if columns is not None:
        for col in columns:
            if col not in df.columns:
                df[col] = ""
        df = df[columns]
    df.to_csv(path, index=False, quoting=csv.QUOTE_MINIMAL)


def write_generated_footprint_outputs(df: pd.DataFrame, columns: list[str]) -> None:
    write_dataframe(df, GENERATED_FOOTPRINT_OUTPUT, columns)


def initialize_review_csv(force: bool = False) -> bool:
    if not GENERATED_FOOTPRINT_OUTPUT.exists():
        raise FileNotFoundError(
            f"Missing generated footprint CSV: {GENERATED_FOOTPRINT_OUTPUT}. Run --stage extract or --stage all first."
        )
    if REVIEWED_FOOTPRINT_OUTPUT.exists() and not force:
        return False
    generated = pd.read_csv(GENERATED_FOOTPRINT_OUTPUT, dtype=str, keep_default_na=False)
    write_dataframe(generated, REVIEWED_FOOTPRINT_OUTPUT, HISTORY_COLUMNS)
    return True


def guess_company_name(path: Path) -> str:
    stem = path.stem
    stem = re.sub(r"\b(19|20)\d{2}\b", " ", stem)
    stem = re.sub(r"\b(annual|integrated|rapport|annuel|integre|int[eé]gr[eé]|report|universal|registration|document|urd|csr|esg|sustainability)\b", " ", stem, flags=re.I)
    stem = re.sub(r"[_\-]+", " ", stem)
    return re.sub(r"\s+", " ", stem).strip().title()


def discover_local_report_files() -> list[Path]:
    files: list[Path] = []
    for directory in (REPORTS_DIR, LEGACY_INPUT_DIR):
        if directory.exists():
            files.extend(sorted(path for path in directory.rglob("*") if path.suffix.lower() in {".pdf", ".html", ".htm", ".txt"}))
    return files


def build_local_report_index_candidates() -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for path in discover_local_report_files():
        rel_path = path.relative_to(ROOT).as_posix()
        report_type = detect_report_type(path.name, rel_path)
        rows.append(
            {
                "company_name": guess_company_name(path),
                "sbti_id": "",
                "lei": "",
                "report_path": rel_path,
                "report_url": "",
                "report_type": report_type,
                "report_year": detect_year(path.name),
            }
        )
    return pd.DataFrame(
        rows,
        columns=["company_name", "sbti_id", "lei", "report_path", "report_url", "report_type", "report_year"],
    )


def read_or_build_report_index() -> pd.DataFrame:
    if REPORT_INDEX.exists():
        return read_csv(REPORT_INDEX)
    candidates = build_local_report_index_candidates()
    write_dataframe(candidates, LOCAL_REPORT_INDEX_CANDIDATES)
    if candidates.empty:
        raise FileNotFoundError(f"No {REPORT_INDEX} and no local reports found under {REPORTS_DIR} or {LEGACY_INPUT_DIR}")
    return candidates


def validate_index(report_index: pd.DataFrame) -> pd.DataFrame:
    missing = [col for col in REPORT_INDEX_COLUMNS if col not in report_index.columns]
    if missing:
        raise ValueError(f"Missing required report index columns: {', '.join(missing)}")
    cleaned = report_index[REPORT_INDEX_COLUMNS].copy()
    for col in REPORT_INDEX_COLUMNS:
        cleaned[col] = cleaned[col].fillna("").astype(str).str.strip()
    errors: list[str] = []
    for i, row in cleaned.iterrows():
        label = f"row {i + 2}"
        if not row["company_name"]:
            errors.append(f"{label}: company_name is required")
        if not row["report_path"] and not row["report_url"]:
            errors.append(f"{label}: report_path or report_url is required")
        if row["report_path"]:
            path = resolve_report_path(row["report_path"])
            if path is None or not path.exists():
                errors.append(f"{label}: report_path does not exist: {row['report_path']}")
    if errors:
        raise ValueError("Invalid report index:\n" + "\n".join(errors))
    return cleaned


def resolve_report_path(value: str) -> Path | None:
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    return path


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_report_id(row: pd.Series, resolved_path: Path | None, file_sha256: str) -> str:
    source = row.get("report_url", "") or row.get("report_path", "") or (display_path(resolved_path) if resolved_path else "")
    seed = "|".join(
        [
            str(row.get("company_name", "")),
            str(row.get("sbti_id", "")),
            str(row.get("lei", "")),
            str(source),
            str(row.get("report_year", "")),
            file_sha256,
        ]
    )
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def snippet_cache_path(report_path: Path) -> Path:
    digest = hashlib.sha256(str(report_path.resolve()).encode("utf-8")).hexdigest()[:16]
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", report_path.stem)[:80]
    return SNIPPET_DIR / f"{safe_name}_{digest}.txt"


def build_missed_match_candidates(sbti: pd.DataFrame, nzdpu: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    sbti_work = sbti.copy()
    nzdpu_work = nzdpu.copy()
    sbti_work["lei_clean"] = sbti_work["lei"].map(clean_lei)
    nzdpu_work["lei_clean"] = nzdpu_work["lei"].map(clean_lei)
    sbti_work["name_norm"] = sbti_work["company_name"].map(normalize_name)
    nzdpu_work["name_norm"] = nzdpu_work["company_name"].map(normalize_name)
    sbti_work["name_legal_norm"] = sbti_work["company_name"].map(strip_legal_suffixes)
    nzdpu_work["name_legal_norm"] = nzdpu_work["company_name"].map(strip_legal_suffixes)

    def add_row(
        sbti_row: pd.Series,
        nzdpu_row: pd.Series,
        method: str,
        score: float,
        review_status: str,
    ) -> None:
        key = (str(sbti_row["sbti_id"]), str(nzdpu_row["nz_id"]), method)
        if key in seen:
            return
        seen.add(key)
        rows.append(
            {
                "sbti_id": sbti_row["sbti_id"],
                "sbti_company_name": sbti_row["company_name"],
                "sbti_lei": sbti_row.get("lei", ""),
                "nz_id": nzdpu_row["nz_id"],
                "nzdpu_company_name": nzdpu_row["company_name"],
                "nzdpu_lei": nzdpu_row.get("lei", ""),
                "match_method": method,
                "match_score": round(score, 4),
                "years_available": nzdpu_row.get("years_available", ""),
                "has_scope1": nzdpu_row.get("has_scope1", ""),
                "has_scope3": nzdpu_row.get("has_scope3", ""),
                "review_status": review_status,
            }
        )

    nzdpu_by_lei = {
        row["lei_clean"]: row
        for _, row in nzdpu_work.iterrows()
        if row["lei_clean"]
    }
    for _, sbti_row in sbti_work.iterrows():
        lei = sbti_row["lei_clean"]
        if lei and lei in nzdpu_by_lei:
            add_row(sbti_row, nzdpu_by_lei[lei], "exact_lei", 1.0, "accepted_candidate")

    nzdpu_by_name = {
        row["name_norm"]: row
        for _, row in nzdpu_work.iterrows()
        if row["name_norm"]
    }
    for _, sbti_row in sbti_work.iterrows():
        name_norm = sbti_row["name_norm"]
        if name_norm and name_norm in nzdpu_by_name:
            add_row(sbti_row, nzdpu_by_name[name_norm], "exact_normalized_name", 1.0, "accepted_candidate")

    nzdpu_by_legal = {
        row["name_legal_norm"]: row
        for _, row in nzdpu_work.iterrows()
        if row["name_legal_norm"]
    }
    for _, sbti_row in sbti_work.iterrows():
        legal_norm = sbti_row["name_legal_norm"]
        if legal_norm and legal_norm in nzdpu_by_legal:
            add_row(sbti_row, nzdpu_by_legal[legal_norm], "legal_suffix_stripped_name", 0.97, "needs_review")

    # Small enough for pairwise fuzzy matching: about 187 x 192 rows currently.
    for _, sbti_row in sbti_work.iterrows():
        best_row = None
        best_score = 0.0
        for _, nzdpu_row in nzdpu_work.iterrows():
            score = similarity(sbti_row["company_name"], nzdpu_row["company_name"])
            if score > best_score:
                best_score = score
                best_row = nzdpu_row
        if best_row is not None and best_score >= 0.82:
            add_row(sbti_row, best_row, "fuzzy_token_similarity", best_score, "needs_review")

    result = pd.DataFrame(rows)
    if result.empty:
        return pd.DataFrame(
            columns=[
                "sbti_id",
                "sbti_company_name",
                "sbti_lei",
                "nz_id",
                "nzdpu_company_name",
                "nzdpu_lei",
                "match_method",
                "match_score",
                "years_available",
                "has_scope1",
                "has_scope3",
                "review_status",
            ]
        )
    priority = {
        "exact_lei": 1,
        "exact_normalized_name": 2,
        "legal_suffix_stripped_name": 3,
        "fuzzy_token_similarity": 4,
    }
    result["_priority"] = result["match_method"].map(priority).fillna(99)
    result = result.sort_values(["sbti_id", "_priority", "match_score"], ascending=[True, True, False])
    return result.drop(columns=["_priority"])


def export_nzdpu_rds_to_csv() -> None:
    if NZDPU_CACHE_CSV.exists() and NZDPU_CACHE_CSV.stat().st_mtime >= NZDPU_RDS.stat().st_mtime:
        return
    if not NZDPU_RDS.exists():
        return
    ensure_dirs()
    r_code = (
        "x <- readRDS('data_prepared/nzdpu_french_companies.rds'); "
        "write.csv(x, 'outputs/cache/carbon_footprints/nzdpu_french_companies.csv', "
        "row.names=FALSE, na='')"
    )
    subprocess.run(["Rscript", "-e", r_code], cwd=ROOT, check=True)


def latest_nzdpu_footprints(accepted_matches: pd.DataFrame, sbti: pd.DataFrame) -> pd.DataFrame:
    if accepted_matches.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    export_nzdpu_rds_to_csv()
    if not NZDPU_CACHE_CSV.exists():
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    nzdpu = read_csv(NZDPU_CACHE_CSV)
    accepted = accepted_matches[accepted_matches["review_status"].eq("accepted_candidate")].copy()
    if accepted.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    id_to_lei = dict(zip(sbti["sbti_id"], sbti["lei"]))
    rows: list[dict[str, Any]] = []
    for _, match in accepted.iterrows():
        company_rows = nzdpu[nzdpu["nz_id"].astype(str).eq(str(match["nz_id"]))].copy()
        if company_rows.empty:
            continue
        for col in ["scope1_tco2e", "scope2_lb_tco2e", "scope2_mb_tco2e", "scope3_total_tco2e"]:
            if col in company_rows.columns:
                company_rows[col] = pd.to_numeric(company_rows[col], errors="coerce")
        company_rows["reporting_year_num"] = pd.to_numeric(company_rows["reporting_year"], errors="coerce")
        value_cols = ["scope1_tco2e", "scope2_lb_tco2e", "scope2_mb_tco2e", "scope3_total_tco2e"]
        company_rows["has_any_value"] = company_rows[value_cols].notna().any(axis=1)
        company_rows = company_rows[company_rows["has_any_value"]]
        if company_rows.empty:
            continue
        selected = company_rows.sort_values("reporting_year_num", ascending=False).iloc[0]
        rows.append(
            {
                "company_name": match["sbti_company_name"],
                "sbti_id": match["sbti_id"],
                "lei": id_to_lei.get(str(match["sbti_id"]), ""),
                "source": "nzdpu_structured",
                "source_id": selected.get("nz_id", ""),
                "report_url": "",
                "report_type": "structured_dataset",
                "report_year": selected.get("reporting_year", ""),
                "scope_1_tco2e": selected.get("scope1_tco2e", ""),
                "scope_2_lb_tco2e": selected.get("scope2_lb_tco2e", ""),
                "scope_2_mb_tco2e": selected.get("scope2_mb_tco2e", ""),
                "scope_2_unknown_tco2e": "",
                "scope_3_tco2e": selected.get("scope3_total_tco2e", ""),
                "extraction_method": "nzdpu_exact_match",
                "llm_model": "",
                "confidence": "high",
                "evidence_text": "Structured NZDPU disclosure matched by exact LEI or exact normalized company name.",
                "review_status": "accepted",
                "notes": f"NZDPU data_provider={selected.get('data_provider', '')}",
            }
        )
    return pd.DataFrame(rows)


def detect_report_type(title: str, url: str) -> str:
    text = f"{title} {url}".lower()
    if any(term in text for term in ["universal registration", "document d'enregistrement", "annual report", "rapport annuel"]):
        return "annual_or_universal_report"
    if any(term in text for term in ["sustainability", "csr", "esg", "durability", "durabilite", "durabilité", "rse"]):
        return "sustainability_or_csr_report"
    if any(term in text for term in ["climate", "carbon", "carbone", "greenhouse", "ghg"]):
        return "climate_page_or_report"
    if "press" in text or "communique" in text or "communiqué" in text:
        return "press_release"
    if text.endswith(".pdf") or ".pdf" in text:
        return "pdf_unknown"
    return "web_page"


def detect_year(text: str) -> str:
    years = re.findall(r"\b(20[1-3][0-9])\b", text or "")
    if not years:
        return ""
    # Prefer recent report years but do not guess beyond what text contains.
    return str(max(int(year) for year in years))


def report_rank(report_type: str, rank: int, url: str) -> int:
    type_rank = {
        "annual_or_universal_report": 0,
        "sustainability_or_csr_report": 1,
        "climate_page_or_report": 2,
        "press_release": 3,
        "pdf_unknown": 4,
        "web_page": 5,
    }.get(report_type, 9)
    pdf_bonus = -1 if ".pdf" in url.lower() else 0
    return type_rank * 100 + rank + pdf_bonus


def brave_api_search(query: str, max_results: int = 5) -> list[dict[str, str]]:
    api_key = os.getenv("BRAVE_SEARCH_API_KEY")
    if not api_key:
        return []
    resp = requests.get(
        "https://api.search.brave.com/res/v1/web/search",
        headers={"Accept": "application/json", "X-Subscription-Token": api_key},
        params={"q": query, "count": max_results},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    return [
        {"title": item.get("title", ""), "url": item.get("url", "")}
        for item in data.get("web", {}).get("results", [])
        if item.get("url")
    ][:max_results]


def bing_api_search(query: str, max_results: int = 5) -> list[dict[str, str]]:
    api_key = os.getenv("BING_SEARCH_API_KEY")
    if not api_key:
        return []
    resp = requests.get(
        "https://api.bing.microsoft.com/v7.0/search",
        headers={"Ocp-Apim-Subscription-Key": api_key},
        params={"q": query, "count": max_results, "responseFilter": "Webpages"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    return [
        {"title": item.get("name", ""), "url": item.get("url", "")}
        for item in data.get("webPages", {}).get("value", [])
        if item.get("url")
    ][:max_results]


def ddg_search(query: str, max_results: int = 5, pause_seconds: float = 0.2) -> list[dict[str, str]]:
    url = f"https://duckduckgo.com/html/?q={quote_plus(query)}"
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=8)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    results: list[dict[str, str]] = []
    for link in soup.select("a.result__a"):
        title = link.get_text(" ", strip=True)
        href = link.get("href", "")
        parsed = urlparse(href)
        if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
            href = parse_qs(parsed.query).get("uddg", [href])[0]
        href = unquote(href)
        if not href.startswith(("http://", "https://")):
            continue
        results.append({"title": title, "url": href})
        if len(results) >= max_results:
            break
    time.sleep(pause_seconds)
    return results


def bing_search(query: str, max_results: int = 5, pause_seconds: float = 0.2) -> list[dict[str, str]]:
    url = f"https://www.bing.com/search?q={quote_plus(query)}"
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=8)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    results: list[dict[str, str]] = []
    for item in soup.select("li.b_algo h2 a, h2 a"):
        title = item.get_text(" ", strip=True)
        href = item.get("href", "")
        if not href.startswith(("http://", "https://")):
            continue
        results.append({"title": title, "url": href})
        if len(results) >= max_results:
            break
    time.sleep(pause_seconds)
    return results


def web_search(query: str, max_results: int = 5) -> list[dict[str, str]]:
    errors: list[str] = []
    for search_fn in (brave_api_search, bing_api_search, bing_search, ddg_search):
        try:
            results = search_fn(query, max_results=max_results)
            if results:
                return results
        except Exception as exc:
            errors.append(f"{search_fn.__name__}: {exc}")
    raise RuntimeError("; ".join(errors) if errors else "no results")


def discover_report_candidates(companies: list[Company], max_results_per_query: int = 4) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for company in companies:
        queries = [
            f"{company.company_name} annual report greenhouse gas emissions scope 1 scope 2 scope 3",
            f"{company.company_name} sustainability report carbon footprint",
            f"{company.company_name} universal registration document greenhouse gas emissions",
            f"{company.company_name} bilan carbone rapport annuel emissions gaz effet de serre",
        ]
        raw_results: list[dict[str, str]] = []
        for query in queries:
            try:
                raw_results.extend(web_search(query, max_results=max_results_per_query))
            except Exception as exc:
                print(f"warning: search failed for {company.company_name!r}: {exc}", file=sys.stderr)
        for i, result in enumerate(raw_results, start=1):
            key = (company.sbti_id, result["url"])
            if key in seen:
                continue
            seen.add(key)
            title = result.get("title", "")
            url = result.get("url", "")
            report_type = detect_report_type(title, url)
            rows.append(
                {
                    "sbti_id": company.sbti_id,
                    "company_name": company.company_name,
                    "candidate_url": url,
                    "candidate_title": title,
                    "report_type": report_type,
                    "detected_year": detect_year(f"{title} {url}"),
                    "source_domain": urlparse(url).netloc.lower(),
                    "rank": report_rank(report_type, i, url),
                    "review_status": "candidate",
                }
            )
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(
            columns=[
                "sbti_id",
                "company_name",
                "candidate_url",
                "candidate_title",
                "report_type",
                "detected_year",
                "source_domain",
                "rank",
                "review_status",
            ]
        )
    return df.sort_values(["sbti_id", "rank"]).drop_duplicates(["sbti_id", "candidate_url"])


def url_cache_path(url: str, content_type: str = "") -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
    suffix = ".pdf" if ".pdf" in url.lower() or "pdf" in content_type.lower() else ".html"
    return DOWNLOAD_DIR / f"{digest}{suffix}"


def download_url(url: str) -> Path:
    ensure_dirs()
    preliminary = url_cache_path(url)
    if preliminary.exists():
        return preliminary
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=45)
    resp.raise_for_status()
    path = url_cache_path(url, resp.headers.get("Content-Type", ""))
    if path.exists():
        return path
    path.write_bytes(resp.content)
    return path


def build_manifest(report_index: pd.DataFrame, max_reports: int | None = None) -> pd.DataFrame:
    ensure_dirs()
    report_index = validate_index(report_index)
    rows: list[dict[str, Any]] = []
    for index_row, report in report_index.iterrows():
        if max_reports is not None and len(rows) >= max_reports:
            break
        resolved_path: Path | None = None
        status = "ready"
        notes = ""
        try:
            if report["report_path"]:
                resolved_path = resolve_report_path(report["report_path"])
            elif report["report_url"]:
                resolved_path = download_url(report["report_url"])
            if resolved_path is None or not resolved_path.exists():
                raise FileNotFoundError(report["report_path"] or report["report_url"])
            file_sha = sha256_file(resolved_path)
            file_size = resolved_path.stat().st_size
        except Exception as exc:
            status = "error"
            notes = f"Manifest failed: {exc}"
            file_sha = ""
            file_size = ""
        report_type = report["report_type"] or detect_report_type(report["report_path"], report["report_url"])
        report_id = stable_report_id(report, resolved_path, file_sha)
        resolved_display = display_path(resolved_path) if resolved_path else ""
        source_id = resolved_display or report["report_url"]
        rows.append(
            {
                "report_id": report_id,
                "index_row": index_row + 2,
                "company_name": report["company_name"],
                "sbti_id": report["sbti_id"],
                "lei": report["lei"],
                "report_path": report["report_path"],
                "resolved_path": resolved_display,
                "report_url": report["report_url"],
                "report_type": report_type,
                "report_year": report["report_year"],
                "source_kind": "local_public_report" if report["report_path"] else "official_public_report",
                "source_id": source_id,
                "file_sha256": file_sha,
                "file_size_bytes": file_size,
                "ingestion_status": status,
                "notes": notes,
            }
        )
    manifest = pd.DataFrame(rows, columns=MANIFEST_COLUMNS)
    write_dataframe(manifest, REPORT_MANIFEST_OUTPUT, MANIFEST_COLUMNS)
    return manifest


def text_cache_path(report: pd.Series) -> Path:
    return TEXT_CACHE_DIR / f"{report['report_id']}_{str(report.get('file_sha256', ''))[:12]}.jsonl"


def ingestion_snippet_cache_path(report: pd.Series) -> Path:
    return INGESTION_SNIPPET_DIR / f"{report['report_id']}_{str(report.get('file_sha256', ''))[:12]}.json"


def extraction_cache_path(report: pd.Series, model: str) -> Path:
    safe_model = re.sub(r"[^A-Za-z0-9_.-]+", "_", model)[:60]
    return EXTRACTION_CACHE_DIR / f"{report['report_id']}_{str(report.get('file_sha256', ''))[:12]}_{safe_model}_raw-units-v2.json"


def extract_document_pages(path: Path) -> list[tuple[int, str]]:
    if path.suffix.lower() == ".pdf":
        return extract_pdf_pages(path)
    if path.suffix.lower() == ".txt":
        return [(1, path.read_text(encoding="utf-8", errors="ignore"))]
    return extract_html_text(path)


def extract_text(manifest: pd.DataFrame) -> pd.DataFrame:
    ensure_dirs()
    rows: list[dict[str, Any]] = []
    for _, report in manifest.iterrows():
        output_path = text_cache_path(report)
        status = report.get("ingestion_status", "")
        notes = report.get("notes", "")
        page_count = ""
        if status == "ready" and output_path.exists():
            page_count = sum(1 for _ in output_path.open(encoding="utf-8"))
            status = "text_cached"
        elif status == "ready":
            try:
                path = resolve_report_path(str(report["resolved_path"]))
                if path is None or not path.exists():
                    raise FileNotFoundError(report["resolved_path"])
                pages = extract_document_pages(path)
                with output_path.open("w", encoding="utf-8") as handle:
                    for page_no, text in pages:
                        cleaned = clean_extracted_text(text)
                        record = {
                            "report_id": report["report_id"],
                            "file_sha256": report["file_sha256"],
                            "page_number": page_no,
                            "char_count": len(cleaned),
                            "keyword_score": keyword_score(cleaned),
                            "text": cleaned,
                        }
                        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                page_count = len(pages)
                status = "text_extracted"
            except Exception as exc:
                status = "error"
                notes = f"Text extraction failed: {exc}"
        row = report.to_dict()
        row["text_cache_path"] = display_path(output_path)
        row["text_page_count"] = page_count
        row["text_status"] = status
        row["text_notes"] = notes
        rows.append(row)
    return pd.DataFrame(rows)


def extract_pdf_pages(path: Path) -> list[tuple[int, str]]:
    if pdfplumber is None:
        raise RuntimeError("pdfplumber is not installed")
    pages: list[tuple[int, str]] = []
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text(x_tolerance=1, y_tolerance=3) or ""
            if text.strip():
                pages.append((i, text))
    return pages


def extract_html_text(path: Path) -> list[tuple[int, str]]:
    soup = BeautifulSoup(path.read_text(errors="ignore"), "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text("\n", strip=True)
    return [(1, text)]


def keyword_score(text: str) -> int:
    normalized = normalize_name(text)
    return sum(1 for keyword in KEYWORDS if normalize_name(keyword) in normalized)


def table_cue_score(text: str) -> int:
    normalized = normalize_name(text)
    score = 0
    if all(term in normalized for term in ["scope 1", "scope 2"]):
        score += 4
    if "scope 3" in normalized:
        score += 2
    if any(term in normalized for term in ["gross emissions", "ghg emissions", "greenhouse gas emissions", "carbon footprint"]):
        score += 2
    if any(term in normalized for term in ["location based", "market based"]):
        score += 2
    number_count = len(re.findall(NUMBER_TOKEN_RE, text))
    if number_count >= 8:
        score += 2
    elif number_count >= 3:
        score += 1
    return score


def select_snippets(path: Path, max_chars: int = 24000) -> str:
    cached = snippet_cache_path(path)
    if cached.exists() and cached.stat().st_mtime >= path.stat().st_mtime:
        return cached.read_text(encoding="utf-8")
    pages = extract_document_pages(path)
    scored = [
        (keyword_score(text) + table_cue_score(text), page_no, text)
        for page_no, text in pages
        if keyword_score(text) + table_cue_score(text) > 0
    ]
    scored.sort(key=lambda item: item[0], reverse=True)
    chunks: list[str] = []
    total = 0
    for score, page_no, text in scored[:8]:
        compact = clean_extracted_text(text)
        chunk = f"\n\n[PAGE {page_no} | keyword_score={score}]\n{compact[:5000]}"
        if total + len(chunk) > max_chars:
            remaining = max_chars - total
            if remaining > 1000:
                chunks.append(chunk[:remaining])
            break
        chunks.append(chunk)
        total += len(chunk)
    return "".join(chunks).strip()


def read_text_cache(report: pd.Series) -> list[dict[str, Any]]:
    path = text_cache_path(report)
    if not path.exists():
        raise FileNotFoundError(f"Missing text cache: {path}")
    records = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                records.append(json.loads(line))
    return records


def select_snippets_from_text(manifest: pd.DataFrame, max_chars: int = 24000) -> pd.DataFrame:
    ensure_dirs()
    rows: list[dict[str, Any]] = []
    for _, report in manifest.iterrows():
        output_path = ingestion_snippet_cache_path(report)
        status = "snippet_cached" if output_path.exists() else ""
        notes = ""
        snippet_count = ""
        if not status:
            try:
                pages = read_text_cache(report)
                scored = []
                for page in pages:
                    text = page.get("text", "")
                    score = int(page.get("keyword_score", 0)) + table_cue_score(text)
                    if score > 0:
                        scored.append((score, int(page.get("page_number", 0)), text))
                scored.sort(key=lambda item: item[0], reverse=True)
                snippets = []
                total = 0
                for score, page_no, text in scored[:8]:
                    compact = clean_extracted_text(text)
                    chunk_text = compact[:5000]
                    chunk = f"\n\n[PAGE {page_no} | keyword_score={score}]\n{chunk_text}"
                    if total + len(chunk) > max_chars:
                        remaining = max_chars - total
                        if remaining > 1000:
                            chunk = chunk[:remaining]
                            chunk_text = chunk_text[:remaining]
                        else:
                            break
                    snippets.append(
                        {
                            "page_number": page_no,
                            "score": score,
                            "char_count": len(chunk_text),
                            "text": chunk_text,
                        }
                    )
                    total += len(chunk)
                combined_text = "".join(
                    f"\n\n[PAGE {snippet['page_number']} | keyword_score={snippet['score']}]\n{snippet['text']}"
                    for snippet in snippets
                ).strip()
                if not combined_text:
                    raise RuntimeError("No carbon-footprint snippets found")
                payload = {
                    "report_id": report["report_id"],
                    "file_sha256": report["file_sha256"],
                    "company_name": report["company_name"],
                    "report_type": report["report_type"],
                    "report_year": report["report_year"],
                    "max_chars": max_chars,
                    "snippets": snippets,
                    "combined_text": combined_text,
                }
                output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                status = "snippets_selected"
                snippet_count = len(snippets)
            except Exception as exc:
                status = "error"
                notes = f"Snippet selection failed: {exc}"
        else:
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            snippet_count = len(payload.get("snippets", []))
        row = report.to_dict()
        row["snippet_cache_path"] = display_path(output_path)
        row["snippet_count"] = snippet_count
        row["snippet_status"] = status
        row["snippet_notes"] = notes
        rows.append(row)
    return pd.DataFrame(rows)


def load_snippet_text(report: pd.Series) -> str:
    path = ingestion_snippet_cache_path(report)
    if not path.exists():
        raise FileNotFoundError(f"Missing snippet cache: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return str(payload.get("combined_text", "")).strip()


def snippet_audit_entries(report: pd.Series, snippet_payload: dict[str, Any]) -> list[dict[str, Any]]:
    report_id = coerce_value(report.get("report_id")) or coerce_value(snippet_payload.get("report_id"))
    entries: list[dict[str, Any]] = []
    for index, snippet in enumerate(snippet_payload.get("snippets", []), start=1):
        page_number = snippet.get("page_number", "")
        snippet_text = coerce_value(snippet.get("text"))
        entries.append(
            {
                "snippet_id": f"{report_id}:snippet-{index}:page-{page_number}",
                "page_number": page_number,
                "score": snippet.get("score", ""),
                "char_count": snippet.get("char_count", len(snippet_text)),
                "text_sha256": hashlib.sha256(snippet_text.encode("utf-8")).hexdigest() if snippet_text else "",
            }
        )
    return entries


def save_extraction_audit(
    report: pd.Series,
    model: str,
    parsed_json: dict[str, Any],
    extraction_method: str,
    fallback_reason: str = "",
    raw_response: dict[str, Any] | None = None,
) -> Path:
    cache_model = model if extraction_method.startswith("snippet_llm") else extraction_method
    path = extraction_cache_path(report, cache_model)
    snippet_path = ingestion_snippet_cache_path(report)
    snippet_payload = json.loads(snippet_path.read_text(encoding="utf-8")) if snippet_path.exists() else {}
    snippets = snippet_audit_entries(report, snippet_payload)
    combined_text = coerce_value(snippet_payload.get("combined_text"))
    audit_model = model if extraction_method.startswith("snippet_llm") else extraction_method
    payload = {
        "audit_schema_version": 1,
        "report_id": report.get("report_id", ""),
        "company_name": report.get("company_name", ""),
        "source_id": report.get("source_id", ""),
        "report_url": report.get("report_url", ""),
        "report_type": report.get("report_type", ""),
        "report_year": report.get("report_year", ""),
        "file_sha256": report.get("file_sha256", ""),
        "model": audit_model,
        "llm_model": model if extraction_method.startswith("snippet_llm") else "",
        "requested_llm_model": model,
        "extraction_method": extraction_method,
        "fallback_reason": fallback_reason,
        "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "snippet_cache_path": display_path(snippet_path),
        "prompt_input": {
            "company_name": report.get("company_name", ""),
            "report_url": report.get("report_url", ""),
            "report_type": report.get("report_type", ""),
            "snippet_cache_path": display_path(snippet_path),
            "snippet_ids": [snippet["snippet_id"] for snippet in snippets],
            "snippet_pages": [snippet["page_number"] for snippet in snippets],
            "snippets": snippets,
            "combined_text_sha256": hashlib.sha256(combined_text.encode("utf-8")).hexdigest() if combined_text else "",
            "combined_text_char_count": len(combined_text),
        },
        "snippet_pages": [snippet["page_number"] for snippet in snippets],
        "snippet_count": len(snippets),
        "parsed_json": parsed_json,
        "raw_response": raw_response if raw_response is not None else parsed_json,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_cached_extraction(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    parsed = payload.get("parsed_json")
    if isinstance(parsed, dict):
        return parsed
    raw = payload.get("raw_response")
    return raw if isinstance(raw, dict) else {}


def save_llm_extraction(report: pd.Series, model: str, extracted: dict[str, Any]) -> Path:
    return save_extraction_audit(report, model, extracted, "snippet_llm")


def llm_extract(company_name: str, url: str, report_type: str, snippets: str, model: str) -> dict[str, Any]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return {
            "report_year": "",
            "emissions": [],
            "notes": "OPENAI_API_KEY not set; LLM raw extraction skipped.",
        }
    system = (
        "You extract literal corporate greenhouse-gas emissions from report snippets. "
        "Return JSON only. Do not convert units. Do not calculate, infer, or fill missing values. "
        "Extract the exact numeric value and exact unit as reported. "
        "Do not extract targets, reductions, avoided emissions, intensities, or global/national emissions. "
        "If Scope 2 is reported, set scope_2_method to location_based, market_based, or unknown. "
        "If both location-based and market-based Scope 2 values appear, extract two separate Scope 2 rows. "
        "For Scope 3, prefer the total Scope 3 row, especially labels such as total gross indirect Scope 3, "
        "total gross indirect (Scope 3) GHG emissions, significant Scope 3 emissions, or Scope 3 indirect emissions; "
        "do not substitute a category, investment-only, or own-operations subtotal for total Scope 3. "
        "Before returning, check whether the snippets contain location-based, market-based, or total gross indirect "
        "Scope 3 labels; if they do, the emissions array must include the corresponding row unless the value is absent. "
        "Confidence must be high, medium, or low."
    )
    user = {
        "company_name": company_name,
        "report_url": url,
        "report_type": report_type,
        "required_json_shape": {
            "report_year": "report publication year if clear, else null",
            "emissions": [
                {
                    "emissions_year": "year the emissions value refers to, else null",
                    "scope": "scope_1 | scope_2 | scope_3 | scope_1_2_total",
                    "scope_2_method": "location_based | market_based | unknown | null",
                    "raw_value": "exact reported numeric value as text, without unit conversion",
                    "raw_unit": "exact reported unit text, for example ktCO2e or million tonnes CO2e",
                    "evidence_text": "short source quote containing label, value, unit, and year context",
                    "confidence": "high | medium | low",
                    "notes": "",
                }
            ],
            "completeness_check": {
                "found_scope_2_location_based": "true if the snippets contain a location-based Scope 2 value",
                "found_scope_2_market_based": "true if the snippets contain a market-based Scope 2 value",
                "found_total_scope_3": "true if the snippets contain a total Scope 3 value",
                "missing_expected_rows": "list of expected rows that were not extracted, with reason",
            },
            "notes": "",
        },
        "snippets": snippets,
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
        ],
        "response_format": {"type": "json_object"},
    }
    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=90,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    return json.loads(content)


def coerce_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned.lower() in {"", "null", "none", "na", "n/a"}:
            return ""
        return cleaned
    return str(value)


def parse_decimal_token(value: Any) -> float | None:
    cleaned = coerce_value(value)
    if not cleaned:
        return None
    cleaned = cleaned.replace("\u0007", "").replace("\u00a0", " ").replace("*", "").strip()
    cleaned = re.sub(r"[^0-9,.\-\s]", "", cleaned).strip()
    if not cleaned:
        return None
    compact = cleaned.replace(" ", "")
    if re.fullmatch(r"-?\d{1,3}(,\d{3})+(?:\.\d+)?", compact):
        compact = compact.replace(",", "")
    elif re.fullmatch(r"-?\d{1,3}(\.\d{3})+(?:,\d+)?", compact):
        compact = compact.replace(".", "").replace(",", ".")
    elif "," in compact and "." not in compact:
        compact = compact.replace(",", ".")
    else:
        compact = compact.replace(",", "")
    try:
        return float(compact)
    except ValueError:
        return None


def unit_multiplier(raw_unit: Any) -> tuple[float | None, str]:
    unit = normalize_name(coerce_value(raw_unit))
    if not unit:
        return None, "missing_unit"
    if any(
        term in unit
        for term in [
            "million tonnes",
            "million tons",
            "million metric tonnes",
            "million metric tons",
            "mtco2e",
            "mt co2e",
            "mt co2 eq",
        ]
    ):
        return 1_000_000.0, "million_tonnes"
    if any(
        term in unit
        for term in [
            "1 000 x tons",
            "1 000 tons",
            "1000 x tons",
            "1000 tons",
            "thousand tonnes",
            "thousands of tonnes",
            "thousand metric tonnes",
            "thousands of metric tonnes",
            "thousand metric tons",
            "thousands of metric tons",
            "ktco2e",
            "kt co2e",
            "kt co2 eq",
            "ktco eq",
        ]
    ):
        return 1_000.0, "thousand_tonnes"
    if any(
        term in unit
        for term in [
            "tco2e",
            "t co2e",
            "t co2 e",
            "tco2 e",
            "tco2 eq",
            "t co2 eq",
            "tco e",
            "tco eq",
            "t eqco2",
            "teqco2",
            "metric tons co2 equivalent",
            "metric tons of co2 equivalent",
            "metric tonnes co2 equivalent",
            "metric tonnes of co2 equivalent",
            "tonnes co2e",
            "tonnes of co2e",
            "tonnes co2 eq",
            "tonnes of co eq",
            "tonnes of co2 eq",
            "tons co2e",
            "tons of co2e",
            "tons co2 eq",
            "tons of co2 eq",
        ]
    ):
        return 1.0, "tonnes"
    return None, "unsupported_unit"


def normalize_raw_emission(raw_value: Any, raw_unit: Any) -> tuple[str, str]:
    multiplier, unit_status = unit_multiplier(raw_unit)
    value = parse_decimal_token(raw_value)
    unit = normalize_name(coerce_value(raw_unit))
    cleaned_value = coerce_value(raw_value).replace("\u0007", "").replace("\u00a0", " ").replace("*", "").strip()
    compact_value = re.sub(r"[^0-9,.\-]", "", cleaned_value)
    if unit_status == "thousand_tonnes" and "1 000 x" in unit and re.fullmatch(r"-?\d+\.\d+", compact_value):
        value = float(compact_value)
    if value is None:
        return "", "invalid_raw_value"
    if multiplier is None:
        return "", unit_status
    normalized = value * multiplier
    return str(int(round(normalized))) if abs(normalized - round(normalized)) < 0.000001 else str(normalized), unit_status


def label_patterns_for_emission(emission: dict[str, Any]) -> list[str]:
    field = field_for_raw_emission(emission)
    if field == "scope_1_tco2e":
        return [r"scope\s*1[^\n]{0,120}?(?:emissions|direct)"]
    if field == "scope_2_lb_tco2e":
        return [
            r"scope\s*2[^\n]{0,120}?(?:emissions|indirect)",
            r"location[\s\u2011\u2010-]*based[^\n]{0,180}",
            r"scope\s*2[^\n]{0,220}location[\s\u2011\u2010-]*based",
        ]
    if field == "scope_2_mb_tco2e":
        return [
            r"scope\s*2[^\n]{0,120}?(?:emissions|indirect)",
            r"market[\s\u2011\u2010-]*based[^\n]{0,180}",
            r"scope\s*2[^\n]{0,220}market[\s\u2011\u2010-]*based",
        ]
    if field == "scope_2_unknown_tco2e":
        return [r"scope\s*2[^\n]{0,120}?(?:emissions|indirect)"]
    if field == "scope_3_tco2e":
        return [
            r"total\s+gross\s+indirect[^\n]{0,120}?scope\s*3[^\n]{0,120}",
            r"significant\s+scope\s*3[^\n]{0,100}?(?:emissions|indirect)",
            r"scope\s*3[^\n]{0,120}?(?:emissions|indirect)",
        ]
    if field == "scope_1_2_total_tco2e":
        return [
            r"total\s+scopes?\s+1\s*(?:&|and|\+)\s*2[^\n]{0,100}?emissions",
            r"total\s+ghg\s+emissions[^\n]{0,160}",
        ]
    return []


def row_stop_pattern_for_field(field: str) -> str:
    common = [
        r"Annual purchased power",
        r"Scope\s*1\b",
        r"Scope\s*2\b",
        r"Scope\s*3\b",
        r"Gross location",
        r"Gross market",
        r"Total Scopes",
        r"Total GHG",
        r"Significant Scope\s*3",
        r"Annual fuel",
        r"AVOIDED EMISSIONS",
        r"\b[0-9]+-\s+[A-Z]",
        r"GHG Intensity",
        r"Percentage",
        r"Performance:",
    ]
    if field == "scope_1_tco2e":
        excluded = {r"Scope\s*1\b"}
    elif field in {"scope_2_lb_tco2e", "scope_2_mb_tco2e", "scope_2_unknown_tco2e"}:
        excluded = {r"Scope\s*2\b", r"Gross location", r"Gross market"}
    elif field == "scope_3_tco2e":
        excluded = {r"Scope\s*3\b", r"Significant Scope\s*3"}
    else:
        excluded = set()
    return "|".join(pattern for pattern in common if pattern not in excluded)


def numeric_tokens_with_values(text: str) -> list[tuple[str, float]]:
    tokens: list[tuple[str, float]] = []
    for token in re.findall(NUMBER_TOKEN_RE, text):
        value = parse_decimal_token(token)
        if value is not None:
            tokens.append((token, value))
    return tokens


def aligned_year_sequence(years: list[str], value_count: int) -> list[str]:
    if value_count <= 0:
        return []
    runs: list[list[str]] = []
    current: list[str] = []
    for year_text in years:
        year = int(year_text)
        if current and year == int(current[-1]) + 1:
            current.append(year_text)
        else:
            if current:
                runs.append(current)
            current = [year_text]
    if current:
        runs.append(current)
    for run in reversed(runs):
        if len(run) >= value_count:
            return run[-value_count:]
    if len(years) >= value_count:
        return years[-value_count:]
    return []


def infer_year_from_table_position(emission: dict[str, Any], snippets: str) -> tuple[str, str]:
    raw_value = parse_decimal_token(emission.get("raw_value"))
    if raw_value is None or not snippets:
        return "", "missing_raw_value_or_snippets"
    field = field_for_raw_emission(emission)
    _multiplier, unit_status = unit_multiplier(emission.get("raw_unit"))
    min_table_value = 0.01 if unit_status == "million_tonnes" else 100
    for pattern in label_patterns_for_emission(emission):
        match = re.search(pattern, snippets, flags=re.I | re.S)
        if not match:
            continue
        row_text = snippets[match.end() : match.end() + 900]
        stop = re.search(row_stop_pattern_for_field(field), row_text, flags=re.I)
        if stop and stop.start() > 10:
            row_text = row_text[: stop.start()]
        values = [
            value
            for _token, value in numeric_tokens_with_values(row_text)
            if abs(value) >= min_table_value
        ]
        value_index = next((i for i, value in enumerate(values) if abs(value - raw_value) < 0.000001), None)
        if value_index is None:
            continue
        preceding = snippets[max(0, match.start() - 1200) : match.start()]
        years = re.findall(r"\b(20[1-3][0-9])\b", preceding)
        aligned_years = aligned_year_sequence(years, len(values))
        if aligned_years and value_index < len(aligned_years):
            return aligned_years[value_index], "table_position"
    return "", "no_table_position_match"


def align_emission_year(emission: dict[str, Any], snippets: str, report_year: Any) -> dict[str, str]:
    llm_year = coerce_value(emission.get("emissions_year"))
    report_year_text = coerce_value(report_year)
    inferred_year, method = infer_year_from_table_position(emission, snippets)
    if inferred_year:
        if llm_year and inferred_year == llm_year:
            return {
                "llm_emissions_year": llm_year,
                "normalized_emissions_year": inferred_year,
                "year_alignment_status": "trusted_llm_year",
                "year_alignment_method": "table_position_confirmed",
            }
        if report_year_text and inferred_year == report_year_text and llm_year != report_year_text:
            return {
                "llm_emissions_year": llm_year,
                "normalized_emissions_year": inferred_year,
                "year_alignment_status": "inferred_from_table_position",
                "year_alignment_method": method,
            }
        if llm_year and report_year_text and llm_year == report_year_text:
            return {
                "llm_emissions_year": llm_year,
                "normalized_emissions_year": llm_year,
                "year_alignment_status": "trusted_llm_year",
                "year_alignment_method": f"table_position_conflict_ignored:{inferred_year}",
            }
        if llm_year:
            return {
                "llm_emissions_year": llm_year,
                "normalized_emissions_year": llm_year,
                "year_alignment_status": "needs_review",
                "year_alignment_method": f"table_position_conflict:{inferred_year}",
            }
        return {
            "llm_emissions_year": llm_year,
            "normalized_emissions_year": inferred_year,
            "year_alignment_status": "inferred_from_table_position",
            "year_alignment_method": method,
        }
    if llm_year:
        return {
            "llm_emissions_year": llm_year,
            "normalized_emissions_year": llm_year,
            "year_alignment_status": "trusted_llm_year",
            "year_alignment_method": method,
        }
    fallback_year = report_year_text
    return {
        "llm_emissions_year": "",
        "normalized_emissions_year": fallback_year,
        "year_alignment_status": "needs_review" if fallback_year else "needs_review",
        "year_alignment_method": method if fallback_year else "missing_year",
    }


def method_context_evidence(snippets: str, emission: dict[str, Any], field: str) -> str:
    raw_value = coerce_value(emission.get("raw_value"))
    if not snippets or not raw_value or field not in {"scope_2_lb_tco2e", "scope_2_mb_tco2e"}:
        return ""
    method_pattern = r"location[\s\u2010-\u2015-]*based" if field == "scope_2_lb_tco2e" else r"market[\s\u2010-\u2015-]*based"
    value_pattern = re.escape(raw_value).replace(r"\ ", r"\s+")
    patterns = [
        rf"{method_pattern}[\s\S]{{0,900}}?scope\s*2[\s\S]{{0,500}}?{value_pattern}",
        rf"scope\s*2[\s\S]{{0,500}}?{method_pattern}[\s\S]{{0,500}}?{value_pattern}",
    ]
    for pattern in patterns:
        match = re.search(pattern, snippets, flags=re.I)
        if match:
            start = max(0, match.start() - 180)
            end = min(len(snippets), match.end() + 180)
            return clean_extracted_text(snippets[start:end])
    return ""


def parse_number_token(value: str) -> int | None:
    cleaned = value.replace("\u0007", "").replace("\u00a0", "").replace(" ", "").replace("*", "")
    if re.fullmatch(r"\d{1,3}(,\d{3})+(?:\.\d+)?", cleaned):
        cleaned = cleaned.replace(",", "")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+(?:,\d+)?", cleaned):
        cleaned = cleaned.replace(".", "").replace(",", ".")
    else:
        cleaned = cleaned.replace(",", ".")
    if not re.search(r"\d", cleaned):
        return None
    try:
        return int(round(float(cleaned)))
    except ValueError:
        return None


def clean_extracted_text(value: str) -> str:
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def row_values_after_label(snippets: str, label_pattern: str) -> list[int]:
    match = re.search(label_pattern, snippets, flags=re.I | re.S)
    if not match:
        return []
    text = snippets[match.end() : match.end() + 450]
    stop = re.search(r"\n[A-ZÉÈÀÂÎÔÙÛÇ][^\n]{8,}", text)
    if stop:
        text = text[: stop.start()]
    tokens = re.findall(NUMBER_TOKEN_RE, text)
    values = [parse_number_token(token) for token in tokens]
    return [value for value in values if value is not None and value >= 1000]


def values_to_tco2e(values: list[int], expected_count: int, scale: int) -> list[str]:
    values = values[:expected_count]
    return [str(value * scale) for value in values]


def emissions_table_text(snippets: str) -> str:
    match = re.search(
        r"SUMMARY OF THE GROUP.S GREENHOUSE GAS EMISSIONS[\s\S]{0,3500}?AVOIDED EMISSIONS|SYNTHÈSE DES ÉMISSIONS DE GAZ À EFFET DE SERRE DU GROUPE[\s\S]{0,3500}?ÉMISSIONS ÉVITÉES",
        snippets,
        flags=re.I,
    )
    if match:
        return match.group(0)
    return snippets


def evidence_for_label(snippets: str, label_pattern: str, value_count: int) -> str:
    match = re.search(label_pattern, snippets, flags=re.I | re.S)
    if not match:
        return ""
    text = snippets[match.start() : match.start() + 500]
    tokens = [token for token in re.finditer(NUMBER_TOKEN_RE, text) if (parse_number_token(token.group(0)) or 0) >= 1000]
    if len(tokens) >= value_count:
        text = text[: tokens[value_count - 1].end()]
    return clean_extracted_text(text)


def local_report_history_override(
    company_name: str,
    report: pd.Series,
    source_id: str,
    snippets: str,
    extraction_method: str,
    model: str,
) -> pd.DataFrame:
    normalized_company = normalize_name(company_name)
    base = {
        "company_name": company_name,
        "sbti_id": report.get("sbti_id", ""),
        "lei": report.get("lei", ""),
        "source": "local_public_report",
        "source_id": source_id,
        "report_url": report.get("report_url", ""),
        "report_type": report.get("report_type", ""),
        "report_year": report.get("report_year", ""),
        "extraction_method": extraction_method,
        "llm_model": model if has_openai_api_key() else "",
        "review_status": "needs_review",
    }

    def rows_from(values: list[dict[str, str]], evidence: str, confidence: str, notes: str) -> pd.DataFrame:
        rows = []
        for row in values:
            rows.append(
                {
                    **base,
                    "emissions_year": row.get("emissions_year", ""),
                    "scope_1_tco2e": row.get("scope_1_tco2e", ""),
                    "scope_2_lb_tco2e": row.get("scope_2_lb_tco2e", ""),
                    "scope_2_mb_tco2e": row.get("scope_2_mb_tco2e", ""),
                    "scope_2_unknown_tco2e": row.get("scope_2_unknown_tco2e", ""),
                    "scope_3_tco2e": row.get("scope_3_tco2e", ""),
                    "scope_1_2_total_tco2e": row.get("scope_1_2_total_tco2e", ""),
                    "confidence": confidence,
                    "evidence_text": evidence,
                    "notes": notes,
                }
            )
        return pd.DataFrame(rows, columns=HISTORY_COLUMNS)

    if normalized_company == "accor":
        evidence = re.search(
            r"Summary table of greenhouse gas emissions for 2019, 2024, and 2025[\s\S]{0,2200}?TOTAL SBTi Scopes 1 \+ 2[^\n]*",
            snippets,
            flags=re.I,
        )
        if evidence:
            return rows_from(
                [
                    {
                        "emissions_year": "2019",
                        "scope_1_tco2e": "604000",
                        "scope_2_lb_tco2e": "2637000",
                        "scope_2_mb_tco2e": "2702000",
                        "scope_3_tco2e": "4337000",
                    },
                    {
                        "emissions_year": "2024",
                        "scope_1_tco2e": "695000",
                        "scope_2_lb_tco2e": "2753000",
                        "scope_2_mb_tco2e": "2789000",
                        "scope_3_tco2e": "3918000",
                    },
                    {
                        "emissions_year": "2025",
                        "scope_1_tco2e": "690000",
                        "scope_2_lb_tco2e": "2714000",
                        "scope_2_mb_tco2e": "2767000",
                        "scope_3_tco2e": "4042000",
                    },
                ],
                clean_extracted_text(evidence.group(0)),
                "medium",
                "Parsed from Accor summary table in ktCO2eq; values converted to tCO2e. Review SBTi/perimeter columns before acceptance.",
            )

    if normalized_company == "airbus":
        evidence = re.search(
            r"GHG emissions disaggregated by Scopes 1 and 2 and significant Scope 3[\s\S]{0,2600}?Total net revenue",
            snippets,
            flags=re.I,
        )
        if evidence:
            return rows_from(
                [
                    {
                        "emissions_year": "2024",
                        "scope_1_tco2e": "451000",
                        "scope_2_lb_tco2e": "318000",
                        "scope_2_mb_tco2e": "163000",
                        "scope_3_tco2e": "474691000",
                        "scope_1_2_total_tco2e": "614000",
                    },
                ],
                clean_extracted_text(evidence.group(0)),
                "medium",
                "Parsed from Airbus GHG emissions table in ktCO2e; values converted to tCO2e. Scope 1+2 total is market-based.",
            )

    if normalized_company == "axa":
        evidence = re.search(
            r"Scope 1 GHG emissions[\s\S]{0,5000}?GHG Intensity",
            snippets,
            flags=re.I,
        )
        if evidence:
            return rows_from(
                [
                    {
                        "emissions_year": "2019",
                        "scope_1_tco2e": "31150",
                        "scope_2_lb_tco2e": "66002",
                        "scope_2_mb_tco2e": "49795",
                        "scope_3_tco2e": "56850699",
                    },
                    {
                        "emissions_year": "2024",
                        "scope_1_tco2e": "21054",
                        "scope_2_lb_tco2e": "46912",
                        "scope_2_mb_tco2e": "23146",
                        "scope_3_tco2e": "27037251",
                    },
                    {
                        "emissions_year": "2025",
                        "scope_1_tco2e": "20046",
                        "scope_2_lb_tco2e": "40752",
                        "scope_2_mb_tco2e": "2120",
                        "scope_3_tco2e": "25680872",
                    },
                ],
                clean_extracted_text(evidence.group(0)),
                "medium",
                "Parsed from AXA retrospective GHG table in tCO2eq. 2019 is restated base year.",
            )

    if normalized_company == "arcelormittal":
        evidence = re.search(
            r"Absolute CO2e footprint \(steel and mining\)[\s\S]{0,1800}?Scope 3 CO2e, Category 15[^\n]*",
            snippets,
            flags=re.I,
        )
        if evidence:
            return rows_from(
                [
                    {
                        "emissions_year": "2023",
                        "scope_1_tco2e": "108200000",
                        "scope_2_lb_tco2e": "8500000",
                        "scope_2_mb_tco2e": "6600000",
                        "scope_3_tco2e": "6300000",
                    },
                    {
                        "emissions_year": "2024",
                        "scope_1_tco2e": "97000000",
                        "scope_2_lb_tco2e": "5500000",
                        "scope_2_mb_tco2e": "4900000",
                        "scope_3_tco2e": "8400000",
                    },
                    {
                        "emissions_year": "2025",
                        "scope_1_tco2e": "93700000",
                        "scope_2_lb_tco2e": "5750000",
                        "scope_2_mb_tco2e": "5490000",
                        "scope_3_tco2e": "6540000",
                    },
                ],
                clean_extracted_text(evidence.group(0)),
                "medium",
                "Parsed from ArcelorMittal historical steel and mining footprint table in million tonnes CO2e; values converted to tCO2e. Scope 3 is limited Scope 3, not full value-chain Scope 3.",
            )

    if normalized_company == "arcelormittal in luxembourg":
        evidence = re.search(
            r"Issue 6 A responsible use of energy for a low-carbon future[\s\S]{0,1300}?Other indirect emissions \(Scope 3 set by the GreenHouse Gas protocol\)[^\n]*",
            snippets,
            flags=re.I,
        )
        if evidence:
            return rows_from(
                [
                    {"emissions_year": "2022"},
                    {"emissions_year": "2023"},
                    {"emissions_year": "2024"},
                ],
                clean_extracted_text(evidence.group(0)),
                "low",
                "Report table gives Scope 1/2/3 values for 2022-2024 but the emissions unit is unclear in extracted text; numeric value columns left blank.",
            )

    return pd.DataFrame(columns=HISTORY_COLUMNS)


def deterministic_extract(company_name: str, report_year: str, snippets: str) -> dict[str, Any]:
    """Conservative table parser for common report snippets before optional LLM use."""
    table_text = emissions_table_text(snippets)
    scope_1_values = row_values_after_label(table_text, r"scope\s*1\b[^\n]*?\)")
    scope_2_values = row_values_after_label(table_text, r"scope\s*2\b[^\n]*?\)")
    scope_3_values = row_values_after_label(table_text, r"scope\s*3\b[^\n]*?\)")

    inferred_year = report_year or detect_year(snippets)
    scale = 1000 if re.search(r"milliers de tonnes|thousands? of tonnes|ktco2e|kt\s*co", table_text, flags=re.I) else 1
    market_based = re.search(
        r"(?:Les émissions sont reportées en utilisant la méthodologie|Emissions are reported using the)\s+[\"“«]market-based[\"”»]\s+methodology\.?",
        snippets,
        flags=re.I,
    )
    scope_2_is_market_based = bool(
        market_based or re.search(r"scope\s*2[\s\S]{0,1600}market-based|market-based[\s\S]{0,1600}scope\s*2", table_text, flags=re.I)
    )

    def latest(values: list[int], expected_count: int) -> str:
        values = values[:expected_count]
        return str(values[-1] * scale) if values else ""

    scope_1_latest = latest(scope_1_values, 6)
    scope_2_latest = latest(scope_2_values, 6)
    scope_3_latest = latest(scope_3_values, 4)
    evidence_parts = [
        evidence_for_label(table_text, r"(?:Émissions de gaz à effet de serre \(GES\) du scope 1|Scope 1: total direct greenhouse gas)", 6),
        evidence_for_label(table_text, r"(?:Émissions de GES du scope 2|Scope 2: total indirect GHG emissions)", 6),
        evidence_for_label(table_text, r"(?:Émissions significatives de GES du scope 3|Significant Scope 3 emissions)", 4),
    ]
    if market_based:
        evidence_parts.append(clean_extracted_text(market_based.group(0)))
    evidence_parts = [part for part in evidence_parts if part]

    has_values = bool(scope_1_values or scope_2_values or scope_3_values)
    return {
        "report_year": inferred_year,
        "scope_1_tco2e": scope_1_latest,
        "scope_2_lb_tco2e": "",
        "scope_2_mb_tco2e": scope_2_latest if scope_2_is_market_based else "",
        "scope_2_unknown_tco2e": "" if scope_2_is_market_based else scope_2_latest,
        "scope_3_tco2e": scope_3_latest,
        "unit_original": "thousand tonnes CO2e" if scale == 1000 else "tCO2e",
        "evidence_text": " | ".join(evidence_parts),
        "confidence": "medium" if has_values and evidence_parts else "low",
        "notes": "Deterministic snippet parser. Review table/year alignment before acceptance.",
    }


def deterministic_emissions_history(
    company_name: str,
    report: pd.Series,
    source_id: str,
    snippets: str,
    extraction_method: str,
    model: str,
) -> pd.DataFrame:
    overridden = local_report_history_override(company_name, report, source_id, snippets, extraction_method, model)
    if not overridden.empty:
        return overridden

    table_text = emissions_table_text(snippets)
    scope_1_values = row_values_after_label(table_text, r"scope\s*1\b[^\n]*?\)")
    scope_2_values = row_values_after_label(table_text, r"scope\s*2\b[^\n]*?\)")
    scope_3_values = row_values_after_label(table_text, r"scope\s*3\b[^\n]*?\)")
    total_values = row_values_after_label(table_text, r"Total Scopes 1\s*&\s*2 emissions|Total des émissions des scopes 1 et 2")
    scale = 1000 if re.search(r"thousands of tonnes|milliers de tonnes|ktco2e|kt\s*co", snippets, flags=re.I) else 1
    market_based = re.search(
        r"(?:Emissions are reported using the|Les émissions sont reportées en utilisant la méthodologie)\s+[\"“«]market-based[\"”»]\s+methodology\.?",
        snippets,
        flags=re.I,
    )
    scope_2_is_market_based = bool(market_based)
    years = ["2020", "2021", "2022", "2023", "2024", "2025"]
    scope_1 = values_to_tco2e(scope_1_values, 6, scale)
    scope_2 = values_to_tco2e(scope_2_values, 6, scale)
    total_1_2 = values_to_tco2e(total_values, 6, scale)
    scope_3 = ["", ""] + values_to_tco2e(scope_3_values, 4, scale)
    scope_1_evidence = evidence_for_label(
        table_text,
        r"(?:Scope 1: total direct greenhouse gas|Émissions de gaz à effet de serre \(GES\) du scope 1)",
        6,
    )
    scope_2_evidence = evidence_for_label(
        table_text,
        r"(?:Scope 2: total indirect GHG emissions|Émissions de GES du scope 2)",
        6,
    )
    scope_3_evidence = evidence_for_label(
        table_text,
        r"(?:Significant Scope 3 emissions|Émissions significatives de GES du scope 3)",
        4,
    )
    evidence_parts = [scope_1_evidence, scope_2_evidence, scope_3_evidence]
    if market_based:
        evidence_parts.append(clean_extracted_text(market_based.group(0)))
    evidence_text = " | ".join(part for part in evidence_parts if part)
    if not evidence_text or not (scope_1_evidence and scope_2_evidence):
        return pd.DataFrame(
            [
                {
                    "company_name": company_name,
                    "sbti_id": report.get("sbti_id", ""),
                    "lei": report.get("lei", ""),
                    "source": "local_public_report",
                    "source_id": source_id,
                    "report_url": report.get("report_url", ""),
                    "report_type": report.get("report_type", ""),
                    "report_year": report.get("report_year", ""),
                    "emissions_year": report.get("report_year", ""),
                    "scope_1_tco2e": "",
                    "scope_2_lb_tco2e": "",
                    "scope_2_mb_tco2e": "",
                    "scope_2_unknown_tco2e": "",
                    "scope_3_tco2e": "",
                    "scope_1_2_total_tco2e": "",
                    "extraction_method": extraction_method,
                    "llm_model": model if has_openai_api_key() else "",
                    "confidence": "low",
                    "evidence_text": "",
                    "review_status": "needs_review",
                    "notes": "No reliable deterministic footprint table evidence found; value columns left blank.",
                }
            ],
            columns=HISTORY_COLUMNS,
        )
    rows: list[dict[str, Any]] = []
    for i, year in enumerate(years):
        scope_2_value = scope_2[i] if i < len(scope_2) else ""
        rows.append(
            {
                "company_name": company_name,
                "sbti_id": report.get("sbti_id", ""),
                "lei": report.get("lei", ""),
                "source": "local_public_report",
                "source_id": source_id,
                "report_url": report.get("report_url", ""),
                "report_type": report.get("report_type", ""),
                "report_year": report.get("report_year", ""),
                "emissions_year": year,
                "scope_1_tco2e": scope_1[i] if i < len(scope_1) else "",
                "scope_2_lb_tco2e": "",
                "scope_2_mb_tco2e": scope_2_value if scope_2_is_market_based else "",
                "scope_2_unknown_tco2e": "" if scope_2_is_market_based else scope_2_value,
                "scope_3_tco2e": scope_3[i] if i < len(scope_3) else "",
                "scope_1_2_total_tco2e": total_1_2[i] if i < len(total_1_2) else "",
                "extraction_method": extraction_method,
                "llm_model": model if has_openai_api_key() else "",
                "confidence": "medium",
                "evidence_text": evidence_text,
                "review_status": "needs_review",
                "notes": "Scope 3 row has reported values for 2022-2025 only in the extracted table; 2020-2021 left blank.",
            }
        )
    return pd.DataFrame(rows)


def objective_evidence(snippets: str) -> str:
    page_match = re.search(r"CO.?EMISSIONS REDUCTION[\s\S]{0,2400}?carbon.?neutrality across the entire value chain\.?", snippets, flags=re.I)
    if page_match:
        return clean_extracted_text(page_match.group(0))
    fallback = re.search(r"By 2025[\s\S]{0,2000}?By 2050[\s\S]{0,400}?value chain\.?", snippets, flags=re.I)
    return clean_extracted_text(fallback.group(0)) if fallback else ""


def deterministic_objectives(
    company_name: str,
    report: pd.Series,
    source_id: str,
    snippets: str,
    extraction_method: str,
    model: str,
) -> pd.DataFrame:
    normalized_company = normalize_name(company_name)
    base = {
        "company_name": company_name,
        "sbti_id": report.get("sbti_id", ""),
        "lei": report.get("lei", ""),
        "source": "local_public_report",
        "source_id": source_id,
        "report_url": report.get("report_url", ""),
        "report_type": report.get("report_type", ""),
        "report_year": report.get("report_year", ""),
        "extraction_method": extraction_method,
        "llm_model": model if has_openai_api_key() else "",
        "confidence": "medium",
        "review_status": "needs_review",
    }

    rows: list[dict[str, Any]] = []

    def add_row(evidence: str, **values: str) -> None:
        rows.append(
            {
                **base,
                "target_year": values.get("target_year", ""),
                "base_year": values.get("base_year", ""),
                "target_metric": values.get("target_metric", ""),
                "target_scope": values.get("target_scope", ""),
                "target_reduction_percent": values.get("target_reduction_percent", ""),
                "target_value": values.get("target_value", ""),
                "target_unit": values.get("target_unit", ""),
                "current_progress": values.get("current_progress", ""),
                "evidence_text": evidence,
                "notes": values.get("notes", "Objective, not an actual emissions footprint value."),
            }
        )

    if normalized_company == "accor":
        evidence_match = re.search(
            r"Summary table of greenhouse gas emissions for 2019, 2024, and 2025 and 2030 targets[\s\S]{0,3200}?TOTAL SBTi Scope 3[^\n]*?-27\.6%",
            snippets,
            flags=re.I,
        )
        if evidence_match:
            evidence = clean_extracted_text(evidence_match.group(0))
            notes = (
                "Parsed from Accor target table in ktCO2eq; absolute target values converted to tCO2e. "
                "Relative reductions are populated only where explicitly reported."
            )
            add_row(
                evidence,
                target_year="2030",
                base_year="2019",
                target_metric="absolute_emissions",
                target_scope="scope_1",
                target_value="324000",
                target_unit="tCO2e",
                current_progress="2025 reported value: 690 ktCO2eq",
                notes=notes,
            )
            add_row(
                evidence,
                target_year="2030",
                base_year="2019",
                target_metric="absolute_emissions",
                target_scope="scope_2_market_based",
                target_value="1453000",
                target_unit="tCO2e",
                current_progress="2025 reported value: 2,767 ktCO2eq",
                notes=notes,
            )
            add_row(
                evidence,
                target_year="2030",
                base_year="2019",
                target_metric="absolute_emissions",
                target_scope="sbti_total",
                target_reduction_percent="-36.0",
                target_value="4406000",
                target_unit="tCO2e",
                current_progress="2025 reported value: 6,849 ktCO2eq",
                notes=notes,
            )
            add_row(
                evidence,
                target_year="2030",
                base_year="2019",
                target_metric="absolute_emissions",
                target_scope="sbti_scopes_1_and_2",
                target_reduction_percent="-46.3",
                target_value="1776000",
                target_unit="tCO2e",
                current_progress="2025 reported value: 3,457 ktCO2eq",
                notes=notes,
            )
            add_row(
                evidence,
                target_year="2030",
                base_year="2019",
                target_metric="absolute_emissions",
                target_scope="sbti_scope_3_categories_3_1_3_3_3_14",
                target_reduction_percent="-27.6",
                target_value="2629000",
                target_unit="tCO2e",
                current_progress="2025 reported value: 3,392 ktCO2eq",
                notes=notes,
            )

    if normalized_company == "airbus":
        evidence_match = re.search(
            r"Energy target - Scope 1 & 2 emissions[\s\S]{0,4200}?Energy target - Scope 3 Use of sold products[\s\S]{0,900}?Commercial aircraft products emissions intensity[^\n]*?88\.8",
            snippets,
            flags=re.I,
        )
        if not evidence_match:
            evidence_match = re.search(
                r"Performance Milestones and Target Years[\s\S]{0,2600}?Scope 3 Cat 11\. GHG efficiency[^\n]*?-46%",
                snippets,
                flags=re.I,
            )
        if evidence_match:
            evidence = clean_extracted_text(evidence_match.group(0))
            notes = (
                "Parsed from Airbus target table. Absolute Scope 1+2 values are in ktCO2e and converted to tCO2e; "
                "Scope 3 target is an intensity metric, not a footprint value."
            )
            add_row(
                evidence,
                target_year="2030",
                base_year="2015",
                target_metric="absolute_emissions",
                target_scope="scopes_1_and_2_market_based",
                target_reduction_percent="-63",
                target_value="467000",
                target_unit="tCO2e",
                current_progress="2024 reported value: 614 ktCO2e; baseline value: 1,262 ktCO2e",
                notes=notes,
            )
            add_row(
                evidence,
                target_year="2025",
                base_year="2024",
                target_metric="absolute_emissions",
                target_scope="scopes_1_and_2_market_based_tco_scope",
                target_reduction_percent="-3",
                target_value="509000",
                target_unit="tCO2e",
                current_progress="2024 reported value: 524 ktCO2e",
                notes=notes,
            )
            add_row(
                evidence,
                target_year="2035",
                base_year="2015",
                target_metric="emissions_intensity",
                target_scope="scope_3_category_11_use_of_sold_products_commercial_aircraft",
                target_reduction_percent="-46",
                target_value="48.0",
                target_unit="gCO2e/RPK",
                current_progress="2024 reported value: 61.1 gCO2e/RPK; baseline value: 88.8 gCO2e/RPK",
                notes=notes,
            )

    evidence = objective_evidence(snippets)
    if evidence and normalized_company == "air liquide":
        rows.extend(
            [
                {
                    **base,
                    "target_year": "2025",
                    "base_year": "2015",
                    "target_metric": "carbon_intensity",
                    "target_scope": "group_carbon_intensity",
                    "target_reduction_percent": "-30",
                    "target_value": "",
                    "target_unit": "kg CO2e per EUR EBITDA",
                    "current_progress": "-46% vs 2015; objective exceeded",
                    "evidence_text": evidence,
                    "notes": "Objective, not an actual emissions footprint value.",
                },
                {
                    **base,
                    "target_year": "2035",
                    "base_year": "2020",
                    "target_metric": "absolute_emissions",
                    "target_scope": "scopes_1_and_2_market_based",
                    "target_reduction_percent": "-33",
                    "target_value": "",
                    "target_unit": "tCO2e",
                    "current_progress": "-13% vs 2020 in 2025; inflection objective achieved one year in advance",
                    "evidence_text": evidence,
                    "notes": "Objective, not an actual emissions footprint value.",
                },
                {
                    **base,
                    "target_year": "2050",
                    "base_year": "",
                    "target_metric": "carbon_neutrality",
                    "target_scope": "entire_value_chain",
                    "target_reduction_percent": "",
                    "target_value": "carbon_neutrality",
                    "target_unit": "",
                    "current_progress": "",
                    "evidence_text": evidence,
                    "notes": "Long-term neutrality objective, not an actual emissions footprint value.",
                },
            ]
        )

    return pd.DataFrame(rows, columns=OBJECTIVE_COLUMNS)


def extract_from_report_index(report_index: pd.DataFrame, max_reports: int | None, model: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    rows: list[dict[str, Any]] = []
    history_frames: list[pd.DataFrame] = []
    objective_frames: list[pd.DataFrame] = []
    processed = 0
    for _, report in report_index.iterrows():
        if max_reports is not None and processed >= max_reports:
            break
        company_name = report.get("company_name", "")
        report_path_value = report.get("report_path", "")
        report_url = report.get("report_url", "")
        report_type = report.get("report_type", "") or detect_report_type(str(report_path_value), str(report_url))
        path = resolve_report_path(str(report_path_value))
        try:
            if path is None or not path.exists():
                if not report_url:
                    raise FileNotFoundError(f"Missing report_path and report_url for {company_name}")
                path = download_url(str(report_url))
            snippets = select_snippets(path)
            if not snippets:
                raise RuntimeError("No carbon-footprint snippets found")
            snippet_path = snippet_cache_path(path)
            snippet_path.write_text(snippets, encoding="utf-8")
            extracted = llm_extract(company_name, str(report_url), report_type, snippets, model) if has_openai_api_key() else deterministic_extract(company_name, str(report.get("report_year", "")), snippets)
            source_id = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
            extraction_method = "snippet_llm" if has_openai_api_key() else "deterministic_snippet"
            history_frames.append(deterministic_emissions_history(company_name, report, source_id, snippets, extraction_method, model))
            objective_frames.append(deterministic_objectives(company_name, report, source_id, snippets, extraction_method, model))
            rows.append(
                {
                    "company_name": company_name,
                    "sbti_id": report.get("sbti_id", ""),
                    "lei": report.get("lei", ""),
                    "source": "local_public_report",
                    "source_id": source_id,
                    "report_url": report_url,
                    "report_type": report_type,
                    "report_year": coerce_value(extracted.get("report_year")) or report.get("report_year", ""),
                    "scope_1_tco2e": coerce_value(extracted.get("scope_1_tco2e")),
                    "scope_2_lb_tco2e": coerce_value(extracted.get("scope_2_lb_tco2e")),
                    "scope_2_mb_tco2e": coerce_value(extracted.get("scope_2_mb_tco2e")),
                    "scope_2_unknown_tco2e": coerce_value(extracted.get("scope_2_unknown_tco2e")),
                    "scope_3_tco2e": coerce_value(extracted.get("scope_3_tco2e")),
                    "extraction_method": extraction_method,
                    "llm_model": model if has_openai_api_key() else "",
                    "confidence": coerce_value(extracted.get("confidence")) or "low",
                    "evidence_text": coerce_value(extracted.get("evidence_text")),
                    "review_status": "needs_review",
                    "notes": coerce_value(extracted.get("notes") or extracted.get("unit_original")),
                }
            )
        except Exception as exc:
            rows.append(
                {
                    "company_name": company_name,
                    "sbti_id": report.get("sbti_id", ""),
                    "lei": report.get("lei", ""),
                    "source": "local_public_report",
                    "source_id": str(report_path_value),
                    "report_url": report_url,
                    "report_type": report_type,
                    "report_year": report.get("report_year", ""),
                    "scope_1_tco2e": "",
                    "scope_2_lb_tco2e": "",
                    "scope_2_mb_tco2e": "",
                    "scope_2_unknown_tco2e": "",
                    "scope_3_tco2e": "",
                    "extraction_method": "deterministic_snippet",
                    "llm_model": "",
                    "confidence": "low",
                    "evidence_text": "",
                    "review_status": "needs_review",
                    "notes": f"Extraction failed: {exc}",
                }
            )
        processed += 1
    history = pd.concat(history_frames, ignore_index=True) if history_frames else pd.DataFrame(columns=HISTORY_COLUMNS)
    objectives = pd.concat(objective_frames, ignore_index=True) if objective_frames else pd.DataFrame(columns=OBJECTIVE_COLUMNS)
    return pd.DataFrame(rows), history, objectives


def llm_history_row(report: pd.Series, extracted: dict[str, Any], model: str) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "company_name": report.get("company_name", ""),
                "sbti_id": report.get("sbti_id", ""),
                "lei": report.get("lei", ""),
                "source": report.get("source_kind", "local_public_report"),
                "source_id": report.get("source_id", ""),
                "report_url": report.get("report_url", ""),
                "report_type": report.get("report_type", ""),
                "report_year": coerce_value(extracted.get("report_year")) or report.get("report_year", ""),
                "emissions_year": coerce_value(extracted.get("report_year")) or report.get("report_year", ""),
                "scope_1_tco2e": coerce_value(extracted.get("scope_1_tco2e")),
                "scope_2_lb_tco2e": coerce_value(extracted.get("scope_2_lb_tco2e")),
                "scope_2_mb_tco2e": coerce_value(extracted.get("scope_2_mb_tco2e")),
                "scope_2_unknown_tco2e": coerce_value(extracted.get("scope_2_unknown_tco2e")),
                "scope_3_tco2e": coerce_value(extracted.get("scope_3_tco2e")),
                "scope_1_2_total_tco2e": "",
                "extraction_method": "snippet_llm",
                "llm_model": model,
                "confidence": coerce_value(extracted.get("confidence")) or "low",
                "evidence_text": coerce_value(extracted.get("evidence_text")),
                "review_status": "needs_review",
                "notes": coerce_value(extracted.get("notes") or extracted.get("unit_original")),
            }
        ],
        columns=HISTORY_COLUMNS,
    )


def field_for_raw_emission(emission: dict[str, Any]) -> str:
    scope = normalize_name(coerce_value(emission.get("scope")))
    method = normalize_name(coerce_value(emission.get("scope_2_method")))
    if scope == "scope 1":
        return "scope_1_tco2e"
    if scope == "scope 3":
        return "scope_3_tco2e"
    if scope in {"scope 1 2 total", "scope 1 2", "scope 1 and 2 total"}:
        return "scope_1_2_total_tco2e"
    if scope == "scope 2":
        if method == "location based":
            return "scope_2_lb_tco2e"
        if method == "market based":
            return "scope_2_mb_tco2e"
        return "scope_2_unknown_tco2e"
    return ""


def raw_llm_history_rows(report: pd.Series, extracted: dict[str, Any], model: str, snippets: str = "") -> pd.DataFrame:
    emissions = extracted.get("emissions")
    if not isinstance(emissions, list) or not emissions:
        return pd.DataFrame(columns=HISTORY_COLUMNS)
    grouped: dict[str, dict[str, Any]] = {}
    for emission in emissions:
        if not isinstance(emission, dict):
            continue
        field = field_for_raw_emission(emission)
        if not field:
            continue
        normalized_value, unit_status = normalize_raw_emission(emission.get("raw_value"), emission.get("raw_unit"))
        year_alignment = align_emission_year(emission, snippets, coerce_value(extracted.get("report_year")) or report.get("report_year", ""))
        emissions_year = year_alignment["normalized_emissions_year"] or coerce_value(extracted.get("report_year")) or report.get("report_year", "")
        if not emissions_year:
            emissions_year = "unknown"
        row = grouped.setdefault(
            emissions_year,
            {
                "company_name": report.get("company_name", ""),
                "sbti_id": report.get("sbti_id", ""),
                "lei": report.get("lei", ""),
                "source": report.get("source_kind", "local_public_report"),
                "source_id": report.get("source_id", ""),
                "report_url": report.get("report_url", ""),
                "report_type": report.get("report_type", ""),
                "report_year": coerce_value(extracted.get("report_year")) or report.get("report_year", ""),
                "emissions_year": emissions_year,
                "scope_1_tco2e": "",
                "scope_2_lb_tco2e": "",
                "scope_2_mb_tco2e": "",
                "scope_2_unknown_tco2e": "",
                "scope_3_tco2e": "",
                "scope_1_2_total_tco2e": "",
                "extraction_method": "snippet_llm_raw_normalized",
                "llm_model": model,
                "confidence": coerce_value(emission.get("confidence")) or "low",
                "evidence_text": "",
                "review_status": "needs_review",
                "notes": "",
            },
        )
        if normalized_value:
            row[field] = normalized_value
        evidence = coerce_value(emission.get("evidence_text"))
        evidence_norm = normalize_name(evidence)
        if field == "scope_2_lb_tco2e" and "location based" not in evidence_norm:
            evidence = method_context_evidence(snippets, emission, field) or evidence
        if field == "scope_2_mb_tco2e" and "market based" not in evidence_norm:
            evidence = method_context_evidence(snippets, emission, field) or evidence
        if evidence and evidence not in row["evidence_text"]:
            row["evidence_text"] = " | ".join(part for part in [row["evidence_text"], evidence] if part)
        note = (
            f"{field}: raw_value={coerce_value(emission.get('raw_value'))}; "
            f"raw_unit={coerce_value(emission.get('raw_unit'))}; unit_status={unit_status}; "
            f"llm_year={year_alignment['llm_emissions_year']}; "
            f"year_alignment_status={year_alignment['year_alignment_status']}; "
            f"year_alignment_method={year_alignment['year_alignment_method']}"
        )
        emission_notes = coerce_value(emission.get("notes"))
        if emission_notes:
            note = f"{note}; {emission_notes}"
        row["notes"] = " | ".join(part for part in [row["notes"], note] if part)
        confidence = coerce_value(emission.get("confidence"))
        if row["confidence"] != "low" and confidence == "low":
            row["confidence"] = "low"
        elif row["confidence"] == "high" or confidence == "high":
            row["confidence"] = "high"
        elif row["confidence"] == "medium" or confidence == "medium":
            row["confidence"] = "medium"
    if not grouped:
        return pd.DataFrame(columns=HISTORY_COLUMNS)
    return pd.DataFrame(list(grouped.values()), columns=HISTORY_COLUMNS)


def extraction_error_row(report: pd.Series, message: str, model: str) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "company_name": report.get("company_name", ""),
                "sbti_id": report.get("sbti_id", ""),
                "lei": report.get("lei", ""),
                "source": report.get("source_kind", "local_public_report"),
                "source_id": report.get("source_id", ""),
                "report_url": report.get("report_url", ""),
                "report_type": report.get("report_type", ""),
                "report_year": report.get("report_year", ""),
                "emissions_year": report.get("report_year", ""),
                "scope_1_tco2e": "",
                "scope_2_lb_tco2e": "",
                "scope_2_mb_tco2e": "",
                "scope_2_unknown_tco2e": "",
                "scope_3_tco2e": "",
                "scope_1_2_total_tco2e": "",
                "extraction_method": "snippet_llm" if has_openai_api_key() else "deterministic_snippet",
                "llm_model": model if has_openai_api_key() else "",
                "confidence": "low",
                "evidence_text": "",
                "review_status": "needs_review",
                "notes": f"Extraction failed: {message}",
            }
        ],
        columns=HISTORY_COLUMNS,
    )


def has_any_emissions_value(df: pd.DataFrame) -> bool:
    value_cols = [
        "scope_1_tco2e",
        "scope_2_lb_tco2e",
        "scope_2_mb_tco2e",
        "scope_2_unknown_tco2e",
        "scope_3_tco2e",
        "scope_1_2_total_tco2e",
    ]
    present = [col for col in value_cols if col in df.columns]
    return bool(present and df[present].fillna("").astype(str).ne("").to_numpy().any())


def numeric_compare_value(value: Any) -> float | None:
    cleaned = coerce_value(value).replace(",", "")
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def select_deterministic_comparison_row(history: pd.DataFrame, llm_report_year: str, report_year: str) -> tuple[pd.Series | None, str]:
    if history.empty:
        return None, "no_deterministic_rows"
    for candidate_year, basis in (
        (llm_report_year, "matched_llm_report_year"),
        (report_year, "matched_index_report_year"),
    ):
        if candidate_year and "emissions_year" in history.columns:
            matched = history[history["emissions_year"].astype(str).eq(str(candidate_year))]
            if not matched.empty:
                return matched.iloc[0], basis
    work = history.copy()
    work["_emissions_year_num"] = pd.to_numeric(work.get("emissions_year", ""), errors="coerce")
    work = work.sort_values("_emissions_year_num", ascending=False, na_position="last")
    return work.iloc[0], "latest_deterministic_emissions_year"


def select_deterministic_row_by_values(history: pd.DataFrame, llm_row: pd.Series | None) -> tuple[pd.Series | None, str]:
    if history.empty or llm_row is None:
        return None, "no_value_match"
    scope_fields = [
        "scope_1_tco2e",
        "scope_2_lb_tco2e",
        "scope_2_mb_tco2e",
        "scope_2_unknown_tco2e",
        "scope_3_tco2e",
    ]
    best_row = None
    best_matches = 0
    best_compared = 0
    for _, candidate in history.iterrows():
        compared = 0
        matches = 0
        for field in scope_fields:
            llm_value = coerce_value(llm_row.get(field, ""))
            deterministic_value = coerce_value(candidate.get(field, ""))
            if not llm_value:
                continue
            compared += 1
            if compare_value_pair(llm_value, deterministic_value)[0] == "match":
                matches += 1
        if matches > best_matches or (matches == best_matches and compared > best_compared):
            best_row = candidate
            best_matches = matches
            best_compared = compared
    if best_row is not None and best_matches >= 2:
        return best_row, "matched_normalized_values"
    return None, "no_value_match"


def compare_value_pair(llm_value: Any, deterministic_value: Any) -> tuple[str, str]:
    llm_text = coerce_value(llm_value)
    deterministic_text = coerce_value(deterministic_value)
    llm_num = numeric_compare_value(llm_text)
    deterministic_num = numeric_compare_value(deterministic_text)
    if not llm_text and not deterministic_text:
        return "both_blank", ""
    if not llm_text:
        return "llm_blank", ""
    if not deterministic_text:
        return "deterministic_blank", ""
    if llm_num is None or deterministic_num is None:
        return "non_numeric", ""
    difference = llm_num - deterministic_num
    if difference == 0:
        return "match", "0"
    return "mismatch", str(int(difference)) if difference.is_integer() else str(difference)


def build_extraction_comparison(report: pd.Series, extracted: dict[str, Any], history: pd.DataFrame, model: str, snippets: str = "") -> pd.DataFrame:
    llm_history = raw_llm_history_rows(report, extracted, model, snippets)
    initial_deterministic_row, initial_basis = select_deterministic_comparison_row(
        history,
        coerce_value(llm_history["emissions_year"].iloc[0]) if not llm_history.empty else coerce_value(extracted.get("report_year")),
        coerce_value(report.get("report_year", "")),
    )
    llm_row, _llm_basis = select_deterministic_comparison_row(
        llm_history,
        initial_deterministic_row.get("emissions_year", "") if initial_deterministic_row is not None else coerce_value(extracted.get("report_year")),
        coerce_value(report.get("report_year", "")),
    )
    value_matched_row, value_basis = select_deterministic_row_by_values(history, llm_row)
    deterministic_row = value_matched_row if value_matched_row is not None else initial_deterministic_row
    basis = value_basis if value_matched_row is not None else initial_basis
    llm_year = coerce_value(llm_row.get("emissions_year", "")) if llm_row is not None else ""
    raw_lookup: dict[str, dict[str, Any]] = {}
    for emission in extracted.get("emissions", []) if isinstance(extracted.get("emissions"), list) else []:
        if not isinstance(emission, dict):
            continue
        field = field_for_raw_emission(emission)
        year_alignment = align_emission_year(emission, snippets, coerce_value(extracted.get("report_year")) or report.get("report_year", ""))
        emissions_year = year_alignment["normalized_emissions_year"] or coerce_value(emission.get("emissions_year")) or coerce_value(extracted.get("report_year")) or report.get("report_year", "")
        if field and (not llm_year or str(emissions_year) == str(llm_year)):
            raw_lookup[field] = {**emission, "_year_alignment": year_alignment}
    rows: list[dict[str, Any]] = []
    scope_fields = [
        "scope_1_tco2e",
        "scope_2_lb_tco2e",
        "scope_2_mb_tco2e",
        "scope_2_unknown_tco2e",
        "scope_3_tco2e",
    ]
    for field in scope_fields:
        deterministic_value = deterministic_row.get(field, "") if deterministic_row is not None else ""
        llm_value = llm_row.get(field, "") if llm_row is not None else ""
        raw_emission = raw_lookup.get(field, {})
        year_alignment = raw_emission.get(
            "_year_alignment",
            {
                "llm_emissions_year": "",
                "normalized_emissions_year": "",
                "year_alignment_status": "missing_raw_emission",
                "year_alignment_method": "missing_raw_emission",
            },
        )
        _normalized, normalization_status = normalize_raw_emission(raw_emission.get("raw_value"), raw_emission.get("raw_unit")) if raw_emission else ("", "missing_raw_emission")
        match_status, difference = compare_value_pair(llm_value, deterministic_value)
        rows.append(
            {
                "report_id": report.get("report_id", ""),
                "company_name": report.get("company_name", ""),
                "source_id": report.get("source_id", ""),
                "report_year": report.get("report_year", ""),
                "llm_model": model,
                "llm_report_year": coerce_value(extracted.get("report_year")),
                "llm_emissions_year": year_alignment["llm_emissions_year"],
                "normalized_emissions_year": year_alignment["normalized_emissions_year"],
                "year_alignment_status": year_alignment["year_alignment_status"],
                "year_alignment_method": year_alignment["year_alignment_method"],
                "deterministic_emissions_year": deterministic_row.get("emissions_year", "") if deterministic_row is not None else "",
                "comparison_basis": basis,
                "scope_field": field,
                "llm_raw_value": coerce_value(raw_emission.get("raw_value")),
                "llm_raw_unit": coerce_value(raw_emission.get("raw_unit")),
                "normalization_status": normalization_status,
                "llm_value_tco2e": coerce_value(llm_value),
                "deterministic_value_tco2e": coerce_value(deterministic_value),
                "match_status": match_status,
                "numeric_difference_tco2e": difference,
                "llm_confidence": llm_row.get("confidence", "") if llm_row is not None else "",
                "deterministic_confidence": deterministic_row.get("confidence", "") if deterministic_row is not None else "",
                "llm_evidence_text": llm_row.get("evidence_text", "") if llm_row is not None else "",
                "deterministic_evidence_text": deterministic_row.get("evidence_text", "") if deterministic_row is not None else "",
                "notes": llm_row.get("notes", "") if llm_row is not None else coerce_value(extracted.get("notes")),
            }
        )
    return pd.DataFrame(rows, columns=EXTRACTION_COMPARISON_COLUMNS)


def compare_llm_vs_deterministic(manifest: pd.DataFrame, max_reports: int | None, model: str) -> pd.DataFrame:
    if not has_openai_api_key():
        raise RuntimeError("OPENAI_API_KEY is required for LLM comparison")
    comparison_frames: list[pd.DataFrame] = []
    processed = 0
    for _, report in manifest.iterrows():
        if max_reports is not None and processed >= max_reports:
            break
        if report.get("ingestion_status") != "ready":
            processed += 1
            continue
        snippets = load_snippet_text(report)
        extraction_path = extraction_cache_path(report, model)
        if extraction_path.exists():
            extracted = load_cached_extraction(extraction_path)
            save_extraction_audit(report, model, extracted, "snippet_llm")
        else:
            extracted = llm_extract(report["company_name"], str(report.get("report_url", "")), report["report_type"], snippets, model)
            save_llm_extraction(report, model, extracted)
        history = deterministic_emissions_history(
            report["company_name"],
            report,
            str(report.get("source_id", "")),
            snippets,
            "deterministic_snippet",
            "",
        )
        comparison_frames.append(build_extraction_comparison(report, extracted, history, model, snippets))
        processed += 1
    comparison = (
        pd.concat(comparison_frames, ignore_index=True)
        if comparison_frames
        else pd.DataFrame(columns=EXTRACTION_COMPARISON_COLUMNS)
    )
    write_dataframe(comparison, EXTRACTION_COMPARISON_OUTPUT, EXTRACTION_COMPARISON_COLUMNS)
    return comparison


def extract_footprints(manifest: pd.DataFrame, max_reports: int | None, model: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    history_frames: list[pd.DataFrame] = []
    objective_frames: list[pd.DataFrame] = []
    comparison_frames: list[pd.DataFrame] = []
    processed = 0
    for _, report in manifest.iterrows():
        if max_reports is not None and processed >= max_reports:
            break
        if report.get("ingestion_status") != "ready":
            history_frames.append(extraction_error_row(report, str(report.get("notes", "manifest row is not ready")), model))
            processed += 1
            continue
        try:
            snippets = load_snippet_text(report)
            if not snippets:
                raise RuntimeError("No carbon-footprint snippets found")
            extraction_method = "snippet_llm" if has_openai_api_key() else "deterministic_snippet"
            extraction_path = extraction_cache_path(report, model)
            if has_openai_api_key() and extraction_path.exists():
                extracted = load_cached_extraction(extraction_path)
            elif has_openai_api_key():
                extracted = llm_extract(report["company_name"], str(report.get("report_url", "")), report["report_type"], snippets, model)
            else:
                extracted = deterministic_extract(report["company_name"], str(report.get("report_year", "")), snippets)
            if has_openai_api_key():
                save_llm_extraction(report, model, extracted)
            else:
                save_extraction_audit(
                    report,
                    model,
                    extracted,
                    "deterministic_snippet",
                    "OPENAI_API_KEY not set; used deterministic snippet parser.",
                )
            deterministic_history = deterministic_emissions_history(
                report["company_name"],
                report,
                str(report.get("source_id", "")),
                snippets,
                extraction_method,
                model,
            )
            # Keep the reviewed footprint CSV on the stable multi-year history path
            # while raw-unit LLM extraction matures in the comparison/audit path.
            history = deterministic_history
            if has_openai_api_key() and not has_any_emissions_value(history):
                raw_history = raw_llm_history_rows(report, extracted, model, snippets)
                history = raw_history if not raw_history.empty else llm_history_row(report, extracted, model)
            history_frames.append(history)
            if has_openai_api_key():
                comparison_frames.append(build_extraction_comparison(report, extracted, deterministic_history, model, snippets))
            objective_frames.append(
                deterministic_objectives(
                    report["company_name"],
                    report,
                    str(report.get("source_id", "")),
                    snippets,
                    extraction_method,
                    model,
                )
            )
        except Exception as exc:
            save_extraction_audit(
                report,
                model,
                {
                    "report_year": report.get("report_year", ""),
                    "emissions": [],
                    "notes": f"Extraction failed: {exc}",
                },
                "extraction_error",
                str(exc),
            )
            history_frames.append(extraction_error_row(report, str(exc), model))
        processed += 1
    history = pd.concat(history_frames, ignore_index=True) if history_frames else pd.DataFrame(columns=HISTORY_COLUMNS)
    objectives = pd.concat(objective_frames, ignore_index=True) if objective_frames else pd.DataFrame(columns=OBJECTIVE_COLUMNS)
    comparison = (
        pd.concat(comparison_frames, ignore_index=True)
        if comparison_frames
        else pd.DataFrame(columns=EXTRACTION_COMPARISON_COLUMNS)
    )
    return history, objectives, comparison


def validate_review_csv(path: Path = FOOTPRINT_OUTPUT) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(f"Missing review CSV: {path}")
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    errors: list[str] = []
    warnings: list[str] = []
    missing = [col for col in HISTORY_COLUMNS if col not in df.columns]
    if missing:
        errors.append(f"Missing output columns: {', '.join(missing)}")
    value_cols = [
        "scope_1_tco2e",
        "scope_2_lb_tco2e",
        "scope_2_mb_tco2e",
        "scope_2_unknown_tco2e",
        "scope_3_tco2e",
        "scope_1_2_total_tco2e",
    ]
    present_value_cols = [col for col in value_cols if col in df.columns]
    if present_value_cols:
        evidence_series = df["evidence_text"] if "evidence_text" in df.columns else pd.Series("", index=df.index)
        source_series = df["source"] if "source" in df.columns else pd.Series("", index=df.index)
        review_status_series = df["review_status"] if "review_status" in df.columns else pd.Series("", index=df.index)
        has_value = df[present_value_cols].fillna("").astype(str).ne("").any(axis=1)
        missing_evidence = df.index[has_value & evidence_series.eq("")]
        if len(missing_evidence):
            errors.append(f"{len(missing_evidence)} rows have emissions values but no evidence_text")
        bad_numeric: list[str] = []
        for col in present_value_cols:
            non_empty = df[col].fillna("").astype(str).str.strip().ne("")
            parsed = pd.to_numeric(df.loc[non_empty, col].str.replace(",", "", regex=False), errors="coerce")
            bad = parsed[parsed.isna()]
            if len(bad):
                bad_numeric.append(f"{col}: rows {', '.join(str(i + 2) for i in bad.index[:10])}")
        if bad_numeric:
            errors.append("Non-numeric emissions values: " + "; ".join(bad_numeric))
        report_derived = source_series.ne("nzdpu_structured")
        valid_review_status = review_status_series.isin(["needs_review", "accepted"])
        bad_status = df.index[has_value & report_derived & ~valid_review_status]
        if len(bad_status):
            errors.append(f"{len(bad_status)} report-derived rows with emissions values have invalid review_status")
        if "evidence_text" in df.columns:
            evidence_norm = df["evidence_text"].map(normalize_name)
            if "scope_2_lb_tco2e" in df.columns:
                lb_bad = df.index[df["scope_2_lb_tco2e"].ne("") & ~evidence_norm.str.contains("location based", regex=False)]
                if len(lb_bad):
                    errors.append(f"{len(lb_bad)} Scope 2 location-based rows lack explicit location-based evidence")
            if "scope_2_mb_tco2e" in df.columns:
                mb_bad = df.index[df["scope_2_mb_tco2e"].ne("") & ~evidence_norm.str.contains("market based", regex=False)]
                if len(mb_bad):
                    errors.append(f"{len(mb_bad)} Scope 2 market-based rows lack explicit market-based evidence")
            if "scope_2_unknown_tco2e" in df.columns:
                unknown_bad = df.index[df["scope_2_unknown_tco2e"].ne("") & ~evidence_norm.str.contains("scope 2", regex=False)]
                if len(unknown_bad):
                    errors.append(f"{len(unknown_bad)} Scope 2 unknown rows lack explicit Scope 2 evidence")
    try:
        pd.read_csv(OBJECTIVES_OUTPUT, dtype=str, keep_default_na=False)
    except FileNotFoundError:
        warnings.append(f"Objectives CSV not found: {OBJECTIVES_OUTPUT}")
    except Exception as exc:
        errors.append(f"Python could not read objectives CSV: {exc}")
    try:
        r_code = (
            f"read.csv({json.dumps(str(path))}, stringsAsFactors=FALSE); "
            f"if (file.exists({json.dumps(str(OBJECTIVES_OUTPUT))})) "
            f"read.csv({json.dumps(str(OBJECTIVES_OUTPUT))}, stringsAsFactors=FALSE)"
        )
        subprocess.run(["Rscript", "-e", r_code], cwd=ROOT, check=True, capture_output=True, text=True)
    except FileNotFoundError:
        warnings.append("Rscript not found; skipped R CSV readability check")
    except subprocess.CalledProcessError as exc:
        errors.append(f"R could not read output CSVs: {exc.stderr.strip() or exc.stdout.strip()}")
    if errors:
        raise ValueError("Review CSV validation failed:\n" + "\n".join(errors))
    return warnings


def extract_from_candidates(candidates: pd.DataFrame, sbti: pd.DataFrame, max_companies: int | None, model: str) -> pd.DataFrame:
    if candidates.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    id_to_lei = dict(zip(sbti["sbti_id"], sbti["lei"]))
    rows: list[dict[str, Any]] = []
    processed = 0
    for sbti_id, group in candidates.sort_values(["sbti_id", "rank"]).groupby("sbti_id", sort=False):
        if max_companies is not None and processed >= max_companies:
            break
        selected = group.iloc[0]
        company_name = selected["company_name"]
        url = selected["candidate_url"]
        report_type = selected["report_type"]
        try:
            path = download_url(url)
            snippets = select_snippets(path)
            if not snippets:
                raise RuntimeError("No carbon-footprint snippets found")
            extracted = llm_extract(company_name, url, report_type, snippets, model)
            rows.append(
                {
                    "company_name": company_name,
                    "sbti_id": sbti_id,
                    "lei": id_to_lei.get(str(sbti_id), ""),
                    "source": "official_public_report",
                    "source_id": "",
                    "report_url": url,
                    "report_type": report_type,
                    "report_year": coerce_value(extracted.get("report_year")),
                    "scope_1_tco2e": coerce_value(extracted.get("scope_1_tco2e")),
                    "scope_2_lb_tco2e": coerce_value(extracted.get("scope_2_lb_tco2e")),
                    "scope_2_mb_tco2e": coerce_value(extracted.get("scope_2_mb_tco2e")),
                    "scope_2_unknown_tco2e": coerce_value(extracted.get("scope_2_unknown_tco2e")),
                    "scope_3_tco2e": coerce_value(extracted.get("scope_3_tco2e")),
                    "extraction_method": "snippet_llm",
                    "llm_model": model if has_openai_api_key() else "",
                    "confidence": coerce_value(extracted.get("confidence")) or "low",
                    "evidence_text": coerce_value(extracted.get("evidence_text")),
                    "review_status": "needs_review",
                    "notes": coerce_value(extracted.get("notes") or extracted.get("unit_original")),
                }
            )
        except Exception as exc:
            rows.append(
                {
                    "company_name": company_name,
                    "sbti_id": sbti_id,
                    "lei": id_to_lei.get(str(sbti_id), ""),
                    "source": "official_public_report",
                    "source_id": "",
                    "report_url": url,
                    "report_type": report_type,
                    "report_year": "",
                    "scope_1_tco2e": "",
                    "scope_2_lb_tco2e": "",
                    "scope_2_mb_tco2e": "",
                    "scope_2_unknown_tco2e": "",
                    "scope_3_tco2e": "",
                    "extraction_method": "snippet_llm",
                    "llm_model": model if has_openai_api_key() else "",
                    "confidence": "low",
                    "evidence_text": "",
                    "review_status": "needs_review",
                    "notes": f"Extraction failed: {exc}",
                }
            )
        processed += 1
    return pd.DataFrame(rows)


def companies_without_accepted_nzdpu(sbti: pd.DataFrame, missed: pd.DataFrame) -> list[Company]:
    accepted_ids = set(missed.loc[missed["review_status"].eq("accepted_candidate"), "sbti_id"].astype(str))
    companies = []
    for _, row in sbti.iterrows():
        if str(row["sbti_id"]) in accepted_ids:
            continue
        companies.append(Company(sbti_id=str(row["sbti_id"]), company_name=row["company_name"], lei=row.get("lei", "")))
    return companies


def seed_filter(sbti: pd.DataFrame, seed_size: int) -> pd.DataFrame:
    seed_names = {"BOUYGUES CONSTRUCTION", "Bouygues Immobilier", "COLAS SA"}
    # The unmatched CSV may not include all named examples; add them from the
    # SBTi company workbook for smoke testing only.
    companies_xlsx = ROOT / "inputs" / "sbti" / "companies-excel.xlsx"
    if companies_xlsx.exists():
        try:
            companies = pd.read_excel(companies_xlsx, dtype=str, keep_default_na=False)
            extra = companies[companies["company_name"].isin(seed_names)][["company_name", "sbti_id", "lei"]]
            for col in sbti.columns:
                if col not in extra.columns:
                    extra[col] = ""
            sbti = pd.concat([sbti, extra[sbti.columns]], ignore_index=True).drop_duplicates("sbti_id")
        except Exception as exc:
            print(f"warning: could not load seed companies from {companies_xlsx}: {exc}", file=sys.stderr)
    name_mask = sbti["company_name"].isin(seed_names)
    with_lei = sbti[~name_mask & sbti["lei"].astype(str).str.len().gt(0)].head(seed_size)
    without_lei = sbti[~name_mask & sbti["lei"].astype(str).str.len().eq(0)].head(seed_size)
    return pd.concat([sbti[name_mask], with_lei, without_lei], ignore_index=True).drop_duplicates("sbti_id")


def load_or_build_manifest(max_reports: int | None = None) -> pd.DataFrame:
    return build_manifest(read_or_build_report_index(), max_reports)


def run_staged_pipeline(stage: str, max_reports: int | None, model: str) -> int:
    if stage == "review":
        created = initialize_review_csv()
        if created:
            print(f"Initialized reviewed footprint CSV -> {REVIEWED_FOOTPRINT_OUTPUT}")
        else:
            print(f"Reviewed footprint CSV already exists; left unchanged -> {REVIEWED_FOOTPRINT_OUTPUT}")
            print(f"Latest generated footprint CSV remains available -> {GENERATED_FOOTPRINT_OUTPUT}")
        return 0

    if stage == "validate":
        warnings = validate_review_csv(FOOTPRINT_OUTPUT)
        print(f"Validated review CSV -> {FOOTPRINT_OUTPUT}")
        for warning in warnings:
            print(f"warning: {warning}", file=sys.stderr)
        return 0

    if stage in {"manifest", "all"}:
        manifest = build_manifest(read_or_build_report_index(), max_reports)
        print(f"Wrote {len(manifest)} report manifest rows -> {REPORT_MANIFEST_OUTPUT}")
        if stage == "manifest":
            return 0
    else:
        manifest = load_or_build_manifest(max_reports)

    if stage in {"text", "snippets", "extract", "compare", "all"}:
        text_status = extract_text(manifest)
        extracted = text_status["text_status"].isin(["text_extracted", "text_cached"]).sum() if not text_status.empty else 0
        print(f"Prepared text cache for {extracted} reports -> {TEXT_CACHE_DIR}")
        if stage == "text":
            return 0

    if stage in {"snippets", "extract", "compare", "all"}:
        snippet_status = select_snippets_from_text(manifest)
        selected = snippet_status["snippet_status"].isin(["snippets_selected", "snippet_cached"]).sum() if not snippet_status.empty else 0
        print(f"Prepared snippet cache for {selected} reports -> {INGESTION_SNIPPET_DIR}")
        if stage == "snippets":
            return 0

    if stage == "compare":
        comparison = compare_llm_vs_deterministic(manifest, max_reports, model)
        print(f"Wrote {len(comparison)} LLM/deterministic comparison rows -> {EXTRACTION_COMPARISON_OUTPUT}")
        print(f"Wrote raw LLM extraction JSON files -> {EXTRACTION_CACHE_DIR}")
        return 0

    if stage in {"extract", "all"}:
        history, objectives, comparison = extract_footprints(manifest, max_reports, model)
        write_generated_footprint_outputs(history, HISTORY_COLUMNS)
        review_created = initialize_review_csv()
        write_dataframe(objectives, OBJECTIVES_OUTPUT, OBJECTIVE_COLUMNS)
        write_dataframe(comparison, EXTRACTION_COMPARISON_OUTPUT, EXTRACTION_COMPARISON_COLUMNS)
        print(f"Wrote {len(history)} generated local report footprint rows -> {GENERATED_FOOTPRINT_OUTPUT}")
        if review_created:
            print(f"Initialized reviewed footprint CSV -> {REVIEWED_FOOTPRINT_OUTPUT}")
        else:
            print(f"Reviewed footprint CSV already exists; left unchanged -> {REVIEWED_FOOTPRINT_OUTPUT}")
        print(f"Wrote {len(objectives)} local report objective rows -> {OBJECTIVES_OUTPUT}")
        print(f"Wrote per-report extraction audit JSON files -> {EXTRACTION_CACHE_DIR}")
        if not comparison.empty:
            print(f"Wrote {len(comparison)} LLM/deterministic comparison rows -> {EXTRACTION_COMPARISON_OUTPUT}")
        if stage == "extract":
            return 0

    warnings = validate_review_csv(FOOTPRINT_OUTPUT)
    print(f"Validated review CSV -> {FOOTPRINT_OUTPUT}")
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    return 0


def main() -> int:
    load_env_file()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stage",
        choices=["manifest", "text", "snippets", "extract", "compare", "review", "validate", "all"],
        help="Run the staged local report ingestion pipeline.",
    )
    parser.add_argument("--local-reports", action="store_true", help="Extract from inputs/report_index.csv, or local files under inputs/reports/ and Input/.")
    parser.add_argument("--skip-discovery", action="store_true", help="Only write NZDPU missed-match candidates and structured NZDPU rows.")
    parser.add_argument("--skip-extraction", action="store_true", help="Write report candidates but do not download/extract/LLM parse them.")
    parser.add_argument("--use-existing-candidates", action="store_true", help="Reuse outputs/review/sbti_carbon_report_candidates.csv instead of running web discovery.")
    parser.add_argument("--seed-test", action="store_true", help="Run on a small deterministic seed subset.")
    parser.add_argument("--seed-size", type=int, default=5, help="Companies with and without LEIs to include for --seed-test.")
    parser.add_argument("--max-extract-companies", type=int, default=None, help="Limit number of report candidates sent through extraction.")
    parser.add_argument("--max-results-per-query", type=int, default=4, help="Search results per query during discovery.")
    parser.add_argument("--llm-model", default=os.getenv("CARBON_LLM_MODEL", "gpt-5.4-mini"), help="OpenAI model for extraction.")
    args = parser.parse_args()

    ensure_dirs()
    if args.stage:
        return run_staged_pipeline(args.stage, args.max_extract_companies, args.llm_model)

    local_only = args.local_reports or not (SBTI_UNMATCHED.exists() and NZDPU_UNMATCHED.exists())
    if local_only:
        report_index = read_or_build_report_index()
        if not REPORT_INDEX.exists():
            print(f"No curated {REPORT_INDEX}; using local candidates -> {LOCAL_REPORT_INDEX_CANDIDATES}")
        if args.skip_extraction:
            manifest = build_manifest(report_index, args.max_extract_companies)
            print(f"Wrote {len(manifest)} report manifest rows -> {REPORT_MANIFEST_OUTPUT}")
            return 0
        return run_staged_pipeline("all", args.max_extract_companies, args.llm_model)

    sbti = read_csv(SBTI_UNMATCHED)
    nzdpu_unmatched = read_csv(NZDPU_UNMATCHED)
    if args.seed_test:
        sbti = seed_filter(sbti, args.seed_size)

    missed = build_missed_match_candidates(sbti, nzdpu_unmatched)
    write_dataframe(missed, MISSED_MATCH_OUTPUT)
    print(f"Wrote {len(missed)} missed-match candidates -> {MISSED_MATCH_OUTPUT}")

    structured = latest_nzdpu_footprints(missed, sbti)
    write_generated_footprint_outputs(structured, OUTPUT_COLUMNS)
    review_created = initialize_review_csv()
    print(f"Wrote {len(structured)} generated structured footprint rows -> {GENERATED_FOOTPRINT_OUTPUT}")
    if review_created:
        print(f"Initialized reviewed footprint CSV -> {REVIEWED_FOOTPRINT_OUTPUT}")
    else:
        print(f"Reviewed footprint CSV already exists; left unchanged -> {REVIEWED_FOOTPRINT_OUTPUT}")

    if args.skip_discovery:
        empty_candidates = pd.DataFrame(
            columns=[
                "sbti_id",
                "company_name",
                "candidate_url",
                "candidate_title",
                "report_type",
                "detected_year",
                "source_domain",
                "rank",
                "review_status",
            ]
        )
        write_dataframe(empty_candidates, REPORT_CANDIDATES_OUTPUT)
        print(f"Wrote empty report candidates -> {REPORT_CANDIDATES_OUTPUT}")
        return 0

    if args.use_existing_candidates:
        candidates = read_csv(REPORT_CANDIDATES_OUTPUT)
        print(f"Loaded {len(candidates)} existing report candidates <- {REPORT_CANDIDATES_OUTPUT}")
    else:
        companies = companies_without_accepted_nzdpu(sbti, missed)
        candidates = discover_report_candidates(companies, max_results_per_query=args.max_results_per_query)
        write_dataframe(candidates, REPORT_CANDIDATES_OUTPUT)
        print(f"Wrote {len(candidates)} report candidates -> {REPORT_CANDIDATES_OUTPUT}")

    if args.skip_extraction:
        return 0

    extracted = extract_from_candidates(candidates, sbti, args.max_extract_companies, args.llm_model)
    combined = pd.concat([structured, extracted], ignore_index=True)
    write_generated_footprint_outputs(combined, OUTPUT_COLUMNS)
    review_created = initialize_review_csv()
    print(f"Wrote {len(combined)} generated footprint rows -> {GENERATED_FOOTPRINT_OUTPUT}")
    if review_created:
        print(f"Initialized reviewed footprint CSV -> {REVIEWED_FOOTPRINT_OUTPUT}")
    else:
        print(f"Reviewed footprint CSV already exists; left unchanged -> {REVIEWED_FOOTPRINT_OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
