"""Create deliberately synthetic classroom inputs; never represents company data.

Run explicitly. The generator refuses to overwrite any existing data folder.
The artificial sales equation below is ONLY a test-data generator. Forecasting
does not use its promotion uplift or weather coefficients.
"""
import json
from pathlib import Path
import argparse
import numpy as np
import pandas as pd

def generate(directory):
    directory = Path(directory)
    if directory.exists():
        raise ValueError('Demo destination already exists; choose a new directory to protect your data.')
    directory.mkdir(parents=True)
    rng = np.random.default_rng(42)
    products = pd.DataFrame([
        ['DEMO-C-BAG', 'DEMO-orange', 'conventional', 'bag'],
        ['DEMO-C-BULK', 'DEMO-orange', 'conventional', 'bulk'],
        ['DEMO-O-BAG', 'DEMO-orange', 'organic', 'bag'],
        ['DEMO-P-BAG', 'DEMO-purple', 'conventional', 'bag']],
        columns=['sku', 'crop_group', 'organic_status', 'package_format'])
    products.to_csv(directory / 'products.csv', index=False)
    crops = pd.DataFrame([
        ['DEMO-orange', 'conventional', 15000, .8, 600, 1, 40, .1, 4, .02],
        ['DEMO-orange', 'organic', 12000, .75, 800, 1, 20, .1, 5, .02],
        ['DEMO-purple', 'conventional', 13000, .8, 650, 1, 20, .1, 4, .02]],
        columns=['crop_group', 'organic_status', 'expected_lbs_per_acre', 'packout_rate',
                 'cost_per_acre', 'min_acres', 'max_acres', 'safety_stock_pct',
                 'shortage_penalty_per_lb', 'excess_penalty_per_lb'])
    crops.to_csv(directory / 'crops.csv', index=False)
    rows = []
    weeks = pd.date_range('2023-01-02', periods=156, freq='W-MON')
    # Promotions are scheduled deterministically (not randomly assigned).
    for j, sku in enumerate(products.sku):
        for i, week in enumerate(weeks):
            promoted = int(i % 12 in [3, 4])
            intensity = .2 if promoted else 0
            game = int(promoted and week.month == 2)
            heart = int(promoted and week.month == 3)
            temp = 65 + 15 * np.sin(i * 2 * np.pi / 52)
            sales = (2500 + j * 350) * (1 + .15 * np.sin(i * 2 * np.pi / 52))
            # This invented response helps verify that the fitted model reacts to plans.
            sales *= 1 + .7 * promoted + .2 * game + .1 * heart
            sales += rng.normal(0, 90)
            rows.append(dict(week=week, sku=sku, customer='DEMO-retailer', sales_lbs=max(0, sales),
                planned_promotion=promoted, promotion_type='display' if promoted else 'none',
                promotion_intensity=intensity, game_day=game, heart_healthy=heart,
                holiday_period='year_end' if week.month == 12 else 'none',
                average_temperature=temp, maximum_temperature=temp + 10,
                minimum_temperature=temp - 10, precipitation=max(0, rng.normal(.2, .1)),
                growing_degree_days=np.nan, heat_event=int(temp > 77)))
    pd.DataFrame(rows).to_csv(directory / 'history.csv', index=False)
    future = []
    for week in pd.date_range(weeks[-1] + pd.Timedelta(weeks=1), periods=12, freq='W-MON'):
        for sku in products.sku:
            future.append(dict(week=week, sku=sku, customer='DEMO-retailer',
                planned_promotion=0, promotion_type='none', promotion_intensity=0,
                game_day=0, heart_healthy=0, holiday_period='none',
                average_temperature=65, maximum_temperature=75, minimum_temperature=55,
                precipitation=.2, growing_degree_days=np.nan, heat_event=0))
    pd.DataFrame(future).to_csv(directory / 'future.csv', index=False)
    pd.DataFrame([dict(start_week=future[8]['week'], end_week=future[20]['week'], sku='*',
        customer='*', promotion_type='display', promotion_intensity=.2, game_day=1,
        heart_healthy=0)]).to_csv(directory / 'promotions.csv', index=False)
    config = dict(synthetic_demo=True, total_available_acres=60,
        yield_multipliers=dict(normal=1, favorable=1.1, adverse=.75),
        high_demand_multiplier=1.25, require_full_coverage=False,
        missing_cost_per_acre=0, validation_horizon_weeks=12, validation_folds=3,
        min_training_weeks=52, moving_average_weights=[1, 2, 3, 4],
        trees=100, min_samples_leaf=2, seed=42,
        optional_defaults=dict(customer='ALL', holiday_period='unknown', planned_promotion=0,
            promotion_type='none', promotion_intensity=0, game_day=0, heart_healthy=0),
        assumptions=['ALL DATA AND PARAMETERS ARE SYNTHETIC CLASSROOM EXAMPLES, NOT COMPANY FACTS.',
            'Yield, acreage, cost, penalties, safety stock, weather and multipliers require user verification.',
            'Missing optional promotion flags default to zero; confirm that missing really means no planned promotion.',
            'Sales/order pounds are used as demand; stockouts and unfilled orders may censor true demand.',
            'High-demand multiplier is a user-selected stress case, not a statistical prediction interval.',
            'Penalty and production costs must use the same currency. Acreage is continuous.'])
    (directory / 'config.json').write_text(json.dumps(config, indent=2))
    print(f'Synthetic inputs created at {directory.resolve()}')

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path, default=Path('data'))
    generate(parser.parse_args().data)
