"""Independent boundary cases for derived market returns; raw arrays stay intact."""
import copy
import json
import unittest

import numpy as np
from factorpool.market_returns import daily_returns, return_policy


class MarketReturnTests(unittest.TestCase):
    def assert_return(self, close, previous, volume, turnover, member, expected):
        arrays = [np.array([[value]], dtype=np.float64)
                  for value in (close, previous, volume, turnover, member)]
        before = [array.copy() for array in arrays]
        for array in arrays:
            array.flags.writeable = False
        actual = daily_returns(*arrays)
        self.assertEqual(actual.shape, (1, 1))
        if expected is None:
            self.assertTrue(np.isnan(actual[0, 0]))
        else:
            self.assertAlmostEqual(actual[0, 0], expected)
        for array, original in zip(arrays, before):
            np.testing.assert_array_equal(array, original)

    def test_positive_volume_uses_prices_even_without_amount_or_membership(self):
        for turnover in (0., 10., -1., None, np.nan, np.inf, -np.inf):
            with self.subTest(turnover=turnover):
                self.assert_return(12, 10, 5, turnover, 0, .2)
        self.assert_return(12, 10, np.nextafter(0., 1.), 0, 1, .2)
        self.assert_return(0, 10, 5, np.nan, 1, -1)
        for close, previous in ((12, 0), (12, -1), (12, None), (12, np.nan), (12, np.inf),
                                (None, 10), (np.nan, 10), (np.inf, 10), (-np.inf, 10),
                                (-1, 10), (1e308, 1e-308)):
            with self.subTest(close=close, previous=previous):
                self.assert_return(close, previous, 5, 0, 1, None)

    def test_confirmed_zero_volume_and_amount_are_zero_without_valid_prices(self):
        for volume in (0., -0.):
            for turnover in (0., -0.):
                for close, previous in ((12, 10), (None, None), (np.nan, np.nan),
                                        (np.inf, -1), (-1, 0), (0, 0)):
                    with self.subTest(volume=volume, turnover=turnover, close=close, previous=previous):
                        self.assert_return(close, previous, volume, turnover, 1, 0)

    def test_unknown_or_invalid_volume_is_missing_instead_of_assumed_no_trade(self):
        for volume in (None, np.nan, np.inf, -np.inf, -1., -np.nextafter(0., 1.)):
            with self.subTest(volume=volume):
                self.assert_return(12, 10, volume, 0, 1, None)
        for turnover in (1., -1., None, np.nan, np.inf, -np.inf):
            with self.subTest(turnover=turnover):
                self.assert_return(12, 10, 0, turnover, 1, None)
        for member in (0., -1., np.nan, np.inf, -np.inf):
            with self.subTest(member=member):
                self.assert_return(12, 10, 0, 0, member, None)

    def test_mixed_matrix_preserves_zero_total_loss_and_missing_cells(self):
        close = np.array([[12., np.nan, 0., 12.], [12., 12., 12., 12.]])
        previous = np.array([[10., np.nan, 10., 10.], [10., 10., 10., 10.]])
        volume = np.array([[5., 0., 5., np.nan], [0., 0., -1., 5.]])
        turnover = np.array([[50., 0., np.nan, 0.], [0., 2., 0., np.nan]])
        member = np.array([[1., 1., 1., 1.], [0., 1., 1., 0.]])
        original = [array.copy() for array in (close, previous, volume, turnover, member)]
        expected = [[.2, 0., -1., np.nan], [np.nan, np.nan, np.nan, .2]]
        np.testing.assert_allclose(daily_returns(close, previous, volume, turnover, member), expected,
                                   rtol=0, atol=1e-15, equal_nan=True)
        for actual, before in zip((close, previous, volume, turnover, member), original):
            np.testing.assert_array_equal(actual, before)

    def test_policy_is_stable_json_and_calls_cannot_mutate_the_next_manifest(self):
        first = return_policy()
        self.assertEqual(first['version'], 'volume-zero-v1')
        self.assertTrue({'normal', 'no_trade', 'unknown', 'suspension', 'lifetime'}.issubset(first))
        canonical = json.dumps(first, sort_keys=True, allow_nan=False)
        self.assertEqual(json.dumps(return_policy(), sort_keys=True, allow_nan=False), canonical)
        first['version'] = 'unrelated-test-rule'
        self.assertEqual(json.dumps(return_policy(), sort_keys=True, allow_nan=False), canonical)

    def test_report_contract_distinguishes_synthetic_and_cleaned_returns(self):
        from factorpool.runner import return_contract
        for synthetic, version in ((True, 'synthetic-price-ratio-v1'),):
            with self.subTest(synthetic=synthetic):
                payload = dict(synthetic=synthetic)
                policy, description, price = return_contract(payload)
                self.assertEqual(policy['version'], version)
                self.assertIn('no volume-based zeroing', description)
                self.assertEqual(price, 'close / prev_close - 1')
                self.assertEqual(payload, dict(synthetic=synthetic))
        original = dict(synthetic=False, return_policy=return_policy())
        before = copy.deepcopy(original)
        policy, description, price = return_contract(original)
        self.assertEqual(policy['version'], 'volume-zero-v1')
        for clause in ('finite volume=0', 'total_turnover=0', 'finite volume>0', 'remain null'):
            self.assertIn(clause, description)
        self.assertIn('volume-aware', price)
        policy['version'] = 'caller-local-edit'
        self.assertEqual(original, before)

    def test_missing_or_unknown_market_policies_are_rejected(self):
        from factorpool.runner import return_contract
        payload = dict(synthetic=False, return_policy=dict(version='future-test-rule', audit=dict(values=[1])))
        before = copy.deepcopy(payload)
        with self.assertRaisesRegex(ValueError, 'Unsupported return policy'):
            return_contract(payload)
        self.assertEqual(payload, before)
        with self.assertRaisesRegex(ValueError, 'requires the current return_policy'):
            return_contract(dict(synthetic=False))
        with self.assertRaisesRegex(ValueError, 'Unsupported return policy'):
            return_contract(dict(synthetic=False, return_policy=dict(version='legacy-price-ratio-v1')))
        for value in (None, [], 'volume-zero-v1'):
            with self.subTest(policy=value):
                with self.assertRaises(ValueError):
                    return_contract(dict(synthetic=False, return_policy=value))
