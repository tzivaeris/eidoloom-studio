import unittest

from cart import Cart, apply_discount
from report import receipt


class CartTests(unittest.TestCase):
    def cart(self):
        cart = Cart(discount_pct=10)
        cart.add("Book", 10000)
        return cart

    def test_apply_discount(self):
        self.assertEqual(apply_discount(10000, 10), 9000)

    def test_total(self):
        self.assertEqual(self.cart().total(), 9000)

    def test_receipt(self):
        self.assertEqual(receipt(self.cart()).splitlines()[-1], "Paid in full: 90.00")


if __name__ == "__main__":
    unittest.main()
