library(shiny)
library(bslib)
library(bsicons)
library(plotly)
library(dplyr)

source("modules/sbti_mod.R")
source("modules/uk_ccc_mod.R")
source("modules/comparison_mod.R")
source("modules/sbti_nzdpu_mod.R")
source("modules/netzero_pathway_mod.R")

# Load data once at startup (shared across all sessions)
# Path is relative to shiny_app/ (the app's working directory)
sbti_data    <- readRDS("../data_prepared/sbti_targets.rds")
uk_ccc_data  <- readRDS("../data_prepared/uk_ccc_subsector.rds")
snbc_data    <- readRDS("../data_prepared/snbc_emissions.rds")
iea_data     <- readRDS("../data_prepared/iea_emissions.rds")
matched_data <- readRDS("../data_prepared/sbti_nzdpu_matched.rds")

# ── Sidebar choices (computed from data, not hardcoded) ────────────────────────

company_choices  <- sort(unique(sbti_data$company_name[!is.na(sbti_data$company_name)]))
scope_choices    <- sort(unique(sbti_data$scope[!is.na(sbti_data$scope)]))
type_choices     <- sort(unique(sbti_data$type[!is.na(sbti_data$type)]))
pathway_scope_choices <- pathway_sbti_scope_choices

uk_sector_choices <- sort(unique(uk_ccc_data$sector))

matched_company_choices <- sort(unique(matched_data$company_name))
matched_scope_choices   <- sort(unique(matched_data$scope[!is.na(matched_data$scope)]))

# Sidebar inputs live outside the module, so must be manually namespaced
ns_sbti    <- NS("sbti")
ns_uk_ccc  <- NS("uk_ccc")
ns_comp    <- NS("comp")
ns_matched <- NS("matched")
ns_pathway <- NS("pathway")

# ── UI ─────────────────────────────────────────────────────────────────────────

ui <- page_navbar(
  title  = "NetZeroDB",
  theme  = bs_theme(version = 5, preset = "flatly"),
  id     = "main_nav",

  # ── SBTi tab ──────────────────────────────────────────────────────────────
  nav_panel(
    "SBTi Targets",
    layout_sidebar(
      sidebar = sidebar(
        title = "Filters",
        width = "300px",
        open  = "desktop",

        # Primary: company search (server-side for performance with 10k+ companies)
        selectizeInput(
          ns_sbti("company_names"),
          label    = "Company",
          choices  = NULL,
          multiple = TRUE,
          options  = list(placeholder = "Search for a company\u2026")
        ),

        hr(),

        # Secondary: scope
        tags$label("Scope", class = "form-label"),
        checkboxGroupInput(
          ns_sbti("scope"),
          label    = NULL,
          choices  = scope_choices,
          selected = scope_choices
        ),

        hr(),

        # Secondary: target type (default = Absolute only)
        selectizeInput(
          ns_sbti("target_type"),
          label    = "Target type",
          choices  = type_choices,
          selected = "Absolute",
          multiple = TRUE
        )
      ),

      sbtiUI("sbti")
    )
  ),

  # ── UK Carbon Budget tab ───────────────────────────────────────────────────
  nav_panel(
    "UK Carbon Budget",
    layout_sidebar(
      sidebar = sidebar(
        title = "Options",
        width = "280px",
        open  = "desktop",

        radioButtons(
          ns_uk_ccc("level"),
          label    = "Aggregation level",
          choices  = c("Sector", "Subsector"),
          selected = "Sector",
          inline   = TRUE
        ),

        hr(),

        tags$label("Sectors", class = "form-label"),
        checkboxGroupInput(
          ns_uk_ccc("sectors"),
          label    = NULL,
          choices  = uk_sector_choices,
          selected = uk_sector_choices
        ),

        # Subsector filter — only shown in subsector mode; choices populated
        # server-side based on currently selected sectors
        conditionalPanel(
          condition = "input['uk_ccc-level'] == 'Subsector'",
          hr(),
          selectizeInput(
            ns_uk_ccc("subsectors"),
            label    = "Subsectors",
            choices  = NULL,
            multiple = TRUE,
            options  = list(placeholder = "Select subsectors\u2026")
          )
        ),

        hr(),

        radioButtons(
          ns_uk_ccc("display_mode"),
          label    = "Y-axis",
          choices  = c("Absolute (MtCO2e)" = "absolute", "Indexed (2025 = 100)" = "relative"),
          selected = "absolute"
        )
      ),

      ukCccUI("uk_ccc")
    )
  ),

  # ── Sectoral Comparison tab ────────────────────────────────────────────────
  nav_panel(
    "Sectoral Comparison",
    layout_sidebar(
      sidebar = sidebar(
        title = "Options",
        width = "280px",
        open  = "desktop",

        selectInput(
          ns_comp("sector"),
          label    = "Sector",
          choices  = harmonized_sectors,
          selected = "Transport"
        ),

        hr(),

        tags$label("Sources", class = "form-label"),
        checkboxGroupInput(
          ns_comp("sources"),
          label    = NULL,
          choices  = c("UK CCC", "SNBC", "IEA WEO2025"),
          selected = c("UK CCC", "SNBC", "IEA WEO2025")
        ),

        hr(),

        radioButtons(
          ns_comp("display_mode"),
          label    = "Y-axis",
          choices  = c(
            "Absolute"               = "absolute",
            "Indexed (2025 = 100)"       = "relative"
          ),
          selected = "absolute"
        )
      ),

      comparisonUI("comp")
    )
  ),

  # ── Net-zero Pathway tab ──────────────────────────────────────────────────
  nav_panel(
    "Net-zero Pathway",
    layout_sidebar(
      sidebar = sidebar(
        title = "Options",
        width = "300px",
        open  = "desktop",

        selectizeInput(
          ns_pathway("find_company"),
          label   = "Find company",
          choices = NULL,
          selected = character(0),
          multiple = TRUE,
          options = list(
            placeholder = "Search a company…",
            maxItems = 1
          )
        ),

        hr(),

        selectInput(
          ns_pathway("sector"),
          label    = "Sector",
          choices  = pathway_sector_choices,
          selected = "Transport"
        ),

        selectInput(
          ns_pathway("subsector"),
          label    = "Subsector",
          choices  = c("All", get_subsectors("Transport")),
          selected = "All"
        ),

        hr(),

        tags$label("Sources", class = "form-label"),
        checkboxGroupInput(
          ns_pathway("sources"),
          label    = NULL,
          choices  = c("UK CCC", "SNBC", "IEA WEO2025", "SBTi"),
          selected = c("UK CCC", "SNBC", "IEA WEO2025", "SBTi")
        ),

        # SBTi-specific controls — only shown when SBTi is checked
        conditionalPanel(
          condition = "input['pathway-sources'] && input['pathway-sources'].includes('SBTi')",

          hr(),

          tags$label("SBTi scope", class = "form-label"),
          checkboxGroupInput(
            ns_pathway("sbti_scope"),
            label    = NULL,
            choices  = pathway_scope_choices,
            selected = pathway_scope_choices
          ),

          selectizeInput(
            ns_pathway("sbti_companies"),
            label    = "Highlight companies",
            choices  = NULL,
            selected = character(0),
            multiple = TRUE,
            options  = list(placeholder = "Search companies…")
          )
        )
      ),

      netZeroPathwayUI("pathway")
    )
  ),

  # ── French Companies tab ───────────────────────────────────────────────────
  nav_panel(
    "French Companies",
    layout_sidebar(
      sidebar = sidebar(
        title = "Filters",
        width = "300px",
        open  = "desktop",

        selectizeInput(
          ns_matched("company_names"),
          label    = "Company",
          choices  = NULL,
          multiple = TRUE,
          options  = list(placeholder = "Search for a company…")
        ),

        hr(),

        tags$label("Scope", class = "form-label"),
        checkboxGroupInput(
          ns_matched("scope"),
          label    = NULL,
          choices  = matched_scope_choices,
          selected = matched_scope_choices
        )
      ),

      sbtiNzdpuUI("matched")
    )
  )
)

# ── Server ─────────────────────────────────────────────────────────────────────

server <- function(input, output, session) {
  # Populate company choices server-side (avoids shipping 10k+ names to the client)
  updateSelectizeInput(
    session, ns_sbti("company_names"),
    choices = company_choices,
    server  = TRUE
  )

  updateSelectizeInput(
    session, ns_matched("company_names"),
    choices = matched_company_choices,
    server  = TRUE
  )

  updateSelectizeInput(
    session, ns_pathway("find_company"),
    choices  = company_choices,
    selected = character(0),
    server   = TRUE
  )

  sbtiServer("sbti",    data = sbti_data)
  ukCccServer("uk_ccc", data = uk_ccc_data)
  comparisonServer("comp",
    uk_ccc_data = uk_ccc_data,
    snbc_data   = snbc_data,
    iea_data    = iea_data
  )
  sbtiNzdpuServer("matched", data = matched_data)

  # Update subsector choices when sector changes (Net-zero Pathway tab)
  observeEvent(input[[ns_pathway("sector")]], {
    sel <- input[[ns_pathway("sector")]]
    if (is.null(sel)) return()
    sub_choices <- c("All", get_subsectors(sel))
    cur_sub <- input[[ns_pathway("subsector")]]

    if (!is.null(cur_sub) && cur_sub %in% sub_choices) {
      updateSelectInput(session, ns_pathway("subsector"),
                        choices = sub_choices)
    } else {
      updateSelectInput(session, ns_pathway("subsector"),
                        choices = sub_choices, selected = "All")
    }
  })

  netZeroPathwayServer("pathway",
    uk_ccc_data = uk_ccc_data,
    snbc_data   = snbc_data,
    iea_data    = iea_data,
    sbti_data   = sbti_data
  )
}

shinyApp(ui, server)
