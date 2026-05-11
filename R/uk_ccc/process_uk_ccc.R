# process_uk_ccc.R
# Reads the UK Climate Change Committee 7th Carbon Budget subsector data and
# produces a tidy emissions dataset.
#
# Input:  inputs/uk climate change committee/The-Seventh-Carbon-Budget-full-dataset.xlsx
#           sheet: "Subsector-level data"
# Output: data_prepared/uk_ccc_subsector.rds

library(readxl)
library(dplyr)

# ── 1. Load ───────────────────────────────────────────────────────────────────

raw <- read_excel(
  "inputs/uk climate change committee/The-Seventh-Carbon-Budget-full-dataset.xlsx",
  sheet = "Subsector-level data"
)

# ── 2. Filter & tidy ──────────────────────────────────────────────────────────

uk_ccc_subsector <- raw |>
  filter(variable == "Emissions: direct emissions total") |>
  select(scenario, country, sector, subsector, variable, variable_unit, year, value) |>
  mutate(source = "UK CCC 7CB")

# ── 3. Validate ───────────────────────────────────────────────────────────────

stopifnot(
  "No rows after filtering"                   = nrow(uk_ccc_subsector) > 0,
  "NA in scenario"                            = !anyNA(uk_ccc_subsector$scenario),
  "NA in sector"                              = !anyNA(uk_ccc_subsector$sector),
  "NA in subsector"                           = !anyNA(uk_ccc_subsector$subsector),
  "NA in year"                                = !anyNA(uk_ccc_subsector$year),
  "NA in value"                               = !anyNA(uk_ccc_subsector$value),
  "year range not 2025-2050"                  = all(uk_ccc_subsector$year %in% 2025:2050),
  "variable_unit not uniformly MtCO2e"        = all(uk_ccc_subsector$variable_unit == "MtCO2e"),
  "unexpected scenarios"                      = setequal(
    unique(uk_ccc_subsector$scenario),
    c("Balanced Pathway", "Baseline")
  )
)

# ── 4. Save ───────────────────────────────────────────────────────────────────

saveRDS(uk_ccc_subsector, "data_prepared/uk_ccc_subsector.rds")

message(
  "Saved data_prepared/uk_ccc_subsector.rds\n",
  "  Rows:       ", nrow(uk_ccc_subsector), "\n",
  "  Sectors:    ", length(unique(uk_ccc_subsector$sector)), "\n",
  "  Subsectors: ", length(unique(uk_ccc_subsector$subsector)), "\n",
  "  Year range: ", min(uk_ccc_subsector$year), "\u2013", max(uk_ccc_subsector$year)
)
