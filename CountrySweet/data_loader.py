"""Read documented CSV inputs and reject ambiguous or unsafe data."""
import json
from pathlib import Path
import numpy as np
import pandas as pd
from features import KEYS, CATEGORICAL, WEATHER

def require(frame, columns, name):
    missing = set(columns) - set(frame.columns)
    if missing:
        raise ValueError(f'{name}: missing columns {sorted(missing)}')

def load_inputs(directory):
    directory = Path(directory)
    config = json.loads((directory / 'config.json').read_text())
    assumptions = list(config.get('assumptions', []))
    window = config.get('farm_constraints', {})
    start, end = window.get('planting_start'), window.get('planting_end')
    if start and end and pd.to_datetime(start) > pd.to_datetime(end):
        raise ValueError('Planting window start exceeds end.')
    def read(name):
        return pd.read_csv(directory / f'{name}.csv')
    products, crops = read('products'), read('crops')
    require(products, ['sku', 'crop_group', 'organic_status', 'package_format'], 'products')
    require(crops, ['crop_group', 'organic_status', 'expected_lbs_per_acre', 'packout_rate',
                    'min_acres', 'max_acres', 'safety_stock_pct', 'shortage_penalty_per_lb',
                    'excess_penalty_per_lb'], 'crops')
    for frame, columns in [(products, ['sku', 'crop_group', 'organic_status', 'package_format']),
                            (crops, ['crop_group', 'organic_status'])]:
        if frame[columns].isna().any().any():
            raise ValueError('Product and crop identifiers cannot be blank.')
        frame[columns] = frame[columns].astype(str)
    if products.sku.duplicated().any() or crops.duplicated(['crop_group', 'organic_status']).any():
        raise ValueError('Products and crop groups must have unique mapping keys.')
    if not set(products.organic_status).issubset({'organic', 'conventional'}):
        raise ValueError('organic_status must be organic or conventional.')
    if not set(crops.organic_status).issubset({'organic', 'conventional'}):
        raise ValueError('Invalid crop organic_status.')
    mapped = products.merge(crops[['crop_group', 'organic_status']], how='left',
                            on=['crop_group', 'organic_status'], indicator=True)
    if (mapped._merge != 'both').any():
        raise ValueError('Every product must map to an existing crop/status pair.')
    if 'cost_per_acre' not in crops:
        crops['cost_per_acre'] = config['missing_cost_per_acre']
        assumptions.append(f"Missing production cost set to {config['missing_cost_per_acre']} per acre.")
    elif crops.cost_per_acre.isna().any():
        crops['cost_per_acre'] = crops.cost_per_acre.fillna(config['missing_cost_per_acre'])
        assumptions.append(f"Blank production costs set to configured {config['missing_cost_per_acre']} per acre.")
    optional = ['historical_yield_low', 'historical_yield_high', 'storage_loss_rate', 'water_need_acre_ft_per_acre', 'expected_maturity_days']
    for column in optional:
        if column not in crops:
            crops[column] = np.nan
        crops[column] = pd.to_numeric(crops[column], errors='raise')
        values = crops[column].dropna()
        if not np.isfinite(values).all() or (values < 0).any():
            raise ValueError(f'{column} must be finite/nonnegative when supplied.')
    if crops.storage_loss_rate.isna().any():
        assumptions.append('Missing storage loss assumed zero for optimization; supply a measured rate. Missing yield ranges/water/maturity are unassessed, not estimated.')
    if ((crops.storage_loss_rate.dropna() < 0) | (crops.storage_loss_rate.dropna() >= 1)).any():
        raise ValueError('storage_loss_rate must be in [0,1).')
    if (crops.historical_yield_low > crops.historical_yield_high).any():
        raise ValueError('Historical yield range lower bound exceeds upper bound.')
    numeric = crops.columns.difference(['crop_group', 'organic_status'] + optional)
    crops[numeric] = crops[numeric].apply(pd.to_numeric, errors='raise')
    if not np.isfinite(crops[numeric].to_numpy()).all() or (crops[numeric] < 0).any().any():
        raise ValueError('Crop numeric inputs must be finite and nonnegative.')
    if (crops.expected_lbs_per_acre <= 0).any() or (crops.packout_rate <= 0).any() or (crops.packout_rate > 1).any():
        raise ValueError('Yield must be positive; packout_rate must be in (0,1].')
    if (crops.min_acres > crops.max_acres).any():
        raise ValueError('min_acres exceeds max_acres.')
    if not np.isfinite(config['total_available_acres']) or config['total_available_acres'] < 0:
        raise ValueError('Total acreage must be finite and nonnegative.')
    for scenario in ['normal', 'favorable', 'adverse']:
        multiplier = config['yield_multipliers'][scenario]
        if not np.isfinite(multiplier) or multiplier <= 0:
            raise ValueError('User-supplied yield multipliers must be finite and positive.')
    if config['high_demand_multiplier'] < 1 or not np.isfinite(config['high_demand_multiplier']):
        raise ValueError('High demand multiplier must be finite and at least one.')
    frames = []
    for name in ['history', 'future']:
        frame = read(name)
        require(frame, ['week', 'sku'], name)
        for alias, canonical in [('lbs_sold', 'sales_lbs'), ('promotion', 'planned_promotion')]:
            if alias in frame:
                if canonical in frame and not np.allclose(pd.to_numeric(frame[alias]), pd.to_numeric(frame[canonical]), equal_nan=True):
                    raise ValueError(f'{name}: {alias} disagrees with {canonical}.')
                frame[canonical] = frame[alias]
                # Keep a single target column so recursive predictions cannot retain an old actual alias.
                frame = frame.drop(columns=alias)
                assumptions.append(f'{name}: {alias} mapped to {canonical}.')
        if frame.sku.isna().any():
            raise ValueError(f'{name}: SKU cannot be blank.')
        frame['sku'] = frame.sku.astype(str)
        if name == 'history':
            require(frame, ['sales_lbs'], name)
            frame.sales_lbs = pd.to_numeric(frame.sales_lbs, errors='raise')
            if not np.isfinite(frame.sales_lbs).all() or (frame.sales_lbs < 0).any():
                raise ValueError('Sales pounds must be finite and nonnegative.')
        frame['week'] = pd.to_datetime(frame.week, errors='raise')
        if frame.week.isna().any() or (frame.week.dt.dayofweek != 0).any():
            raise ValueError('Use Monday week-start dates in every input.')
        defaults = config['optional_defaults']
        for flag, token in [('game_day', 'game'), ('heart_healthy', 'heart')]:
            if flag not in frame and 'promotion_type' in frame:
                frame[flag] = frame.promotion_type.astype(str).str.contains(token, case=False, na=False).astype(int)
                assumptions.append(f'{name}.{flag}: inferred from promotion_type text; verify campaign labels.')
        for column in ['customer', 'holiday_period', 'planned_promotion', 'promotion_type',
                       'promotion_intensity', 'game_day', 'heart_healthy'] + WEATHER:
            if column not in frame:
                frame[column] = defaults.get(column, np.nan)
                assumptions.append(f'{name}.{column}: configured default or missing value used.')
            if column not in CATEGORICAL:
                frame[column] = pd.to_numeric(frame[column], errors='raise')
                if np.isinf(frame[column].to_numpy()).any():
                    raise ValueError(f'{name}.{column}: infinite value.')
            else:
                frame[column] = frame[column].fillna('unknown').astype(str)
        for flag in ['planned_promotion', 'game_day', 'heart_healthy', 'heat_event']:
            if not frame[flag].dropna().isin([0, 1]).all():
                raise ValueError(f'{name}.{flag} must be 0 or 1.')
        if (frame.promotion_intensity.dropna() < 0).any():
            raise ValueError('Promotion intensity must be nonnegative in a consistent scale.')
        if frame.duplicated(KEYS + ['week']).any():
            raise ValueError(f'{name}: duplicate SKU/customer/week rows; aggregate first.')
        frame = frame.merge(products, on='sku', how='left', validate='many_to_one', indicator=True)
        if (frame._merge != 'both').any():
            raise ValueError(f'{name}: unknown SKU.')
        frame = frame.drop(columns='_merge').sort_values(['week'] + KEYS).reset_index(drop=True)
        frames.append(frame)
    history, future = frames
    if history.empty or future.empty or future.week.min() != history.week.max() + pd.Timedelta(weeks=1):
        raise ValueError('Future must begin exactly one week after the final history week.')
    # Do not silently interpret missing orders as zero demand.
    for key, group in history.groupby(KEYS):
        expected = pd.date_range(group.week.min(), history.week.max(), freq='W-MON')
        if list(group.week) != list(expected):
            raise ValueError(f'History has missing weeks for {key}; explicitly supply zero or corrected sales.')
        needed = config['min_training_weeks'] + config['validation_folds'] * config['validation_horizon_weeks']
        if len(group) < needed:
            raise ValueError(f'{key}: need at least {needed} weeks for configured validation.')
    historical_keys = set(map(tuple, history[KEYS].to_numpy()))
    future_keys = set(map(tuple, future[KEYS].to_numpy()))
    if future_keys != historical_keys:
        raise ValueError('Future must contain every historical SKU/customer series; new SKUs need a separate cold-start policy.')
    expected = pd.date_range(future.week.min(), future.week.max(), freq='W-MON')
    for key, group in future.groupby(KEYS):
        if list(group.week) != list(expected):
            raise ValueError(f'Future missing weeks for {key}.')
    promotions = read('promotions')
    require(promotions, ['start_week', 'end_week', 'sku', 'customer', 'promotion_type',
                         'promotion_intensity', 'game_day', 'heart_healthy'], 'promotions')
    for c in ['sku', 'customer', 'promotion_type']:
        if promotions[c].isna().any():
            raise ValueError('Promotion identifiers/type cannot be blank.')
        promotions[c] = promotions[c].astype(str)
    for c in ['start_week', 'end_week']:
        promotions[c] = pd.to_datetime(promotions[c], errors='raise')
        if promotions[c].isna().any() or (promotions[c].dt.dayofweek != 0).any():
            raise ValueError('Promotion dates must be Mondays.')
    if (promotions.start_week > promotions.end_week).any():
        raise ValueError('Promotion start exceeds end.')
    for c in ['promotion_intensity', 'game_day', 'heart_healthy']:
        promotions[c] = pd.to_numeric(promotions[c], errors='raise')
        if not np.isfinite(promotions[c]).all() or (promotions[c] < 0).any():
            raise ValueError('Invalid promotion input.')
    if not promotions[['game_day', 'heart_healthy']].isin([0, 1]).all().all():
        raise ValueError('Campaign flags must be 0/1.')
    assumptions.append('52-week seasonal lag approximates same period last year; no agronomic weather rule is inferred.')
    assumptions.append('Forecast horizon demand is aggregated into a single production pool; harvest timing, inventory, grade and land certification are not modeled.')
    assumptions.append('Harvest/packing capacities, when supplied, cap horizon-average throughput only; weekly peaks and planting/maturity timing are not scheduled. Planting dates and maturity are recorded planning context.')
    for name in ['available_irrigation_acre_ft', 'maximum_harvest_lbs_per_week', 'maximum_packing_lbs_per_week']:
        if config.get('farm_constraints', {}).get(name) is None:
            assumptions.append(f'{name}: not supplied; corresponding resource constraint is not assessed.')
    for c in WEATHER:
        if history[c].isna().any() or future[c].isna().any():
            assumptions.append(f'{c}: missing weather is imputed from training data, or zero if entirely unavailable; no weather effect can be learned without observations.')
    return history, future, crops, promotions, config, assumptions
