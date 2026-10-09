// data.js
// Loads the JSON exports (see scripts/export_web_data.py) and provides the
// numerical helpers that mirror the R functions used by the Shiny app
// (stats::approx, stats::quantile type 7, dplyr-style C-locale sorting).

const DATA_DIR = "data";

async function fetchJson(path) {
  const resp = await fetch(`${DATA_DIR}/${path}`);
  if (!resp.ok) throw new Error(`Failed to load ${path} (${resp.status})`);
  return resp.json();
}

// Expand a dictionary-encoded columnar export into an array of row objects.
function decodeColumnar(payload) {
  const { n, levels = {}, cols } = payload;
  const names = Object.keys(cols);
  const rows = new Array(n);
  for (let i = 0; i < n; i++) {
    const row = { _row: i };
    for (const name of names) {
      const v = cols[name][i];
      if (levels[name]) row[name] = v < 0 ? null : levels[name][v];
      else row[name] = v;
    }
    rows[i] = row;
  }
  return rows;
}

export async function loadAll() {
  const [sbti, ukCcc, snbc, iea, matched, mapping, meta] = await Promise.all([
    fetchJson("sbti_targets.json"),
    fetchJson("uk_ccc_subsector.json"),
    fetchJson("snbc_emissions.json"),
    fetchJson("iea_emissions.json"),
    fetchJson("sbti_nzdpu_matched.json"),
    fetchJson("sector_mapping.json"),
    fetchJson("meta.json"),
  ]);

  const sbtiRows = decodeColumnar(sbti);
  // Keep the company code so target wording shards can be looked up
  sbtiRows.forEach((r, i) => { r._company_code = sbti.cols.company_name[i]; });

  return {
    sbti: sbtiRows,
    sbtiCompanies: sbti.levels.company_name,
    ukCcc: decodeColumnar(ukCcc),
    snbc,
    iea,
    matched: decodeColumnar(matched),
    sectorMapping: mapping,
    meta,
  };
}

// ── Target wording (sharded, lazily loaded) ──────────────────────────────────

const WORDING_SHARDS = 32;
const wordingCache = new Map();

export async function loadWording(companyCodes) {
  const shards = [...new Set(companyCodes.map((c) => c % WORDING_SHARDS))];
  await Promise.all(shards.map(async (s) => {
    if (wordingCache.has(s)) return;
    const key = String(s).padStart(2, "0");
    wordingCache.set(s, fetchJson(`sbti_wording/${key}.json`));
  }));
  const loaded = await Promise.all(shards.map((s) => wordingCache.get(s)));
  return (row, code) => {
    const shard = loaded[shards.indexOf(code % WORDING_SHARDS)];
    return shard ? shard[row] ?? null : null;
  };
}

// ── Sorting helpers (dplyr::arrange uses the C locale) ───────────────────────

export function cmp(a, b) {
  if (a === b) return 0;
  if (a === null || a === undefined) return 1;   // NA last, as in arrange()
  if (b === null || b === undefined) return -1;
  if (typeof a === "number" && typeof b === "number") return a - b;
  const sa = String(a), sb = String(b);
  return sa < sb ? -1 : sa > sb ? 1 : 0;
}

export function sortBy(arr, ...keys) {
  // Array.prototype.sort is stable, matching dplyr::arrange()
  return [...arr].sort((x, y) => {
    for (const k of keys) {
      const c = cmp(typeof k === "function" ? k(x) : x[k], typeof k === "function" ? k(y) : y[k]);
      if (c !== 0) return c;
    }
    return 0;
  });
}

export function uniqueSorted(values) {
  return [...new Set(values.filter((v) => v !== null && v !== undefined))].sort(cmp);
}

export function groupBy(arr, keyFn) {
  const m = new Map();
  for (const x of arr) {
    const k = keyFn(x);
    if (!m.has(k)) m.set(k, []);
    m.get(k).push(x);
  }
  return m;
}

// ── Numerical helpers ────────────────────────────────────────────────────────

// stats::approx(x, y, xout, rule = 1, ties = mean): linear interpolation,
// NaN outside the observed range; duplicated x values are averaged.
export function approx(xs, ys, xout) {
  const pts = new Map();
  xs.forEach((x, i) => {
    const y = ys[i];
    if (x === null || y === null || Number.isNaN(x) || Number.isNaN(y)) return;
    if (!pts.has(x)) pts.set(x, []);
    pts.get(x).push(y);
  });
  const ux = [...pts.keys()].sort((a, b) => a - b);
  if (ux.length === 0) return NaN;
  const uy = ux.map((x) => { const v = pts.get(x); return v.reduce((s, t) => s + t, 0) / v.length; });
  if (xout < ux[0] || xout > ux[ux.length - 1]) return NaN;
  for (let i = 0; i < ux.length; i++) {
    if (ux[i] === xout) return uy[i];
    if (ux[i] > xout) {
      const t = (xout - ux[i - 1]) / (ux[i] - ux[i - 1]);
      return uy[i - 1] + t * (uy[i] - uy[i - 1]);
    }
  }
  return NaN;
}

// stats::quantile(type = 7)
export function quantile(values, p) {
  const v = values.filter((x) => !Number.isNaN(x)).sort((a, b) => a - b);
  if (v.length === 0) return NaN;
  const h = (v.length - 1) * p;
  const lo = Math.floor(h), hi = Math.ceil(h);
  return v[lo] + (h - lo) * (v[hi] - v[lo]);
}

// R's round(x, digits) for display purposes
export function round(x, digits = 0) {
  const f = 10 ** digits;
  return Math.round(x * f) / f;
}

export function fmtInt(x) {
  return Math.round(x).toLocaleString("en-US");
}

export function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ── Sector mapping helpers (mirror shiny_app/utils/sector_mapping.R) ─────────

export const HARMONIZED_SECTORS = ["Transport", "Buildings", "Agriculture", "Industry", "Energy", "Waste"];

export const PATHWAY_SECTOR_CHOICES = {
  "Cross-source": ["Transport", "Buildings", "Agriculture", "Industry", "Energy", "Waste"],
  "UK CCC only": ["Land use", "Engineered removals", "F-gases"],
  "SBTi only": ["Financial & Professional Services", "Technology & Telecoms",
    "Healthcare", "Consumer & Retail", "Other"],
};

export function mapFor(mapping, source) {
  return mapping.filter((r) => r.source === source);
}

export function getSubsectors(mapping, macroSector) {
  return [...new Set(mapping
    .filter((r) => r.harmonized_sector === macroSector && r.harmonized_subsector)
    .map((r) => r.harmonized_subsector))];
}
