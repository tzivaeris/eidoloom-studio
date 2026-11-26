import unittest

import box


class BoxTests(unittest.TestCase):
    def test_add(self):
        b = box.new_box()
        self.assertIn("'a'", box.add_item(b, "a"))
        self.assertEqual(b, ["a"])

    def test_full(self):
        b = box.new_box()
        for i in range(box.MAX_ITEMS):
            box.add_item(b, i)
        with self.assertRaises(ValueError):
            box.add_item(b, "extra")

    def test_remove(self):
        b = ["a", "b"]
        self.assertEqual(box.remove_item(b, "a"), ["b"])


if __name__ == "__main__":
    unittest.main()
