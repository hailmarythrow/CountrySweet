# Country Sweet Produce / Bako Sweet planning prototype

This student-friendly Python project connects weekly SKU demand forecasts to
crop-group acreage decisions. **No company information was provided. All included
data and output results are synthetic demonstration material.**

## Easy browser app

Double-click **Start App.cmd** to open the local app. See APP_GUIDE.md for the
input editors, differentiated charts, persistent saved runs and coordinate-based
weather south of Bakersfield. The four output files remain compatible with the CLI.
The expanded user-provided feature function also adds year, quarter, lag_2 and
rolling_std_4. Four/eight-week feature windows now require complete windows.

## Run on Windows

Open PowerShell in this project folder. Python 3.10 or newer is required.

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py --data data --output outputs
```

## Share with teammates through GitHub

The project folder to add to GitHub is the folder containing this README:
`BakoSweetPrototype`. It includes the application source, synthetic input data,
tests, Streamlit configuration, and the logo asset. Installed environments,
generated outputs, run logs, backups, and private/local configuration are
excluded by `.gitignore`.

The app does not require environment variables or API keys. Weather lookups
use the public Open-Meteo service; internet access is needed for live weather
data, but the demo data and core planning workflow are local.

For a fresh teammate setup:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
Start App.cmd
```

Alternatively, run the browser app directly:

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py
```

The browser app runs locally at `http://127.0.0.1:8501`. Keep the terminal
window open while using it.

The `data` folder already contains explicitly labeled synthetic inputs. To create
another independent demonstration: `py generate_demo.py --data another_demo`.
The generator refuses to overwrite an existing folder. Run the model against real
inputs in a separate folder: `py main.py --data real_data --output real_outputs`.
Set `synthetic_demo` to false only after replacing every demonstration input.

## Input files and units

CSV column names and units are intentional. Missing required inputs cause an
error instead of a fabricated estimate. Every week must be a Monday week-start.
Historical weekly series must be complete through the final history week.
Insert a zero-sales row only if zero sales actually occurred; do not hide gaps.
Future must include all historical SKU/customer series for the same contiguous
horizon. Supply pounds consistently: convert package counts to pounds before
loading. Never mix revenue, cases and pounds.

| File | Required columns / meaning |
| --- | --- |
| history.csv | week, sku, sales_lbs; one row per SKU/customer/week |
| future.csv | week, sku; no future sales; starts one week after history |
| products.csv | sku, crop_group, organic_status, package_format; exactly one underlying crop/status pair per SKU |
| crops.csv | crop_group, organic_status, expected_lbs_per_acre, packout_rate, min_acres, max_acres, safety_stock_pct, shortage_penalty_per_lb, excess_penalty_per_lb; optional cost_per_acre |
| promotions.csv | start_week, end_week, sku, customer, promotion_type, promotion_intensity, game_day, heart_healthy; inclusive Monday endpoints; `*` selects all SKUs/customers |
| config.json | acreage, weather multipliers, validation settings, weights, model settings and explicit assumptions |

Use `organic` / `conventional` exactly. Product mappings cannot be duplicated.
Crop/variety names and package formats are your own categories. Customer can be
omitted for aggregate SKU series; its configured default is `ALL`. Avoid counting
aggregate sales and customer-level sales together.

Both history and future accept these additional explanatory columns:

- `customer`, `holiday_period` (user-supplied category; no holidays inferred).
- `planned_promotion` (0/1), `promotion_type` (e.g. display),
  `promotion_intensity` (consistent nonnegative scale), `game_day` and
  `heart_healthy` (0/1).
- `average_temperature`, `maximum_temperature`, `minimum_temperature`,
  `precipitation`, `growing_degree_days`, `heat_event` (0/1).

Choose and document weather units, aggregation methods, geographical relevance,
heat-event threshold and GDD definition in `config.json` assumptions. Use the
same units across history and future. No physiological response is assumed.
Weather columns are fitted as demand predictors. Missing numeric data is imputed
inside each training fold; entirely unavailable numeric fields use zero with a
missing indicator. Missing inputs/defaults are recorded in `assumptions.json`.

`packout_rate` and `safety_stock_pct` are decimals (0.8 means 80%, 0.1 means 10%).
Yield is gross pounds per acre for the production pool matching your forecast
horizon. Cost is currency per acre; penalties are the same currency per pound.
If cost is absent, `missing_cost_per_acre` is used and flagged. This default must
be reviewed; it is not an estimate of the company's cost.

An empty promotions file must still have the listed header. Nonmatching or
overlapping promotion records are rejected. Future.csv may also supply existing
plans; promotions.csv overlays additional records. Base scenarios clear all
promotion/campaign fields to create an explicit no-promotion counterfactual.

## Forecasting and evaluation

`features.py` adds month, ISO week, seasonal sine/cosine, previous-week sales,
prior 4- and 8-week means, and a 52-week seasonal lag when available. The four
methods are last-week naive, 4-week average, weighted 4-week average (weights
oldest to newest), and a pooled random forest across SKU/customer series.

`forecast.py` uses expanding training windows and nonoverlapping later holdout
blocks. It never shuffles time observations. All methods use the same dates and
fixed-origin recursive horizon: after week one, lag inputs use each method's own
predictions. This matches the production forecast and prevents holdout sales
from leaking into lags. The random forest may bootstrap training observations
internally; this does not change the chronological validation split.

`model_accuracy.csv` reports pooled and per-series MAE, RMSE, MAPE, WAPE and
bias. MAPE excludes actual-zero rows and reports the excluded count; WAPE and
bias percentage are undefined (blank) when total actual demand is zero. Bias is
prediction minus actual, so positive values mean overforecasting. Scores are in
pounds or percent as indicated. Backtest row-level evidence is saved separately.

Planned promotions enter the model as future-known variables, without a
permanent uplift rule. Response can increase, decrease or stay flat depending on
the fitted history. Baselines do not respond to plans. The acreage plan uses ML
so promotions can affect it; compare its accuracy against baselines before
business use. Unsupported promotion types and limited variation are flagged.
Observational promotion response is predictive association, not a causal proof.

Historical validation assumes recorded promotion plans and the week's weather
were known at the forecast origin. Observed future-week weather is an idealized
backtest input: real operations require archived weather forecasts/scenarios and
promotion-plan snapshots to measure fully realistic performance. Promotions
chosen after sales were observed should not be labeled as previously known.

## Weather and scenarios

`yield_multipliers` includes normal, favorable and adverse user-supplied values.
Adjusted yield = expected pounds per acre × selected multiplier. This is a
transparent user scenario, not a learned agronomic model. No temperature or
rainfall rule changes yield automatically. If measured weather-adjusted yields
are available, use the corresponding expected yield and avoid double adjustment.

Optionally add `weather_normal.csv`, `weather_adverse.csv`, or
`weather_favorable.csv`, containing `week` and any weather columns above. Supply
exactly one row per future week; values apply to all SKUs/customers in that week.
Absent columns retain future.csv values. Adverse weather without an override
changes yield only, explicitly flagged. Normal and adverse run automatically;
favorable is configurable and can be added to the scenario specification list
in `scenarios.py` using the same machinery, or enabled in the app with the
optional favorable-weather comparison checkbox.

The five requested scenarios are Base/Normal, Promotion/Normal, Base/Adverse,
Promotion/Adverse and High demand. High demand scales the promotion/normal
forecast by `high_demand_multiplier`, a declared planning stress assumption.
Promotion records use the same learned model as base; no refitting occurs on
future data. High demand is applied after the recursive forecast is produced.

## Acreage optimization explained

Sum selected SKU/customer forecasts into crop/status demand pounds over the
entire future horizon. For each pair, let A be acres, S shortage pounds, E excess
pounds, Y weather-adjusted yield, P packout and D demand plus safety stock:

```
minimize sum(cost_per_acre*A + shortage_penalty*S + excess_penalty*E)
subject to Y*P*A + S - E = D
           sum(A) <= total_available_acres
           min_acres <= A <= max_acres
           S >= 0, E >= 0
```

This is solved with scipy.optimize.linprog (HiGHS). Acreage is continuous, so
fractional acres are allowed. Organic and conventional demand have distinct
equations; organic can only be supplied by organic acreage. Max_acres should
reflect actual certified organic land and other limits. There is no automatic
cross-status substitution or conversion of conventional land to organic.

Default `require_full_coverage=false` permits penalized shortfalls. If land is
scarce or production cost exceeds the shortage penalty, the economic solution
can leave shortages. `coverage_satisfied` and shortage pounds make this visible.
Set `require_full_coverage=true` to force enough supply for demand plus safety
stock; infeasible constraints stop the run with a solver error. Check penalty
values carefully. Excess is measured relative to the safety-stock target.

This is a single production-pool planning prototype. It does not schedule harvests,
planting dates, storage losses, existing inventory, multi-crop rotation, grade
mix or shelf life. Match the horizon and usable yield to a real harvest planning
window before interpreting acreage. Sales may understate unconstrained demand
when there are stockouts. Mixed-variety SKUs need a bill-of-materials extension;
do not assign them arbitrarily to one crop.

## Outputs

- forecast.csv: weekly pounds for every method and scenario; includes explanatory inputs.
- acreage_recommendation.csv: scenario/crop/status acres, usable pounds, targets,
  shortfalls, excess and cost components.
- scenario_comparison.csv: scenario totals, unused acres, objective and coverage.
- model_accuracy.csv: pooled and SKU/customer chronological holdout metrics.
- backtest_predictions.csv: actual vs predicted rows and forecast origins.
- assumptions.json: configuration snapshot, limitations and missing-input flags.

All CSV files carry `synthetic_demo` to distinguish classroom results. They are
written only after all scenario runs succeed. Use a fresh output folder for each
decision run to preserve previous results.

## Verification and source references

Run `py -m unittest discover -s tests -v` for meaningful checks of no-leakage
recursive forecasts, learned promotion response, organic constraints, shortages,
hard-coverage infeasibility and acreage arithmetic.

Implementation references: [SciPy linprog](https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.linprog.html)
and [scikit-learn RandomForestRegressor](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.RandomForestRegressor.html).
