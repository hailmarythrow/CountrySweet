"""Business checks: empirical ranges, risk posture and physical resource limits."""
import unittest
import pandas as pd
import numpy as np
from optimizer import optimize
from uncertainty import crop_demand_ranges

class ResourceTests(unittest.TestCase):
    def setUp(self):
        self.crops = pd.DataFrame([dict(crop_group='A', organic_status='organic', expected_lbs_per_acre=1000.,
            packout_rate=.8, min_acres=0., max_acres=100., safety_stock_pct=0., cost_per_acre=1.,
            shortage_penalty_per_lb=10., excess_penalty_per_lb=1., water_need_acre_ft_per_acre=2.)])
        self.forecast = pd.DataFrame([dict(week=pd.Timestamp('2026-01-05'), crop_group='A', organic_status='organic', prediction_lbs=8000.)])
        self.config = dict(total_available_acres=100., yield_multipliers={'normal':1.}, require_full_coverage=False)
        self.ranges = pd.DataFrame([dict(crop_group='A', organic_status='organic', low_demand_lbs=6400., expected_demand_lbs=8000., high_demand_lbs=9600.)])

    def test_posture_changes_acres_and_exposes_expected_shortage(self):
        results = [optimize(self.forecast, self.crops, dict(self.config, planning_posture=p), 'normal', self.ranges)
                   for p in ['Conservative', 'Balanced', 'Demand-protective']]
        self.assertEqual([r.recommended_acres.iloc[0] for r in results], [8., 10., 12.])
        self.assertEqual(results[0].shortage_vs_expected_lbs.iloc[0], 1600.)

    def test_water_capacity_and_storage_loss(self):
        config = dict(self.config, farm_constraints={'available_irrigation_acre_ft':10.})
        water = optimize(self.forecast, self.crops, config, 'normal')
        self.assertEqual(water.recommended_acres.iloc[0], 5.)
        self.assertEqual(water.shortage_lbs.iloc[0], 4000.)
        storage = optimize(self.forecast, self.crops.assign(storage_loss_rate=.2), self.config, 'normal')
        self.assertEqual(storage.recommended_acres.iloc[0], 12.5)
        config = dict(self.config, farm_constraints={'maximum_packing_lbs_per_week':4000.})
        capacity = optimize(self.forecast, self.crops, config, 'normal')
        self.assertEqual(capacity.usable_supply_lbs.iloc[0], 4000.)
        with self.assertRaises(ValueError):
            optimize(self.forecast, self.crops.drop(columns='water_need_acre_ft_per_acre'),
                     dict(self.config, farm_constraints={'available_irrigation_acre_ft':10.}), 'normal')

    def test_ranges_from_holdout_errors_not_fixed_percentages(self):
        scored = pd.DataFrame([dict(model='machine_learning', crop_group='A', organic_status='organic', fold=i,
            actual_lbs=8000. + error, prediction_lbs=8000.) for i, error in enumerate([-1000., 0., 2000.])])
        notes=[]
        ranges = crop_demand_ranges(self.forecast, scored, dict(validation_horizon_weeks=1, validation_folds=3), notes)
        self.assertAlmostEqual(ranges.low_demand_lbs.iloc[0], 7200.)
        self.assertAlmostEqual(ranges.high_demand_lbs.iloc[0], 9600.)
        self.assertTrue(any('weakly supported' in s for s in notes))
