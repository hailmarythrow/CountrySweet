"""Command-line entry point: python main.py --data data --output outputs."""
import argparse
import json
from pathlib import Path
from data_loader import load_inputs
from forecast import fit_model, validate
from scenarios import run_scenarios

def main():
    parser = argparse.ArgumentParser(description='Weekly SKU forecasting and crop acreage planning')
    parser.add_argument('--data', type=Path, default=Path('data'))
    parser.add_argument('--output', type=Path, default=Path('outputs'))
    args = parser.parse_args()
    history, future, crops, promotions, config, assumptions = load_inputs(args.data)
    if config.get('synthetic_demo', False):
        print('SYNTHETIC DEMO ONLY: no Country Sweet Produce / Bako Sweet company data.')
    accuracy, backtest = validate(history, config)
    model = fit_model(history, config)
    forecasts, acreage, comparisons = run_scenarios(history, future, crops, promotions, config,
                                                    model, args.data, assumptions, backtest)
    if history.planned_promotion.nunique() < 2:
        assumptions.append('History lacks both promoted and unpromoted observations; promotion response is unsupported.')
    unseen = set(forecasts.promotion_type) - set(history.promotion_type)
    if unseen:
        assumptions.append(f'Unseen promotion types {sorted(unseen)} have no learned type-specific effect.')
    if not comparisons.coverage_satisfied.all():
        assumptions.append('Some scenarios do not cover demand plus safety stock. Review shortages and penalties, or enable require_full_coverage.')
    assumptions.append('Promotion coefficients capture predictive associations, not randomized causal effects; historical selection/confounding may remain.')
    assumptions.append('Backtest uses historical contemporaneous weather and recorded promotion plans as known inputs; deploy using only weather/plans available at the forecast origin. Archived forecasts are needed for fully realistic weather validation.')
    args.output.mkdir(parents=True, exist_ok=True)
    for name, frame in [('forecast', forecasts), ('acreage_recommendation', acreage),
                         ('scenario_comparison', comparisons), ('model_accuracy', accuracy),
                         ('backtest_predictions', backtest)]:
        frame['synthetic_demo'] = config.get('synthetic_demo', False)
        frame.to_csv(args.output / f'{name}.csv', index=False)
    report = dict(assumptions=sorted(set(assumptions)), config=config,
                  history_end=str(history.week.max().date()), horizon_weeks=future.week.nunique(),
                  acreage_forecast_model='machine_learning')
    (args.output / 'assumptions.json').write_text(json.dumps(report, indent=2))
    print(comparisons.to_string(index=False))
    print(f'Outputs saved to {args.output.resolve()}')
    print('Review assumptions.json before interpreting results.')

if __name__ == '__main__':
    main()
