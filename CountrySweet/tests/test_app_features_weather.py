"""Verify requested feature additions and local weather transformations."""
import unittest
import pandas as pd
import numpy as np
from features import create_features, next_features
from weather import aggregate_weekly
from weather import fetch_outlook
from unittest.mock import patch
import io
import json

class UserFeatureTests(unittest.TestCase):
    def test_aliases_customer_isolation_and_strict_windows(self):
        weeks = pd.date_range('2025-01-06', periods=9, freq='W-MON')
        df = pd.DataFrame([dict(week=w, sku='A', customer=c, lbs_sold=i + offset,
            promotion=1, promotion_type='Heart Healthy', game_day=1)
            for c, offset in [('one', 0), ('two', 1000)] for i, w in enumerate(weeks)])
        out = create_features(df.sample(frac=1, random_state=1))
        first = out[out.customer == 'one'].reset_index(drop=True)
        self.assertTrue(pd.isna(first.rolling_4.iloc[3]))
        self.assertEqual(first.rolling_4.iloc[4], 1.5)
        self.assertEqual(first.lag_2.iloc[4], 2)
        self.assertAlmostEqual(first.rolling_std_4.iloc[4], np.std([0, 1, 2, 3], ddof=1))
        second = out[out.customer == 'two'].reset_index(drop=True)
        self.assertEqual(second.lag_1.iloc[1], 1000)
        self.assertTrue((out.game_day == 1).all())  # Explicit flag is never overwritten.
        self.assertTrue((out.heart_healthy == 1).all())
        self.assertIn('year', out)
        self.assertIn('quarter', out)

    def test_training_and_recursive_definitions_match(self):
        weeks = pd.date_range('2025-01-06', periods=10, freq='W-MON')
        df = pd.DataFrame({'week': weeks, 'sku': 'A', 'customer': 'ALL', 'sales_lbs': np.arange(10.)})
        trained = create_features(df).iloc[-1]
        predicted = next_features(df.iloc[-1:].drop(columns='sales_lbs'), df.iloc[:-1]).iloc[0]
        for name in ['lag_1', 'lag_2', 'mean_4', 'mean_8', 'rolling_std_4']:
            self.assertAlmostEqual(trained[name], predicted[name])

class WeatherTests(unittest.TestCase):
    def setUp(self):
        self.daily = pd.DataFrame(dict(date=pd.date_range('2025-01-06', periods=7),
            average_temperature=50., maximum_temperature=60., minimum_temperature=40., precipitation=.1))

    def test_weekly_units_and_user_rules(self):
        row = aggregate_weekly(self.daily, ['2025-01-06'], 55., 45.).iloc[0]
        self.assertAlmostEqual(row.precipitation, .7)
        self.assertEqual(row.growing_degree_days, 35)
        self.assertEqual(row.heat_event, 1)
        row = aggregate_weekly(self.daily, ['2025-01-06']).iloc[0]
        self.assertTrue(pd.isna(row.heat_event))
        self.assertTrue(pd.isna(row.growing_degree_days))

    def test_incomplete_weather_rejected(self):
        with self.assertRaises(ValueError):
            aggregate_weekly(self.daily.iloc[:-1], ['2025-01-06'])

    def test_live_outlook_keeps_missing_final_day_explicit(self):
        dates = pd.date_range('2026-10-04', periods=16)
        hours = pd.date_range('2026-10-04', periods=16*24, freq='h')
        payload = {'current': {'time': '2026-10-04T08:00', 'temperature_2m': 70},
            'daily': {'time': dates.strftime('%Y-%m-%d').tolist(),
                'temperature_2m_max': [80]*15 + [None], 'temperature_2m_min': [60]*15 + [None],
                'precipitation_sum': [0]*15 + [None], 'precipitation_probability_max': [5]*16},
            'hourly': {'time': hours.strftime('%Y-%m-%dT%H:%M').tolist(),
                       'temperature_2m': [70]*(15*24) + [None]*24}}
        with patch('weather.urlopen', return_value=io.BytesIO(json.dumps(payload).encode())):
            daily, current, metadata = fetch_outlook(35.057549, -118.966403)
        self.assertEqual(len(daily), 16)
        self.assertEqual(current['temperature_2m'], 70)
        self.assertTrue(pd.isna(daily.maximum_temperature.iloc[-1]))
        self.assertTrue(pd.isna(daily.average_temperature.iloc[-1]))
        self.assertEqual(metadata['unavailable_dates'], ['2026-10-19'])
