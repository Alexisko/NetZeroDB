# NetZero DB Project — Claude Instructions

## Project Goal

Build a **Net Zero Trajectory Database and Shiny App** that allows users to:

* Explore emissions trajectories across **companies, sectors, and geographies**
* Compare pathways from different sources (e.g. SBTi, UK CCC)
* Visualize progress toward net zero

This project is **modular, data-first, and reproducible**.

---

## Core Principles

1. **Modularity**

   * Each dataset (SBTi, UK CCC, etc.) is processed independently
   * Each dataset has its own:

     * data pipeline
     * processed output
     * Shiny module


---

## 📂 Project Structure

```
netzero-db/
│
├── inputs/
│   ├── sbti/
│   └── uk_ccc/
│
├── data_processed/
│
├── R/
│   ├── utils/
│   ├── sbti/
│   └── uk_ccc/
│
├── shiny_app/
│   ├── app.R
│   └── modules/
│
├── outputs/
└── README.md
```

---

## Data Processing Rules

* Always:

  * Preserve original values (`unit`, raw columns if needed)
  * Add standardized fields
* Never:

  * Overwrite raw data
  * Mix units without explicit conversion
* Prefer:

  * Tidy format (long format)
  * Explicit year-by-year trajectories

---

## 🧩 Shiny App Guidelines

* Use **modular architecture**
* Each dataset has its own:

  * UI module
  * Server module
