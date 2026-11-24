"""Hidden black-box grader for Tallybook. Runs inside the grading container, never visible to the agent.
Every check here is stated in PROMPT.md. The project is only driven through its CLI and HTTP API."""
import concurrent.futures
import csv
import http.client
import io
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import unittest

PROJECT = os.environ.get("PROJECT", os.getcwd())
JAN = ("2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z")


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "tally.db")
        self.start_server()
        self.addCleanup(self.stop_server)

    def start_server(self):
        self.port = free_port()
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "tallybook", "serve", "--port", str(self.port), "--db", self.db],
            cwd=PROJECT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                self.fail(f"server exited with code {self.proc.returncode}")
            try:
                if self.call("GET", "/api/health")[0] == 200:
                    return
            except OSError:
                pass
            time.sleep(0.1)
        self.fail("server did not answer /api/health within 15s")

    def stop_server(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()

    def raw(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(method, path, body, headers or {})
            res = conn.getresponse()
            return res.status, res.getheader("Content-Type") or "", res.read()
        finally:
            conn.close()

    def call(self, method, path, body=None):
        payload = None if body is None else json.dumps(body)
        headers = {} if body is None else {"Content-Type": "application/json"}
        status, _, data = self.raw(method, path, payload, headers)
        try:
            return status, json.loads(data) if data else None
        except ValueError:
            return status, data

    def client(self, name="Acme", rate=6000):
        status, data = self.call("POST", "/api/clients", {"name": name, "rate_cents": rate})
        self.assertEqual(status, 201, data)
        return data

    def entry(self, client, start="2026-01-05T09:00:00Z", end="2026-01-05T10:00:00Z", **extra):
        status, data = self.call("POST", "/api/entries", {"client_id": client["id"], "start": start, "end": end, **extra})
        self.assertEqual(status, 201, data)
        return data

    def invoice(self, client, span=JAN):
        status, data = self.call("POST", "/api/invoices", {"client_id": client["id"], "from": span[0], "to": span[1]})
        self.assertEqual(status, 201, data)
        return data

    def assertError(self, result, status):
        self.assertEqual(result[0], status, result[1])
        self.assertIsInstance(result[1], dict, result[1])
        self.assertIsInstance(result[1].get("error"), str, result[1])

    def summary(self, *span):
        return subprocess.run([sys.executable, "-m", "tallybook", "summary", "--db", self.db, "--from", span[0], "--to", span[1]],
                              cwd=PROJECT, capture_output=True, text=True, timeout=30)


class Clients(Base):
    def test_health(self):
        self.assertEqual(self.call("GET", "/api/health"), (200, {"status": "ok"}))

    def test_create_and_list_sorted_by_id(self):
        first, second = self.client("Zeta", 100), self.client("Alpha", 200)
        self.assertEqual(first["name"], "Zeta")
        self.assertEqual(first["rate_cents"], 100)
        self.assertEqual(self.call("GET", "/api/clients")[1], [first, second])

    def test_name_trimmed_and_unicode(self):
        self.assertEqual(self.client("  Αθηνά & Co  ")["name"], "Αθηνά & Co")
        self.assertEqual(self.call("GET", "/api/clients")[1][0]["name"], "Αθηνά & Co")

    def test_duplicate_ignores_case(self):
        self.client("Acme")
        self.assertError(self.call("POST", "/api/clients", {"name": " ACME ", "rate_cents": 1}), 409)

    def test_invalid_clients(self):
        for body in ({"rate_cents": 1}, {"name": "   ", "rate_cents": 1}, {"name": 5, "rate_cents": 1}, {"name": "x"},
                     {"name": "x", "rate_cents": -1}, {"name": "x", "rate_cents": 1.5}, {"name": "x", "rate_cents": "1"},
                     {"name": "x", "rate_cents": True}):
            with self.subTest(body=body):
                self.assertError(self.call("POST", "/api/clients", body), 400)

    def test_patch_rate(self):
        client = self.client()
        status, data = self.call("PATCH", f"/api/clients/{client['id']}", {"rate_cents": 7})
        self.assertEqual((status, data["rate_cents"]), (200, 7))
        self.assertError(self.call("PATCH", f"/api/clients/{client['id']}", {"rate_cents": True}), 400)
        self.assertError(self.call("PATCH", "/api/clients/999", {"rate_cents": 7}), 404)


class Errors(Base):
    def test_malformed_json(self):
        status, _, data = self.raw("POST", "/api/clients", "{not json", {"Content-Type": "application/json"})
        self.assertEqual(status, 400)
        self.assertIsInstance(json.loads(data).get("error"), str)

    def test_non_object_json(self):
        self.assertError(self.call("POST", "/api/clients", [1, 2]), 400)

    def test_unknown_path(self):
        self.assertError(self.call("GET", "/api/nope"), 404)

    def test_unsupported_method(self):
        self.assertIn(self.call("DELETE", "/api/clients")[0], (404, 405))


class Entries(Base):
    def test_entry_shape_and_defaults(self):
        client = self.client()
        data = self.entry(client)
        self.assertEqual({k: data[k] for k in ("client_id", "note", "minutes", "invoice_id")},
                         {"client_id": client["id"], "note": "", "minutes": 60, "invoice_id": None})
        self.assertEqual(self.entry(client, "2026-01-06T09:00:00Z", "2026-01-06T09:01:00Z", note="hi")["note"], "hi")

    def test_minutes_round_up(self):
        client = self.client()
        for day, end, minutes in ((10, "00:00:01", 1), (11, "00:01:00", 1), (12, "00:01:01", 2), (13, "01:30:00", 90)):
            with self.subTest(end=end):
                self.assertEqual(self.entry(client, f"2026-01-{day}T00:00:00Z", f"2026-01-{day}T{end}Z")["minutes"], minutes)

    def test_invalid_timestamps(self):
        client = self.client()
        good = "2026-01-01T10:00:00Z"
        for bad in ("2026-02-30T10:00:00Z", "2026-01-01T10:00:00+02:00", "2026-01-01T10:00:00", "2026-01-01 10:00:00Z",
                    "2026-1-1T10:00:00Z", "2026-01-01T25:00:00Z", "nope", 5, None):
            with self.subTest(bad=bad):
                self.assertError(self.call("POST", "/api/entries", {"client_id": client["id"], "start": bad, "end": "2026-03-01T10:00:00Z"}), 400)
                self.assertError(self.call("POST", "/api/entries", {"client_id": client["id"], "start": good, "end": bad}), 400)

    def test_end_must_follow_start(self):
        client = self.client()
        same = "2026-01-01T10:00:00Z"
        self.assertError(self.call("POST", "/api/entries", {"client_id": client["id"], "start": same, "end": same}), 400)
        self.assertError(self.call("POST", "/api/entries", {"client_id": client["id"], "start": same, "end": "2026-01-01T09:00:00Z"}), 400)

    def test_unknown_client(self):
        self.assertError(self.call("POST", "/api/entries", {"client_id": 999, "start": "2026-01-01T10:00:00Z", "end": "2026-01-01T11:00:00Z"}), 404)

    def test_overlaps(self):
        client = self.client()
        self.entry(client, "2026-01-05T10:00:00Z", "2026-01-05T11:00:00Z")
        for start, end in (("2026-01-05T10:30:00Z", "2026-01-05T10:40:00Z"), ("2026-01-05T09:00:00Z", "2026-01-05T12:00:00Z"),
                           ("2026-01-05T09:30:00Z", "2026-01-05T10:00:01Z"), ("2026-01-05T10:59:59Z", "2026-01-05T11:30:00Z")):
            with self.subTest(start=start):
                self.assertError(self.call("POST", "/api/entries", {"client_id": client["id"], "start": start, "end": end}), 409)

    def test_touching_and_other_clients_allowed(self):
        client = self.client()
        self.entry(client, "2026-01-05T10:00:00Z", "2026-01-05T11:00:00Z")
        self.entry(client, "2026-01-05T11:00:00Z", "2026-01-05T12:00:00Z")
        self.entry(client, "2026-01-05T09:00:00Z", "2026-01-05T10:00:00Z")
        self.entry(self.client("Other"), "2026-01-05T10:00:00Z", "2026-01-05T11:00:00Z")

    def test_list_filters_and_order(self):
        one, two = self.client("One"), self.client("Two")
        late = self.entry(one, "2026-01-20T10:00:00Z", "2026-01-20T11:00:00Z")
        early = self.entry(one, "2026-01-10T10:00:00Z", "2026-01-10T11:00:00Z")
        other = self.entry(two, "2026-01-15T10:00:00Z", "2026-01-15T11:00:00Z")
        ids = lambda path: [e["id"] for e in self.call("GET", path)[1]]
        self.assertEqual(ids("/api/entries"), [early["id"], other["id"], late["id"]])
        self.assertEqual(ids(f"/api/entries?client_id={one['id']}"), [early["id"], late["id"]])
        self.assertEqual(ids("/api/entries?from=2026-01-15T10:00:00Z"), [other["id"], late["id"]])
        self.assertEqual(ids("/api/entries?to=2026-01-15T10:00:00Z"), [early["id"]])

    def test_list_invalid_params(self):
        self.assertError(self.call("GET", "/api/entries?from=nope"), 400)
        self.assertError(self.call("GET", "/api/entries?client_id=abc"), 400)

    def test_delete(self):
        client = self.client()
        data = self.entry(client)
        status, _, body = self.raw("DELETE", f"/api/entries/{data['id']}")
        self.assertEqual((status, body), (204, b""))
        self.assertError(self.call("DELETE", f"/api/entries/{data['id']}"), 404)
        self.assertEqual(self.call("GET", "/api/entries")[1], [])

    def test_delete_invoiced_refused(self):
        client = self.client()
        data = self.entry(client)
        self.invoice(client)
        self.assertError(self.call("DELETE", f"/api/entries/{data['id']}"), 409)

    def test_concurrent_conflicts_only_one_wins(self):
        client = self.client()
        body = {"client_id": client["id"], "start": "2026-01-05T10:00:00Z", "end": "2026-01-05T11:00:00Z"}
        with concurrent.futures.ThreadPoolExecutor(12) as pool:
            codes = sorted(r[0] for r in pool.map(lambda _: self.call("POST", "/api/entries", body), range(12)))
        self.assertEqual(codes, [201] + [409] * 11)


class Invoices(Base):
    def test_lines_total_and_marks_entries(self):
        client = self.client(rate=6000)
        late = self.entry(client, "2026-01-20T10:00:00Z", "2026-01-20T10:30:00Z")
        early = self.entry(client, "2026-01-10T10:00:00Z", "2026-01-10T11:00:00Z")
        data = self.invoice(client)
        self.assertEqual(data["lines"], [{"entry_id": early["id"], "minutes": 60, "amount_cents": 6000},
                                         {"entry_id": late["id"], "minutes": 30, "amount_cents": 3000}])
        self.assertEqual(data["total_cents"], 9000)
        self.assertEqual((data["client_id"], data["from"], data["to"]), (client["id"], *JAN))
        self.assertTrue(all(e["invoice_id"] == data["id"] for e in self.call("GET", "/api/entries")[1]))
        self.assertEqual(self.call("GET", f"/api/invoices/{data['id']}"), (200, data))

    def test_half_up_rounding_per_line(self):
        for rate, expected in ((30, 1), (90, 2), (150, 3), (29, 0), (31, 1)):
            with self.subTest(rate=rate):
                client = self.client(f"R{rate}", rate)
                self.entry(client, "2026-01-05T10:00:00Z", "2026-01-05T10:01:00Z")
                self.assertEqual(self.invoice(client)["total_cents"], expected)

    def test_total_sums_rounded_lines(self):
        client = self.client(rate=30)
        for hour in (10, 11, 12):
            self.entry(client, f"2026-01-05T{hour}:00:00Z", f"2026-01-05T{hour}:01:00Z")
        self.assertEqual(self.invoice(client)["total_cents"], 3)

    def test_large_amount_is_exact(self):
        client = self.client(rate=123456789012345)
        self.entry(client, "2026-01-05T10:00:00Z", "2026-01-05T10:07:00Z")
        self.assertEqual(self.invoice(client)["total_cents"], (2 * 7 * 123456789012345 + 60) // 120)

    def test_range_half_open_and_other_client_excluded(self):
        client, other = self.client("A"), self.client("B")
        inside = self.entry(client, "2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z")
        self.entry(client, "2026-02-01T00:00:00Z", "2026-02-01T01:00:00Z")
        self.entry(other, "2026-01-02T00:00:00Z", "2026-01-02T01:00:00Z")
        self.assertEqual([l["entry_id"] for l in self.invoice(client)["lines"]], [inside["id"]])

    def test_not_billed_twice(self):
        client = self.client()
        self.entry(client)
        self.invoice(client)
        self.assertError(self.call("POST", "/api/invoices", {"client_id": client["id"], "from": JAN[0], "to": JAN[1]}), 409)

    def test_invalid_invoices(self):
        client = self.client()
        self.assertError(self.call("POST", "/api/invoices", {"client_id": 999, "from": JAN[0], "to": JAN[1]}), 404)
        self.assertError(self.call("POST", "/api/invoices", {"client_id": client["id"], "from": JAN[1], "to": JAN[0]}), 400)
        self.assertError(self.call("POST", "/api/invoices", {"client_id": client["id"], "from": JAN[0], "to": JAN[0]}), 400)
        self.assertError(self.call("POST", "/api/invoices", {"client_id": client["id"], "from": "nope", "to": JAN[1]}), 400)
        self.assertError(self.call("GET", "/api/invoices/999"), 404)
        self.assertError(self.call("GET", "/api/invoices/999.csv"), 404)

    def test_numbering_per_year(self):
        client = self.client()
        numbers = []
        for year in (2026, 2026, 2025, 2026):
            day = 5 + len(numbers)
            self.entry(client, f"{year}-03-{day:02}T10:00:00Z", f"{year}-03-{day:02}T11:00:00Z")
            numbers.append(self.invoice(client, (f"{year}-03-01T00:00:00Z", f"{year}-04-01T00:00:00Z"))["number"])
        self.assertEqual(numbers, ["INV-2026-0001", "INV-2026-0002", "INV-2025-0001", "INV-2026-0003"])

    def test_rate_change_does_not_alter_invoice(self):
        client = self.client(rate=6000)
        self.entry(client)
        data = self.invoice(client)
        self.call("PATCH", f"/api/clients/{client['id']}", {"rate_cents": 1})
        self.assertEqual(self.call("GET", f"/api/invoices/{data['id']}")[1], data)

    def test_csv(self):
        client = self.client(rate=6000)
        one = self.entry(client, "2026-01-05T10:00:00Z", "2026-01-05T11:00:00Z")
        two = self.entry(client, "2026-01-06T10:00:00Z", "2026-01-06T10:30:00Z")
        status, kind, body = self.raw("GET", f"/api/invoices/{self.invoice(client)['id']}.csv")
        self.assertEqual(status, 200)
        self.assertIn("text/csv", kind)
        self.assertEqual(list(csv.reader(io.StringIO(body.decode()))), [
            ["entry_id", "start", "end", "minutes", "amount_cents"],
            [str(one["id"]), one["start"], one["end"], "60", "6000"],
            [str(two["id"]), two["start"], two["end"], "30", "3000"],
            ["TOTAL", "", "", "90", "9000"]])

    def test_concurrent_invoices_bill_once(self):
        client = self.client()
        self.entry(client)
        body = {"client_id": client["id"], "from": JAN[0], "to": JAN[1]}
        with concurrent.futures.ThreadPoolExecutor(8) as pool:
            codes = sorted(r[0] for r in pool.map(lambda _: self.call("POST", "/api/invoices", body), range(8)))
        self.assertEqual(codes, [201] + [409] * 7)


class Cli(Base):
    def test_persists_across_restart(self):
        client = self.client()
        data = self.entry(client)
        invoice = self.invoice(client)
        self.stop_server()
        self.start_server()
        self.assertEqual(self.call("GET", "/api/clients")[1], [client])
        self.assertEqual(self.call("GET", "/api/entries")[1][0]["id"], data["id"])
        self.assertEqual(self.call("GET", f"/api/invoices/{invoice['id']}")[1], invoice)

    def test_summary(self):
        zed, abe = self.client("Zed", 6000), self.client("Abe", 150)
        self.client("Idle", 100)
        billed = self.entry(zed, "2026-01-05T10:00:00Z", "2026-01-05T11:00:00Z")
        self.invoice(zed, ("2026-01-05T00:00:00Z", "2026-01-06T00:00:00Z"))
        self.entry(zed, "2026-01-07T10:00:00Z", "2026-01-07T10:30:00Z")
        self.entry(zed, "2026-03-01T10:00:00Z", "2026-03-01T10:30:00Z")
        self.entry(abe, "2026-01-07T10:00:00Z", "2026-01-07T10:01:00Z")
        self.call("PATCH", f"/api/clients/{zed['id']}", {"rate_cents": 12000})
        result = self.summary(*JAN)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [{"client": "Abe", "minutes": 1, "uninvoiced_cents": 3},
                                                     {"client": "Zed", "minutes": 90, "uninvoiced_cents": 6000}])
        self.assertIsNotNone(billed)

    def test_summary_invalid(self):
        self.client()
        for span in (("nope", JAN[1]), (JAN[0], "2026-02-30T00:00:00Z"), (JAN[1], JAN[0])):
            with self.subTest(span=span):
                result = self.summary(*span)
                self.assertEqual(result.returncode, 2)
                self.assertTrue(result.stderr.strip())


class Ui(Base):
    def page(self):
        status, kind, body = self.raw("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", kind)
        return body.decode()

    def test_forms_present(self):
        page = self.page()
        for form in ("client-form", "entry-form", "invoice-form"):
            with self.subTest(form=form):
                self.assertRegex(page, rf"<form\b[^>]*\bid=[\"']{form}[\"']")

    def test_self_contained_and_safe(self):
        page = self.page()
        self.assertIsNone(re.search(r"""(?:src|href)\s*=\s*["']?(?:https?:)?//""", page))
        self.assertNotIn("innerHTML", page)
        self.assertIn("fetch(", page)

    def test_does_not_serve_files(self):
        for path in ("/tallybook/__main__.py", "/README.md", "/../README.md", "/static/../README.md",
                     "/..%2fREADME.md", "/%2e%2e/%2e%2e/etc/passwd", "/tally.db"):
            with self.subTest(path=path):
                self.assertEqual(self.raw("GET", path)[0], 404)


if __name__ == "__main__":
    unittest.main()
