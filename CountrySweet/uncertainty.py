"""Indicative ranges from actual errors in chronological holdout forecasts.

These are empirical error quantiles, not calibrated confidence intervals. Crop
totals are calculated from entire holdout blocks, retaining within-block sales
correlation. A small number of blocks means weak tail evidence, which is flagged.
"""
import numpy as np
import pandas as pd

def crop_demand_ranges(forecast, backtest, config, assumptions, error_multiplier=1.):
    keys = ['crop_group', 'organic_status']
    expected = forecast.groupby(keys).prediction_lbs.sum().rename('expected_demand_lbs').reset_index()
    scored = backtest[backtest.model == 'machine_learning'].copy()
    scored['error_lbs'] = scored.actual_lbs - scored.prediction_lbs
    errors = scored.groupby(keys + ['fold']).error_lbs.sum().reset_index()
    scale = forecast.week.nunique() / config['validation_horizon_weeks']
    low_q, high_q = .1, .9
    rows = []
    for _, row in expected.iterrows():
        values = errors[(errors.crop_group == row.crop_group) & (errors.organic_status == row.organic_status)].error_lbs.to_numpy() * scale * error_multiplier
        if not len(values):
            raise ValueError('No holdout error evidence for a forecast crop group.')
        point = row.expected_demand_lbs
        # Keep the model's expected value inside the range even if errors are all one-sided.
        rows.append(dict(crop_group=row.crop_group, organic_status=row.organic_status,
            low_demand_lbs=max(0., min(point, point + np.quantile(values, low_q))),
            expected_demand_lbs=point,
            high_demand_lbs=max(point, point + np.quantile(values, high_q)),
            error_blocks=len(values), range_method='10th/90th percentile crop-block holdout errors; indicative, uncalibrated'))
    if config['validation_folds'] < 10:
        assumptions.append('Demand uncertainty has fewer than 10 chronological holdout blocks; low/high tails are indicative and weakly supported, not calibrated confidence limits.')
    if scale != 1:
        assumptions.append(f'Uncertainty block errors scaled by {scale:.3f} for the planning horizon; this assumes proportional error scaling and is not validated.')
    assumptions.append('Summed crop low/high bounds are a planning envelope, not a joint confidence interval. Errors may shift under new promotions/weather.')
    return pd.DataFrame(rows)
