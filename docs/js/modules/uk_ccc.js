// uk_ccc.js — UK Carbon Budget tab
// Port of shiny_app/modules/uk_ccc_mod.R: UK CCC 7th Carbon Budget emissions
// trajectories at sector or subsector level, absolute or indexed to 2025.

import { $, checkboxGroup, searchSelect, radioValue, plot, emptyPlot } from "../lib/ui.js";
import { uniqueSorted, groupBy, sortBy, round, escapeHtml } from "../lib/data.js";

// Fixed palette per sector (sector-level view)
const SECTOR_COLORS = {
  "Agriculture": "#4e9a51",
  "Aviation": "#6baed6",
  "Electricity supply": "#f7c948",
  "Engineered removals": "#984ea3",
  "F-gases": "#ff7f00",
  "Fuel supply": "#e7969c",
  "Industry": "#1f78b4",
  "Land use": "#33a02c",
  "Non-residential buildings": "#a6761d",
  "Residential buildings": "#e6550d",
  "Shipping": "#74c476",
  "Surface transport": "#d62728",
  "Waste": "#8c564b",
};

// 24-colour qualitative palette for the subsector-level view (Tableau-inspired)
const QUALITATIVE_PALETTE = [
  "#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f",
  "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#bab0ac",
  "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
  "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
  "#aec7e8", "#ffbb78", "#98df8a", "#ff9896",
];

export function initUkCcc(data) {
  const rows = data.ukCcc;
  const sectorChoices = uniqueSorted(rows.map((r) => r.sector));

  const plotEl = $("ukccc-plot");
  const subtitleEl = $("ukccc-subtitle");
  const subsectorWrap = $("ukccc-subsector-wrap");

  const level = () => radioValue("ukccc-level");
  const displayMode = () => radioValue("ukccc-display");

  const sectors = checkboxGroup($("ukccc-sectors"), "ukccc-sector", sectorChoices, sectorChoices, () => {
    refreshSubsectors();
    update();
  });
  const subsectors = searchSelect($("ukccc-subsectors"), [], {
    placeholder: "Select subsectors…",
    onChange: () => update(),
  });

  // Subsectors of the currently selected sectors (all sectors if none checked)
  function availableSubsectors() {
    const sel = new Set(sectors.get());
    return uniqueSorted(rows.filter((r) => sel.size === 0 || sel.has(r.sector)).map((r) => r.subsector));
  }

  // Refresh subsector choices (all selected) whenever sectors or level change
  function refreshSubsectors() {
    const subs = availableSubsectors();
    subsectors.clear(true);
    subsectors.clearOptions();
    subsectors.addOptions(subs.map((s) => ({ value: s, text: s })));
    subsectors.setValue(subs, true);
  }

  document.querySelectorAll('input[name="ukccc-level"]').forEach((el) => el.addEventListener("change", () => {
    subsectorWrap.classList.toggle("d-none", level() !== "Subsector");
    if (level() === "Subsector") refreshSubsectors();
    update();
  }));
  document.querySelectorAll('input[name="ukccc-display"]').forEach((el) => el.addEventListener("change", update));

  // Aggregate to the chosen level: Sector sums subsectors per scenario × year
  function aggregated() {
    const sel = new Set(sectors.get());
    const subSel = new Set(subsectors.getValue());
    let df = rows.filter((r) => sel.size === 0 || sel.has(r.sector));
    if (level() === "Subsector" && subSel.size > 0) df = df.filter((r) => subSel.has(r.subsector));

    if (level() === "Sector") {
      const g = groupBy(df, (r) => `${r.scenario}|${r.sector}|${r.year}`);
      return [...g.values()].map((grp) => ({
        scenario: grp[0].scenario, group_label: grp[0].sector, year: grp[0].year,
        value: grp.reduce((s, r) => s + r.value, 0),
      }));
    }
    return df.map((r) => ({ scenario: r.scenario, group_label: r.subsector, sector: r.sector, year: r.year, value: r.value }));
  }

  // Indexed mode: each group indexed to its own 2025 value. Groups with a zero
  // 2025 baseline are dropped (e.g. engineered removals ramping up from zero).
  function plotData(agg) {
    if (displayMode() === "absolute") return agg;
    const base = new Map();
    agg.filter((r) => r.year === 2025).forEach((r) => base.set(`${r.scenario}|${r.group_label}`, r.value));
    return agg
      .filter((r) => { const b = base.get(`${r.scenario}|${r.group_label}`); return b !== undefined && b !== 0; })
      .map((r) => ({ ...r, value: r.value / base.get(`${r.scenario}|${r.group_label}`) * 100 }));
  }

  function renderSubtitle(agg) {
    const levelLabel = level() === "Sector" ? "Sector level" : "Subsector level";
    const modeLabel = displayMode() === "absolute" ? "MtCO2e" : "Indexed (2025 = 100)";
    let html = `<span class="text-muted small">${levelLabel} · ${modeLabel}</span>`;
    if (displayMode() === "relative") {
      const at2025 = groupBy(agg.filter((r) => r.year === 2025), (r) => `${r.scenario}|${r.group_label}`);
      const excluded = new Set([...at2025.values()]
        .filter((g) => g.reduce((s, r) => s + r.value, 0) === 0)
        .map((g) => g[0].group_label));
      if (excluded.size > 0) {
        html += `<small class="text-muted ms-2">(${excluded.size} group(s) with zero 2025 baseline hidden)</small>`;
      }
    }
    subtitleEl.innerHTML = html;
  }

  function update() {
    const agg = aggregated();
    const df = plotData(agg);
    renderSubtitle(agg);

    if (df.length === 0) {
      emptyPlot(plotEl, "No data for the current selection.");
      return;
    }

    const isSector = level() === "Sector";
    const absolute = displayMode() === "absolute";

    // Stable colours for all subsectors of the selected sectors, so toggling
    // individual subsectors does not reshuffle the palette
    const subColors = {};
    availableSubsectors().forEach((s, i) => { subColors[s] = QUALITATIVE_PALETTE[i % QUALITATIVE_PALETTE.length]; });

    const groups = groupBy(sortBy(df, "group_label", "scenario", "year"), (r) => `${r.group_label}|${r.scenario}`);
    const traces = [...groups.values()].map((grp) => {
      const label = grp[0].group_label;
      const scen = grp[0].scenario;
      const color = (isSector ? SECTOR_COLORS[label] : subColors[label]) ?? "#999999";
      const balanced = scen === "Balanced Pathway";
      return {
        x: grp.map((r) => r.year),
        y: grp.map((r) => r.value),
        text: grp.map((r) => `<b>${escapeHtml(label)}</b><br>Scenario: ${scen}<br>Year: ${r.year}<br>` +
          `Value: ${absolute ? `${round(r.value, 2)} MtCO2e` : round(r.value, 1)}`),
        name: label,
        type: "scatter", mode: "lines", hoverinfo: "text",
        // Only Balanced Pathway gets a legend entry; legendgroup toggles both scenarios
        showlegend: balanced,
        legendgroup: label,
        opacity: balanced ? 1 : 0.6,
        line: { color, dash: balanced ? "solid" : "dash", width: balanced ? 2.5 : 1.5 },
      };
    });

    const refY = absolute ? 0 : 100;
    plot(plotEl, traces, {
      xaxis: { title: { text: "Year" }, dtick: 5, tick0: 2025, range: [2024, 2051] },
      yaxis: {
        title: { text: absolute ? "Emissions (MtCO2e)" : "Emissions Index (2025 = 100)" },
        range: absolute ? null : [0, 200],
        autorange: absolute,
        zeroline: true, zerolinecolor: "#6c757d", zerolinewidth: 1,
      },
      shapes: [{
        type: "line", x0: 0, x1: 1, xref: "paper", y0: refY, y1: refY, yref: "y",
        line: { dash: "dot", color: "#adb5bd", width: 1 },
      }],
      legend: { title: { text: isSector ? "<b>Sector</b>" : "<b>Subsector</b>" }, itemsizing: "constant" },
      // unified hover reads well with 13 sectors; closest is cleaner with ~60 subsectors
      hovermode: isSector ? "x unified" : "closest",
    });
  }

  update();
}
