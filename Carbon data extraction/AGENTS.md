# Carbon Data Extraction — Codex Instructions

## Project Goal

Build a focused, reproducible pipeline to extract corporate carbon footprint data from public company reports.

The project should help users:

* collect annual reports, sustainability reports, URDs, CSR reports, and climate pages
* extract reported greenhouse gas emissions values
* distinguish Scope 1, Scope 2 location-based, Scope 2 market-based, Scope 2 unknown, and Scope 3
* preserve evidence text and source metadata for human review
* produce clean review CSVs that can later be imported into the broader NetZero DB project

This subproject is separate from the Shiny app and trajectory-analysis code.

---

## Core Principles

1. **Review-first extraction**

   * Do not treat report-derived values as final without review.
   * Every extracted value must keep source, year, evidence text, confidence, and review status.
   * Ambiguous values should be left blank or marked low confidence.

2. **Public reports only**

   * Prefer official company or parent-company sources.
   * Reports may be downloaded manually by the user.
   * Automated discovery is optional and should not be required for the main workflow.

3. **Preserve raw evidence**

   * Keep report URLs, local file paths, page/snippet evidence, original units, and notes.
   * Do not overwrite original report files.
   * Do not silently infer missing values.

4. **Conservative Scope 2 handling**

   * Use `scope_2_lb_tco2e` only when explicitly location-based.
   * Use `scope_2_mb_tco2e` only when explicitly market-based.
   * Use `scope_2_unknown_tco2e` when Scope 2 is reported but the method is unclear.

---

## Preferred Project Structure

```text
Carbon data extraction/
├── AGENTS.md
├── inputs/
│   ├── reports/
│   └── report_index.csv
├── outputs/
│   ├── review/
│   └── cache/
├── scripts/
│   ├── gather_carbon_footprints.py
│   └── README_carbon_footprints.md
└── README.md
```

---

## Input Rules

The preferred main input is a manually curated report index:

```text
inputs/report_index.csv
```

Recommended columns:

* `company_name`
* `sbti_id`
* `lei`
* `report_path`
* `report_url`
* `report_type`
* `report_year`

Report files should live under:

```text
inputs/reports/
```

Use stable, readable filenames when possible, for example:

```text
40011936_COLAS_SA_2024.pdf
```

---

## Output Rules

The main reviewed output should be:

```text
outputs/review/sbti_carbon_footprints.csv
```

Required columns:

* `company_name`
* `sbti_id`
* `lei`
* `source`
* `source_id`
* `report_url`
* `report_type`
* `report_year`
* `scope_1_tco2e`
* `scope_2_lb_tco2e`
* `scope_2_mb_tco2e`
* `scope_2_unknown_tco2e`
* `scope_3_tco2e`
* `extraction_method`
* `llm_model`
* `confidence`
* `evidence_text`
* `review_status`
* `notes`

All emissions values must be standardized to `tCO2e`.

---

## Extraction Rules

* Use Python for PDF/text extraction and LLM orchestration.
* Use deterministic text extraction to select relevant snippets before calling an LLM.
* Send snippets to the LLM, not whole reports, unless the report is very small.
* The LLM must return strict structured data.
* Never extract emissions reduction targets, avoided emissions, intensity metrics, or pathway values as actual footprint values.
* Prefer actual reported emissions for a specific reporting year.
* If the company boundary is unclear, record that in `notes` and lower confidence.

---

## Confidence Rules

Use:

* `high`: official source, explicit reporting year, explicit scope label, explicit unit
* `medium`: likely correct but table/entity/year context needs review
* `low`: ambiguous value, unclear boundary, unclear year, or possible target/intensity confusion

Report-derived rows should default to:

```text
review_status = needs_review
```

Only manually reviewed rows should become:

```text
review_status = accepted
```

---

## Coding Guidelines

* Keep scripts runnable from the `Carbon data extraction/` project root.
* Prefer simple Python scripts over notebooks for reusable pipeline steps.
* Keep paths configurable through CLI arguments where practical.
* Cache downloaded or processed files under `outputs/cache/`.
* Do not commit API keys, downloaded proprietary files, or secrets.
* Do not mutate the parent NetZero DB project unless explicitly requested.

---

## Validation

Before considering extraction complete:

* Confirm output CSVs can be read by R and Python.
* Confirm all numeric emissions columns are in `tCO2e`.
* Confirm every non-empty emissions value has evidence text.
* Confirm unlabeled Scope 2 values are stored in `scope_2_unknown_tco2e`.
* Confirm report-derived rows are marked `needs_review` unless manually accepted.
