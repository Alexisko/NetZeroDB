// ui.js
// Small UI helpers shared by all modules: form controls, Plotly wrappers and
// colour palettes reused across tabs.

import { escapeHtml } from "./data.js";

export const SCOPE_COLORS = {
  "1": "#0d6efd",
  "2": "#198754",
  "3": "#dc3545",
  "1+2": "#fd7e14",
  "1+2+3": "#6f42c1",
  "1+3": "#20c997",
  "2+3": "#d63384",
};

export const SOURCE_COLORS = {
  "UK CCC": "#1f78b4",
  "SNBC": "#33a02c",
  "IEA WEO2025": "#e31a1c",
  "SBTi": "#ff7f00",
};

export const SCENARIO_DASH = {
  "Balanced Pathway": "solid",
  "Baseline": "dash",
  "SNBC": "solid",
  "NZE 2050": "solid",
};

export const SCENARIO_OPACITY = {
  "Balanced Pathway": 1,
  "Baseline": 0.5,
  "SNBC": 1,
  "NZE 2050": 1,
};

// ── Form controls ────────────────────────────────────────────────────────────

export function $(id) {
  return document.getElementById(id);
}

// Render a checkbox group into `container`; returns a getter for checked values.
export function checkboxGroup(container, name, choices, selected, onChange, labels = {}) {
  container.innerHTML = choices.map((c, i) => `
    <div class="form-check">
      <input class="form-check-input" type="checkbox" id="${name}-${i}" value="${escapeHtml(c)}"
        ${selected.includes(c) ? "checked" : ""}>
      <label class="form-check-label" for="${name}-${i}">${escapeHtml(labels[c] ?? c)}</label>
    </div>`).join("");
  container.addEventListener("change", onChange);
  return {
    get: () => [...container.querySelectorAll("input:checked")].map((el) => el.value),
    set: (values) => container.querySelectorAll("input").forEach((el) => {
      el.checked = values.includes(el.value);
    }),
  };
}

export function radioValue(name) {
  const el = document.querySelector(`input[name="${name}"]:checked`);
  return el ? el.value : null;
}

// Searchable select (Tom Select). `choices` is an array of strings.
export function searchSelect(el, choices, { multiple = true, maxItems = null, placeholder, onChange } = {}) {
  const ts = new TomSelect(el, {
    options: choices.map((c) => ({ value: c, text: c })),
    items: [],
    maxItems: multiple ? maxItems : 1,
    maxOptions: 100,
    placeholder,
    plugins: multiple && maxItems !== 1 ? ["remove_button"] : [],
    closeAfterSelect: true,
    searchField: ["text"],
    sortField: [{ field: "$score" }, { field: "text" }],
    onChange,
  });
  return ts;
}

export function setSelectOptions(select, choices, selected) {
  // `choices` may be an array or an object of { groupLabel: [values] }
  const opt = (v) => `<option value="${escapeHtml(v)}" ${v === selected ? "selected" : ""}>${escapeHtml(v)}</option>`;
  if (Array.isArray(choices)) {
    select.innerHTML = choices.map(opt).join("");
  } else {
    select.innerHTML = Object.entries(choices).map(([g, vals]) =>
      `<optgroup label="${escapeHtml(g)}">${vals.map(opt).join("")}</optgroup>`).join("");
  }
}

export function notes(container, msgs) {
  container.innerHTML = msgs.map((m) => `<p class="text-muted small mb-1">${escapeHtml(m)}</p>`).join("");
  container.classList.toggle("d-none", msgs.length === 0);
}

// ── Plotly wrappers ──────────────────────────────────────────────────────────

const PLOT_CONFIG = {
  responsive: true,
  displaylogo: false,
  modeBarButtonsToRemove: ["lasso2d", "select2d"],
};

const BASE_LAYOUT = {
  margin: { l: 70, r: 20, t: 20, b: 50 },
  font: { family: "Lato, -apple-system, 'Segoe UI', Roboto, sans-serif", size: 12 },
  paper_bgcolor: "rgba(0,0,0,0)",
  plot_bgcolor: "rgba(0,0,0,0)",
  hoverlabel: { align: "left" },
};

export function plot(el, traces, layout = {}) {
  const full = { ...BASE_LAYOUT, ...layout };
  // On narrow screens a side legend squeezes the plot area: move it below
  if (window.innerWidth < 768 && full.legend !== undefined) {
    full.legend = { ...full.legend, orientation: "h", x: 0, xanchor: "left", y: -0.25, yanchor: "top" };
    full.margin = { ...full.margin, l: 55, b: 40 };
    full.height = 560;
  }
  Plotly.react(el, traces, full, PLOT_CONFIG);
}

export function emptyPlot(el, message) {
  plot(el, [], {
    xaxis: { visible: false },
    yaxis: { visible: false },
    annotations: [{
      text: message,
      showarrow: false,
      xref: "paper", yref: "paper", x: 0.5, y: 0.5,
      font: { size: 14, color: "#6c757d" },
    }],
  });
}

export function resizePlots(root) {
  root.querySelectorAll(".js-plotly-plot").forEach((el) => Plotly.Plots.resize(el));
}

// ── Full-screen toggle for cards (mirrors bslib's full_screen = TRUE) ────────

export function enableFullScreen(root = document) {
  root.querySelectorAll("[data-fullscreen-toggle]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const card = btn.closest(".card");
      card.classList.toggle("card-fullscreen");
      document.body.classList.toggle("has-fullscreen-card", card.classList.contains("card-fullscreen"));
      btn.querySelector("i").className = card.classList.contains("card-fullscreen")
        ? "bi bi-fullscreen-exit" : "bi bi-arrows-fullscreen";
      resizePlots(card);
    });
  });
}
