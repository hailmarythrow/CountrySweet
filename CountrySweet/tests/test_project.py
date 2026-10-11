"""Business constraints and leakage checks using synthetic inputs only."""
import tempfile
import unittest
from pathlib import Path
import numpy as np
import pandas as pd
from generate_demo import generate
from data_loader import load_inputs
from forecast import predict, fit_model, validate, metrics
from optimizer import optimize
from scenarios import promotion_plan
from features import training_features

class PlanningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.directory = Path(cls.temp.name) / 'data'
        generate(cls.directory)
        cls.history, cls.future, cls.crops, cls.promotions, cls.config, _ = load_inputs(cls.directory)
        cls.config['trees'] = 30

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_recursive_naive_and_lags(self):
        prediction = predict(self.history, self.future, 'naive', self.config)
        for (sku, customer), rows in prediction.groupby(['sku', 'customer']):
            last = self.history[(self.history.sku == sku) & (self.history.customer == customer)].iloc[-1].sales_lbs
            np.testing.assert_allclose(rows.prediction_lbs, last)
        one = self.history[self.history.sku == self.history.sku.iloc[0]].copy()
        features = training_features(one)
        self.assertTrue(pd.isna(features.lag_1.iloc[0]))
        self.assertAlmostEqual(features.lag_1.iloc[1], one.sales_lbs.iloc[0])

    def test_holdout_sales_never_used(self):
        config = dict(self.config, validation_folds=1, validation_horizon_weeks=4)
        _, first = validate(self.history, config)
        changed = self.history.copy()
        cutoff = sorted(changed.week.unique())[-4]
        changed.loc[changed.week >= cutoff, 'sales_lbs'] *= 100
        _, second = validate(changed, config)
        np.testing.assert_allclose(first.prediction_lbs, second.prediction_lbs)

    def test_promotion_response_learned(self):
        model = fit_model(self.history, self.config)
        base = predict(self.history, self.future, 'machine_learning', self.config, model)
        planned = promotion_plan(self.future, self.promotions)
        promotion = predict(self.history, planned, 'machine_learning', self.config, model)
        # Positive response is expected ONLY because this test's invented history has it.
        self.assertGreater(promotion.prediction_lbs.sum(), base.prediction_lbs.sum())

    def test_acreage_and_organic_separation(self):
        forecast = self.future.copy()
        forecast['prediction_lbs'] = 1000.
        result = optimize(forecast, self.crops, self.config, 'normal')
        self.assertLessEqual(result.recommended_acres.sum(), self.config['total_available_acres'] + 1e-8)
        np.testing.assert_allclose(result.usable_supply_lbs + result.shortage_lbs - result.excess_lbs,
                                   result.target_with_safety_lbs, atol=1e-6)
        crops = self.crops.copy()
        crops.loc[crops.organic_status == 'organic', ['min_acres', 'max_acres']] = 0
        result = optimize(forecast, crops, self.config, 'normal')
        organic = result[result.organic_status == 'organic'].iloc[0]
        self.assertAlmostEqual(organic.usable_supply_lbs, 0)
        self.assertAlmostEqual(organic.shortage_lbs, organic.target_with_safety_lbs)
        with self.assertRaises(ValueError):
            optimize(forecast, crops, dict(self.config, require_full_coverage=True), 'normal')

    def test_weather_adjustment_and_infeasible_minimums(self):
        forecast = self.future.assign(prediction_lbs=1000.)
        normal = optimize(forecast, self.crops, self.config, 'normal')
        adverse = optimize(forecast, self.crops, self.config, 'adverse')
        self.assertGreater(adverse.recommended_acres.sum(), normal.recommended_acres.sum())
        with self.assertRaises(ValueError):
            optimize(forecast, self.crops, dict(self.config, total_available_acres=1), 'normal')

    def test_gaps_rejected(self):
        original = pd.read_csv(self.directory / 'history.csv')
        try:
            # Remove an interior week rather than silently treating it as zero.
            original.drop(index=10).to_csv(self.directory / 'history.csv', index=False)
            with self.assertRaises(ValueError):
                load_inputs(self.directory)
        finally:
            original.to_csv(self.directory / 'history.csv', index=False)

    def test_zero_actual_metrics(self):
        result = metrics(pd.DataFrame({'actual_lbs': [0., 10.], 'prediction_lbs': [5., 8.]}))
        self.assertAlmostEqual(result['WAPE_pct'], 70)
        self.assertAlmostEqual(result['MAPE_pct'], 20)
        self.assertEqual(result['MAPE_excluded_zero_rows'], 1)
        self.assertAlmostEqual(result['bias_lbs'], 1.5)

if __name__ == '__main__':
    unittest.main()
