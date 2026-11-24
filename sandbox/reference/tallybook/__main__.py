"""Reference Tallybook, used only by `arena.py selftest` to prove the grader can pass."""
import argparse
import datetime
import json
import os
import re
import sqlite3
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TS_RE = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", re.A)
PAGE = open(os.path.join(os.path.dirname(__file__), "page.html"), encoding="utf-8").read()
LOCK = threading.Lock()


class Fail(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


def parse_ts(value):
    if not isinstance(value, str) or not TS_RE.fullmatch(value):
        raise Fail(400, f"invalid timestamp: {value!r}")
    try:
        return datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise Fail(400, f"invalid timestamp: {value!r}") from None


def is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def get_rate(body):
    rate = body.get("rate_cents")
    if not is_int(rate) or rate < 0:
        raise Fail(400, "rate_cents must be an integer >= 0")
    return rate


def minutes_of(start, end):
    return (int((parse_ts(end) - parse_ts(start)).total_seconds()) + 59) // 60


def amount(minutes, rate):
    return (2 * minutes * rate + 60) // 120


def connect(path):
    db = sqlite3.connect(path, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS clients(id INTEGER PRIMARY KEY, name TEXT, rate_cents TEXT);
        CREATE TABLE IF NOT EXISTS invoices(id INTEGER PRIMARY KEY, year TEXT, doc TEXT);
        CREATE TABLE IF NOT EXISTS entries(id INTEGER PRIMARY KEY, client_id INTEGER, start TEXT, end TEXT, note TEXT,
                                           invoice_id INTEGER);""")
    db.commit()
    return db


def client_json(row):
    return {"id": row["id"], "name": row["name"], "rate_cents": int(row["rate_cents"])}


def entry_json(row):
    return {"id": row["id"], "client_id": row["client_id"], "start": row["start"], "end": row["end"], "note": row["note"],
            "minutes": minutes_of(row["start"], row["end"]), "invoice_id": row["invoice_id"]}


def find_client(db, client_id):
    row = db.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone() if is_int(client_id) else None
    if row is None:
        raise Fail(404, "unknown client")
    return row


def create_client(db, body):
    name, rate = body.get("name"), get_rate(body)
    if not isinstance(name, str) or not name.strip():
        raise Fail(400, "name must be a non-blank string")
    name = name.strip()
    if any(row["name"].casefold() == name.casefold() for row in db.execute("SELECT name FROM clients")):
        raise Fail(409, "client name already exists")
    cur = db.execute("INSERT INTO clients(name, rate_cents) VALUES(?, ?)", (name, str(rate)))
    return 201, client_json(find_client(db, cur.lastrowid))


def create_entry(db, body):
    start, end, note = body.get("start"), body.get("end"), body.get("note", "")
    if parse_ts(end) <= parse_ts(start):
        raise Fail(400, "end must be after start")
    if not isinstance(note, str):
        raise Fail(400, "note must be a string")
    if not is_int(body.get("client_id")):
        raise Fail(400, "client_id must be an integer")
    client = find_client(db, body["client_id"])
    if db.execute("SELECT 1 FROM entries WHERE client_id=? AND start<? AND end>?", (client["id"], end, start)).fetchone():
        raise Fail(409, "entry overlaps another entry")
    cur = db.execute("INSERT INTO entries(client_id, start, end, note) VALUES(?, ?, ?, ?)", (client["id"], start, end, note))
    return 201, entry_json(db.execute("SELECT * FROM entries WHERE id=?", (cur.lastrowid,)).fetchone())


def list_entries(db, query):
    sql, args = "SELECT * FROM entries WHERE 1=1", []
    if "client_id" in query:
        if not re.fullmatch(r"\d+", query["client_id"][0], re.A):
            raise Fail(400, "client_id must be an integer")
        sql += " AND client_id=?"
        args.append(int(query["client_id"][0]))
    for key, clause in (("from", " AND start>=?"), ("to", " AND start<?")):
        if key in query:
            parse_ts(query[key][0])
            sql += clause
            args.append(query[key][0])
    return 200, [entry_json(row) for row in db.execute(sql + " ORDER BY start, id", args)]


def delete_entry(db, entry_id):
    row = db.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
    if row is None:
        raise Fail(404, "unknown entry")
    if row["invoice_id"] is not None:
        raise Fail(409, "entry is invoiced")
    db.execute("DELETE FROM entries WHERE id=?", (entry_id,))
    return 204, None


def create_invoice(db, body):
    begin, finish = body.get("from"), body.get("to")
    if parse_ts(finish) <= parse_ts(begin):
        raise Fail(400, "from must be before to")
    if not is_int(body.get("client_id")):
        raise Fail(400, "client_id must be an integer")
    client = find_client(db, body["client_id"])
    rate = int(client["rate_cents"])
    rows = db.execute("SELECT * FROM entries WHERE client_id=? AND invoice_id IS NULL AND start>=? AND start<? ORDER BY start, id",
                      (client["id"], begin, finish)).fetchall()
    if not rows:
        raise Fail(409, "nothing to bill")
    lines = []
    for row in rows:
        minutes = minutes_of(row["start"], row["end"])
        lines.append({"entry_id": row["id"], "minutes": minutes, "amount_cents": amount(minutes, rate)})
    year = begin[:4]
    sequence = db.execute("SELECT COUNT(*) FROM invoices WHERE year=?", (year,)).fetchone()[0] + 1
    cur = db.execute("INSERT INTO invoices(year, doc) VALUES(?, '')", (year,))
    doc = {"id": cur.lastrowid, "number": f"INV-{year}-{sequence:04}", "client_id": client["id"], "from": begin, "to": finish,
           "lines": lines, "total_cents": sum(line["amount_cents"] for line in lines)}
    db.execute("UPDATE invoices SET doc=? WHERE id=?", (json.dumps(doc), doc["id"]))
    db.executemany("UPDATE entries SET invoice_id=? WHERE id=?", [(doc["id"], row["id"]) for row in rows])
    return 201, doc


def find_invoice(db, invoice_id):
    row = db.execute("SELECT doc FROM invoices WHERE id=?", (invoice_id,)).fetchone()
    if row is None:
        raise Fail(404, "unknown invoice")
    return json.loads(row["doc"])


def invoice_csv(db, invoice_id):
    doc = find_invoice(db, invoice_id)
    out = ["entry_id,start,end,minutes,amount_cents"]
    for line in doc["lines"]:
        row = db.execute("SELECT start, end FROM entries WHERE id=?", (line["entry_id"],)).fetchone()
        out.append(f"{line['entry_id']},{row['start']},{row['end']},{line['minutes']},{line['amount_cents']}")
    out.append(f"TOTAL,,,{sum(line['minutes'] for line in doc['lines'])},{doc['total_cents']}")
    return "\r\n".join(out) + "\r\n"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    db = None

    def log_message(self, *args):
        pass

    def send(self, status, kind="application/json", data=b""):
        self.send_response(status)
        if status != 204:
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        try:
            body = json.loads(raw)
        except ValueError:
            raise Fail(400, "malformed JSON") from None
        if not isinstance(body, dict):
            raise Fail(400, "body must be a JSON object")
        return body

    def route(self):
        url = urllib.parse.urlsplit(self.path)
        method, path, db = self.command, url.path, self.db
        if (method, path) == ("GET", "/"):
            return self.send(200, "text/html; charset=utf-8", PAGE.encode())
        if (method, path) == ("GET", "/api/health"):
            return self.reply(200, {"status": "ok"})
        if (method, path) == ("GET", "/api/clients"):
            return self.reply(200, [client_json(r) for r in db.execute("SELECT * FROM clients ORDER BY id")])
        if (method, path) == ("POST", "/api/clients"):
            return self.reply(*create_client(db, self.body()))
        if (method, path) == ("POST", "/api/entries"):
            return self.reply(*create_entry(db, self.body()))
        if (method, path) == ("GET", "/api/entries"):
            return self.reply(*list_entries(db, urllib.parse.parse_qs(url.query)))
        if (method, path) == ("POST", "/api/invoices"):
            return self.reply(*create_invoice(db, self.body()))
        match = re.fullmatch(r"/api/clients/(\d+)", path, re.A)
        if match and method == "PATCH":
            client = find_client(db, int(match[1]))
            db.execute("UPDATE clients SET rate_cents=? WHERE id=?", (str(get_rate(self.body())), client["id"]))
            return self.reply(200, client_json(find_client(db, client["id"])))
        match = re.fullmatch(r"/api/entries/(\d+)", path, re.A)
        if match and method == "DELETE":
            return self.reply(*delete_entry(db, int(match[1])))
        match = re.fullmatch(r"/api/invoices/(\d+)\.csv", path, re.A)
        if match and method == "GET":
            return self.send(200, "text/csv; charset=utf-8", invoice_csv(db, int(match[1])).encode())
        match = re.fullmatch(r"/api/invoices/(\d+)", path, re.A)
        if match and method == "GET":
            return self.reply(200, find_invoice(db, int(match[1])))
        raise Fail(404, "not found")

    def reply(self, status, data):
        self.send(status, data=b"" if data is None else json.dumps(data).encode())

    def handle_any(self):
        with LOCK:
            try:
                self.route()
                self.db.commit()
            except Fail as exc:
                self.db.rollback()
                self.close_connection = True
                self.reply(exc.status, {"error": exc.message})

    do_GET = do_POST = do_PATCH = do_DELETE = do_PUT = handle_any


def summary(db_path, begin, finish):
    if parse_ts(finish) <= parse_ts(begin):
        raise Fail(400, "from must be before to")
    db = connect(db_path)
    out = []
    for client in db.execute("SELECT * FROM clients ORDER BY name"):
        rows = db.execute("SELECT * FROM entries WHERE client_id=? AND start>=? AND start<?", (client["id"], begin, finish)).fetchall()
        if rows:
            rate = int(client["rate_cents"])
            out.append({"client": client["name"], "minutes": sum(minutes_of(r["start"], r["end"]) for r in rows),
                        "uninvoiced_cents": sum(amount(minutes_of(r["start"], r["end"]), rate) for r in rows if r["invoice_id"] is None)})
    print(json.dumps(out))


def main():
    parser = argparse.ArgumentParser(prog="tallybook")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve")
    serve.add_argument("--port", type=int, required=True)
    serve.add_argument("--db", required=True)
    report = commands.add_parser("summary")
    report.add_argument("--db", required=True)
    report.add_argument("--from", dest="begin", required=True)
    report.add_argument("--to", dest="finish", required=True)
    args = parser.parse_args()
    if args.command == "serve":
        Handler.db = connect(args.db)
        ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
    try:
        summary(args.db, args.begin, args.finish)
    except Fail as exc:
        print(exc.message, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
