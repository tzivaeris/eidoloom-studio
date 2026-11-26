import os
import unittest

from contacts import load_contacts

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(HERE, "contacts.csv")


class ContactsTests(unittest.TestCase):
    def test_count(self):
        self.assertEqual(len(load_contacts(PATH)), 12)

    def test_first_row(self):
        first = load_contacts(PATH)[0]
        self.assertEqual(first["name"], "Alice Johnson")
        self.assertEqual(first["city"], "London")

    def test_empty_field(self):
        self.assertEqual(load_contacts(PATH)[2]["note"], "")


if __name__ == "__main__":
    unittest.main()
