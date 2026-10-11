"""Explanatory variables. Every sales lag uses only earlier weeks."""
import numpy as np
import pandas as pd

KEYS = ['sku', 'customer']
CATEGORICAL = ['sku', 'customer', 'organic_status', 'package_format', 'crop_group',
               'promotion_type', 'holiday_period']
NUMERIC = ['year', 'quarter', 'month', 'week_of_year', 'week_sin', 'week_cos', 'planned_promotion',
           'promotion_intensity', 'game_day', 'heart_healthy', 'average_temperature',
           'maximum_temperature', 'minimum_temperature', 'precipitation',
           'growing_degree_days', 'heat_event', 'lag_1', 'lag_2', 'mean_4', 'mean_8',
           'rolling_std_4', 'lag_52']
WEATHER = ['average_temperature', 'maximum_temperature', 'minimum_temperature',
           'precipitation', 'growing_degree_days', 'heat_event']

def calendar(frame):
    """Keep holiday/campaign flags supplied by the user; do not invent calendars."""
    out = frame.copy()
    out['year'] = out.week.dt.year
    out['quarter'] = out.week.dt.quarter
    out['month'] = out.week.dt.month
    out['week_of_year'] = out.week.dt.isocalendar().week.astype(int)
    out['week_sin'] = np.sin(2 * np.pi * out.week_of_year / 52.1775)
    out['week_cos'] = np.cos(2 * np.pi * out.week_of_year / 52.1775)
    return out

def create_features(df):
    """Adapt the user's CSP/Bako Sweet function while retaining customer isolation.

    Accept lbs_sold/promotion as aliases for sales_lbs/planned_promotion. Strict
    4/8-week windows match the supplied function. No future row enters a lag.
    Explicit campaign flags take priority; otherwise infer from promotion_type.
    """
    out = df.copy()
    for original, canonical in [('lbs_sold', 'sales_lbs'), ('promotion', 'planned_promotion')]:
        if original in out:
            if canonical in out and not pd.to_numeric(out[original]).equals(pd.to_numeric(out[canonical])):
                if not np.allclose(pd.to_numeric(out[original]), pd.to_numeric(out[canonical]), equal_nan=True):
                    raise ValueError(f'{original} and {canonical} disagree.')
            out[canonical] = out[original]
    if 'sales_lbs' not in out:
        raise ValueError('create_features requires lbs_sold or sales_lbs.')
    out['week'] = pd.to_datetime(out.week)
    if 'customer' not in out:
        out['customer'] = 'ALL'
    if out.duplicated(KEYS + ['week']).any():
        raise ValueError('Duplicate SKU/customer/week rows; aggregate before feature creation.')
    assumptions = []
    for column, default in [('planned_promotion', 0), ('promotion_intensity', 0), ('promotion_type', 'none')]:
        if column not in out:
            out[column] = default
            assumptions.append(f'{column}: default {default} assumed.')
    for column, token in [('game_day', 'game'), ('heart_healthy', 'heart')]:
        if column not in out:
            out[column] = out.promotion_type.astype(str).str.contains(token, case=False, na=False).astype(int)
            assumptions.append(f'{column}: inferred from promotion_type text.')
    out = calendar(out.sort_values(KEYS + ['week']))
    grouped = out.groupby(KEYS, sort=False)['sales_lbs']
    out['lag_1'] = grouped.shift(1)
    out['lag_2'] = grouped.shift(2)
    for n in (4, 8):
        out[f'rolling_{n}'] = grouped.transform(lambda s: s.shift(1).rolling(n).mean())
        out[f'mean_{n}'] = out[f'rolling_{n}']
    out['rolling_std_4'] = grouped.transform(lambda s: s.shift(1).rolling(4).std())
    # A 52-week lag is an explicit approximation to the same retail week last year.
    out['lag_52'] = grouped.shift(52)
    out.attrs['feature_assumptions'] = assumptions
    return out

def training_features(history):
    return create_features(history)

def next_features(rows, history):
    """At a fixed forecast origin, later weeks use predictions, never future sales."""
    out = calendar(rows)
    for i, row in out.iterrows():
        past = history[(history.sku == row.sku) & (history.customer == row.customer)
                       & (history.week < row.week)].sort_values('week')
        values = past.sales_lbs.to_numpy()
        out.loc[i, 'lag_1'] = values[-1] if len(values) else np.nan
        out.loc[i, 'lag_2'] = values[-2] if len(values) >= 2 else np.nan
        for n in (4, 8):
            out.loc[i, f'mean_{n}'] = values[-n:].mean() if len(values) >= n else np.nan
            out.loc[i, f'rolling_{n}'] = out.loc[i, f'mean_{n}']
        out.loc[i, 'rolling_std_4'] = values[-4:].std(ddof=1) if len(values) >= 4 else np.nan
        last_year = past.loc[past.week == row.week - pd.Timedelta(weeks=52), 'sales_lbs']
        out.loc[i, 'lag_52'] = last_year.iloc[0] if len(last_year) else np.nan
    return out
