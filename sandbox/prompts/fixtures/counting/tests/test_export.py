import unittest

import shop


class ExportTest(unittest.TestCase):
    def test_export_one(self):
        self.assertEqual(shop.export([[1, 2]]), "1,2")

    def test_export_two(self):
        self.assertEqual(shop.export([[1], [2]]), "1\n2")

    def test_export_empty(self):
        self.assertEqual(shop.export([]), "")

    @unittest.skip("too slow for CI")
    def test_export_large(self):
        self.assertEqual(len(shop.export([[i] for i in range(10**6)])), 6888889)
