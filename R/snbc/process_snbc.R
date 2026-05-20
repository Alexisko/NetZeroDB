# process_snbc.R
# Reads the French SNBC (Stratégie Nationale Bas-Carbone) carbon budget data
# and produces a tidy emissions dataset aligned with the harmonized sector taxonomy.
#
# Input:  inputs/snbc/snbc_carbon_budget.csv  (semicolon-delimited, wide format)
# Output: shiny_app/data_prepared/snbc_emissions.rds
#
# Time representation: historical years used as-is; carbon budget periods
# represented by their midpoint year (2026, 2031, 2036) with a period_label
# column for hover text.

library(dplyr)
library(tidyr)
library(readr)

source("R/utils/sector_mapping.R")

# ── 1. Load ───────────────────────────────────────────────────────────────────

raw <- read_delim(
  "inputs/snbc/snbc_carbon_budget.csv",
  delim     = ";",
  col_types = cols(.default = col_character())
)

# ── 2. French → English sector names ─────────────────────────────────────────

# Mapping from original French names to English (as used in sector_mapping.R)
french_to_english <- c(
  "Transports"                    = "Transport",
  "B\u00e2timents"               = "Buildings",
  "Agriculture"                   = "Agriculture",
  "Industrie"                     = "Industry",
  "Production d\u2019\u00e9nergie" = "Energy",
  "Production d'énergie"          = "Energy",
  "D\u00e9chets"                  = "Waste"
)

# ── 3. Pivot to long format ───────────────────────────────────────────────────

# Column names in the raw file:
#   Secteur | 1990 | 2005 | 2023 |
#   3e budget carbone (2024\u20132028) | 4e budget carbone (2029\u20132033) | 5e budget carbone (2034\u20132038)

# Map budget period column names to midpoint years and labels
budget_period_map <- c(
  "1990"                               = "1990",
  "2005"                               = "2005",
  "2023"                               = "2023"
)

snbc_long <- raw |>
  # Drop the total row
  filter(!grepl("Total", Secteur, ignore.case = TRUE)) |>
  # Translate French sector names
  mutate(sector = recode(Secteur, !!!french_to_english)) |>
  # Drop any unmapped sectors (safety check)
  filter(sector %in% unique(sector_mapping$harmonized_sector)) |>
  select(-Secteur) |>
  # Pivot all year/budget columns to long
  pivot_longer(
    cols      = -sector,
    names_to  = "col_name",
    values_to = "value"
  ) |>
  mutate(value = as.numeric(value))

# ── 4. Assign years and period labels ─────────────────────────────────────────

snbc_emissions <- snbc_long |>
  mutate(
    year = case_when(
      col_name == "2023"  ~ 2023L,
      grepl("2024", col_name) ~ 2026L,   # midpoint of 2024-2028
      grepl("2029", col_name) ~ 2031L,   # midpoint of 2029-2033
      grepl("2034", col_name) ~ 2036L,   # midpoint of 2034-2038
      TRUE ~ NA_integer_
    ),
    period_label = case_when(
      col_name == "2023"  ~ "2023",
      grepl("2024", col_name) ~ "2024\u20132028",
      grepl("2029", col_name) ~ "2029\u20132033",
      grepl("2034", col_name) ~ "2034\u20132038",
      TRUE ~ NA_character_
    )
  ) |>
  filter(!is.na(year)) |>
  select(-col_name) |>
  mutate(
    source   = "SNBC",
    country  = "France",
    scenario = "SNBC",
    unit     = "MtCO2e"
  ) |>
  select(source, country, scenario, sector, year, value, unit, period_label)

# ── 5. Validate ───────────────────────────────────────────────────────────────

stopifnot(
  "No rows after processing"     = nrow(snbc_emissions) > 0,
  "NA in sector"                 = !anyNA(snbc_emissions$sector),
  "NA in year"                   = !anyNA(snbc_emissions$year),
  "NA in value"                  = !anyNA(snbc_emissions$value),
  "unexpected sectors"           = all(snbc_emissions$sector %in% harmonized_sectors),
  "expected 4 years per sector"  = all(
    snbc_emissions |>
      count(sector) |>
      pull(n) == 4
  )
)

# ── 6. Save ───────────────────────────────────────────────────────────────────

saveRDS(snbc_emissions, "shiny_app/data_prepared/snbc_emissions.rds")

message(
  "Saved shiny_app/data_prepared/snbc_emissions.rds\n",
  "  Rows:    ", nrow(snbc_emissions), "\n",
  "  Sectors: ", paste(sort(unique(snbc_emissions$sector)), collapse = ", "), "\n",
  "  Years:   ", paste(sort(unique(snbc_emissions$year)), collapse = ", ")
)
