// app.js — entry point
// Loads the data once, initialises one module per tab (mirroring the Shiny
// modules) and handles hash-based tab navigation.

import { loadAll } from "./lib/data.js";
import { enableFullScreen, resizePlots } from "./lib/ui.js";
import { initSbti } from "./modules/sbti.js";
import { initUkCcc } from "./modules/uk_ccc.js";
import { initComparison } from "./modules/comparison.js";
import { initPathway } from "./modules/pathway.js";
import { initFrench } from "./modules/french.js";
import { initMethodology } from "./modules/methodology.js";

const TABS = ["sbti", "uk-ccc", "comparison", "pathway", "french", "methodology"];
const DEFAULT_TAB = "sbti";

function showTab(name) {
  if (!TABS.includes(name)) name = DEFAULT_TAB;
  document.querySelectorAll(".tab-pane-page").forEach((el) => el.classList.toggle("d-none", el.id !== `tab-${name}`));
  document.querySelectorAll("[data-tab]").forEach((el) => {
    const active = el.dataset.tab === name;
    el.classList.toggle("active", active);
    if (active) el.setAttribute("aria-current", "page"); else el.removeAttribute("aria-current");
  });
  // Plotly cannot size charts inside hidden containers: resize on display
  requestAnimationFrame(() => resizePlots(document.getElementById(`tab-${name}`)));
  const collapse = document.getElementById("main-nav");
  if (collapse.classList.contains("show")) bootstrap.Collapse.getOrCreateInstance(collapse).hide();
}

function currentTab() {
  const h = location.hash.replace(/^#/, "").split("/")[0];
  return TABS.includes(h) ? h : DEFAULT_TAB;
}

async function main() {
  const loading = document.getElementById("loading");
  try {
    const data = await loadAll();
    // All tabs are rendered while visible, then hidden by showTab()
    initSbti(data);
    initUkCcc(data);
    initComparison(data);
    initPathway(data);
    initFrench(data);
    initMethodology(data);
    enableFullScreen();
    loading.remove();
    onHashChange();
  } catch (err) {
    console.error(err);
    loading.innerHTML = `<div class="alert alert-danger m-4">Could not load the data: ${err.message}.<br>
      If you opened this file directly from disk, serve the folder over HTTP instead
      (e.g. <code>python -m http.server -d docs</code>).</div>`;
  }
}

// In-page anchors inside the methodology tab look like #methodology/section
function onHashChange() {
  showTab(currentTab());
  const sub = location.hash.split("/")[1];
  if (sub) requestAnimationFrame(() => document.getElementById(sub)?.scrollIntoView({ behavior: "smooth" }));
}

window.addEventListener("hashchange", onHashChange);

main();
