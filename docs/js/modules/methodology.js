// methodology.js — Methodology tab
// Fills the data-driven parts of the methodological notes: dataset vintages /
// sizes and the harmonized sector mapping table.

import { $ } from "../lib/ui.js";
import { escapeHtml, groupBy } from "../lib/data.js";

const SOURCE_ORDER = ["UK CCC", "SNBC", "IEA WEO2025", "SBTi"];

export function initMethodology(data) {
  const { meta, sectorMapping } = data;

  document.querySelectorAll("[data-meta]").forEach((el) => {
    const v = el.dataset.meta.split(".").reduce((o, k) => (o ? o[k] : undefined), meta);
    if (v !== undefined) el.textContent = typeof v === "number" ? v.toLocaleString("en-US") : v;
  });

  // Mapping table: one row per harmonized sector / subsector, one column per source
  const bySector = groupBy(sectorMapping, (r) => r.harmonized_sector);
  const body = [...bySector.entries()].map(([sector, rows]) => {
    const subs = groupBy(rows, (r) => r.harmonized_subsector ?? "");
    return [...subs.entries()].map(([sub, subRows], i) => {
      const cells = SOURCE_ORDER.map((src) => {
        const orig = subRows.filter((r) => r.source === src).map((r) => escapeHtml(r.original_sector));
        return `<td>${orig.length ? orig.join("<br>") : `<span class="text-muted">—</span>`}</td>`;
      }).join("");
      const head = i === 0 ? `<th rowspan="${subs.size}" scope="rowgroup">${escapeHtml(sector)}</th>` : "";
      return `<tr>${head}<td>${sub ? escapeHtml(sub) : `<span class="text-muted">(sector level)</span>`}</td>${cells}</tr>`;
    }).join("");
  }).join("");

  $("method-mapping").innerHTML = `
    <table class="table table-sm table-bordered mapping-table">
      <thead class="table-light">
        <tr><th>Harmonized sector</th><th>Subsector</th>${SOURCE_ORDER.map((s) => `<th>${s}</th>`).join("")}</tr>
      </thead>
      <tbody>${body}</tbody>
    </table>`;
}
