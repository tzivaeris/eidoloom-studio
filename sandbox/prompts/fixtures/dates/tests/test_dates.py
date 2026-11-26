import unittest
from datetime import date

from dates import days_until_birthday


class BirthdayTests(unittest.TestCase):
    def test_later_this_year(self):
        self.assertEqual(days_until_birthday(date(1990, 6, 10), date(2025, 6, 1)), 9)

    def test_already_passed(self):
        self.assertEqual(days_until_birthday(date(1990, 1, 10), date(2025, 1, 20)), 355)

    def test_next_day_of_month(self):
        self.assertEqual(days_until_birthday(date(2000, 3, 2), date(2025, 3, 1)), 1)


if __name__ == "__main__":
    unittest.main()
