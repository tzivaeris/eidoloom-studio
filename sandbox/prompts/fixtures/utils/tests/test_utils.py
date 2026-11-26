import hashlib
import os
import tempfile
import unittest

from utils import (clamp, file_hash, percent, read_lines, round_half_up, slugify, title_case, truncate,
                   write_atomic)


class TextTests(unittest.TestCase):
    def test_slugify(self):
        self.assertEqual(slugify("Hello, World!"), "hello-world")

    def test_truncate(self):
        self.assertEqual(truncate("abcdefghij", 8), "abcde...")
        self.assertEqual(truncate("abc", 8), "abc")

    def test_title_case(self):
        self.assertEqual(title_case("hELLO wORLD"), "Hello World")


class NumberTests(unittest.TestCase):
    def test_clamp(self):
        self.assertEqual([clamp(5, 0, 3), clamp(-1, 0, 3), clamp(2, 0, 3)], [3, 0, 2])

    def test_percent(self):
        self.assertEqual(percent(1, 3), 33.3)
        self.assertEqual(percent(1, 0), 0.0)

    def test_round_half_up(self):
        self.assertEqual([round_half_up(2.5), round_half_up(2.4), round_half_up(-2.5)], [3, 2, -3])


class FileTests(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a.txt")
            write_atomic(p, "one\ntwo\n")
            self.assertEqual(read_lines(p), ["one", "two"])
            self.assertEqual(file_hash(p), hashlib.sha256(b"one\ntwo\n").hexdigest())


if __name__ == "__main__":
    unittest.main()
