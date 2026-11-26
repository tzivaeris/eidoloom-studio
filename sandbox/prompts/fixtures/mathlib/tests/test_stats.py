import unittest

import stats


class TotalTests(unittest.TestCase):
    def test_total(self):
        self.assertEqual(stats.total([1, 2, 3]), 6)

    def test_total_empty(self):
        self.assertEqual(stats.total([]), 0)

    def test_total_floats(self):
        self.assertAlmostEqual(stats.total([0.5, 0.25]), 0.75)


if __name__ == "__main__":
    unittest.main()
