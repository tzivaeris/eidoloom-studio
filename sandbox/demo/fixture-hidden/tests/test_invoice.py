import unittest

from invoice import format_eur, invoice_total, line_total, vat


class InvoiceTest(unittest.TestCase):
    def test_line_total(self):
        self.assertEqual(line_total(1250, 3), 3750)

    def test_quantity_must_be_positive(self):
        with self.assertRaises(ValueError):
            line_total(1250, 0)

    def test_vat_exact(self):
        self.assertEqual(vat(10000), 2400)

    def test_vat_rounds_to_nearest_cent(self):
        self.assertEqual(vat(1999), 480)   # 479.76 cents

    def test_invoice_total(self):
        self.assertEqual(invoice_total([(1999, 1), (500, 2)]), {"net": 2999, "vat": 720, "gross": 3719})

    def test_format_eur(self):
        self.assertEqual(format_eur(3719), "37.19 EUR")


if __name__ == "__main__":
    unittest.main()
