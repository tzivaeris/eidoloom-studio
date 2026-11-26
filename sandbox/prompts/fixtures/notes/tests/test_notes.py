import os
import tempfile
import unittest

import notes


class NotesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old = os.getcwd()
        os.chdir(self.tmp.name)
        self.addCleanup(os.chdir, self.old)

    def test_add_and_list(self):
        notes.add("buy milk")
        notes.add("call Ann")
        self.assertEqual([n["id"] for n in notes.load()], [1, 2])

    def test_search(self):
        notes.add("Buy milk")
        notes.add("call Ann")
        self.assertEqual([n["text"] for n in notes.search("milk")], ["Buy milk"])

    def test_remove(self):
        notes.add("a")
        self.assertTrue(notes.remove(1))
        self.assertFalse(notes.remove(1))
        self.assertEqual(notes.load(), [])

    def test_main_codes(self):
        self.assertEqual(notes.main(["add", "x"]), 0)
        self.assertEqual(notes.main(["remove", "9"]), 1)
        self.assertEqual(notes.main([]), 2)


if __name__ == "__main__":
    unittest.main()
