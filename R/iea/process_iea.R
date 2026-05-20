# process_iea.R
# Reads IEA WEO 2025 Annex A free dataset (World + Regions) and produces a
# tidy sectoral CO2 emissions dataset aligned with the harmonized sector taxonomy.
#
# Inputs:  inputs/iea/WEO2025_AnnexA_Free_Dataset_World.csv
#          inputs/iea/WEO2025_AnnexA_Free_Dataset_Regions.csv
# Output:  shiny_app/data_prepared/iea_emissions.rds
#
# Filtering logic:
#   CATEGORY == "CO2 total"
#   FLOW     %in% c("Transport", "Buildings", "Industry", "Power sector inputs")
#   PRODUCT  == "Total"
# "Power sector inputs" is renamed to "Energy" to match the harmonized taxonomy.

library(dplyr)
library(readr)

source("R/utils/sector_mapping.R")

# ── 1. Load ───────────────────────────────────────────────────────────────────

world   <- read_csv("inputs/iea/WEO2025_AnnexA_Free_Dataset_World.csv",
                    show_col_types = FALSE)
regions <- read_csv("inputs/iea/WEO2025_AnnexA_Free_Dataset_Regions.csv",
                    show_col_types = FALSE)

raw <- bind_rows(world, regions)

# ── 2. Filter to sectoral CO2 emissions ───────────────────────────────────────

iea_flows <- c("Transport", "Buildings", "Industry", "Power sector inputs")

# Keep:
#   - Historical 2023: serves as the baseline anchor for the NZE trajectory
#   - NZE 2050 scenario from 2024 onwards
# Rows are then unified under a single "NZE 2050" scenario label so they
# plot as one connected trajectory starting at the 2023 observed level.

iea_filtered <- raw |>
  filter(
    REGION   == "World",
    CATEGORY == "CO2 total",
    FLOW     %in% iea_flows,
    PRODUCT  == "Total",
    (SCENARIO == "Historical"                              & YEAR == 2023) |
    (SCENARIO == "Net Zero Emissions by 2050 Scenario"    & YEAR >= 2024)
  )

# ── 3. Rename and harmonize ───────────────────────────────────────────────────

iea_emissions <- iea_filtered |>
  transmute(
    source   = "IEA WEO2025",
    scenario = "NZE 2050",
    sector   = if_else(FLOW == "Power sector inputs", "Energy", FLOW),
    year     = as.integer(YEAR),
    value    = VALUE,
    unit     = "Mt CO2"
  )

# ── 4. Deduplicate (World appears in both files) ──────────────────────────────

iea_emissions <- iea_emissions |>
  distinct()

# ── 5. Validate ───────────────────────────────────────────────────────────────

stopifnot(
  "No rows after filtering"   = nrow(iea_emissions) > 0,
  "NA in scenario"            = !anyNA(iea_emissions$scenario),
  "NA in sector"              = !anyNA(iea_emissions$sector),
  "NA in year"                = !anyNA(iea_emissions$year),
  "NA in value"               = !anyNA(iea_emissions$value),
  "unexpected sectors"        = all(
    iea_emissions$sector %in% c("Transport", "Buildings", "Industry", "Energy")
  ),
  "unexpected scenarios"      = all(iea_emissions$scenario == "NZE 2050")
)

# ── 6. Save ───────────────────────────────────────────────────────────────────

saveRDS(iea_emissions, "shiny_app/data_prepared/iea_emissions.rds")

message(
  "Saved shiny_app/data_prepared/iea_emissions.rds\n",
  "  Rows:      ", nrow(iea_emissions), "\n",
  "  Sectors:   ", paste(sort(unique(iea_emissions$sector)), collapse = ", "), "\n",
  "  Scenarios: ", paste(sort(unique(iea_emissions$scenario)), collapse = ", "), "\n",
  "  Years:     ", paste(sort(unique(iea_emissions$year)), collapse = ", ")
)
