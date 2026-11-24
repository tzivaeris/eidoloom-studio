#!/usr/bin/env python3
"""A tiny in-memory listmonk API for the tests of tools/listmonk_mcp.py: python3 mock_listmonk.py PORT.

It also plays MailSender's own routes the tools use (MAILSENDER_URL): /pacer/status, /pacer/continues/<ids> (each campaign's record
list, set with POST /mock/record?campaign=N&list=M) and a status change held by the pacer (POST /mock/pacer?held=1 answers 409).
POST /mock/stuck?mode=soft|hard makes an import hang (hard: stopping it does not work, as with a database lock), and
POST /mock/restart is what LISTMONK_RESTART runs in the tests. It answers only to "Authorization: token tester:secret" and keeps what was sent (tests, emails, status changes) in
GET /mock/sent, so the tests can tell what would have reached people.
"""
import email.parser
import email.policy
import json
import re
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOCK = threading.Lock()
DB = {"lists": [{"id": 1, "name": "Γονείς", "type": "private", "optin": "single", "tags": [], "subscriber_count": 0}],
      "subs": [], "campaigns": [], "sent": [], "import": {"name": "", "total": 0, "imported": 0, "status": "none"}, "log": "",
      "records": {}, "held": False}
PACER = {"limit": 1500, "window_hours": 25, "used": 1400, "left": 100, "margin": 15, "full_at": None, "blocked_until": None, "block_reason": None,
         "resume_at": None, "queue": [], "events": []}
TEMPLATES = [{"id": 1, "name": "Newsletter", "type": "campaign", "is_default": True},
             {"id": 2, "name": "Newsletter – transactional", "type": "tx", "is_default": False}]


def add_sub(email, name, lists, attribs=None, status="enabled", sub_status="confirmed"):
    s = {"id": max([x["id"] for x in DB["subs"]] + [0]) + 1, "email": email.lower(), "name": name, "status": status, "attribs": attribs or {},
         "lists": [{"id": l, "name": next(x["name"] for x in DB["lists"] if x["id"] == l), "subscription_status": sub_status} for l in lists]}
    DB["subs"].append(s)
    return s


def in_lists(s, ids):
    return any(l["id"] in ids for l in s["lists"])


MEMBERS = r"subscribers\.id (NOT )?IN \(SELECT subscriber_id FROM subscriber_lists WHERE list_id IN \(([\d,]+)\)\)"


def matches(s, q, list_ids, sub_status):
    if list_ids and not any(l["id"] in list_ids for l in s["lists"]):
        return False
    for neg, ids in re.findall(MEMBERS, q):           # who is (or is not) in some lists, e.g. a continuation's records
        if in_lists(s, {int(i) for i in ids.split(",")}) == bool(neg):
            return False
    m = re.search(r"subscribers\.id IN \(([\d,]+)\)", q)        # people picked by hand
    if m and s["id"] not in {int(i) for i in m.group(1).split(",")} and not re.search(r"@> '", q):
        return False
    if sub_status and not any(l["subscription_status"] == sub_status and (not list_ids or l["id"] in list_ids) for l in s["lists"]):
        return False
    m = re.search(r"subscribers\.email = '([^']*)'", q)
    if m and s["email"] != m.group(1):
        return False
    m = re.search(r"LIKE '%(.*?)%'", q)
    if m and m.group(1) not in s["email"] and m.group(1) not in s["name"].lower():
        return False
    tags = (s.get("attribs") or {}).get("tags")
    if "jsonb_typeof(subscribers.attribs->'tags') = 'array'" in q and not isinstance(tags, list):
        return False
    wants = [json.loads(w.replace("''", "'"))[0] for w in re.findall(r"subscribers\.attribs->'tags' @> '(\[.*?\])'::jsonb", q)]
    if wants:
        have = tags if isinstance(tags, list) else []
        hit = any if " OR subscribers.attribs->'tags'" in q else all
        if not hit(t in have for t in wants):
            return False
    if "subscribers.status = 'enabled'" in q and s["status"] != "enabled":
        return False
    if "NOT IN (SELECT subscriber_id FROM subscriber_lists WHERE status = 'unsubscribed')" in q and any(
            l["subscription_status"] == "unsubscribed" for l in s["lists"]):
        return False
    if "subscribers.status != 'blocklisted'" in q and s["status"] == "blocklisted":
        return False
    if "subscriber_lists.status != 'unsubscribed'" in q and not any(l["subscription_status"] != "unsubscribed" and (not list_ids or l["id"] in list_ids) for l in s["lists"]):
        return False
    return True


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, code, data=None, raw=None):
        body = raw.encode() if raw is not None else json.dumps({"data": data} if code < 400 else {"message": data}, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html" if raw is not None else "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def reply_plain(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def handle_any(self, method):
        url = urllib.parse.urlparse(self.path)
        path, q = url.path, urllib.parse.parse_qs(url.query)
        if path == "/mock/sent":
            return self.reply(200, DB["sent"])
        if path == "/mock/stuck":          # an import that never moves: "soft" stops when asked, "hard" does not (a database lock)
            DB["import"] = {"name": "stuck.csv", "total": 50, "imported": 0, "status": "importing"}
            DB["stuck"] = (urllib.parse.parse_qs(url.query).get("mode") or ["soft"])[0]
            return self.reply(200, True)
        if path == "/mock/tracking":       # MailSender's tracking connection on (1) or off (0)
            DB["tracking_off"] = (urllib.parse.parse_qs(url.query).get("on") or ["0"])[0] != "1"
            return self.reply(200, True)
        if path == "/mock/record":         # campaign N reached the people on list M (MailSender's record list)
            DB["records"][int(q["campaign"][0])] = int(q["list"][0])
            return self.reply(200, True)
        if path == "/mock/pacer":          # nothing may be sent now: starting a campaign is held (MailSender answers 409)
            DB["held"] = q.get("held", ["0"])[0] == "1"
            return self.reply(200, True)
        if path == "/mock/restart":
            DB["import"], DB["stuck"] = {"name": "", "total": 0, "imported": 0, "status": "none"}, None
            DB["sent"].append({"kind": "restart"})
            return self.reply(200, True)
        if self.headers.get("Authorization") != "token tester:secret":
            return self.reply(403, "invalid session")
        raw = self.body()
        ctype = self.headers.get("Content-Type", "")
        data = json.loads(raw) if raw and "json" in ctype else {}
        with LOCK:
            return self.route(method, path, q, data, raw, ctype)

    def route(self, method, path, q, data, raw, ctype):
        one = lambda key: (q.get(key) or [""])[0]
        if path == "/pacer/status":
            return self.reply_plain({**PACER, "next_free": [{"at": "2026-11-21T10:00:00+00:00", "n": 700}],
                                     "frees": [{"at": "2026-11-21T10:00:00+00:00", "n": 700}, {"at": "2026-11-21T12:00:00+00:00", "n": 700}]})
        m = re.fullmatch(r"/pacer/continues/([\d,]+)", path)
        if m:
            ids = [int(i) for i in m.group(1).split(",")]
            recs = [DB["records"][i] for i in ids if i in DB["records"]]
            return self.reply_plain({"sql": f"subscribers.id IN (SELECT subscriber_id FROM subscriber_lists WHERE list_id IN ({','.join(map(str, recs))}))" if recs else None,
                                     "records": {str(i): DB["records"].get(i) for i in ids}, "missing": []})
        if path == "/api/dashboard/counts":
            return self.reply(200, {"subscribers": {"total": len(DB["subs"]), "blocklisted": sum(s["status"] == "blocklisted" for s in DB["subs"])},
                                    "campaigns": {"total": len(DB["campaigns"])}, "messages": len(DB["sent"])})
        if path == "/api/lists" and method == "GET":
            for l in DB["lists"]:
                l["subscriber_count"] = sum(any(x["id"] == l["id"] for x in s["lists"]) for s in DB["subs"])
            return self.reply(200, {"results": DB["lists"], "total": len(DB["lists"])})
        if path == "/api/lists" and method == "POST":
            l = {"id": len(DB["lists"]) + 1, "subscriber_count": 0, **{k: data[k] for k in ("name", "type", "optin", "tags")}}
            l["tags"] = [t.replace(" ", "-") for t in l["tags"] or []]        # as listmonk does
            DB["lists"].append(l)
            return self.reply(200, l)
        if path == "/api/subscribers" and method == "DELETE":
            ids = {int(x) for x in q.get("id", [])}
            DB["subs"] = [x for x in DB["subs"] if x["id"] not in ids]
            DB["sent"].append({"kind": "delete", "ids": sorted(ids)})
            return self.reply(200, True)
        m = re.fullmatch(r"/api/lists/(\d+)", path)
        if m and method == "PUT":
            l = next(x for x in DB["lists"] if x["id"] == int(m.group(1)))
            l.update({k: data[k] for k in ("name", "type", "optin", "tags") if k in data})
            l["tags"] = [t.replace(" ", "-") for t in l.get("tags") or []]   # as listmonk does
            return self.reply(200, l)
        if path == "/api/subscribers" and method == "GET":
            ids = [int(x) for x in q.get("list_id", [])]
            found = [s for s in DB["subs"] if matches(s, one("query"), ids, one("subscription_status"))]
            per = one("per_page")
            return self.reply(200, {"results": found if per in ("", "all") else found[:int(per)], "total": len(found)})
        if path == "/api/subscribers" and method == "POST":
            if any(s["email"] == data["email"] for s in DB["subs"]):
                return self.reply(409, "E-mail already exists.")
            return self.reply(200, add_sub(data["email"], data["name"], data.get("lists") or [], data.get("attribs"), data.get("status", "enabled")))
        m = re.fullmatch(r"/api/subscribers/(\d+)", path)
        if m and method == "PUT":
            s = next(s for s in DB["subs"] if s["id"] == int(m.group(1)))
            s.update(email=data["email"], name=data["name"], status=data["status"], attribs=data["attribs"])
            if "lists" in data:   # as listmonk does: memberships not in "lists" are dropped (with their status), the others kept
                keep = {l["id"]: l for l in s["lists"]}
                s["lists"] = [keep.get(l) or {"id": l, "name": next(x["name"] for x in DB["lists"] if x["id"] == l), "subscription_status": "confirmed"}
                              for l in data["lists"]]
            return self.reply(200, s)
        if path == "/api/subscribers/lists" and method == "PUT":
            for s in DB["subs"]:
                if s["id"] not in data["ids"]:
                    continue
                for t in data["target_list_ids"]:
                    have = next((l for l in s["lists"] if l["id"] == t), None)
                    if data["action"] == "add" and not have:
                        s["lists"].append({"id": t, "name": next(x["name"] for x in DB["lists"] if x["id"] == t), "subscription_status": "confirmed"})
                    elif data["action"] == "remove" and have:
                        s["lists"].remove(have)
                    elif data["action"] == "unsubscribe" and have:
                        have["subscription_status"] = "unsubscribed"
            return self.reply(200, True)
        if path == "/api/settings" and method == "GET":
            return self.reply(200, {"privacy.disable_tracking": DB.get("tracking_off", True)})
        if path == "/api/subscribers/query/lists" and method == "PUT":
            for s in [s for s in DB["subs"] if matches(s, data.get("query") or "", data.get("list_ids") or [], "")]:
                for t in data["target_list_ids"]:
                    have = next((l for l in s["lists"] if l["id"] == t), None)
                    if data["action"] == "add" and not have:
                        s["lists"].append({"id": t, "name": next(x["name"] for x in DB["lists"] if x["id"] == t), "subscription_status": data.get("status") or "confirmed"})
                    elif data["action"] == "remove" and have:
                        s["lists"].remove(have)
            return self.reply(200, True)
        if path == "/api/health":
            return self.reply(200, True)
        if path == "/api/import/subscribers" and method == "DELETE":
            if DB.get("stuck") != "hard" and DB["import"]["status"] == "importing":
                DB["import"]["status"] = "stopped"
            return self.reply(200, DB["import"])
        if path == "/api/import/subscribers" and method == "GET":
            return self.reply(200, DB["import"])
        if path == "/api/import/subscribers/logs":
            return self.reply(200, DB["log"])
        if path == "/api/import/subscribers" and method == "POST" and DB["import"]["status"] == "importing":
            return self.reply(400, "an import is already running")
        if path == "/api/import/subscribers" and method == "POST":
            msg = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + raw)
            parts = {p.get_param("name", header="content-disposition"): p for p in msg.iter_parts()}
            params = json.loads(parts["params"].get_content())
            rows = [r for r in parts["file"].get_payload(decode=True).decode("utf-8-sig").splitlines() if r.strip()]
            head = rows[0].split(params["delim"])
            done = 0
            for r in rows[1:]:
                cells = dict(zip(head, next(__import__("csv").reader([r], delimiter=params["delim"]))))
                if any(s["email"] == cells["email"].lower() for s in DB["subs"]):
                    continue
                add_sub(cells["email"], cells.get("name", ""), params["lists"], json.loads(cells.get("attributes") or "{}"))
                done += 1
            DB["import"] = {"name": parts["file"].get_filename(), "total": len(rows) - 1, "imported": done, "status": "finished"}
            DB["log"] = f"imported {done} records"
            return self.reply(200, DB["import"])
        if path == "/api/templates":
            return self.reply(200, TEMPLATES)
        if path == "/api/campaigns" and method == "GET":
            rows = [c for c in DB["campaigns"] if not one("status") or c["status"] == one("status")]
            return self.reply(200, {"results": rows[::-1], "total": len(rows)})
        if path == "/api/campaigns" and method == "POST":
            data["tags"] = [t.replace(" ", "-") for t in data.get("tags") or []]      # as listmonk does
            c = {"id": len(DB["campaigns"]) + 1, "status": "draft", "sent": 0, "to_send": 0, **data,
                 "lists": [{"id": i, "name": next(x["name"] for x in DB["lists"] if x["id"] == i)} for i in data["lists"]],
                 "media": [{"id": i, "filename": f"file{i}.pdf"} for i in data.get("media") or []]}
            DB["campaigns"].append(c)
            return self.reply(200, c)
        m = re.fullmatch(r"/api/campaigns/(\d+)(/status|/test|/preview)?", path)
        if m:
            c = next((c for c in DB["campaigns"] if c["id"] == int(m.group(1))), None)
            if not c:
                return self.reply(404, "campaign not found")
            if not m.group(2) and method == "GET":
                return self.reply(200, c)
            if not m.group(2) and method == "PUT":
                data["tags"] = [t.replace(" ", "-") for t in data.get("tags") or []]  # as listmonk does
                c.update({**data, "lists": [{"id": i, "name": next(x["name"] for x in DB["lists"] if x["id"] == i)} for i in data["lists"]],
                          "media": [{"id": i, "filename": f"file{i}.pdf"} for i in data.get("media") or []]})
                return self.reply(200, c)
            if m.group(2) == "/status":
                if DB["held"] and data["status"] == "running":
                    DB["sent"].append({"kind": "held", "campaign": c["id"]})
                    return self.reply_plain({"message": "held", "held": True, "pacer": {**PACER, "resume_at": "2026-11-21T10:00:00+00:00"}}, 409)
                c["status"] = data["status"]
                DB["sent"].append({"kind": "status", "campaign": c["id"], "status": data["status"]})
                return self.reply(200, c)
            if m.group(2) == "/test":
                DB["sent"].append({"kind": "test", "campaign": c["id"], "to": data["subscribers"]})
                return self.reply(200, True)
            if m.group(2) == "/preview":
                form = urllib.parse.parse_qs(raw.decode())
                return self.reply(200, raw=f"<html><body><img src=logo.png><div>{form['body'][0]}</div><p>Unsubscribe</p></body></html>")
        if path == "/api/campaigns/running/stats":
            return self.reply(200, [{"id": int(one("campaign_id")), "sent": 1, "to_send": 2, "net_rate": 1}])
        if path == "/api/tx" and method == "POST":
            DB["sent"].append({"kind": "tx", **data})
            return self.reply(200, True)
        return self.reply(404, f"no route {method} {path}")

    def do_GET(self):
        self.handle_any("GET")

    def do_POST(self):
        self.handle_any("POST")

    def do_PUT(self):
        self.handle_any("PUT")

    def do_DELETE(self):
        self.handle_any("DELETE")


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
