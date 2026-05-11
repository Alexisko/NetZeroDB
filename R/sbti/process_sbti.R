# process_sbti.R
# Reads raw SBTi Excel files and produces a tidy targets dataset.
#
# Input:  inputs/sbti/targets-excel.xlsx   (primary — one row per target)
#         inputs/sbti/companies-excel.xlsx  (company metadata & commitment status)
# Output: data_prepared/sbti_targets.rds

library(readxl)
library(dplyr)
library(stringr)

# ── 1. Load raw files ─────────────────────────────────────────────────────────

raw_targets  <- read_excel("inputs/sbti/targets-excel.xlsx")
raw_companies <- read_excel("inputs/sbti/companies-excel.xlsx")

# ── 2. Clean helpers ──────────────────────────────────────────────────────────

# Convert literal "NA" strings to real NA
na_strings_to_na <- function(x) {
  if (is.character(x)) dplyr::na_if(x, "NA") else x
}

# Strip leading/trailing whitespace from company names.
# Note: some CIS companies have legitimate double-quotes in their official name
# (e.g. "Hytex Plastic" CJSC) — do NOT remove those.
clean_company_name <- function(x) str_trim(x)

# Parse year columns that mix "2032.0" and "FY2032" into integer years.
# Strips an optional "FY" prefix, then coerces to integer.
parse_year_col <- function(x) {
  x |>
    str_remove("^FY") |>
    as.numeric() |>
    as.integer()
}

# ── 3. Prepare commitment-status lookup (from targets file, action=Commitment) ─

commitment_status <- raw_targets |>
  filter(action == "Commitment") |>
  mutate(across(where(is.character), na_strings_to_na)) |>
  # Keep the "best" status per company: prefer Active/Target set over Removed
  mutate(
    status_rank = case_when(
      status == "Active"     ~ 1L,
      status == "Target set" ~ 2L,
      status == "Extended"   ~ 3L,
      status == "Removed"    ~ 4L,
      TRUE                   ~ 5L
    )
  ) |>
  arrange(sbti_id, status_rank) |>
  group_by(sbti_id) |>
  slice(1) |>
  ungroup() |>
  select(sbti_id, commitment_status = status)

# ── 4. Prepare company metadata lookup ────────────────────────────────────────

company_meta <- raw_companies |>
  mutate(
    across(where(is.character), na_strings_to_na),
    company_name = clean_company_name(company_name),
    across(
      c(near_term_target_year, long_term_target_year, net_zero_year),
      parse_year_col
    )
  ) |>
  select(
    sbti_id,
    near_term_status,
    near_term_target_classification,
    near_term_target_year,
    long_term_status,
    long_term_target_classification,
    long_term_target_year,
    net_zero_status,
    net_zero_year,
    ba15_status,
    date_updated
  )

# ── 5. Clean and tidy the target rows ─────────────────────────────────────────

sbti_targets <- raw_targets |>
  # Keep only actual target rows (not commitment header rows)
  filter(action == "Target") |>
  mutate(
    # Apply NA-string cleaning to all character columns
    across(where(is.character), na_strings_to_na),
    # Clean company name
    company_name = clean_company_name(company_name),
    # Convert numeric columns stored as character
    target_value = suppressWarnings(as.numeric(target_value)),
    base_year    = suppressWarnings(as.integer(as.numeric(base_year))),
    target_year  = suppressWarnings(as.integer(as.numeric(target_year))),
    # Standardise scope labels: "1+2", "1+2+3", "1.0" → "1", "2.0" → "2", "3.0" → "3"
    scope = case_when(
      scope == "1.0"   ~ "1",
      scope == "2.0"   ~ "2",
      scope == "3.0"   ~ "3",
      TRUE             ~ scope      # already "1+2", "1+2+3", "1+3", "2+3"
    ),
    # Source tag
    source = "SBTi"
  ) |>
  # Drop the action column (all rows are "Target" now)
  select(-action) |>
  # Join commitment status from commitment rows
  left_join(commitment_status, by = "sbti_id") |>
  # Join company-level metadata
  left_join(company_meta, by = "sbti_id") |>
  # Reorder columns for readability
  select(
    # Identifiers
    sbti_id, row_entry_id,
    # Company
    company_name, organization_type, location, region, sector,
    # Company-level status (from companies file)
    near_term_status, near_term_target_classification, near_term_target_year,
    long_term_status, long_term_target_classification, long_term_target_year,
    net_zero_status, net_zero_year,
    ba15_status,
    # This target's commitment status
    commitment_status,
    # Target details
    commitment_type, validation_route,
    scope, type, sub_type,
    target_classification_short,
    company_temperature_alignment,
    base_year, target_year,
    target_value,            # proportion 0–1 (e.g. 0.42 = 42% reduction)
    year_type,               # CY = calendar year, FY = fiscal year
    full_target_language,
    target_wording,
    isin, lei,
    date_published, date_updated,
    source
  )

# ── 6. Validate ───────────────────────────────────────────────────────────────

stopifnot(
  "No rows after processing" = nrow(sbti_targets) > 0,
  "target_year before base_year" = all(
    is.na(sbti_targets$base_year) | is.na(sbti_targets$target_year) |
      sbti_targets$target_year >= sbti_targets$base_year
  )
)

# Warn (don't stop) for target_value outliers — intensity targets may use
# different scales; only one row (AMD) exceeds 1, likely a data-entry error
outliers <- sbti_targets |>
  filter(!is.na(target_value), target_value > 1)
if (nrow(outliers) > 0) {
  warning(
    nrow(outliers), " row(s) have target_value > 1 (raw value preserved):\n",
    paste0("  ", outliers$company_name, " | ", outliers$type,
           " | value=", outliers$target_value, collapse = "\n")
  )
}

# ── 7. Save ───────────────────────────────────────────────────────────────────

saveRDS(sbti_targets, "data_prepared/sbti_targets.rds")

message(
  "Saved data_prepared/sbti_targets.rds\n",
  "  Rows: ", nrow(sbti_targets), "\n",
  "  Companies: ", length(unique(sbti_targets$sbti_id)), "\n",
  "  Columns: ", ncol(sbti_targets)
)
