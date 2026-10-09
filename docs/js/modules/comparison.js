// comparison.js — Sectoral Comparison tab
// Port of shiny_app/modules/comparison_mod.R: UK CCC vs SNBC vs IEA WEO2025
// for one harmonized sector, absolute or indexed to 2025.

import { $, checkboxGroup, radioValue, setSelectOptions, plot, emptyPlot, notes,
  SOURCE_COLORS, SCENARIO_DASH, SCENARIO_OPACITY } from "../lib/ui.js";
import { HARMONIZED_SECTORS, mapFor, groupBy, sortBy, approx, round } from "../lib/data.js";

export const SOURCES = ["UK CCC", "SNBC", "IEA WEO2025"];

// Rows for one harmonized sector, per source (shared with the pathway tab).
// UK CCC: Balanced Pathway, summed across the mapped CCC sectors.
export function macroSeries(data, source, originalSectors) {
  if (originalSectors.length === 0) return [];
  const set = new Set(originalSectors);
  if (source === "UK CCC") {
    const g = groupBy(data.ukCcc.filter((r) => set.has(r.sector) && r.scenario === "Balanced Pathway"),
      (r) => r.year);
    return [...g.values()].map((grp) => ({
      source: "UK CCC", scenario: "Balanced Pathway", year: grp[0].year,
      value: grp.reduce((s, r) => s + r.value, 0), unit: "MtCO2e", period_label: String(grp[0].year),
    }));
  }
  if (source === "SNBC") {
    return data.snbc.filter((r) => set.has(r.sector)).map((r) => ({
      source: r.source, scenario: r.scenario, year: r.year, value: r.value, unit: r.unit, period_label: r.period_label,
    }));
  }
  return data.iea.filter((r) => set.has(r.sector)).map((r) => ({
    source: r.source, scenario: r.scenario, year: r.year, value: r.value, unit: r.unit, period_label: String(r.year),
  }));
}

// Index every (source, scenario) series to 2025 = 100. SNBC (2023/2026) and IEA
// (2023/2035) have no 2025 observation: their base is linearly interpolated.
export function indexTo2025(df) {
  const groups = groupBy(df, (r) => `${r.source}|${r.scenario}`);
  const out = [];
  for (const grp of groups.values()) {
    const base = approx(grp.map((r) => r.year), grp.map((r) => r.value), 2025);
    if (Number.isNaN(base) || base === 0) continue;
    grp.forEach((r) => out.push({ ...r, value: r.value / base * 100 }));
  }
  return out;
}

export function initComparison(data) {
  const plotEl = $("comp-plot");
  const sectorEl = $("comp-sector");
  const ukMap = mapFor(data.sectorMapping, "UK CCC");
  const snbcMap = mapFor(data.sectorMapping, "SNBC");
  const ieaMap = mapFor(data.sectorMapping, "IEA WEO2025");
  const sourceMaps = { "UK CCC": ukMap, "SNBC": snbcMap, "IEA WEO2025": ieaMap };
  const ieaCovered = new Set(ieaMap.map((r) => r.harmonized_sector));

  setSelectOptions(sectorEl, HARMONIZED_SECTORS, "Transport");
  sectorEl.addEventListener("change", update);
  const sources = checkboxGroup($("comp-sources"), "comp-source", SOURCES, SOURCES, update);
  document.querySelectorAll('input[name="comp-display"]').forEach((el) => el.addEventListener("change", update));

  function update() {
    const sector = sectorEl.value;
    const selSources = sources.get();
    const absolute = radioValue("comp-display") === "absolute";

    // Coverage / unit notes
    const msgs = [];
    if (selSources.includes("IEA WEO2025") && !ieaCovered.has(sector)) {
      msgs.push(`IEA WEO2025 Annex A does not include ${sector} — only Transport, Buildings, Industry, and Energy are available.`);
    }
    if (selSources.includes("IEA WEO2025") && ieaCovered.has(sector)) {
      msgs.push("IEA values are in Mt CO₂ (combustion); UK CCC and SNBC values are in MtCO₂e.");
    }
    notes($("comp-notes"), msgs);
    $("comp-subtitle").textContent = absolute ? "Absolute emissions" : "Indexed (2025 = 100)";

    let df = [];
    for (const src of SOURCES) {
      if (!selSources.includes(src)) continue;
      const orig = sourceMaps[src].filter((r) => r.harmonized_sector === sector).map((r) => r.original_sector);
      df = df.concat(macroSeries(data, src, orig));
    }
    if (!absolute && df.length > 0) df = indexTo2025(df);

    if (df.length === 0) {
      emptyPlot(plotEl, "No data for the current selection.");
      return;
    }

    const groups = groupBy(sortBy(df, "source", "scenario", "year"), (r) => `${r.source}|${r.scenario}`);
    const traces = [...groups.values()].map((grp) => {
      const src = grp[0].source, scen = grp[0].scenario;
      const color = SOURCE_COLORS[src] ?? "#999999";
      return {
        x: grp.map((r) => r.year),
        y: grp.map((r) => r.value),
        text: grp.map((r) => `<b>${src}</b><br>Scenario: ${scen}<br>Period: ${r.period_label}<br>` +
          `Year: ${r.year}<br>Value: ${absolute ? `${round(r.value, 1)} ${r.unit}` : round(r.value, 1)}`),
        name: `${src} — ${scen}`,
        type: "scatter",
        mode: src === "SNBC" ? "lines+markers" : "lines",   // SNBC data are sparse
        hoverinfo: "text",
        legendgroup: src,
        legendgrouptitle: { text: `<b>${src}</b>` },
        opacity: SCENARIO_OPACITY[scen] ?? 1,
        line: { color, dash: SCENARIO_DASH[scen] ?? "solid", width: 2.5 },
        marker: { color, size: 7 },
      };
    });

    plot(plotEl, traces, {
      xaxis: { title: { text: "Year" }, dtick: 5, tick0: 2025, range: [2022, 2052] },
      yaxis: {
        title: { text: absolute ? "Emissions (MtCO₂e / Mt CO₂)" : "Emissions Index (2025 = 100)" },
        zeroline: true, zerolinecolor: "#6c757d", zerolinewidth: 1,
      },
      legend: { groupclick: "toggleitem", itemsizing: "constant" },
      hovermode: "closest",
    });
  }

  update();
}
