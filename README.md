# NetZeroDB

Net Zero Trajectory Database: explore emissions trajectories across companies (SBTi),
sectors and geographies (UK CCC 7th Carbon Budget, French SNBC, IEA WEO 2025), and check
French companies' progress against their targets (NZDPU).

The project ships two front-ends built on the same processed data:

| Front-end | Location | Hosting |
|-----------|----------|---------|
| Shiny app | `shiny_app/` | Posit Connect Cloud (R server) |
| Static website (same features + methodology notes) | `docs/` | GitHub Pages or Vercel (no server) |

## Data pipeline

```
inputs/ ──► R/<dataset>/*.R ──► data_prepared/*.rds ──► shiny_app/
                                         │
                                         └──► scripts/export_web_data.py ──► docs/data/*.json ──► website
```

1. Run the R processing scripts (`R/sbti`, `R/uk_ccc`, `R/snbc`, `R/iea`, `R/nzdpu`,
   then `R/analysis/sbti_nzdpu_match.R`).
2. Export the prepared data for the website:

   ```bash
   pip install -r scripts/requirements-web.txt
   python scripts/export_web_data.py
   ```

   The sector taxonomy is read directly from `R/utils/sector_mapping.R`, so the website
   and the Shiny app always use the same mapping.

## Static website (`docs/`)

Plain HTML + JavaScript (Plotly.js, Bootstrap, Tom Select loaded from jsDelivr); no build
step. Each Shiny module has a JavaScript counterpart in `docs/js/modules/`:

| Tab | Shiny module | JS module |
|-----|--------------|-----------|
| SBTi Targets | `sbti_mod.R` | `sbti.js` |
| UK Carbon Budget | `uk_ccc_mod.R` | `uk_ccc.js` |
| Sectoral Comparison | `comparison_mod.R` | `comparison.js` |
| Net-zero Pathway | `netzero_pathway_mod.R` | `pathway.js` |
| French Companies | `sbti_nzdpu_mod.R` | `french.js` |
| Methodology | — | `methodology.js` + `index.html` |

Preview locally (ES modules need an HTTP server, not `file://`):

```bash
python -m http.server -d docs 8000   # then open http://localhost:8000
```

### Deploy on GitHub Pages

Repository **Settings → Pages → Build and deployment**: Source *Deploy from a branch*,
branch `main`, folder `/docs`. The site is published at
`https://<user>.github.io/<repo>/`.

### Deploy on Vercel

Import the repository in Vercel. `vercel.json` sets the output directory to `docs/`
with no build step.
