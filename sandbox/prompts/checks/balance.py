"""Test 1: GET /api/clients/{id}/balance on Tallybook, driven only through the running server."""
import http.client
import json
import socket
import subprocess
import sys
import tempfile
import time

from common import attempt, check, finish

with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
db = tempfile.mkdtemp() + "/t.db"
server = subprocess.Popen([sys.executable, "-m", "tallybook", "serve", "--port", str(port), "--db", db], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def call(method, path, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=8)
    try:
        conn.request(method, path, json.dumps(body) if body is not None else None, {"Content-Type": "application/json"})
        res = conn.getresponse()
        data = res.read()
        return res.status, json.loads(data) if data else None
    finally:
        conn.close()


try:
    for _ in range(100):
        try:
            if call("GET", "/api/health")[0] == 200:
                break
        except OSError:
            time.sleep(0.1)
    client = call("POST", "/api/clients", {"name": "Acme", "rate_cents": 150})[1]["id"]
    call("POST", "/api/entries", {"client_id": client, "start": "2026-01-05T09:00:00Z", "end": "2026-01-05T10:00:00Z"})
    call("POST", "/api/invoices", {"client_id": client, "from": "2026-01-05T00:00:00Z", "to": "2026-01-06T00:00:00Z"})
    call("POST", "/api/entries", {"client_id": client, "start": "2026-01-07T09:00:00Z", "end": "2026-01-07T09:01:00Z"})
    call("POST", "/api/entries", {"client_id": client, "start": "2026-01-08T09:00:00Z", "end": "2026-01-08T09:01:00Z"})
    attempt("balance counts only uninvoiced entries, rounded per entry", lambda: call("GET", f"/api/clients/{client}/balance"),
            (200, {"client_id": client, "uninvoiced_minutes": 2, "uninvoiced_cents": 6}))
    call("PATCH", f"/api/clients/{client}", {"rate_cents": 60})
    attempt("balance uses the current rate", lambda: call("GET", f"/api/clients/{client}/balance")[1]["uninvoiced_cents"], 2)
    status, body = call("GET", "/api/clients/999/balance")
    check("unknown client gets 404 in the usual format", status == 404 and isinstance((body or {}).get("error"), str), (status, body))
finally:
    server.kill()
finish()
