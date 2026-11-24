import unittest

from tallybook.__main__ import Fail, amount, connect, create_client, create_entry, create_invoice, is_int, minutes_of, parse_ts

A = "2026-01-01T10:00:00Z"
B = "2026-01-01T11:00:00Z"


class Rules(unittest.TestCase):
    def setUp(self):
        self.db = connect(":memory:")
        self.client = create_client(self.db, {"name": "Acme", "rate_cents": 6000})[1]

    def entry(self, start=A, end=B):
        return create_entry(self.db, {"client_id": self.client["id"], "start": start, "end": end})

    def invoice(self):
        return create_invoice(self.db, {"client_id": self.client["id"], "from": A, "to": "2026-02-01T00:00:00Z"})

    def assertFails(self, status, fn, *args):
        with self.assertRaises(Fail) as caught:
            fn(*args)
        self.assertEqual(caught.exception.status, status)

    def test_real_dates_only(self):
        self.assertFails(400, parse_ts, "2026-02-30T10:00:00Z")

    def test_offset_rejected(self):
        self.assertFails(400, parse_ts, "2026-01-01T10:00:00+02:00")

    def test_minutes_round_up(self):
        self.assertEqual(minutes_of(A, "2026-01-01T10:01:01Z"), 2)

    def test_exact_minute(self):
        self.assertEqual(minutes_of(A, "2026-01-01T10:01:00Z"), 1)

    def test_half_rounds_up(self):
        self.assertEqual(amount(1, 150), 3)

    def test_bool_is_not_int(self):
        self.assertFalse(is_int(True))

    def test_duplicate_name(self):
        self.assertFails(409, create_client, self.db, {"name": "ACME", "rate_cents": 1})

    def test_overlap_refused(self):
        self.entry()
        self.assertFails(409, self.entry, "2026-01-01T10:30:00Z", "2026-01-01T12:00:00Z")

    def test_touching_allowed(self):
        self.entry()
        self.assertEqual(self.entry(B, "2026-01-01T12:00:00Z")[0], 201)

    def test_invoice_total(self):
        self.entry()
        self.assertEqual(self.invoice()[1]["total_cents"], 6000)

    def test_nothing_to_bill(self):
        self.assertFails(409, self.invoice)


if __name__ == "__main__":
    unittest.main()
