#!/usr/bin/env python3
"""Smoke-test the local report ingestion pipeline without an LLM key."""

from __future__ import annotations

import os
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "gather_carbon_footprints.py"
OUTPUT = ROOT / "outputs" / "review" / "carbon_footprints.csv"
GENERATED_OUTPUT = ROOT / "outputs" / "review" / "carbon_footprints_generated.csv"
OBJECTIVES_OUTPUT = ROOT / "outputs" / "review" / "carbon_objectives.csv"
COMPARISON_OUTPUT = ROOT / "outputs" / "review" / "carbon_extraction_comparison.csv"
EXTRACTION_DIR = ROOT / "outputs" / "cache" / "report_ingestion" / "extractions"
REPORT_INDEX = ROOT / "inputs" / "report_index.csv"


def main() -> int:
    env = os.environ.copy()
    env.pop("OPENAI_API_KEY", None)
    env["CARBON_SKIP_DOTENV"] = "1"
    originals = {
        path: path.read_bytes()
        for path in (OUTPUT, GENERATED_OUTPUT, OBJECTIVES_OUTPUT, COMPARISON_OUTPUT)
        if path.exists()
    }
    original_audits = {
        path: path.read_bytes()
        for path in EXTRACTION_DIR.glob("*.json")
    } if EXTRACTION_DIR.exists() else {}
    try:
        subprocess.run([sys.executable, str(SCRIPT), "--stage", "all"], cwd=ROOT, env=env, check=True)

        report_index = pd.read_csv(REPORT_INDEX, dtype=str, keep_default_na=False)
        expected_companies = set(report_index["company_name"].dropna())
        df = pd.read_csv(GENERATED_OUTPUT, dtype=str, keep_default_na=False)
        companies = set(df["company_name"])
        missing = expected_companies - companies
        if missing:
            raise AssertionError(f"Missing expected companies: {sorted(missing)}")
        if len(df) < len(expected_companies):
            raise AssertionError(
                f"Expected at least one footprint row per indexed company; found {len(df)} rows for {len(expected_companies)} companies"
            )
        if set(df["review_status"]) != {"needs_review"}:
            raise AssertionError("Report-derived rows must remain needs_review")
        if set(df["extraction_method"]) != {"deterministic_snippet"}:
            raise AssertionError("No-key smoke test should use deterministic snippets only")
        objectives = pd.read_csv(OBJECTIVES_OUTPUT, dtype=str, keep_default_na=False)
        if objectives.empty:
            raise AssertionError("Expected objective rows from known target tables")
        accor_targets = objectives[
            (objectives["company_name"].eq("Accor"))
            & (objectives["target_year"].eq("2030"))
            & (objectives["target_scope"].eq("sbti_scopes_1_and_2"))
            & (objectives["target_value"].eq("1776000"))
        ]
        if accor_targets.empty:
            raise AssertionError("Accor 2030 SBTi Scopes 1+2 objective was not parsed from the emissions table")
        airbus_targets = objectives[
            (objectives["company_name"].eq("Airbus"))
            & (objectives["target_year"].eq("2035"))
            & (objectives["target_metric"].eq("emissions_intensity"))
            & (objectives["target_value"].eq("48.0"))
        ]
        if airbus_targets.empty:
            raise AssertionError("Airbus Scope 3 intensity objective was not parsed from the target table")
        if set(objectives["review_status"]) != {"needs_review"}:
            raise AssertionError("Report-derived objective rows must remain needs_review")
        value_cols = [
            "scope_1_tco2e",
            "scope_2_lb_tco2e",
            "scope_2_mb_tco2e",
            "scope_2_unknown_tco2e",
            "scope_3_tco2e",
            "scope_1_2_total_tco2e",
        ]
        has_value = df[value_cols].ne("").any(axis=1)
        if (has_value & df["evidence_text"].eq("")).any():
            raise AssertionError("Every row with emissions values must keep evidence_text")
        audit_paths = sorted(EXTRACTION_DIR.glob("*_deterministic_snippet_raw-units-v2.json"))
        if not audit_paths:
            raise AssertionError("No-key extraction should write deterministic per-report audit JSON")
        audit = json.loads(audit_paths[0].read_text(encoding="utf-8"))
        if audit.get("model") != "deterministic_snippet":
            raise AssertionError("Audit JSON should record the deterministic fallback model")
        if "OPENAI_API_KEY not set" not in audit.get("fallback_reason", ""):
            raise AssertionError("Audit JSON should record why deterministic fallback was used")
        if not isinstance(audit.get("parsed_json"), dict):
            raise AssertionError("Audit JSON should include parsed_json")
        prompt_input = audit.get("prompt_input", {})
        if not prompt_input.get("snippet_ids") or not prompt_input.get("snippet_pages"):
            raise AssertionError("Audit JSON should include prompt input snippet IDs and pages")
        if OUTPUT in originals and OUTPUT.read_bytes() != originals[OUTPUT]:
            raise AssertionError("Existing reviewed footprint CSV should not be overwritten by reruns")
    finally:
        for path in (OUTPUT, GENERATED_OUTPUT, OBJECTIVES_OUTPUT, COMPARISON_OUTPUT):
            if path.exists() and path not in originals:
                path.unlink()
        for path, content in originals.items():
            path.write_bytes(content)
        if EXTRACTION_DIR.exists():
            for path in EXTRACTION_DIR.glob("*.json"):
                if path not in original_audits:
                    path.unlink()
            for path, content in original_audits.items():
                path.write_bytes(content)
    print("Smoke test passed: local no-key ingestion produced review-ready footprint rows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
