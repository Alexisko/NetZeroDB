// french.js — French Companies tab
// Port of shiny_app/modules/sbti_nzdpu_mod.R: SBTi targets of French
// corporates matched with their NZDPU disclosed emissions (actual vs target).

import { $, checkboxGroup, searchSelect, plot, emptyPlot, SCOPE_COLORS } from "../lib/ui.js";
import { uniqueSorted, groupBy, sortBy, round, fmtInt, escapeHtml, cmp } from "../lib/data.js";

export function initFrench(data) {
  const rows = data.matched;
  const scopeChoices = uniqueSorted(rows.map((r) => r.scope));
  const trajEl = $("french-plot");
  const sectorEl = $("french-sector-plot");

  const companies = searchSelect($("french-company"), uniqueSorted(rows.map((r) => r.company_name)), {
    placeholder: "Search for a company…",
    onChange: () => update(),
  });
  const scopes = checkboxGroup($("french-scope"), "french-scope", scopeChoices, scopeChoices, () => update());

  function filtered() {
    const sc = new Set(scopes.get());
    return rows.filter((r) => sc.size === 0 || sc.has(r.scope));
  }

  // Most recent reporting year per target (sbti_id × scope × target year)
  function latestOnTrack(df) {
    const out = [];
    for (const grp of groupBy(df, (r) => `${r.sbti_id}|${r.scope}|${r.target_year}`).values()) {
      let best = grp[0];
      for (const r of grp) if (r.reporting_year > best.reporting_year) best = r;
      if (best.on_track !== null) out.push(best);
    }
    return out;
  }

  function renderValueBoxes(df) {
    $("french-vb-companies").textContent = new Set(df.map((r) => r.nz_id)).size;
    const latest = latestOnTrack(df);
    $("french-vb-ontrack").textContent = latest.length === 0 ? "—"
      : `${Math.round(latest.filter((r) => r.on_track).length / latest.length * 100)}%`;
    const years = df.map((r) => r.reporting_year).filter((y) => y !== null);
    $("french-vb-year").textContent = years.length ? Math.max(...years) : "—";
  }

  function renderTrajectory(df) {
    const sel = new Set(companies.getValue());
    if (sel.size === 0) {
      emptyPlot(trajEl, "Search for a company in the sidebar to view its trajectory.");
      return;
    }
    const d = df.filter((r) => sel.has(r.company_name) &&
      r.base_year !== null && r.target_year !== null && r.target_value !== null && r.target_value <= 1 &&
      r.emissions_tco2e !== null && r.base_emissions !== null);
    if (d.length === 0) {
      emptyPlot(trajEl, "No trajectory data found for the current selection.");
      return;
    }

    // Target path: dashed segment from base-year emissions to the target level
    const targetPts = [];
    const seenT = new Set();
    for (const r of d) {
      const k = [r.sbti_id, r.company_name, r.scope, r.base_year, r.target_year, r.target_value, r.base_emissions].join("|");
      if (seenT.has(k)) continue;
      seenT.add(k);
      const gid = `${r.sbti_id} ${r.scope}`;
      const name = escapeHtml(r.company_name);
      const tgt = r.base_emissions * (1 - r.target_value);
      targetPts.push(
        { gid, scope: r.scope, x: r.base_year, y: r.base_emissions, key: r.base_year - 0.5,
          text: `<b>${name}</b> — Scope ${r.scope}<br>Baseline: ${r.base_year}<br>Emissions: ${fmtInt(r.base_emissions)} tCO2e` },
        { gid, scope: r.scope, x: r.target_year, y: tgt, key: r.target_year,
          text: `<b>${name}</b> — Scope ${r.scope}<br>Target: ${r.target_year}<br>Reduction: ${round(r.target_value * 100)}%<br>` +
            `Target emissions: ${fmtInt(tgt)} tCO2e` },
        { gid, scope: r.scope, x: null, y: null, key: Infinity, text: null },
      );
    }

    // Actual emissions: one point per (company, scope, reporting year)
    const actualPts = [];
    const seenA = new Set();
    for (const r of sortBy(d, "sbti_id", "scope", "reporting_year")) {
      const k = [r.sbti_id, r.company_name, r.scope, r.reporting_year, r.emissions_tco2e].join("|");
      if (seenA.has(k)) continue;
      seenA.add(k);
      const gid = `${r.sbti_id} ${r.scope} actual`;
      if (!actualPts.some((p) => p.gid === gid)) actualPts.push({ gid, scope: r.scope, x: null, y: null, key: Infinity, text: null });
      actualPts.push({ gid, scope: r.scope, x: r.reporting_year, y: r.emissions_tco2e, key: r.reporting_year,
        text: `<b>${escapeHtml(r.company_name)}</b> — Scope ${r.scope}<br>Year: ${r.reporting_year}<br>` +
          `Emissions: ${fmtInt(r.emissions_tco2e)} tCO2e` });
    }

    const targetLines = sortBy(targetPts, "gid", "key");
    const actualLines = sortBy(actualPts, "gid", "key");
    const traces = [];
    for (const sc of uniqueSorted(d.map((r) => r.scope))) {
      const color = SCOPE_COLORS[sc] ?? "#999999";
      const tl = targetLines.filter((p) => p.scope === sc);
      const al = actualLines.filter((p) => p.scope === sc);
      traces.push({
        x: tl.map((p) => p.x), y: tl.map((p) => p.y), text: tl.map((p) => p.text),
        type: "scatter", mode: "lines", name: `Scope ${sc} — target`, legendgroup: sc,
        line: { color, dash: "dash", width: 2 }, hoverinfo: "text",
      });
      traces.push({
        x: al.map((p) => p.x), y: al.map((p) => p.y), text: al.map((p) => p.text),
        type: "scatter", mode: "lines+markers", name: `Scope ${sc} — actual`, legendgroup: sc,
        line: { color, width: 2 }, marker: { color, size: 7 }, hoverinfo: "text",
      });
    }

    plot(trajEl, traces, {
      xaxis: { title: { text: "Year" }, dtick: 5 },
      yaxis: { title: { text: "Emissions (tCO2e)" }, zeroline: false },
      legend: { title: { text: "<b>Scope</b>" } },
      hovermode: "closest",
    });
  }

  function renderSectorChart(df) {
    const summary = [...groupBy(latestOnTrack(df), (r) => r.sics_sector).values()].map((g) => {
      const n = g.length, k = g.filter((r) => r.on_track).length;
      return { sector: g[0].sics_sector, n, k, pct: k / n };
    }).sort((a, b) => a.pct - b.pct || cmp(a.sector, b.sector));

    if (summary.length === 0) {
      emptyPlot(sectorEl, "No on-track data available for the current scope selection.");
      return;
    }

    plot(sectorEl, [{
      x: summary.map((s) => s.pct),
      y: summary.map((s) => s.sector),
      type: "bar", orientation: "h",
      marker: { color: "#0d6efd" },
      text: summary.map((s) => `<b>${escapeHtml(s.sector)}</b><br>${Math.round(s.pct * 100)}% on track<br>${s.k} / ${s.n} targets`),
      textposition: "none",
      hoverinfo: "text",
    }], {
      xaxis: { title: { text: "% On Track" }, tickformat: ".0%", range: [0, 1] },
      yaxis: { title: { text: "" }, categoryorder: "array", categoryarray: summary.map((s) => s.sector), automargin: true },
      shapes: [{
        type: "line", x0: 0.5, x1: 0.5, xref: "x", y0: 0, y1: 1, yref: "paper",
        line: { dash: "dot", color: "#adb5bd", width: 1 },
      }],
      margin: { l: 160, r: 20, t: 20, b: 50 },
      hovermode: "closest",
    });
  }

  function update() {
    const df = filtered();
    renderValueBoxes(df);
    renderTrajectory(df);
    renderSectorChart(df);
  }

  update();
}
