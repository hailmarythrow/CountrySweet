# Open the app

## Brand, farm assumptions and demand risk

The app uses the supplied Bako Sweet logo and its green/orange/white palette.
Crop panels show expected yield, historical yield low/high, packout, storage
loss, safety stock, water need and maturity with farmer-facing units. Percentages
are entered as 80 for 80% in these panels and saved as decimals in CSV inputs.
Unknown historical ranges, water, dates, maturity and capacities stay blank.

Water availability is a farm-wide acre-foot limit; per-crop water needs are
acre-feet per acre. Supplying availability requires water needs for every crop.
Storage loss reduces usable pounds after packout. Missing storage loss is
assumed zero and flagged. Supplied harvest and packing limits constrain average
throughput over the forecast horizon, not individual weekly peaks. Planting
window and maturity are recorded but not used to schedule harvests. Historical
yield range is descriptive; expected yield remains the user's chosen planning value.

Results show Low / Expected / High total pounds for each scenario. Low/high use
10th/90th percentiles of actual-minus-predicted crop-level chronological
holdout-block errors. They are indicative empirical ranges, not calibrated
confidence intervals. Crop bounds are summed into a planning envelope; this does
not establish joint probability coverage. With the demo's three blocks, tail
evidence is weak and explicitly shown. A different future horizon scales block
errors proportionally, a flagged unvalidated assumption. The high-demand stress
case scales both forecast and error envelope by its configured multiplier.

The **Planning posture** slider chooses Low (Conservative), Expected (Balanced)
or High (Demand-protective) as the crop demand target before safety stock. The
actual shortage/excess penalties entered per crop still govern the economic
solution. This means conservative plans can under-supply expected demand even
when their lower planning target is covered; Results explicitly reports that gap.
No posture guarantees coverage under land, water, capacity or cost constraints.

Double-click **Start App.cmd** in the BakoSweetPrototype folder on your Desktop.
The app opens in your browser. Keep the launcher window open while working.
Closing that window stops the app; it does not delete results or installed packages.
To reopen it later, double-click the same launcher again.

If packages are missing, the launcher installs requirements once. If Python is
missing, use the setup steps in README.md to create `.venv` first.

## First test

1. Keep **Included dataset** selected. The orange banner marks it as synthetic.
2. Click **Run planning scenarios** in the left sidebar.
3. Open **3 · Results**. Compare acreage across the five scenarios.
4. Choose **Differentiate by** to compare SKU, crop, organic/conventional,
   package format or customer. Choose the scenario and forecasting method.
5. Download the four requested CSVs or the complete run ZIP.

## Edit a plan

Use **1 · Inputs & promotions** to edit total acreage, weather yield multipliers,
crop yields/costs/limits, safety stock and promotion periods. Use decimal rates:
0.8 = 80% packout and 0.1 = 10% safety stock. Promotion dates use Monday week
starts; the end date is inclusive. `*` targets all SKUs/customers. Click **Run
planning scenarios** again after changing inputs. The visible results always
belong to a particular saved run, not to unsaved input edits.

Each run saves edited input snapshots, output CSVs, assumptions and a run log
under `app_runs/run_...`. Original data CSVs stay intact. If you close the app,
use **Previously saved run → Open saved run** in Results to recover your work.
To resume input editing from an old run, ZIP its `inputs` folder and import it
as a dataset. This restores the configuration and input tables for another run.

## Farm weather south of Bakersfield

Weather now appears at the top of the app before production inputs. It shows
current modeled temperature, today's high/low and rain chance, and seven-day
rainfall. Select **Next 7 days** or **Next 16 days** to see daily forecasts.
Refresh live weather updates the provider data; otherwise it is cached up to
30 minutes. Blank values are unavailable provider data, not zero rainfall.

The approximate farm area is **2101 Mettler Frontage Rd E, Bakersfield, CA 93307**.
The US Census street-address geocoder matched it to **35.057549, -118.966403**.
This is an approximate address point, not a surveyed field boundary. Adjust
coordinates in the upfront location expander if needed. Coordinate verification
and source metadata are recorded with each saved run.

The live panel is independent of the sales/demo dates. In the Kern County weather
tab, a separate table shows future weather supplied to the production model,
including longer-horizon scenario overrides. The live panel does not overwrite
model inputs automatically. Use the explicit fetch/apply controls to change them.

Historical and near-term forecast buttons retrieve Open-Meteo modeled grid-cell
weather for that point. It is not an on-farm weather station. Provider grid
coordinates, requested coordinates, date range, units and retrieval time are
recorded in the configuration/assumptions output. Temperature is Fahrenheit and
precipitation is inches. History and future weather must use consistent units.

Weekly weather is aggregated Monday-Sunday: average temperature is the mean of
hourly-derived daily means; maximum and minimum are weekly extremes; rainfall
is a seven-day total. A missing day or missing hour causes an error; the app
does not fabricate weather. Historical archive data can have reporting delays.

The live provider offers up to 16 days. This cannot supply a full 12-week
production horizon. The app rejects requests outside that range. Enter future
weather assumptions or upload `weather_normal.csv`, `weather_adverse.csv` and
`weather_favorable.csv` using the weather tab. Each override needs one row per
future Monday and one or more documented weather columns. Missing override
columns retain future.csv values. The same weekly weather applies to all series.

Heat flags and GDD are optional, with user-defined temperature thresholds/base
values. The UI example values are not agronomic recommendations. No weather
threshold or rainfall rule automatically changes yield. Yield multipliers remain
explicit planning assumptions. Farm-area weather should be distinguished from
retailer weather; do not assume a local temperature change causes consumer demand.
No soil nutrient observations were supplied, so soil fertility does not change
forecast demand or yield in the app.

The public API is for permitted noncommercial uses. Check [Open-Meteo terms](https://open-meteo.com/en/terms)
and [commercial options](https://open-meteo.com/en/pricing) before business use.
Uploaded weather remains available without an API call.

## Your feature function

`features.create_features(df)` accepts `lbs_sold` and `promotion` aliases and
adds the requested year, month, week, quarter, lag_1, lag_2, strict rolling_4,
strict rolling_8, rolling_std_4 and lag_52 variables. The machine-learning model
uses these additional fields. The existing mean_4/mean_8 names remain aliases.
With customer data, lags are grouped by SKU **and customer**, avoiding mixing
retailer histories. Explicit Game Day and Heart Healthy indicators are preserved;
when missing, campaign names are inferred from promotion_type text and flagged.
Recursive forecast features use exactly the same lag/window definitions.

## Your own inputs

Download the synthetic template ZIP in Help. Replace its values with reviewed
company data and set `synthetic_demo=false` in config.json. Upload the ZIP in the
sidebar, click Import, and select the saved imported dataset. Include exactly
one history.csv, future.csv, products.csv, crops.csv, promotions.csv and config.json.
Weather override CSVs are optional. Follow README.md for schema and units.

The app runs on this computer at a localhost address. It is a local prototype;
running it does not publish company data to a website. Weather fetches send only
coordinates, dates and requested weather variables to the weather provider.
