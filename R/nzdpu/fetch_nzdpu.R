# fetch_nzdpu.R
# Downloads GHG footprint data for French companies from the NZDPU API.
#
# Input:  .env (NZDPU API key)
# Output: shiny_app/data_prepared/nzdpu_french_companies.rds
#
# One row per company × reporting year × data provider.
# Emissions are in metric tons CO2e (tCO2e) as reported by disclosing companies.
# "—" (not disclosed) values are converted to NA.

library(httr2)
library(jsonlite)
library(dplyr)
library(purrr)

readRenviron(".env")
api_key  <- Sys.getenv("NZDPU")
BASE_URL <- "https://climatedatautility.org/wis"

stopifnot("NZDPU API key not found in .env" = nchar(api_key) > 0)


# ── Helpers ───────────────────────────────────────────────────────────────────

nzdpu_get <- function(path, ...) {
  resp <- request(paste0(BASE_URL, path)) |>
    req_headers("x-api-key" = api_key) |>
    req_url_query(...) |>
    req_perform()
  resp_body_json(resp, simplifyVector = TRUE)
}

nzdpu_search_page <- function(fields, start = 0, limit = 100) {
  body <- list(
    meta = list(
      jurisdiction    = c("France"),
      reporting_year  = character(0),
      sics_sector     = character(0),
      sics_sub_sector = character(0),
      sics_industry   = character(0),
      company_name    = character(0),
      data_provider   = character(0)
    ),
    fields = as.character(fields)
  )
  resp <- request(paste0(BASE_URL, "/search")) |>
    req_headers("x-api-key" = api_key, "Content-Type" = "application/json") |>
    req_url_query(limit = limit, start = start) |>
    req_body_raw(toJSON(body, auto_unbox = FALSE), type = "application/json") |>
    req_perform()
  resp_body_json(resp, simplifyVector = TRUE)
}

EMISSION_PATTERN <- "^total_s[123]"

normalize_page <- function(items) {
  items |>
    mutate(across(where(is.character), ~ ifelse(.x == "—", NA_character_, .x))) |>
    mutate(across(matches(EMISSION_PATTERN), ~ suppressWarnings(as.numeric(.x))))
}

paginate <- function(fetch_page_fn, limit = 100) {
  first  <- fetch_page_fn(start = 0)
  total  <- first$total_disclosures %||% first$total
  pages  <- ceiling(total / limit)
  starts <- seq(0, by = limit, length.out = pages)
  results <- vector("list", pages)
  results[[1]] <- normalize_page(as.data.frame(first$items))
  if (pages > 1) {
    for (i in seq(2, pages)) {
      cat(sprintf("  page %d/%d (start=%d)\n", i, pages, starts[i]))
      page <- fetch_page_fn(start = starts[i])
      results[[i]] <- normalize_page(as.data.frame(page$items))
    }
  }
  bind_rows(results)
}


# ── 1. Company metadata ───────────────────────────────────────────────────────

cat("Fetching French company metadata...\n")

companies_raw <- paginate(
  function(start) nzdpu_get(
    "/coverage/companies",
    jurisdiction = "France",
    limit = 100,
    start = start
  ),
  limit = 100
)

companies_df <- companies_raw |>
  select(nz_id, company_name, lei, lei_source, jurisdiction,
         sics_sector, sics_sub_sector, sics_industry) |>
  rename(sics_sub_sector_meta = sics_sub_sector,
         sics_industry_meta   = sics_industry)

cat(sprintf("  %d companies retrieved\n", nrow(companies_df)))


# ── 2. Emissions disclosures ──────────────────────────────────────────────────

SEARCH_FIELDS <- c(
  "company_name", "legal_entity_identifier", "nz_id",
  "jurisdiction", "sics_sector", "sics_sub_sector", "sics_industry",
  "reporting_year",
  "total_s1_emissions_ghg",
  "total_s2_lb_emissions_ghg",
  "total_s2_mb_emissions_ghg",
  "total_s3_emissions_ghg",
  paste0("total_s3_ghgp_c", 1:15, "_emissions_ghg")
)

cat("Fetching emissions disclosures (paginated)...\n")

emissions_raw <- paginate(
  function(start) nzdpu_search_page(SEARCH_FIELDS, start = start, limit = 100),
  limit = 100
)

cat(sprintf("  %d disclosure records retrieved\n", nrow(emissions_raw)))


# ── 3. Clean & join ───────────────────────────────────────────────────────────

EMISSION_COLS <- c(
  "total_s1_emissions_ghg",
  "total_s2_lb_emissions_ghg",
  "total_s2_mb_emissions_ghg",
  "total_s3_emissions_ghg",
  paste0("total_s3_ghgp_c", 1:15, "_emissions_ghg")
)

emissions_clean <- emissions_raw |>
  mutate(across(all_of(EMISSION_COLS), as.numeric)) |>
  rename(lei_search = legal_entity_identifier)

nzdpu_df <- emissions_clean |>
  left_join(
    companies_df |> select(nz_id, lei, lei_source,
                           sics_sub_sector_meta, sics_industry_meta),
    by = "nz_id"
  ) |>
  rename(
    scope1_tco2e       = total_s1_emissions_ghg,
    scope2_lb_tco2e    = total_s2_lb_emissions_ghg,
    scope2_mb_tco2e    = total_s2_mb_emissions_ghg,
    scope3_total_tco2e = total_s3_emissions_ghg
  ) |>
  rename_with(
    ~ gsub("total_s3_ghgp_c(\\d+)_emissions_ghg", "scope3_c\\1_tco2e", .x),
    starts_with("total_s3_ghgp_c")
  ) |>
  mutate(source = "NZDPU", country = "France") |>
  select(
    nz_id, company_name, lei, lei_source, lei_search,
    reporting_year, data_provider, source, country,
    jurisdiction, sics_sector, sics_sub_sector_meta, sics_industry_meta,
    scope1_tco2e, scope2_lb_tco2e, scope2_mb_tco2e, scope3_total_tco2e,
    starts_with("scope3_c")
  )


# ── 4. Validate ───────────────────────────────────────────────────────────────

dup_check <- nzdpu_df |>
  count(nz_id, reporting_year, data_provider) |>
  filter(n > 1)

if (nrow(dup_check) > 0) {
  warning(sprintf("%d duplicate nz_id × year × provider rows detected", nrow(dup_check)))
}

n_companies <- n_distinct(nzdpu_df$nz_id)
n_with_lei  <- nzdpu_df |> filter(!is.na(lei), lei != "") |> distinct(nz_id) |> nrow()
n_scope1    <- sum(!is.na(nzdpu_df$scope1_tco2e))

cat(sprintf(
  "Validation:\n  %d unique companies | %d with LEI | %d scope-1 values disclosed\n",
  n_companies, n_with_lei, n_scope1
))

stopifnot(
  "Expected at least 200 companies" = n_companies >= 200,
  "Expected at least 500 disclosure records" = nrow(nzdpu_df) >= 500,
  "scope3_c15_tco2e column missing" = "scope3_c15_tco2e" %in% names(nzdpu_df)
)


# ── 5. Save ───────────────────────────────────────────────────────────────────

saveRDS(nzdpu_df, "shiny_app/data_prepared/nzdpu_french_companies.rds")
cat(sprintf("Saved %d rows × %d cols → shiny_app/data_prepared/nzdpu_french_companies.rds\n",
            nrow(nzdpu_df), ncol(nzdpu_df)))
