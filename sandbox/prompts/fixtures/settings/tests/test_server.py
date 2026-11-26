import os
import tempfile
import unittest

import db


class DbTest(unittest.TestCase):
    def test_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(db.all_notes(os.path.join(d, "t.db")), [])


class PortTest(unittest.TestCase):
    def test_port_from_env(self):
        os.environ["NOTES_PORT"] = "9999"
        import importlib
        import server
        importlib.reload(server)
        self.assertEqual(server.PORT, 9999)
        del os.environ["NOTES_PORT"]
