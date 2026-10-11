"""Local browser app for business students; all calculation stays in Python.

Streamlit reruns the script after a widget changes. session_state retains edits
between reruns. Each model run writes a permanent input/output snapshot so users
can close the browser and later inspect their saved results.
"""
from pathlib import Path
import copy
import json
import pandas as pd
import streamlit as st
from app_support import read_dataset, import_zip, run_plan
from weather import fetch_weather
from features import WEATHER
from weather_dashboard import show_weather
from farm_inputs import show_farm_inputs

ROOT = Path(__file__).resolve().parent
RUNS = ROOT / 'app_runs'
UPLOADS = ROOT / 'app_data'
st.set_page_config(page_title='Bako Sweet · Farm Planning', page_icon='🍠', layout='wide')
header = st.columns([1, 5])
header[0].image(str(ROOT / 'assets' / 'bako_sweet_logo.png'), width=145)
header[1].title('Bako Sweet · Farm Planning')
st.caption('San Joaquin Valley · Kern County · Farms south of Bakersfield')

with st.sidebar:
    st.subheader('Your workspace')
    choices = {'Included dataset': ROOT / 'data'}
    if UPLOADS.exists():
        choices.update({p.name: p for p in sorted(UPLOADS.glob('import_*')) if p.is_dir()})
    selected = st.selectbox('Dataset', list(choices), key='dataset_choice')
    upload = st.file_uploader('Import a dataset ZIP', type=['zip'], help='Include history, future, products, crops, promotions CSVs and config.json. Download the template below.')
    if st.button('Import uploaded dataset', disabled=upload is None):
        try:
            path = import_zip(upload.getvalue(), UPLOADS)
            st.success(f'Saved {path.name}. Choose it in Dataset.')
            st.rerun()
        except Exception as error:
            st.error(str(error))
    st.caption('Original CSV inputs are preserved. Each run saves your edits separately.')

path = choices[selected]
if st.session_state.get('active_dataset') != str(path):
    try:
        frames, config, extra = read_dataset(path)
    except Exception as error:
        st.error(f'Cannot load this dataset: {error}')
        st.stop()
    st.session_state.frames = frames
    st.session_state.configuration = config
    st.session_state.extra = extra
    st.session_state.active_dataset = str(path)
    st.session_state.weather_log = copy.deepcopy(config.get('weather_provenance', []))
    st.session_state.pop('result', None)
    st.session_state.generation = st.session_state.get('generation', 0) + 1

generation = st.session_state.generation
frames = {name: frame.copy() for name, frame in st.session_state.frames.items()}
config = copy.deepcopy(st.session_state.configuration)
location, live_outlook = show_weather(ROOT, config, generation)
latitude, longitude, verified = location['latitude'], location['longitude'], location['verified']
is_demo = bool(config.get('synthetic_demo', False))
if is_demo:
    st.warning('SYNTHETIC DEMO — sales, yields, costs, promotions and planning weather are classroom examples. Live farm weather above is fetched separately. These are not company recommendations.')
else:
    st.info('User-supplied dataset. Verify units, crop mappings and assumptions before using the recommendations.')
history, future = frames['history'], frames['future']
cols = st.columns(4)
cols[0].metric('Product SKUs', frames['products'].sku.nunique())
cols[1].metric('Crop / status pairs', len(frames['crops']))
cols[2].metric('History weeks', history.week.nunique())
cols[3].metric('Planning weeks', future.week.nunique())
st.caption(f"History ends {pd.to_datetime(history.week).max().date()} · Future begins {pd.to_datetime(future.week).min().date()}")

inputs_tab, weather_tab, results_tab, guide_tab = st.tabs(['1 · Inputs & promotions', '2 · Kern County weather', '3 · Results', 'Help & assumptions'])

with inputs_tab:
    st.subheader('Production assumptions')
    a, b, c = st.columns(3)
    config['total_available_acres'] = a.number_input('Total available acres', min_value=0., value=float(config['total_available_acres']), key=f'acres_{generation}')
    config['high_demand_multiplier'] = b.number_input('High-demand multiplier', min_value=1., value=float(config['high_demand_multiplier']), step=.05, key=f'demand_{generation}')
    config['require_full_coverage'] = c.checkbox('Require full demand + safety stock coverage', value=config['require_full_coverage'], key=f'coverage_{generation}')
    st.caption('With full coverage unchecked, the optimizer can recommend a penalized shortage. All shortages are shown in Results.')
    weather_cols = st.columns(3)
    for col, name in zip(weather_cols, ['normal', 'favorable', 'adverse']):
        config['yield_multipliers'][name] = col.number_input(f'{name.title()} yield multiplier', min_value=.01,
            value=float(config['yield_multipliers'][name]), step=.05, key=f'yield_{name}_{generation}')
    st.caption('Multipliers are your yield assumptions. Temperature/rainfall do not automatically change yield. Favorable is available as an optional extra scenario.')
    config['include_favorable_scenarios'] = st.checkbox('Also compare favorable weather', value=config.get('include_favorable_scenarios', False), key=f'favorable_{generation}')
    config['planning_posture'] = st.select_slider('Planning posture', options=['Conservative', 'Balanced', 'Demand-protective'],
        value=config.get('planning_posture', 'Balanced'), key=f'posture_{generation}')
    st.caption('Conservative plans to the low demand estimate; Balanced to expected demand; Demand-protective to the high estimate. Safety stock and your shortage/excess costs still apply. Conservative can leave more unmet demand if sales are stronger than planned.')
    frames['crops'] = show_farm_inputs(frames['crops'], config, generation)
    with st.expander('Crop acreage minimum / maximum limits'):
        limits = st.data_editor(frames['crops'][['crop_group', 'organic_status', 'min_acres', 'max_acres']],
            disabled=['crop_group', 'organic_status'], hide_index=True, key=f'crop_limits_{generation}')
        frames['crops'][['min_acres', 'max_acres']] = limits[['min_acres', 'max_acres']]
    st.subheader('Future promotion periods')
    st.caption('Monday-to-Monday inclusive dates. Use * for all SKUs/customers. Effects are learned from history, rather than a fixed uplift. Overlapping plans require an explicit combined type.')
    frames['promotions'] = st.data_editor(frames['promotions'], num_rows='dynamic', hide_index=True, key=f'promotions_{generation}')
    st.subheader('SKU → crop mapping')
    frames['products'] = st.data_editor(frames['products'], num_rows='dynamic', hide_index=True, key=f'products_{generation}')
    with st.expander('Weekly history and future-known inputs'):
        st.caption('Accepted sales names: sales_lbs or lbs_sold. Accepted promotion flags: planned_promotion or promotion. Do not mix package counts with pounds.')
        st.write('Historical weekly pounds')
        frames['history'] = st.data_editor(frames['history'], num_rows='dynamic', hide_index=True, key=f'history_{generation}')
        st.write('Future weekly rows: weather, holidays and previously planned promotions')
        frames['future'] = st.data_editor(frames['future'], num_rows='dynamic', hide_index=True, key=f'future_{generation}')
    with st.expander('Forecast validation settings'):
        x, y = st.columns(2)
        config['validation_folds'] = x.number_input('Chronological validation folds', min_value=1, value=int(config['validation_folds']), step=1, key=f'folds_{generation}')
        config['validation_horizon_weeks'] = y.number_input('Weeks per holdout block', min_value=1, value=int(config['validation_horizon_weeks']), step=1, key=f'horizon_{generation}')
        st.caption('All models forecast each holdout block recursively. At least minimum training weeks + folds × block length must exist per series.')

with weather_tab:
    st.subheader('Weather for the farm area')
    st.write(f"Approximate farm area: {location['address']}. Adjust coordinates in the weather panel at the top of the app.")
    other_locations = [entry for entry in st.session_state.weather_log
        if abs(entry.get('requested_latitude', latitude) - latitude) > .0001
        or abs(entry.get('requested_longitude', longitude) - longitude) > .0001]
    if other_locations:
        st.info('Some previously fetched planning weather used a different coordinate point. Refetch it for the new farm location before interpreting the model as farm-targeted.')
    if not verified:
        st.info('Approximate location — replace the coordinates or confirm the farm point before fetching weather for operational use.')
    st.caption('Farm-area weather is distinct from retailer/customer weather. It is a predictor only if the history supports an association; crop yield still uses your scenario multipliers.')
    rules = st.columns(2)
    feature_rules = config.get('weather_feature_rules', {})
    heat_on = rules[0].checkbox('Calculate a user-defined heat-event flag', value=feature_rules.get('heat_threshold_F') is not None, key=f'heat_on_{generation}')
    threshold = rules[0].number_input('Your heat threshold (°F)', value=float(feature_rules.get('heat_threshold_F') or 95.), key=f'heat_{generation}') if heat_on else None
    gdd_on = rules[1].checkbox('Calculate user-defined growing degree days', value=feature_rules.get('gdd_base_F') is not None, key=f'gdd_on_{generation}')
    gdd_base = rules[1].number_input('Your GDD base temperature (°F)', value=float(feature_rules.get('gdd_base_F') or 50.), key=f'gdd_{generation}') if gdd_on else None
    config['weather_feature_rules'] = dict(heat_threshold_F=threshold, gdd_base_F=gdd_base)
    st.caption('These optional values are editable examples, not sweet-potato agronomic recommendations. GDD uses sum(max(0, daily (max+min)/2 − base)), with no upper cap.')
    units_checked = st.checkbox('My existing weather columns use °F and inches, or I will replace/convert them consistently', key=f'units_{generation}')
    terms_checked = st.checkbox('I have checked that my weather API usage is permitted for my use case', key=f'terms_{generation}')
    st.markdown('[Open-Meteo source and attribution](https://open-meteo.com/) · [API terms](https://open-meteo.com/en/terms) · [Commercial plans](https://open-meteo.com/en/pricing)')
    st.caption('The public endpoint is for eligible noncommercial use. This classroom prototype uses it only when you click fetch. Business deployment needs an appropriate provider arrangement or uploaded weather CSVs.')
    fetch_cols = st.columns(2)
    for column, kind, target in [(fetch_cols[0], 'historical', 'history'), (fetch_cols[1], 'forecast', 'future')]:
        label = 'Fetch regional historical weather' if kind == 'historical' else 'Fetch next complete forecast weeks'
        if column.button(label, disabled=not (verified and units_checked and terms_checked)):
            try:
                with st.spinner('Fetching coordinate-specific weather...'):
                    weather, metadata = fetch_weather(latitude, longitude, frames[target].week, kind, threshold, gdd_base)
                updated = frames[target].copy()
                updated['week'] = pd.to_datetime(updated.week)
                indexed = weather.set_index('week')
                for name in WEATHER:
                    updated[name] = updated.week.map(indexed[name])
                frames[target] = updated
                st.session_state.frames = frames
                st.session_state.configuration = config
                st.session_state.weather_log.append(metadata)
                st.session_state.last_weather = weather
                st.session_state.generation += 1  # Reset editor caches to reflect fetched columns.
                st.rerun()
            except Exception as error:
                st.error(str(error))
    st.caption('Historical fetch covers completed weeks. Live forecast fetch requires every planning week to fit within the next 16 days; a 12-week horizon needs supplied scenario weather. The app never invents distant weather.')
    st.subheader('Weather supplied for the future production horizon')
    st.caption('These are dataset/scenario planning inputs, which may extend beyond live forecast coverage. Included demo weather is synthetic. Live weather above is not applied to these inputs automatically.')
    future_weather = frames['future'].copy()
    future_weather['week'] = pd.to_datetime(future_weather.week)
    weather_columns = [name for name in WEATHER if name in future_weather]
    if weather_columns:
        # Keep distinct rows rather than averaging different retailer/location assumptions.
        available = [name for name in ['week', 'sku', 'customer'] if name in future_weather] + weather_columns
        st.dataframe(future_weather[available].sort_values('week'), hide_index=True)
    for override_name, content in st.session_state.extra.items():
        import io
        with st.expander(f'Planning scenario: {override_name}'):
            st.dataframe(pd.read_csv(io.BytesIO(content)), hide_index=True)
    weather_extra = st.file_uploader('Upload weekly weather for a scenario', type=['csv'], key=f'weather_upload_{generation}')
    weather_name = st.selectbox('Apply uploaded weather to', ['normal', 'adverse', 'favorable'], key=f'weather_name_{generation}')
    if st.button('Use uploaded scenario weather', disabled=weather_extra is None):
        try:
            weather_frame = pd.read_csv(weather_extra)
            if 'week' not in weather_frame or not (set(WEATHER) & set(weather_frame)):
                raise ValueError('Weather CSV needs week and at least one documented weather column.')
            st.session_state.extra[f'weather_{weather_name}.csv'] = weather_frame.to_csv(index=False).encode()
            st.success(f'{weather_name.title()} weather stored for the next run.')
        except Exception as error:
            st.error(str(error))
    for name in list(st.session_state.extra):
        st.write(f'Active override: {name}')
        if st.button(f'Remove {name}', key=f'remove_{name}_{generation}'):
            del st.session_state.extra[name]
            st.rerun()
    if 'last_weather' in st.session_state:
        weather_frame = st.session_state.last_weather
        st.dataframe(weather_frame, hide_index=True)
        st.download_button('Download fetched weather CSV', weather_frame.to_csv(index=False), 'kern_county_weather.csv', 'text/csv')

config['weather_provenance'] = st.session_state.weather_log
config.setdefault('assumptions', [])
config['assumptions'] += [f"Weather location: {latitude}, {longitude}; verified farm point: {verified}.",
    'Regional weather represents farm-area grid estimates; no soil nutrient/yield claim is assumed.',
    'App result views aggregate one saved model run; changing controls requires a new run.']
with st.sidebar:
    st.divider()
    run_clicked = st.button('Run planning scenarios', type='primary', use_container_width=True)
    st.caption('Runs forecasts, chronological validation and crop acreage optimization. Usually takes seconds for the demo.')

if run_clicked:
    try:
        with st.spinner('Forecasting demand and planning acreage...'):
            st.session_state.result = run_plan(frames, config, st.session_state.extra, RUNS)
        st.success('Run completed. Open the Results tab for charts and downloads.')
    except Exception as error:
        st.error(f'Could not complete this run: {error}')

with results_tab:
    st.subheader('Saved planning results')
    saved = sorted(RUNS.glob('run_*/outputs/scenario_comparison.csv'), reverse=True) if RUNS.exists() else []
    if saved:
        choice = st.selectbox('Previously saved run', [str(p.parent.parent.name) for p in saved])
        if st.button('Open saved run'):
            directory = RUNS / choice
            tables = {name: pd.read_csv(directory / 'outputs' / f'{name}.csv') for name in ['forecast', 'acreage_recommendation', 'scenario_comparison', 'model_accuracy']}
            st.session_state.result = dict(tables=tables,
                assumptions=json.loads((directory / 'outputs' / 'assumptions.json').read_text()), directory=str(directory), zip=None)
    if 'result' not in st.session_state:
        st.info('Click Run planning scenarios. Your CSV results and input snapshot will be saved permanently.')
    else:
        result = st.session_state.result
        tables = result['tables']
        run_demo = result['assumptions']['config'].get('synthetic_demo', False)
        if run_demo:
            st.warning('Displayed results are synthetic demonstration results.')
        st.caption(f"Saved run: {result['directory']}. These results reflect the inputs at run time.")
        st.write('Scenario comparison')
        st.dataframe(tables['scenario_comparison'], hide_index=True)
        summary = tables['scenario_comparison'].set_index('scenario')
        if 'low_demand_lbs' in summary:
            estimate_scenario = st.selectbox('Demand range scenario', list(summary.index), key='range_scenario')
            estimates = summary.loc[estimate_scenario]
            cards = st.columns(3)
            for col, label, field in zip(cards, ['Low estimate', 'Expected demand', 'High estimate'],
                                         ['low_demand_lbs', 'expected_demand_lbs', 'high_demand_lbs']):
                col.metric(label, f'{estimates[field]:,.0f} lb')
            st.caption(f"Saved planning posture: {estimates.planning_posture}. Indicative empirical demand envelope, not a calibrated confidence interval. It comes from chronological holdout errors; promotion/weather shifts can change error behavior.")
            blocks = result['assumptions']['config']['validation_folds']
            if blocks < 10:
                st.info(f'Only {blocks} chronological holdout blocks support these ranges. Low/high tail estimates are weakly supported; add more historical blocks before treating them as reliable risk limits.')
            st.caption(f"Shortage against expected demand: {estimates.shortage_vs_expected_lbs:,.0f} lb. This exposes risk even when the chosen lower planning target is covered.")
        st.bar_chart(summary[['acres']], color='#008C35')
        if not tables['scenario_comparison'].coverage_satisfied.all():
            st.warning('Some scenarios leave shortages. Review shortage pounds, yield, land limits and penalty assumptions.')
        st.subheader('Demand by product, crop, status, package and customer')
        forecast = tables['forecast'].copy()
        forecast.week = pd.to_datetime(forecast.week)
        filters = st.columns(3)
        scenario = filters[0].selectbox('Forecast scenario', forecast.scenario.unique())
        method = filters[1].selectbox('Forecast method', ['machine_learning', 'naive', 'moving_average_4', 'weighted_moving_average'])
        dimension = filters[2].selectbox('Differentiate by', ['sku', 'crop_group', 'organic_status', 'package_format', 'customer'])
        view = forecast[(forecast.scenario == scenario) & (forecast.model == method)]
        selections = st.multiselect(f'Select {dimension}', sorted(view[dimension].astype(str).unique()), default=sorted(view[dimension].astype(str).unique()))
        view = view[view[dimension].astype(str).isin(selections)]
        chart = view.pivot_table(index='week', columns=dimension, values='prediction_lbs', aggfunc='sum')
        st.line_chart(chart)
        st.caption('Demand is pounds per week. Each line is a selected category; no SKU/customer series is counted twice.')
        st.dataframe(view[['week', 'sku', 'crop_group', 'organic_status', 'package_format', 'customer', 'prediction_lbs']], hide_index=True)
        st.subheader('Acreage recommendations by underlying crop group')
        allocation = tables['acreage_recommendation']
        st.dataframe(allocation[allocation.scenario == scenario], hide_index=True)
        st.subheader('Time-ordered model accuracy')
        accuracy = tables['model_accuracy']
        scope = st.radio('Accuracy view', ['all', 'series'], horizontal=True)
        st.dataframe(accuracy[accuracy.scope == scope], hide_index=True)
        st.caption('MAE/RMSE are pounds; lower errors are better. Positive bias means overforecasting. MAPE excludes actual-zero rows; WAPE retains their errors.')
        st.subheader('Download this run')
        for name, table in tables.items():
            st.download_button(f'Download {name}.csv', table.to_csv(index=False), f'{name}.csv', 'text/csv')
        st.download_button('Download assumptions.json', json.dumps(result['assumptions'], indent=2), 'assumptions.json', 'application/json')
        if result.get('zip'):
            st.download_button('Download all results + input snapshot', result['zip'], 'planning_run.zip', 'application/zip')
        with st.expander('Assumptions for this result'):
            for item in result['assumptions']['assumptions']:
                st.write('• ' + item)

with guide_tab:
    st.subheader('Start here')
    st.write('1. Try the included demo. 2. Edit crop assumptions and promotions. 3. Check regional weather inputs. 4. Click Run planning scenarios. 5. Compare and download results.')
    st.write('Import a ZIP containing the five documented CSV inputs and config.json for your own dataset. Mark synthetic_demo=false only when every demo value is replaced with reviewed company inputs.')
    import io, zipfile
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in ['history.csv', 'future.csv', 'products.csv', 'crops.csv', 'promotions.csv', 'config.json']:
            archive.write(ROOT / 'data' / name, name)
    st.download_button('Download synthetic input template ZIP', buffer.getvalue(), 'synthetic_input_template.zip', 'application/zip')
    st.write('Your create_features function is included with customer-safe grouping: year, quarter, lag_1, lag_2, strict rolling_4/rolling_8, rolling_std_4, lag_52, promotion fields and campaign flags. Explicit campaign flags take priority over text inference.')
    st.write('Acreage covers one production pool for the entire forecast horizon. Harvest timing, stockouts, storage, grade mix and measured soil nutrients need additional data before operational use.')
    st.markdown((ROOT / 'README.md').read_text())
