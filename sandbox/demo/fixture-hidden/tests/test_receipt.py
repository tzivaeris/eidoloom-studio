import unittest

from invoice import invoice_total
from receipt import receipt


class ReceiptTest(unittest.TestCase):
    def test_receipt_total(self):
        lines = [(1519, 1), (500, 1)]
        self.assertEqual(invoice_total(lines)["gross"], 2504)
        self.assertEqual(receipt(lines).splitlines()[-1], "Total: 25.04 EUR")

    def test_receipt_lines(self):
        self.assertEqual(receipt([(1250, 2)]).splitlines()[0], "2 x 12.50 EUR")


if __name__ == "__main__":
    unittest.main()
