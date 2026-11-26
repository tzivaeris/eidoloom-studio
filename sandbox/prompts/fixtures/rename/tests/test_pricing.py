import unittest

from pricing import calc
from orders import order_total, recalculate


class CalcTests(unittest.TestCase):
    def test_calc_plain(self):
        self.assertEqual(calc([1, 2, 3]), 6)

    def test_calc_tax(self):
        self.assertEqual(calc([10, 20], 0.1), 33.0)

    def test_order_total(self):
        self.assertEqual(order_total([(2, 5.0)], 0.2), 12.0)

    def test_recalculate(self):
        self.assertEqual(recalculate([(1, 10.0)], 0.5), 15.0)


if __name__ == "__main__":
    unittest.main()
