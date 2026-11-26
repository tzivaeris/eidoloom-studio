import unittest

from invoice import invoice_total
from receipt import receipt


class ReceiptTest(unittest.TestCase):
    def test_receipt_total(self):
        lines = [(1999, 1), (500, 2)]
        self.assertEqual(invoice_total(lines)["gross"], 3719)
        self.assertEqual(receipt(lines).splitlines()[-1], "Total: 37.19 EUR")

    def test_receipt_lines(self):
        self.assertEqual(receipt([(500, 2)]).splitlines()[0], "2 x 5.00 EUR")


if __name__ == "__main__":
    unittest.main()
