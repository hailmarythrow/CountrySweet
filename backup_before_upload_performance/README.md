# Country Sweet Produce — Inventory Visualization Dashboard

A Streamlit dashboard built for the BA 3108 Country Sweet Produce project. All built-in numbers are fabricated demonstration records. This is not an official Country Sweet Produce product.

## Run it

On Windows, double-click `Start-Dashboard.cmd` to use the prepared Python 3.12 environment (`.venv312`). Keep its terminal window open while using the dashboard; press Ctrl+C to stop it. If the dashboard is already running, open http://127.0.0.1:8501.

For a fresh setup, use Python 3.12 and install the requirements:

```bash
python -m pip install -r requirements.txt
streamlit run app.py
```

The same commands work on macOS after creating and activating a virtual environment. Streamlit displays a local URL, typically `http://localhost:8501`.

## Dashboard features

- Light/dark mode and a configurable welcome time zone.
- A live welcome clock plus cached current weather for selectable cities.
- Seven workbook-backed views: Executive Overview, Inventory Timeline, Organic vs. Conventional, Product Breakdown, Harvest & Sales, Purchasing & Loads, and Data Explorer & Quality.
- Variety, size, classification, harvest, sales, packout, and load charts use Plotly and keep source worksheet/reporting-period captions visible.
- Shared Year, reporting week, variety, size, organic/conventional, product, and packaging filters with a reset control.
- Workbook inspection that lists every worksheet, recognizes supported measures, and reports missing values, spreadsheet errors, and missing formula caches.

## Upload your spreadsheet

The dashboard first looks for a worksheet named `Forecasted Inventory Levels`, then lets you select another recognized inventory worksheet. It scans the first rows for common header names and preserves product, variety, size, classification, packaging, units, year, week, and reporting-date fields when present. Related harvest/sales/packout and purchasing/load worksheets are imported independently for their own views. It does not change the uploaded workbook or replace missing calculated values with zero.

The simplest supported worksheet contains these columns:

- `product` — descriptive product label
- `variety` — e.g. Orange, White, Purple
- `classification` — Organic or Conventional
- `size` — e.g. Small, Medium, USDA No. 1
- `stored_lbs` — current inventory in pounds
- `forecast_demand_lbs` — forecast demand in pounds for the same reporting window

The workbook named `Packout Analysis - 2026 Crop Year.xlsx` can be inspected sheet by sheet. The dashboard reports unavailable sheets and missing fields visibly; it does not invent inventory values. Inventory timelines require year/week or reporting-date fields in the imported worksheet. Formula cells with no cached result are flagged and remain unavailable rather than being treated as zero.

The import and presentation code is split into `utils/excel_loader.py` (workbook inspection and normalization), `utils/validation.py` (filters and quality helpers), `utils/charts.py` (Plotly figures), `utils/theme.py` (light/dark tokens), and `utils/ui.py` (source and validation components).

Uploads are processed within the running app session; there is no database or permanent file storage in this prototype. Do not upload confidential business data to a public deployment without appropriate access controls.

## Scope

The dashboard visualizes supplied inventory and demand values. It does not calculate forecasts, recommend purchases, optimize profit, or make X-blocking decisions. It also does not automatically combine separate farm forecast, ERP inventory, and FP&A workbooks.
