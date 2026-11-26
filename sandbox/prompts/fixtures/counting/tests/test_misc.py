import unittest

import shop


class MiscTest(unittest.TestCase):
    def test_types(self):
        self.assertIsInstance(shop.total([1.5]), float)

    def test_strings(self):
        self.assertEqual(shop.export([["a"]]), "a")

    def prepare_data(self):
        return [[1]]

    def test_prepared(self):
        self.assertEqual(shop.export(self.prepare_data()), "1")

