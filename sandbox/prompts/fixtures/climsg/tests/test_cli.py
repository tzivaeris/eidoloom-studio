import json
import os
import subprocess
import sys
import tempfile
import unittest

CLI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cli.py")


def run(cwd, *args):
    return subprocess.run([sys.executable, CLI, *args], cwd=cwd, capture_output=True, text=True)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_add_and_list_json(self):
        self.assertEqual(run(self.tmp.name, "add", "milk").returncode, 0)
        result = run(self.tmp.name, "list", "--json")
        self.assertEqual(result.returncode, 0)
        items = json.loads(result.stdout)
        self.assertEqual(set(items[0]), {"id", "name", "created"})
        self.assertEqual(items[0]["name"], "milk")

    def test_duplicate_fails(self):
        run(self.tmp.name, "add", "milk")
        self.assertEqual(run(self.tmp.name, "add", "milk").returncode, 1)

    def test_empty_name_fails(self):
        self.assertEqual(run(self.tmp.name, "add", "  ").returncode, 2)

    def test_remove(self):
        run(self.tmp.name, "add", "milk")
        self.assertEqual(run(self.tmp.name, "remove", "1").returncode, 0)
        self.assertEqual(json.loads(run(self.tmp.name, "list", "--json").stdout), [])
        self.assertEqual(run(self.tmp.name, "remove", "1").returncode, 1)

    def test_list_plain_ok(self):
        self.assertEqual(run(self.tmp.name, "list").returncode, 0)


if __name__ == "__main__":
    unittest.main()
