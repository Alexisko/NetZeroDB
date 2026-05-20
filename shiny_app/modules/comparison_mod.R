library(shiny)
library(bslib)
library(plotly)
library(dplyr)

source("utils/sector_mapping.R")

# ── Source colour palette ─────────────────────────────────────────────────────

source_colors <- c(
  "UK CCC"      = "#1f78b4",
  "SNBC"        = "#33a02c",
  "IEA WEO2025" = "#e31a1c"
)

# Short scenario labels for the legend
scenario_labels <- c(
  "Balanced Pathway" = "Balanced Pathway",
  "Baseline"         = "Baseline",
  "SNBC"             = "SNBC",
  "NZE 2050"         = "NZE 2050"
)

# Main scenarios get solid lines; baselines get dashes
scenario_dash <- c(
  "Balanced Pathway" = "solid",
  "Baseline"         = "dash",
  "SNBC"             = "solid",
  "NZE 2050"         = "solid"
)

scenario_opacity <- c(
  "Balanced Pathway" = 1,
  "Baseline"         = 0.5,
  "SNBC"             = 1,
  "NZE 2050"         = 1
)

# ── UI ────────────────────────────────────────────────────────────────────────

comparisonUI <- function(id) {
  ns <- NS(id)

  tagList(
    card(
      full_screen = TRUE,
      min_height  = "420px",
      card_header(
        class = "d-flex justify-content-between align-items-center",
        "Sectoral Emissions \u2014 Cross-Source Comparison",
        uiOutput(ns("chart_subtitle"))
      ),
      uiOutput(ns("iea_note")),
      plotlyOutput(ns("plot_comparison"), height = "400px")
    )
  )
}

# ── Server ────────────────────────────────────────────────────────────────────

comparisonServer <- function(id, uk_ccc_data, snbc_data, iea_data) {

  # Per-source sector lookup tables (computed once)
  uk_map   <- sector_mapping |> filter(source == "UK CCC")
  snbc_map <- sector_mapping |> filter(source == "SNBC")
  iea_map  <- sector_mapping |> filter(source == "IEA WEO2025")

  moduleServer(id, function(input, output, session) {

    # ── Reactive: filter & combine all sources ────────────────────────────────

    combined_data <- reactive({
      sel_sector  <- req(input$sector)
      sel_sources <- input$sources %||% character(0)
      dfs <- list()

      # ── UK CCC ──
      if ("UK CCC" %in% sel_sources) {
        uk_sectors <- uk_map |>
          filter(harmonized_sector == sel_sector) |>
          pull(original_sector)

        if (length(uk_sectors) > 0) {
          dfs[["UK CCC"]] <- uk_ccc_data |>
            filter(sector %in% uk_sectors, scenario == "Balanced Pathway") |>
            group_by(scenario, year) |>
            summarise(value = sum(value), .groups = "drop") |>
            mutate(
              source       = "UK CCC",
              unit         = "MtCO2e",
              period_label = as.character(year)
            )
        }
      }

      # ── SNBC ──
      if ("SNBC" %in% sel_sources) {
        snbc_sectors <- snbc_map |>
          filter(harmonized_sector == sel_sector) |>
          pull(original_sector)

        if (length(snbc_sectors) > 0) {
          dfs[["SNBC"]] <- snbc_data |>
            filter(sector %in% snbc_sectors) |>
            transmute(
              source, scenario, year, value, unit, period_label
            )
        }
      }

      # ── IEA ──
      if ("IEA WEO2025" %in% sel_sources) {
        iea_sectors <- iea_map |>
          filter(harmonized_sector == sel_sector) |>
          pull(original_sector)

        if (length(iea_sectors) > 0) {
          dfs[["IEA WEO2025"]] <- iea_data |>
            filter(sector %in% iea_sectors) |>
            mutate(period_label = as.character(year))
        }
      }

      if (length(dfs) == 0) return(tibble())
      bind_rows(dfs)
    })

    # ── Reactive: apply display mode ──────────────────────────────────────────
    # Indexed mode: all sources indexed to 100 at 2025.
    # UK CCC has a direct 2025 observation; SNBC (2023/2026) and IEA (2023/2035)
    # do not, so their 2025 base values are obtained by linear interpolation
    # between the two nearest data points using stats::approx().

    plot_data <- reactive({
      df <- combined_data()
      if (nrow(df) == 0) return(df)

      if (input$display_mode == "absolute") return(df)

      baseline_2025 <- df |>
        group_by(source, scenario) |>
        summarise(
          base_value = approx(year, value, xout = 2025, rule = 1)$y,
          .groups = "drop"
        )

      df |>
        left_join(baseline_2025, by = c("source", "scenario")) |>
        filter(!is.na(base_value), base_value != 0) |>
        mutate(value = value / base_value * 100) |>
        select(-base_value)
    })

    # ── UI: IEA coverage note ─────────────────────────────────────────────────

    output$iea_note <- renderUI({
      sel_sector  <- req(input$sector)
      sel_sources <- input$sources %||% character(0)

      # IEA note: sectors not covered
      iea_covered <- iea_map |> pull(harmonized_sector) |> unique()
      iea_checked <- "IEA WEO2025" %in% sel_sources

      msgs <- character(0)

      if (iea_checked && !sel_sector %in% iea_covered) {
        msgs <- c(msgs, paste0(
          "IEA WEO2025 Annex\u00a0A does not include ", sel_sector,
          " \u2014 only Transport, Buildings, Industry, and Energy are available."
        ))
      }

      if ("IEA WEO2025" %in% sel_sources && sel_sector %in% iea_covered) {
        msgs <- c(msgs,
          "IEA values are in Mt\u00a0CO\u2082 (combustion); UK\u00a0CCC and SNBC values are in MtCO\u2082e."
        )
      }

      if (length(msgs) == 0) return(NULL)

      div(
        class = "px-3 pb-1",
        lapply(msgs, function(m) {
          tags$p(class = "text-muted small mb-1", m)
        })
      )
    })

    # ── Dynamic subtitle ──────────────────────────────────────────────────────

    output$chart_subtitle <- renderUI({
      mode_label <- if (input$display_mode == "absolute") {
        "Absolute emissions"
      } else {
        "Indexed (2025 = 100)"
      }
      tags$span(class = "text-muted small", mode_label)
    })

    # ── Trajectory chart ──────────────────────────────────────────────────────

    output$plot_comparison <- renderPlotly({

      df <- plot_data()

      if (nrow(df) == 0) {
        return(
          plot_ly() |>
            layout(
              xaxis = list(visible = FALSE),
              yaxis = list(visible = FALSE),
              annotations = list(list(
                text      = "No data for the current selection.",
                showarrow = FALSE,
                xref = "paper", yref = "paper", x = 0.5, y = 0.5,
                font = list(size = 14, color = "#6c757d")
              ))
            )
        )
      }

      y_title <- if (input$display_mode == "absolute") {
        "Emissions (MtCO\u2082e / Mt CO\u2082)"
      } else {
        "Emissions Index (2025 = 100)"
      }

      # Build one trace per (source × scenario)
      groups <- df |>
        arrange(source, scenario, year) |>
        group_by(source, scenario) |>
        group_split()

      p <- plot_ly()

      for (grp in groups) {
        g_src  <- grp$source[[1]]
        g_scen <- grp$scenario[[1]]

        g_color   <- unname(source_colors[g_src])
        if (is.null(g_color) || is.na(g_color)) g_color <- "#999999"

        g_dash    <- unname(scenario_dash[g_scen])
        if (is.null(g_dash) || is.na(g_dash)) g_dash <- "solid"

        g_opacity <- unname(scenario_opacity[g_scen])
        if (is.null(g_opacity) || is.na(g_opacity)) g_opacity <- 1

        scen_short <- unname(scenario_labels[g_scen])
        if (is.null(scen_short) || is.na(scen_short)) scen_short <- g_scen

        trace_name <- paste0(g_src, " \u2014 ", scen_short)

        val_fmt <- if (input$display_mode == "absolute") {
          paste0(round(grp$value, 1), " ", grp$unit)
        } else {
          paste0(round(grp$value, 1))
        }

        hover_text <- paste0(
          "<b>", g_src, "</b><br>",
          "Scenario: ", scen_short, "<br>",
          if (!is.null(grp$period_label)) paste0("Period: ", grp$period_label, "<br>") else "",
          "Year: ", grp$year, "<br>",
          "Value: ", val_fmt
        )

        # SNBC: use lines+markers (sparse data)
        trace_mode <- if (g_src == "SNBC") "lines+markers" else "lines"

        p <- p |>
          add_trace(
            data        = grp,
            x           = ~year,
            y           = ~value,
            type        = "scatter",
            mode        = trace_mode,
            name        = trace_name,
            text        = hover_text,
            hoverinfo   = "text",
            legendgroup = g_src,
            legendgrouptitle = list(text = paste0("<b>", g_src, "</b>")),
            opacity     = g_opacity,
            line = list(color = g_color, dash = g_dash, width = 2.5),
            marker = list(color = g_color, size = 7)
          )
      }

      p |>
        layout(
          xaxis = list(
            title = "Year",
            dtick = 5,
            tick0 = 2025,
            range = list(2022, 2052)
          ),
          yaxis = list(
            title    = y_title,
            zeroline = TRUE,
            zerolinecolor = "#6c757d",
            zerolinewidth = 1
          ),
          legend = list(
            groupclick = "toggleitem",
            itemsizing = "constant"
          ),
          hovermode = "closest"
        )
    })
  })
}

# Null-coalescing operator (available in R 4.4+ as |>, define here for safety)
`%||%` <- function(x, y) if (!is.null(x)) x else y
