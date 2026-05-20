# sbti_nzdpu_match.R
# Joins SBTi targets (what French companies promised) with NZDPU emissions
# (what they actually emitted) to assess whether companies are on track.
#
# Input:  shiny_app/data_prepared/sbti_targets.rds
#         shiny_app/data_prepared/nzdpu_french_companies.rds
#         inputs/manual_matches.csv          (optional — user-curated pairs)
# Output: shiny_app/data_prepared/sbti_nzdpu_matched.rds
#         outputs/review/sbti_unmatched_corporate.csv
#         outputs/review/nzdpu_unmatched.csv
#
# One row per SBTi target × reporting_year.
# on_track: TRUE = actual emissions ≤ linear target path at that year.

library(dplyr)
library(tidyr)
library(stringr)

# ── 1. Load ───────────────────────────────────────────────────────────────────

sbti_raw  <- readRDS("shiny_app/data_prepared/sbti_targets.rds")
nzdpu_raw <- readRDS("shiny_app/data_prepared/nzdpu_french_companies.rds")


# ── 2. Filter SBTi: French, Corporate, Absolute targets ──────────────────────
# • Corporate only (excludes SME and Financial Institution)
# • Absolute only (Intensity targets can't be compared to raw NZDPU values)

sbti_fr <- sbti_raw |>
  filter(
    location          == "France",
    organization_type == "Corporate",
    type              == "Absolute"
  ) |>
  select(
    sbti_id, company_name, lei, sector, scope,
    base_year, target_year, target_value,
    target_classification_short, company_temperature_alignment, commitment_status
  )

cat(sprintf(
  "SBTi French Corporate (Absolute): %d targets across %d companies\n",
  nrow(sbti_fr), n_distinct(sbti_fr$sbti_id)
))


# ── 3. Build NZDPU scoped emissions (long format) ────────────────────────────
# Each SBTi scope maps to one or more NZDPU columns summed together.
# Scope 2 uses location-based (lb) as the default.

scope_map <- list(
  "1"     = c("scope1_tco2e"),
  "2"     = c("scope2_lb_tco2e"),
  "3"     = c("scope3_total_tco2e"),
  "1+2"   = c("scope1_tco2e", "scope2_lb_tco2e"),
  "1+2+3" = c("scope1_tco2e", "scope2_lb_tco2e", "scope3_total_tco2e"),
  "1+3"   = c("scope1_tco2e", "scope3_total_tco2e"),
  "2+3"   = c("scope2_lb_tco2e", "scope3_total_tco2e")
)

sbti_scopes <- unique(sbti_fr$scope)

nzdpu_scoped <- lapply(intersect(sbti_scopes, names(scope_map)), function(sc) {
  cols_needed <- scope_map[[sc]]
  cols_avail  <- intersect(cols_needed, names(nzdpu_raw))
  if (length(cols_avail) == 0) return(NULL)
  nzdpu_raw |>
    mutate(
      scope           = sc,
      emissions_tco2e = rowSums(across(all_of(cols_avail)), na.rm = FALSE)
    ) |>
    select(nz_id, company_name, lei, reporting_year, sics_sector,
           scope, emissions_tco2e)
}) |>
  bind_rows()


# ── 4. Match companies ────────────────────────────────────────────────────────

normalize_name <- function(x) {
  x |>
    str_to_lower() |>
    str_trim() |>
    str_remove_all("[^a-z0-9 ]") |>
    str_squish()
}

sbti_companies  <- sbti_fr   |> distinct(sbti_id, company_name, lei) |>
  mutate(name_norm = normalize_name(company_name))
nzdpu_companies <- nzdpu_raw |> distinct(nz_id, company_name, lei) |>
  mutate(name_norm = normalize_name(company_name))

# Primary: exact LEI match
lei_pairs <- sbti_companies |>
  filter(!is.na(lei), lei != "") |>
  inner_join(
    nzdpu_companies |> filter(!is.na(lei), lei != "") |> select(nz_id, lei),
    by = "lei"
  ) |>
  select(sbti_id, nz_id) |>
  mutate(match_method = "lei")

# Secondary: normalised exact name
name_pairs <- sbti_companies |>
  filter(!sbti_id %in% lei_pairs$sbti_id) |>
  inner_join(
    nzdpu_companies |>
      filter(!nz_id %in% lei_pairs$nz_id) |>
      select(nz_id, name_norm),
    by = "name_norm"
  ) |>
  select(sbti_id, nz_id) |>
  mutate(match_method = "name")

# Tertiary: user-curated manual matches (inputs/manual_matches.csv)
# File format: sbti_id, nzdpu_nz_id  (one pair per row, header required)
manual_pairs <- if (file.exists("inputs/manual_matches.csv")) {
  raw_manual <- read.csv("inputs/manual_matches.csv", stringsAsFactors = FALSE) |>
    rename(nz_id = nzdpu_nz_id) |>
    mutate(match_method = "manual") |>
    # Only keep pairs not already matched automatically
    filter(
      !sbti_id %in% c(lei_pairs$sbti_id, name_pairs$sbti_id),
      !nz_id   %in% c(lei_pairs$nz_id,   name_pairs$nz_id)
    )
  cat(sprintf("  Loaded %d manual match(es) from inputs/manual_matches.csv\n",
              nrow(raw_manual)))
  raw_manual
} else {
  data.frame(sbti_id = numeric(), nz_id = integer(), match_method = character())
}

company_pairs <- bind_rows(lei_pairs, name_pairs, manual_pairs) |>
  distinct(sbti_id, nz_id, .keep_all = TRUE)

cat(sprintf(
  "Matched: %d companies  (LEI: %d | name: %d | manual: %d)\n",
  n_distinct(company_pairs$sbti_id),
  n_distinct(lei_pairs$sbti_id),
  n_distinct(name_pairs$sbti_id),
  n_distinct(manual_pairs$sbti_id)
))


# ── 5. Join targets → company pairs → NZDPU emissions ────────────────────────

matched_long <- company_pairs |>
  inner_join(sbti_fr, by = "sbti_id", relationship = "many-to-many") |>
  inner_join(
    nzdpu_scoped |> select(nz_id, scope, reporting_year, emissions_tco2e, sics_sector),
    by = c("nz_id", "scope"),
    relationship = "many-to-many"
  )


# ── 6. Normalise to base_year and compute on-track status ────────────────────

base_emissions <- matched_long |>
  filter(reporting_year == base_year, !is.na(emissions_tco2e)) |>
  select(sbti_id, nz_id, scope, base_emissions = emissions_tco2e) |>
  distinct()

matched_indexed <- matched_long |>
  left_join(base_emissions, by = c("sbti_id", "nz_id", "scope")) |>
  mutate(
    emissions_index    = (emissions_tco2e / base_emissions) * 100,
    target_final_index = (1 - target_value) * 100,
    target_index = case_when(
      is.na(base_emissions)         ~ NA_real_,
      reporting_year <= base_year   ~ 100,
      reporting_year >= target_year ~ target_final_index,
      TRUE ~ 100 - (100 - target_final_index) *
        (reporting_year - base_year) / (target_year - base_year)
    ),
    on_track = case_when(
      is.na(emissions_index) | is.na(target_index) ~ NA,
      emissions_index <= target_index              ~ TRUE,
      TRUE                                         ~ FALSE
    )
  ) |>
  select(
    sbti_id, nz_id, company_name, lei, match_method,
    sector, sics_sector, scope,
    target_classification_short, company_temperature_alignment, commitment_status,
    base_year, target_year, target_value,
    reporting_year,
    emissions_tco2e, base_emissions,
    emissions_index, target_index,
    on_track
  )


# ── 7. Diagnostics ────────────────────────────────────────────────────────────

n_sbti_fr   <- n_distinct(sbti_fr$sbti_id)
n_nzdpu_fr  <- n_distinct(nzdpu_raw$nz_id)
n_matched   <- n_distinct(matched_indexed$nz_id)
n_sbti_only <- n_sbti_fr - n_distinct(matched_indexed$sbti_id)
n_nzdpu_only <- n_nzdpu_fr - n_distinct(matched_indexed$nz_id)

cat(sprintf("
── Coverage ──────────────────────────────────────────
  SBTi French Corporate: %d companies
  NZDPU French:          %d companies
  Matched (in both):     %d
  SBTi only (no NZDPU): %d
  NZDPU only (no SBTi): %d

",
  n_sbti_fr, n_nzdpu_fr, n_matched, n_sbti_only, n_nzdpu_only
))

most_recent_per_target <- matched_indexed |>
  group_by(sbti_id, nz_id, scope) |>
  slice_max(reporting_year, n = 1, with_ties = FALSE) |>
  ungroup()

cat("── On-track status (most recent year per target) ────\n")
most_recent_per_target |>
  filter(!is.na(on_track)) |>
  count(scope, on_track) |>
  mutate(pct = round(n / sum(n) * 100, 1), .by = scope) |>
  arrange(scope, on_track) |>
  print()


# ── 8. Review CSVs for unmatched companies ────────────────────────────────────

dir.create("outputs/review", recursive = TRUE, showWarnings = FALSE)

matched_sbti_ids  <- unique(matched_indexed$sbti_id)
matched_nzdpu_ids <- unique(matched_indexed$nz_id)

# Unmatched SBTi corporate companies — one row per company, key fields for review
sbti_unmatched <- sbti_fr |>
  filter(!sbti_id %in% matched_sbti_ids) |>
  group_by(sbti_id) |>
  summarise(
    company_name       = first(company_name),
    lei                = first(na.omit(c(lei, NA_character_))),
    sbti_sector        = first(sector),
    commitment_status  = first(commitment_status),
    n_targets          = n(),
    scopes             = paste(sort(unique(scope)), collapse = ", "),
    target_years       = paste(sort(unique(target_year)), collapse = ", "),
    .groups = "drop"
  ) |>
  mutate(nzdpu_nz_id = NA_character_) |>   # ← fill this in to record a match
  arrange(company_name) |>
  select(company_name, sbti_id, lei, sbti_sector, commitment_status,
         n_targets, scopes, target_years, nzdpu_nz_id)

# Unmatched NZDPU companies — one row per company
nzdpu_unmatched <- nzdpu_raw |>
  filter(!nz_id %in% matched_nzdpu_ids) |>
  group_by(nz_id) |>
  summarise(
    company_name    = first(company_name),
    lei             = first(na.omit(c(lei, NA_character_))),
    sics_sector     = first(sics_sector),
    years_available = paste(sort(unique(reporting_year)), collapse = ", "),
    n_years         = n_distinct(reporting_year),
    has_scope1      = any(!is.na(scope1_tco2e)),
    has_scope3      = any(!is.na(scope3_total_tco2e)),
    .groups = "drop"
  ) |>
  arrange(company_name) |>
  select(company_name, nz_id, lei, sics_sector, years_available,
         n_years, has_scope1, has_scope3)

write.csv(sbti_unmatched, "outputs/review/sbti_unmatched_corporate.csv",
          row.names = FALSE, na = "")
write.csv(nzdpu_unmatched, "outputs/review/nzdpu_unmatched.csv",
          row.names = FALSE, na = "")

# Manual matches template (only written if file doesn't exist yet)
template_path <- "inputs/manual_matches.csv"
if (!file.exists(template_path)) {
  write.csv(
    data.frame(sbti_id = character(), nzdpu_nz_id = character()),
    template_path, row.names = FALSE
  )
  cat(sprintf("  Created empty template: %s\n", template_path))
}

cat(sprintf("
── Unmatched companies saved for review ──────────────
  outputs/review/sbti_unmatched_corporate.csv   (%d companies)
  outputs/review/nzdpu_unmatched.csv            (%d companies)

  To record manual matches:
    1. Open both CSVs side-by-side
    2. Fill 'nzdpu_nz_id' in the SBTi file when you spot a match
    3. Copy sbti_id + nzdpu_nz_id pairs into inputs/manual_matches.csv
    4. Re-run this script

",
  nrow(sbti_unmatched), nrow(nzdpu_unmatched)
))


# ── 9. Save matched dataset ───────────────────────────────────────────────────

saveRDS(matched_indexed, "shiny_app/data_prepared/sbti_nzdpu_matched.rds")

message(sprintf(
  "Saved shiny_app/data_prepared/sbti_nzdpu_matched.rds  |  %d rows × %d cols",
  nrow(matched_indexed), ncol(matched_indexed)
))
