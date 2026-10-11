# Verification record

The complete synthetic demonstration ran successfully, producing all five
scenario comparisons and the four requested CSV files plus a assumptions log
and row-level backtest evidence. No company data was used.

Seven automated checks passed:

1. Recursive naive forecasts and lag construction use preceding observations.
2. Changing all holdout actual sales leaves every model forecast unchanged.
3. Synthetic historical promotion response is learned and reacts to future plans.
4. Acreage respects total land, balances pounds, and cannot use conventional
   acres for organic demand; hard coverage rejects organic capacity shortages.
5. Adverse user-supplied yield multipliers change acreage; infeasible minimum
   acreage constraints raise a solver error.
6. Missing interior historical weeks are rejected.
7. MAPE excludes zeros, WAPE retains zero-demand error, and bias uses the
   prediction-minus-actual sign.

Verified using Python 3.12, pandas 2.3.3, NumPy 2.5.3, SciPy 1.18.1 and
scikit-learn 1.9.1. The model defaults to one job for portability; optional
`model_jobs` in config.json can increase parallelism on an unrestricted machine.

These checks verify software behavior with synthetic inputs. They do not verify
company forecast accuracy, crop yields, acreage assumptions or economic values.

## App upgrade verification

Eleven automated model/feature/weather checks now pass, including customer-safe
lags, the user's strict rolling windows, training/recursive feature parity,
complete-week weather rejection and user-defined GDD/heat calculations.

Streamlit's AppTest also booted the app, clicked Run planning scenarios, verified
all five scenarios and switched through all five differentiated chart dimensions.
A live Open-Meteo historical request for the approximate regional point returned
a complete week, with provider/requested coordinates and units recorded.
The initial point remains unverified as a company farm location.

## Upfront farm weather update

Twelve automated checks now pass, including keeping unavailable final-day live
forecast values explicitly missing. The app test verified upfront current weather,
the 16-day horizon selector, a full planning run and source/location metadata.
The supplied Mettler Frontage address was matched with the US Census geocoder;
its approximate address coordinates are recorded in farm_location.json. A live
forecast request at that point succeeded. Live weather remains separate from
sales/demo planning weather until the user explicitly applies weather to inputs.

## Brand and planning risk upgrade

Fifteen automated checks pass. New checks verify holdout-error-derived demand
ranges, monotonic acreage changes across planning postures, shortage versus
expected demand, irrigation limits, packing limits, storage loss and missing
water-rate rejection. The complete app workflow also verifies displayed Low /
Expected / High demand and saved range fields. Example crop and sales inputs
remain synthetic; missing new farm inputs stay blank and assumptions are logged.
