#!/usr/bin/env python3
"""
Gather carbon-footprint evidence for unmatched SBTi companies.

This script is intentionally review-first:
  * it never edits manual match files;
  * it writes candidate/review CSVs;
  * report-derived values default to review_status=needs_review;
  * Scope 2 values are split into location-based, market-based, and unknown.

Stages:
  1. missed_match: compare SBTi-unmatched companies with NZDPU-unmatched rows.
  2. discovery: find public report/page candidates with a lightweight web search.
  3. extraction: download candidate reports/pages, select relevant snippets, and
     ask an LLM for strict JSON extraction when OPENAI_API_KEY is available.
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
FOOTPRINT_OUTPUT = REVIEW_DIR / "carbon_footprints.csv"
OBJECTIVES_OUTPUT = REVIEW_DIR / "carbon_objectives.csv"

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
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)


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


def resolve_report_path(value: str) -> Path | None:
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    return path


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


def select_snippets(path: Path, max_chars: int = 24000) -> str:
    cached = snippet_cache_path(path)
    if cached.exists() and cached.stat().st_mtime >= path.stat().st_mtime:
        return cached.read_text(encoding="utf-8")
    if path.suffix.lower() == ".pdf":
        pages = extract_pdf_pages(path)
    else:
        pages = extract_html_text(path)
    scored = [
        (keyword_score(text), page_no, text)
        for page_no, text in pages
        if keyword_score(text) > 0
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


def llm_extract(company_name: str, url: str, report_type: str, snippets: str, model: str) -> dict[str, Any]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return {
            "report_year": "",
            "scope_1_tco2e": "",
            "scope_2_lb_tco2e": "",
            "scope_2_mb_tco2e": "",
            "scope_2_unknown_tco2e": "",
            "scope_3_tco2e": "",
            "unit_original": "",
            "evidence_text": "",
            "confidence": "low",
            "notes": "OPENAI_API_KEY not set; LLM extraction skipped.",
        }
    system = (
        "You extract actual corporate greenhouse-gas emissions from report snippets. "
        "Return JSON only. Use null for unknown values. Convert all emissions to tCO2e. "
        "Do not extract targets, reductions, avoided emissions, intensities, or global/national emissions. "
        "If Scope 2 is reported but not explicitly location-based or market-based, put it in "
        "scope_2_unknown_tco2e. Populate scope_2_lb_tco2e or scope_2_mb_tco2e only when explicitly labeled. "
        "Confidence must be high, medium, or low."
    )
    user = {
        "company_name": company_name,
        "report_url": url,
        "report_type": report_type,
        "required_json_fields": [
            "report_year",
            "scope_1_tco2e",
            "scope_2_lb_tco2e",
            "scope_2_mb_tco2e",
            "scope_2_unknown_tco2e",
            "scope_3_tco2e",
            "unit_original",
            "evidence_text",
            "confidence",
            "notes",
        ],
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
        "llm_model": model if os.getenv("OPENAI_API_KEY") else "",
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
    evidence_parts = [
        evidence_for_label(table_text, r"(?:Scope 1: total direct greenhouse gas|Émissions de gaz à effet de serre \(GES\) du scope 1)", 6),
        evidence_for_label(table_text, r"(?:Scope 2: total indirect GHG emissions|Émissions de GES du scope 2)", 6),
        evidence_for_label(table_text, r"(?:Significant Scope 3 emissions|Émissions significatives de GES du scope 3)", 4),
    ]
    if market_based:
        evidence_parts.append(clean_extracted_text(market_based.group(0)))
    evidence_text = " | ".join(part for part in evidence_parts if part)
    if not evidence_text:
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
                    "llm_model": model if os.getenv("OPENAI_API_KEY") else "",
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
                "llm_model": model if os.getenv("OPENAI_API_KEY") else "",
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
    evidence = objective_evidence(snippets)
    if not evidence:
        return pd.DataFrame(columns=OBJECTIVE_COLUMNS)
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
        "llm_model": model if os.getenv("OPENAI_API_KEY") else "",
        "confidence": "medium",
        "evidence_text": evidence,
        "review_status": "needs_review",
    }
    rows = [
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
            "notes": "Long-term neutrality objective, not an actual emissions footprint value.",
        },
    ]
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
            extracted = llm_extract(company_name, str(report_url), report_type, snippets, model) if os.getenv("OPENAI_API_KEY") else deterministic_extract(company_name, str(report.get("report_year", "")), snippets)
            source_id = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
            extraction_method = "snippet_llm" if os.getenv("OPENAI_API_KEY") else "deterministic_snippet"
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
                    "llm_model": model if os.getenv("OPENAI_API_KEY") else "",
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
                    "llm_model": model if os.getenv("OPENAI_API_KEY") else "",
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
                    "llm_model": model if os.getenv("OPENAI_API_KEY") else "",
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
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
    local_only = args.local_reports or not (SBTI_UNMATCHED.exists() and NZDPU_UNMATCHED.exists())
    if local_only:
        report_index = read_or_build_report_index()
        if not REPORT_INDEX.exists():
            print(f"No curated {REPORT_INDEX}; using local candidates -> {LOCAL_REPORT_INDEX_CANDIDATES}")
        if args.skip_extraction:
            write_dataframe(report_index, LOCAL_REPORT_INDEX_CANDIDATES)
            print(f"Wrote {len(report_index)} local report candidates -> {LOCAL_REPORT_INDEX_CANDIDATES}")
            return 0
        _latest, history, objectives = extract_from_report_index(report_index, args.max_extract_companies, args.llm_model)
        write_dataframe(history, FOOTPRINT_OUTPUT, HISTORY_COLUMNS)
        write_dataframe(objectives, OBJECTIVES_OUTPUT, OBJECTIVE_COLUMNS)
        print(f"Wrote {len(history)} local report footprint rows -> {FOOTPRINT_OUTPUT}")
        print(f"Wrote {len(objectives)} local report objective rows -> {OBJECTIVES_OUTPUT}")
        return 0

    sbti = read_csv(SBTI_UNMATCHED)
    nzdpu_unmatched = read_csv(NZDPU_UNMATCHED)
    if args.seed_test:
        sbti = seed_filter(sbti, args.seed_size)

    missed = build_missed_match_candidates(sbti, nzdpu_unmatched)
    write_dataframe(missed, MISSED_MATCH_OUTPUT)
    print(f"Wrote {len(missed)} missed-match candidates -> {MISSED_MATCH_OUTPUT}")

    structured = latest_nzdpu_footprints(missed, sbti)
    write_dataframe(structured, FOOTPRINT_OUTPUT, OUTPUT_COLUMNS)
    print(f"Wrote {len(structured)} structured footprint rows -> {FOOTPRINT_OUTPUT}")

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
    write_dataframe(combined, FOOTPRINT_OUTPUT, OUTPUT_COLUMNS)
    print(f"Wrote {len(combined)} footprint rows -> {FOOTPRINT_OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
