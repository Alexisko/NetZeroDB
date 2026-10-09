// pathway.js — Net-zero Pathway tab
// Port of shiny_app/modules/netzero_pathway_mod.R: macro pathways (UK CCC,
// SNBC, IEA) indexed to 2025 = 100, overlaid with the distribution (median +
// IQR) of SBTi absolute targets and optional individual company trajectories.

import { $, checkboxGroup, searchSelect, setSelectOptions, plot, emptyPlot, notes,
  SOURCE_COLORS, SCENARIO_DASH, SCENARIO_OPACITY } from "../lib/ui.js";
import { PATHWAY_SECTOR_CHOICES, mapFor, getSubsectors, groupBy, sortBy, uniqueSorted,
  approx, quantile, round, escapeHtml } from "../lib/data.js";
import { macroSeries, indexTo2025 } from "./comparison.js";

const SOURCES = ["UK CCC", "SNBC", "IEA WEO2025", "SBTi"];
const SCOPE_CHOICES = ["1", "2", "1+2", "1+3", "2+3", "3", "1+2+3"];
const MACRO_ONLY_SOURCES = ["SNBC", "IEA WEO2025"];   // dimmed when a subsector is selected
const YEARS = Array.from({ length: 2050 - 2023 + 1 }, (_, i) => 2023 + i);
const COMPANY_COLORS = [
  "#984ea3", "#377eb8", "#4daf4a", "#e41a1c", "#ff7f00",
  "#a65628", "#f781bf", "#17becf", "#bcbd22", "#7f7f7f",
];

// Targets eligible for the SBTi distribution: absolute, active / set (or no
// commitment status), base year ≤ 2025 and target year ≥ 2026.
function isEligible(r) {
  return r.type === "Absolute" &&
    (r.commitment_status === "Active" || r.commitment_status === "Target set" || r.commitment_status === null);
}
function hasWindow(r) {
  return r.target_value !== null && r.base_year !== null && r.target_year !== null &&
    r.base_year <= 2025 && r.target_year >= 2026;
}

export function initPathway(data) {
  const mapping = data.sectorMapping;
  const ukMap = mapFor(mapping, "UK CCC");
  const snbcMap = mapFor(mapping, "SNBC");
  const ieaMap = mapFor(mapping, "IEA WEO2025");
  const sbtiMap = mapFor(mapping, "SBTi");
  const ieaCovered = new Set(ieaMap.map((r) => r.harmonized_sector));
  const sbtiEligible = data.sbti.filter(isEligible);

  const plotEl = $("pathway-plot");
  const sectorEl = $("pathway-sector");
  const subsectorEl = $("pathway-subsector");
  const sbtiControls = $("pathway-sbti-controls");

  let highlighted = [];   // source of truth for highlighted companies
  let syncing = false;    // guards programmatic updates of the highlight select

  // ── Controls ───────────────────────────────────────────────────────────────

  setSelectOptions(sectorEl, PATHWAY_SECTOR_CHOICES, "Transport");
  setSubsectorChoices("Transport", "All");

  const sources = checkboxGroup($("pathway-sources"), "pathway-source", SOURCES, SOURCES, () => {
    sbtiControls.classList.toggle("d-none", !sources.get().includes("SBTi"));
    update();
  });
  const scopes = checkboxGroup($("pathway-scope"), "pathway-scope", SCOPE_CHOICES, SCOPE_CHOICES, () => {
    refreshCompanyChoices();
    update();
  });
  const companies = searchSelect($("pathway-companies"), [], {
    placeholder: "Search companies…",
    onChange: () => {
      if (syncing) return;
      highlighted = companies.getValue();
      update();
    },
  });
  const finder = searchSelect($("pathway-find"), data.sbtiCompanies, {
    multiple: false,
    placeholder: "Search a company…",
    onChange: (val) => { if (val) findCompany(val); },
  });

  sectorEl.addEventListener("change", () => {
    setSubsectorChoices(sectorEl.value, subsectorEl.value);
    refreshCompanyChoices();
    update();
  });
  subsectorEl.addEventListener("change", () => { refreshCompanyChoices(); update(); });

  function setSubsectorChoices(sector, current) {
    const choices = ["All", ...getSubsectors(mapping, sector)];
    setSelectOptions(subsectorEl, choices, choices.includes(current) ? current : "All");
  }

  const selScopes = () => { const s = scopes.get(); return s.length ? s : SCOPE_CHOICES; };

  function sbtiSectorsFor(sector, subsector) {
    return sbtiMap
      .filter((r) => r.harmonized_sector === sector && (subsector === "All" || r.harmonized_subsector === subsector))
      .map((r) => r.original_sector);
  }

  // Companies offered for highlighting follow the sector / subsector / scope filters
  function refreshCompanyChoices() {
    const sec = new Set(sbtiSectorsFor(sectorEl.value, subsectorEl.value));
    const sc = new Set(selScopes());
    const cos = uniqueSorted(sbtiEligible.filter((r) => sec.has(r.sector) && sc.has(r.scope)).map((r) => r.company_name));
    const cosSet = new Set(cos);
    highlighted = highlighted.filter((c) => cosSet.has(c));

    syncing = true;
    companies.clear(true);
    companies.clearOptions();
    companies.addOptions(cos.map((c) => ({ value: c, text: c })));
    companies.setValue(highlighted, true);
    syncing = false;
  }

  // ── Find company: jump to its sector / subsector and highlight it ─────────

  function findCompany(co) {
    const coRows = data.sbti.filter((r) => r.company_name === co && r.sector !== null);
    const counts = new Map();
    coRows.forEach((r) => counts.set(r.sector, (counts.get(r.sector) ?? 0) + 1));

    // Most frequent SBTi sector of the company (ties: mapping order)
    let best = null;
    for (const m of sbtiMap) {
      const n = counts.get(m.original_sector);
      if (n !== undefined && (best === null || n > best.n)) best = { ...m, n };
    }

    if (best) {
      sectorEl.value = best.harmonized_sector;
      const subChoices = ["All", ...getSubsectors(mapping, best.harmonized_sector)];
      const sub = best.harmonized_subsector && subChoices.includes(best.harmonized_subsector)
        ? best.harmonized_subsector : "All";
      setSubsectorChoices(best.harmonized_sector, sub);

      const coSectors = new Set(sbtiSectorsFor(best.harmonized_sector, sub));
      const coScopes = uniqueSorted(sbtiEligible
        .filter((r) => r.company_name === co && coSectors.has(r.sector) && hasWindow(r))
        .map((r) => r.scope));

      const curSources = sources.get();
      if (!curSources.includes("SBTi")) {
        sources.set([...curSources, "SBTi"]);
        sbtiControls.classList.remove("d-none");
      }
      scopes.set([...new Set([...scopes.get(), ...coScopes])]);
      if (coScopes.length > 0 && !highlighted.includes(co)) highlighted = [...highlighted, co];

      refreshCompanyChoices();
      update();
    }

    // Reset so the control acts as a "jump" button
    finder.clear(true);
  }

  // ── Data ───────────────────────────────────────────────────────────────────

  function macroData(sector, subsector, selSources) {
    let df = [];
    let ukFallback = false;
    if (selSources.includes("UK CCC")) {
      // Subsector filter, falling back to the whole macro sector if CCC has no match
      let rows = ukMap.filter((r) => r.harmonized_sector === sector &&
        (subsector === "All" || r.harmonized_subsector === subsector));
      ukFallback = subsector !== "All" && rows.length === 0;
      if (ukFallback) rows = ukMap.filter((r) => r.harmonized_sector === sector);
      df = df.concat(macroSeries(data, "UK CCC", rows.map((r) => r.original_sector)));
    }
    // SNBC and IEA are always shown at macro level
    if (selSources.includes("SNBC")) {
      df = df.concat(macroSeries(data, "SNBC",
        snbcMap.filter((r) => r.harmonized_sector === sector).map((r) => r.original_sector)));
    }
    if (selSources.includes("IEA WEO2025")) {
      df = df.concat(macroSeries(data, "IEA WEO2025",
        ieaMap.filter((r) => r.harmonized_sector === sector).map((r) => r.original_sector)));
    }
    return df.length ? indexTo2025(df) : df;
  }

  function sbtiTrajectory(sector, subsector, selSources) {
    const empty = { agg: [], ind: [], nTargets: 0, nCos: 0 };
    if (!selSources.includes("SBTi")) return empty;
    const sec = new Set(sbtiSectorsFor(sector, subsector));
    if (sec.size === 0) return empty;
    const sc = new Set(selScopes());

    const targets = sbtiEligible.filter((r) => sec.has(r.sector) && sc.has(r.scope) && hasWindow(r));
    if (targets.length === 0) return empty;

    // Each target: straight line from 100 at base year to (1 − reduction) × 100
    // at target year, re-indexed so that its 2025 value = 100.
    const byYear = new Map(YEARS.map((y) => [y, []]));
    targets.forEach((t) => {
      const xs = [t.base_year, t.target_year];
      const ys = [100, (1 - t.target_value) * 100];
      const v25 = approx(xs, ys, 2025);
      if (Number.isNaN(v25) || v25 === 0) return;
      YEARS.forEach((y) => {
        const v = approx(xs, ys, y);
        if (!Number.isNaN(v)) byYear.get(y).push(v / v25 * 100);
      });
    });

    const agg = YEARS.filter((y) => byYear.get(y).length > 0).map((y) => {
      const v = byYear.get(y);
      return { year: y, q25: quantile(v, 0.25), median: quantile(v, 0.5), q75: quantile(v, 0.75), n: v.length };
    });
    if (agg.length === 0) return empty;

    // Individual trajectories: one stitched line per (company, scope, base year)
    const ind = [];
    if (highlighted.length > 0) {
      const hs = new Set(highlighted);
      const coTargets = sortBy(targets.filter((t) => hs.has(t.company_name)),
        "company_name", "scope", "base_year", "target_year");
      const grouped = groupBy(coTargets, (t) => `${t.company_name}|${t.scope}|${t.base_year}`);
      for (const grp of grouped.values()) {
        const yrs = [grp[0].base_year, ...grp.map((t) => t.target_year)];
        const vals = [100, ...grp.map((t) => 100 * (1 - t.target_value))];
        const v25 = approx(yrs, vals, 2025);
        if (Number.isNaN(v25) || v25 === 0) continue;
        yrs.forEach((y, i) => ind.push({
          company_name: grp[0].company_name, scope: grp[0].scope, base_year: grp[0].base_year,
          year: y, value: vals[i] / v25 * 100, target_vs_base: vals[i] - 100,
        }));
      }
      const baseYears = groupBy(ind, (r) => `${r.company_name}|${r.scope}`);
      ind.forEach((r) => {
        r.multiple_base_years = new Set(baseYears.get(`${r.company_name}|${r.scope}`).map((x) => x.base_year)).size > 1;
      });
    }

    return { agg, ind, nTargets: targets.length, nCos: new Set(targets.map((t) => t.company_name)).size };
  }

  // ── Render ─────────────────────────────────────────────────────────────────

  function renderNotes(sector, subsector, selSources, sbtiRes) {
    const msgs = [];
    if (subsector !== "All") {
      msgs.push(`SNBC and IEA WEO2025 are shown at sector level — no ${subsector} subsector breakdown available.`);
    }
    if (selSources.includes("UK CCC") && subsector !== "All" &&
      !ukMap.some((r) => r.harmonized_sector === sector && r.harmonized_subsector === subsector)) {
      msgs.push(`UK CCC has no ${subsector} subsector — showing full ${sector} sector as reference.`);
    }
    if (selSources.includes("IEA WEO2025") && !ieaCovered.has(sector)) {
      msgs.push(`IEA WEO2025 Annex A does not include ${sector} — only Transport, Buildings, Industry, and Energy are available.`);
    }
    if (selSources.includes("IEA WEO2025") && ieaCovered.has(sector)) {
      msgs.push("IEA values are in Mt CO₂ (combustion); UK CCC and SNBC values are in MtCO₂e.");
    }
    if (selSources.includes("SBTi")) {
      const scopeStr = selScopes().join(" / ");
      if (sbtiRes.nTargets > 0) {
        msgs.push(`SBTi: ${sbtiRes.nTargets} Absolute target${sbtiRes.nTargets !== 1 ? "s" : ""} from ` +
          `${sbtiRes.nCos} compan${sbtiRes.nCos !== 1 ? "ies" : "y"} (scope ${scopeStr}).`);
      } else if (sbtiSectorsFor(sector, subsector).length === 0) {
        msgs.push(`No SBTi sector mapping for ${sector}${subsector !== "All" ? ` / ${subsector}` : ""}.`);
      } else {
        msgs.push(`No SBTi Absolute targets found for the current selection (scope: ${scopeStr}).`);
      }
    }
    notes($("pathway-notes"), msgs);
  }

  function update() {
    const sector = sectorEl.value;
    const subsector = subsectorEl.value || "All";
    const selSources = sources.get();
    const inSubsector = subsector !== "All";

    $("pathway-subtitle").textContent = inSubsector ? `${sector} — ${subsector}` : sector;

    const macro = macroData(sector, subsector, selSources);
    const sbtiRes = sbtiTrajectory(sector, subsector, selSources);
    renderNotes(sector, subsector, selSources, sbtiRes);

    if (macro.length === 0 && sbtiRes.agg.length === 0) {
      emptyPlot(plotEl, "No data for the current selection.");
      return;
    }

    const ukHasSubsector = !inSubsector ||
      ukMap.some((r) => r.harmonized_sector === sector && r.harmonized_subsector === subsector);
    const traces = [];

    // Macro pathway traces (UK CCC / SNBC / IEA)
    const groups = groupBy(sortBy(macro, "source", "scenario", "year"), (r) => `${r.source}|${r.scenario}`);
    for (const grp of groups.values()) {
      const src = grp[0].source, scen = grp[0].scenario;
      const color = SOURCE_COLORS[src] ?? "#999999";
      let opacity = SCENARIO_OPACITY[scen] ?? 1;
      // Dim macro-only sources (and the UK CCC fallback) when a subsector is selected
      if (inSubsector && (MACRO_ONLY_SOURCES.includes(src) || (src === "UK CCC" && !ukHasSubsector))) opacity *= 0.25;
      const macroOnly = MACRO_ONLY_SOURCES.includes(src);
      traces.push({
        x: grp.map((r) => r.year),
        y: grp.map((r) => r.value),
        text: grp.map((r) => `<b>${src}</b><br>` +
          (macroOnly ? "" : `Scenario: ${scen}<br>`) +
          (src === "SNBC" ? `Period: ${r.period_label}<br>` : "") +
          `Year: ${r.year}<br>Index: ${round(r.value, 1)}`),
        name: macroOnly ? src : `${src} — ${scen}`,
        type: "scatter", mode: src === "SNBC" ? "lines+markers" : "lines", hoverinfo: "text",
        legendgroup: src, legendgrouptitle: { text: `<b>${src}</b>` },
        opacity,
        line: { color, dash: SCENARIO_DASH[scen] ?? "solid", width: 2.5 },
        marker: { color, size: 7 },
      });
    }

    // SBTi IQR ribbon + median line
    if (sbtiRes.agg.length > 0) {
      const a = sbtiRes.agg;
      traces.push({
        x: a.map((r) => r.year), y: a.map((r) => r.q25),
        type: "scatter", mode: "lines", line: { color: "transparent" },
        showlegend: false, hoverinfo: "skip", legendgroup: "SBTi",
      });
      traces.push({
        x: a.map((r) => r.year), y: a.map((r) => r.q75),
        type: "scatter", mode: "lines", fill: "tonexty", fillcolor: "rgba(255,127,0,0.15)",
        line: { color: "transparent" }, name: "SBTi — IQR",
        legendgroup: "SBTi", legendgrouptitle: { text: "<b>SBTi</b>" },
        text: a.map((r) => `<b>SBTi IQR</b><br>Year: ${r.year}<br>25th– 75th pct: [${round(r.q25, 1)}, ${round(r.q75, 1)}]<br>n targets: ${r.n}`),
        hoverinfo: "text",
      });
      traces.push({
        x: a.map((r) => r.year), y: a.map((r) => r.median),
        type: "scatter", mode: "lines", name: "SBTi — Median", legendgroup: "SBTi",
        line: { color: SOURCE_COLORS.SBTi, width: 2.5 },
        text: a.map((r) => `<b>SBTi Median</b><br>Year: ${r.year}<br>Index: ${round(r.median, 1)}` +
          `<br>IQR: [${round(r.q25, 1)}, ${round(r.q75, 1)}]<br>n targets: ${r.n}`),
        hoverinfo: "text",
      });
    }

    // Individual company traces
    if (sbtiRes.ind.length > 0) {
      const names = uniqueSorted(sbtiRes.ind.map((r) => r.company_name));
      const palette = Object.fromEntries(names.map((n, i) => [n, COMPANY_COLORS[i % COMPANY_COLORS.length]]));
      const pct = (v) => `${v > 0 ? "+" : ""}${round(v, 1)}%`;
      const indGroups = groupBy(sbtiRes.ind, (r) => `${r.company_name}|${r.scope}|${r.base_year}`);
      for (const grp of indGroups.values()) {
        const g0 = grp[0];
        const color = palette[g0.company_name];
        traces.push({
          x: grp.map((r) => r.year), y: grp.map((r) => r.value),
          type: "scatter", mode: "lines+markers",
          name: g0.multiple_base_years
            ? `${g0.company_name} (s${g0.scope}, base ${g0.base_year})`
            : `${g0.company_name} (s${g0.scope})`,
          legendgroup: "SBTi companies", legendgrouptitle: { text: "<b>SBTi companies</b>" },
          line: { color, dash: "dot", width: 1.8 }, marker: { color, size: 7 },
          text: grp.map((r) => `<b>${escapeHtml(r.company_name)}</b><br>Base year: ${r.base_year}<br>` +
            `Scope: ${r.scope}<br>Year: ${r.year}<br>Target: ${pct(r.target_vs_base)}<br>` +
            `Index: ${pct(r.value - 100)} (vs. 2025)`),
          hoverinfo: "text",
        });
      }
    }

    plot(plotEl, traces, {
      shapes: [{
        type: "line", x0: 2023, x1: 2052, y0: 100, y1: 100,
        line: { color: "#6c757d", width: 1, dash: "dot" },
      }],
      xaxis: { title: { text: "Year" }, dtick: 5, tick0: 2025, range: [2022, 2052] },
      yaxis: {
        title: { text: "Emissions Index (2025 = 100)" },
        zeroline: true, zerolinecolor: "#6c757d", zerolinewidth: 1,
      },
      legend: { groupclick: "toggleitem", itemsizing: "constant" },
      hovermode: "closest",
    });
  }

  refreshCompanyChoices();
  update();
}
