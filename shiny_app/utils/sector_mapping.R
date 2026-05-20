# sector_mapping.R
# Harmonized sector taxonomy mapping all sources to a common set of sectors,
# with optional subsector granularity for UK CCC and SBTi.
# Sourced by processing scripts and the Shiny comparison/pathway modules.

library(dplyr)

# ── Harmonized sector mapping ─────────────────────────────────────────────────
# harmonized_subsector is NA for sources without subsector breakdown (SNBC, IEA).

sector_mapping <- tribble(
  ~source,       ~original_sector,                                                                                                   ~harmonized_sector,                  ~harmonized_subsector,

  # ── UK CCC — cross-source sectors ─────────────────────────────────────────
  "UK CCC",      "Surface transport",                                                                                                "Transport",                         "Road & Rail",
  "UK CCC",      "Aviation",                                                                                                         "Transport",                         "Aviation",
  "UK CCC",      "Shipping",                                                                                                         "Transport",                         "Maritime",
  "UK CCC",      "Residential buildings",                                                                                            "Buildings",                         "Residential",
  "UK CCC",      "Non-residential buildings",                                                                                        "Buildings",                         "Commercial",
  "UK CCC",      "Agriculture",                                                                                                      "Agriculture",                       "Farming",
  "UK CCC",      "Industry",                                                                                                         "Industry",                          "Manufacturing",
  "UK CCC",      "Electricity supply",                                                                                               "Energy",                            "Power Generation",
  "UK CCC",      "Fuel supply",                                                                                                      "Energy",                            "Gas & Fuel Supply",
  "UK CCC",      "Waste",                                                                                                            "Waste",                             "Waste Management",

  # ── UK CCC — UK-only sectors (no SNBC/IEA/SBTi equivalent) ───────────────
  "UK CCC",      "Land use",                                                                                                         "Land use",                          NA,
  "UK CCC",      "Engineered removals",                                                                                              "Engineered removals",               NA,
  "UK CCC",      "F-gases",                                                                                                          "F-gases",                           NA,

  # ── SNBC — no subsector breakdown ─────────────────────────────────────────
  "SNBC",        "Transport",                                                                                                        "Transport",                         NA,
  "SNBC",        "Buildings",                                                                                                        "Buildings",                         NA,
  "SNBC",        "Agriculture",                                                                                                      "Agriculture",                       NA,
  "SNBC",        "Industry",                                                                                                         "Industry",                          NA,
  "SNBC",        "Energy",                                                                                                           "Energy",                            NA,
  "SNBC",        "Waste",                                                                                                            "Waste",                             NA,

  # ── IEA WEO2025 — no subsector breakdown ──────────────────────────────────
  "IEA WEO2025", "Transport",                                                                                                        "Transport",                         NA,
  "IEA WEO2025", "Buildings",                                                                                                        "Buildings",                         NA,
  "IEA WEO2025", "Industry",                                                                                                         "Industry",                          NA,
  "IEA WEO2025", "Energy",                                                                                                           "Energy",                            NA,
  # IEA Annex A free dataset does not include Agriculture or Waste

  # ── SBTi — cross-source sectors ───────────────────────────────────────────
  # Transport
  "SBTi",        "Air Transportation - Airlines",                                                                                    "Transport",                         "Aviation",
  "SBTi",        "Air Transportation - Airport Services",                                                                            "Transport",                         "Aviation",
  "SBTi",        "Air Freight Transportation and Logistics",                                                                         "Transport",                         "Aviation",
  "SBTi",        "Ground Transportation - Trucking Transportation",                                                                  "Transport",                         "Road & Rail",
  "SBTi",        "Ground Transportation - Highways and Railtracks",                                                                  "Transport",                         "Road & Rail",
  "SBTi",        "Ground Transportation - Railroads Transportation",                                                                 "Transport",                         "Road & Rail",
  "SBTi",        "Automobiles and Components",                                                                                       "Transport",                         "Road & Rail",
  "SBTi",        "Water Transportation - Water Transportation",                                                                      "Transport",                         "Maritime",
  "SBTi",        "Water Transportation - Ports and Services",                                                                        "Transport",                         "Maritime",

  # Buildings
  "SBTi",        "Homebuilding",                                                                                                     "Buildings",                         "Residential",
  "SBTi",        "Real Estate",                                                                                                      "Buildings",                         "Commercial",
  "SBTi",        "Construction and Engineering",                                                                                     "Buildings",                         "Construction",
  "SBTi",        "Building Products",                                                                                                "Buildings",                         "Construction",
  "SBTi",        "Construction Materials",                                                                                           "Buildings",                         "Construction",

  # Agriculture
  "SBTi",        "Food Production - Agricultural Production",                                                                        "Agriculture",                       "Farming",
  "SBTi",        "Food Production - Animal Source Food Production",                                                                  "Agriculture",                       "Farming",
  "SBTi",        "Forest and Paper Products - Forestry, Timber, Pulp and Paper, Rubber",                                            "Agriculture",                       "Forestry",

  # Industry
  "SBTi",        "Electrical Equipment and Machinery",                                                                               "Industry",                          "Manufacturing",
  "SBTi",        "Tires",                                                                                                            "Industry",                          "Manufacturing",
  "SBTi",        "Containers and Packaging",                                                                                         "Industry",                          "Manufacturing",
  "SBTi",        "Textiles, Apparel, Footwear and Luxury Goods",                                                                    "Consumer & Retail",                 "Retail & Consumer Goods",
  "SBTi",        "Aerospace and Defense",                                                                                            "Industry",                          "Manufacturing",
  "SBTi",        "Food and Beverage Processing",                                                                                     "Industry",                          "Manufacturing",
  "SBTi",        "Chemicals",                                                                                                        "Industry",                          "Chemicals",
  "SBTi",        "Mining - Iron, Aluminum, Other Metals",                                                                            "Industry",                          "Mining",
  "SBTi",        "Mining - Other (Rare Minerals, Precious Metals and Gems)",                                                         "Industry",                          "Mining",
  "SBTi",        "Mining - Coal",                                                                                                    "Industry",                          "Mining",

  # Energy
  "SBTi",        "Electric Utilities and Independent Power Producers and Energy Traders (including Fossil, Alternative and Nuclear Energy)", "Energy",               "Power Generation",
  "SBTi",        "Gas Utilities",                                                                                                    "Energy",                            "Gas & Fuel Supply",

  # Waste
  "SBTi",        "Solid Waste Management Utilities",                                                                                 "Waste",                             "Waste Management",

  # ── SBTi — SBTi-only sectors (no sectoral pathway equivalent) ────────────
  # Financial & Professional Services
  "SBTi",        "Banks, Diverse Financials, Insurance",                                                                             "Financial & Professional Services", "Banks & Insurance",
  "SBTi",        "Specialized Financial Services, Consumer Finance, Insurance Brokerage Firms",                                     "Financial & Professional Services", "Banks & Insurance",
  "SBTi",        "Professional Services",                                                                                            "Financial & Professional Services", "Professional Services",
  "SBTi",        "Trading Companies and Distributors, and Commercial Services and Supplies",                                        "Financial & Professional Services", "Professional Services",

  # Technology & Telecoms
  "SBTi",        "Software and Services",                                                                                            "Technology & Telecoms",             "Software & IT",
  "SBTi",        "Technology Hardware and Equipment",                                                                                "Technology & Telecoms",             "Software & IT",
  "SBTi",        "Semiconductors and Semiconductors Equipment",                                                                     "Technology & Telecoms",             "Software & IT",
  "SBTi",        "Telecommunication Services",                                                                                       "Technology & Telecoms",             "Telecoms & Media",
  "SBTi",        "Media",                                                                                                            "Technology & Telecoms",             "Telecoms & Media",

  # Healthcare
  "SBTi",        "Healthcare Providers and Services, and Healthcare Technology",                                                     "Healthcare",                        "Healthcare Services",
  "SBTi",        "Healthcare Equipment and Supplies",                                                                                "Healthcare",                        "Healthcare Services",
  "SBTi",        "Pharmaceuticals, Biotechnology and Life Sciences",                                                                 "Healthcare",                        "Pharma & Biotech",

  # Consumer & Retail
  "SBTi",        "Consumer Durables, Household and Personal Products",                                                               "Consumer & Retail",                 "Retail & Consumer Goods",
  "SBTi",        "Retailing",                                                                                                        "Consumer & Retail",                 "Retail & Consumer Goods",
  "SBTi",        "Food and Staples Retailing",                                                                                       "Consumer & Retail",                 "Retail & Consumer Goods",
  "SBTi",        "Hotels, Restaurants and Leisure, and Tourism Services",                                                            "Consumer & Retail",                 "Leisure & Services",
  "SBTi",        "Specialized Consumer Services",                                                                                    "Consumer & Retail",                 "Leisure & Services",
  "SBTi",        "Education Services",                                                                                               "Consumer & Retail",                 "Leisure & Services",
  "SBTi",        "Tobacco",                                                                                                          "Consumer & Retail",                 "Leisure & Services",

  # Other
  "SBTi",        "Water Utilities",                                                                                                  "Other",                             "Water",
  "SBTi",        "Public Agencies",                                                                                                  "Other",                             "Public Sector"
)

# ── Convenience vectors / helpers ─────────────────────────────────────────────

# Cross-source harmonized sectors (used by Sectoral Comparison tab)
harmonized_sectors <- c(
  "Transport",
  "Buildings",
  "Agriculture",
  "Industry",
  "Energy",
  "Waste"
)

# All sectors available in the Net-zero Pathway tab, grouped for the UI
pathway_sector_choices <- list(
  "Cross-source"  = c("Transport", "Buildings", "Agriculture", "Industry", "Energy", "Waste"),
  "UK CCC only"   = c("Land use", "Engineered removals", "F-gases"),
  "SBTi only"     = c("Financial & Professional Services", "Technology & Telecoms",
                       "Healthcare", "Consumer & Retail", "Other")
)

# Returns harmonized subsectors for a given macro sector (UK CCC + SBTi, deduplicated)
get_subsectors <- function(macro_sector) {
  sector_mapping |>
    filter(harmonized_sector == macro_sector, !is.na(harmonized_subsector)) |>
    pull(harmonized_subsector) |>
    unique()
}
