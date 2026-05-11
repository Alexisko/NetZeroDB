# Carbon Footprint Gathering Pipeline

Run from the project root.

## 1. First local report experiment

Put downloaded public reports under `inputs/reports/`, or point to them from:

```text
inputs/report_index.csv
```

Then run:

```sh
python3 scripts/gather_carbon_footprints.py --local-reports
```

For the Air Liquide example currently indexed in `inputs/report_index.csv`, this writes:

- `outputs/review/carbon_footprints.csv`
- `outputs/review/carbon_objectives.csv`
- selected snippet cache files under `outputs/cache/carbon_footprints/snippets/`

If `OPENAI_API_KEY` is not set, the script uses a conservative deterministic snippet parser and keeps rows marked `needs_review`.

Targets and objectives are intentionally written to `carbon_objectives.csv`, not to footprint value columns.

## 2. Local missed-match pass only

```sh
python3 scripts/gather_carbon_footprints.py --skip-discovery
```

Writes:

- `outputs/review/sbti_nzdpu_missed_match_candidates.csv`
- `outputs/review/carbon_report_candidates.csv` with headers only
- `outputs/review/carbon_footprints.csv` with accepted exact NZDPU rows, if any

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
- Exact NZDPU matches can be marked `review_status = accepted`.
