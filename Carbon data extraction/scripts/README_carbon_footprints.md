# Carbon Footprint Gathering Pipeline

Run from the project root.

## 1. Staged local report ingestion

Put downloaded public reports under `inputs/reports/`, or point to them from:

```text
inputs/report_index.csv
```

Run the full local ingestion pipeline:

```sh
python3 scripts/gather_carbon_footprints.py --stage all
```

This writes:

- `outputs/cache/report_ingestion/report_manifest.csv`
- page-level text caches under `outputs/cache/report_ingestion/text/`
- compact snippet caches under `outputs/cache/report_ingestion/snippets/`
- per-report extraction audit JSON under `outputs/cache/report_ingestion/extractions/`
- latest machine-generated footprint rows in `outputs/review/carbon_footprints_generated.csv`
- human-reviewed footprint rows in `outputs/review/carbon_footprints.csv`
- `outputs/review/carbon_objectives.csv`
- `outputs/review/carbon_extraction_comparison.csv` when `OPENAI_API_KEY` is available

If `OPENAI_API_KEY` is not set, the script uses a conservative deterministic snippet parser and keeps rows marked `needs_review`.
It still writes extraction audit JSON with the deterministic fallback reason.
The script loads `.env` from the project root when present, without printing secrets or overriding already exported environment variables.

Targets and objectives are intentionally written to `carbon_objectives.csv`, not to footprint value columns.

Each extraction audit JSON records report metadata, the model or deterministic method used, the prompt input snippet IDs/pages and snippet cache path, the parsed JSON, the raw response when present, and any fallback reason. LLM and deterministic fallback artifacts use separate filenames so a no-key smoke run does not overwrite a prior LLM parse.

Individual stages are available for debugging and cheap reruns:

```sh
python3 scripts/gather_carbon_footprints.py --stage manifest
python3 scripts/gather_carbon_footprints.py --stage text
python3 scripts/gather_carbon_footprints.py --stage snippets
python3 scripts/gather_carbon_footprints.py --stage extract
python3 scripts/gather_carbon_footprints.py --stage compare
python3 scripts/gather_carbon_footprints.py --stage review
python3 scripts/gather_carbon_footprints.py --stage validate
```

The extract/all stages rewrite `carbon_footprints_generated.csv`, then initialize `carbon_footprints.csv` only when the reviewed file does not already exist.
Manual edits belong in `carbon_footprints.csv`; reruns leave that file unchanged so accepted rows and notes are not lost.
Use `--stage review` after an extraction rerun to create the reviewed CSV if it is missing.

The compare stage does not rewrite either footprint CSV. It saves raw LLM responses and compares each LLM single-year extraction with the closest deterministic regression row.
The comparison file keeps both the literal LLM extraction (`llm_raw_value`, `llm_raw_unit`) and the deterministic Python normalization (`llm_value_tco2e`, `normalization_status`).
It also records year alignment fields (`llm_emissions_year`, `normalized_emissions_year`, `year_alignment_status`, `year_alignment_method`) so table-position corrections and year-review flags are visible.

`--local-reports` also runs the staged local pipeline for backward compatibility.

Run the no-key smoke fixture test:

```sh
python3 scripts/test_report_ingestion_smoke.py
```

## 2. Local missed-match pass only

```sh
python3 scripts/gather_carbon_footprints.py --skip-discovery
```

Writes:

- `outputs/review/sbti_nzdpu_missed_match_candidates.csv`
- `outputs/review/carbon_report_candidates.csv` with headers only
- `outputs/review/carbon_footprints_generated.csv` with accepted exact NZDPU rows, if any
- `outputs/review/carbon_footprints.csv` only if the reviewed CSV does not already exist

## 3. Report discovery without LLM extraction

```sh
python3 scripts/gather_carbon_footprints.py --skip-extraction
```

Writes public report/page candidates to:

- `outputs/review/carbon_report_candidates.csv`

Discovery works best with one of these optional search API keys:

```sh
BRAVE_SEARCH_API_KEY=... python3 scripts/gather_carbon_footprints.py --skip-extraction
BING_SEARCH_API_KEY=... python3 scripts/gather_carbon_footprints.py --skip-extraction
```

If no search API key is set, the script falls back to public HTML search pages, which may be blocked or rate-limited.

## 4. LLM-assisted extraction

```sh
OPENAI_API_KEY=... python3 scripts/gather_carbon_footprints.py
```

Optional model override:

```sh
CARBON_LLM_MODEL=gpt-5.4-mini OPENAI_API_KEY=... python3 scripts/gather_carbon_footprints.py
```

For a cheap smoke test:

```sh
OPENAI_API_KEY=... python3 scripts/gather_carbon_footprints.py --seed-test --max-extract-companies 3
```

To run extraction from an already reviewed/edited candidate CSV:

```sh
OPENAI_API_KEY=... python3 scripts/gather_carbon_footprints.py --use-existing-candidates
```

## Output Rules

- All emissions are stored in `tCO2e`.
- Scope 2 is split into:
  - `scope_2_lb_tco2e`
  - `scope_2_mb_tco2e`
  - `scope_2_unknown_tco2e`
- If a report gives Scope 2 but does not explicitly say location-based or market-based, the value belongs in `scope_2_unknown_tco2e`.
- Report-derived values default to `review_status = needs_review`.
- Manually reviewed report-derived rows can be marked `review_status = accepted`.
- Exact NZDPU matches can be marked `review_status = accepted`.
