import unittest

import stats


class StatsTests(unittest.TestCase):
    def test_mean(self):
        self.assertEqual(stats.mean([1, 2, 3, 4]), 2.5)

    def test_mean_empty(self):
        with self.assertRaises(ValueError):
            stats.mean([])

    def test_median_odd(self):
        self.assertEqual(stats.median([5, 1, 3]), 3)

    def test_median_even(self):
        self.assertEqual(stats.median([4, 1, 3, 2]), 2.5)

    def test_variance(self):
        self.assertAlmostEqual(stats.variance([2, 4, 4, 4, 5, 5, 7, 9]), 4.0)

    def test_clamp(self):
        self.assertEqual(stats.clamp(15, 0, 10), 10)
        self.assertEqual(stats.clamp(-3, 0, 10), 0)


if __name__ == "__main__":
    unittest.main()
