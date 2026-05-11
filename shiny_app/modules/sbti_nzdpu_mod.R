library(shiny)
library(bslib)
library(bsicons)
library(plotly)
library(DT)
library(dplyr)
library(scales)

# ── UI ────────────────────────────────────────────────────────────────────────

sbtiNzdpuUI <- function(id) {
  ns <- NS(id)

  tagList(
    # ── Value boxes ───────────────────────────────────────────────────────────
    layout_columns(
      col_widths = c(4, 4, 4),
      value_box(
        title    = "Companies matched",
        value    = textOutput(ns("vb_companies"), inline = TRUE),
        showcase = bs_icon("buildings"),
        theme    = "primary"
      ),
      value_box(
        title    = "On track (latest year)",
        value    = textOutput(ns("vb_on_track"), inline = TRUE),
        showcase = bs_icon("check-circle"),
        theme    = value_box_theme(bg = "#198754", fg = "#fff")
      ),
      value_box(
        title    = "Most recent disclosure",
        value    = textOutput(ns("vb_year"), inline = TRUE),
        showcase = bs_icon("calendar-check"),
        theme    = "secondary"
      )
    ),

    # ── Company trajectory ────────────────────────────────────────────────────
    card(
      full_screen = TRUE,
      min_height  = "420px",
      card_header("Company Trajectory — Actual vs Target"),
      plotlyOutput(ns("plot_trajectory"), height = "380px")
    ),

    # ── Sector on-track bar chart ─────────────────────────────────────────────
    card(
      full_screen = TRUE,
      min_height  = "340px",
      card_header("% On Track by Sector (most recent year per target)"),
      plotlyOutput(ns("plot_sector"), height = "300px")
    )
  )
}

# ── Server ────────────────────────────────────────────────────────────────────

sbtiNzdpuServer <- function(id, data) {
  moduleServer(id, function(input, output, session) {

    scope_colors <- c(
      "1"     = "#0d6efd",
      "2"     = "#198754",
      "3"     = "#dc3545",
      "1+2"   = "#fd7e14",
      "1+2+3" = "#6f42c1",
      "1+3"   = "#20c997",
      "2+3"   = "#d63384"
    )

    # Filtered by scope only (used by value boxes + sector chart)
    filtered_data <- reactive({
      df <- data
      if (!is.null(input$scope) && length(input$scope) > 0)
        df <- filter(df, scope %in% input$scope)
      df
    })

    # Filtered by scope + company (used by trajectory chart)
    company_data <- reactive({
      df <- filtered_data()
      if (!is.null(input$company_names) && length(input$company_names) > 0)
        df <- filter(df, company_name %in% input$company_names)
      df
    })

    # Most-recent reporting_year per target; used by value boxes + sector chart
    latest_on_track <- reactive({
      filtered_data() |>
        group_by(sbti_id, scope, target_year) |>
        slice_max(reporting_year, n = 1, with_ties = FALSE) |>
        ungroup() |>
        filter(!is.na(on_track))
    })

    # ── Value boxes ───────────────────────────────────────────────────────────

    output$vb_companies <- renderText({
      n_distinct(filtered_data()$nz_id)
    })

    output$vb_on_track <- renderText({
      df <- latest_on_track()
      if (nrow(df) == 0) return("—")
      pct <- mean(df$on_track) * 100
      paste0(round(pct, 0), "%")
    })

    output$vb_year <- renderText({
      yr <- max(filtered_data()$reporting_year, na.rm = TRUE)
      if (is.infinite(yr)) "—" else as.character(yr)
    })

    # ── Trajectory chart ──────────────────────────────────────────────────────

    output$plot_trajectory <- renderPlotly({

      empty_plot <- function(msg) {
        plot_ly() |>
          layout(
            xaxis = list(visible = FALSE),
            yaxis = list(visible = FALSE),
            annotations = list(list(
              text      = msg,
              showarrow = FALSE,
              xref = "paper", yref = "paper", x = 0.5, y = 0.5,
              font = list(size = 14, color = "#6c757d")
            ))
          )
      }

      if (is.null(input$company_names) || length(input$company_names) == 0)
        return(empty_plot("Search for a company in the sidebar to view its trajectory."))

      df <- company_data() |>
        filter(
          !is.na(base_year), !is.na(target_year),
          !is.na(target_value), target_value <= 1,
          !is.na(emissions_tco2e), !is.na(base_emissions)
        )

      if (nrow(df) == 0)
        return(empty_plot("No trajectory data found for the current selection."))

      # ── Target path: one dashed segment per unique (sbti_id, scope, base/target year) ──

      target_pts <- df |>
        distinct(sbti_id, company_name, scope, base_year, target_year, target_value, base_emissions) |>
        mutate(group_id = paste(sbti_id, scope))

      target_lines <- bind_rows(
        target_pts |> mutate(year = as.numeric(base_year),
                             emissions = base_emissions,
                             hover_text = paste0(
                               "<b>", company_name, "</b> — Scope ", scope, "<br>",
                               "Baseline: ", base_year, "<br>",
                               "Emissions: ", format(round(base_emissions), big.mark = ","), " tCO2e"
                             ),
                             .sort_key = as.numeric(base_year) - 0.5),
        target_pts |> mutate(year = as.numeric(target_year),
                             emissions = base_emissions * (1 - target_value),
                             hover_text = paste0(
                               "<b>", company_name, "</b> — Scope ", scope, "<br>",
                               "Target: ", target_year, "<br>",
                               "Reduction: ", percent(target_value, accuracy = 1), "<br>",
                               "Target emissions: ", format(round(base_emissions * (1 - target_value)), big.mark = ","), " tCO2e"
                             ),
                             .sort_key = as.numeric(target_year)),
        target_pts |> mutate(year = NA_real_, emissions = NA_real_, hover_text = NA_character_,
                             .sort_key = Inf)
      ) |>
        arrange(group_id, .sort_key)

      # ── Actual emissions: one point per (company, scope, reporting_year) ──

      actual_pts <- df |>
        distinct(sbti_id, company_name, scope, reporting_year, emissions_tco2e) |>
        arrange(sbti_id, scope, reporting_year) |>
        mutate(
          group_id   = paste(sbti_id, scope, "actual"),
          hover_text = paste0(
            "<b>", company_name, "</b> — Scope ", scope, "<br>",
            "Year: ", reporting_year, "<br>",
            "Emissions: ", format(round(emissions_tco2e), big.mark = ","), " tCO2e"
          )
        )

      actual_lines <- bind_rows(
        actual_pts |> mutate(.sort_key = as.numeric(reporting_year)),
        actual_pts |> distinct(group_id, company_name, scope) |>
          mutate(reporting_year = NA_integer_, emissions_tco2e = NA_real_,
                 hover_text = NA_character_, .sort_key = Inf)
      ) |>
        arrange(group_id, .sort_key)

      # Build plot: add one trace group per scope for target lines, then actual
      scopes_present <- sort(unique(df$scope))

      p <- plot_ly()

      for (sc in scopes_present) {
        col <- scope_colors[sc]
        if (is.na(col)) col <- "#999999"

        tl <- filter(target_lines, scope == sc)
        p <- add_trace(p,
          data      = tl,
          x         = ~year, y = ~emissions,
          type      = "scatter", mode = "lines",
          name      = paste0("Scope ", sc, " — target"),
          legendgroup = sc,
          line      = list(color = col, dash = "dash", width = 2),
          text      = ~hover_text, hoverinfo = "text"
        )

        al <- filter(actual_lines, scope == sc)
        p <- add_trace(p,
          data      = al,
          x         = ~reporting_year, y = ~emissions_tco2e,
          type      = "scatter", mode = "lines+markers",
          name      = paste0("Scope ", sc, " — actual"),
          legendgroup = sc,
          line      = list(color = col, width = 2),
          marker    = list(color = col, size = 7),
          text      = ~hover_text, hoverinfo = "text"
        )
      }

      p |>
        layout(
          xaxis = list(title = "Year", dtick = 5),
          yaxis = list(
            title    = "Emissions (tCO2e)",
            zeroline = FALSE
          ),
          legend    = list(title = list(text = "<b>Scope</b>")),
          hovermode = "closest"
        )
    })

    # ── Sector on-track bar chart ─────────────────────────────────────────────

    output$plot_sector <- renderPlotly({

      summary_df <- latest_on_track() |>
        group_by(sics_sector) |>
        summarise(
          n_total    = n(),
          n_on_track = sum(on_track),
          pct        = n_on_track / n_total,
          .groups    = "drop"
        ) |>
        arrange(pct) |>
        mutate(
          hover_text = paste0(
            "<b>", sics_sector, "</b><br>",
            round(pct * 100, 0), "% on track<br>",
            n_on_track, " / ", n_total, " targets"
          )
        )

      if (nrow(summary_df) == 0) {
        return(
          plot_ly() |>
            layout(
              xaxis = list(visible = FALSE), yaxis = list(visible = FALSE),
              annotations = list(list(
                text = "No on-track data available for the current scope selection.",
                showarrow = FALSE,
                xref = "paper", yref = "paper", x = 0.5, y = 0.5,
                font = list(size = 14, color = "#6c757d")
              ))
            )
        )
      }

      plot_ly(
        summary_df,
        x         = ~pct,
        y         = ~reorder(sics_sector, pct),
        type      = "bar",
        orientation = "h",
        marker    = list(color = "#0d6efd"),
        text      = ~hover_text,
        hoverinfo = "text"
      ) |>
        layout(
          xaxis = list(
            title      = "% On Track",
            tickformat = ".0%",
            range      = c(0, 1)
          ),
          yaxis  = list(title = ""),
          shapes = list(list(
            type  = "line",
            x0 = 0.5, x1 = 0.5, xref = "x",
            y0 = 0,   y1 = 1,   yref = "paper",
            line  = list(dash = "dot", color = "#adb5bd", width = 1)
          )),
          margin    = list(l = 160),
          hovermode = "closest"
        )
    })
  })
}
