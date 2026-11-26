import unittest

import shop


def make_prices():
    return [1, 2, 3]


def check_positive(value):
    assert value > 0


class TotalTest(unittest.TestCase):
    def setUp(self):
        self.prices = make_prices()

    def helper_total(self):
        return shop.total(self.prices)

    def test_total(self):
        self.assertEqual(self.helper_total(), 6)

    def test_empty(self):
        self.assertEqual(shop.total([]), 0)

    def test_single(self):
        self.assertEqual(shop.total([5]), 5)

    def test_positive(self):
        check_positive(shop.total(self.prices))

    # def test_negative(self):
    #     self.assertEqual(shop.total([-1]), -1)
