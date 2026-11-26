import unittest

from paginate import page_count, paginate


class PaginateTests(unittest.TestCase):
    def test_exact_multiple(self):
        self.assertEqual(paginate([1, 2, 3, 4], 2), [[1, 2], [3, 4]])

    def test_single_full_page(self):
        self.assertEqual(paginate([1, 2, 3], 3), [[1, 2, 3]])

    def test_empty(self):
        self.assertEqual(paginate([], 5), [])

    def test_bad_size(self):
        with self.assertRaises(ValueError):
            paginate([1], 0)

    def test_page_count_exact(self):
        self.assertEqual(page_count(list(range(10)), 5), 2)


if __name__ == "__main__":
    unittest.main()
