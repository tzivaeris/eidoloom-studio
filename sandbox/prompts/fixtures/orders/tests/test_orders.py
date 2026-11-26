import unittest

import orders


class HandleTest(unittest.TestCase):
    def test_simple(self):
        r = orders.handle({"customer": "ann", "items": [{"name": "apple", "qty": 2}]})
        self.assertEqual(r, {"customer": "ann", "total": 100, "lines": 1})

    def test_discount(self):
        r = orders.handle({"customer": "bo", "items": [{"name": "plum", "qty": 20}]})
        self.assertEqual(r["total"], 1620)

    def test_two_lines(self):
        r = orders.handle({"customer": "cy", "items": [{"name": "apple", "qty": 1}, {"name": "pear", "qty": 1}]})
        self.assertEqual((r["total"], r["lines"]), (120, 2))

    def test_no_customer(self):
        with self.assertRaises(ValueError):
            orders.handle({"items": [{"name": "apple", "qty": 1}]})
