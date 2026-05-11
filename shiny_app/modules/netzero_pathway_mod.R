library(shiny)
library(bslib)
library(plotly)
library(dplyr)
library(tidyr)

source("../R/utils/sector_mapping.R")

# ── Colour palette ────────────────────────────────────────────────────────────

source_colors <- c(
  "UK CCC"      = "#1f78b4",
  "SNBC"        = "#33a02c",
  "IEA WEO2025" = "#e31a1c",
  "SBTi"        = "#ff7f00"
)

company_color <- "#984ea3"
company_colors <- c(
  "#984ea3", "#377eb8", "#4daf4a", "#e41a1c", "#ff7f00",
  "#a65628", "#f781bf", "#17becf", "#bcbd22", "#7f7f7f"
)

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

# ── Per-source lookup tables (rebuilt each time the module is sourced) ────────

uk_map_nz   <- sector_mapping |> filter(source == "UK CCC")
snbc_map_nz <- sector_mapping |> filter(source == "SNBC")
iea_map_nz  <- sector_mapping |> filter(source == "IEA WEO2025")
sbti_map_nz <- sector_mapping |> filter(source == "SBTi")

# Sources that only show macro-level data — dimmed when a subsector is selected
macro_only_sources <- c("SNBC", "IEA WEO2025")

pathway_sbti_scope_choices <- c("1", "2", "1+2", "1+3", "2+3", "3", "1+2+3")

# ── UI ────────────────────────────────────────────────────────────────────────

netZeroPathwayUI <- function(id) {
  ns <- NS(id)

  tagList(
    card(
      full_screen = TRUE,
      min_height  = "420px",
      card_header(
        class = "d-flex justify-content-between align-items-center",
        "Net-zero Pathway — Indexed Emissions (2025 = 100)",
        uiOutput(ns("chart_subtitle"))
      ),
      uiOutput(ns("notes")),
      plotlyOutput(ns("plot_pathway"), height = "400px")
    )
  )
}

# ── Server ────────────────────────────────────────────────────────────────────

netZeroPathwayServer <- function(id, uk_ccc_data, snbc_data, iea_data, sbti_data) {

  moduleServer(id, function(input, output, session) {

    # Server-side source of truth for highlighted companies.
    # Using a reactiveVal avoids the race condition where input$sbti_companies
    # hasn't reflected a new selection by the time the choices observe fires.
    highlighted_cos <- reactiveVal(character(0))

    # Sync user manual changes (add / remove) back into the reactiveVal.
    observeEvent(input$sbti_companies, {
      highlighted_cos(input$sbti_companies %||% character(0))
    }, ignoreNULL = FALSE, ignoreInit = TRUE)

    # ── Reactive: macro pathway data (UK CCC, SNBC, IEA) ─────────────────────

    combined_data <- reactive({
      sel_sector    <- req(input$sector)
      sel_subsector <- input$subsector %||% "All"
      sel_sources   <- input$sources   %||% character(0)
      dfs <- list()

      # ── UK CCC — subsector filter with macro fallback ──
      if ("UK CCC" %in% sel_sources) {
        uk_rows_sub <- if (sel_subsector == "All") {
          uk_map_nz |> filter(harmonized_sector == sel_sector)
        } else {
          uk_map_nz |> filter(harmonized_sector == sel_sector,
                               harmonized_subsector == sel_subsector)
        }
        # If subsector selected but no UK CCC match, fall back to full macro sector
        uk_fallback <- sel_subsector != "All" && nrow(uk_rows_sub) == 0
        uk_rows     <- if (uk_fallback) {
          uk_map_nz |> filter(harmonized_sector == sel_sector)
        } else {
          uk_rows_sub
        }
        uk_sectors <- uk_rows |> pull(original_sector)

        if (length(uk_sectors) > 0) {
          dfs[["UK CCC"]] <- uk_ccc_data |>
            filter(sector %in% uk_sectors, scenario == "Balanced Pathway") |>
            group_by(scenario, year) |>
            summarise(value = sum(value), .groups = "drop") |>
            mutate(source = "UK CCC", unit = "MtCO2e",
                   period_label = as.character(year),
                   at_macro = uk_fallback)
        }
      }

      # ── SNBC — always macro level ──
      if ("SNBC" %in% sel_sources) {
        snbc_sectors <- snbc_map_nz |>
          filter(harmonized_sector == sel_sector) |>
          pull(original_sector)

        if (length(snbc_sectors) > 0) {
          dfs[["SNBC"]] <- snbc_data |>
            filter(sector %in% snbc_sectors) |>
            transmute(source, scenario, year, value, unit, period_label)
        }
      }

      # ── IEA WEO2025 — always macro level ──
      if ("IEA WEO2025" %in% sel_sources) {
        iea_sectors <- iea_map_nz |>
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

    # ── Reactive: index macro pathways to 2025 = 100 ─────────────────────────

    plot_data <- reactive({
      df <- combined_data()
      if (nrow(df) == 0) return(df)

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

    # ── Reactive: SBTi aggregate + individual trajectories ───────────────────

    sbti_trajectory <- reactive({
      sel_sector    <- req(input$sector)
      sel_subsector <- input$subsector    %||% "All"
      sel_scopes    <- input$sbti_scope   %||% pathway_sbti_scope_choices
      sel_sources   <- input$sources      %||% character(0)

      empty <- list(agg = tibble(), ind = tibble(), n_targets = 0L, n_cos = 0L)
      if (!"SBTi" %in% sel_sources || length(sel_scopes) == 0) return(empty)

      # 1. SBTi sector lookup (respects subsector)
      sbti_rows <- if (sel_subsector == "All") {
        sbti_map_nz |> filter(harmonized_sector == sel_sector)
      } else {
        sbti_map_nz |> filter(harmonized_sector == sel_sector,
                               harmonized_subsector == sel_subsector)
      }
      sbti_sectors <- sbti_rows |> pull(original_sector)
      if (length(sbti_sectors) == 0) return(empty)

      # 2. Filter targets
      targets <- sbti_data |>
        filter(
          sector %in% sbti_sectors,
          type == "Absolute",
          commitment_status %in% c("Active", "Target set") | is.na(commitment_status),
          scope %in% sel_scopes,
          !is.na(target_value), !is.na(base_year), !is.na(target_year),
          base_year <= 2025, target_year >= 2026
        )

      if (nrow(targets) == 0) return(empty)

      # 3. Build normalized trajectory for each target row
      years <- 2023:2050

      build_traj <- function(base_yr, tgt_yr, tgt_val) {
        pts_x  <- c(base_yr, tgt_yr)
        pts_y  <- c(100, (1 - tgt_val) * 100)
        vals   <- suppressWarnings(approx(pts_x, pts_y, xout = years, rule = 1)$y)
        val_25 <- suppressWarnings(approx(pts_x, pts_y, xout = 2025, rule = 1)$y)
        if (is.na(val_25) || val_25 == 0) return(tibble(year = years, value = NA_real_))
        tibble(year = years, value = vals / val_25 * 100)
      }

      targets_indexed <- targets |>
        mutate(row_id = row_number()) |>
        rowwise() |>
        mutate(traj = list(build_traj(base_year, target_year, target_value))) |>
        ungroup() |>
        unnest(traj) |>
        filter(!is.na(value))

      if (nrow(targets_indexed) == 0) return(empty)

      # 4. Aggregate: median + IQR by year
      agg <- targets_indexed |>
        group_by(year) |>
        summarise(
          q25       = quantile(value, 0.25, na.rm = TRUE),
          median    = median(value,          na.rm = TRUE),
          q75       = quantile(value, 0.75, na.rm = TRUE),
          n_targets = n_distinct(row_id),
          .groups   = "drop"
        )

      # 5. Individual company trajectories — one stitched line per
      # (company, scope, base_year), so targets with different baselines do not
      # get merged into a single trajectory.
      sel_cos <- input$sbti_companies %||% character(0)
      ind <- if (length(sel_cos) > 0) {
        co_targets <- targets |>
          filter(company_name %in% sel_cos) |>
          arrange(company_name, scope, base_year, target_year)

        if (nrow(co_targets) == 0) {
          tibble()
        } else {
          co_targets |>
            group_by(company_name, scope, base_year) |>
            summarise(
              ord      = list(order(target_year)),
              all_yrs  = list(c(first(base_year), target_year[ord[[1]]])),
              all_vals = list(c(100, 100 * (1 - target_value[order(target_year)]))),
              .groups  = "drop"
            ) |>
            select(-ord) |>
            rowwise() |>
            mutate(
              val_25 = suppressWarnings(approx(all_yrs, all_vals, xout = 2025, rule = 1)$y),
              pts = if (!is.na(val_25) && val_25 != 0) {
                list(tibble(
                  year = all_yrs,
                  value = all_vals / val_25 * 100,
                  target_vs_base = all_vals - 100
                ))
              } else {
                list(tibble(
                  year = double(),
                  value = double(),
                  target_vs_base = double()
                ))
              }
            ) |>
            ungroup() |>
            select(company_name, scope, base_year, pts) |>
            unnest(pts) |>
            filter(!is.na(year)) |>
            group_by(company_name, scope) |>
            mutate(has_multiple_base_years = n_distinct(base_year) > 1) |>
            ungroup()
        }
      } else {
        tibble()
      }

      list(
        agg      = agg,
        ind      = ind,
        n_targets = nrow(targets),
        n_cos    = n_distinct(targets$company_name)
      )
    })

    # ── Find company: update sector / subsector / sources / highlights ───────────

    observeEvent(input$find_company, {
      co <- input$find_company
      if (is.null(co) || length(co) == 0 || co[[1]] == "") return()
      co <- co[[1]]

      co_sectors <- sbti_data |>
        filter(company_name == co, !is.na(sector)) |>
        count(sector, sort = TRUE)
      if (nrow(co_sectors) == 0) {
        updateSelectizeInput(session, "find_company",
                             choices  = sort(unique(sbti_data$company_name[!is.na(sbti_data$company_name)])),
                             selected = character(0), server = TRUE)
        return()
      }

      mapping <- sbti_map_nz |>
        filter(original_sector %in% co_sectors$sector) |>
        left_join(co_sectors, by = c("original_sector" = "sector")) |>
        arrange(desc(n)) |>
        slice(1)
      if (nrow(mapping) == 0) {
        updateSelectizeInput(session, "find_company",
                             choices  = sort(unique(sbti_data$company_name[!is.na(sbti_data$company_name)])),
                             selected = character(0), server = TRUE)
        return()
      }

      best_sector    <- mapping$harmonized_sector
      best_subsector <- mapping$harmonized_subsector

      updateSelectInput(session, "sector", selected = best_sector)

      sub_choices <- c("All", get_subsectors(best_sector))
      sel_sub <- if (!is.na(best_subsector) && best_subsector %in% sub_choices) {
        best_subsector
      } else {
        "All"
      }
      updateSelectInput(session, "subsector", choices = sub_choices, selected = sel_sub)

      company_rows <- if (sel_sub == "All") {
        sbti_map_nz |> filter(harmonized_sector == best_sector)
      } else {
        sbti_map_nz |> filter(harmonized_sector == best_sector,
                               harmonized_subsector == sel_sub)
      }
      company_sectors <- company_rows |> pull(original_sector)
      company_scopes <- sbti_data |>
        filter(
          company_name == co,
          sector %in% company_sectors,
          type == "Absolute",
          commitment_status %in% c("Active", "Target set") | is.na(commitment_status),
          !is.na(target_value), !is.na(base_year), !is.na(target_year),
          base_year <= 2025, target_year >= 2026
        ) |>
        pull(scope) |>
        unique()

      cur_sources <- input$sources %||% character(0)
      if (!"SBTi" %in% cur_sources) {
        updateCheckboxGroupInput(session, "sources", selected = c(cur_sources, "SBTi"))
      }

      cur_scopes <- input$sbti_scope %||% character(0)
      next_scopes <- unique(c(cur_scopes, company_scopes))
      if (length(next_scopes) > 0 && !setequal(cur_scopes, next_scopes)) {
        updateCheckboxGroupInput(session, "sbti_scope", selected = next_scopes)
      }

      if (length(company_scopes) > 0) {
        # Add company to server-side highlighted list.
        # The company choices observe will pick this up and pass it as `selected`
        # in the same updateSelectizeInput call where it sets the new choices.
        cur_highlighted <- highlighted_cos()
        if (!co %in% cur_highlighted) {
          highlighted_cos(unique(c(cur_highlighted, co)))
        }
      }

      # Reset the Find company input so it acts like a "jump" control
      updateSelectizeInput(session, "find_company",
                           choices  = sort(unique(sbti_data$company_name[!is.na(sbti_data$company_name)])),
                           selected = character(0), server = TRUE)
    })

    # ── Dynamic company choices (update when sector / subsector / scope changes) ─

    observeEvent(
      list(input$sector, input$subsector, input$sbti_scope),
    {
      sel_sector      <- req(input$sector)
      sel_subsector   <- input$subsector  %||% "All"
      sel_scopes      <- input$sbti_scope %||% pathway_sbti_scope_choices
      cur_highlighted <- isolate(highlighted_cos())

      sbti_rows <- if (sel_subsector == "All") {
        sbti_map_nz |> filter(harmonized_sector == sel_sector)
      } else {
        sbti_map_nz |> filter(harmonized_sector == sel_sector,
                               harmonized_subsector == sel_subsector)
      }
      sbti_sectors <- sbti_rows |> pull(original_sector)

      cos <- sbti_data |>
        filter(
          sector %in% sbti_sectors,
          type == "Absolute",
          commitment_status %in% c("Active", "Target set") | is.na(commitment_status),
          scope %in% sel_scopes
        ) |>
        pull(company_name) |>
        unique() |>
        sort()

      # Always pass selected= so choices refresh never wipes the current highlights
      keep_sel <- intersect(cur_highlighted, cos)
      if (!setequal(cur_highlighted, keep_sel)) highlighted_cos(keep_sel)

      updateSelectizeInput(session, "sbti_companies",
                           choices  = cos,
                           selected = keep_sel,
                           server   = TRUE)
    }, ignoreNULL = FALSE)

    # ── UI: notes panel ───────────────────────────────────────────────────────

    output$notes <- renderUI({
      sel_sector    <- req(input$sector)
      sel_subsector <- input$subsector %||% "All"
      sel_sources   <- input$sources   %||% character(0)
      sel_scopes    <- input$sbti_scope %||% character(0)

      msgs <- list()

      # Subsector note: SNBC and IEA show macro only
      if (sel_subsector != "All") {
        msgs <- c(msgs, list(tags$p(
          class = "text-muted small mb-1",
          paste0(
            "SNBC and IEA WEO2025 are shown at sector level — ",
            "no ", sel_subsector, " subsector breakdown available."
          )
        )))
      }

      # UK CCC fallback: subsector selected but no UK CCC match
      if ("UK CCC" %in% sel_sources && sel_subsector != "All") {
        uk_sub_exists <- nrow(
          uk_map_nz |> filter(harmonized_sector == sel_sector,
                               harmonized_subsector == sel_subsector)
        ) > 0
        if (!uk_sub_exists) {
          msgs <- c(msgs, list(tags$p(
            class = "text-muted small mb-1",
            paste0("UK CCC has no ", sel_subsector,
                   " subsector — showing full ", sel_sector, " sector as reference.")
          )))
        }
      }

      # IEA coverage
      iea_covered <- iea_map_nz |> pull(harmonized_sector) |> unique()
      if ("IEA WEO2025" %in% sel_sources && !sel_sector %in% iea_covered) {
        msgs <- c(msgs, list(tags$p(
          class = "text-muted small mb-1",
          paste0(
            "IEA WEO2025 Annex A does not include ", sel_sector,
            " — only Transport, Buildings, Industry, and Energy are available."
          )
        )))
      }

      # IEA unit mismatch
      if ("IEA WEO2025" %in% sel_sources && sel_sector %in% iea_covered) {
        msgs <- c(msgs, list(tags$p(
          class = "text-muted small mb-1",
          "IEA values are in Mt CO₂ (combustion); UK CCC and SNBC values are in MtCO₂e."
        )))
      }

      # SBTi target count
      if ("SBTi" %in% sel_sources) {
        sbti_res <- sbti_trajectory()
        if (sbti_res$n_targets > 0) {
          scope_str <- paste(sel_scopes, collapse = " / ")
          msgs <- c(msgs, list(tags$p(
            class = "text-muted small mb-1",
            paste0(
              "SBTi: ", sbti_res$n_targets, " Absolute target",
              if (sbti_res$n_targets != 1) "s" else "",
              " from ", sbti_res$n_cos, " compan",
              if (sbti_res$n_cos != 1) "ies" else "y",
              " (scope ", scope_str, ")."
            )
          )))
        } else {
          sbti_rows <- if (sel_subsector == "All") {
            sbti_map_nz |> filter(harmonized_sector == sel_sector)
          } else {
            sbti_map_nz |> filter(harmonized_sector == sel_sector,
                                   harmonized_subsector == sel_subsector)
          }
          if (nrow(sbti_rows) == 0) {
            msgs <- c(msgs, list(tags$p(
              class = "text-muted small mb-1",
              paste0(
                "No SBTi sector mapping for ", sel_sector,
                if (sel_subsector != "All") paste0(" / ", sel_subsector) else "",
                "."
              )
            )))
          } else {
            msgs <- c(msgs, list(tags$p(
              class = "text-muted small mb-1",
              paste0(
                "No SBTi Absolute targets found for the current selection ",
                "(scope: ", paste(sel_scopes, collapse = " / "), ")."
              )
            )))
          }
        }
      }

      if (length(msgs) == 0) return(NULL)
      div(class = "px-3 pb-1", msgs)
    })

    # ── Dynamic subtitle ──────────────────────────────────────────────────────

    output$chart_subtitle <- renderUI({
      sel_subsector <- input$subsector %||% "All"
      label <- if (sel_subsector == "All") {
        req(input$sector)
      } else {
        paste0(req(input$sector), " — ", sel_subsector)
      }
      tags$span(class = "text-muted small", label)
    })

    # ── Main chart ────────────────────────────────────────────────────────────

    output$plot_pathway <- renderPlotly({

      macro_df      <- plot_data()
      sbti_res      <- sbti_trajectory()
      sel_sector    <- req(input$sector)
      sel_subsector <- input$subsector %||% "All"
      in_subsector  <- sel_subsector != "All"

      # UK CCC lacks some subsectors (e.g. Chemicals) — detect fallback case
      uk_ccc_has_subsector <- !in_subsector || nrow(
        uk_map_nz |> filter(harmonized_sector == sel_sector,
                             harmonized_subsector == sel_subsector)
      ) > 0

      has_macro <- nrow(macro_df) > 0
      has_sbti  <- nrow(sbti_res$agg) > 0

      if (!has_macro && !has_sbti) {
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

      p <- plot_ly()

      # ── Macro pathway traces (UK CCC / SNBC / IEA) ──
      if (has_macro) {
        groups <- macro_df |>
          arrange(source, scenario, year) |>
          group_by(source, scenario) |>
          group_split()

        for (grp in groups) {
          g_src  <- grp$source[[1]]
          g_scen <- grp$scenario[[1]]

          g_color   <- unname(source_colors[g_src])
          if (is.null(g_color) || is.na(g_color)) g_color <- "#999999"

          g_dash    <- unname(scenario_dash[g_scen])
          if (is.null(g_dash) || is.na(g_dash)) g_dash <- "solid"

          g_opacity <- unname(scenario_opacity[g_scen])
          if (is.null(g_opacity) || is.na(g_opacity)) g_opacity <- 1

          # Dim macro-only sources (SNBC, IEA) and UK CCC fallback when a subsector is selected
          is_macro_dim <- in_subsector && (
            g_src %in% macro_only_sources ||
            (g_src == "UK CCC" && !uk_ccc_has_subsector)
          )
          if (is_macro_dim) g_opacity <- g_opacity * 0.25

          trace_name <- if (g_src == "SNBC" || g_src == "IEA WEO2025") g_src else
            paste0(g_src, " — ", g_scen)

          hover_text <- paste0(
            "<b>", g_src, "</b><br>",
            if (!g_src %in% c("SNBC", "IEA WEO2025")) paste0("Scenario: ", g_scen, "<br>") else "",
            if (g_src == "SNBC" && !is.null(grp$period_label)) paste0("Period: ", grp$period_label, "<br>") else "",
            "Year: ", grp$year, "<br>",
            "Index: ", round(grp$value, 1)
          )

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
              line        = list(color = g_color, dash = g_dash, width = 2.5),
              marker      = list(color = g_color, size = 7)
            )
        }
      }

      # ── SBTi IQR ribbon + median line ──
      if (has_sbti) {
        agg <- sbti_res$agg

        # Lower bound of ribbon (transparent line, no legend)
        p <- p |>
          add_trace(
            data      = agg,
            x         = ~year, y = ~q25,
            type      = "scatter", mode = "lines",
            line      = list(color = "transparent"),
            showlegend = FALSE,
            hoverinfo  = "skip",
            legendgroup = "SBTi"
          )

        # Upper bound — fills to lower bound
        p <- p |>
          add_trace(
            data        = agg,
            x           = ~year, y = ~q75,
            type        = "scatter", mode = "lines",
            fill        = "tonexty",
            fillcolor   = "rgba(255,127,0,0.15)",
            line        = list(color = "transparent"),
            name        = "SBTi — IQR",
            legendgroup = "SBTi",
            legendgrouptitle = list(text = "<b>SBTi</b>"),
            text        = ~paste0(
              "<b>SBTi IQR</b><br>Year: ", year,
              "<br>25th– 75th pct: [", round(q25, 1), ", ", round(q75, 1), "]",
              "<br>n targets: ", n_targets
            ),
            hoverinfo   = "text"
          )

        # Median line
        p <- p |>
          add_trace(
            data        = agg,
            x           = ~year, y = ~median,
            type        = "scatter", mode = "lines",
            name        = "SBTi — Median",
            legendgroup = "SBTi",
            line        = list(color = "#ff7f00", width = 2.5),
            text        = ~paste0(
              "<b>SBTi Median</b><br>Year: ", year,
              "<br>Index: ", round(median, 1),
              "<br>IQR: [", round(q25, 1), ", ", round(q75, 1), "]",
              "<br>n targets: ", n_targets
            ),
            hoverinfo   = "text"
          )
      }

      # ── Individual company traces — one stitched line per (company, scope, base_year) ──
      if (nrow(sbti_res$ind) > 0) {
        company_palette <- setNames(
          rep(company_colors, length.out = n_distinct(sbti_res$ind$company_name)),
          sort(unique(sbti_res$ind$company_name))
        )

        ind_groups <- sbti_res$ind |>
          group_by(company_name, scope, base_year) |>
          group_split()

        for (co_grp in ind_groups) {
          co_name  <- co_grp$company_name[[1]]
          co_scope <- co_grp$scope[[1]]
          co_base  <- co_grp$base_year[[1]]
          has_multiple_base_years <- co_grp$has_multiple_base_years[[1]]
          trace_name <- if (isTRUE(has_multiple_base_years)) {
            paste0(co_name, " (s", co_scope, ", base ", co_base, ")")
          } else {
            paste0(co_name, " (s", co_scope, ")")
          }
          co_color <- unname(company_palette[co_name])

          co_grp <- co_grp |>
            mutate(
              target_label = paste0(
                ifelse(target_vs_base > 0, "+", ""),
                round(target_vs_base, 1),
                "%"
              ),
              index_label = paste0(
                ifelse(value - 100 > 0, "+", ""),
                round(value - 100, 1),
                "%"
              ),
              hover_text = paste0(
                "<b>", company_name, "</b><br>",
                "Base year: ", base_year, "<br>",
                "Year: ", year, "<br>",
                "Target: ", target_label, "<br>",
                "Index: ", index_label, " (vs. 2025)"
              )
            )

          p <- p |>
            add_trace(
              data        = co_grp,
              x           = ~year, y = ~value,
              type        = "scatter", mode = "lines+markers",
              name        = trace_name,
              legendgroup = "SBTi companies",
              legendgrouptitle = list(text = "<b>SBTi companies</b>"),
              line        = list(color = co_color, dash = "dot", width = 1.8),
              marker      = list(color = co_color, size = 7),
              text        = ~hover_text,
              hoverinfo   = "text"
            )
        }
      }

      # ── Horizontal reference line at 100 ──
      p |>
        layout(
          shapes = list(list(
            type    = "line",
            x0 = 2023, x1 = 2052,
            y0 = 100,  y1 = 100,
            line = list(color = "#6c757d", width = 1, dash = "dot")
          )),
          xaxis = list(
            title = "Year",
            dtick = 5,
            tick0 = 2025,
            range = list(2022, 2052)
          ),
          yaxis = list(
            title    = "Emissions Index (2025 = 100)",
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

`%||%` <- function(x, y) if (!is.null(x)) x else y
