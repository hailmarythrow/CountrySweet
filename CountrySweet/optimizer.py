"""Linear programming: allocate acreage to crop/status pairs, never packaging SKUs."""
import numpy as np
from scipy.optimize import linprog

def optimize(forecast, crops, config, weather, ranges=None):
    demand = forecast.groupby(['crop_group', 'organic_status']).prediction_lbs.sum().rename('demand_lbs')
    table = crops.merge(demand, on=['crop_group', 'organic_status'], how='left').fillna({'demand_lbs': 0})
    if ranges is not None:
        table = table.merge(ranges, on=['crop_group', 'organic_status'], how='left')
    for column in ['low_demand_lbs', 'expected_demand_lbs', 'high_demand_lbs']:
        if column not in table:
            table[column] = table.demand_lbs
        table[column] = table[column].fillna(table.demand_lbs)
    posture = config.get('planning_posture', 'Balanced')
    mapping = {'Conservative': 'low_demand_lbs', 'Balanced': 'expected_demand_lbs', 'Demand-protective': 'high_demand_lbs'}
    if posture not in mapping:
        raise ValueError('Invalid planning posture.')
    table['planning_demand_lbs'] = table[mapping[posture]]
    table['planning_posture'] = posture
    table['storage_loss_rate'] = table.get('storage_loss_rate', 0.)
    table['storage_loss_rate'] = table.storage_loss_rate.fillna(0.)
    if ((table.storage_loss_rate < 0) | (table.storage_loss_rate >= 1)).any():
        raise ValueError('Storage loss must be a decimal in [0,1).')
    multiplier = config['yield_multipliers'][weather]
    table['weather_adjusted_lbs_per_acre'] = table.expected_lbs_per_acre * multiplier
    usable = (table.weather_adjusted_lbs_per_acre * table.packout_rate * (1 - table.storage_loss_rate)).to_numpy()
    target = (table.planning_demand_lbs * (1 + table.safety_stock_pct)).to_numpy()
    n = len(table)
    # x = [acres for each crop/status, shortage pounds, excess pounds].
    # supply + shortage - excess = forecast pounds plus safety stock.
    # Separate organic/conventional rows prohibit supplying organic with conventional acres.
    balance = np.hstack([np.diag(usable), np.eye(n), -np.eye(n)])
    cost = np.r_[table.cost_per_acre, table.shortage_penalty_per_lb, table.excess_penalty_per_lb]
    constraints = [np.r_[np.ones(n), np.zeros(2 * n)]]
    limits = [config['total_available_acres']]
    water = config.get('farm_constraints', {}).get('available_irrigation_acre_ft')
    water_rates = table.get('water_need_acre_ft_per_acre')
    if water is not None:
        if water_rates is None or water_rates.isna().any():
            raise ValueError('Available water was supplied: enter water need for every crop group before optimizing.')
        constraints.append(np.r_[water_rates.to_numpy(), np.zeros(2*n)])
        limits.append(float(water))
    weeks = forecast.week.nunique()
    # Capacity is averaged across the horizon, not a weekly harvest schedule.
    for field, coefficient in [('maximum_harvest_lbs_per_week', table.weather_adjusted_lbs_per_acre),
                               ('maximum_packing_lbs_per_week', table.weather_adjusted_lbs_per_acre * table.packout_rate)]:
        capacity = config.get('farm_constraints', {}).get(field)
        if capacity is not None:
            constraints.append(np.r_[coefficient.to_numpy(), np.zeros(2*n)])
            limits.append(float(capacity) * weeks)
    if not np.isfinite(limits).all() or (np.asarray(limits) < 0).any():
        raise ValueError('Land, water and capacity limits must be finite and nonnegative.')
    if water_rates is not None and ((water_rates.dropna() < 0).any() or np.isinf(water_rates.dropna()).any()):
        raise ValueError('Water needs must be finite and nonnegative.')
    bounds = list(zip(table.min_acres, table.max_acres))
    # Optional hard coverage forces shortages to zero; infeasible runs are reported.
    shortage_bounds = [(0, 0 if config['require_full_coverage'] else None)] * n
    result = linprog(cost, A_ub=np.asarray(constraints), b_ub=limits,
                     A_eq=balance, b_eq=target,
                     bounds=bounds + shortage_bounds + [(0, None)] * n, method='highs')
    if not result.success:
        raise ValueError(f'Acreage optimization failed: {result.message}')
    table['recommended_acres'] = result.x[:n]
    table['usable_supply_lbs'] = result.x[:n] * usable
    table['shortage_vs_expected_lbs'] = np.maximum(table.expected_demand_lbs - table.usable_supply_lbs, 0)
    table['water_used_acre_ft'] = table.recommended_acres * water_rates if water_rates is not None else np.nan
    table['target_with_safety_lbs'] = target
    table['shortage_lbs'] = result.x[n:2*n]
    table['excess_lbs'] = result.x[2*n:]
    table['production_cost'] = table.recommended_acres * table.cost_per_acre
    table['shortage_cost'] = table.shortage_lbs * table.shortage_penalty_per_lb
    table['excess_cost'] = table.excess_lbs * table.excess_penalty_per_lb
    table['objective_cost'] = table[['production_cost', 'shortage_cost', 'excess_cost']].sum(axis=1)
    table['weather_scenario'] = weather
    table['yield_multiplier'] = multiplier
    table['coverage_satisfied'] = table.shortage_lbs < 1e-5
    return table
