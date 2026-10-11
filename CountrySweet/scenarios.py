"""Planned promotions are future-known inputs; their effects are learned by ML."""
import pandas as pd
from forecast import METHODS, predict
from optimizer import optimize
from features import WEATHER
from uncertainty import crop_demand_ranges

def promotion_plan(future, promotions):
    planned = future.copy()
    # Intensity may be fractional even when future.csv initially contains zeros.
    planned['promotion_intensity'] = planned.promotion_intensity.astype(float)
    touched = pd.Series(False, index=planned.index)
    for _, row in promotions.iterrows():
        mask = planned.week.between(row.start_week, row.end_week)
        for key in ['sku', 'customer']:
            if row[key] != '*':
                mask &= planned[key] == str(row[key])
        if not mask.any():
            raise ValueError('Promotion period/target matches no future rows.')
        if (mask & touched).any():
            raise ValueError('Overlapping promotion records require an explicit combined promotion type.')
        touched |= mask
        planned.loc[mask, 'planned_promotion'] = 1
        for c in ['promotion_type', 'promotion_intensity', 'game_day', 'heart_healthy']:
            planned.loc[mask, c] = row[c]
    return planned

def run_scenarios(history, future, crops, promotions, config, model, input_directory, assumptions, backtest=None):
    base = future.copy()
    # The base scenario is an explicit no-promotion counterfactual.
    base['planned_promotion'] = 0
    base['promotion_type'] = 'none'
    base['promotion_intensity'] = 0
    base['game_day'] = 0
    base['heart_healthy'] = 0
    promo = promotion_plan(future, promotions)
    specs = [('Base demand / Normal weather', base, 'normal', 1),
             ('Promotion / Normal weather', promo, 'normal', 1),
             ('Base demand / Adverse weather', base, 'adverse', 1),
             ('Promotion / Adverse weather', promo, 'adverse', 1),
             ('High-demand scenario', promo, 'normal', config['high_demand_multiplier'])]
    if config.get('include_favorable_scenarios', False):
        specs += [('Base demand / Favorable weather', base, 'favorable', 1),
                  ('Promotion / Favorable weather', promo, 'favorable', 1)]
    forecasts, acreages, comparisons = [], [], []
    for name, rows, weather, factor in specs:
        rows = rows.copy()
        path = input_directory / f'weather_{weather}.csv'
        if path.exists():
            overrides = pd.read_csv(path, parse_dates=['week'])
            if overrides.week.duplicated().any() or set(overrides.week) != set(rows.week):
                raise ValueError(f'{path.name}: supply exactly one row for every future week.')
            for column in WEATHER:
                if column in overrides:
                    values = pd.to_numeric(overrides[column], errors='raise')
                    if values.isna().any() or values.isin([float('inf'), float('-inf')]).any():
                        raise ValueError(f'{path.name}: weather overrides must be finite.')
                    if column == 'heat_event' and not values.isin([0, 1]).all():
                        raise ValueError('Scenario heat_event must be 0/1.')
                    rows[column] = rows.week.map(pd.Series(values.to_numpy(), index=overrides.week))
        elif weather != 'normal':
            assumptions.append(f'No weather_{weather}.csv: demand weather inputs stay at future.csv values; {weather} weather changes yield only.')
        scenario_forecasts = []
        for method in METHODS:
            predicted = predict(history, rows, method, config, model)
            predicted['prediction_lbs'] *= factor
            predicted['scenario'] = name
            scenario_forecasts.append(predicted)
        combined = pd.concat(scenario_forecasts, ignore_index=True)
        # ML supplies the acreage optimizer so planned promotion variables affect the plan.
        selected = combined[combined.model == 'machine_learning']
        if backtest is not None:
            ranges = crop_demand_ranges(selected, backtest, config, assumptions, factor)
        else:
            ranges = None
        allocation = optimize(selected, crops, config, weather, ranges)
        allocation['scenario'] = name
        comparisons.append(dict(scenario=name, weather=weather, demand_multiplier=factor,
            forecast_demand_lbs=selected.prediction_lbs.sum(),
            low_demand_lbs=allocation.low_demand_lbs.sum(),
            expected_demand_lbs=allocation.expected_demand_lbs.sum(),
            high_demand_lbs=allocation.high_demand_lbs.sum(),
            planning_posture=config.get('planning_posture', 'Balanced'),
            planning_demand_lbs=allocation.planning_demand_lbs.sum(),
            shortage_vs_expected_lbs=allocation.shortage_vs_expected_lbs.sum(),
            water_used_acre_ft=allocation.water_used_acre_ft.sum(min_count=1),
            target_with_safety_lbs=allocation.target_with_safety_lbs.sum(),
            acres=allocation.recommended_acres.sum(),
            unallocated_acres=config['total_available_acres'] - allocation.recommended_acres.sum(),
            usable_supply_lbs=allocation.usable_supply_lbs.sum(),
            shortage_lbs=allocation.shortage_lbs.sum(), excess_lbs=allocation.excess_lbs.sum(),
            production_cost=allocation.production_cost.sum(), shortage_cost=allocation.shortage_cost.sum(),
            excess_cost=allocation.excess_cost.sum(), objective_cost=allocation.objective_cost.sum(),
            coverage_satisfied=allocation.coverage_satisfied.all()))
        forecasts.append(combined)
        acreages.append(allocation)
    return pd.concat(forecasts), pd.concat(acreages), pd.DataFrame(comparisons)
