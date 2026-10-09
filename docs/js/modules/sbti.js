// sbti.js — SBTi Targets tab
// Port of shiny_app/modules/sbti_mod.R: indexed reduction trajectories per
// company × scope × base year, plus a target-details table.

import { $, checkboxGroup, searchSelect, plot, emptyPlot, SCOPE_COLORS } from "../lib/ui.js";
import { sortBy, uniqueSorted, round, escapeHtml, loadWording, cmp } from "../lib/data.js";

const PAGE_SIZE = 25;

export function initSbti(data) {
  const rows = data.sbti;
  const scopeChoices = uniqueSorted(rows.map((r) => r.scope));
  const typeChoices = uniqueSorted(rows.map((r) => r.type));

  const plotEl = $("sbti-plot");
  const tableEl = $("sbti-table");

  const state = { page: 0, sortCol: null, sortDir: 1, requestId: 0 };

  const companies = searchSelect($("sbti-company"), data.sbtiCompanies, {
    placeholder: "Search for a company…",
    onChange: () => { state.page = 0; update(); },
  });
  const scopes = checkboxGroup($("sbti-scope"), "sbti-scope", scopeChoices, scopeChoices, () => { state.page = 0; update(); });
  const types = searchSelect($("sbti-type"), typeChoices, {
    placeholder: "All target types",
    onChange: () => { state.page = 0; update(); },
  });
  types.setValue(["Absolute"], true);

  function filtered() {
    const cos = new Set(companies.getValue());
    const sc = new Set(scopes.get());
    const ty = new Set(types.getValue());
    return rows.filter((r) =>
      (cos.size === 0 || cos.has(r.company_name)) &&
      (sc.size === 0 || sc.has(r.scope)) &&
      (ty.size === 0 || ty.has(r.type)));
  }

  // ── Trajectory chart ───────────────────────────────────────────────────────

  function renderPlot(df) {
    if (companies.getValue().length === 0) {
      emptyPlot(plotEl, "Search for a company in the sidebar to view its trajectory.");
      return;
    }

    const base = df
      .filter((r) => r.base_year !== null && r.target_year !== null &&
        r.target_value !== null && r.target_value <= 1)
      .map((r) => ({ ...r, group_id: `${r.company_name} ${r.scope} ${r.base_year}` }));

    if (base.length === 0) {
      emptyPlot(plotEl, "No targets found for the current selection.");
      return;
    }

    // Stitched trajectories: one base point (index = 100) per group, one point
    // per target sorted by target year, and an NA separator between groups.
    const pts = [];
    const seen = new Set();
    for (const r of base) {
      if (seen.has(r.group_id)) continue;
      seen.add(r.group_id);
      const name = escapeHtml(r.company_name);
      pts.push({
        group_id: r.group_id, scope: r.scope, year: r.base_year, index: 100, key: r.base_year - 0.5,
        text: `<b>${name}</b><br>Scope: ${r.scope}<br>Baseline: ${r.base_year} (index = 100)`,
      });
      pts.push({ group_id: r.group_id, scope: r.scope, year: null, index: null, key: Infinity, text: null });
    }
    for (const r of base) {
      const idx = 100 * (1 - r.target_value);
      pts.push({
        group_id: r.group_id, scope: r.scope, year: r.target_year, index: idx, key: r.target_year,
        text: `<b>${escapeHtml(r.company_name)}</b><br>Scope: ${r.scope}<br>` +
          `Classification: ${escapeHtml(r.target_classification_short ?? "—")}<br>` +
          `Reduction: ${round(r.target_value * 100)}%<br>` +
          `${r.base_year} → ${r.target_year}<br>Index: ${round(idx, 1)}`,
      });
    }
    const ordered = sortBy(pts, "group_id", "key");

    const traces = uniqueSorted(ordered.map((p) => p.scope)).map((sc) => {
      const g = ordered.filter((p) => p.scope === sc);
      const color = SCOPE_COLORS[sc] ?? "#999999";
      return {
        x: g.map((p) => p.year), y: g.map((p) => p.index), text: g.map((p) => p.text),
        name: sc, type: "scatter", mode: "lines+markers", hoverinfo: "text",
        line: { width: 2, color }, marker: { size: 6, color },
      };
    });

    plot(plotEl, traces, {
      xaxis: { title: { text: "Year" }, dtick: 5 },
      yaxis: { title: { text: "Emissions Index (Base Year = 100)" }, range: [0, 110], zeroline: false },
      shapes: [{
        type: "line", x0: 0, x1: 1, xref: "paper", y0: 100, y1: 100, yref: "y",
        line: { dash: "dot", color: "#adb5bd", width: 1 },
      }],
      legend: { title: { text: "<b>Scope</b>" } },
      hovermode: "closest",
    });
  }

  // ── Target details table ───────────────────────────────────────────────────

  const COLUMNS = [
    { key: "company_name", label: "Company", width: "160px" },
    { key: "scope", label: "Scope", width: "60px" },
    { key: "type", label: "Type" },
    { key: "classification", label: "Classification" },
    { key: "base_year", label: "Base Year" },
    { key: "target_year", label: "Target Year" },
    { key: "reduction", label: "Reduction", sortKey: "target_value" },
    { key: "wording", label: "Target Wording", width: "400px" },
  ];

  function reductionLabel(v) {
    if (v === null) return "—";
    if (v > 1) return `${round(v * 100, 1)}% (outlier)`;
    return `${(v * 100).toFixed(1)}%`;
  }

  async function renderTable(df) {
    if (companies.getValue().length === 0) {
      tableEl.innerHTML = `<p class="text-muted p-3 mb-0">Select a company to see target details.</p>`;
      return;
    }

    let sorted = sortBy(df, "company_name", "scope", "target_year");
    if (state.sortCol) {
      const col = COLUMNS.find((c) => c.key === state.sortCol);
      const k = col.sortKey ?? col.key;
      sorted = [...sorted].sort((a, b) => state.sortDir * cmp(a[k], b[k]));
    }
    const nPages = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE));
    state.page = Math.min(state.page, nPages - 1);
    const pageRows = sorted.slice(state.page * PAGE_SIZE, (state.page + 1) * PAGE_SIZE);

    const reqId = ++state.requestId;
    const getWording = await loadWording(pageRows.map((r) => r._company_code));
    if (reqId !== state.requestId) return;   // a newer render superseded this one

    const head = COLUMNS.map((c) => {
      const active = state.sortCol === c.key;
      const icon = active ? (state.sortDir === 1 ? "bi-caret-up-fill" : "bi-caret-down-fill") : "bi-chevron-expand";
      return `<th data-col="${c.key}" ${c.width ? `style="min-width:${c.width}"` : ""}>
        ${c.label} <i class="bi ${icon} sort-icon"></i></th>`;
    }).join("");

    const body = pageRows.map((r) => `<tr>
      <td>${escapeHtml(r.company_name)}</td>
      <td>${escapeHtml(r.scope ?? "")}</td>
      <td>${escapeHtml(r.type ?? "")}</td>
      <td>${escapeHtml(r.target_classification_short ?? "—")}</td>
      <td>${r.base_year ?? ""}</td>
      <td>${r.target_year ?? ""}</td>
      <td>${reductionLabel(r.target_value)}</td>
      <td class="wording">${escapeHtml(getWording(r._row, r._company_code) ?? "—")}</td>
    </tr>`).join("");

    const from = sorted.length === 0 ? 0 : state.page * PAGE_SIZE + 1;
    const to = Math.min(sorted.length, (state.page + 1) * PAGE_SIZE);

    tableEl.innerHTML = `
      <div class="table-scroll">
        <table class="table table-sm table-striped table-hover data-table mb-0">
          <thead><tr>${head}</tr></thead>
          <tbody>${body || `<tr><td colspan="8" class="text-muted text-center">No targets match the current filters.</td></tr>`}</tbody>
        </table>
      </div>
      <div class="d-flex justify-content-between align-items-center px-2 pt-2 small text-muted">
        <span>Showing ${from} to ${to} of ${sorted.length.toLocaleString("en-US")} entries</span>
        <div class="btn-group btn-group-sm">
          <button class="btn btn-outline-secondary" data-page="prev" ${state.page === 0 ? "disabled" : ""}>Previous</button>
          <span class="btn btn-outline-secondary disabled">${state.page + 1} / ${nPages}</span>
          <button class="btn btn-outline-secondary" data-page="next" ${state.page >= nPages - 1 ? "disabled" : ""}>Next</button>
        </div>
      </div>`;
  }

  tableEl.addEventListener("click", (e) => {
    const th = e.target.closest("th[data-col]");
    if (th) {
      const col = th.dataset.col;
      if (state.sortCol === col) state.sortDir = -state.sortDir;
      else { state.sortCol = col; state.sortDir = 1; }
      state.page = 0;
      renderTable(filtered());
      return;
    }
    const btn = e.target.closest("button[data-page]");
    if (btn) {
      state.page += btn.dataset.page === "next" ? 1 : -1;
      renderTable(filtered());
    }
  });

  function update() {
    const df = filtered();
    renderPlot(df);
    renderTable(df);
  }

  update();
}
