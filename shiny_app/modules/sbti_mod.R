library(shiny)
library(bslib)
library(bsicons)
library(plotly)
library(DT)
library(dplyr)
library(scales)

# ── UI ────────────────────────────────────────────────────────────────────────

sbtiUI <- function(id) {
  ns <- NS(id)

  tagList(
    card(
      full_screen = TRUE,
      min_height  = "420px",
      card_header("Emissions Reduction Trajectories"),
      plotlyOutput(ns("plot_trajectory"), height = "380px")
    ),

    card(
      full_screen = TRUE,
      card_header("Target Details"),
      DT::DTOutput(ns("tbl_wording"))
    )
  )
}

# ── Server ────────────────────────────────────────────────────────────────────

sbtiServer <- function(id, data) {
  moduleServer(id, function(input, output, session) {

    # Reactive: filter data to selected companies, scopes, and target type
    filtered_data <- reactive({
      df <- data

      if (!is.null(input$company_names) && length(input$company_names) > 0)
        df <- filter(df, company_name %in% input$company_names)

      if (!is.null(input$scope) && length(input$scope) > 0)
        df <- filter(df, scope %in% input$scope)

      if (!is.null(input$target_type) && length(input$target_type) > 0)
        df <- filter(df, type %in% input$target_type)

      df
    })

    # ── Trajectory chart ──────────────────────────────────────────────────────

    output$plot_trajectory <- renderPlotly({

      # Empty state: no company selected
      if (is.null(input$company_names) || length(input$company_names) == 0) {
        return(
          plot_ly() |>
            layout(
              xaxis = list(visible = FALSE),
              yaxis = list(visible = FALSE),
              annotations = list(list(
                text      = "Search for a company in the sidebar to view its trajectory.",
                showarrow = FALSE,
                xref = "paper", yref = "paper", x = 0.5, y = 0.5,
                font = list(size = 14, color = "#6c757d")
              ))
            )
        )
      }

      # Filter to rows where a trajectory can be built
      traj_base <- filtered_data() |>
        filter(
          !is.na(base_year), !is.na(target_year),
          !is.na(target_value), target_value <= 1
        ) |>
        # Group key: company × scope × base_year (same baseline = same trajectory)
        mutate(group_id = paste(company_name, scope, base_year))

      if (nrow(traj_base) == 0) {
        return(
          plot_ly() |>
            layout(
              xaxis = list(visible = FALSE),
              yaxis = list(visible = FALSE),
              annotations = list(list(
                text      = "No targets found for the current selection.",
                showarrow = FALSE,
                xref = "paper", yref = "paper", x = 0.5, y = 0.5,
                font = list(size = 14, color = "#6c757d")
              ))
            )
        )
      }

      # Build stitched trajectories:
      #   - One base point per (company, scope, base_year) group at index = 100
      #   - One target point per row, sorted by target_year within the group
      #   - NA separator between groups (breaks the plotly line)
      # Using .sort_key to order: base (base_year - 0.5) → targets (target_year) → NA (Inf)

      base_pts <- traj_base |>
        distinct(group_id, company_name, scope, base_year) |>
        mutate(
          year       = base_year,
          index      = 100,
          hover_text = paste0(
            "<b>", company_name, "</b><br>",
            "Scope: ", scope, "<br>",
            "Baseline: ", base_year, " (index = 100)"
          ),
          .sort_key  = as.numeric(base_year) - 0.5
        )

      target_pts <- traj_base |>
        mutate(
          year       = target_year,
          index      = 100 * (1 - target_value),
          hover_text = paste0(
            "<b>", company_name, "</b><br>",
            "Scope: ", scope, "<br>",
            "Classification: ", coalesce(target_classification_short, "\u2014"), "<br>",
            "Reduction: ", percent(target_value, accuracy = 1), "<br>",
            base_year, " \u2192 ", target_year, "<br>",
            "Index: ", round(100 * (1 - target_value), 1)
          ),
          .sort_key  = as.numeric(target_year)
        )

      sep_pts <- traj_base |>
        distinct(group_id, company_name, scope, base_year) |>
        mutate(year = NA_real_, index = NA_real_, hover_text = NA_character_, .sort_key = Inf)

      traj_plot <- bind_rows(base_pts, target_pts, sep_pts) |>
        arrange(group_id, .sort_key)

      # Consistent colour palette per scope
      scope_colors <- c(
        "1"     = "#0d6efd",
        "2"     = "#198754",
        "3"     = "#dc3545",
        "1+2"   = "#fd7e14",
        "1+2+3" = "#6f42c1",
        "1+3"   = "#20c997",
        "2+3"   = "#d63384"
      )

      plot_ly(
        traj_plot,
        x         = ~year,
        y         = ~index,
        color     = ~scope,
        colors    = scope_colors,
        type      = "scatter",
        mode      = "lines+markers",
        text      = ~hover_text,
        hoverinfo = "text",
        line      = list(width = 2),
        marker    = list(size = 6)
      ) |>
        layout(
          xaxis = list(title = "Year", dtick = 5),
          yaxis = list(
            title      = "Emissions Index (Base Year = 100)",
            range      = c(0, 110),
            zeroline   = FALSE
          ),
          shapes = list(list(
            type  = "line",
            x0 = 0, x1 = 1, xref = "paper",
            y0 = 100, y1 = 100, yref = "y",
            line  = list(dash = "dot", color = "#adb5bd", width = 1)
          )),
          legend    = list(title = list(text = "<b>Scope</b>")),
          hovermode = "closest"
        )
    })

    # ── Target wording table ──────────────────────────────────────────────────

    output$tbl_wording <- DT::renderDataTable({

      if (is.null(input$company_names) || length(input$company_names) == 0) {
        return(
          data.frame(Message = "Select a company to see target details.") |>
            DT::datatable(rownames = FALSE, options = list(dom = "t"))
        )
      }

      filtered_data() |>
        arrange(company_name, scope, target_year) |>
        transmute(
          Company        = company_name,
          Scope          = scope,
          Type           = type,
          Classification = coalesce(target_classification_short, "\u2014"),
          `Base Year`    = base_year,
          `Target Year`  = target_year,
          `Reduction`    = case_when(
            is.na(target_value)  ~ "\u2014",
            target_value > 1     ~ paste0(round(target_value * 100, 1), "% (outlier)"),
            TRUE                 ~ percent(target_value, accuracy = 0.1)
          ),
          `Target Wording` = coalesce(target_wording, full_target_language, "\u2014")
        ) |>
        DT::datatable(
          rownames      = FALSE,
          fillContainer = TRUE,
          options = list(
            scrollX    = TRUE,
            scrollY    = "350px",
            pageLength = 25,
            dom        = "tip",
            columnDefs = list(
              list(width = "160px", targets = 0),  # Company
              list(width = "60px",  targets = 1),  # Scope
              list(width = "400px", targets = 7)   # Target Wording
            )
          )
        )
    })
  })
}
