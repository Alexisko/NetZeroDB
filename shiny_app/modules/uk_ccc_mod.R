library(shiny)
library(bslib)
library(plotly)
library(dplyr)

# ── Colour palettes ───────────────────────────────────────────────────────────

# Fixed palette per sector (used in sector-level view)
sector_colors <- c(
  "Agriculture"               = "#4e9a51",
  "Aviation"                  = "#6baed6",
  "Electricity supply"        = "#f7c948",
  "Engineered removals"       = "#984ea3",
  "F-gases"                   = "#ff7f00",
  "Fuel supply"               = "#e7969c",
  "Industry"                  = "#1f78b4",
  "Land use"                  = "#33a02c",
  "Non-residential buildings" = "#a6761d",
  "Residential buildings"     = "#e6550d",
  "Shipping"                  = "#74c476",
  "Surface transport"         = "#d62728",
  "Waste"                     = "#8c564b"
)

# 24-colour qualitative palette for subsector-level view (Tableau-inspired)
qualitative_palette <- c(
  "#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f",
  "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#bab0ac",
  "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
  "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
  "#aec7e8", "#ffbb78", "#98df8a", "#ff9896"
)

# ── UI ────────────────────────────────────────────────────────────────────────

ukCccUI <- function(id) {
  ns <- NS(id)

  tagList(
    card(
      full_screen = TRUE,
      min_height  = "420px",
      card_header(
        class = "d-flex justify-content-between align-items-center",
        "UK CCC 7th Carbon Budget \u2014 Emissions Trajectories",
        uiOutput(ns("chart_subtitle"))
      ),
      plotlyOutput(ns("plot_trajectories"), height = "380px")
    )
  )
}

# ── Server ────────────────────────────────────────────────────────────────────

ukCccServer <- function(id, data) {
  moduleServer(id, function(input, output, session) {

    # Reactive: filter to selected sectors (and subsectors when in subsector mode)
    base_filtered <- reactive({
      df <- data

      if (!is.null(input$sectors) && length(input$sectors) > 0)
        df <- filter(df, sector %in% input$sectors)

      if (input$level == "Subsector" &&
          !is.null(input$subsectors) && length(input$subsectors) > 0)
        df <- filter(df, subsector %in% input$subsectors)

      df
    })

    # Reactive: stable colour mapping for all subsectors of selected sectors.
    # Based on the full set (before subsector filtering) so colours stay
    # consistent when individual subsectors are toggled on/off.
    sub_colors <- reactive({
      subs <- data |>
        filter(sector %in% req(input$sectors)) |>
        pull(subsector) |>
        unique() |>
        sort()

      setNames(rep_len(qualitative_palette, length(subs)), subs)
    })

    # Observer: refresh subsector choices whenever sectors selection or level changes
    observeEvent(list(input$sectors, input$level), {
      if (input$level != "Subsector") return()

      subs <- data |>
        filter(sector %in% req(input$sectors)) |>
        pull(subsector) |>
        unique() |>
        sort()

      updateSelectizeInput(session, "subsectors", choices = subs, selected = subs)
    }, ignoreNULL = FALSE)

    # Reactive: aggregate to chosen level
    #   - Sector:    sum subsectors → one row per (scenario, sector, year)
    #   - Subsector: pass through; keep sector column for colour lookup
    aggregated <- reactive({
      df <- base_filtered()

      if (input$level == "Sector") {
        df |>
          group_by(scenario, sector, year) |>
          summarise(value = sum(value), .groups = "drop") |>
          rename(group_label = sector)
      } else {
        df |>
          select(scenario, sector, subsector, year, value) |>
          rename(group_label = subsector)
      }
    })

    # Reactive: apply display mode
    #   - absolute: values in MtCO2e as-is
    #   - relative: index each group to its own 2025 value (2025 = 100)
    #     Groups with val_2025 == 0 are dropped (division by zero; these are
    #     sectors/subsectors that ramp up from nothing, e.g. BECCS, DACCS).
    plot_data <- reactive({
      df <- aggregated()

      if (input$display_mode == "absolute") {
        return(df)
      }

      baseline_2025 <- df |>
        filter(year == 2025) |>
        select(scenario, group_label, val_2025 = value)

      df |>
        left_join(baseline_2025, by = c("scenario", "group_label")) |>
        filter(val_2025 != 0) |>
        mutate(value = value / val_2025 * 100) |>
        select(-val_2025)
    })

    # ── Dynamic subtitle ──────────────────────────────────────────────────────

    output$chart_subtitle <- renderUI({
      level_label <- if (input$level == "Sector") "Sector level" else "Subsector level"
      mode_label  <- if (input$display_mode == "absolute") "MtCO2e" else "Indexed (2025 = 100)"

      subtitle <- tags$span(
        class = "text-muted small",
        paste(level_label, "\u00b7", mode_label)
      )

      if (input$display_mode == "relative") {
        agg <- aggregated()
        n_excluded <- agg |>
          filter(year == 2025) |>
          group_by(scenario, group_label) |>
          summarise(val = sum(value), .groups = "drop") |>
          filter(val == 0) |>
          pull(group_label) |>
          unique() |>
          length()

        if (n_excluded > 0) {
          return(tagList(
            subtitle,
            tags$small(
              class = "text-muted ms-2",
              paste0("(", n_excluded, " group(s) with zero 2025 baseline hidden)")
            )
          ))
        }
      }

      subtitle
    })

    # ── Trajectory chart ──────────────────────────────────────────────────────

    output$plot_trajectories <- renderPlotly({

      df <- plot_data()

      # Empty state
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

      # Hover mode: unified works well at sector level (13 groups),
      # closest is cleaner at subsector level (up to 59 groups)
      hover_mode <- if (input$level == "Sector") "x unified" else "closest"

      y_title <- if (input$display_mode == "absolute") "Emissions (MtCO2e)"
                 else "Emissions Index (2025 = 100)"

      # Reference line position
      ref_y <- if (input$display_mode == "relative") 100 else 0

      # Y-axis range: in relative mode fix lower bound at 0 (indexed values
      # below 0 are not meaningful here) and cap upper bound to prevent
      # outliers from collapsing the scale
      y_range <- if (input$display_mode == "relative") list(c(0, 200)) else list(NULL)

      # Build one trace per (group_label × scenario) combination
      groups <- df |>
        arrange(group_label, scenario, year) |>
        group_by(group_label, scenario) |>
        group_split()

      p <- plot_ly()

      for (grp in groups) {
        g_label  <- grp$group_label[[1]]
        g_scen   <- grp$scenario[[1]]

        g_color <- if (input$level == "Sector") {
          unname(sector_colors[g_label])
        } else {
          unname(sub_colors()[g_label])
        }
        if (is.null(g_color) || is.na(g_color)) g_color <- "#999999"

        g_dash    <- if (g_scen == "Balanced Pathway") "solid" else "dash"
        g_width   <- if (g_scen == "Balanced Pathway") 2.5 else 1.5
        g_opacity <- if (g_scen == "Balanced Pathway") 1 else 0.6

        val_fmt <- if (input$display_mode == "absolute") {
          paste0(round(grp$value, 2), " MtCO2e")
        } else {
          paste0(round(grp$value, 1))
        }

        hover_text <- paste0(
          "<b>", g_label, "</b><br>",
          "Scenario: ", g_scen, "<br>",
          "Year: ", grp$year, "<br>",
          "Value: ", val_fmt
        )

        # Only show legend entry for Balanced Pathway; legendgroup links both
        # lines so clicking once toggles both scenarios for that group
        show_legend <- (g_scen == "Balanced Pathway")

        p <- p |>
          add_lines(
            data        = grp,
            x           = ~year,
            y           = ~value,
            name        = g_label,
            text        = hover_text,
            hoverinfo   = "text",
            showlegend  = show_legend,
            legendgroup = g_label,
            opacity     = g_opacity,
            line = list(color = g_color, dash = g_dash, width = g_width)
          )
      }

      p |>
        layout(
          xaxis = list(
            title = "Year",
            dtick = 5,
            tick0 = 2025,
            range = c(2024, 2051)
          ),
          yaxis = list(
            title        = y_title,
            range        = y_range[[1]],
            zeroline     = TRUE,
            zerolinecolor = "#6c757d",
            zerolinewidth = 1
          ),
          shapes = list(list(
            type  = "line",
            x0 = 0, x1 = 1, xref = "paper",
            y0 = ref_y, y1 = ref_y, yref = "y",
            line  = list(dash = "dot", color = "#adb5bd", width = 1)
          )),
          legend    = list(
            title      = list(text = if (input$level == "Sector") "<b>Sector</b>" else "<b>Subsector</b>"),
            itemsizing = "constant"
          ),
          hovermode = hover_mode
        )
    })
  })
}
