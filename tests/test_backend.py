#!/usr/bin/env python3
"""End-to-end test of studio.py against mock_gateway.py. Run: python3 tests/test_backend.py"""
import base64
import http.client
import importlib.util
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import types
import urllib.error
import urllib.request
import re

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
RESULTS = {"pass": 0, "fail": 0}


def check(name, cond, detail=""):
    RESULTS["pass" if cond else "fail"] += 1
    print(("  ok    " if cond else "  FAIL  ") + name + ("" if cond else f"   [{detail}]"))
    return cond


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_port(port, proc, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        if proc.poll() is not None:
            raise RuntimeError("process exited early")
        try:
            socket.create_connection(("127.0.0.1", port), 0.5).close()
            return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("port never opened")


class Client:
    """Minimal HTTP client with cookie handling and an SSE reader."""

    def __init__(self, port):
        self.port = port
        self.cookie = None
        self.ip = None                  # sent as X-Forwarded-For, so each test client has its own login limit

    def request(self, method, path, body=None, csrf=True, headers=None, cookie=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        h = dict(headers or {})
        if csrf and method == "POST":
            h["X-Studio"] = "1"
        if cookie and self.cookie:
            h["Cookie"] = self.cookie
        if self.ip:
            h["X-Forwarded-For"] = self.ip
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        conn.request(method, path, data, h)
        resp = conn.getresponse()
        raw = resp.read()
        conn.close()
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = raw
        set_cookie = resp.getheader("Set-Cookie")
        if set_cookie and set_cookie.startswith("studio_session_"):
            pair = set_cookie.split(";")[0]
            self.cookie = pair if pair.split("=", 1)[1] else None
        return resp.status, parsed, resp

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.request("POST", path, {} if body is None else body, **kw)

    def sse(self, path, timeout=30):
        """Yield (event, data) pairs from an SSE endpoint until it closes."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        conn.request("GET", path, headers={"Cookie": self.cookie})
        resp = conn.getresponse()
        if resp.status != 200:
            yield "http_error", resp.status
            return
        event = None
        try:
            while True:
                line = resp.readline()
                if not line:
                    return
                line = line.decode().rstrip("\n")
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:") and event:
                    yield event, json.loads(line[5:])
                    event = None
        finally:
            conn.close()


# ---------------------------------------------------------------------------

def make_fixture(tmp):
    projects = os.path.join(tmp, "projects")
    sample = os.path.join(projects, "sample")
    outside = os.path.join(tmp, "outside")
    for d in (os.path.join(sample, "src"), os.path.join(sample, "node_modules"), outside):
        os.makedirs(d)
    files = {
        "README.md": "# Sample Project\n\nA tiny project for testing.\n",
        "src/main.py": "def main():\n    print('hello world')\n",
        "node_modules/dep.js": "Sample dependency\n",
    }
    for rel, text in files.items():
        with open(os.path.join(sample, rel), "w") as fh:
            fh.write(text)
    with open(os.path.join(sample, "blob.bin"), "wb") as fh:
        fh.write(b"\x00\x01\x02Sample\xff")
    with open(os.path.join(outside, "secret.txt"), "w") as fh:
        fh.write("TOP SECRET")
    os.symlink(outside, os.path.join(sample, "escape"))
    os.symlink(outside, os.path.join(sample, "link2"))
    os.symlink(os.path.join(outside, "secret.txt"), os.path.join(sample, "leak.txt"))
    return projects, sample, outside


def start_studio(tmp, cfg_path, port):
    log = open(os.path.join(tmp, "studio.log"), "a")
    proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "studio.py"), "--config", cfg_path,
                             "--host", "127.0.0.1", "--port", str(port)], stdout=log, stderr=log)
    wait_port(port, proc)
    return proc


def run_agent(c, ws, session, message, model="quartz:strata", auto_apply=False, auto_commands=False,
              on_approval=None, context=None, stop_on=None):
    """Send a message and consume the SSE stream, answering approvals via on_approval(event)."""
    body = {"session": session, "ws": ws, "message": message, "model": model,
            "auto_apply": auto_apply, "auto_commands": auto_commands}
    if context:
        body["context"] = context
    status, data, _ = c.post("/api/agent/send", body)
    assert status == 200, (status, data)
    run = data["run"]
    events = []
    for name, ev in c.sse(f"/api/agent/stream?run={run}", timeout=60):
        events.append(ev)
        if name == "approval":
            if stop_on and ev["kind"] == stop_on:
                c.post("/api/agent/stop", {"run": run})
            else:
                decision, note = on_approval(ev)
                c.post("/api/agent/decision", {"run": run, "approval_id": ev["approval_id"],
                                               "decision": decision, "note": note})
    return run, events


def kinds(events):
    return [e["type"] for e in events]


def test_voices(tmp, gw_port):
    """The Voices view's routes and the voice MCP server, against tools/voice_engine.py in its fake mode (a tone, no model)."""
    print("voices")
    # Making room (Studio's "Strata first"): an idle Strata is unloaded through the gateway, a busy one is left alone.
    import http.server as hs
    manager_seen = {"running": True, "busy": 0, "stops": 0}

    class FakeManager(hs.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def reply(self, data):
            body = json.dumps(data).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.reply({"running": manager_seen["running"], "lifecycle": {"active_requests": manager_seen["busy"]}})

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            manager_seen["stops"] += 1
            manager_seen["running"] = False
            self.reply({"ok": True})
    fake_manager = hs.ThreadingHTTPServer(("127.0.0.1", 0), FakeManager)
    threading.Thread(target=fake_manager.serve_forever, daemon=True).start()
    spec = importlib.util.spec_from_file_location("voice_engine_room", os.path.join(ROOT, "tools", "voice_engine.py"))
    ve = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ve)
    ve.MANAGER_URL = f"http://127.0.0.1:{fake_manager.server_port}"
    manager_seen["busy"] = 1
    job = {"state": "queued"}
    check("a Strata that is answering someone is not unloaded for a reading", ve.make_room(job) is False and manager_seen["stops"] == 0)
    manager_seen["busy"] = 0
    check("an idle Strata is unloaded to make room, and the reading says so", ve.make_room(job) is True and manager_seen["stops"] == 1
          and job["state"] == "making_room" and ve.state["made_room"] is True)
    check("with Strata gone there is nothing more to unload", ve.make_room({"state": "queued"}) is False and manager_seen["stops"] == 1)
    ve.MANAGER_URL = "http://127.0.0.1:9"
    check("without a gateway it just waits, as before", ve.make_room({"state": "queued"}) is False)
    fake_manager.shutdown()
    folder = os.path.join(tmp, "voices")
    projects = os.path.join(folder, "projects")
    os.makedirs(os.path.join(projects, "book"))
    engine_port, port = free_port(), free_port()
    engine = subprocess.Popen([sys.executable, os.path.join(ROOT, "tools", "voice_engine.py")], env={**os.environ, "VOICE_FAKE": "1",
                              "VOICE_PORT": str(engine_port), "VOICE_OUT_ROOTS": folder}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    config = os.path.join(folder, "app", "studio.config.json")
    os.makedirs(os.path.dirname(config))
    json.dump({"host": "127.0.0.1", "port": port, "gateway": f"http://127.0.0.1:{gw_port}", "workspace_roots": [projects], "user_root": os.path.join(projects, "studio-users"),
               "default_model": "quartz:strata", "max_turns": 10, "command_timeout_s": 20, "voice_url": f"http://127.0.0.1:{engine_port}"}, open(config, "w"))
    server = start_studio(folder, config, port)

    def wait(client, job, timeout=30):
        deadline = time.time() + timeout
        while time.time() < deadline:
            st = client.get(f"/api/voices/job?id={job}")[1]
            if st.get("state") in ("done", "error", "cancelled"):
                return st
            time.sleep(0.3)
        return {}
    try:
        wait_port(engine_port, engine)
        admin = Client(port)
        admin.post("/api/auth/setup", {"password": "admin password"})
        bob = Client(port)
        bob.post("/api/auth/signup", {"token": admin.post("/api/admin/invites", {})[1]["token"], "username": "bob", "password": "member password"})
        d = admin.get("/api/voices")[1]
        check("the Voices view sees the engine and an empty cast", d.get("enabled") and d["engine"]["ok"] and d["voices"] == [] and d["sample_text"], d)
        check("a voice needs a description", admin.post("/api/voices/design", {"description": ""})[0] == 400)
        jobs = admin.post("/api/voices/design", {"description": "Μια ήρεμη γυναικεία φωνή", "takes": 2})[1]["jobs"]
        states = [wait(admin, j) for j in jobs]
        check("a new voice comes as takes to choose from", len(jobs) == 2 and all(st.get("state") == "done" and st.get("url") for st in states), states)
        status, audio, resp = admin.get(states[0]["url"])
        check("a take can be played", status == 200 and audio[:4] == b"RIFF" and resp.getheader("Content-Type") == "audio/wav")
        check("but not by someone else", bob.get(states[0]["url"])[0] == 404)
        s, d, _ = admin.post("/api/voices/save", {"job": jobs[1], "name": "Αφηγήτρια", "description": "Μια ήρεμη γυναικεία φωνή"})
        check("the chosen take becomes a voice in the cast", s == 200 and d["voice"]["name"] == "Αφηγήτρια", d)
        vid = d["voice"]["id"]
        check("its recording is kept", os.path.isfile(os.path.join(folder, "app", "data", "voices", "samples", vid + ".wav")))
        check("two voices cannot share a name", admin.post("/api/voices/save", {"job": jobs[0], "name": "αφηγήτρια"})[0] == 409)
        listed = bob.get("/api/voices")[1]["voices"]
        check("everyone sees the cast, and only its maker or an admin may change a voice", [v["name"] for v in listed] == ["Αφηγήτρια"] and listed[0]["can_edit"] is False
              and bob.post("/api/voices/save", {"id": vid, "name": "Mine"})[0] == 403 and bob.post("/api/voices/delete", {"id": vid})[0] == 403)
        check("a voice has a language, Greek unless chosen", listed[0]["lang"] == "el" and set(bob.get("/api/voices")[1]["sample_texts"]) == {"el", "en"})
        check("only known languages", admin.post("/api/voices/save", {"id": vid, "name": "Αφηγήτρια", "lang": "xx"})[0] == 400)
        check("a voice's recording can be played by anyone signed in", bob.get(f"/api/voices/audio?voice={vid}")[0] == 200 and Client(port).get(f"/api/voices/audio?voice={vid}", cookie=False)[0] == 401)
        s, d, _ = bob.post("/api/voices/speak", {"voice": vid, "text": "Καλημέρα σε όλους. Σήμερα διαβάζουμε μαζί.", "format": "mp3"})
        st = wait(bob, d["job"])
        check("anyone can read text in a voice of the cast, as MP3", s == 200 and st.get("state") == "done", st)
        status, audio, resp = bob.get(st["url"])
        check("the reading is an MP3", status == 200 and resp.getheader("Content-Type") == "audio/mpeg" and len(audio) > 500)
        check("reading needs a voice and text", bob.post("/api/voices/speak", {"voice": "nope", "text": "x"})[0] == 404 and bob.post("/api/voices/speak", {"voice": vid, "text": " "})[0] == 400)
        ws = admin.post("/api/workspace/open", {"path": os.path.join(projects, "book")})[1]["workspace"]["id"]
        mine = admin.post("/api/voices/speak", {"voice": vid, "text": "Κεφάλαιο πρώτο."})[1]["job"]
        wait(admin, mine)
        s, d, _ = admin.post("/api/voices/to_project", {"ws": ws, "job": mine, "path": "voice/chapter-1.mp3"})
        check("a reading can be saved into the project", s == 200 and os.path.isfile(os.path.join(projects, "book", "voice", "chapter-1.mp3")), d)
        again = admin.post("/api/voices/to_project", {"ws": ws, "job": mine, "path": "voice/chapter-1.mp3"})[1]
        check("saving over a file puts the old one in the trash", again.get("trashed"), again)
        check("only inside the folder, and only as audio", admin.post("/api/voices/to_project", {"ws": ws, "job": mine, "path": "../out.mp3"})[0] == 403
              and admin.post("/api/voices/to_project", {"ws": ws, "job": mine, "path": "notes.txt"})[0] == 400)
        check("nobody else can save my reading", bob.post("/api/voices/to_project", {"ws": ws, "job": mine, "path": "x.mp3"})[0] in (403, 404))
        req = urllib.request.Request(f"http://127.0.0.1:{engine_port}/speak", data=json.dumps({"text": "x", "out": "/tmp/escape.wav"}).encode(), headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=10)
            refused = False
        except urllib.error.HTTPError as e:
            refused = e.code == 400
        check("the engine writes only inside its allowed folders", refused)

        print("voice MCP server")
        out = os.path.join(projects, "book", "voice", "intro.mp3")
        calls = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                 {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "list_voices", "arguments": {}}},
                 {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "speak", "arguments": {"text": "Καλωσήρθατε στο βιβλίο.", "voice": "αφηγήτρια", "path": out}}},
                 {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "speak", "arguments": {"text": "x", "voice": "Nobody", "path": out}}},
                 {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "speak", "arguments": {"text": "x", "voice": "Αφηγήτρια", "path": "/etc/x.mp3"}}}]
        mcp = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "voice_mcp.py")], input="\n".join(json.dumps(c) for c in calls) + "\n", capture_output=True, text=True, timeout=60,
                             env={**os.environ, "VOICE_URL": f"http://127.0.0.1:{engine_port}", "VOICES_FILE": os.path.join(folder, "app", "data", "voices", "voices.json"), "VOICE_ROOTS": projects})
        answers = {a["id"]: a for a in map(json.loads, mcp.stdout.splitlines())}
        text = lambda i: answers[i]["result"]["content"][0]["text"]
        check("the voice MCP server offers list_voices, speak and save_voice", [t["name"] for t in answers[2]["result"]["tools"]] == ["list_voices", "speak", "save_voice"])
        check("list_voices names the cast, with each voice's language", "Αφηγήτρια [Greek, designed" in text(3), text(3))
        check("speak reads text in a voice of the cast into the project", not answers[4]["result"]["isError"] and os.path.isfile(out) and "Αφηγήτρια" in text(4), text(4))
        check("an unknown voice is refused, naming the real ones", answers[5]["result"]["isError"] and "Αφηγήτρια" in text(5))
        check("and so is a file outside the projects", answers[6]["result"]["isError"])
        print("voices from recordings")
        rec = os.path.join(projects, "book", "voice", "mello.wav")
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=220:duration=12", "-ac", "2", "-y", rec], check=True)
        base = {"ws": ws, "path": "voice/mello.wav", "speaker": "Mello, a designed voice the owner made", "consent": "made", "transcript": "Γεια σου, είμαι ο Μέλο."}
        check("copying a voice needs a reason it is allowed", bob.post("/api/voices/from_recording", {**base, "consent": "found it"})[0] in (400, 403, 404)
              and admin.post("/api/voices/from_recording", {**base, "consent": ""})[0] == 400)
        check("and whose voice it is", admin.post("/api/voices/from_recording", {**base, "speaker": " "})[0] == 400)
        check("only a sound file inside the folder", admin.post("/api/voices/from_recording", {**base, "path": "../../etc/passwd"})[0] in (400, 403)
              and admin.post("/api/voices/from_recording", {**base, "path": "voice/chapter-1.txt"})[0] == 400)
        check("a reference over 30 s or under 1 s is refused", admin.post("/api/voices/from_recording", {**base, "start": 5, "end": 5.5})[0] == 400)
        s, d, _ = admin.post("/api/voices/from_recording", {**base, "start": 1, "end": 9})
        st = wait(admin, d.get("job"))
        check("a recording gives a test reading in its voice", s == 200 and d["seconds"] == 8 and st.get("state") == "done", (d, st))
        s, d, _ = admin.post("/api/voices/save", {"job": d["job"], "name": "Mello", "lang": "el", "description": "a small warm creature", "transcript": "Γεια σου, είμαι ο Μέλο."})
        mello = d.get("voice") or {}
        sample = os.path.join(folder, "app", "data", "voices", "samples", mello.get("id", "x") + ".wav")
        check("which is kept as a voice with its source, transcript and consent", s == 200 and mello["origin"] == "recording" and mello["consent"] == "made"
              and mello["source"] == "voice/mello.wav" and mello["transcript"].startswith("Γεια") and mello["seconds"] == 8, mello)
        info = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=channels", "-of", "json", sample], capture_output=True, text=True).stdout
        check("the voice keeps the chosen part of the recording itself, as mono", abs(float(json.loads(info)["format"]["duration"]) - 8) < 0.1
              and json.loads(info)["streams"][0]["channels"] == 1, info)
        check("its transcript can be changed later", admin.post("/api/voices/save", {"id": mello["id"], "name": "Mello", "transcript": "Γεια!"})[1]["voice"]["transcript"] == "Γεια!")
        s, d, _ = bob.post("/api/voices/speak", {"voice": mello["id"], "text": "Πάμε να παίξουμε!"})
        check("and anyone can read in it", s == 200 and wait(bob, d["job"]).get("state") == "done")
        clone_out = os.path.join(projects, "book", "voice", "pip.mp3")
        calls = [{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "save_voice", "arguments": {"name": "Pip", "recording": rec, "start": 0, "end": 6, "lang": "en",
                  "transcript": "Hi, I'm Pip.", "consent": "the owner's own designed voice for Pip"}}},
                 {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "save_voice", "arguments": {"name": "Pip2", "recording": rec, "lang": "en", "consent": "ok"}}},
                 {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "save_voice", "arguments": {"name": "mello", "recording": rec, "end": 5, "lang": "el", "consent": "the owner's own voice"}}},
                 {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "save_voice", "arguments": {"name": "Etc", "recording": "/etc/hostname", "lang": "el", "consent": "the owner's own voice"}}},
                 {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "list_voices", "arguments": {}}},
                 {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "speak", "arguments": {"text": "Let's go!", "voice": "pip", "path": clone_out}}},
                 {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "speak", "arguments": {"text": "Once more.", "reference": rec, "reference_end": 4,
                  "reference_transcript": "Hi.", "path": os.path.join(projects, "book", "voice", "once.wav")}}}]
        mcp = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "voice_mcp.py")], input="\n".join(json.dumps(c) for c in calls) + "\n", capture_output=True, text=True, timeout=90,
                             env={**os.environ, "VOICE_URL": f"http://127.0.0.1:{engine_port}", "VOICES_FILE": os.path.join(folder, "app", "data", "voices", "voices.json"), "VOICE_ROOTS": projects})
        answers = {a["id"]: a for a in map(json.loads, mcp.stdout.splitlines())}
        check("save_voice keeps a recording as a named voice, with its consent", not answers[1]["result"]["isError"] and "Pip" in text(1), text(1))
        check("save_voice needs a real reason it is allowed", answers[2]["result"]["isError"] and "consent" in text(2))
        check("save_voice never replaces a voice of the same name", answers[3]["result"]["isError"] and "already" in text(3))
        check("save_voice reads only inside the projects", answers[4]["result"]["isError"])
        check("list_voices says where each voice came from", "Pip [English, copied from a recording]" in text(5) and "Mello [Greek, copied from a recording]" in text(5), text(5))
        check("speak reads in a voice the agent saved", not answers[6]["result"]["isError"] and os.path.isfile(clone_out), text(6))
        check("speak reads in the voice of a recording, with its transcript", not answers[7]["result"]["isError"] and "with its transcript" in text(7), text(7))
        pip = next(v for v in admin.get("/api/voices")[1]["voices"] if v["name"] == "Pip")
        check("Studio's Voices view shows the agent's voice and its source", pip["origin"] == "recording" and pip["consent"].startswith("the owner") and pip["seconds"] == 6, pip)
        print("uploads")

        def upload(client, rel, data, uid, offset=0, total=None, conflict="", ws_id=ws):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
            q = urllib.parse.urlencode({"ws": ws_id, "path": rel, "id": uid, "offset": offset, "total": len(data) if total is None else total, "conflict": conflict})
            conn.request("POST", "/api/files/upload?" + q, data, {"X-Studio": "1", "Cookie": client.cookie or "", "Content-Type": "application/octet-stream"})
            resp = conn.getresponse()
            body = json.loads(resp.read() or b"{}")
            conn.close()
            return resp.status, body
        blob = os.urandom(3000)
        s1, d1 = upload(admin, "drops/take.bin", blob[:1000], "aaaa1111", 0, 3000)
        check("the first part of an upload is kept aside, not in the folder yet", s1 == 200 and d1["have"] == 1000 and not os.path.exists(os.path.join(projects, "book", "drops", "take.bin")), d1)
        check("parts must come in order", upload(admin, "drops/take.bin", blob[2000:], "aaaa1111", 2000, 3000)[0] == 409)
        s2, d2 = upload(admin, "drops/take.bin", blob[1000:], "aaaa1111", 1000, 3000)
        check("the last part moves the whole file into the folder", s2 == 200 and d2["path"] == "drops/take.bin" and open(os.path.join(projects, "book", "drops", "take.bin"), "rb").read() == blob, d2)
        check("names already taken are found before uploading", admin.post("/api/files/upload_check", {"ws": ws, "paths": ["drops/take.bin", "drops/new.bin"]})[1]["taken"] == ["drops/take.bin"])
        s3, d3 = upload(admin, "drops/take.bin", b"again", "bbbb2222")
        check("keep both gives the new file a free name", s3 == 200 and d3["path"] == "drops/take (2).bin", d3)
        s4, d4 = upload(admin, "drops/take.bin", b"newer", "cccc3333", conflict="replace")
        check("replace puts the old file in the trash", s4 == 200 and d4["trashed"] and open(os.path.join(projects, "book", "drops", "take.bin"), "rb").read() == b"newer", d4)
        check("uploads stay inside the folder", upload(admin, "../escape.bin", b"x", "dddd4444")[0] == 403)
        check("and in a folder the person may open", upload(bob, "x.bin", b"x", "eeee5555")[0] in (403, 404))
        check("an upload needs Studio's header, like every change", Client(port).post("/api/files/upload_check", {"ws": ws, "paths": []}, csrf=False)[0] in (401, 403))
        sound = os.path.join(projects, "book", "attachments", "line.wav")
        os.makedirs(os.path.dirname(sound), exist_ok=True)
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=300:duration=2.5", "-ar", "22050", "-y", sound], check=True)
        import studio as sm
        facts = sm.media_facts(sound)
        check("a sound added to a message is described to the model by its length and rate", "sound, 2.50 s" in facts and "22050 Hz" in facts, facts)
        text = sm.project_files_text({"path": os.path.realpath(os.path.join(projects, "book"))}, [{"kind": "file", "name": "line.wav", "path": "attachments/line.wav"},
                                                                              {"kind": "file", "name": "take (2).bin", "path": "drops/take (2).bin"}])
        check("the model gets its path in the project and its full path for tool servers", "attachments/line.wav" in text and os.path.realpath(sound) in text and "Attached file drops/take (2).bin:\n```\nagain" in text, text)
        refused = admin.post("/api/agent/send", {"ws": ws, "session": admin.post("/api/agent/sessions", {"ws": ws})[1]["id"], "model": "quartz:strata", "message": "x", "attachments": [{"kind": "file", "name": "p", "path": "../../etc/passwd"}]})
        ok = admin.post("/api/agent/send", {"ws": ws, "session": admin.post("/api/agent/sessions", {"ws": ws})[1]["id"], "model": "quartz:strata", "message": "use this sound",
                                            "attachments": [{"kind": "file", "name": "line.wav", "path": "attachments/line.wav"}]})
        check("a file outside the folder is refused before a run starts, so the folder is not left busy", refused[0] in (400, 403) and ok[0] == 200, (refused[:2], ok[:2]))
        check("a sound from the folder goes with the message and is named on it", ok[1].get("saved_files") == ["attachments/line.wav"], ok[1])
        print("downloads and moves")
        import io as _io
        import zipfile as _zip
        book = os.path.join(projects, "book")
        os.makedirs(os.path.join(book, "drops", "deep", "node_modules"), exist_ok=True)
        open(os.path.join(book, "drops", "deep", "ψ.txt"), "w").write("γεια")
        open(os.path.join(book, "drops", "deep", "node_modules", "big.js"), "w").write("x")
        s, d, r = admin.get("/api/files/download?" + urllib.parse.urlencode({"ws": ws, "path": "drops/take.bin"}))
        check("one file downloads as it is, as an attachment", s == 200 and d == b"newer" and r.getheader("Content-Disposition").startswith('attachment; filename="take.bin"'), r.getheader("Content-Disposition"))
        s, d, r = admin.get("/api/files/download?" + urllib.parse.urlencode({"ws": ws, "path": "drops/deep/ψ.txt"}))
        check("a Greek name is kept for the browser", s == 200 and "filename*=UTF-8''%CF%88.txt" in r.getheader("Content-Disposition"), r.getheader("Content-Disposition"))
        s, d, r = admin.get("/api/files/download?" + urllib.parse.urlencode({"ws": ws, "path": "drops"}))
        names = sorted(_zip.ZipFile(_io.BytesIO(d)).namelist()) if s == 200 else []
        check("a folder downloads as a zip, without node_modules inside", s == 200 and "drops/deep/ψ.txt" in names and "drops/take.bin" in names and not any("node_modules" in n for n in names)
              and r.getheader("Content-Disposition").startswith('attachment; filename="drops.zip"'), names)
        check("downloads stay inside the folder", admin.get("/api/files/download?" + urllib.parse.urlencode({"ws": ws, "path": "../"}))[0] == 403
              and bob.get("/api/files/download?" + urllib.parse.urlencode({"ws": ws, "path": "drops/take.bin"}))[0] in (403, 404))
        s, d, _ = admin.post("/api/files/zip", {"ws": ws, "paths": ["drops/take.bin", "drops/deep/ψ.txt", "drops/deep"]})
        check("several files make one download ticket", s == 200 and d["url"].startswith("/api/files/zip?token="), d)
        check("the ticket is the user's own", bob.get(d["url"])[0] == 404)
        s, z, _ = admin.get(d["url"])
        zf = _zip.ZipFile(_io.BytesIO(z))
        check("the zip names files from their common folder, each once", sorted(zf.namelist()) == ["deep/ψ.txt", "take.bin"] and zf.read("deep/ψ.txt").decode() == "γεια", zf.namelist())
        check("a ticket works once", admin.get(d["url"])[0] == 404)
        check("a zip asks only for files in the folder", admin.post("/api/files/zip", {"ws": ws, "paths": ["../../etc"]})[0] == 403)
        check("a download is a GET a link can make, so it needs no header, but still the sign-in", Client(port).get("/api/files/download?" + urllib.parse.urlencode({"ws": ws, "path": "drops/take.bin"}))[0] == 401)
        os.makedirs(os.path.join(book, "moved"), exist_ok=True)
        open(os.path.join(book, "moved", "take.bin"), "w").write("there")
        s, d, _ = admin.post("/api/files/move", {"ws": ws, "paths": ["drops/take (2).bin", "drops/take.bin", "drops/deep"], "to": "moved"})
        check("moving reports a name already taken and moves the rest", s == 200 and d["taken"] == ["drops/take.bin"]
              and {m["to"] for m in d["moved"]} == {"moved/take (2).bin", "moved/deep"} and os.path.isfile(os.path.join(book, "moved", "deep", "ψ.txt")), d)
        s, d, _ = admin.post("/api/files/move", {"ws": ws, "paths": ["drops/take.bin"], "to": "moved", "conflict": "keep"})
        check("keep both moves it under a free name", d["moved"] == [{"from": "drops/take.bin", "to": "moved/take (3).bin"}], d)
        s, d, _ = admin.post("/api/files/move", {"ws": ws, "paths": ["moved"], "to": "moved/deep"})
        check("a folder cannot go inside itself", d["skipped"] and d["skipped"][0]["why"].startswith("a folder cannot") and os.path.isdir(os.path.join(book, "moved", "deep")), d)
        s, d, _ = admin.post("/api/files/move", {"ws": ws, "paths": ["moved/deep/ψ.txt"], "to": ""})
        check("files move up to the top of the project", d["moved"] == [{"from": "moved/deep/ψ.txt", "to": "ψ.txt"}], d)
        check("moves stay inside the folder", admin.post("/api/files/move", {"ws": ws, "paths": ["ψ.txt"], "to": "../.."})[0] == 403
              and admin.post("/api/files/move", {"ws": ws, "paths": ["../../etc/passwd"], "to": ""})[0] == 403)
        q = lambda **a: admin.get("/api/files/names?" + urllib.parse.urlencode({"ws": ws, **a}))
        s, d, _ = q(q="DEEP")
        check("names are found whatever their case, folders too", s == 200 and {"path": "moved/deep", "type": "dir"} in d["names"], d)
        check("match case finds only the same case", q(q="DEEP", case=1)[1]["names"] == [])
        check("a slash searches the whole path", {"path": "moved/deep", "type": "dir"} in q(q="moved/de")[1]["names"] and q(q="ved/de")[1]["names"])
        check("ignored folders are not searched", not any("node_modules" in n["path"] for n in q(q="big")[1]["names"]))
        check("a name search needs words", q(q=" ")[0] == 400)
        open(os.path.join(book, "moved", "deep", "take.bin"), "w").write("old")
        s, d, _ = admin.post("/api/files/move", {"ws": ws, "paths": ["moved/take.bin"], "to": "moved/deep", "conflict": "replace"})
        check("replace puts the file it replaces in the trash", d["moved"] and open(os.path.join(book, "moved", "deep", "take.bin")).read() == "there", d)
        for v in admin.get("/api/voices")[1]["voices"]:
            if v["id"] != vid:
                admin.post("/api/voices/delete", {"id": v["id"]})
        check("only an admin or its maker can delete a voice", admin.post("/api/voices/delete", {"id": vid})[0] == 200 and admin.get("/api/voices")[1]["voices"] == [])
    finally:
        engine.terminate()
        server.terminate()


def test_codex(tmp, gw_port):
    """Codex as the chat's agent, through tests/mock_codex.py speaking Codex's app-server protocol."""
    print("Codex as the chat's agent (a fake codex app-server)")
    folder = os.path.join(tmp, "codex")
    projects = os.path.join(folder, "projects")
    work = os.path.join(projects, "calc")
    os.makedirs(work)
    open(os.path.join(work, "calc.py"), "w").write("def add(a, b):\n    return a - b\n\nprint(add(2, 3))\n")
    log = os.path.join(folder, "codex.jsonl")
    os.environ["MOCK_CODEX_LOG"] = log            # the fake Codex is started by Studio, which passes its environment on
    os.environ["CODEX_HOME"] = os.path.join(folder, "codex-home")     # where the fake Codex saves its own pictures
    port = free_port()
    config = os.path.join(folder, "app", "studio.config.json")
    os.makedirs(os.path.dirname(config))
    json.dump({"host": "127.0.0.1", "port": port, "gateway": f"http://127.0.0.1:{gw_port}", "workspace_roots": [projects],
               "user_root": os.path.join(projects, "studio-users"), "default_model": "quartz:strata", "max_turns": 10, "command_timeout_s": 20,
               "codex": True, "codex_command": f"{sys.executable} {os.path.join(ROOT, 'tests', 'mock_codex.py')}", "mcp": True}, open(config, "w"))
    server = start_studio(folder, config, port)
    sent = lambda method: [json.loads(l) for l in open(log) if json.loads(l).get("method") == method] if os.path.exists(log) else []

    def chat(c, ws, sid, message, model="codex:mock-big", decide=None, during=None, **kw):
        """Sends a message and follows the stream: decide(event) answers approvals, during(run, event) acts on other events."""
        status, data, _ = c.post("/api/agent/send", {"session": sid, "ws": ws, "message": message, "model": model, **kw})
        assert status == 200, (status, data)
        run, events = data["run"], []
        for name, ev in c.sse(f"/api/agent/stream?run={run}", timeout=60):
            events.append(ev)
            if name == "approval" and decide:
                decision, extra = decide(ev)
                c.post("/api/agent/decision", {"run": run, "approval_id": ev["approval_id"], "decision": decision, **extra})
            elif during:
                during(run, ev)
        return events

    def text(events):
        return "".join(e.get("text", "") for e in events if e["type"] == "token")

    try:
        admin = Client(port)
        admin.post("/api/auth/setup", {"password": "admin password"})
        bob = Client(port)
        token = admin.post("/api/admin/invites", {})[1]["token"]
        bob.post("/api/auth/signup", {"token": token, "username": "bob", "password": "member password"})
        models = admin.get("/api/models")[1]["models"]
        codex = [m for m in models if m["kind"] == "codex"]
        check("admins see Codex's models in the picker, without its hidden ones", [m["name"] for m in codex] == ["codex:mock-big", "codex:mock-small"]
              and codex[0]["label"] == "Mock Big · Codex" and codex[0]["vision"] is True and codex[1]["vision"] is False, codex)
        check("other people do not", not any(m["kind"] == "codex" for m in bob.get("/api/models")[1]["models"]))
        ws = admin.post("/api/workspace/open", {"path": work})[1]["workspace"]["id"]
        admin.post("/api/workspace/sandbox", {"ws": ws, "enabled": False})
        sid = admin.post("/api/agent/sessions", {"ws": ws})[1]["id"]

        events = chat(admin, ws, sid, "hi there")
        session = json.load(open(os.path.join(folder, "app", "data", "sessions", sid + ".json")))
        check("a message goes to Codex and its reply streams into the chat", "Hello from mock Codex" in text(events)
              and events[-1]["type"] == "done" and events[-1]["reason"] == "answered", [e["type"] for e in events][-6:])
        start = sent("thread/start")
        check("Codex starts in the folder, asking before commands and edits, inside its sandbox", start and start[-1]["params"]["cwd"] == os.path.realpath(work)
              and start[-1]["params"]["approvalPolicy"] == "untrusted" and start[-1]["params"]["sandbox"] == "workspace-write"
              and start[-1]["params"]["model"] == "mock-big", start[-1:])
        live = [e for e in events if e["type"] == "codex_usage"]
        check("Codex's usage updates arrive live during a run, merged with the last read", live and live[0]["usage"]["windows"][0]["used"] == 13, live)
        check("and are not kept in the chat", not any(e.get("type") == "codex_usage" for e in json.load(open(os.path.join(folder, "app", "data", "sessions", sid + ".json")))["events"]))
        usage = admin.get("/api/codex/usage?fresh=1")[1]
        check("admins read Codex's usage: the 5-hour window and the week, with resets", [(w["label"], w["used"]) for w in usage["windows"]] == [("5-hour window", 12), ("This week", 40)]
              and usage["plan"] == "plus" and usage["reset_credits"] == 2 and usage["windows"][0]["resets_at"] > time.time(), usage)
        check("other people cannot", bob.get("/api/codex/usage")[0] == 403)
        acct = admin.get("/api/codex/account")[1]
        check("admins see which account Codex uses", acct["account"] == {"type": "chatgpt", "email": "tester@example.com", "plan": "plus", "workspace": None}, acct)
        check("Codex can be signed out", admin.post("/api/codex/logout", {})[0] == 200 and admin.get("/api/codex/account")[1]["account"] is None)
        st = admin.post("/api/codex/login", {"method": "device"})[1]
        check("signing in with a code shows Codex's link and one-time code", st["state"] == "waiting" and st["url"] == "https://auth.example/device" and st["code"] == "ABCD-1234", st)
        check("and finishes when the person has signed in", wait_until(lambda: admin.get("/api/codex/login")[1]["state"] == "done", 10)
              and admin.get("/api/codex/login")[1].get("email") == "tester@example.com" and admin.get("/api/codex/account")[1]["account"])
        admin.post("/api/codex/logout", {})
        st = admin.post("/api/codex/login", {"method": "link"})[1]
        redirect = urllib.parse.parse_qs(urllib.parse.urlsplit(st["url"]).query)["redirect_uri"][0]
        check("a sign-in link refuses an address that is not its own last page", admin.post("/api/codex/login/finish", {"url": "http://evil.example/auth/callback?code=good"})[0] == 400
              and admin.post("/api/codex/login/finish", {"url": "http://localhost:1/auth/callback?code=good"})[0] == 400)
        status, st, _ = admin.post("/api/codex/login/finish", {"url": redirect + "?code=good&state=x"})
        check("and finishes with the address the browser ended on, opened on the server", status == 200 and st["state"] == "done" and admin.get("/api/codex/account")[1]["account"], st)
        admin.post("/api/codex/logout", {})
        check("an API key signs in at once and is not kept by Studio", admin.post("/api/codex/login", {"method": "apikey", "api_key": "sk-test-" + "x" * 30})[1]["state"] == "done"
              and "sk-test" not in open(os.path.join(folder, "app", "studio.config.json")).read() and admin.get("/api/codex/account")[1]["account"])
        check("a key that is not one is refused", admin.post("/api/codex/login", {"method": "apikey", "api_key": "short"})[0] == 400)
        check("people who are not admins cannot sign Codex in or out", bob.post("/api/codex/logout", {})[0] == 403 and bob.post("/api/codex/login", {"method": "device"})[0] == 403)
        check("the chat keeps Codex's conversation id and its reply", session.get("codex_thread", "").startswith("thread-")
              and session["messages"][-1] == {"role": "assistant", "content": "Hello from mock Codex"}, session.get("messages"))
        check("Codex models carry their effort levels and default", codex[0]["efforts"][-1]["value"] == "ultra" and codex[0]["default_effort"] == "low", codex[0])
        check("a chat can choose Codex's effort, up to ultra", admin.post("/api/agent/session/settings", {"ws": ws, "id": sid, "effort": "ultra"})[1]["effort"] == "ultra")
        events = chat(admin, ws, sid, "and again")
        check("and the turn asks Codex for it", sent("turn/start")[-1]["params"].get("effort") == "ultra", sent("turn/start")[-1]["params"])
        admin.post("/api/agent/session/settings", {"ws": ws, "id": sid, "effort": ""})
        resumed = sent("thread/resume")
        check("the next message reopens the same Codex conversation", resumed and resumed[-1]["params"]["threadId"] == session["codex_thread"]
              and len(sent("thread/start")) == 1 and "Hello from mock Codex" in text(events))
        turns = sent("turn/start")
        check("and sends only the new message", [i["text"] for i in turns[-1]["params"]["input"]] == ["and again"], turns[-1]["params"]["input"])

        asked = []

        def decide(ev):
            asked.append(ev)
            return "accept", {}
        events = chat(admin, ws, sid, "please edit calc.py", decide=decide)
        kinds_asked = [(a["kind"], a.get("command") or a.get("path")) for a in asked]
        check("a read-only command passes without asking, like a Strata model's", not any(k == "command" and "cat calc.py" in (w or "") for k, w in kinds_asked)
              and any(e["type"] == "tool_call" and e["args"].get("command") == "cat calc.py" for e in events), kinds_asked)
        edit = next((a for a in asked if a["kind"] == "edit"), None)
        check("Codex's patch is an edit card with the files' text before and after", edit and edit["path"].startswith("2 files")
              and "return a + b" in edit["new"] and "return a - b" in edit["old"] and "fixed" in edit["new"] and edit["change"]["added"] >= 2, edit)
        check("a command that may change things is a command card", ("command", "python3 calc.py") in kinds_asked, kinds_asked)
        changed = [e for e in events if e["type"] == "file_changed"]
        check("each changed file gets a card with an undo point", sorted((e["path"], e["action"]) for e in changed) == [("NOTES.md", "create"), ("calc.py", "edit")]
              and all(e.get("checkpoint") for e in changed) and "a + b" in open(os.path.join(work, "calc.py")).read(), changed)
        check("its plan, commands and token use show as usual", any(e["type"] == "plan" and e["steps"][0] == "Read calc.py" for e in events)
              and any(e["type"] == "tool_result" and e["name"] == "run" and e["output"] == "5\n" for e in events)
              and any(e["type"] == "usage" and e["prompt_tokens"] == 500 and e["context_tokens"] == 272000 for e in events)
              and "Patch completed; the run was allowed." in text(events))
        check("and the run ends with the files card", any(e["type"] == "files" for e in events), [e["type"] for e in events][-5:])
        check("Codex's current plan step moves the plan card", any(e["type"] == "tool_call" and e["args"].get("progress") == "3/3" for e in events))
        calc_undo = next(e["checkpoint"] for e in changed if e["path"] == "calc.py")
        notes_undo = next(e["checkpoint"] for e in changed if e["path"] == "NOTES.md")
        admin.post("/api/agent/undo", {"ws": ws, "checkpoint": calc_undo})
        admin.post("/api/agent/undo", {"ws": ws, "checkpoint": notes_undo})
        check("Undo puts Codex's edits back", "a - b" in open(os.path.join(work, "calc.py")).read() and not os.path.exists(os.path.join(work, "NOTES.md")))

        events = chat(admin, ws, sid, "edit it again", decide=lambda ev: ("reject", {}) if ev["kind"] == "edit" else ("accept", {}))
        check("a rejected patch is not applied, and Codex hears so", "a - b" in open(os.path.join(work, "calc.py")).read()
              and "Patch declined" in text(events) and not any(e["type"] == "file_changed" for e in events))
        events = chat(admin, ws, sid, "edit with auto", auto_apply=True, auto_commands=True, decide=lambda ev: ("reject", {}))
        check("with auto-apply and auto-run on, nothing asks", not any(e["type"] == "approval" for e in events) and "a + b" in open(os.path.join(work, "calc.py")).read()
              and "the run was allowed" in text(events))

        steered = []

        def steer(run, ev):
            if ev["type"] == "token" and not steered:
                steered.append(admin.post("/api/agent/guide", {"run": run, "message": "use blue"})[0])
        events = chat(admin, ws, sid, "slow job", during=steer)
        check("a message sent while Codex works reaches the running turn", steered == [200] and "Got: use blue" in text(events)
              and sent("turn/steer") and events[-1]["reason"] == "answered", (steered, text(events)))
        stopped = []

        def stop(run, ev):
            if ev["type"] == "token" and not stopped:
                stopped.append(admin.post("/api/agent/stop", {"run": run})[0])
        events = chat(admin, ws, sid, "slow job", during=stop)
        check("Stop interrupts Codex's turn", stopped == [200] and events[-1]["reason"] == "stopped" and sent("turn/interrupt"), events[-1])
        events = chat(admin, ws, sid, "ask me", decide=lambda ev: ("accept", {"answers": [{"choices": ["Blue"], "text": ""}]}))
        question = next((e for e in events if e["type"] == "approval"), {})
        check("Codex's questions use the question card", question.get("kind") == "question" and question["questions"][0]["options"][1]["label"] == "Blue"
              and "You picked Blue" in text(events), question)
        events = chat(admin, ws, sid, "fail please")
        check("a failed turn shows Codex's reason", any(e["type"] == "error" and "usage limit reached" in e["text"] for e in events) and events[-1]["reason"] == "error")

        sid2 = admin.post("/api/agent/sessions", {"ws": ws})[1]["id"]
        run_agent(admin, ws, sid2, "Hello")                     # the mock gateway's model answers first
        events = chat(admin, ws, sid2, "your turn")
        check("Codex joining a chat gets what was said before", "(I know the earlier chat)" in text(events), text(events))
        json.dump({}, open(log + ".threads", "w"))             # Codex lost its conversations
        events = chat(admin, ws, sid2, "still there?")
        check("a conversation Codex cannot reopen starts again with the chat so far", "(I know the earlier chat)" in text(events)
              and any("could not reopen" in (e.get("text") or "") for e in events if e["type"] == "status"))

        psid = admin.post("/api/agent/sessions", {"ws": ws})[1]["id"]
        events = chat(admin, ws, psid, "please draw a lighthouse")
        pics = [e for e in events if e["type"] == "tool_picture"]
        got = admin.get(f"/api/agent/attachment?session={psid}&id={pics[0]['images'][0]['id']}") if pics else (0, None)
        check("a picture Codex makes with its own image tool is kept and shown in the chat, with its prompt", len(pics) == 1
              and pics[0]["server"] == "codex" and pics[0]["prompt"] == "A red lighthouse at dusk" and got[0] == 200
              and any(e["type"] == "tool_result" and e.get("ok") and "shown in the chat" in e.get("summary", "") for e in events), (pics, got[0]))
        check("but only from Codex's own picture folder", not any("hostname" in json.dumps(e) and e["type"] == "tool_picture" for e in events))
        gsid = admin.post("/api/agent/sessions", {"ws": ws})[1]["id"]
        events = chat(admin, ws, gsid, "Codex goal with two steps", goal=True, goal_rounds=5)
        goals = [e for e in events if e["type"] == "goal"]
        stored = json.load(open(os.path.join(folder, "app", "data", "sessions", gsid + ".json")))
        check("a goal with Codex goes round until its reply starts with GOAL COMPLETE", [g["state"] for g in goals] == ["active", "done"]
              and goals[0]["rounds"] == 1 and "GOAL COMPLETE" in text(events)
              and "give a final reply that starts with GOAL COMPLETE" in stored["messages"][0]["content"]
              and "call finish" not in stored["messages"][0]["content"], (goals, text(events)[-200:]))
        bsid2 = admin.post("/api/agent/sessions", {"ws": ws})[1]["id"]
        events = chat(admin, ws, bsid2, "Codex budget: keep going", goal=True, goal_rounds=10, goal_codex_tokens=4)
        goals = [e for e in events if e["type"] == "goal"]
        check("a Codex allowance pauses the goal at the end of the round that passes it", goals and goals[-1]["state"] == "paused"
              and goals[-1]["codex_used"] == 5000 and goals[-1]["codex_tokens"] == 4000 and "5K of 4K new tokens" in goals[-1]["why"], goals)
        check("an allowance out of range is refused", admin.post("/api/agent/goal", {"ws": ws, "id": bsid2, "action": "edit", "text": "x", "goal_codex_tokens": 0})[0] == 400)
        resumed = admin.post("/api/agent/goal", {"ws": ws, "id": bsid2, "action": "resume"})[1]["goal"]
        check("and Resume grants it again", resumed["state"] == "active" and resumed["codex_used"] == 0 and resumed["codex_tokens"] == 4000, resumed)
        # Real Codex does a whole goal in one turn, so the allowance is checked during the turn too.
        lsid = admin.post("/api/agent/sessions", {"ws": ws})[1]["id"]
        events = chat(admin, ws, lsid, "Codex long: twenty questions", goal=True, goal_rounds=10, goal_codex_tokens=10)
        goals = [e for e in events if e["type"] == "goal"]
        runs = [e for e in events if e["type"] == "tool_call" and e["name"] == "run"]
        said = [e.get("text") or "" for e in events if e["type"] == "status"]
        check("an allowance that runs out inside a turn asks Codex to stop at a good point", any("Codex is asked to stop at a good point" in t for t in said)
              and "questions 1-3 are in quiz.html" in text(events), said)
        check("and the goal pauses after its summary, without another round", goals and goals[-1]["state"] == "paused" and goals[-1]["rounds"] == 0
              and "10K of 10K new tokens" in goals[-1]["why"] and len(runs) <= 4, (goals[-1:], len(runs)))
        ssid = admin.post("/api/agent/sessions", {"ws": ws})[1]["id"]
        events = chat(admin, ws, ssid, "Codex long stubborn: twenty questions", goal=True, goal_rounds=10, goal_codex_tokens=10)
        goals = [e for e in events if e["type"] == "goal"]
        runs = [e for e in events if e["type"] == "tool_call" and e["name"] == "run"]
        done = [e for e in events if e["type"] == "done"]
        said = [e.get("text") or "" for e in events if e["type"] == "status"]
        check("Codex that carries on is stopped at its second call after the note", any("it started another call" in t for t in said)
              and 4 <= len(runs) <= 6 and "Done without being stopped" not in text(events), (len(runs), said[-3:]))
        check("and that pauses the goal, not a stop by the person", goals and goals[-1]["state"] == "paused" and "allowance" in goals[-1]["why"]
              and done and done[-1].get("reason") != "stopped", (goals[-1:], done[-1:]))
        check("/compact, tasks and Time-skip stay with Studio's own models", admin.post("/api/agent/compact", {"ws": ws, "session": sid, "model": "codex:mock-big"})[0] == 400
              and admin.post("/api/tasks/save", {"ws": ws, "task": {"title": "t", "prompt": "p", "mode": "ask", "model": "codex:mock-big", "schedule": {"every_min": 60}}})[0] == 400)
        bws = bob.post("/api/workspace/open", {"path": ""})
        bob_ws = bws[1]["workspace"]["id"] if bws[0] == 200 else None
        if bob_ws:
            bsid = bob.post("/api/agent/sessions", {"ws": bob_ws})[1]["id"]
            check("only admins may use Codex", bob.post("/api/agent/send", {"session": bsid, "ws": bob_ws, "message": "hi", "model": "codex:mock-big"})[0] == 403)
        admin.post("/api/workspace/sandbox", {"ws": ws, "enabled": True})
        status, body, _ = admin.post("/api/agent/send", {"session": sid, "ws": ws, "message": "hi", "model": "codex:mock-big"})
        check("and only in folders with the Sandbox off", status == 400 and "Sandbox off" in body.get("error", ""), body)
        status, body, _ = admin.post("/api/agent/send", {"session": sid, "ws": ws, "message": "hi", "model": "codex:nope"})
        admin.post("/api/workspace/sandbox", {"ws": ws, "enabled": False})
        status, body, _ = admin.post("/api/agent/send", {"session": sid, "ws": ws, "message": "hi", "model": "codex:nope"})
        check("a model Codex does not offer is refused", status == 400 and "does not offer" in body.get("error", ""), body)

        # Studio's tool servers reach Codex through one bridge server of its own, which hands each call back to the run.
        mock = [sys.executable, os.path.join(ROOT, "tests", "mock_mcp.py")]
        admin.post("/api/admin/mcp", {"servers": [{"name": "mock", "command": mock[0], "args": mock[1:], "approval": "with_commands"},
                                                 {"name": "blender", "command": mock[0], "args": mock[1:], "approval": "with_commands"}]})
        wait_until(lambda: all(x.get("state") == "ready" for x in admin.get("/api/admin/mcp")[1]["servers"]), 20)
        sid3 = admin.post("/api/agent/sessions", {"ws": ws})[1]["id"]
        asked = []
        events = chat(admin, ws, sid3, "use the tools", decide=lambda ev: (asked.append(ev) or ("accept", {})))
        reply = text(events)
        check("Codex gets Studio's tools through the bridge, without their secrets", "mock__echo" in reply and "blender__picture" in reply and "save_picture" in reply
              and "view_page" in reply and "open_preview" in reply,
              reply)
        start = sent("thread/start")[-1]["params"]
        bridge = (start.get("config") or {}).get("mcp_servers", {}).get("studio", {})
        check("the bridge is one server, `studio`, with a one-time token and room for long calls",
              bridge.get("args", [""])[0].endswith("studio_mcp_bridge.py") and len(bridge.get("env", {}).get("STUDIO_BRIDGE_TOKEN", "")) > 20
              and bridge.get("tool_timeout_sec", 0) >= 120 and "MOCK" not in json.dumps(bridge), bridge)
        check("each call asks in Studio as the server is set to, and runs there", [a.get("tool") for a in asked] == ["echo", "picture"]
              and "mock__echo -> text:echo: via bridge" in reply, ([a.get("tool") for a in asked], reply))
        check("a picture comes back to Codex to look at, and shows in the chat", "image:image/png" in reply and any(e["type"] == "tool_picture" for e in events))
        check("the calls show as Studio's own tool rows, once", [e["name"] for e in events if e["type"] == "tool_call"] == ["mcp__mock__echo", "mcp__mock__picture"])
        check("Studio's tools are named in Codex's instructions", "studio` tool server" in (start.get("developerInstructions") or ""))
        c_post_json(gw_port, "/_mock/card", {"on": True, "running": True, "busy": 0, "stops": 0})
        events = chat(admin, ws, sid3, "use the tools on mock", auto_commands=True)
        check("tools that do not use the graphics card leave Strata loaded", c_post_json(gw_port, "/_mock/card", {})["stops"] == 0)
        events = chat(admin, ws, sid3, "use the tools on blender", auto_commands=True)
        statuses = [e.get("text") or "" for e in events if e["type"] == "status"]
        check("before Codex's first call to a card-hungry tool, an idle Strata is unloaded for the tools", c_post_json(gw_port, "/_mock/card", {})["stops"] == 1
              and any("whole graphics card" in t for t in statuses), statuses)
        c_post_json(gw_port, "/_mock/card", {"running": True, "busy": 2})
        events = chat(admin, ws, sid3, "use the tools on blender", auto_commands=True)
        check("but not while Strata is answering someone", c_post_json(gw_port, "/_mock/card", {})["stops"] == 1)
        c_post_json(gw_port, "/_mock/card", {"on": False, "busy": 0})
        status, _, _ = admin.get("/api/codex/bridge/tools", headers={"X-Studio-Bridge": "made-up"})
        check("the bridge answers only a running Codex chat's token", status == 403)

        asked = []
        events = chat(admin, ws, sid3, "helpers please", decide=lambda ev: (asked.append(ev) or ("accept", {})))
        check("Codex's sub-agents show as rows, and their commands ask like the main agent's",
              any(e["type"] == "tool_call" and e["name"] == "codex_agent" and e["args"].get("task") == "count the files" for e in events)
              and any(e["type"] == "tool_result" and e["name"] == "codex_agent" and "3 files" in e["output"] for e in events)
              and [a.get("command") for a in asked] == ["touch counted.txt"] and "SECRET" not in text(events)
              and "The helper counted 3 files; its command was allowed." in text(events), (text(events), asked))
    finally:
        os.environ.pop("MOCK_CODEX_LOG", None)
        server.terminate()


def test_pc_folders(tmp, gw_port):
    """A folder on a person's own computer, through the real studio-helper (tools/helper/dist), with Studio over plain HTTP."""
    print("folders on a person's computer")
    binary = os.path.join(ROOT, "tools", "helper", "dist", "studio-helper")
    if not os.path.isfile(binary):
        check("(studio-helper is not built: run tools/helper/build.sh; these checks were skipped)", True)
        return
    folder = os.path.join(tmp, "pc")
    projects = os.path.join(folder, "projects")
    os.makedirs(projects)
    port = free_port()
    config = os.path.join(folder, "app", "studio.config.json")
    os.makedirs(os.path.dirname(config))
    json.dump({"host": "127.0.0.1", "port": port, "gateway": f"http://127.0.0.1:{gw_port}", "workspace_roots": [projects],
               "user_root": os.path.join(projects, "studio-users"), "default_model": "quartz:strata", "max_turns": 10, "command_timeout_s": 20}, open(config, "w"))
    server = start_studio(folder, config, port)
    pc = os.path.join(folder, "their-pc", "Homework")
    os.makedirs(os.path.join(pc, "sub"))
    os.makedirs(os.path.join(pc, "node_modules", "lib"))
    for rel, text in (("a.txt", "one"), ("sub/b.py", "print('b')\n"), ("node_modules/lib/x.js", "skip me"), ("Γράμμα.txt", "καλημέρα")):
        with open(os.path.join(pc, rel), "w", encoding="utf-8") as f:
            f.write(text)
    home = os.path.join(folder, "helper-home")
    env = {**os.environ, "STUDIO_HELPER_HOME": home}
    helper = None

    def until(fn, timeout=15):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if fn():
                return True
            time.sleep(0.2)
        return False
    try:
        admin = Client(port)
        admin.post("/api/auth/setup", {"password": "admin password"})
        alice = Client(port)
        token = admin.post("/api/admin/invites", {})[1]["token"]
        alice.post("/api/auth/signup", {"token": token, "username": "alice", "password": "member password"})
        check("a user may not pair a computer until an admin allows it", alice.post("/api/helpers/pair", {})[0] == 403 and alice.get("/api/helpers")[1]["allowed"] is False)
        check("users cannot allow themselves", alice.post("/api/admin/users/update", {"username": "alice", "pc_folders": True})[0] == 403)
        # The Graphics card right (2026-11-17): a user may choose how the card is shared once an admin allows it.
        check("a user cannot see or change the graphics card setting without the right", alice.get("/api/card")[0] == 403
              and alice.post("/api/card", {"mode": "tools"})[0] == 403 and alice.post("/api/card/restart", {})[0] == 403
              and alice.get("/api/account")[1]["card_allowed"] is False)
        check("the right is true or false", admin.post("/api/admin/users/update", {"username": "alice", "card": "yes"})[0] == 400)
        s, d, _ = admin.post("/api/admin/users/update", {"username": "alice", "card": True})
        check("an admin gives the graphics card right per user", s == 200 and d["user"]["card"] is True and alice.get("/api/account")[1]["card_allowed"] is True, d)
        check("with it the user reaches the setting", alice.get("/api/card")[0] != 403 and alice.post("/api/card", {"mode": "both"})[0] == 400)
        admin.post("/api/admin/users/update", {"username": "alice", "card": False})
        check("and loses it when it is taken away", alice.get("/api/card")[0] == 403)
        s, d, _ = admin.post("/api/admin/users/update", {"username": "alice", "pc_folders": True})
        check("an admin allows it per user", s == 200 and d["user"]["pc_folders"] is True, d)
        code = alice.post("/api/helpers/pair", {})[1]["code"]
        check("a pairing code is eight letters and digits", re.fullmatch(r"[A-Z2-9]{8}", code), code)
        pair = lambda c: subprocess.run([binary, "pair", "-server", f"http://127.0.0.1:{port}", "-code", c, "-folder", pc], env=env, capture_output=True, text=True, timeout=60)
        wrong = pair("WRONGCOD")
        check("a wrong code is refused", wrong.returncode != 0 and "wrong or has expired" in wrong.stdout, wrong.stdout[-300:])
        done = pair(code)
        check("the helper pairs with the code", done.returncode == 0 and "Paired" in done.stdout, done.stdout[-300:] + done.stderr[-300:])
        check("a code works once", pair(code).returncode != 0)
        saved = json.load(open(os.path.join(home, "config.json")))["connections"][0]
        helper = subprocess.Popen([binary, "run"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        listed = lambda: alice.get("/api/helpers")[1]["helpers"]
        check("Studio sees the computer online", until(lambda: listed() and listed()[0]["online"]), listed())
        mirror = listed()[0]["path"]
        latest = open(os.path.join(ROOT, "tools", "helper", "VERSION")).read().strip()
        check("Studio knows which helper version the computer runs, and that it is current", listed()[0]["version"] == latest and listed()[0]["outdated"] is False
              and alice.get("/api/helpers")[1]["latest"] == latest, listed()[0])
        import studio as sm
        check("an older or unknown helper is called outdated", sm.helper_older("0.03", "0.04") and sm.helper_older("", "0.04") and not sm.helper_older("0.10", "0.04") and not sm.helper_older("0.04", "0.04"))
        check("its copy is named after the folder and the computer", os.path.basename(mirror).startswith("Homework@"), mirror)
        ws = alice.post("/api/workspace/open", {"path": mirror})[1]["workspace"]
        check("the folder says which computer it is on, and is not sandboxed", ws["helper"]["machine"] and ws["helper"]["shell"] == "bash" and ws["sandbox"] is False, ws)
        check("the files arrive", until(lambda: os.path.exists(os.path.join(mirror, "sub", "b.py")) and os.path.exists(os.path.join(mirror, "Γράμμα.txt"))))
        check("folders like node_modules stay on the computer", not os.path.exists(os.path.join(mirror, "node_modules")))
        check("Studio records when the computer last synced", until(lambda: json.load(open(os.path.join(folder, "app", "data", "helpers.json")))[saved["id"]].get("last_sync")))
        alice.post("/api/files/write", {"ws": ws["id"], "path": "a.txt", "content": "two"})
        check("an edit in Studio reaches the computer", until(lambda: open(os.path.join(pc, "a.txt")).read() == "two"))
        with open(os.path.join(pc, "c.txt"), "w") as f:
            f.write("made there")
        check("a file made on the computer comes to Studio", until(lambda: os.path.exists(os.path.join(mirror, "c.txt"))))
        session = alice.post("/api/agent/sessions", {"ws": ws["id"]})[1]["id"]
        alice.post("/api/files/write", {"ws": ws["id"], "path": "a.txt", "content": "three"})
        s, d, _ = alice.post("/api/command/run", {"ws": ws["id"], "session": session, "command": "cat a.txt; echo; pwd; echo made > made.txt"})
        check("a command runs on the computer, in the folder, after Studio's edit", s == 200 and d["ok"] and "three" in d["output"] and pc in d["output"], d)
        check("and the files it makes are here when it answers", os.path.exists(os.path.join(mirror, "made.txt")))
        s, d, _ = alice.post("/api/command/run", {"ws": ws["id"], "session": session, "command": "false | cat"})
        check("a failure hidden in a pipe is still named", not d["ok"] and "exit 0, but" in d["summary"], d)
        os.remove(os.path.join(pc, "c.txt"))
        check("a file deleted there goes here", until(lambda: not os.path.exists(os.path.join(mirror, "c.txt"))))
        alice.post("/api/files/delete", {"ws": ws["id"], "path": "made.txt"})
        check("a file deleted in Studio goes there, while the trash stays in Studio", until(lambda: not os.path.exists(os.path.join(pc, "made.txt"))) and not os.path.exists(os.path.join(pc, ".strata-studio")))
        term = alice.post("/api/term/open", {"ws": ws["id"], "cols": 80, "rows": 24})[1]["id"]
        alice.post("/api/term/input", {"id": term, "data": "echo term-$((6*7))\n"})
        seen = []

        def term_said():
            got = alice.get(f"/api/term/poll?id={term}&sent=0")[1]
            seen[:] = [base64.b64decode(e["data"]["b64"]).decode("utf-8", "replace") for e in got["events"] if e["type"] == "data"]
            return "term-42" in "".join(seen)
        check("a terminal runs on the computer", until(term_said), "".join(seen)[-300:])
        alice.post("/api/term/close", {"id": term})

        # Web servers on the computer (helper 0.06): Studio lists the ones its commands started and relays their pages.
        with open(os.path.join(pc, "page.html"), "w") as f:
            f.write("<html><head><title>PC page</title></head><body>hello from the computer</body></html>")
        web, other, held = free_port(), free_port(), free_port()
        started = time.time()
        s, d, _ = alice.post("/api/command/run", {"ws": ws["id"], "session": session,
                                                  "command": f"python3 -m http.server {web} --bind 127.0.0.1 >/dev/null 2>&1 & python3 -m http.server {held} --bind 127.0.0.1 & echo started"})
        check("a command that leaves a server running in the background returns at once, even when the server keeps its output",
              s == 200 and "started" in d.get("output", "") and time.time() - started < 20, (s, d.get("summary"), round(time.time() - started, 1)))
        outsider = subprocess.Popen([sys.executable, "-m", "http.server", str(other), "--bind", "127.0.0.1"], cwd=tmp, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            listed_ports = []

            def ports_seen():
                got = alice.get(f"/api/live/ports?ws={ws['id']}")
                listed_ports[:] = got[1].get("ports", []) if got[0] == 200 else [got]
                return web in listed_ports
            check("Studio lists the computer's server that a Studio command started", until(ports_seen), listed_ports)
            check("but not a server started outside Studio on that computer", other not in listed_ports, listed_ports)
            check("and opening that one is refused", alice.post("/api/live/open", {"ws": ws["id"], "port": other})[0] == 403)
            s, d, _ = alice.post("/api/live/open", {"ws": ws["id"], "port": web})
            check("the live address opens for the computer's server", s == 200 and d["url"].startswith("/live/"), d)
            page = urllib.request.urlopen(f"http://127.0.0.1:{port}{d['url']}page.html", timeout=30).read().decode()
            check("and its page arrives through the helper, rewritten like any live page", "hello from the computer" in page and "<title>PC page</title>" in page, page[:300])
            missing = urllib.request.Request(f"http://127.0.0.1:{port}{d['url']}nothing-here.html")
            try:
                urllib.request.urlopen(missing, timeout=30)
                code = 200
            except urllib.error.HTTPError as e:
                code = e.code
            check("the server's own errors come through as they are", code == 404, code)
        finally:
            outsider.kill()
            alice.post("/api/command/run", {"ws": ws["id"], "session": session, "command": f"pkill -f 'http.server {web}'; pkill -f 'http.server {held}'; true"})
        key = {"X-Helper-Key": saved["key"]}
        for bad in ("../outside.txt", "sub/../../outside.txt", "/etc/x", "C:/x", ".git/index.lock", "node_modules/y.js", "a:b"):
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/helper/sync", data=json.dumps({"changes": {bad: {"b64": "eA=="}}}).encode(),
                                         headers={"Content-Type": "application/json", "X-Studio": "1", **key}, method="POST")
            urllib.request.urlopen(req, timeout=10).read()
        check("paths from a helper cannot leave the copy or reach skipped folders", not os.path.exists(os.path.join(os.path.dirname(mirror), "outside.txt"))
              and not os.path.exists(os.path.join(mirror, "node_modules")) and not os.path.exists(os.path.join(mirror, ".git")))
        bad_key = urllib.request.Request(f"http://127.0.0.1:{port}/api/helper/poll?wait=0", headers={"X-Studio": "1", "X-Helper-Key": "x" * 40})
        try:
            urllib.request.urlopen(bad_key, timeout=10)
            refused = False
        except urllib.error.HTTPError as e:
            refused = e.code == 401
        check("a wrong helper key is refused", refused)
        check("someone else cannot remove the computer", Client(port).post("/api/helpers/remove", {"id": saved["id"]})[0] == 401)
        dl = alice.get("/api/helpers/download?os=linux")
        check("the helper can be downloaded from Studio", dl[0] == 200 and len(dl[1]) > 1000000)
        check("removing the computer in Studio stops its helper", alice.post("/api/helpers/remove", {"id": saved["id"]})[0] == 200 and until(lambda: helper.poll() is not None, 20))
        ws2 = alice.post("/api/workspace/open", {"path": mirror})[1]["workspace"]
        check("its copy stays, as an ordinary sandboxed folder", ws2["helper"] is None and ws2["sandbox"] is True and os.path.exists(os.path.join(mirror, "a.txt")), ws2)
        check("and says where it came from: a copy kept from that computer", ws2["place"]["kind"] == "kept" and ws2["place"]["machine"], ws2.get("place"))
        kept = alice.get("/api/helpers")[1].get("kept", [])
        check("My computers lists such copies apart from the connected folders", [k["path"] for k in kept] == [os.path.realpath(mirror)], kept)
        listing = alice.get("/api/workspaces/browse?path=" + urllib.parse.quote(os.path.dirname(os.path.realpath(mirror))))[1]["dirs"]
        check("and the folder list marks it", any(x["path"] == os.path.realpath(mirror) and x.get("kind") == "kept" for x in listing), listing)
        # Disconnect in the helper itself (0.07): Studio is told, drops the connection and keeps its copy.
        code = alice.post("/api/helpers/pair", {})[1]["code"]
        pair(code)
        before = {h["id"] for h in alice.get("/api/helpers")[1]["helpers"]}
        saved_n = len(subprocess.run([binary, "list"], env=env, capture_output=True, text=True, timeout=30).stdout.strip().splitlines())
        gone = subprocess.run([binary, "remove", str(saved_n)], env=env, capture_output=True, text=True, timeout=60)
        check("Disconnect in the helper tells Studio, which drops the folder from My computers", "Studio was told" in gone.stdout
              and not ({h["id"] for h in alice.get("/api/helpers")[1]["helpers"]} & before), gone.stdout[-300:])
        kept_now = alice.get("/api/helpers")[1]["kept"]
        check("and keeps its copy here as a kept copy (pairing the same folder again reuses it)", len(kept_now) == 1 and os.path.isdir(kept_now[0]["path"]), kept_now)
        newest = kept_now[0]["path"]
        check("a kept copy can be deleted from the server", alice.post("/api/folders/delete_copy", {"path": newest})[0] == 200
              and not os.path.exists(newest) and alice.get("/api/helpers")[1]["kept"] == [])
        binned = os.listdir(os.path.join(folder, "app", "data", "deleted_folders", "alice"))
        check("into a bin it can be brought back from", len(binned) == 1 and os.path.basename(newest) in binned[0], binned)
        check("an ordinary folder is not deleted this way", alice.post("/api/folders/delete_copy", {"path": os.path.join(projects, "studio-users", "alice")})[0] in (400, 403))
        code = alice.post("/api/helpers/pair", {})[1]["code"]
        pair(code)
        hid = [h["id"] for h in alice.get("/api/helpers")[1]["helpers"]][0]
        third = [h["path"] for h in alice.get("/api/helpers")[1]["helpers"]][0]
        check("disconnecting in Studio can delete the copy too", alice.post("/api/helpers/remove", {"id": hid, "delete_copy": True})[1].get("deleted") is True
              and not os.path.exists(third) and alice.get("/api/helpers")[1]["helpers"] == [])
        code = alice.post("/api/helpers/pair", {})[1]["code"]
        pair(code)
        check("taking the permission away removes the user's computers", admin.post("/api/admin/users/update", {"username": "alice", "pc_folders": False})[0] == 200
              and alice.get("/api/helpers")[1]["helpers"] == [] and alice.post("/api/helpers/pair", {})[0] == 403)
    finally:
        if helper and helper.poll() is None:
            helper.kill()
        server.terminate()


def test_waiting_room(tmp, gw_port):
    print("waiting room")
    folder = os.path.join(tmp, "room")
    projects = os.path.join(folder, "projects")
    os.makedirs(os.path.join(projects, "admin-project"))
    port = free_port()
    config = os.path.join(folder, "app", "studio.config.json")
    os.makedirs(os.path.dirname(config))
    json.dump({"host": "127.0.0.1", "port": port, "gateway": f"http://127.0.0.1:{gw_port}", "workspace_roots": [projects],
               "user_root": os.path.join(projects, "studio-users"), "default_model": "quartz:strata", "max_turns": 10, "command_timeout_s": 20,
               "queue_idle_s": 3, "queue_max_s": 12, "queue_offer_s": 2, "queue_warn_s": 1, "queue_presence_s": 60}, open(config, "w"))
    server = start_studio(folder, config, port)
    try:
        admin = Client(port)
        admin.post("/api/auth/setup", {"password": "admin password"})

        def member(name, ip):
            client = Client(port)
            client.ip = ip
            token = admin.post("/api/admin/invites", {})[1]["token"]
            client.post("/api/auth/signup", {"token": token, "username": name, "password": "member password"})
            home = client.get("/api/config")[1]["workspace_roots"][0]
            ws = client.post("/api/workspace/open", {"path": home})[1]["workspace"]["id"]
            client.ws, client.sess = ws, client.post("/api/agent/sessions", {"ws": ws})[1]["id"]
            return client

        alice, bob, carol = member("alice", "10.1.0.1"), member("bob", "10.1.0.2"), member("carol", "10.1.0.3")

        def send(client, message="hello"):
            return client.post("/api/agent/send", {"session": client.sess, "ws": client.ws, "message": message, "model": "quartz:strata"})

        def state(client):
            return client.get("/api/queue/state")[1]

        def until(fn, timeout=10):
            deadline = time.time() + timeout
            while time.time() < deadline:
                if fn():
                    return True
                time.sleep(0.1)
            return False

        def finish_run(client, run):
            for name, ev in client.sse(f"/api/agent/stream?run={run}"):
                if name == "approval":
                    client.post("/api/agent/decision", {"run": run, "approval_id": ev["approval_id"], "decision": "reject"})

        check("the seat starts free", state(alice)["free"] is True)
        s, d, _ = send(alice)
        check("sending takes a free seat", s == 200 and state(alice)["me"]["holds"] is True, d)
        finish_run(alice, d["run"])
        check("holder can send again", send(alice)[0] == 200)
        time.sleep(0.3)
        s, d, _ = send(bob)
        check("a second user is refused and told to wait", s == 423 and d.get("waiting_room") is True, d)
        check("they are put in line", state(bob)["me"]["position"] == 1)
        check("they can see who has the agent", state(bob)["holder"]["username"] == "alice")
        s, _, _ = bob.post("/api/files/create", {"ws": bob.ws, "path": "notes.txt", "type": "file"})
        check("waiting users can still use their files", s == 200, s)
        check("joining twice does not duplicate", bob.post("/api/queue/join", {})[0] == 200 and len(state(bob)["queue"]) == 1)
        check("not your turn yet", bob.post("/api/queue/accept", {})[0] == 409)
        check("only the holder can finish", bob.post("/api/queue/release", {})[0] == 409)
        s, _, _ = send(carol)
        check("line keeps its order", s == 423 and state(carol)["me"]["position"] == 2)
        for path, body in (("/api/admin/queue/end", {}), ("/api/admin/queue/remove", {"username": "carol"}), ("/api/admin/queue/move", {"username": "carol", "position": 0})):
            check(f"users cannot call {path}", bob.post(path, body)[0] == 403)
        check("admin can reorder", admin.post("/api/admin/queue/move", {"username": "carol", "position": 0})[0] == 200 and state(carol)["me"]["position"] == 1)
        admin.post("/api/admin/queue/move", {"username": "bob", "position": 0})
        check("reorder rejects bad positions", admin.post("/api/admin/queue/move", {"username": "bob", "position": "x"})[0] == 400)
        check("removing a stranger fails", admin.post("/api/admin/queue/remove", {"username": "nobody"})[0] == 404)

        alice.post("/api/queue/keep", {})
        check("finishing passes the seat on", alice.post("/api/queue/release", {})[0] == 200 and until(lambda: state(bob)["me"]["offered"]))
        s, _, _ = send(alice)
        check("nobody can jump the line during an offer", s == 423 and state(alice)["me"]["position"] == 3, state(alice))
        check("a skipped offer goes to the next person", bob.post("/api/queue/leave", {})[0] == 200 and until(lambda: state(carol)["me"]["offered"]))
        check("an unanswered offer expires", until(lambda: state(carol)["me"]["position"] is None) and until(lambda: state(alice)["me"]["offered"]))
        check("accepting takes the seat", alice.post("/api/queue/accept", {})[0] == 200 and state(alice)["me"]["holds"] is True)

        s, d, _ = send(alice, "Rename Sample")
        run = d["run"]
        check("cannot finish during a run", alice.post("/api/queue/release", {})[0] == 409)
        time.sleep(4)
        check("a run is not ended by the idle timer when nobody waits", state(alice)["me"]["holds"] is True)
        send(bob)
        check("an unanswered approval ends the turn when others wait", until(lambda: state(bob)["me"]["offered"], 8), state(bob))
        finish_run(alice, run)
        bob.post("/api/queue/accept", {})

        send(bob)
        time.sleep(0.3)
        send(carol)
        check("the holder is warned before an idle turn ends", until(lambda: state(bob)["me"]["warn"] == "idle", 6), state(bob))
        check("keep restarts the idle clock", bob.post("/api/queue/keep", {})[0] == 200 and state(bob)["me"]["idle_left"] >= 1)
        check("an idle turn passes on", until(lambda: state(carol)["me"]["offered"], 8))
        carol.post("/api/queue/accept", {})

        admin.post("/api/queue/join", {})
        check("admin can end a session", admin.post("/api/admin/queue/remove", {"username": "admin"})[0] == 200 and admin.post("/api/admin/queue/end", {})[0] == 200 and state(carol)["me"]["holds"] is False)
        check("ending when nobody holds it fails", admin.post("/api/admin/queue/end", {})[0] == 409)

        s, d, _ = send(alice, "Rename Sample")
        run = d["run"]
        time.sleep(0.5)
        check("admin ending a session stops the run", admin.post("/api/admin/queue/end", {})[0] == 200)
        finish_run(alice, run)
        check("the stopped run really ends", alice.get("/api/agent/active?ws=" + alice.ws)[1]["run"] is None)

        s, d, _ = send(alice)
        finish_run(alice, d["run"])
        check("seat holder disabled loses the seat", admin.post("/api/admin/users/update", {"username": "alice", "disabled": True})[0] == 200 and state(bob)["free"] is True)
        admin.post("/api/admin/users/update", {"username": "alice", "disabled": False})
        alice.post("/api/auth/login", {"username": "alice", "password": "member password"})

        import threading
        stream_events = []

        def listen():
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=8)
            conn.request("GET", "/api/queue/stream", headers={"Cookie": bob.cookie})
            resp = conn.getresponse()
            for raw in resp:
                line = raw.decode().strip()
                if line.startswith("data:") and "me" in json.loads(line[5:]):     # state events; the first answer also carries the chat
                    stream_events.append(json.loads(line[5:]))
                    if len(stream_events) >= 2:
                        break
            conn.close()

        listener = threading.Thread(target=listen, daemon=True)
        listener.start()
        time.sleep(0.4)
        carol.post("/api/queue/join", {})
        listener.join(8)
        check("the stream sends live changes", len(stream_events) >= 2 and "me" in stream_events[-1], stream_events[-1:])
        first_poll = bob.get("/api/queue/poll")[1]["events"]
        check("a window's first answer always carries the chat, even empty, so it can tell old messages from new", [e["type"] for e in first_poll][:2] == ["state", "chat"], first_poll[:2])
        check("the stream requires login", Client(port).get("/api/queue/stream", cookie=False)[0] == 401)

        print("chat")
        for path in ("/api/queue/chat",):
            check("chat requires login", Client(port).get(path, cookie=False)[0] == 401)
        secret = "public hello Γειά σου <b>x</b>"
        s, d, _ = alice.post("/api/queue/chat", {"text": secret})
        check("anyone can post", s == 200 and d["message"]["from"] == "alice", d)
        check("everyone sees public messages", all(secret in [m["text"] for m in u.get("/api/queue/chat")[1]["messages"]] for u in (bob, carol, admin)))
        s, d, _ = alice.post("/api/queue/chat", {"text": "just for bob", "to": "bob"})
        check("private messages can be sent", s == 200 and d["message"]["to"] == "bob", d)
        texts = lambda u: [m["text"] for m in u.get("/api/queue/chat")[1]["messages"]]
        check("sender sees their private message", "just for bob" in texts(alice))
        check("recipient sees it", "just for bob" in texts(bob))
        check("a third user cannot", "just for bob" not in texts(carol))
        check("an admin cannot either", "just for bob" not in texts(admin))
        for body, code in (({"text": ""}, 400), ({"text": "   "}, 400), ({"text": 5}, 400), ({"text": "x" * 1001}, 400),
                           ({"text": "hi", "to": "nobody"}, 404), ({"text": "hi", "to": "alice"}, 400)):
            check(f"bad message {str(body)[:24]} -> {code}", alice.post("/api/queue/chat", body)[0] == code)
        chat_events = []

        def listen_chat():
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=8)
            conn.request("GET", "/api/queue/stream", headers={"Cookie": carol.cookie})
            resp, name = conn.getresponse(), ""
            for raw in resp:
                line = raw.decode().strip()
                if line.startswith("event:"):
                    name = line[6:].strip()
                elif line.startswith("data:") and name == "chat":
                    chat_events.extend(json.loads(line[5:])["messages"])
                    if any(m["text"] == "live message" for m in chat_events):
                        break
            conn.close()

        chat_listener = threading.Thread(target=listen_chat, daemon=True)
        chat_listener.start()
        time.sleep(0.4)
        bob.post("/api/queue/chat", {"text": "live message"})
        chat_listener.join(8)
        live = [m["text"] for m in chat_events]
        check("the stream delivers messages", "live message" in live, live)
        check("the stream hides other people's private messages", "just for bob" not in live, live)
        bob.post("/api/queue/join", {})
        check("the holder or line can be messaged without a live connection", "bob" in [p["username"] for p in state(alice)["online"]], state(alice)["online"])
        limited = [carol.post("/api/queue/chat", {"text": f"m{i}"})[0] for i in range(9)]
        check("flooding is limited", limited[-1] == 429 and limited[0] == 200, limited)
        check("users cannot clear the chat", bob.post("/api/admin/queue/chat/clear", {})[0] == 403)
        check("admins can clear the chat", admin.post("/api/admin/queue/chat/clear", {})[0] == 200 and texts(alice) == [] and state(alice)["chat_epoch"] == 1)
        s, d, _ = alice.post("/api/queue/chat", {"text": "never reaches the model"})
        for person in (admin, carol, bob):
            person.post("/api/queue/leave", {})
            person.post("/api/queue/release", {})
        check("the seat is free before the estimate test", until(lambda: state(alice)["free"] and not state(alice)["queue"]), state(alice))

        print("estimates")
        s, d, _ = send(alice, "Do it with progress")
        run = d["run"]
        check("the model's progress reaches the waiting room", until(lambda: ((state(bob)["holder"] or {}).get("estimate") or {}).get("state") == "approval"), state(bob))
        estimate = state(bob)["holder"]["estimate"]
        check("an approval shows as waiting, with the step", estimate["step"] >= 2 and estimate["total"] == 5, estimate)
        finish_run(alice, run)
        sessions = os.path.join(folder, "app", "data", "sessions")
        stored = " ".join(open(os.path.join(sessions, n), encoding="utf-8").read() for n in os.listdir(sessions))
        check("chat never enters a model conversation", "never reaches the model" not in stored and "public hello" not in stored)

        print("room details")
        room = state(bob)
        check("each person online shows their place", all({"holds", "position"} <= set(p) for p in room["online"]), room["online"])
        check("the day's turns are counted", room["stats"]["turns_today"] >= 4 and room["stats"]["typical_turn_s"] > 0, room["stats"])
        s, d, _ = send(alice)
        finish_run(alice, d["run"])
        bob.post("/api/queue/join", {})
        carol.post("/api/queue/join", {})
        waits = state(bob)["me"].get("wait_s"), state(carol)["me"].get("wait_s")
        check("people in line get a wait estimate, longer further back", None not in waits and waits[1] > waits[0], waits)
        for person in (bob, carol):
            person.post("/api/queue/leave", {})
        alice.post("/api/queue/release", {})
        people = bob.get("/api/people")[1]["people"]
        check("everyone else can be listed, not yourself", {"alice", "carol", "admin"} <= {p["username"] for p in people} and "bob" not in {p["username"] for p in people}, people)

        print("passing a chat")
        home = alice.get("/api/config")[1]["workspace_roots"][0]
        with open(os.path.join(home, "plan.md"), "w") as f:
            f.write("half done")
        alice.post("/api/agent/session/settings", {"ws": alice.ws, "id": alice.sess, "effort": "low"})
        original = json.load(open(os.path.join(sessions, alice.sess + ".json")))
        offer = lambda **extra: alice.post("/api/handoff/offer", {"ws": alice.ws, "session": alice.sess, "to": "bob", "note": "Finish the plan", **extra})
        check("you cannot pass a chat to yourself", offer(to="alice")[0] == 400)
        check("nor to someone unknown", offer(to="nobody")[0] == 404)
        check("nor someone else's chat", bob.post("/api/handoff/offer", {"ws": bob.ws, "session": alice.sess, "to": "carol"})[0] == 404)
        check("a long note is refused", offer(note="x" * 501)[0] == 400)
        s, d, _ = offer()
        check("a chat can be offered to someone", s == 200 and d["offer"]["to"] == "bob" and d["offer"]["state"] == "offered", d)
        hid = d["offer"]["id"]
        check("offering the same chat twice is refused", offer()[0] == 409)
        incoming = state(bob)["handoffs"]["incoming"]
        check("the recipient sees the offer, with the note", [h["id"] for h in incoming] == [hid] and incoming[0]["note"] == "Finish the plan" and incoming[0]["from"] == "alice", incoming)
        check("the sender sees it waiting", state(alice)["handoffs"]["outgoing"][0]["state"] == "offered")
        check("nobody else sees it", state(carol)["handoffs"] == {"incoming": [], "outgoing": []})
        check("only the recipient can accept", carol.post("/api/handoff/accept", {"id": hid})[0] == 404 and alice.post("/api/handoff/accept", {"id": hid})[0] == 404)
        s, d, _ = bob.post("/api/handoff/accept", {"id": hid})
        check("accepting copies the chat and the folder", s == 200 and d["ws"]["owner"] == "bob" and d["session"] != alice.sess, d)
        new_ws, new_sid = d["ws"], d["session"]
        check("the folder copy is in their own space", os.path.dirname(new_ws["path"]) == bob.get("/api/config")[1]["workspace_roots"][0]
              and open(os.path.join(new_ws["path"], "plan.md")).read() == "half done", new_ws["path"])
        copied = bob.get(f"/api/agent/session?ws={new_ws['id']}&id={new_sid}")
        check("the recipient can open the chat in that folder", copied[0] == 200, copied[0])
        stored_copy = json.load(open(os.path.join(sessions, new_sid + ".json")))
        check("the chat keeps its history and settings, and says who passed it", stored_copy["owner"] == "bob" and stored_copy["messages"] == original["messages"]
              and stored_copy.get("effort") == "low" and stored_copy["events"][-1]["type"] == "handoff" and stored_copy["events"][-1]["note"] == "Finish the plan", stored_copy["events"][-1:])
        check("its system prompt is built again for the new folder", "system_prompt" not in stored_copy)
        with open(os.path.join(new_ws["path"], "plan.md"), "w") as f:
            f.write("bob's version")
        check("their edits do not reach the sender", open(os.path.join(home, "plan.md")).read() == "half done")
        check("the sender's chat stays theirs", alice.get(f"/api/agent/session?ws={alice.ws}&id={alice.sess}")[0] == 200)
        check("the sender hears it was accepted", state(alice)["handoffs"]["outgoing"][0]["state"] == "accepted" and state(bob)["handoffs"]["incoming"] == [])
        check("an offer cannot be accepted twice", bob.post("/api/handoff/accept", {"id": hid})[0] == 409)
        hid2 = offer(to="carol")[1]["offer"]["id"]
        check("the recipient can decline", carol.post("/api/handoff/decline", {"id": hid2})[0] == 200 and state(alice)["handoffs"]["outgoing"][-1]["state"] == "declined")
        hid3 = offer(to="carol")[1]["offer"]["id"]
        check("only the sender can cancel", carol.post("/api/handoff/cancel", {"id": hid3})[0] == 404 and alice.post("/api/handoff/cancel", {"id": hid3})[0] == 200
              and state(carol)["handoffs"]["incoming"] == [])
        hid4 = offer(to="carol")[1]["offer"]["id"]
        s, d, _ = carol.post("/api/handoff/accept", {"id": hid4})
        check("a second copy gets its own folder name", s == 200 and d["ws"]["path"] != new_ws["path"] and os.path.basename(d["ws"]["path"]).startswith(os.path.basename(home) + "-from-alice"), d.get("ws"))
        check("a passed copy says who passed it", d["ws"]["place"]["kind"] == "passed" and d["ws"]["place"]["from"], d["ws"].get("place"))
        check("people cannot open each other's copies", carol.get(f"/api/agent/session?ws={new_ws['id']}&id={new_sid}")[0] == 404)
    finally:
        server.terminate()
        server.wait(5)


def test_mcp(tmp, gw_port):
    print("MCP tool servers")
    sys.path.insert(0, ROOT)
    import studio as sm
    check("server settings show plainly only when harmless", sm.setting_shown("LISTMONK_USER", "studio") == "studio"
          and sm.setting_shown("LISTMONK_TOKEN", "abcdefghijklmnop") == "••••mnop" and sm.setting_shown("API_KEY", "short") == "••••"
          and sm.setting_shown("DATABASE_URI", "postgresql://app:hunter22@db/x") == "••••db/x" and sm.setting_shown("SEARXNG_URL", "http://127.0.0.1:8888") == "http://127.0.0.1:8888")
    check("MCP names keep to the allowed characters", sm.mcp_tool_name("blender", "get scene.info") == "mcp__blender__get_scene_info")
    slow = sm.clean_mcp_spec({"name": "comfy", "command": "python3", "timeout_s": 1800})
    check("a server can have a longer call timeout", slow["timeout_s"] == 1800 and "timeout_s" not in sm.clean_mcp_spec({"name": "x", "command": "y"}))
    for bad in (5, 5000, "60", True):
        try:
            sm.clean_mcp_spec({"name": "comfy", "command": "python3", "timeout_s": bad})
            check(f"a timeout of {bad!r} is refused", False)
        except sm.ApiError:
            check(f"a timeout of {bad!r} is refused", True)
    long_name = sm.mcp_tool_name("server", "x" * 80)
    check("long MCP names are cut to 64 with a hash", len(long_name) == 64 and long_name.startswith("mcp__server__x"))
    check("a picture result is described, not sent", "[image returned: image/png, about 3 KB" in sm.mcp_result_text({"content": [{"type": "image", "data": "A" * 4096, "mimeType": "image/png"}]}))
    check("structured output is shown when there is no text", '"sum": 5' in sm.mcp_result_text({"content": [], "structuredContent": {"sum": 5}}))
    check("long MCP output is cut", sm.mcp_result_text({"content": [{"type": "text", "text": "x" * 20000}]}).endswith("8000 more characters]"))
    calls, _ = sm.recover_bare_tool_calls('{"name": "mcp__mock__echo", "arguments": {"text": "hi"}}', {"mcp__mock__echo": {}})
    check("a bare JSON call to an MCP tool is recovered", [c["function"]["name"] for c in calls] == ["mcp__mock__echo"])
    check("a bare JSON call to an unknown MCP tool is not", sm.recover_bare_tool_calls('{"name": "mcp__mock__echo", "arguments": {}}')[0] == [])

    folder = os.path.join(tmp, "mcp")
    projects = os.path.join(folder, "projects")
    os.makedirs(os.path.join(projects, "work"))
    port, http_port = free_port(), free_port()
    config = os.path.join(folder, "app", "studio.config.json")
    os.makedirs(os.path.dirname(config))
    json.dump({"host": "127.0.0.1", "port": port, "gateway": f"http://127.0.0.1:{gw_port}", "workspace_roots": [projects],
               "user_root": os.path.join(projects, "studio-users"), "default_model": "quartz:strata", "max_turns": 10, "command_timeout_s": 20,
               "mcp_call_timeout_s": 4}, open(config, "w"))
    web = subprocess.Popen([sys.executable, os.path.join(ROOT, "tests", "mock_mcp.py"), "--http", str(http_port)])
    server = start_studio(folder, config, port)
    try:
        wait_port(http_port, web)
        c = Client(port)
        c.post("/api/auth/setup", {"password": "admin password"})
        ws = c.post("/api/workspace/open", {"path": os.path.join(projects, "work")})[1]["workspace"]["id"]
        session = c.post("/api/agent/sessions", {"ws": ws})[1]["id"]
        check("no tool servers at first", c.get("/api/admin/mcp")[1]["servers"] == [])
        mock = {"name": "mock", "command": sys.executable, "args": [os.path.join(ROOT, "tests", "mock_mcp.py")], "env": {"SECRET_X": "s3cret"}}
        webspec = {"name": "web", "url": f"http://127.0.0.1:{http_port}/mcp", "headers": {"Authorization": "Bearer test-token"}, "approval": "with_commands"}
        for bad, why in (([{**mock, "name": "bad name"}], "a bad name"), ([mock, mock], "two servers with one name"),
                         ([{"name": "x", "url": "ftp://host"}], "a URL that is not http"), ([{"name": "x"}], "no command"),
                         ([{**mock, "approval": "never"}], "an unknown approval")):
            check(f"the panel refuses {why}", c.post("/api/admin/mcp", {"servers": bad})[0] == 400)
        status, saved, _ = c.post("/api/admin/mcp", {"servers": [mock, webspec, {"name": "broken", "command": "/nonexistent/program"}]})
        check("tool servers are saved", status == 200 and [s["name"] for s in saved["servers"]] == ["mock", "web", "broken"], saved)
        check("data/mcp.json is private", oct(os.stat(os.path.join(folder, "app", "data", "mcp.json")).st_mode & 0o777) == "0o600")

        def servers():
            return {s["name"]: s for s in c.get("/api/admin/mcp")[1]["servers"]}
        deadline = time.time() + 20
        while time.time() < deadline and any(s["state"] == "starting" for s in servers().values()):
            time.sleep(0.2)
        found = servers()
        check("a program server starts", found["mock"]["state"] == "ready", found["mock"])
        check("both pages of its tool list are read", len(found["mock"]["tools"]) == 8, [t["name"] for t in found["mock"]["tools"]])
        check("an HTTP server starts with its header", found["web"]["state"] == "ready", found["web"])
        check("a missing program is reported", found["broken"]["state"] == "error" and found["broken"]["error"], found["broken"])
        check("its stderr reaches the log", any("mock MCP server started" in line for line in found["mock"]["log"]), found["mock"]["log"])
        check("secrets come back masked", found["mock"]["env"] == {"SECRET_X": sm.SECRET_MASK} and found["web"]["headers"] == {"Authorization": sm.SECRET_MASK})
        check("secrets never appear in the panel data", "s3cret" not in json.dumps(c.get("/api/admin/mcp")[1]))
        # Saving the list as shown keeps the stored secrets.
        c.post("/api/admin/mcp", {"servers": [{k: v for k, v in s.items() if k in ("name", "command", "args", "env", "url", "headers", "approval", "enabled")} for s in found.values()]})
        deadline = time.time() + 20
        while time.time() < deadline and servers()["web"]["state"] != "ready":
            time.sleep(0.2)

        def agent(message, accept=True, auto_commands=False, on_approval=None):
            asked = []

            def decide(ev):
                asked.append(ev)
                return ("accept" if accept else "reject"), ("" if accept else "not now")
            run, events = run_agent(c, ws, session, message, auto_commands=auto_commands, on_approval=on_approval or decide)
            results = [e for e in events if e["type"] == "tool_result" and e["name"] != "finish"]
            return asked, results, events

        asked, results, _ = agent('use mcp mcp__mock__echo {"text": "hi", "progress": "1/2"}')
        check("a call asks first, naming the server and tool", len(asked) == 1 and asked[0]["kind"] == "mcp_ask" and asked[0]["server"] == "mock" and asked[0]["tool"] == "echo", asked)
        check("the approval shows the arguments without progress", asked and '"text": "hi"' in asked[0]["arguments"] and "progress" not in asked[0]["arguments"], asked)
        check("the tool runs and its text comes back", results and results[0]["ok"] and results[0]["output"] == "echo: hi", results)
        last = c_get_json(gw_port, "/_mock/requests")["requests"][-1]
        check("the model is offered every server's tools", {"mcp__mock__echo", "mcp__web__pinged", "list_files"} <= set(last["tool_names"]), last["tool_names"])
        check("the broken server's tools are not offered", not any(n.startswith("mcp__broken") for n in last["tool_names"]))
        check("the system prompt names the servers", "mcp__<server>__<tool>" in last["system"] and "- mock: A test server." in last["system"], last["system"][-400:])
        check("and gives the project's full path for MCP tools that take files", f"the project folder is {os.path.realpath(os.path.join(projects, 'work'))}" in last["system"], last["system"][-500:])
        check("a stored secret still reaches the program", agent('use mcp mcp__mock__env {"name": "SECRET_X"}')[1][0]["output"] == "s3cret")
        asked, results, _ = agent('use mcp mcp__mock__echo {"text": "no"}', accept=False)
        check("a rejected call does not run", results and not results[0]["ok"] and "rejected this tool call" in results[0]["output"] and "not now" in results[0]["output"], results)
        asked, results, _ = agent('use mcp mcp__mock__echo {"text": "auto"}', auto_commands=True)
        check("a server set to ask every time asks even with auto-run", len(asked) == 1)
        asked, results, _ = agent('use mcp mcp__web__echo {"text": "over http"}', auto_commands=True)
        check("a server that follows commands runs with auto-run on", asked == [] and results and results[0]["output"] == "echo: over http", (asked, results))
        asked, results, _ = agent('use mcp mcp__web__echo {"text": "ask"}')
        check("and asks with auto-run off", len(asked) == 1 and asked[0]["kind"] == "mcp")
        results = agent('use mcp mcp__mock__fail {}')[1]
        check("an error result fails the call", results and not results[0]["ok"] and results[0]["output"] == "it broke" and "failed" in results[0]["summary"], results)
        check("structured output reaches the model", '"sum": 5' in agent('use mcp mcp__mock__add {"a": 2, "b": 3}')[1][0]["output"])
        asked, results, events = agent('use mcp mcp__mock__count {}', auto_commands=True)
        call_id = next(e["id"] for e in events if e["type"] == "tool_call" and e["name"] == "mcp__mock__count")
        notes = [e for e in events if e["type"] == "tool_progress"]
        check("a tool's progress notifications reach its row in the chat", results and "with progress" in results[0]["output"] and notes
              and all(e["id"] == call_id for e in notes) and notes[-1]["percent"] == 100 and "counted 3 of 3" in notes[-1]["message"], (results[:1], notes))
        check("a tool at work is not marked waiting", all(e["waiting"] is False for e in notes), notes)
        stored = json.load(open(os.path.join(tmp, "mcp", "app", "data", "sessions", session + ".json")))
        check("and are not kept in the chat", not any(e["type"] == "tool_progress" for e in stored["events"]))

        # A program the server needs (Blender for the blender server), played by a script that listens on a port.
        app_port = free_port()
        fake_app = os.path.join(tmp, "fake_app.py")
        with open(fake_app, "w") as f:
            f.write("import socket, sys, time\ntime.sleep(1.5)\nsrv = socket.create_server(('127.0.0.1', int(sys.argv[1])))\nwhile True: time.sleep(1)\n")
        fields = ("name", "transport", "enabled", "approval", "users", "description", "command", "args", "env", "url", "headers", "timeout_s")
        current = [{k: v for k, v in sv.items() if k in fields and v is not None} for sv in c.get("/api/admin/mcp")[1]["servers"]]
        with_app = [{**sv, "app_command": f"{sys.executable} {fake_app} {app_port}", "app_port": app_port} if sv["name"] == "mock" else sv for sv in current]
        check("a program needs its port", c.post("/api/admin/mcp", {"servers": [{**sv, "app_port": None} if sv["name"] == "mock" else sv for sv in with_app]})[0] == 400)
        check("a program to start is saved with the server", c.post("/api/admin/mcp", {"servers": with_app})[0] == 200)
        app = lambda: servers()["mock"]["app"]
        check("the panel shows it is not running yet", app() and app()["running"] is False and app()["port"] == app_port, app())
        asked, results, events = agent('use mcp mcp__mock__echo {"text": "with app"}', auto_commands=True)
        check("a call that needs it starts it first, then runs", results and results[0]["ok"] and any(e["type"] == "status" and "Starting" in (e.get("text") or "") for e in events)
              and app()["running"] and app()["started_by_studio"], (results[:1], app()))
        check("Studio can stop a program it started", c.post("/api/admin/mcp/app", {"name": "mock", "action": "stop"})[0] == 200 and app()["running"] is False)
        check("stopping it twice says so", c.post("/api/admin/mcp/app", {"name": "mock", "action": "stop"})[0] == 409)
        check("and start it from the panel", c.post("/api/admin/mcp/app", {"name": "mock", "action": "start"})[1].get("running") is True and app()["running"])
        check("a server with no program has none to start", c.post("/api/admin/mcp/app", {"name": "web", "action": "start"})[0] == 404)
        c.post("/api/admin/mcp/app", {"name": "mock", "action": "stop"})
        check("its window is shown or hidden, nothing else", c.post("/api/admin/mcp", {"servers": [{**sv, "app_window": "maybe"} if sv["name"] == "mock" else sv for sv in with_app]})[0] == 400)
        check("the window choice is saved", c.post("/api/admin/mcp", {"servers": [{**sv, "app_window": "hidden"} if sv["name"] == "mock" else sv for sv in with_app]})[0] == 200
              and app()["window"] == "hidden")
        s2, d2, _ = c.post("/api/admin/mcp/app", {"name": "mock", "action": "start"})
        if shutil.which("Xvfb"):
            check("a hidden program runs on an invisible screen", s2 == 200 and app()["running"] and app()["hidden_available"], (s2, d2))
            c.post("/api/admin/mcp/app", {"name": "mock", "action": "stop"})
        else:
            check("without Xvfb a hidden program says what to install", s2 == 502 and "sudo apt install xvfb" in d2.get("error", "") and app()["hidden_available"] is False
                  and "xvfb" in app()["error"], (s2, d2, app()))
            results = agent('use mcp mcp__mock__echo {"text": "hidden"}', auto_commands=True)[1]
            check("and so does a call that needs it, to the agent", results and not results[0]["ok"] and "sudo apt install xvfb" in results[0]["output"], results[:1])
        c.post("/api/admin/mcp", {"servers": current})
        before = len(c_get_json(gw_port, "/_mock/requests")["requests"])
        _, results, events = agent('use mcp mcp__mock__picture {}')
        check("a picture is described, and attached for a model that can see", "[image returned: image/png" in results[0]["output"] and "attached to the next message" in results[0]["output"], results[0]["output"])
        shown = [e for e in events if e["type"] == "tool_picture"]
        check("the chat gets the picture too", len(shown) == 1 and shown[0]["server"] == "mock" and shown[0]["tool"] == "picture" and shown[0]["images"][0]["id"].endswith(".png"), shown)
        later = c_get_json(gw_port, "/_mock/requests")["requests"][before:]
        check("the model's next request carries it as an image part", any(r["image_parts"] for r in later[1:]) and not later[0]["image_parts"], [len(r["image_parts"]) for r in later])
        check("in a note after the tool result", any(r["last_user"].startswith("Studio note: the pictures returned by mock · picture are attached.") for r in later[1:]))
        blind_session = c.post("/api/agent/sessions", {"ws": ws})[1]["id"]
        before = len(c_get_json(gw_port, "/_mock/requests")["requests"])
        blind = run_agent(c, ws, blind_session, 'use mcp mcp__mock__picture {}', model="qwen2.5-coder:3b", on_approval=lambda ev: ("accept", ""))[1]
        out = next(e for e in blind if e["type"] == "tool_result" and e["name"] == "mcp__mock__picture")["output"]
        check("a model that cannot see gets only the description, while the chat still shows the picture",
              "not shown to you" in out and any(e["type"] == "tool_picture" for e in blind)
              and not any(r["image_parts"] for r in c_get_json(gw_port, "/_mock/requests")["requests"][before:]), out)
        check("either way the result names the picture's id for save_picture", re.search(r'Studio kept it as picture "[0-9a-f]{16}\.png"', out), out)
        many = types.SimpleNamespace(session={"id": session}, vision=False, emit=lambda *a, **k: None, pending_pictures=[])
        picture = {"type": "image", "mimeType": "image/png", "data": base64.b64encode(open(os.path.join(ROOT, "tests", "mock_mcp.py"), "rb").read()[:64]).decode()}
        saved_app, sm.APP = getattr(sm, "APP", None), types.SimpleNamespace(data_dir=os.path.join(folder, "pictures-data"))
        try:
            seen_ids = sm.keep_tool_pictures(many, {"server": "comfyui", "tool": "generate_images"}, {"content": [picture] * 8})[1]
        finally:
            sm.APP = saved_app
        check("all 8 pictures of a batch get an id", len(seen_ids) == 8 and len(set(seen_ids)) == 8, seen_ids)

        def save(message, approve=True):
            seen = []

            def decide(ev):
                seen.append(ev)
                return ("accept" if approve else "reject"), ""
            events = run_agent(c, ws, c.post("/api/agent/sessions", {"ws": ws})[1]["id"], message, auto_commands=True, on_approval=decide)[1]
            result = next((e for e in events if e["type"] == "tool_result" and e["name"] == "save_picture"), {})
            return result, events, [a for a in seen if a.get("action") == "picture"]
        target = os.path.join(projects, "work", "assets", "shot.png")
        result, events, asked = save("save the picture to assets/shot.png")
        changed = [e for e in events if e["type"] == "file_changed"]
        check("save_picture asks like an edit, showing the picture", len(asked) == 1 and asked[0]["kind"] == "edit" and asked[0]["path"] == "assets/shot.png"
              and re.fullmatch(r"[0-9a-f]{16}\.png", asked[0].get("picture", "")), asked)
        check("and writes the picture into the project", result.get("ok") and os.path.isfile(target) and open(target, "rb").read()[:8] == b"\x89PNG\r\n\x1a\n",
              (result, [(e["type"], e.get("name") or e.get("text") or e.get("reason")) for e in events if e["type"] not in ("token", "thinking", "usage")]))
        check("with an undo point", changed and changed[0]["action"] == "picture" and changed[0]["path"] == "assets/shot.png", changed)
        result, _, _ = save("save the picture to assets/shot.png")
        trash = os.path.join(projects, "work", ".strata-studio", "trash")
        check("saving over a file moves the old one to the trash", result.get("ok") and "earlier file is in the trash" in result["output"]
              and any("shot.png" in names for _, _, names in os.walk(trash)), result)
        undone = c.post("/api/agent/undo", {"ws": ws, "checkpoint": changed[0]["checkpoint"]})
        check("undo takes a saved picture away again", undone[0] == 200 and undone[1]["restored"] == ["assets/shot.png"], undone[:2])
        check("a path with the wrong file type is refused", "must end in .png" in save("save the picture to assets/shot.jpg")[0].get("output", ""))
        check("an id that is not a picture of this chat is refused", "no picture" in save("save the picture to x.png as 0123456789abcdef.png")[0].get("output", ""))
        check("a path outside the project is refused", not save("save the picture to ../outside.png")[0].get("ok") and not os.path.exists(os.path.join(projects, "outside.png")))
        result, _, _ = save("save the picture to assets/no.png", approve=False)
        check("a rejected save writes nothing", not result.get("ok") and not os.path.exists(os.path.join(projects, "work", "assets", "no.png")), result)

        stand_in = types.SimpleNamespace(mcp_tools={"mcp__greek-check__check_text": {"server": "greek-check", "tool": "check_text"},
                                                    "mcp__comfyui__generate_image": {"server": "comfyui", "tool": "generate_image"},
                                                    "mcp__comfyui__list_models": {"server": "comfyui", "tool": "list_models"},
                                                    "mcp__other__list_models": {"server": "other", "tool": "list_models"}})
        names = {short: sm.full_tool_name(stand_in, short) for short in ("mcp__greek-check", "mcp__greek-check__", "check_text", "generate_image",
                                                                         "mcp__comfyui", "list_models", "read_file", "nope")}
        check("a server's only tool, or a tool name only one server has, is understood from a short name",
              names["mcp__greek-check"] == names["mcp__greek-check__"] == names["check_text"] == "mcp__greek-check__check_text"
              and names["generate_image"] == "mcp__comfyui__generate_image", names)
        check("a short name that could mean several tools, or a built-in one, stays as it is",
              names["mcp__comfyui"] == "mcp__comfyui" and names["list_models"] == "list_models" and names["read_file"] == "read_file" and names["nope"] == "nope", names)
        check("Studio answers the server's ping", agent('use mcp mcp__mock__pinged {}')[1][0]["output"] == "yes")
        results = agent('use mcp mcp__mock__slow {"seconds": 30}')[1]
        check("a call with no answer times out", results and not results[0]["ok"] and "no answer after 4s" in results[0]["output"], results)
        check("an unknown MCP tool is refused", "Unknown tool 'mcp__mock__nope'" in agent('use mcp mcp__mock__nope {}')[1][0]["output"])

        status, data, _ = c.post("/api/agent/send", {"session": session, "ws": ws, "message": 'use mcp mcp__mock__slow {"seconds": 3}', "model": "quartz:strata"})
        began, stopped_output = time.time(), None
        for name, ev in c.sse(f"/api/agent/stream?run={data['run']}", timeout=60):
            if name == "approval":
                c.post("/api/agent/decision", {"run": data["run"], "approval_id": ev["approval_id"], "decision": "accept"})
                threading.Timer(0.5, lambda: c.post("/api/agent/stop", {"run": data["run"]})).start()
            if name == "tool_result":
                stopped_output = ev["output"]
        check("stopping a run cancels a waiting call", stopped_output and "stopped by the user" in stopped_output and time.time() - began < 3, (stopped_output, time.time() - began))

        c.post("/api/admin/mcp", {"servers": [{**mock, "env": {"SECRET_X": sm.SECRET_MASK}, "enabled": False}, webspec]})
        agent("hello")
        names = c_get_json(gw_port, "/_mock/requests")["requests"][-1]["tool_names"]
        check("a server turned off is stopped and not offered", servers()["mock"]["state"] == "off" and not any(n.startswith("mcp__mock") for n in names) and "mcp__web__echo" in names)
        check("restarting an unknown server is refused", c.post("/api/admin/mcp/restart", {"name": "mock"})[0] == 404)
        check("restarting a server works", c.post("/api/admin/mcp/restart", {"name": "web"})[0] == 200)
        deadline = time.time() + 20
        while time.time() < deadline and servers()["web"]["state"] != "ready":
            time.sleep(0.2)

        def restart_then_allow(ev):
            c.post("/api/admin/mcp/restart", {"name": "web"})
            return "accept", ""
        results = agent('use mcp mcp__web__echo {"text": "after restart"}', on_approval=restart_then_allow)[1]
        check("a run started before a restart reaches the restarted server", results and results[0]["ok"] and results[0]["output"] == "echo: after restart", results)
        before = servers()["web"]["log"]
        c.post("/api/admin/mcp", {"servers": [{**mock, "env": {"SECRET_X": sm.SECRET_MASK}, "enabled": False}, {**webspec, "approval": "always", "description": "Changed only here."}]})
        check("changing approval or the description does not restart the server", servers()["web"]["log"] == before and servers()["web"]["approval"] == "always", servers()["web"]["log"][-2:])
        check("and the new approval applies at once", len(agent('use mcp mcp__web__echo {"text": "x"}', auto_commands=True)[0]) == 1)
        docker_spec = {"name": "pg db", "transport": "stdio", "command": "docker", "args": ["run", "-i", "--rm", "img", "--flag"]}
        check("a docker server gets a container name Studio can remove", sm.mcp_argv(docker_spec) == (["docker", "run", "--name", "studio-mcp-pg-db", "-i", "--rm", "img", "--flag"], "studio-mcp-pg-db"))
        check("its own --name is kept", sm.mcp_argv({**docker_spec, "args": ["run", "--name", "mine", "img"]}) == (["docker", "run", "--name", "mine", "img"], "mine"))
        check("other programs run as written", sm.mcp_argv({**docker_spec, "command": "npx", "args": ["-y", "pkg"]}) == (["npx", "-y", "pkg"], None))

        member = Client(port)
        token = c.post("/api/admin/invites", {})[1]["token"]
        member.post("/api/auth/signup", {"token": token, "username": "mila", "password": "member password"})
        check("users cannot see the tool servers", member.get("/api/admin/mcp")[0] == 403)
        home = member.get("/api/config")[1]["workspace_roots"][0]
        mws = member.post("/api/workspace/open", {"path": home})[1]["workspace"]["id"]
        msession = member.post("/api/agent/sessions", {"ws": mws})[1]["id"]
        c.post("/api/queue/release", {})
        run_agent(member, mws, msession, "hello")
        names = c_get_json(gw_port, "/_mock/requests")["requests"][-1]["tool_names"]
        check("users do not get a server's tools by default", names and not any(n.startswith("mcp__") for n in names), names)
        c.post("/api/admin/mcp", {"servers": [{**webspec, "headers": {"Authorization": sm.SECRET_MASK}, "users": True}]})
        member.post("/api/queue/release", {})
        run_agent(member, mws, msession, "hello")
        names = c_get_json(gw_port, "/_mock/requests")["requests"][-1]["tool_names"]
        check("they get it once it is shared with users", "mcp__web__echo" in names, names)
        check("a removed server is gone", list(servers()) == ["web"])
    finally:
        for p in (server, web):
            p.terminate()
            p.wait()


def test_stale_sandbox(tmp):
    print("sandbox: a folder replaced at the same path")
    sys.path.insert(0, ROOT)
    import studio as sm
    folder = os.path.join(tmp, "stale")
    work = os.path.join(folder, "work")
    os.makedirs(work)
    saved_app, saved_docker = sm.APP, sm.docker
    sm.APP = types.SimpleNamespace(data_dir=os.path.join(folder, "data"), cfg={"sandbox_image": "test-image", "sandbox_network": "off"})
    ws = {"id": "abc123", "path": work}
    home = os.path.join(folder, "data", "sandbox_homes", "abc123")
    calls, container = [], {"state": None, "seen": None}

    def fake_docker(*args, timeout=120):
        calls.append(args)
        out = types.SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[0] == "inspect":
            out.returncode, out.stdout = (1, "") if container["state"] is None else (0, container["state"])
        elif args[0] == "exec" and container["seen"] == "stale":
            out.returncode, out.stderr = 126, "OCI runtime exec failed: current working directory is outside of container mount namespace root"
        elif args[0] == "exec":
            out.returncode, out.stdout = (1, "") if container["seen"] is None else (0, container["seen"])
        elif args[0] == "run":
            container["state"], container["seen"] = "true", f"{os.stat(work).st_ino}\n{os.stat(home).st_ino}\n"
        elif args[0] == "rm":
            container["state"] = None
        elif args[0] == "start":
            container["state"] = "true"
        return out
    sm.docker = fake_docker
    try:
        sm.SANDBOX_VERIFIED.clear()
        sm.ensure_sandbox(ws)
        check("a new folder gets a container", [c[0] for c in calls] == ["inspect", "run"], calls)
        calls.clear()
        sm.ensure_sandbox(ws)
        sm.ensure_sandbox(ws)
        check("a current container is reused and checked once", [c[0] for c in calls] == ["inspect", "exec", "inspect"], calls)
        os.rename(work, work + "-old")
        os.makedirs(work)
        calls.clear()
        sm.ensure_sandbox(ws)
        check("a container that still sees the replaced folder is created again", [c[0] for c in calls] == ["inspect", "exec", "rm", "run"], calls)
        container["state"] = "false"
        calls.clear()
        sm.ensure_sandbox(ws)
        check("a stopped container is started and still checked", [c[0] for c in calls] == ["inspect", "start", "exec"] and container["state"] == "true", calls)
        os.rename(work, work + "-older")
        os.makedirs(work)
        container["seen"] = None
        calls.clear()
        sm.ensure_sandbox(ws)
        check("when the check cannot run, the container is kept", [c[0] for c in calls] == ["inspect", "exec"], calls)
        check("the check runs from /, which works even when /work was deleted", calls[1][:3] == ("exec", "-w", "/"), calls)
        container["seen"] = "stale"
        os.rename(work, work + "-oldest")
        os.makedirs(work)
        calls.clear()
        sm.ensure_sandbox(ws)
        check("a container that refuses commands because its folder was deleted is created again",
              [c[0] for c in calls] == ["inspect", "exec", "rm", "run"], calls)
    finally:
        sm.APP, sm.docker = saved_app, saved_docker
        sm.SANDBOX_VERIFIED.clear()


def test_presence(tmp, gw_port):
    print("waiting room: leaving the page")
    sys.path.insert(0, ROOT)
    import types as _types
    import studio as sm
    sm.APP = _types.SimpleNamespace(active={})
    room = sm.WaitingRoom({"queue_presence_s": 0.1, "queue_offer_s": 100, "queue_idle_s": 100, "queue_warn_s": 1, "queue_max_s": 100})
    alice, bob = {"username": "alice"}, {"username": "bob"}
    room.join(alice)
    room.join(bob)
    check("someone in line starts present", room.find("bob") is not None)
    room.connect("bob")
    time.sleep(0.2)
    room.tick()
    check("an open connection keeps their place", room.find("bob") is not None)
    room.disconnect("bob")
    time.sleep(0.2)
    room.tick()
    check("someone who leaves the page loses their place", room.find("bob") is None)
    room.seen["bob"] = time.time()
    room.join(bob)
    room.holder["since"] = time.time() - 200
    check("over the limit, the holder cannot start another run", room.over_limit())
    try:
        room.claim(alice)
        check("over-limit claim refused", False)
    except sm.ApiError as exc:
        check("over-limit claim refused", exc.status == 409)
    room.tick()
    check("over the limit between runs, the seat passes on", room.holder is None and room.queue[0]["offer_expires"])


def test_estimates():
    print("estimate calculations")
    sys.path.insert(0, ROOT)
    import types as _types
    import studio as sm
    now = 100000.0
    run = _types.SimpleNamespace(started=now - 600, approvals={}, progress=None)
    check("no plan yet: still estimating", sm.estimate_run(run, now)["state"] == "early")
    run.progress = (1, 8, now - 600)
    check("the first step cannot be timed yet", sm.estimate_run(run, now)["state"] == "early")
    run.progress = (3, 8, now)
    got = sm.estimate_run(run, now)
    check("pace of finished steps gives a range", got["state"] == "range" and got["low_min"] == 21 and got["high_min"] == 45, got)
    run.progress = (8, 8, now - 10)
    got = sm.estimate_run(run, now)
    check("the last step still has a range", got["state"] == "range" and got["low_min"] >= 1, got)
    run.progress = (2, 2, now - 10000)
    run.started = now - 10100
    check("past the measured pace it says so", sm.estimate_run(run, now)["state"] == "over")
    run.approvals = {"x": {}}
    check("an approval pauses the estimate", sm.estimate_run(run, now)["state"] == "approval")
    real = _types.SimpleNamespace(progress=None, plan=None)
    note = sm.Run.note_progress
    for text, expected in (("3/8", (3, 8)), (" 3 / 8 ", (3, 8)), ("9/8", None), ("0/5", None), ("abc", None), ("3", None), (5, None), (None, None), ("99999/99999", None)):
        real.progress = None
        note(real, text)
        check(f"progress note {text!r}", (real.progress[:2] if real.progress else None) == expected, real.progress)
    real.progress = None
    note(real, "2/5")
    first = real.progress[2]
    time.sleep(0.01)
    note(real, "2/6")
    check("the same step keeps its start time", real.progress[2] == first and real.progress[1] == 6)
    names = {t["function"]["name"] for t in sm.TOOL_SCHEMAS}
    check("every working tool asks for progress", all("progress" in t["function"]["parameters"]["required"] for t in sm.TOOL_SCHEMAS if t["function"]["name"] != "finish"), names)
    check("finish does not", all("progress" not in t["function"]["parameters"]["required"] for t in sm.TOOL_SCHEMAS if t["function"]["name"] == "finish"))

    print("tool calls sent as text")
    recover = sm.recover_text_tool_calls
    xml = '<tool_call>\n<function=replace_in_file>\n<parameter=new_text>\na\n  b <x>\n</parameter>\n<parameter=path>\nf.py\n</parameter>\n</function>\n</tool_call>'
    calls, rest = recover("Fixing it.\n" + xml + "\n" + xml.replace("f.py", "g.py"))
    check("several text calls are recovered, with exact values", [json.loads(c["function"]["arguments"]) for c in calls] == [{"new_text": "a\n  b <x>", "path": "f.py"}, {"new_text": "a\n  b <x>", "path": "g.py"}] and rest == "Fixing it.", calls)
    calls, rest = recover('<tool_call>{"name": "run", "arguments": {"command": "ls"}}</tool_call>')
    check("the JSON form is recovered", calls and calls[0]["function"]["name"] == "run" and rest == "", calls)
    calls, rest = recover("Fixing it.\n" + xml.replace("replace_in_file", "edit_file"))
    check("an unknown tool name is kept, for the model to hear about", [c["function"]["name"] for c in calls] == ["edit_file"] and rest == "Fixing it.", calls)
    bare = sm.recover_bare_tool_calls
    saved_bare_app = sm.APP
    sm.APP = types.SimpleNamespace(cfg={"delete_tool": True, "skills": True, "plan_tool": True, "live_view": True, "mcp": True})
    calls, rest = bare('{"name": replace_in_file, "arguments": {" path": "a.md", "old_text": "x\ny"}}\n{"name": "read_file", "arguments": {"path": "a.md"}}')
    check("bare calls are recovered, with a quoted name and tidy keys", [c["function"]["name"] for c in calls] == ["replace_in_file", "read_file"] and json.loads(calls[0]["function"]["arguments"]) == {"path": "a.md", "old_text": "x\ny"} and rest == "", calls)
    calls, rest = bare('I will look first.\n```json\n{"name": "run", "arguments": {"command": "ls"}}\n```')
    check("a fenced bare call is recovered and the text kept", calls and calls[0]["function"]["name"] == "run" and rest == "I will look first.", rest)
    calls, rest = bare('{"name": "read_file", "arguments": {"path": "a.md"}}\n{"name": "finish", "arguments": {"summary": "Done."}}')
    check("a batch that ends in finish is recovered", [c["function"]["name"] for c in calls] == ["read_file", "finish"], calls)
    check("an unknown name is left as an answer", bare('{"name": "edit_file", "arguments": {}}')[0] == [])
    check("other JSON is left as an answer", bare('Use this: {"name": "Maria", "age": 3}')[0] == [])
    check("JSON followed by prose is left as an answer", bare('{"name": "run", "arguments": {"command": "ls"}}\nThat is the format.')[0] == [])
    check("a plain answer is untouched", bare("All done.") == ([], "All done."))
    check("a broken object is left as an answer", bare('{"name": "run", "arguments": {"command"')[0] == [])
    check("Mica's reply counts as garbled", sm.has_garbled_call("<tool_call>\n<function=replace_in>\n</function>") is True)
    check("a closed call that cannot be read counts as garbled", sm.has_garbled_call('<tool_call>\n{"name": oops}\n</tool_call>') is True)
    check("text about the format does not", sm.has_garbled_call("The format is <tool_call> and more.") is False and sm.has_garbled_call('Like <tool_call>{"name": "x"}</tool_call> in a sentence.') is False)
    sm.APP = saved_bare_app
    saved_app = sm.APP
    sm.APP = types.SimpleNamespace(cfg={"delete_tool": True, "skills": True, "plan_tool": True, "live_view": True, "mcp": True})
    ok, summary, output = sm.execute_tool(types.SimpleNamespace(note_progress=lambda value: None), "edit_file", {"path": "f.py"})
    sm.APP = saved_app
    check("an unknown tool does nothing and names the real tools", ok is False and summary == "Unknown tool" and "replace_in_file" in output and "finish" in output, output)
    print("naming the user's language")
    language = sm.detect_language
    check("English is recognised", language("There is a bug in the parser. Find it and fix it.") == "English")
    check("Greek is recognised, with code names in it", language("Διόρθωσε το `get_words` στο words.py και τρέξε τα tests.") == "Greek")
    check("other languages are left unknown", [language(t) for t in ("Corrige le bogue dans ce fichier, merci.", "Behebe bitte den Fehler in dieser Datei.", "Corrige el error en este archivo.", "修复这个文件中的错误。")] == [None] * 4)
    check("too little text is left unknown", [language(t) for t in ("ok", "", "yes go")] == [None] * 3)
    latest = lambda *texts: sm.user_language([{"role": "user", "content": t} for t in texts])
    check("the latest message decides", latest("Fix the bug in this file please.", "Τώρα γράψε και τα tests για αυτό.") == "Greek")
    check("a short reply looks further back", latest("Γράψε ένα πρόγραμμα για αυτό.", "ok") == "Greek")
    check("a long message in another language is not mislabelled", latest("Fix the bug in this file please.", "Corrige le bogue dans ce fichier, merci.") is None)
    check("tool results and the repeat nudge are ignored", sm.user_language([{"role": "user", "content": "Γράψε ένα πρόγραμμα."}, {"role": "tool", "content": "the and of to is"}, {"role": "user", "content": "You repeated the same call; change approach."}]) == "Greek")
    for text in ("All done, nothing to do.", "", None, "The format is <tool_call> and it ends there.", xml + " and that is how you call a tool.",
                 "<tool_call>not json</tool_call>", '<tool_call>{"name": "run", "arguments": "ls"}</tool_call>', '<tool_call>{"name": ["run"], "arguments": {}}</tool_call>'):
        calls, rest = recover(text)
        check(f"not a call: {str(text)[:32]!r}", calls == [] and rest == text, calls)


def test_overnight_scoring():
    print("overnight scoring")
    sys.path.insert(0, os.path.join(ROOT, "sandbox"))
    import overnight as ov
    check("Greek with code names counts as Greek", ov.is_greek("Έφτιαξα το words.py με functions get_words και count_and_longest_word, και όλα περνούν."))
    check("a leaked tool call does not count as prose", ov.prose("<tool_call>\n<function=run>\nls\n</function>\n</tool_call>").strip() == "")
    check("English with a few Greek names does not", not ov.is_greek("The page shows the name Γιώργος and the title Κέντρο, and every other word here is plain English."))
    missing = r"(not exist|not found)"
    check("a missing file is understood in Greek", ov.says_impossible("Το αρχείο payments.py δεν υπάρχει στο project.", missing))
    check("and in English", ov.says_impossible("The file does not exist.", missing))
    check("a claim of success is not", not ov.says_impossible("Διόρθωσα το σφάλμα και όλα δουλεύουν.", missing) and not ov.says_impossible("Fixed the bug.", missing))
    check("example requests are not invented routes", ov.route_claims("Try `GET /api/invoices/5.csv` | 200\nUnknown path, e.g. `GET /api/nope`, gives 404\n`PATCH /api/clients/abc` gives 400\nSee /api/other") == {"/api/invoices/{id}.csv"})
    check("an invented route is found in a table", ov.route_claims("| GET | `/api/payments` | 200, 404 | server.py | h_list_payments |") == {"/api/payments"})
    check("and in a heading", ov.route_claims("### POST /api/refunds/{id}") == {"/api/refunds/{id}"})
    check("the mentioned routes cover examples too", ov.route_mentions("see /api/clients/3 and /api/invoices/<id>.csv.") == {"/api/clients/{id}", "/api/invoices/{id}.csv"})
    check("Batchline's limit is about 300 minutes", ov.LIMITS["B"] == 18000)


def git_cli(folder, *args):
    return subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@t", *args], cwd=folder, capture_output=True, text=True, check=True).stdout


def test_improvements(c, projects, cfg_path):
    sys.path.insert(0, ROOT)
    import studio as sm
    print("edits in parts")
    old, new = "a\nb\nc\nd\ne\nf\ng\nh\n", "ONE\nb\nc\nd\ne\nf\ng\nEIGHT\n"
    hunks = sm.hunks_for_approval(old, new)
    check("two separate changes give two parts", [h["line"] for h in hunks] == [1, 8] and hunks[0]["new"] == "ONE\n", hunks)
    check("one part can be applied alone", sm.merge_hunks(old, new, {1}) == "a\nb\nc\nd\ne\nf\ng\nEIGHT\n" and sm.merge_hunks(old, new, {0}) == "ONE\nb\nc\nd\ne\nf\ng\nh\n")
    check("both parts give the whole edit", sm.merge_hunks(old, new, {0, 1}) == new)
    check("no choice for a single change or a new file", sm.hunks_for_approval("a\n", "b\n") is None and sm.hunks_for_approval("", "b\n") is None)
    folder = os.path.join(projects, "improve")
    os.makedirs(folder)
    with open(os.path.join(folder, "parts.txt"), "w") as fh:
        fh.write("a\nb\nc\nd\ne\nf\ng\nh\n")
    ws = c.post("/api/workspace/open", {"path": folder})[1]["workspace"]["id"]
    session = c.post("/api/agent/sessions", {"ws": ws})[1]["id"]
    seen = {}
    status, data, _ = c.post("/api/agent/send", {"session": session, "ws": ws, "message": "Please write two parts", "model": "quartz:strata", "auto_apply": False, "auto_commands": True})
    events = []
    for name, ev in c.sse(f"/api/agent/stream?run={data['run']}", timeout=60):
        events.append(ev)
        if name == "approval":
            seen.update(ev)
            check("a bad parts value is refused", c.post("/api/agent/decision", {"run": data["run"], "approval_id": ev["approval_id"], "decision": "accept", "parts": "all"})[0] == 400)
            c.post("/api/agent/decision", {"run": data["run"], "approval_id": ev["approval_id"], "decision": "accept", "parts": [1]})
    written = open(os.path.join(folder, "parts.txt")).read()
    check("the approval lists the parts", len(seen.get("hunks") or []) == 2, seen.get("hunks"))
    check("only the chosen part is written", written == "a\nb\nc\nd\ne\nf\ng\nSEVEN\n", written)
    result = next(e for e in events if e["type"] == "tool_result")
    check("the model is told it was only partly applied", "Only part of this change" in result["output"] and result["ok"], result)
    check("the undo point holds the old text", any(e["type"] == "file_changed" for e in events))

    print("action cards")
    change = sm.describe_change("a\nb\nc\nd\ne\nf\ng\nh\ni\nj\n", "a\nB\nc\nd\ne\nf\ng\nh\ni\nJ\nk\n")
    check("a change counts its lines", change["added"] == 3 and change["removed"] == 2, change)
    check("it has numbered rows, with context around the change", ["ctx", 1, 1, "a"] in change["rows"] and ["del", 2, None, "b"] in change["rows"] and ["add", None, 2, "B"] in change["rows"] and ["add", None, 11, "k"] in change["rows"], change["rows"])
    far = sm.describe_change("".join(f"{i}\n" for i in range(50)), "".join(("X\n" if i in (2, 40) else f"{i}\n") for i in range(50)))
    check("far-apart changes get a separator and leave out the middle", [r[0] for r in far["rows"]].count("sep") == 1 and not any(r[3] == "20" for r in far["rows"]), far["rows"][:4])
    created = sm.describe_change(None, "x\ny\n")
    check("a new file is all additions", created["added"] == 2 and created["removed"] == 0)
    huge = sm.describe_change("a\n" * 2000, "b\n" * 2000)
    check("a huge file reports counts without a diff", huge["too_large"] and huge["rows"] == [])
    many = sm.describe_change("".join(f"{i}\n" for i in range(1400)), "".join(f"x{i}\n" for i in range(1400)))
    check("a very long diff is cut and says so", many.get("truncated") is True and len(many["rows"]) == sm.MAX_DIFF_ROWS)
    check("the agent has a delete tool", "delete_file" in {t["function"]["name"] for t in sm.TOOL_SCHEMAS})
    actions = os.path.join(projects, "actions")
    os.makedirs(os.path.join(actions, "sub"))
    with open(os.path.join(actions, "old.txt"), "w") as fh:
        fh.write("keep me safe\nsecond\n")
    actions_ws = c.post("/api/workspace/open", {"path": actions})[1]["workspace"]["id"]
    new_session = lambda: c.post("/api/agent/sessions", {"ws": actions_ws})[1]["id"]
    asked = []
    def refuse(ev):
        asked.append(ev)
        return "reject", ""
    events = run_agent(c, actions_ws, new_session(), "Please delete a file", on_approval=refuse)[1]
    result = next(e for e in events if e["type"] == "tool_result")
    check("deleting asks first, showing what goes", asked and asked[0]["action"] == "delete" and asked[0]["change"]["removed"] == 2, asked[:1])
    check("refusing keeps the file", os.path.exists(os.path.join(actions, "old.txt")) and result["ok"] is False and "rejected" in result["output"], result)
    events = run_agent(c, actions_ws, new_session(), "Please delete a file", auto_apply=True, auto_commands=True)[1]
    gone = next(e for e in events if e["type"] == "file_changed")
    check("deleting removes it and reports the change", not os.path.exists(os.path.join(actions, "old.txt")) and gone["action"] == "delete" and gone["change"]["removed"] == 2, gone)
    check("the file goes to the trash, not away", any("old.txt" in names for _, _, names in os.walk(os.path.join(actions, ".strata-studio", "trash"))))
    check("undo brings a deleted file back", c.post("/api/agent/undo", {"ws": actions_ws, "checkpoint": gone["checkpoint"]})[0] == 200 and open(os.path.join(actions, "old.txt")).read() == "keep me safe\nsecond\n")
    os.remove(os.path.join(actions, "old.txt"))
    missing = next(e for e in run_agent(c, actions_ws, new_session(), "Please delete a file", auto_apply=True)[1] if e["type"] == "tool_result")
    check("deleting a missing file fails plainly", missing["ok"] is False and "not found" in missing["output"], missing)
    os.makedirs(os.path.join(actions, "old.txt"))
    folder_try = next(e for e in run_agent(c, actions_ws, new_session(), "Please delete a file", auto_apply=True)[1] if e["type"] == "tool_result")
    check("a folder is refused", folder_try["ok"] is False and os.path.isdir(os.path.join(actions, "old.txt")), folder_try)
    os.rmdir(os.path.join(actions, "old.txt"))
    outside_file = os.path.join(projects, "outside-secret.txt")
    with open(outside_file, "w") as fh:
        fh.write("x\n")
    os.symlink(outside_file, os.path.join(actions, "old.txt"))
    link_try = next(e for e in run_agent(c, actions_ws, new_session(), "Please delete a file", auto_apply=True)[1] if e["type"] == "tool_result")
    check("a link is refused, and its target stays", link_try["ok"] is False and os.path.exists(outside_file) and os.path.islink(os.path.join(actions, "old.txt")), link_try)
    os.remove(os.path.join(actions, "old.txt"))
    edit_events = run_agent(c, actions_ws, new_session(), "Please make two edits", auto_apply=True, auto_commands=True)[1]
    made = next(e for e in edit_events if e["type"] == "file_changed")
    check("creating a file is reported as such, with its lines", made["action"] == "create" and made["change"]["added"] == 40, made["change"]["added"])
    ran = next(e for e in edit_events if e["type"] == "tool_result" and e["name"] == "run")
    check("a command's result keeps its output and exit code", ran["ok"] is False and ran["summary"] == "exit 4" and "9" in ran["output"], ran)
    sm_saved = sm.APP
    sm.APP = types.SimpleNamespace(cfg={"delete_tool": False, "skills": False, "plan_tool": True, "live_view": True, "mcp": True})
    off = sm.execute_tool(types.SimpleNamespace(note_progress=lambda value: None), "save_skill", {"name": "x"})
    check("with skills off, the model has no such tool", off[0] is False and "Unknown tool" in off[1] and "use_skill" not in off[2], off)
    try:
        ok, summary, output = sm.execute_tool(types.SimpleNamespace(note_progress=lambda value: None), "delete_file", {"path": "x"})
        check("with it off, delete is an unknown tool, and not offered", ok is False and summary == "Unknown tool" and "delete_file" not in output.split("Available")[1], output)
    finally:
        sm.APP = sm_saved

    print("the agent stays out of .git")
    git_cli(folder, "init", "-q")
    config_before = open(os.path.join(folder, ".git", "config")).read()
    session = c.post("/api/agent/sessions", {"ws": ws})[1]["id"]
    _, events = run_agent(c, ws, session, "Please touch git folder", auto_apply=True, auto_commands=True)
    result = next(e for e in events if e["type"] == "tool_result")
    check("writing to .git is refused", result["ok"] is False and ".git" in result["output"] and open(os.path.join(folder, ".git", "config")).read() == config_before, result)

    print("git routes")
    plain = os.path.join(projects, "plain")
    os.makedirs(os.path.join(plain, "sub"))
    plain_ws = c.post("/api/workspace/open", {"path": plain})[1]["workspace"]["id"]
    check("a plain folder is not a repository", c.get(f"/api/git/status?ws={plain_ws}")[1] == {"state": "none"})
    check("commit in a plain folder is refused", c.post("/api/git/commit", {"ws": plain_ws, "message": "x", "paths": ["a"]})[0] == 400)
    check("init works", c.post("/api/git/init", {"ws": plain_ws})[0] == 200 and c.get(f"/api/git/status?ws={plain_ws}")[1]["state"] == "repo")
    check("init twice is refused", c.post("/api/git/init", {"ws": plain_ws})[0] == 400)
    sub_ws = c.post("/api/workspace/open", {"path": os.path.join(plain, "sub")})[1]["workspace"]["id"]
    check("a subfolder is reported as inside a larger repository", c.get(f"/api/git/status?ws={sub_ws}")[1] == {"state": "parent"})
    for name, text in (("a.txt", "one\n"), ("b.txt", "two\n"), ("c d.txt", "three\n")):
        with open(os.path.join(plain, name), "w") as fh:
            fh.write(text)
    state = c.get(f"/api/git/status?ws={plain_ws}")[1]
    check("new files are listed, including a space in a name", {f["path"] for f in state["files"]} == {"a.txt", "b.txt", "c d.txt"} and all(f["untracked"] for f in state["files"]), state)
    check("an empty repository has no history", state["log"] == [])
    diff = c.get(f"/api/git/diff?ws={plain_ws}&path=a.txt")[1]["diff"]
    check("an untracked file shows as a diff", "+one" in diff, diff)
    check("a commit needs a message", c.post("/api/git/commit", {"ws": plain_ws, "message": " ", "paths": ["a.txt"]})[0] == 400)
    check("a commit needs paths", c.post("/api/git/commit", {"ws": plain_ws, "message": "x", "paths": []})[0] == 400)
    check("paths outside the folder are refused", c.post("/api/git/commit", {"ws": plain_ws, "message": "x", "paths": ["../improve/parts.txt"]})[0] == 403)
    check("the diff route refuses paths outside", c.get(f"/api/git/diff?ws={plain_ws}&path=../improve/parts.txt")[0] == 403)
    check("only the chosen files are committed", c.post("/api/git/commit", {"ws": plain_ws, "message": "first", "paths": ["a.txt", "c d.txt"]})[0] == 200)
    state = c.get(f"/api/git/status?ws={plain_ws}")[1]
    check("the rest stays uncommitted", [f["path"] for f in state["files"]] == ["b.txt"] and state["log"][0]["subject"] == "first", state)
    with open(os.path.join(plain, "a.txt"), "w") as fh:
        fh.write("changed\n")
    check("a changed file shows its diff", "+changed" in c.get(f"/api/git/diff?ws={plain_ws}&path=a.txt")[1]["diff"])
    check("a commit can be shown", "first" in c.get(f"/api/git/show?ws={plain_ws}&hash={state['log'][0]['hash']}")[1]["diff"])
    check("a bad commit id is refused", c.get(f"/api/git/show?ws={plain_ws}&hash=--output=x")[0] == 400)
    check("a bad branch name is refused", c.post("/api/git/branch", {"ws": plain_ws, "name": "--detach", "create": True})[0] == 400)
    os.remove(os.path.join(plain, "a.txt"))
    git_cli(plain, "checkout", "-q", "--", "a.txt")
    check("a branch can be created and listed", c.post("/api/git/branch", {"ws": plain_ws, "name": "topic/x", "create": True})[0] == 200 and c.get(f"/api/git/status?ws={plain_ws}")[1]["branch"] == "topic/x")
    marker = os.path.join(projects, "git-ran-a-program")
    script = os.path.join(projects, "evil.sh")
    with open(script, "w") as fh:
        fh.write(f"#!/bin/sh\ntouch {marker}\n")
    os.chmod(script, 0o755)
    git_cli(plain, "config", "core.fsmonitor", script)
    git_cli(plain, "config", "core.pager", script)
    git_cli(plain, "config", "diff.external", script)
    hook = os.path.join(plain, ".git", "hooks", "pre-commit")
    os.makedirs(os.path.dirname(hook), exist_ok=True)
    shutil.copy(script, hook)
    with open(os.path.join(plain, "b.txt"), "a") as fh:
        fh.write("more\n")
    c.get(f"/api/git/status?ws={plain_ws}")
    c.get(f"/api/git/diff?ws={plain_ws}&path=a.txt")
    done = c.post("/api/git/commit", {"ws": plain_ws, "message": "second", "paths": ["b.txt"]})[0]
    check("settings in .git/config and hooks do not run programs", done == 200 and not os.path.exists(marker), done)
    for key in ("core.fsmonitor", "core.pager", "diff.external"):
        git_cli(plain, "config", "--unset", key)
    os.remove(hook)

    driver = os.path.join(projects, "driver")
    os.makedirs(driver)
    git_cli(driver, "init", "-q")
    driver_ws = c.post("/api/workspace/open", {"path": driver})[1]["workspace"]["id"]
    check("a clean repository opens", c.get(f"/api/git/status?ws={driver_ws}")[0] == 200)
    git_cli(driver, "config", "filter.x.clean", script)
    with open(os.path.join(driver, ".gitattributes"), "w") as fh:
        fh.write("* filter=x\n")
    with open(os.path.join(driver, "a.txt"), "w") as fh:
        fh.write("x\n")
    refused = c.get(f"/api/git/status?ws={driver_ws}")
    check("on the host, a repository's own filter programs are refused, not run", refused[0] == 403 and "filter or merge" in refused[1]["error"] and not os.path.exists(marker), refused[:2])
    git_cli(driver, "config", "--unset", "filter.x.clean")
    git_cli(driver, "config", "merge.x.driver", script)
    check("merge programs too", c.get(f"/api/git/status?ws={driver_ws}")[0] == 403 and not os.path.exists(marker))
    git_cli(driver, "config", "--remove-section", "merge.x")
    included = os.path.join(projects, "included-config")
    with open(included, "w") as fh:
        fh.write(f"[filter \"x\"]\n\tclean = {script}\n")
    git_cli(driver, "config", "include.path", included)
    check("settings pulled in with include are seen", c.get(f"/api/git/status?ws={driver_ws}")[0] == 403 and not os.path.exists(marker))

    print("run branches")
    repo = os.path.join(projects, "branching")
    os.makedirs(repo)
    with open(os.path.join(repo, "README.md"), "w") as fh:
        fh.write("# Sample once\n")
    git_cli(repo, "init", "-q", "-b", "main")
    git_cli(repo, "add", "-A")
    git_cli(repo, "commit", "-q", "-m", "start")
    repo_ws = c.post("/api/workspace/open", {"path": repo})[1]["workspace"]["id"]

    def branch_run(message="Please rename the word"):
        session_id = c.post("/api/agent/sessions", {"ws": repo_ws})[1]["id"]
        status_, data_, _ = c.post("/api/agent/send", {"session": session_id, "ws": repo_ws, "message": message, "model": "quartz:strata", "auto_apply": True, "auto_commands": True, "git_branch": True})
        return [ev for _, ev in c.sse(f"/api/agent/stream?run={data_['run']}", timeout=60)]
    events = [e for e in branch_run() if e["type"] == "git_branch"]
    check("a run starts and ends on its own branch", [e["state"] for e in events] == ["started", "finished"] and events[1]["committed"] is True and events[0]["base"] == "main", events)
    branch = events[0]["branch"]
    check("its changes are committed there", "Demo" in git_cli(repo, "show", f"{branch}:README.md") and "Sample" in git_cli(repo, "show", "main:README.md"))
    check("only run branches can be thrown away this way", c.post("/api/git/run", {"ws": repo_ws, "action": "discard", "branch": "main", "base": branch})[0] == 400)
    check("a run branch can be thrown away", c.post("/api/git/run", {"ws": repo_ws, "action": "discard", "branch": branch, "base": "main"})[0] == 200 and "Sample" in open(os.path.join(repo, "README.md")).read() and branch not in git_cli(repo, "branch"))
    events = [e for e in branch_run() if e["type"] == "git_branch"]
    branch = events[0]["branch"]
    check("a run branch can be merged", c.post("/api/git/run", {"ws": repo_ws, "action": "merge", "branch": branch, "base": "main"})[0] == 200 and "Demo" in git_cli(repo, "show", "main:README.md") and branch not in git_cli(repo, "branch"))
    events = [e for e in branch_run("Please make secrets") if e["type"] == "git_branch"]
    check("secrets and package folders stay out of a run's commit", events[-1]["committed"] is True and git_cli(repo, "show", "--name-only", "--format=", events[0]["branch"]).split() == ["README.md"], git_cli(repo, "show", "--name-only", "--format=", events[0]["branch"]))
    c.post("/api/git/run", {"ws": repo_ws, "action": "discard", "branch": events[0]["branch"], "base": "main"})
    os.remove(os.path.join(repo, ".env"))
    shutil.rmtree(os.path.join(repo, "node_modules"))
    git_cli(repo, "switch", "-q", "-c", "feat+x@1")
    events = [e for e in branch_run() if e["type"] == "git_branch"]
    check("a branch with + and @ in its name still works as a base", events[0]["state"] == "started" and c.post("/api/git/run", {"ws": repo_ws, "action": "discard", "branch": events[0]["branch"], "base": "feat+x@1"})[0] == 200, events)
    git_cli(repo, "switch", "-q", "main")
    with open(os.path.join(repo, "README.md"), "a") as fh:
        fh.write("my own unsaved work\n")
    events = [e for e in branch_run() if e["type"] == "git_branch"]
    check("uncommitted work means no branch, and is left alone", len(events) == 1 and events[0]["state"] == "skipped" and "uncommitted" in events[0]["reason"] and git_cli(repo, "symbolic-ref", "--short", "HEAD").strip() == "main", events)

    print("test checks")
    check("test commands are detected", sm.detect_test_command(plain) is None)
    probe = os.path.join(projects, "probe")
    os.makedirs(os.path.join(probe, "tests"))
    write = lambda name, text: open(os.path.join(probe, name), "w").write(text)
    write("tests/test_x.py", "")
    check("python tests found", "unittest discover -s tests" in sm.detect_test_command(probe))
    write("pytest.ini", "")
    check("pytest preferred when configured", sm.detect_test_command(probe) == "python3 -m pytest -q")
    write("package.json", '{"scripts": {"test": "echo \\"Error: no test specified\\" && exit 1"}}')
    check("npm's placeholder test script is ignored", sm.detect_test_command(probe) == "python3 -m pytest -q")
    write("package.json", '{"scripts": {"test": "node --test"}}')
    check("npm test found", sm.detect_test_command(probe) == "npm test")
    write("Makefile", "build:\n\ttrue\ntest:\n\ttrue\n")
    check("make test found", sm.detect_test_command(probe) == "make test")
    write("STUDIO.md", "Rules.\n\nTest command: `./check.sh --fast`\n")
    check("STUDIO.md decides", sm.detect_test_command(probe) == "./check.sh --fast")
    write("STUDIO.md", "")
    write("package.json", "[" * 200000)
    check("a very deep package.json is harmless", sm.detect_test_command(probe) == "make test")
    check("a broken package.json is harmless", (write("STUDIO.md", ""), write("Makefile", ""), write("package.json", "{"), sm.detect_test_command(probe))[3] == "python3 -m pytest -q")
    checked = os.path.join(projects, "checked")
    os.makedirs(checked)
    with open(os.path.join(checked, "README.md"), "w") as fh:
        fh.write("# Sample once\n")
    with open(os.path.join(checked, "Makefile"), "w") as fh:
        fh.write("test:\n\t@echo failing on purpose; exit 3\n")
    checked_ws = c.post("/api/workspace/open", {"path": checked})[1]["workspace"]["id"]
    def checked_run(**kwargs):
        return run_agent(c, checked_ws, c.post("/api/agent/sessions", {"ws": checked_ws})[1]["id"], "Please rename the word", **kwargs)[1]
    events = checked_run(auto_apply=True, auto_commands=True)
    found = [e for e in events if e["type"] == "check"]
    check("Studio runs the tests and reports failure beside the claim", len(found) == 1 and found[0]["status"] == "failed" and found[0]["claimed"] is True and "failing on purpose" in found[0]["output"], found)
    check("the run still ends normally", events[-1]["type"] == "done" and events[-1]["reason"] == "finished", events[-1])
    with open(os.path.join(checked, "README.md"), "w") as fh:
        fh.write("# Sample once\n")
    asked = []
    def refuse_check(ev):
        asked.append(ev)
        return ("reject", "") if ev.get("purpose") == "check" else ("accept", "")
    events = checked_run(auto_apply=True, on_approval=refuse_check)
    found = [e for e in events if e["type"] == "check"]
    check("without auto-run the tests ask for approval first", any(e.get("purpose") == "check" and e["command"] == "make test" for e in asked) and found and found[0]["status"] == "skipped", found)
    events = run_agent(c, checked_ws, c.post("/api/agent/sessions", {"ws": checked_ws})[1]["id"], "hello there")[1]
    check("a plain answer with no changes runs nothing", not [e for e in events if e["type"] == "check"])

    print("reminders and trimming")
    run = types.SimpleNamespace(turns=1, session={"messages": [{"role": "user", "content": "Rule: use Greek docstrings."}]})
    saved = sm.APP
    sm.APP = types.SimpleNamespace(cfg={"rule_reminders": True, "rule_reminder_turns": 20})
    try:
        check("no reminder early in a one-message chat", sm.earlier_instructions(run) is None)
        run.turns = 20
        check("a reminder after many turns", "Greek docstrings" in sm.earlier_instructions(run))
        run.turns = 1
        run.session["messages"] += [{"role": "assistant", "content": "ok"}, {"role": "user", "content": "You repeated the same call; change approach."}, {"role": "user", "content": "Second request."}]
        text = sm.earlier_instructions(run)
        check("a reminder once there are two requests, without the nudge", "Greek docstrings" in text and "Second request" in text and "You repeated" not in text, text)
        run.session["messages"] = [{"role": "user", "content": f"request number {i}."} for i in range(12)]
        text = sm.earlier_instructions(run)
        check("long chats keep the first and newest requests", "number 0." in text and "number 1." in text and "number 11." in text and "number 5." not in text, text)
        sm.APP.cfg["rule_reminders"] = False
        check("the setting turns it off", sm.earlier_instructions(run) is None)
    finally:
        sm.APP = saved
    big = "\n".join(f"line {i}" for i in range(20000))
    messages = []
    for i, (name, args) in enumerate([("run", {"command": "make test"}), ("read_file", {"path": "x.py"}), ("search", {"query": "foo"})] + [("list_files", {"path": "."})] * 6):
        messages += [{"role": "assistant", "content": "", "tool_calls": [{"id": f"c{i}", "function": {"name": name, "arguments": json.dumps(args)}}]}, {"role": "tool", "tool_call_id": f"c{i}", "content": big}]
    sm.trim_context(messages, smart=True)
    tools = [m["content"] for m in messages if m["role"] == "tool"]
    check("a trimmed command keeps its command and last lines", "make test" in tools[0] and "line 19999" in tools[0] and len(tools[0]) < 1000, tools[0][:200])
    check("a trimmed read keeps the path and advice", "x.py" in tools[1] and "read it again" in tools[1] and len(tools[1]) < 1000)
    check("the newest outputs stay whole", tools[-1] == big and tools[-6] == big)
    again = list(tools)
    sm.trim_context(messages, smart=True)
    check("trimming twice changes nothing", [m["content"] for m in messages if m["role"] == "tool"] == again)

    small = [{"role": "assistant", "content": "", "tool_calls": [{"id": f"s{i}", "function": {"name": "search", "arguments": "{}"}}]} if i % 2 == 0 else {"role": "tool", "tool_call_id": f"s{i - 1}", "content": "y" * 1000} for i in range(40)]
    sm.trim_context(small, smart=True)
    check("the normal limit leaves short chats alone", all(m["content"] == "y" * 1000 for m in small if m["role"] == "tool"))
    sm.trim_context(small, smart=True, limit=5000)
    check("a lower limit trims them", sum(m["content"].startswith("[older output trimmed") for m in small if m["role"] == "tool") == 14)

    print("chat report")
    session_id = c.post("/api/agent/sessions", {"ws": repo_ws})[1]["id"]
    run_agent(c, repo_ws, session_id, "Please rename the word", auto_apply=True, auto_commands=True)
    status, body, response = c.get(f"/api/agent/session/export?id={session_id}")
    text = body.decode() if isinstance(body, bytes) else str(body)
    check("a chat exports as a Markdown file", status == 200 and "attachment" in response.getheader("Content-Disposition") and response.getheader("Content-Type").startswith("text/markdown"), status)
    check("the report has the request, tools and outcome", "Please rename the word" in text and "replace_in_file" in text and "Run ended: finished" in text, text[:300])
    check("the report leaves out private reasoning", "I should start by exploring" not in text)
    check("another chat's id is refused", c.get("/api/agent/session/export?id=nope")[0] in (400, 404))

    print("backups")
    backup = c.post("/api/admin/backups", {})
    check("a backup can be made", backup[0] == 200 and backup[1]["name"].startswith("studio-data-"), backup[:2])
    listing = c.get("/api/admin/backups")[1]
    path = os.path.join(listing["folder"], listing["backups"][0]["name"])
    check("backups are private", oct(os.stat(path).st_mode)[-3:] == "600" and oct(os.stat(listing["folder"]).st_mode)[-3:] == "700")
    import tarfile as tf
    with tf.open(path) as archive:
        names = archive.getnames()
    check("a backup holds the accounts and chats", "data/users.json" in names and any(n.startswith("data/sessions/") for n in names), names[:5])
    fake = os.path.join(projects, "fake-data", "data")
    os.makedirs(fake)
    keep_cfg = os.path.join(projects, "fake-data", "cfg.json")
    with open(keep_cfg, "w") as fh:
        json.dump({"host": "127.0.0.1", "port": free_port()}, fh)
    with open(os.path.join(fake, "marker"), "w") as fh:
        fh.write("old")
    cli = lambda *args: subprocess.run([sys.executable, os.path.join(ROOT, "studio.py"), "--config", keep_cfg, *args], capture_output=True, text=True)
    done = cli("--restore", path)
    restored = os.path.join(projects, "fake-data", "data")
    aside = [n for n in os.listdir(os.path.join(projects, "fake-data")) if n.startswith("data.before-restore")]
    check("restore puts the backup in place and keeps the old data aside", done.returncode == 0 and os.path.exists(os.path.join(restored, "users.json")) and aside and os.path.exists(os.path.join(projects, "fake-data", aside[0], "marker")), done.stdout + done.stderr)
    evil = os.path.join(projects, "not-a-backup.tar.gz")
    with tf.open(evil, "w:gz") as archive:
        archive.add(script, arcname="../escape")
    no_dir = os.path.join(projects, "no-dir.tar.gz")
    with tf.open(no_dir, "w:gz") as archive:
        archive.add(script, arcname="data/users.json")
    before = sorted(os.listdir(os.path.join(projects, "fake-data")))
    check("an archive with no data folder is refused and moves nothing", cli("--restore", no_dir).returncode == 1 and sorted(os.listdir(os.path.join(projects, "fake-data"))) == before)
    check("restore refuses another archive", cli("--restore", evil).returncode == 1 and not os.path.exists(os.path.join(projects, "escape")))
    made = cli("--backup")
    check("--backup works from the command line", made.returncode == 0 and made.stdout.startswith("Wrote"), made.stdout + made.stderr)
    guard = os.path.join(projects, "fake-data", "cfg2.json")
    with open(guard, "w") as fh:
        json.dump({"host": "127.0.0.1", "port": json.load(open(cfg_path))["port"]}, fh)
    busy = subprocess.run([sys.executable, os.path.join(ROOT, "studio.py"), "--config", guard, "--restore", path], capture_output=True, text=True)
    check("restore refuses while Studio is running", busy.returncode == 1 and "Stop it first" in busy.stderr, busy.stderr)
    folder2 = os.path.join(projects, "fake-data2", "data")
    os.makedirs(folder2)
    for _ in range(4):
        sm.make_backup(folder2, 2)
        time.sleep(1.05)
    check("only the newest backups are kept", len(sm.list_backups(folder2)) == 2)
    context = next(e for e in run_agent(c, repo_ws, c.post("/api/agent/sessions", {"ws": repo_ws})[1]["id"], "hello there")[1] if e["type"] == "usage")
    check("usage carries the context size when known", "context_tokens" in context, context)


def test_sync(c, projects, st_port, gw_port):
    print("sync between windows")
    import threading
    folder = os.path.join(projects, "synced")
    os.makedirs(folder)
    with open(os.path.join(folder, "README.md"), "w") as fh:
        fh.write("# Sample once\n")
    ws = c.post("/api/workspace/open", {"path": folder})[1]["workspace"]["id"]
    check("settings start empty or readable", c.get("/api/prefs")[0] == 200)
    for key, value in (("theme", "neon"), ("auto_apply", "yes"), ("nonsense", True), ("session:../../x", "a"), ("model", "x" * 500), ("last_ws", 5)):
        check(f"bad setting refused: {key}", c.post("/api/prefs", {"key": key, "value": value})[0] == 400)
    for key, value in (("theme", "sepia"), ("auto_commands", False), ("model", "quartz:strata"), (f"session:{ws}", "abc"), (f"git_branch:{ws}", True), ("last_ws", folder)):
        check(f"setting saved: {key}", c.post("/api/prefs", {"key": key, "value": value})[0] == 200)
    check("settings come back for any window", c.get("/api/prefs")[1]["prefs"]["theme"] == "sepia")
    other = Client(st_port)
    other.ip = "10.0.0.77"
    other.post("/api/auth/signup", {"token": c.post("/api/admin/invites", {})[1]["token"], "username": "syncer", "password": "a long enough password"})
    check("another user has their own settings", other.get("/api/prefs")[1]["prefs"] == {}, other.get("/api/prefs")[1])
    seen, theirs = [], []

    def follow(client, into):
        try:
            for name, ev in client.sse("/api/queue/stream", timeout=8):
                if name == "sync":
                    into.append(ev)
        except OSError:
            pass
    for client, into in ((c, seen), (other, theirs)):
        threading.Thread(target=follow, args=(client, into), daemon=True).start()
    time.sleep(0.5)
    c.post("/api/prefs", {"key": "theme", "value": "light"})
    c.post("/api/files/write", {"ws": ws, "path": "note.txt", "content": "x"})
    opened = c.post("/api/term/open", {"ws": ws, "cols": 80, "rows": 24})[1]
    listing = c.get(f"/api/term/list?ws={ws}")[1]["terminals"]
    check("a terminal is listed for other windows", any(t["id"] == opened["id"] and t["n"] == opened["n"] for t in listing), listing)
    check("not for another user", other.get(f"/api/term/list?ws={ws}")[0] in (403, 404))
    session = c.post("/api/agent/sessions", {"ws": ws})[1]["id"]
    run_agent(c, ws, session, "hello there")
    c.post("/api/term/close", {"id": opened["id"]})
    check("a closed terminal leaves the list", not any(t["id"] == opened["id"] for t in c.get(f"/api/term/list?ws={ws}")[1]["terminals"]))
    time.sleep(1)
    kinds_seen = [e["type"] for e in seen]
    check("every kind of change reaches other windows", {"prefs", "files", "terminals", "sessions", "run_started"} <= set(kinds_seen), kinds_seen)
    started = next((e for e in seen if e["type"] == "run_started"), {})
    check("a new run says where to find it", started.get("ws") == ws and started.get("session") == session and started.get("run"), started)
    check("nothing reaches another user", theirs == [], theirs)
    # long polling, for tunnels that hold streams back
    check("polling requires login", Client(st_port).get("/api/queue/poll", cookie=False)[0] == 401)
    first = c.get("/api/queue/poll")[1]
    check("polling answers at once with the state", first["events"][0]["type"] == "state" and "sync" in first["cursor"], first)
    their_first = other.get("/api/queue/poll")[1]
    c.post("/api/prefs", {"key": "wrap", "value": False})
    cursor = first["cursor"]
    news = c.get(f"/api/queue/poll?message={cursor['message']}&sync={cursor['sync']}")[1]
    check("a change reaches a polling window", any(e["type"] == "sync" and e["data"]["type"] == "prefs" for e in news["events"]), news)
    cursor = their_first["cursor"]
    news = other.get(f"/api/queue/poll?message={cursor['message']}&sync={cursor['sync']}")[1]
    check("a polling window gets nothing of another user's", not [e for e in news["events"] if e["type"] == "sync"], news)
    polled_run = c.post("/api/agent/send", {"session": session, "ws": ws, "message": "hello there", "model": "quartz:strata", "auto_apply": True, "auto_commands": True})[1]["run"]
    check("polling another user's run is refused", other.get(f"/api/agent/poll?run={polled_run}")[0] == 404)
    polled, after, ended = [], 0, False
    for _ in range(100):
        reply = c.get(f"/api/agent/poll?run={polled_run}&after={after}")[1]
        polled += reply["events"]
        after = reply["cursor"]["after"]
        if reply.get("end"):
            ended = True
            break
    check("polling follows a run to its end", ended and polled and polled[-1]["type"] == "done", polled[-1:])
    check("polling gives each event once, in order", [e["data"]["seq"] for e in polled] == list(range(1, len(polled) + 1)))
    check("and the same events as the stream", [e["type"] for e in polled] == [name for name, _ in c.sse(f"/api/agent/stream?run={polled_run}", timeout=30)])
    check("a run already ended still answers", c.get(f"/api/agent/poll?run={polled_run}&after={after}")[1].get("end") is True)
    polled_term = c.post("/api/term/open", {"ws": ws, "cols": 80, "rows": 24})[1]["id"]
    check("polling another user's terminal is refused", other.get(f"/api/term/poll?id={polled_term}")[0] == 404)
    c.post("/api/term/input", {"id": polled_term, "data": "echo polled-$((40+2))\nexit\n"})
    output, sent, exit_events = b"", "", []
    for _ in range(100):
        reply = c.get(f"/api/term/poll?id={polled_term}&sent={sent}")[1]
        output += b"".join(base64.b64decode(e["data"]["b64"]) for e in reply["events"] if e["type"] == "data")
        exit_events += [e for e in reply["events"] if e["type"] == "exit"]
        sent = reply["cursor"]["sent"]
        if reply.get("end"):
            break
    check("terminal output arrives by polling", b"polled-42" in output, output[-200:])
    check("and so does the end of the shell", len(exit_events) == 1, exit_events)
    c.post("/api/term/close", {"id": polled_term})
    # skills the model saves for itself
    import studio as sm
    good_skill = {"name": "A skill", "kind": "testing", "description": "When.", "instructions": "Steps."}
    for label, wrong in (("an unknown kind", {"kind": "magic"}), ("a long name", {"name": "x" * 81}), ("a two-line name", {"name": "two\nlines"}), ("no steps", {"instructions": "  "})):
        try:
            sm.tool_save_skill(None, {**good_skill, **wrong})
            check(f"{label} is refused", False)
        except sm.ApiError as exc:
            check(f"{label} is refused", exc.status == 400, exc.message)
    asked_before = len(c_get_json(gw_port, "/_mock/requests")["requests"])
    check("no skills to begin with", c.get("/api/skills")[1]["skills"] == [])
    skill_session = c.post("/api/agent/sessions", {"ws": ws})[1]["id"]
    refused = run_agent(c, ws, skill_session, "Please learn a skill", on_approval=lambda ev: ("reject", ""))[1]
    check("a skill asks first, like an edit", next(e for e in refused if e["type"] == "approval")["kind"] == "skill")
    check("a refused skill is not saved", c.get("/api/skills")[1]["skills"] == [] and not [e for e in refused if e["type"] == "skill_saved"])
    asked_for = []

    def accept_skill(ev):
        asked_for.append(ev)
        return "accept", ""
    events = run_agent(c, ws, skill_session, "Please learn a skill", on_approval=accept_skill)[1]
    check("the approval shows the whole skill", asked_for[0]["skill_kind"] == "testing" and "Run each command" in asked_for[0]["instructions"], asked_for[0])
    saved = next((e for e in events if e["type"] == "skill_saved"), {})
    check("saving tells the chat its kind and name", saved.get("skill_kind") == "testing" and saved.get("name") == "Check a README" and saved.get("updated") is False, saved)
    loaded = [e for e in events if e["type"] == "tool_result" and e["name"] == "use_skill"]
    check("the model can load it again, whatever the capitals, even with its kind copied from the list", loaded and loaded[0]["ok"] and "Run each command" in loaded[0]["output"], loaded)
    listed = c.get("/api/skills")[1]["skills"]
    check("the Skills list has it, with its use counted", len(listed) == 1 and listed[0]["uses"] == 1 and listed[0]["skill_kind"] == "testing", listed)
    before_run = c_get_json(gw_port, "/_mock/requests")["requests"][asked_before:]
    check("saving does not change the system prompt within a run", len({r["system"] for r in before_run[-3:]}) == 1)
    check("another user does not have it", other.get("/api/skills")[1]["skills"] == [])
    check("nor can they delete it", other.post("/api/skills/delete", {"name": "Check a README"})[0] == 404)
    again = run_agent(c, ws, skill_session, "Please learn a skill", auto_apply=True, auto_commands=True)[1]
    check("auto-apply saves without asking, and says it replaced the old one", not [e for e in again if e["type"] == "approval"] and next(e for e in again if e["type"] == "skill_saved")["updated"] is True)
    check("replacing keeps one skill", len(c.get("/api/skills")[1]["skills"]) == 1)
    run_agent(c, ws, skill_session, "hello after the skill")
    since = c_get_json(gw_port, "/_mock/requests")["requests"][asked_before:]
    line = "- Check a README (testing): When a README must match the code."
    check("later runs hear of it in a note beside a new message, not in the system prompt",
          any(line in r["last_user"] and r["last_user"].startswith("Studio context") for r in since) and not any(line in r["system"] for r in since), [r["last_user"][:80] for r in since][-3:])
    check("and the system prompt of this chat never changed", len({r["system"] for r in since}) == 1, len({r["system"] for r in since}))
    check("deleting needs a name", c.post("/api/skills/delete", {})[0] == 400)
    check("deleting removes it", c.post("/api/skills/delete", {"name": "check a readme"})[0] == 200 and c.get("/api/skills")[1]["skills"] == [])
    mine = {"name": "Read the README first", "kind": "workflow", "description": "Before any change.", "instructions": "1. Read it.\n2. Follow it."}
    saved = c.post("/api/skills/save", mine)
    check("the user can write a skill", saved[0] == 200 and saved[1]["skill"]["skill_kind"] == "workflow" and saved[1]["skill"]["folder"] == "" and saved[1]["skill"]["uses"] == 0, saved[:2])
    check("by the model's rules: a known kind", c.post("/api/skills/save", {**mine, "kind": "magic"})[0] == 400)
    check("a name of one line", c.post("/api/skills/save", {**mine, "name": "a\nb"})[0] == 400)
    check("and some steps", c.post("/api/skills/save", {**mine, "instructions": "  "})[0] == 400)
    check("a new skill cannot take an existing name", c.post("/api/skills/save", {**mine, "name": "read the readme FIRST"})[0] == 409)
    created = saved[1]["skill"]["created"]
    edited = c.post("/api/skills/save", {**mine, "name": "Read the README", "description": "Always.", "old_name": "Read the README first"})
    listed = c.get("/api/skills")[1]["skills"]
    check("editing can rename it, keeping when it was made", edited[0] == 200 and [s["name"] for s in listed] == ["Read the README"] and listed[0]["description"] == "Always." and listed[0]["created"] == created, listed)
    c.post("/api/skills/save", {**mine, "name": "Second skill"})
    check("an edit cannot take another skill's name", c.post("/api/skills/save", {**mine, "name": "second skill", "old_name": "Read the README"})[0] == 409)
    check("editing a skill that is gone is not found", c.post("/api/skills/save", {**mine, "old_name": "Nothing like it"})[0] == 404)
    check("another user's list is their own", other.get("/api/skills")[1]["skills"] == [] and other.post("/api/skills/save", {**mine, "old_name": "Read the README"})[0] == 404)
    for name in ("Read the README", "Second skill"):
        c.post("/api/skills/delete", {"name": name})
    conn = http.client.HTTPConnection("127.0.0.1", st_port, timeout=10)
    conn.request("POST", "/api/prefs", json.dumps({"key": "wrap", "value": True}), {"X-Studio": "1", "X-Studio-Client": "window-7", "Cookie": c.cookie, "Content-Type": "application/json"})
    conn.getresponse().read()
    time.sleep(0.5)
    check("a change names the window that made it, so it can ignore it", any(e["type"] == "prefs" and e.get("origin") == "window-7" for e in seen), seen[-3:])


def test_plan(c, projects, gw_port):
    print("plan tool")
    sys.path.insert(0, ROOT)
    import studio as sm
    folder = os.path.join(projects, "planned")
    os.makedirs(folder)
    with open(os.path.join(folder, "README.md"), "w") as fh:
        fh.write("# Planned\n")
    ws = c.post("/api/workspace/open", {"path": folder})[1]["workspace"]["id"]
    asked_before = len(c_get_json(gw_port, "/_mock/requests")["requests"])
    events = run_agent(c, ws, c.post("/api/agent/sessions", {"ws": ws})[1]["id"], "Please make a plan", auto_apply=True, auto_commands=True)[1]
    plans = [e for e in events if e["type"] == "plan"]
    check("the plan reaches the chat as its own event, with numbering and bullets removed",
          len(plans) == 1 and plans[0]["steps"] == ["Look at the files", "Read README.md", "Finish"] and plans[0]["revised"] is False, plans)
    check("it comes before the work it plans", kinds(events).index("plan") < [i for i, e in enumerate(events) if e["type"] == "tool_call" and e["name"] == "list_files"][0])
    result = next(e for e in events if e["type"] == "tool_result" and e["name"] == "plan")
    check("the model is told the user sees it and how to report progress", result["ok"] and "3 steps" in result["output"] and '"1/3"' in result["output"], result)
    progress = [e["args"].get("progress") for e in events if e["type"] == "tool_call"]
    check("each tool call keeps its progress for the card", progress[:3] == ["1/3", "1/3", "2/3"], progress)
    asked = c_get_json(gw_port, "/_mock/requests")["requests"][asked_before:]
    check("the model is offered the plan tool", all("plan" in r["tool_names"] for r in asked if r["tools"]))
    check("and told to plan with it", all(sm.PLAN_RULE in r["system"] and sm.PLAN_OLD_RULE not in r["system"] for r in asked))
    check("the plan does not change the system prompt", len({r["system"] for r in asked}) == 1)
    session = c.post("/api/agent/sessions", {"ws": ws})[1]["id"]
    events = run_agent(c, ws, session, "Please make a plan and revise it", auto_apply=True, auto_commands=True)[1]
    plans = [e for e in events if e["type"] == "plan"]
    check("a second plan in the same run is marked as revised", [p["revised"] for p in plans] == [False, True] and len(plans[1]["steps"]) == 4, plans)
    stored = c.get(f"/api/agent/session?id={session}")[1]
    check("plans are kept in the chat for later", sum(e["type"] == "plan" for e in stored.get("events", [])) == 2, list(stored)[:5])
    events = run_agent(c, ws, c.post("/api/agent/sessions", {"ws": ws})[1]["id"], "Please make a plan as text", auto_apply=True, auto_commands=True)[1]
    check("steps sent as one string, a step per line, also work", next(e for e in events if e["type"] == "plan")["steps"] == ["Look at the files", "Read README.md", "Finish"])
    events = run_agent(c, ws, c.post("/api/agent/sessions", {"ws": ws})[1]["id"], "Please make a plan and miscount", auto_apply=True, auto_commands=True)[1]
    outputs = [e["output"] for e in events if e["type"] == "tool_result" and e["name"] in ("list_files", "read_file")]
    check("a progress total that disagrees with the plan gets a note, once", len(outputs) == 2 and 'your plan has 3 steps, so set progress as "n/3"' in outputs[0] and "Studio note" not in outputs[1], [o[-120:] for o in outputs])
    counted = types.SimpleNamespace(plan=["a", "b", "c"], progress=None)
    sm.Run.note_progress(counted, "5/8")
    check("the plan's length is the total, whatever the model counts", counted.progress[:2] == (3, 3), counted.progress)
    counted.plan = None
    sm.Run.note_progress(counted, "5/8")
    check("without a plan the model's own count stands", counted.progress[:2] == (5, 8), counted.progress)
    run = types.SimpleNamespace(plan=None, emit=lambda *a, **k: None, note_progress=lambda value: None)
    sm_saved = sm.APP
    sm.APP = types.SimpleNamespace(cfg={"delete_tool": True, "skills": True, "plan_tool": True, "live_view": True, "mcp": True})
    for label, bad in (("no steps", []), ("a number", 3), ("blank steps", ["  ", "-"]), ("too many steps", [f"step {i}" for i in range(21)])):
        check(f"a plan with {label} is refused", sm.execute_tool(run, "plan", {"steps": bad})[0] is False)
    mixed = sm.execute_tool(run, "plan", {"steps": ["Εξαγωγή κειμένου", "Έλεγχος με το γreek-check", "Δοκιμές με το greek-check"]})
    check("a step mixing Greek and Latin letters in one word is sent back unseen", mixed[0] is False and "step 2 has «γreek»" in mixed[2]
          and "Call plan again" in mixed[2] and "step 3" not in mixed[2] and run.plan is None, mixed)
    shown = []
    run.emit = lambda kind, **k: shown.append(kind)
    cjk = sm.execute_tool(run, "plan", {"steps": ["Κώδικας: μουσική, loweringduring朗读, κουμπί", "Τρέιλερ: έλεγχος_CLEAR της αφήγησης", "Run `npm test` in tests/"]})
    check("Chinese in a step, or Greek and Latin joined by _, sends the plan back unseen", cjk[0] is False and shown == ["guard_blocked"]
          and "«loweringduring朗读»" in cjk[2] and "«έλεγχος_CLEAR»" in cjk[2] and "step 3" not in cjk[2], cjk)
    run.emit = lambda *a, **k: None
    check("a clean plan gets none", "Studio note" not in sm.execute_tool(run, "plan", {"steps": ["Έλεγχος με το greek-check", "Run the tests"]})[2])
    long = sm.execute_tool(run, "plan", {"steps": ["x" * 500]})
    check("a long step is shortened", long[0] and len(run.plan[0]) == sm.MAX_PLAN_STEP, long)
    sm.APP.cfg["plan_tool"] = False
    try:
        off = sm.execute_tool(run, "plan", {"steps": ["a"]})
        check("with plan_tool off, plan is an unknown tool", off[0] is False and off[1] == "Unknown tool" and "plan" not in off[2].split("Available tools:")[1], off)
    finally:
        sm.APP = sm_saved


def question_run(c, ws, message, answer):
    """Runs a message whose questions are answered by answer(event) -> (decision, answers); returns (events, refusals)."""
    run = c.post("/api/agent/send", {"session": c.post("/api/agent/sessions", {"ws": ws})[1]["id"], "ws": ws, "message": message,
                                      "model": "quartz:strata", "auto_apply": True, "auto_commands": True})[1]["run"]
    events, refusals = [], []
    for name, ev in c.sse(f"/api/agent/stream?run={run}", timeout=60):
        events.append(ev)
        if name == "approval":
            for bad in ([{"choices": ["Purple"]}, {"choices": ["Home"]}], [{"choices": ["Blue", "Red"]}, {"choices": ["Home"]}],
                        [{"choices": [], "text": " "}, {"choices": ["Home"]}], [{"choices": ["Blue"]}]):
                if len(ev["questions"]) == 2:
                    refusals.append(c.post("/api/agent/decision", {"run": run, "approval_id": ev["approval_id"], "decision": "accept", "answers": bad})[0])
            decision, answers = answer(ev)
            if decision == "stop":
                c.post("/api/agent/stop", {"run": run})
                continue
            c.post("/api/agent/decision", {"run": run, "approval_id": ev["approval_id"], "decision": decision, "answers": answers})
    return events, refusals


def test_questions(c, projects, gw_port):
    print("questions from the agent")
    sys.path.insert(0, ROOT)
    import studio as sm
    folder = os.path.join(projects, "questions")
    os.makedirs(folder)
    ws = c.post("/api/workspace/open", {"path": folder})[1]["workspace"]["id"]
    events, refusals = question_run(c, ws, "Please ask me", lambda ev: ("accept", [{"choices": ["Red"], "text": ""}, {"choices": ["Home", "Help"], "text": "and the footer"}]))
    asked = next(e for e in events if e["type"] == "approval")
    check("the questions reach the chat as a card, even with auto modes on", asked["kind"] == "question" and len(asked["questions"]) == 2, asked)
    check("choices are cleaned of numbering, keep their descriptions, and multi is kept",
          [o["label"] for o in asked["questions"][0]["options"]] == ["Blue", "Green", "Red"] and asked["questions"][0]["options"][2]["description"] == "Stands out most"
          and asked["questions"][0]["multi"] is False and asked["questions"][1]["multi"] is True, asked["questions"])
    check("answers that pick an unknown choice, two for a one-choice question, nothing, or the wrong count are refused", refusals == [400, 400, 400, 400], refusals)
    resolved = next(e for e in events if e["type"] == "approval_resolved")
    check("the answers are kept with the card", resolved["answers"][1] == {"choices": ["Home", "Help"], "text": "and the footer"}, resolved)
    done = events[-1]
    check("the model gets each question with its answer", done["reason"] == "finished" and "1. Which colour should the button be?\n   Answer: Red" in done["summary"]
          and "Answer: Home, Help; in their own words: and the footer" in done["summary"], done)
    events, _ = question_run(c, ws, "Please ask me", lambda ev: ("reject", None))
    check("a skipped question tells the model to use its own judgment", "The user skipped the questions" in events[-1].get("summary", ""), events[-1])
    events, _ = question_run(c, ws, "Please ask me one", lambda ev: ("accept", [{"choices": [], "text": "Orange, please"}]))
    check("one question sent on its own works, and an answer can be only the user's words",
          len(next(e for e in events if e["type"] == "approval")["questions"]) == 1 and "in their own words: Orange, please" in events[-1].get("summary", ""), events[-1])
    events, _ = question_run(c, ws, "Please ask me", lambda ev: ("stop", None))
    check("stopping while a question waits ends the run", events[-1]["type"] == "done" and events[-1]["reason"] == "stopped", events[-1])
    events, _ = question_run(c, ws, "Please ask in mixed letters", lambda ev: ("accept", [{"choices": ["Δάσος"], "text": ""}]))
    cards = [e for e in events if e["type"] == "approval"]
    sent_back = next((e for e in events if e["type"] == "tool_result" and e["name"] == "ask_user" and not e["ok"]), None)
    check("a question card with a word mixing alphabets goes back to the model unseen", sent_back and "potάμι" in sent_back["output"] and "not shown" in sent_back["output"], sent_back)
    check("and the fixed card is the only one the user sees", len(cards) == 1 and "ποτάμι" in json.dumps(cards[0]["questions"], ensure_ascii=False), cards)
    written = run_agent(c, ws, c.post("/api/agent/sessions", {"ws": ws})[1]["id"], "Please write mixed letters", auto_apply=True)[1]
    result = next(e for e in written if e["type"] == "tool_result" and e["name"] == "write_file")
    check("a file written with a word mixing alphabets is saved, and the model is told which words, a Greek letter hidden in code too",
          result["ok"] and "story.md now has «potάμι», «mapΑ»" in result["output"] and os.path.exists(os.path.join(folder, "story.md")), result["output"][-300:])
    with open(os.path.join(folder, "old.py"), "w") as fh:
        fh.write("y = [\n")                     # already broken before the run: an edit that keeps it broken is not named
    written = run_agent(c, ws, c.post("/api/agent/sessions", {"ws": ws})[1]["id"], "Please write broken code", auto_apply=True)[1]
    outs = [e["output"] for e in written if e["type"] == "tool_result" and e["name"] in ("write_file", "replace_in_file")]
    check("an edit that leaves Python unparsable says where, with the line", len(outs) == 5 and "Studio checked calc.py: it no longer parses on line 2 (`return a +`)" in outs[0], outs[:1])
    check("the fix, a file broken before, and a good JS module get no note", all("Studio checked" not in o for o in outs[1:4]), outs[1:4])
    check("broken JSON is named too", "Studio checked data.json" in outs[4] and "invalid JSON" in outs[4], outs[4:])
    check("the parsers stay quiet on good files and tsconfig's comments",
          sm.file_problem("a.py", "x = 1\n") is None and sm.file_problem("tsconfig.json", "{ // c\n}") is None
          and sm.file_problem("b.sh", "if true; then\necho\n")[0] == 3 and sm.file_problem("c.js", "const a = ;\n")[0] == 1)
    for label, bad in (("one choice", [{"question": "x", "options": ["a"]}]), ("five questions", [{"question": "x", "options": ["a", "b"]}] * 5),
                       ("no text", [{"options": ["a", "b"]}]), ("seven choices", [{"question": "x", "options": list("abcdefg")}]),
                       ("the same choice twice", [{"question": "x", "options": ["a", "a"]}])):
        try:
            sm.clean_questions(bad)
            check(f"questions with {label} are refused", False)
        except sm.ApiError:
            check(f"questions with {label} are refused", True)
    asked = c_get_json(gw_port, "/_mock/requests")["requests"]
    check("the model is offered ask_user", any("ask_user" in r["tool_names"] for r in asked if r["tools"]))


def test_rewind_branch(c, projects):
    print("rewind and branch")
    folder = os.path.join(projects, "rewound")
    os.makedirs(folder)
    readme = os.path.join(folder, "README.md")
    with open(readme, "w") as fh:
        fh.write("# Sample project\n")
    ws = c.post("/api/workspace/open", {"path": folder})[1]["workspace"]["id"]
    sid = c.post("/api/agent/sessions", {"ws": ws})[1]["id"]
    for message in ("hello", "Please rename the word", "hello again"):
        run_agent(c, ws, sid, message, auto_apply=True, auto_commands=True)
    check("the agent changed the file", "Demo" in open(readme).read())
    before = json.load(open(os.path.join(projects, "..", "app", "data", "sessions", sid + ".json")))
    users = [e for e in before["events"] if e["type"] == "user"]
    check("each message records where it starts in the model's messages", [e.get("mi") for e in users][0] == 0 and all(isinstance(e.get("mi"), int) for e in users), [e.get("mi") for e in users])
    status, branch, _ = c.post("/api/agent/session/branch", {"ws": ws, "id": sid, "user": 1})
    copy = c.get(f"/api/agent/session?id={branch['id']}")[1]
    check("a branch is a new chat with everything before the message", status == 200 and branch["title"].startswith("Branch: ")
          and [e["text"] for e in copy["events"] if e["type"] == "user"] == ["hello"] and copy["events"][-1]["type"] == "done", copy["events"][-1:])
    whole = c.post("/api/agent/session/branch", {"ws": ws, "id": sid, "user": 3})[1]
    check("a branch at the end copies the whole chat", len(c.get(f"/api/agent/session?id={whole['id']}")[1]["events"]) == len(before["events"]))
    events = run_agent(c, ws, branch["id"], "hello from the branch")[1]
    branched = json.load(open(os.path.join(projects, "..", "app", "data", "sessions", branch["id"] + ".json")))
    check("a branch carries on by itself", events[-1]["reason"] == "answered" and len([m for m in branched["messages"] if m["role"] == "user"]) == 2
          and len(c.get(f"/api/agent/session?id={sid}")[1]["events"]) == len(before["events"]), branched["messages"][-2:])
    check("an unknown message is refused", c.post("/api/agent/session/rewind", {"ws": ws, "id": sid, "user": 7})[0] == 400
          and c.post("/api/agent/session/branch", {"ws": ws, "id": sid, "user": -1})[0] == 400)
    status, r, _ = c.post("/api/agent/session/rewind", {"ws": ws, "id": sid, "user": 1, "undo_files": True})
    after = json.load(open(os.path.join(projects, "..", "app", "data", "sessions", sid + ".json")))
    check("rewind gives the message back and removes it and everything after", status == 200 and r["text"] == "Please rename the word"
          and [e["text"] for e in after["events"] if e["type"] == "user"] == ["hello"] and len(after["messages"]) == users[1]["mi"], (r, len(after["messages"])))
    check("with undo_files, the agent's changes after it are undone", r["restored"] == ["README.md"] and open(readme).read() == "# Sample project\n", r)
    check("the saved prompt is built again after a cut", "system_prompt" not in after)
    events = run_agent(c, ws, sid, "hello once more")[1]
    check("the chat goes on after a rewind", events[-1]["reason"] == "answered" and len(c.get(f"/api/agent/session?id={sid}")[1]["events"]) > len(after["events"]))
    # Chats from before this change have no recorded starts: the user's messages are counted instead.
    path = os.path.join(projects, "..", "app", "data", "sessions", sid + ".json")
    old = json.load(open(path))
    for e in old["events"]:
        e.pop("mi", None)
    json.dump(old, open(path, "w"))
    status, r, _ = c.post("/api/agent/session/rewind", {"ws": ws, "id": sid, "user": 1})
    check("an older chat is cut by counting its messages", status == 200 and r["text"] == "hello once more"
          and [m["content"] for m in json.load(open(path))["messages"] if m["role"] == "user"] == ["hello"], r)
    sid2 = c.post("/api/agent/sessions", {"ws": ws})[1]["id"]
    run_id = c.post("/api/agent/send", {"session": sid2, "ws": ws, "message": "Please work slowly", "model": "quartz:strata", "auto_apply": True, "auto_commands": True})[1]["run"]
    busy = c.post("/api/agent/session/rewind", {"ws": ws, "id": sid2, "user": 0})[0], c.post("/api/agent/session/branch", {"ws": ws, "id": sid2, "user": 0})[0]
    for _ in c.sse(f"/api/agent/stream?run={run_id}", timeout=60):
        pass
    check("a chat with a run going cannot be rewound or branched", busy == (409, 409), busy)


def test_plan_mode(c, projects):
    print("plan first")
    sys.path.insert(0, ROOT)
    import studio as sm
    folder = os.path.join(projects, "planfirst")
    os.makedirs(folder)
    readme = os.path.join(folder, "README.md")
    with open(readme, "w") as fh:
        fh.write("# Sample\n")
    ws = c.post("/api/workspace/open", {"path": folder})[1]["workspace"]["id"]
    sid = c.post("/api/agent/sessions", {"ws": ws})[1]["id"]
    status, data, _ = c.post("/api/agent/send", {"session": sid, "ws": ws, "message": "Please plan the rename", "model": "quartz:strata",
                                                 "auto_apply": True, "auto_commands": True, "plan_mode": True})
    events, asked = [], []
    for name, ev in c.sse(f"/api/agent/stream?run={data['run']}", timeout=60):
        events.append(ev)
        if name == "approval":
            asked.append(ev)
            c.post("/api/agent/settings", {"run": data["run"], "auto_apply": True, "auto_commands": True})     # switching auto-run on again does not answer it
            time.sleep(0.3)
            c.post("/api/agent/decision", {"run": data["run"], "approval_id": ev["approval_id"], "decision": "reject"})
    results = {e["id"]: e for e in events if e["type"] == "tool_result"}
    calls = [e for e in events if e["type"] == "tool_call"]
    edit = results[next(e["id"] for e in calls if e["name"] == "write_file")]
    check("in plan first, an edit is refused even with auto-apply on", edit["ok"] is False and "Plan mode is on" in edit["output"] and open(readme).read() == "# Sample\n", edit)
    look = results[next(e["id"] for e in calls if e["name"] == "run" and e["args"]["command"] == "git status")]
    check("a command that only looks runs without asking", not any(a.get("command") == "git status" for a in asked) and look["summary"] != "Command skipped", look)
    check("one that could change things asks, even with auto-run on, and a toggle does not answer it",
          [a.get("command") for a in asked] == ["touch made.txt"] and not os.path.exists(os.path.join(folder, "made.txt")), asked)
    touch = results[next(e["id"] for e in calls if e["name"] == "run" and e["args"]["command"] == "touch made.txt")]
    check("and the model is told why it did not run", "Plan mode is on, and the user did not allow" in touch["output"], touch)
    done = events[-1]
    check("the run ends with the plan's steps for the page", done.get("plan_mode") is True and done["plan_steps"] == ["Rename Sample to Demo in README.md", "Run the tests"], done)
    stored = json.load(open(os.path.join(projects, "..", "app", "data", "sessions", sid + ".json")))
    check("the message is marked, and the model gets the plan note with it", stored["events"][0].get("plan_mode") is True
          and stored["messages"][0]["content"].startswith("Plan first is on for this message") and sm.typed_part(stored["messages"][0]["content"]) == "Please plan the rename", stored["messages"][0]["content"][:120])
    events = run_agent(c, ws, sid, "Carry out the plan above.", auto_apply=True, auto_commands=True)[1]
    check("the next message, without plan first, makes the changes", events[-1]["reason"] == "finished" and "Demo" in open(readme).read() and "plan_mode" not in events[-1], events[-1])
    run = types.SimpleNamespace(plan=None)
    check("without a plan call, the numbered lines of the reply are the steps", sm.plan_steps(run, "Plan:\n1. Read it\n2) Fix it\nThat is all.") == ["Read it", "Fix it"])
    for command, ok in (("ls -la src | wc -l", True), ("git log -5 --oneline", True), ("grep -rn foo .", True), ("find . -name '*.py'", True),
                        ("cat a > b", False), ("find . -delete", False), ("sort -o out in", False), ("rg --pre=x foo", False), ("echo $(id)", False),
                        ("git branch new", False), ("git -c core.pager=x log", False), ("ls; rm x", False), ("npm test", False), ("uniq a b", False),
                        ("grep -rn foo . 2>&1 | head", True), ("cat a 2>&1 > b", False), ("echo 2>&1x", False),
                        ("pwd; ls -la", True), ("cd src && ls", True), ("ls || pwd", True), ("ls; rm -rf x", False), ("ls & rm x", False), ("cd src && npm i", False),
                        ('grep -rn TODO . 2>/dev/null | head -20; echo "exit=$?"', True), ("ls > /dev/null", True), ("ls > notes.txt", False), ("echo $(rm x)", False), ("cat /dev/null > a", False)):
        check(f"{command!r} {'only looks' if ok else 'could change things'}", sm.looks_only(command) is ok)
    english_with_greek = ("All three pieces are done. The search now treats sigma forms alike, so a prefix works. " * 6
                          + "Checks: «Φόνισ», «φόνισσα», «φόνις», «Η ΦΟΝΙΣΣΑ», «καπλανι», «ΚΑΠΛΑΝΑΣ», «πριγκιπας», «Η Φόνισσα», «Το Καπλάνι της Βιτρίνας».")
    check("an English reply that quotes Greek examples is not sent back as mixed", sm.mixed_scripts(english_with_greek, greek_chat=False) == ""
          and "Latin letters" in sm.mixed_scripts(english_with_greek))
    check("the project's own test command counts as looking, with its output piped to tail", sm.looks_only("python3 -m unittest -q 2>&1 | tail -20", "python3 -m unittest -q")
          and not sm.looks_only("python3 -m unittest -q; rm -rf x", "python3 -m unittest -q") and not sm.looks_only("python3 -m unittest -q 2>&1 | tail -20"))


def test_compact(c, projects, gw_port):
    print("/compact and long messages")
    sys.path.insert(0, ROOT)
    import studio as sm
    folder = os.path.join(projects, "compacted")
    os.makedirs(folder)
    with open(os.path.join(folder, "README.md"), "w") as fh:
        fh.write("# Sample\n")
    ws = c.post("/api/workspace/open", {"path": folder})[1]["workspace"]["id"]
    sid = c.post("/api/agent/sessions", {"ws": ws})[1]["id"]
    short = c.post("/api/agent/compact", {"ws": ws, "session": sid})[1]["run"]
    events = [ev for _, ev in c.sse(f"/api/agent/stream?run={short}", timeout=30)]
    check("a short chat is left alone", events[-1]["reason"] == "answered" and any("nothing to summarize" in e.get("text", "") for e in events), events[-2:])
    for message in ("hello", "Please rename the word"):
        run_agent(c, ws, sid, message, auto_apply=True, auto_commands=True)
    path = os.path.join(projects, "..", "app", "data", "sessions", sid + ".json")
    shown_before = len(json.load(open(path))["events"])
    run = c.post("/api/agent/compact", {"ws": ws, "session": sid})[1]["run"]
    events = [ev for _, ev in c.sse(f"/api/agent/stream?run={run}", timeout=30)]
    compacted = next((e for e in events if e["type"] == "compacted"), None)
    check("/compact ends with the summary and the sizes before and after", events[-1]["reason"] == "compacted" and compacted
          and compacted["summary"].startswith("Summary: the user asked") and type(compacted["before"]) is int and type(compacted["after"]) is int, compacted)
    check("the summary is not streamed into the chat as a reply", not any(e["type"] in ("token", "thinking") for e in events))
    after = json.load(open(path))
    check("the model's messages become the summary; the chat the user sees stays", len(after["messages"]) == 2 and after["messages"][0]["content"].startswith(sm.COMPACT_INTRO)
          and [e["type"] for e in after["events"][shown_before:]] == ["usage", "compacted", "done"], [e["type"] for e in after["events"][shown_before:]])
    before_requests = len(c_get_json(gw_port, "/_mock/requests")["requests"])
    events = run_agent(c, ws, sid, "hello again")[1]
    sent = c_get_json(gw_port, "/_mock/requests")["requests"][before_requests:]
    check("the next message goes on from the summary", events[-1]["reason"] == "answered" and sent and sent[0]["n_messages"] == 4, [r["n_messages"] for r in sent])
    check("the chat cannot be rewound to before the summary", c.post("/api/agent/session/rewind", {"ws": ws, "id": sid, "user": 1})[0] == 409)
    status, r, _ = c.post("/api/agent/session/rewind", {"ws": ws, "id": sid, "user": 2})
    check("but a message after it can", status == 200 and r["text"] == "hello again" and len(json.load(open(path))["messages"]) == 2, r)
    long_text = "Please read all of this. " + "word " * 5000
    run_id = c.post("/api/agent/send", {"session": sid, "ws": ws, "message": long_text, "model": "quartz:strata", "as_file": True, "auto_apply": True, "auto_commands": True})[1]["run"]
    for _ in c.sse(f"/api/agent/stream?run={run_id}", timeout=30):
        pass
    stored = json.load(open(path))
    event = [e for e in stored["events"] if e["type"] == "user"][-1]
    saved = os.path.join(folder, event.get("saved_as", "missing"))
    check("a message saved as a file is kept whole in the folder", event.get("saved_as", "").startswith(".strata-studio/messages/") and open(saved).read() == long_text, event.get("saved_as"))
    check("the model is told where it is and to read it in parts", event["text"].endswith("…") and len(event["text"]) < 500
          and sm.typed_part(stored["messages"][event["mi"]]["content"]).startswith(f"My message is long, so Studio saved it as the file {event['saved_as']}"), stored["messages"][event["mi"]]["content"][:200])


def test_turn_guard(c, projects, gw_port):
    print("turn guard")
    folder = os.path.join(projects, "guarded")
    os.makedirs(folder)
    ws = c.post("/api/workspace/open", {"path": folder})[1]["workspace"]["id"]

    def guarded(message):
        before = len(c_get_json(gw_port, "/_mock/requests")["requests"])
        events = run_agent(c, ws, c.post("/api/agent/sessions", {"ws": ws})[1]["id"], message, auto_apply=True, auto_commands=True)[1]
        return events, c_get_json(gw_port, "/_mock/requests")["requests"][before:]
    events, sent = guarded("Please fail a lot")
    done = events[-1]
    noted = [i for i, r in enumerate(sent) if "your last 4 tool calls all failed" in r.get("all_user", "")]
    check("calls that keep failing get one note after 4, and the run stops after 8", done["reason"] == "stuck" and done["stuck"] == "8 tool calls in a row failed"
          and done["turns"] == 8 and noted and noted[0] == 4, (done, noted))
    events, sent = guarded("Please replan forever")
    check("a plan written again and again gets a note, then stops", events[-1]["reason"] == "stuck" and events[-1]["stuck"] == "the plan was rewritten 7 times"
          and any("written the plan 4 times" in r.get("all_user", "") for r in sent), events[-1])
    events, _ = guarded("Please say nothing")
    check("a run that ends without a word is marked, so the page can say so", events[-1]["reason"] == "answered" and events[-1].get("silent") is True, events[-1])
    events = run_agent(c, ws, c.post("/api/agent/sessions", {"ws": ws})[1]["id"], "Please skip commands", on_approval=lambda ev: ("reject", ""))[1]
    check("commands the user rejects do not count as failures", events[-1]["reason"] == "finished" and sum(e["type"] == "approval" for e in events) == 9, events[-1])


def test_run_files(c, projects):
    print("files a run changed")
    folder = os.path.join(projects, "swept")
    os.makedirs(os.path.join(folder, "node_modules"))
    for name, text in (("README.md", "# Sample\n"), ("old.txt", "old\n")):
        with open(os.path.join(folder, name), "w") as fh:
            fh.write(text)
    ws = c.post("/api/workspace/open", {"path": folder})[1]["workspace"]["id"]

    def files_of(message):
        events = run_agent(c, ws, c.post("/api/agent/sessions", {"ws": ws})[1]["id"], message, auto_apply=True, auto_commands=True)[1]
        found = [e for e in events if e["type"] == "files"]
        return (found[0] if found else None), events
    found, events = files_of("Please rename the word")
    check("an edit shows in the run's files, with its counts", found and found["files"] == [{"path": "README.md", "status": "changed", "added": 1, "removed": 1}]
          and found["total"] == 1, found)
    check("the files come before the end of the run", [e["type"] for e in events].index("files") < len(events) - 1)
    found, _ = files_of("Please make secrets")
    paths = {f["path"]: f["status"] for f in found["files"]} if found else {}
    check("files written by commands are found too, but not in folders such as node_modules", paths == {".env": "new", "README.md": "changed"}, paths)
    found, _ = files_of("Please delete a file")
    check("a deleted file is listed as deleted", found and {"path": "old.txt", "status": "deleted", "added": 0, "removed": 1} in found["files"], found)
    found, _ = files_of("Please edit back and forth")
    check("a file edited twice shows its net change, not the edits added up", found and {"path": "notes.txt", "status": "new", "added": 1, "removed": 0} in found["files"], found)
    found, _ = files_of("hello")
    check("a run that changed nothing has no files card", found is None)


def test_goal(c, projects, gw_port):
    print("/goal")
    sys.path.insert(0, ROOT)
    import studio as sm
    folder = os.path.join(projects, "goals")
    os.makedirs(folder)
    ws = c.post("/api/workspace/open", {"path": folder})[1]["workspace"]["id"]

    def goal_run(message, sid=None, **extra):
        sid = sid or c.post("/api/agent/sessions", {"ws": ws})[1]["id"]
        run = c.post("/api/agent/send", {"session": sid, "ws": ws, "message": message, "model": "quartz:strata", "auto_apply": True,
                                         "auto_commands": True, "goal": True, **extra})[1]["run"]
        return sid, [ev for _, ev in c.sse(f"/api/agent/stream?run={run}", timeout=90)]
    sid, events = goal_run("Please reach the goal of two parts")
    goals = [e for e in events if e["type"] == "goal"]
    done = events[-1]
    check("a goal not reached is sent back, and ends when the model says GOAL COMPLETE", done["reason"] == "finished" and done["summary"].startswith("GOAL COMPLETE")
          and [g["state"] for g in goals] == ["active", "done"] and goals[0]["rounds"] == 1 and done["goal"]["state"] == "done", (done, goals))
    stored = json.load(open(os.path.join(projects, "..", "app", "data", "sessions", sid + ".json")))
    check("the goal is kept with the chat, and the model got the goal with the message", stored["goal"]["state"] == "done"
          and stored["messages"][0]["content"].startswith("Goal for this chat: Please reach the goal of two parts")
          and stored["events"][0].get("goal") is True, stored["goal"])
    check("the chat returns its goal", c.get(f"/api/agent/session?id={sid}")[1]["goal"]["state"] == "done")
    _, events = goal_run("Please goal blocked")
    check("BLOCKED stops and says why", events[-1]["goal"]["state"] == "blocked" and events[-1]["goal"]["why"] == "I need the API key for the payment service.", events[-1])
    sid, events = goal_run("Please goal forever")
    rounds = [e for e in events if e["type"] == "goal"]
    check("a goal never reached stops after the round limit", events[-1]["goal"]["state"] == "paused" and events[-1]["goal"]["rounds"] == 20
          and "20 rounds" in events[-1]["goal"]["why"], events[-1].get("goal"))
    sid3, events = goal_run("Please goal forever\n\nDone when:\n- never", goal_parts={"goal": "Please goal forever", "done": "- never", "limits": ""}, goal_rounds=3)
    check("the Goal window's round limit and parts are kept and used", events[-1]["goal"]["state"] == "paused" and events[-1]["goal"]["rounds"] == 3
          and events[-1]["goal"]["max_rounds"] == 3 and events[-1]["goal"]["parts"] == {"goal": "Please goal forever", "done": "- never"}, events[-1].get("goal"))
    brief = "Please goal forever. " + "Step with many details. " * 200
    sid4, events = goal_run(brief + "\n\nDone when:\n- the book opens", goal_parts={"goal": brief, "done": "- the book opens"}, goal_rounds=2)
    stored = json.load(open(os.path.join(projects, "..", "app", "data", "sessions", sid4 + ".json")))
    notes = [m["content"] for m in stored["messages"] if m.get("role") == "user" and "the goal is not reached yet" in str(m.get("content"))]
    check("a long goal is taken whole and repeated in short each round, with what done means", len(brief) > 4000 and notes
          and all(len(n) < 1500 and "- the book opens" in n and "in full in the message that set it" in n for n in notes)
          and brief.strip() in stored["messages"][0]["content"], [len(n) for n in notes])
    check("a round limit outside 1 to 100 is refused", c.post("/api/agent/send", {"session": c.post("/api/agent/sessions", {"ws": ws})[1]["id"], "ws": ws, "message": "x",
          "model": "quartz:strata", "goal": True, "goal_rounds": 500})[0] == 400)
    r = c.post("/api/agent/goal", {"ws": ws, "id": sid3, "action": "edit", "text": "Better goal", "goal_parts": {"goal": "Better goal"}, "goal_rounds": 7})[1]
    check("editing from the window changes the parts and the limit too", r["goal"]["parts"] == {"goal": "Better goal"} and r["goal"]["max_rounds"] == 7, r)
    for action, state in (("resume", "active"), ("pause", "paused")):
        check(f"{action} sets the goal {state}", c.post("/api/agent/goal", {"ws": ws, "id": sid, "action": action})[1]["goal"]["state"] == state)
    status, r, _ = c.post("/api/agent/goal", {"ws": ws, "id": sid, "action": "edit", "text": "A new goal"})
    stored = json.load(open(os.path.join(projects, "..", "app", "data", "sessions", sid + ".json")))
    check("an edit changes the goal and tells the model", r["goal"]["text"] == "A new goal" and stored["messages"][-1]["content"].endswith("Goal from now on: A new goal"), r)
    check("clear removes it", c.post("/api/agent/goal", {"ws": ws, "id": sid, "action": "clear"})[1]["goal"] is None
          and c.post("/api/agent/goal", {"ws": ws, "id": sid, "action": "pause"})[0] == 404)
    check("a wrong action is refused", c.post("/api/agent/goal", {"ws": ws, "id": sid, "action": "explode"})[0] == 400)


def test_tasks(c, projects):
    print("scheduled tasks and loops")
    sys.path.insert(0, ROOT)
    import studio as sm
    # Schedules, in local time: Wednesday 2026-11-11 10:30.
    wed = time.mktime((2026, 11, 11, 10, 30, 0, 0, 0, -1))
    at = lambda *a: time.mktime((*a, 0, 0, 0, -1))
    for label, schedule, expected in (("daily, later today", {"kind": "daily", "time": "11:00"}, at(2026, 11, 11, 11, 0)),
                                      ("daily, already past", {"kind": "daily", "time": "08:00"}, at(2026, 11, 12, 8, 0)),
                                      ("weekdays skip the weekend", {"kind": "weekdays", "time": "08:00"}, at(2026, 11, 12, 8, 0)),
                                      ("weekly on Monday", {"kind": "weekly", "time": "09:00", "weekday": 0}, at(2026, 11, 16, 9, 0)),
                                      ("hourly", {"kind": "hourly", "time": "00:15"}, at(2026, 11, 11, 11, 15)),
                                      ("every 30 minutes", {"kind": "every", "minutes": 30}, wed + 1800)):
        check(f"next run: {label}", sm.next_run(schedule, wed) == expected, (time.ctime(sm.next_run(schedule, wed)), time.ctime(expected)))
    fri = at(2026, 11, 13, 9, 0)
    check("next run: weekdays after Friday is Monday", sm.next_run({"kind": "weekdays", "time": "08:00"}, fri) == at(2026, 11, 16, 8, 0))
    for text, minutes, prompt, limits in (("10m check the build, stop after 3 answers", 10, "check the build", {"max_runs": 3}),
                                          ("2h look at the logs", 120, "look at the logs", {}),
                                          ("5 minutes run the tests, stop when it says passed", 5, "run the tests", {"stop_when": "passed"}),
                                          ("1m ping, stop after twice", 1, "ping", {"max_runs": 2})):
        got = sm.parse_loop(text)
        check(f"/loop {text!r}", got[0] == minutes and got[1] == prompt and got[2] == limits, got)
    span = sm.parse_loop("15m watch the deploy for 2 hours")
    check("/loop ... for 2 hours ends in two hours", span[1] == "watch the deploy" and abs(span[2]["until"] - time.time() - 7200) < 5, span)
    for bad in ("check things", "10m", "10m stop after 3"):
        try:
            sm.parse_loop(bad)
            check(f"/loop {bad!r} is refused", False)
        except sm.ApiError:
            check(f"/loop {bad!r} is refused", True)
    folder = os.path.join(projects, "tasked")
    os.makedirs(folder)
    readme = os.path.join(folder, "README.md")
    with open(readme, "w") as fh:
        fh.write("# Sample\n")
    ws = c.post("/api/workspace/open", {"path": folder})[1]["workspace"]["id"]

    def task_of(tid):
        return next(t for t in c.get("/api/tasks")[1]["tasks"] if t["id"] == tid)

    def wait_done(tid, runs, timeout=40):
        deadline = time.time() + timeout
        while time.time() < deadline:
            t = task_of(tid)
            if t.get("runs", 0) >= runs and not t.get("running"):
                return t
            time.sleep(0.3)
        return task_of(tid)

    for label, bad in (("no prompt", {"prompt": "", "schedule": {"kind": "daily", "time": "08:00"}}), ("a bad time", {"prompt": "x", "schedule": {"kind": "daily", "time": "25:00"}}),
                       ("a weekly task without a day", {"prompt": "x", "schedule": {"kind": "weekly", "time": "08:00"}}), ("an unknown mode", {"prompt": "x", "mode": "yolo", "schedule": {"kind": "daily", "time": "08:00"}})):
        check(f"a task with {label} is refused", c.post("/api/tasks/save", {"ws": ws, "task": bad})[0] == 400)
    status, saved, _ = c.post("/api/tasks/save", {"ws": ws, "task": {"title": "Ask first", "prompt": "Please rename the word", "mode": "ask", "schedule": {"kind": "daily", "time": "06:00"}}})
    tid = saved["task"]["id"]
    check("a task is saved with its next run", status == 200 and saved["task"]["next_run"] > time.time() and saved["task"]["enabled"] is True, saved)
    check("Run now starts it", c.post("/api/tasks/action", {"id": tid, "action": "run"})[0] == 200)
    t = wait_done(tid, 1)
    check("a run with nobody there skips what needs a yes, and says so", t["last"]["refused"] == ["change README.md", "run the command `echo ok`"] and open(readme).read() == "# Sample\n"
          and t["last"]["reason"] == "finished", t.get("last"))
    chat = c.get(f"/api/agent/session?id={t['last']['session']}")[1]
    check("each run has its own chat, marked as a task", chat["events"][0].get("task") == tid and any(e["type"] == "approval_resolved" and e["decision"] == "reject" for e in chat["events"]))
    status, saved, _ = c.post("/api/tasks/save", {"ws": ws, "task": {"id": tid, "title": "Do it", "prompt": "Please rename the word", "mode": "auto", "schedule": {"kind": "weekdays", "time": "06:00"}}})
    c.post("/api/tasks/action", {"id": tid, "action": "run"})
    t = wait_done(tid, 2)
    check("in Do everything, the edit is made and the result lists the file", "Demo" in open(readme).read() and t["last"]["refused"] == [] and t["last"]["files"] == ["README.md"]
          and len(t["history"]) == 2 and t["schedule"]["kind"] == "weekdays", t.get("last"))
    with open(readme, "w") as fh:
        fh.write("# Sample\n")
    c.post("/api/tasks/save", {"ws": ws, "task": {"id": tid, "prompt": "Please plan the rename", "mode": "look", "schedule": {"kind": "weekdays", "time": "06:00"}}})
    c.post("/api/tasks/action", {"id": tid, "action": "run"})
    t = wait_done(tid, 3)
    check("Look only changes nothing, and a command that could change things is skipped", open(readme).read() == "# Sample\n" and not os.path.exists(os.path.join(folder, "made.txt"))
          and t["last"]["refused"] == ["run the command `touch made.txt`"], t.get("last"))
    check("pause and resume", c.post("/api/tasks/action", {"id": tid, "action": "pause"})[0] == 200 and task_of(tid)["enabled"] is False
          and c.post("/api/tasks/action", {"id": tid, "action": "resume"})[0] == 200 and task_of(tid)["enabled"] is True)
    check("delete", c.post("/api/tasks/action", {"id": tid, "action": "delete"})[0] == 200 and not any(x["id"] == tid for x in c.get("/api/tasks")[1]["tasks"]))
    sid = c.post("/api/agent/sessions", {"ws": ws})[1]["id"]
    status, loop, _ = c.post("/api/tasks/loop", {"ws": ws, "session": sid, "text": "1m hello from the loop, stop after 1 answer"})
    check("/loop makes a loop for the chat", status == 200 and loop["task"]["loop"]["session"] == sid and loop["task"]["loop"]["max_runs"] == 1 and loop["task"]["schedule"] == {"kind": "every", "minutes": 1}, loop)
    t = wait_done(loop["task"]["id"], 1, timeout=30)
    events = c.get(f"/api/agent/session?id={sid}")[1]["events"]
    check("the timer starts it in that chat, and its own rule ends it", [e["text"] for e in events if e["type"] == "user"] == ["hello from the loop"]
          and t["enabled"] is False and t["loop"]["runs"] == 1, (t.get("loop"), t.get("enabled")))
    check("a loop needs an interval", c.post("/api/tasks/loop", {"ws": ws, "session": sid, "text": "check things"})[0] == 400)


def test_timeskip(c, projects, tmp):
    print("Time-skip")
    folder = os.path.join(projects, "skipped")
    os.makedirs(folder)
    readme = os.path.join(folder, "README.md")
    for name, text in (("README.md", "# Sample\n"), ("Makefile", "test:\n\tgrep -q Demo README.md\n")):
        with open(os.path.join(folder, name), "w") as fh:
            fh.write(text)
    ws = c.post("/api/workspace/open", {"path": folder})[1]["workspace"]["id"]

    def skip(message="Please rename the word", during=None):
        sid = c.post("/api/agent/sessions", {"ws": ws})[1]["id"]
        status, data, _ = c.post("/api/timeskip/start", {"ws": ws, "session": sid, "message": message, "model": "quartz:strata", "auto_commands": True})
        assert status == 200, data
        if during:
            during(sid)
        events = [ev for _, ev in c.sse(f"/api/agent/stream?run={data['run']}", timeout=60)]
        return sid, data["id"], events
    busy = []
    sid, jid, events = skip(during=lambda sid: busy.append(c.post("/api/agent/send", {"session": sid, "ws": ws, "message": "hello", "model": "quartz:strata"})[0]))
    card = next(e for e in events if e["type"] == "timeskip")
    check("the agent works on a copy: your folder is untouched", open(readme).read() == "# Sample\n" and all(e.get("copy") for e in events if e["type"] == "file_changed"), card)
    check("the card lists what differs, with the change", card["state"] == "ready" and [f["path"] for f in card["files"]] == ["README.md"]
          and card["files"][0]["change"]["added"] == 1 and card["files"][0]["status"] == "changed", card["files"])
    check("the tests run on both versions", card["checks"]["command"] == "make test" and card["checks"]["original"]["ok"] is False and card["checks"]["copy"]["ok"] is True, card["checks"])
    check("the chat cannot take another message while it works", busy == [409], busy)
    check("no files card or test check of its own", not any(e["type"] in ("files", "check") for e in events))
    status, kept, _ = c.post("/api/timeskip/decide", {"id": jid, "action": "keep"})
    check("Keep writes the update into your folder, with an undo point", status == 200 and "Demo" in open(readme).read() and kept["files"] == ["README.md"] and kept["checkpoint"], kept)
    check("and the copy is removed", not os.path.exists(os.path.join(tmp, "app", "data", "timeskip", jid, "work")))
    check("a decided Time-skip cannot be decided again", c.post("/api/timeskip/decide", {"id": jid, "action": "keep"})[0] == 409)
    stored = c.get(f"/api/agent/session?id={sid}")[1]["events"]
    check("the decision is kept in the chat", stored[-1]["type"] == "timeskip_decided" and stored[-1]["action"] == "keep")
    check("Undo puts your folder back", c.post("/api/agent/undo", {"ws": ws, "checkpoint": kept["checkpoint"]})[0] == 200 and open(readme).read() == "# Sample\n")
    _, jid, _ = skip()
    status, gone, _ = c.post("/api/timeskip/decide", {"id": jid, "action": "discard"})
    check("Throw away leaves your folder as it was", status == 200 and open(readme).read() == "# Sample\n" and not os.path.exists(os.path.join(tmp, "app", "data", "timeskip", jid, "work")))
    _, jid, _ = skip()
    with open(readme, "w") as fh:
        fh.write("# Sample, edited meanwhile\n")
    status, err, _ = c.post("/api/timeskip/decide", {"id": jid, "action": "keep"})
    check("a file you changed meanwhile is not overwritten without asking", status == 409 and err.get("conflicts") == ["README.md"] and "edited meanwhile" in open(readme).read(), err)
    status, kept, _ = c.post("/api/timeskip/decide", {"id": jid, "action": "keep", "force": True})
    check("unless you say so", status == 200 and "Demo" in open(readme).read())
    with open(readme, "w") as fh:
        fh.write("# Sample\n")
    _, _, events = skip("hello")
    check("a run that changes nothing says so, and leaves nothing to keep", any(e["type"] == "timeskip" and e["state"] == "nothing" for e in events), [(e["type"], e.get("text", e.get("reason", ""))[:80]) for e in events])


def test_user_tools_effort(c, projects, gw_port):
    print("your own tools, and reasoning effort")
    folder = os.path.join(projects, "tooled")
    os.makedirs(folder)
    ws = c.post("/api/workspace/open", {"path": folder})[1]["workspace"]["id"]
    shout = {"name": "shout", "kind": "command", "description": "Shouts what it is given back", "text": 'echo "IN:$STUDIO_INPUT" | tr a-z A-Z'}
    rules = {"name": "house_rules", "kind": "instructions", "description": "The house rules for this team", "text": "Always write tests first."}
    check("tools are saved", c.post("/api/tools/save", {"tool": shout})[0] == 200 and c.post("/api/tools/save", {"tool": rules})[0] == 200)
    for label, bad in (("a bad name", {**rules, "name": "Bad Name"}), ("no description", {**rules, "name": "x_tool", "description": ""}),
                       ("an unknown kind", {**rules, "name": "y_tool", "kind": "magic"})):
        check(f"a tool with {label} is refused", c.post("/api/tools/save", {"tool": bad})[0] == 400)
    check("two tools with one name are refused", c.post("/api/tools/save", {"tool": shout})[0] == 409)
    check("they are listed", [t["name"] for t in c.get("/api/tools/mine")[1]["tools"]] == ["house_rules", "shout"])
    before = len(c_get_json(gw_port, "/_mock/requests")["requests"])
    events = run_agent(c, ws, c.post("/api/agent/sessions", {"ws": ws})[1]["id"], "Please use my tools", on_approval=lambda ev: ("accept", ""))[1]
    asked = [e for e in events if e["type"] == "approval"]
    check("a command tool asks first, showing the command and the input", len(asked) == 1 and asked[0]["purpose"] == "user_tool" and asked[0]["tool"] == "shout"
          and "STUDIO_INPUT: hi; rm -rf x" in asked[0]["command"], asked)
    check("the model's input reaches the command as a variable, never as part of it", "IN:HI; RM -RF X" in events[-1]["summary"]
          and "Always write tests first." in events[-1]["summary"], events[-1].get("summary"))
    sent = c_get_json(gw_port, "/_mock/requests")["requests"][before:]
    check("the model is offered them as user__ tools", all("user__shout" in r["tool_names"] and "user__house_rules" in r["tool_names"] for r in sent if r["tools"]))
    c.post("/api/tools/save", {"old_name": "shout", "tool": {**shout, "on": False}})
    before = len(c_get_json(gw_port, "/_mock/requests")["requests"])
    run_agent(c, ws, c.post("/api/agent/sessions", {"ws": ws})[1]["id"], "hello")
    sent = c_get_json(gw_port, "/_mock/requests")["requests"][before:]
    check("a tool turned off is not offered", sent and "user__shout" not in sent[0]["tool_names"] and "user__house_rules" in sent[0]["tool_names"])
    check("delete", c.post("/api/tools/delete", {"name": "shout"})[0] == 200 and c.post("/api/tools/delete", {"name": "shout"})[0] == 404)
    sid = c.post("/api/agent/sessions", {"ws": ws})[1]["id"]
    check("an unknown effort is refused", c.post("/api/agent/session/settings", {"ws": ws, "id": sid, "effort": "huge"})[0] == 400)
    check("a chat keeps its reasoning effort", c.post("/api/agent/session/settings", {"ws": ws, "id": sid, "effort": "high"})[1]["effort"] == "high"
          and c.get(f"/api/agent/session?id={sid}")[1]["effort"] == "high")
    before = len(c_get_json(gw_port, "/_mock/requests")["requests"])
    run_agent(c, ws, sid, "hello")
    c.post("/api/agent/session/settings", {"ws": ws, "id": sid, "effort": ""})
    run_agent(c, ws, sid, "hello again")
    sent = c_get_json(gw_port, "/_mock/requests")["requests"][before:]
    check("it is sent with the chat's requests, and left out when set back to the default", [r["reasoning_effort"] for r in sent] == ["high", None], [r["reasoning_effort"] for r in sent])


def test_pipes(c, projects):
    print("failures hidden in a pipe")
    sys.path.insert(0, ROOT)
    import studio as sm
    saved, sm.APP = sm.APP if hasattr(sm, "APP") else None, types.SimpleNamespace(cfg={"command_timeout_s": 20, "sandbox_required": False, "sandbox_default": False})
    folder = os.path.join(projects, "piped")
    os.makedirs(folder)
    run = types.SimpleNamespace(ws={"id": "piped", "path": folder}, stop=False)
    try:
        for command, ok, summary in (('python3 -c "import sys; print(1); sys.exit(3)" | tail -1', False, "exit 0, but `python3` exited 3"),
                                     ("seq 1 1000000 | head -1", True, "exit 0"), ("grep zzz /etc/hostname | wc -l", True, "exit 0"),
                                     ("echo a | false | cat", False, "exit 0, but `false` exited 1"), ("false", False, "exit 1"), ("exit 4", False, "exit 4"),
                                     ("ls; python3 -c 'exit(2)' 2>&1 | tee out.log | tail -1", False, "exit 0, but `python3` exited 2"), ("echo hi", True, "exit 0")):
            got = sm.run_command(run, command)
            check(f"{command!r}: {summary}", got[:2] == (ok, summary), got)
        out = sm.run_command(run, 'echo "$STUDIO_PIPE_MARK" 9 9; printf "no newline"')[2]
        check("Studio's marker line is taken out of the output, and a command cannot pose as it", out.endswith(" 9 9\nno newline") and out.count("__studio_pipe_") == 1, out)
        check("a command that cannot be parsed still runs, and names the part by number", sm.hidden_failure('echo "x', [3, 0]) == (1, None, 3))
    finally:
        sm.APP = saved
    ws = c.post("/api/workspace/open", {"path": folder})[1]["workspace"]["id"]
    events = run_agent(c, ws, c.post("/api/agent/sessions", {"ws": ws})[1]["id"], "Run this: python3 -c 'import sys; sys.exit(5)' | tail -1", auto_commands=True)[1]
    result = next(e for e in events if e["type"] == "tool_result" and e["name"] == "run")
    check("the agent sees the hidden failure, and the card shows it", result["ok"] is False and result["summary"] == "exit 0, but `python3` exited 5"
          and result["output"].startswith("exit code 0, but in the pipe `python3` (part 1) exited 5"), result)


def test_announce(c, projects, gw_port):
    print("runs that stop on an announcement")
    sys.path.insert(0, ROOT)
    import studio as sm
    folder = os.path.join(projects, "announced")
    os.makedirs(folder)
    ws = c.post("/api/workspace/open", {"path": folder})[1]["workspace"]["id"]

    def run(message):
        events = run_agent(c, ws, c.post("/api/agent/sessions", {"ws": ws})[1]["id"], message, auto_apply=True, auto_commands=True)[1]
        nudges = sum("said what it would do next" in e.get("text", "") for e in events if e["type"] == "status")
        return events[-1], nudges, events
    done, nudges, events = run("Please announce")
    check("a run that stops on \"Adding X now.\" is sent back once and finishes the work", done["reason"] == "finished" and nudges == 1
          and any(e["type"] == "tool_call" and e["name"] == "run" for e in events), (done, nudges))
    done, nudges, _ = run("Please announce twice")
    check("a second announcement straight after the note ends the run", done["reason"] == "answered" and nudges == 1, (done, nudges))
    done, nudges, _ = run("Please announce and ask")
    check("a question to the user is not an announcement", done["reason"] == "answered" and nudges == 0, (done, nudges))
    events = run_agent(c, ws, c.post("/api/agent/sessions", {"ws": ws})[1]["id"], "Please catalogue quietly", auto_apply=True, auto_commands=True)[1]
    asked = [e for e in events if e["type"] == "status" and "without writing its answer" in e.get("text", "")]
    finishes = [e for e in events if e["type"] == "tool_call" and e["name"] == "finish"]
    check("a one-line finish after reading only is sent back once for the answer itself",
          len(asked) == 1 and len(finishes) == 2 and events[-1]["reason"] == "finished" and "file11.txt" in (events[-1].get("summary") or ""),
          (len(asked), len(finishes), events[-1]))
    events = run_agent(c, ws, c.post("/api/agent/sessions", {"ws": ws})[1]["id"], "Please catalogue quietly after the tests", auto_apply=True, auto_commands=True)[1]
    asked = [e for e in events if e["type"] == "status" and "without writing its answer" in e.get("text", "")]
    check("running the tests on the way still counts as only reading", len(asked) == 1 and "file11.txt" in (events[-1].get("summary") or ""),
          (len(asked), events[-1]))
    events = run_agent(c, ws, c.post("/api/agent/sessions", {"ws": ws})[1]["id"], "Please catalogue quietly after a build", auto_apply=True, auto_commands=True)[1]
    asked = [e for e in events if e["type"] == "status" and "without writing its answer" in e.get("text", "")]
    check("a build is work, so its one-line finish is left alone", not asked and events[-1]["reason"] == "finished", (len(asked), events[-1]))
    done, nudges, _ = run("hello")
    check("a plain answer without any work is left alone", done["reason"] == "answered" and nudges == 0, (done, nudges))
    for text in ("Adding back covers — a darker plain panel with a text block and barcode on the opposite face of each book.",
                 "The base colours are hard to recover, so I'll use the original palette values and rebuild both faces.",
                 "Covers applied. The shelf is far in the current framing — checking them close-up by moving the camera.",
                 "Let me fix the import:", "Θα διορθώσω τώρα το αρχείο.",
                 "I have a full picture now. Plan: add a `client_balance` op in `db.py`, wire the route and handler, add tests, run them."):
        check(f"an announcement: {text[:40]}", sm.announces_work(text))
    for text in ("Should I also add back covers?", "Everything is done: the tests pass.", "All done. If you want, I'll also add back covers.",
                 "The tree is fixed — it now sits fully outside the window.", "Done — nothing left to fix.", "## Summary\n" + "Did things. " * 80,
                 "Here is my plan: add the route, then the tests. If you agree, I'll start."):
        check(f"not an announcement: {text[:40]}", not sm.announces_work(text))


def wait_until(fn, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.2)
    return bool(fn())


def test_session_cache(c, projects, gw_port, cfg_path):
    print("session cache: a chat's engine state saved to disk and restored after a reload (Strata session files)")
    os.makedirs(os.path.join(projects, "cache-work"), exist_ok=True)
    ws = c.post("/api/workspace/open", {"path": os.path.join(projects, "cache-work")})[1]["workspace"]["id"]
    engine = lambda **kw: c_post_json(gw_port, "/_mock/engine", kw)
    # Stop must answer while the model loads before a restore (2026-11-12: Stop did nothing for minutes).
    import studio as sm
    stopper = types.SimpleNamespace(stop=False)
    threading.Timer(0.4, lambda: setattr(stopper, "stop", True)).start()
    began = time.time()
    try:
        sm.stoppable(stopper, lambda: time.sleep(5))
        stopped_in = None
    except sm.Stopped:
        stopped_in = time.time() - began
    check("Stop ends a slow model load before a cache restore at once", stopped_in is not None and stopped_in < 1.5, stopped_in)
    check("work that finishes returns its result", sm.stoppable(types.SimpleNamespace(stop=False), lambda: 42) == 42)
    try:
        engine(on=True, pid=10, loaded=True, saves=[], restores=[], files=[], port=gw_port)
        a = c.post("/api/agent/sessions", {"ws": ws})[1]["id"]
        run_agent(c, ws, a, "Run this: echo cached", auto_commands=True)
        saved = []

        def saved_a():
            saved[:] = engine()["saves"]
            return f"studio-{a}.bin" in saved
        check("when a run ends, the chat's engine state is saved", wait_until(saved_a, 10), saved)
        active = json.load(open(os.path.join(os.path.dirname(cfg_path), "data", "active_runs.json")))
        check("the list of working runs is empty once the run has ended", active["runs"] == [], active)
        index = json.load(open(os.path.join(os.path.dirname(cfg_path), "data", "session_cache.json")))
        check("and indexed with its size", index.get(a, {}).get("tokens") == 120000, index)
        engine(pid=11)                                  # the engine was restarted (by the gateway, a tool, anyone)
        events = run_agent(c, ws, a, "Run this: echo again", auto_commands=True)[1]
        statuses = [e.get("text") or "" for e in events if e["type"] == "status"]
        check("after a reload the chat is restored before its request, not read again", engine()["restores"] == [f"studio-{a}.bin"]
              and any(t.startswith("Restored this chat from the cache: 120,000 tokens") for t in statuses), (engine()["restores"], statuses[:4]))
        b = c.post("/api/agent/sessions", {"ws": ws})[1]["id"]
        run_agent(c, ws, b, "Run this: echo other", auto_commands=True)
        check("a chat without a saved state is just sent", engine()["restores"] == [f"studio-{a}.bin"])
        wait_until(lambda: f"studio-{b}.bin" in engine()["saves"], 10)
        run_agent(c, ws, a, "Run this: echo back", auto_commands=True)
        check("going back to a chat after another one restores it", engine()["restores"] == [f"studio-{a}.bin"] * 2, engine()["restores"])
        engine(loaded=False, pid=20)                    # unloaded while idle
        events = run_agent(c, ws, a, "Run this: echo cold", auto_commands=True)[1]
        statuses = [e.get("text") or "" for e in events if e["type"] == "status"]
        check("with the engine unloaded, it is loaded first, then the chat restored", engine()["restores"] == [f"studio-{a}.bin"] * 3
              and "Loading the model before restoring this chat…" in statuses, statuses[:5])
        # An engine update changes the session files' fingerprint, and the engine refuses older files with 400 (2026-11-17).
        settled = [-1]                       # the last run's state is saved in the background: let that land first

        def quiet():
            n, settled[0] = len(engine()["saves"]), len(engine()["saves"])
            time.sleep(0.5)
            return len(engine()["saves"]) == n
        wait_until(quiet, 10)
        engine(pid=30, files=[])
        run_agent(c, ws, a, "Run this: echo updated", auto_commands=True)
        index = json.load(open(os.path.join(os.path.dirname(cfg_path), "data", "session_cache.json")))
        check("a saved state the engine refuses is forgotten, then saved again by the new engine", engine()["restores"] == [f"studio-{a}.bin"] * 3
              and wait_until(lambda: f"studio-{a}.bin" in engine()["files"], 10), (engine()["restores"], index.get(a)))
    finally:
        engine(on=False)


def test_sounds(tmp):
    print("sound effects: tools/sound_engine.py (fake mode) and tools/sound_mcp.py")
    if not shutil.which("ffmpeg"):
        check("ffmpeg is installed for the sound tests", False, "sudo apt install ffmpeg")
        return
    root, port = os.path.join(tmp, "sounds"), free_port()
    os.makedirs(root)
    eng = subprocess.Popen([sys.executable, os.path.join(ROOT, "tools", "sound_engine.py")], env={**os.environ, "SOUND_FAKE": "1", "SOUND_PORT": str(port),
                           "SOUND_OUT_ROOTS": root, "SOUND_MAKE_ROOM": "0"}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_port(port, eng)
        out = os.path.join(root, "assets", "audio", "waves.mp3")
        call = lambda i, args, token=None: {"jsonrpc": "2.0", "id": i, "method": "tools/call",
                                            "params": {"name": "make_sound", "arguments": args, **({"_meta": {"progressToken": token}} if token else {})}}
        calls = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                 call(3, {"description": "small waves on a pebble beach", "seconds": 2, "path": out}, "s3"),
                 call(4, {"description": "a click", "path": "/etc/click.mp3"}),
                 call(5, {"description": "a click", "seconds": 90, "path": os.path.join(root, "long.mp3")}),
                 call(6, {"sounds": [{"description": "a click", "seconds": 1, "path": os.path.join(root, "sfx", "a.mp3")},
                                     {"description": "a chime", "seconds": 1.5, "path": os.path.join(root, "sfx", "b.mp3")},
                                     {"description": "waves", "seconds": 2, "path": os.path.join(root, "sfx", "c.mp3")}]}, "s6"),
                 call(7, {"sounds": [{"description": "a click", "path": os.path.join(root, "ok.mp3")}, {"description": "x", "path": "/etc/x.mp3"}]})]
        proc = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "sound_mcp.py")], input="\n".join(json.dumps(c) for c in calls) + "\n",
                              capture_output=True, text=True, timeout=180, env={**os.environ, "SOUND_URL": f"http://127.0.0.1:{port}", "SOUND_ROOTS": root})
        messages = list(map(json.loads, proc.stdout.splitlines()))
        replies = {m["id"]: m for m in messages if "id" in m}
        notes = [m["params"] for m in messages if m.get("method") == "notifications/progress"]
        made = replies[3]["result"]
        check("it lists make_sound", [t["name"] for t in replies[2]["result"]["tools"]] == ["make_sound"])
        check("make_sound writes the MP3 where it was asked, with its length", not made["isError"] and os.path.getsize(out) > 1000
              and re.search(r"Saved 2\.\d s of sound", made["content"][0]["text"]), made)
        three = [n for n in notes if n["progressToken"] == "s3"]
        check("and reports its progress to the caller", three and three[-1]["progress"] == 100 and {n["progressToken"] for n in notes} == {"s3", "s6"}, notes[-2:])
        check("a file outside the project is refused", replies[4]["result"]["isError"] and "must be inside" in replies[4]["result"]["content"][0]["text"])
        batch = replies[6]["result"]
        six = [n for n in notes if n["progressToken"] == "s6"]
        check("several sounds in one call are all made, each reported", not batch["isError"] and all(os.path.isfile(os.path.join(root, "sfx", f)) for f in ("a.mp3", "b.mp3", "c.mp3"))
              and [l[:9] for l in batch["content"][0]["text"].splitlines()] == ["1. Saved ", "2. Saved ", "3. Saved "], batch)
        check("their progress counts across the batch", six and six[-1]["progress"] == 100 and any(n["message"].startswith("Sound 3/3") for n in six), six[-2:])
        check("a bad sound in a batch is named", replies[7]["result"]["isError"] and "sound 2" in replies[7]["result"]["content"][0]["text"], replies[7]["result"])
        check("a length past 30 s is refused, saying the limit", replies[5]["result"]["isError"] and "0.5 to 30" in replies[5]["result"]["content"][0]["text"],
              replies[5]["result"])
    finally:
        eng.kill()


def test_card(c, gw_port):
    print("graphics card setting (Strata first or tools ready), against the mock gateway's pool")
    try:
        c_post_json(gw_port, "/_mock/card", {"on": True, "running": True, "busy": 0, "mode": "fixed", "pool_mib": 9216, "posts": []})
        s, d, _ = c.get("/api/card")
        check("never chosen in Studio, the setting is read from the gateway's pool", s == 200 and d["mode"] == "tools" and d["pending"] is False
              and d["pool"]["active_mib"] == 9216, d)
        s, d, _ = c.post("/api/card", {"mode": "strata"})
        posts = c_post_json(gw_port, "/_mock/card", {})["posts"]
        check("choosing while Strata runs keeps it pending and does not restart Strata", s == 200 and d["mode"] == "strata" and d["pending"] is True and posts == [], (d, posts))
        c_post_json(gw_port, "/_mock/card", {"busy": 1})
        check("Restart now waits while Strata is answering a request", c.post("/api/card/restart", {})[0] == 409)
        c_post_json(gw_port, "/_mock/card", {"busy": 0})
        s, d, _ = c.post("/api/card/restart", {})
        posts = c_post_json(gw_port, "/_mock/card", {})["posts"]
        check("Restart now sends the whole card to Strata: no pool", s == 200 and d["pending"] is False and len(posts) == 1
              and posts[0]["mode"] == "off" and posts[0]["pool_mib"] == 0 and posts[0]["while_running"] is True, (d, posts))
        c_post_json(gw_port, "/_mock/card", {"running": False})
        s, d, _ = c.post("/api/card", {"mode": "tools"})
        posts = c_post_json(gw_port, "/_mock/card", {})["posts"]
        check("with Strata not running, a choice applies at once, for its next start", s == 200 and d["pending"] is False and len(posts) == 2
              and posts[1] == {"mode": "fixed", "pool_mib": 9216, "keep_ollama": False, "while_running": False}, (d, posts))
        check("an unknown choice is refused", c.post("/api/card", {"mode": "both"})[0] == 400)
        check("the card setting shows how long the gateway keeps an idle Strata", c.get("/api/card")[1]["strata_idle"] == 15)
        s, d, _ = c.post("/api/card/strata_idle", {"minutes": 30})
        runtime = c_post_json(gw_port, "/_mock/card", {})["runtime"]
        check("changing it keeps the gateway's other runtime settings as they were", s == 200 and d["strata_idle"] == 30
              and runtime == {"auto_start": False, "idle_timeout_minutes": 30, "startup_script": "run-test.sh"}, (d, runtime))
        check("only the gateway's own choices are taken", c.post("/api/card/strata_idle", {"minutes": 7})[0] == 400)
    finally:
        c_post_json(gw_port, "/_mock/card", {"on": False, "posts": []})


def test_comfyui_dashboard(c, gw_port):
    print("ComfyUI dashboard (the mock gateway plays ComfyUI)")
    sys.path.insert(0, ROOT)
    import studio as sm
    check("without comfyui_url there is no dashboard", c.get("/api/comfyui/status")[1] == {"available": False})
    check("nor pictures from it", c.get("/api/comfyui/image?filename=studio_00001_.png")[0] == 404)
    check("the default picture model is checked", c.post("/api/comfyui/settings", {"default_model": "dalle"})[0] == 400)
    check("and saved", c.post("/api/comfyui/settings", {"default_model": "auto"})[1] == {"default_model": "auto", "music_model": "ace-step"})
    check("the music model is checked", c.post("/api/comfyui/settings", {"music_model": "suno"})[0] == 400 and c.post("/api/comfyui/settings", {})[0] == 400)
    check("and saved without touching the picture one",
          c.post("/api/comfyui/settings", {"music_model": "heartmula"})[1] == {"default_model": "auto", "music_model": "heartmula"})
    c.post("/api/comfyui/settings", {"music_model": "ace-step"})
    saved = sm.APP
    sm.APP = types.SimpleNamespace(cfg={"comfyui_url": f"http://127.0.0.1:{gw_port}"}, data_dir=tempfile.mkdtemp())
    req = types.SimpleNamespace(arg=lambda name, default="": "")
    try:
        c_post_json(gw_port, "/_mock/comfy", {"busy": False})
        idle = sm.comfyui_status(req)
        last = idle["recent"][0]
        check("idle, it reports memory, the version and the default model", idle["state"] == "idle" and "current" not in idle and idle["version"] == "0.44.0"
              and idle["default_model"] == "z-image-turbo"
              and idle["memory"]["vram_free"] == 9 * 2 ** 30 and "7800 XT" in idle["memory"]["device"], idle)
        check("a finished picture comes with its prompt, size, seed, time and file",
              last["prompt"] == "a red fox in the snow" and last["size"] == "1152x896" and last["seed"] == 7 and last["seconds"] == 14.0
              and last["ok"] and last["images"] == [{"filename": "studio_00001_.png", "subfolder": "", "type": "output"}], last)
        # A comfyui tool call's progress comes from what the watcher hears: waiting until its job starts, then the sampler's steps.
        notes, stop = [], threading.Event()
        threading.Thread(target=sm.comfy_progress, args=("generate_image", notes.append, stop), daemon=True).start()
        time.sleep(1.4)
        with sm.COMFY_WATCH.lock:
            sm.COMFY_WATCH.started["job-tool"] = time.time()
            sm.COMFY_WATCH.progress["job-tool"] = {"value": 3, "max": 8}
        time.sleep(1.2)
        stop.set()
        check("a picture call waits, then reports the sampler's steps as its progress",
              notes and notes[0]["message"].startswith("Waiting for ComfyUI") and "progress" not in notes[0]
              and notes[-1] == {"progress": 3, "total": 8, "message": "Picture · step 3/8"}, notes)
        c_post_json(gw_port, "/_mock/comfy", {"busy": True})
        with sm.COMFY_WATCH.lock:
            sm.COMFY_WATCH.progress["job-now"] = {"value": 12, "max": 25}
        busy = sm.comfyui_status(req)
        cur = busy["current"]
        check("while it works, the picture being made shows with its step and the queue behind it",
              busy["state"] == "work" and busy["pending"] == 1 and cur["prompt"] == "a lighthouse at dusk" and cur["studio"] is True
              and (cur["step"], cur["of"]) == (12, 25), busy)
        graph = sm.comfyui_graph(req)
        sampler = next(n for n in graph["nodes"] if n["id"] == "5")
        check("the nodes of the picture being made, with their settings and links", graph["job"] == "job-now" and graph["state"] == "running"
              and sampler["type"] == "KSampler" and sampler["values"]["seed"] == 7 and {"input": "positive", "node": "2", "slot": 0} in sampler["links"]
              and (graph["step"], graph["of"]) == (12, 25), graph)
        waiting = sm.comfyui_graph(types.SimpleNamespace(arg=lambda name, default="": "job-next" if name == "job" else ""))
        check("a job further back in the queue is waiting", waiting["state"] == "waiting" and waiting["job"] == "job-next", waiting)
        c_post_json(gw_port, "/_mock/comfy", {"busy": False})
        latest = sm.comfyui_graph(req)
        check("with nothing running, the latest picture's nodes: all done, with the saved picture on its node",
              latest["job"] == "job-done" and latest["state"] == "done" and sorted(latest["done"]) == ["2", "4", "5", "7"] and latest["seconds"] == 14.0
              and latest["images"] == {"7": [{"filename": "studio_00001_.png", "subfolder": ""}]}, latest)
        missing = types.SimpleNamespace(arg=lambda name, default="": "gone" if name == "job" else "")
        try:
            sm.comfyui_graph(missing)
            check("an unknown job is a 404", False)
        except sm.ApiError as exc:
            check("an unknown job is a 404", exc.status == 404, exc.message)
        watch = sm.ComfyWatch()
        for message in ({"type": "execution_start", "data": {"prompt_id": "j"}}, {"type": "execution_cached", "data": {"prompt_id": "j", "nodes": ["1"]}},
                        {"type": "executing", "data": {"prompt_id": "j", "node": "2"}}, {"type": "executing", "data": {"prompt_id": "j", "node": "5"}}):
            watch.note(message)
        check("the listener follows which node runs and which are done or cached", watch.nodes["j"]["node"] == "5" and watch.nodes["j"]["done"] == ["2"] and watch.nodes["j"]["cached"] == ["1"], watch.nodes)
        watch.note({"type": "execution_error", "data": {"prompt_id": "j", "node_id": "5", "exception_message": "out of memory"}})
        check("and which one failed, and why", watch.nodes["j"]["error"] == {"node": "5", "message": "out of memory"} and watch.nodes["j"]["node"] is None, watch.nodes)
        swap_file = os.path.join(sm.APP.data_dir, "swap.json")
        sm.APP.cfg["comfyui_swap_file"] = swap_file
        check("no swap file: Strata is not reported unloaded", sm.comfy_swap() == {"active": False, "since": None})
        json.dump({"full": True, "until": time.time() + 600, "since": 1000.0}, open(swap_file, "w"))
        check("while the comfyui tool has unloaded Strata, the status says so", sm.comfyui_status(req)["swap"] == {"active": True, "since": 1000.0})
        json.dump({"full": True, "until": time.time() - 1, "since": 1000.0}, open(swap_file, "w"))
        check("a swap left behind by a crash expires", sm.comfy_swap()["active"] is False)
        sm.APP.cfg["comfyui_url"] = "http://127.0.0.1:9"
        check("a ComfyUI that does not answer is reported, not an error", sm.comfyui_status(req)["state"] == "err")
    finally:
        c_post_json(gw_port, "/_mock/comfy", {"busy": False})
        sm.APP = saved

    # The progress listener, against a minimal WebSocket server.
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    got = {}

    def frame(obj):
        data = json.dumps(obj).encode()
        return bytes([0x81, len(data)]) + data if len(data) < 126 else bytes([0x81, 126]) + struct.pack(">H", len(data)) + data

    def serve():
        conn, _ = server.accept()
        request = b""
        while b"\r\n\r\n" not in request:
            request += conn.recv(1024)
        got["request"] = request.decode()
        conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n")
        conn.sendall(frame({"type": "status", "data": {"status": {"exec_info": {"queue_remaining": 1}}}})
                     + frame({"type": "execution_start", "data": {"prompt_id": "j1"}})
                     + frame({"type": "progress", "data": {"value": 5, "max": 25, "prompt_id": "j1", "node": "5"}})
                     + bytes([0x89, 2]) + b"hi")
        got["pong"] = conn.recv(64)
        conn.sendall(bytes([0x88, 0]))
        conn.close()
    threading.Thread(target=serve, daemon=True).start()
    watch = sm.ComfyWatch()
    watch.url = f"http://127.0.0.1:{server.getsockname()[1]}"     # as ensure() sets it; listen stops when it changes
    try:
        watch.listen(watch.url)
    finally:
        server.close()
    check("the listener asks for the MCP server's client id", "GET /ws?clientId=strata-studio " in got.get("request", ""), got.get("request"))
    check("it keeps each picture's step and start", watch.progress.get("j1") == {"value": 5, "max": 25} and "j1" in watch.started, watch.progress)
    pong = got.get("pong", b"")
    check("and answers a ping with a masked pong", pong[:1] == b"\x8a" and pong[1] & 0x80 and bytes(b ^ pong[2 + i % 4] for i, b in enumerate(pong[6:8])) == b"hi", pong)


def test_page_build(c):
    print("page build")
    page = c.get("/")[1].decode()
    stamped = re.search(r'<meta name="studio-build" content="([0-9a-f]{12})">', page)
    build = c.get("/api/auth/state")[2].getheader("X-Studio-Build")
    check("the page is stamped with the build it was served from, and API replies name the same", stamped and stamped.group(1) == build, (stamped, build))
    css = os.path.join(ROOT, "web", "app.css")
    before = os.stat(css)
    try:
        os.utime(css, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))
        check("changed page files give API replies a new build", c.get("/api/auth/state")[2].getheader("X-Studio-Build") not in (None, build))
    finally:
        os.utime(css, ns=(before.st_atime_ns, before.st_mtime_ns))
    check("and the same files give the same build again", c.get("/api/auth/state")[2].getheader("X-Studio-Build") == build)


def test_guide(c, projects, gw_port):
    print("guiding a run")
    folder = os.path.join(projects, "guided")
    os.makedirs(folder)
    ws = c.post("/api/workspace/open", {"path": folder})[1]["workspace"]["id"]

    def guided_run(message, guide_after, texts):
        """Runs `message`; once `guide_after(event)` is true, sends each of `texts` as guidance."""
        session = c.post("/api/agent/sessions", {"ws": ws})[1]["id"]
        status, data, _ = c.post("/api/agent/send", {"session": session, "ws": ws, "message": message, "model": "quartz:strata",
                                                      "auto_apply": True, "auto_commands": True})
        assert status == 200, (status, data)
        run, events, answers, sent = data["run"], [], [], False
        for _name, ev in c.sse(f"/api/agent/stream?run={run}", timeout=60):
            events.append(ev)
            if not sent and guide_after(ev):
                sent = True
                answers = [c.post("/api/agent/guide", {"run": run, "message": text})[:2] for text in texts]
        return run, session, events, answers

    run, session, events, answers = guided_run("Please work slowly", lambda e: e["type"] == "tool_call", ["Use port 9000", "  and add tests  "])
    check("guidance is accepted while the run works", [a[0] for a in answers] == [200, 200], answers)
    users = [e for e in events if e["type"] == "user"]
    check("it shows in the chat at once, marked as guidance", [(u["text"], u.get("guided")) for u in users] == [("Use port 9000", True), ("and add tests", True)], users)
    done = events[-1]
    check("the model reads it at its next step and the run carries on", done["type"] == "done" and done["reason"] == "finished"
          and "Guidance: Use port 9000 | and add tests" in done.get("summary", ""), done)
    check("the run did not restart: the work it had done stands", sum(e["type"] == "tool_call" and e["name"] == "run" for e in events) == 3)
    stored = c.get(f"/api/agent/session?id={session}")[1]
    check("guidance is kept in the chat, marked, for later", [e.get("guided") for e in stored["events"] if e["type"] == "user"] == [None, True, True])
    check("a run that ended takes no more guidance", c.post("/api/agent/guide", {"run": run, "message": "late"})[0] == 409)
    check("an empty message is refused", c.post("/api/agent/guide", {"run": run, "message": "  "})[0] == 400)
    check("an unknown run is refused", c.post("/api/agent/guide", {"run": "nope", "message": "x"})[0] == 404)

    count = {"n": 0}

    def after_third(ev):
        count["n"] += ev["type"] == "tool_result"
        return count["n"] == 3
    events = guided_run("Please work slowly and only talk", lambda e: e["type"] == "tool_call", ["Do the back covers too"])[2]
    statuses = [e.get("text", "") for e in events if e["type"] == "status"]
    check("a model that answers a steer with words only is asked once to carry on, and does",
          events[-1]["reason"] == "finished" and "Guidance: Do the back covers too" in events[-1].get("summary", "")
          and sum("replied without acting" in s for s in statuses) == 1, (events[-1], statuses))
    count = {"n": 0}
    events = guided_run("Please work slowly", lambda e: False, [])[2]
    check("without a steer, nothing is added", not any("replied without acting" in e.get("text", "") for e in events if e["type"] == "status"))
    for word, reason in (("", "finished"), (" and answer", "answered")):
        count["n"] = 0
        events = guided_run(f"Please work slowly, slow end{word}", after_third, ["One more thing"])[2]
        done = events[-1]
        summaries = [e.get("summary", "") for e in events if e["type"] == "done"]
        replies = "".join(e.get("text", "") for e in events if e["type"] == "token")
        check(f"guidance sent as the model writes its last reply ({reason}) is answered before the run ends",
              done["reason"] == reason and "Guidance: One more thing" in (summaries[-1] + replies), (done, replies[-200:]))

    session = c.post("/api/agent/sessions", {"ws": ws})[1]["id"]
    status, data, _ = c.post("/api/agent/send", {"session": session, "ws": ws, "message": "Please work slowly", "model": "quartz:strata",
                                                  "auto_apply": True, "auto_commands": True})
    run = data["run"]
    for _name, ev in c.sse(f"/api/agent/stream?run={run}", timeout=60):
        if ev["type"] == "tool_call":
            c.post("/api/agent/stop", {"run": run})
            check("after Stop, guidance is refused", c.post("/api/agent/guide", {"run": run, "message": "too late"})[0] == 409)
    messages = c.get(f"/api/agent/session?id={session}")[1]
    check("a stopped run keeps its chat", messages["events"][-1]["type"] == "done", messages["events"][-1])


def test_model_loading(c, projects, gw_port, cfg_path):
    print("model loading progress")
    sys.path.insert(0, ROOT)
    import studio as sm
    folder = os.path.join(projects, "loading")
    os.makedirs(folder)
    ws = c.post("/api/workspace/open", {"path": folder})[1]["workspace"]["id"]
    seen, stop = [], threading.Event()

    def poll():
        while not stop.wait(0.7):
            seen.append(c.get("/api/model/status?model=quartz:strata")[1].get("load"))
    watcher = threading.Thread(target=poll, daemon=True)
    watcher.start()
    try:
        events = run_agent(c, ws, c.post("/api/agent/sessions", {"ws": ws})[1]["id"], "hello, slow load")[1]
    finally:
        stop.set()
        watcher.join()
    loads = [e["load"] for e in events if e["type"] == "status" and e.get("load")]
    check("a run that waits for a loading model shows its progress", len(loads) >= 2 and all(e["text"] == "Loading model…" for e in events if e.get("load")), loads)
    check("the bar only moves forward, and stays short of 100 until it is ready", all(a["percent"] <= b["percent"] for a, b in zip(loads, loads[1:])) and loads[-1]["percent"] < 100 and loads[0]["percent"] >= 1, [l["percent"] for l in loads])
    check("it names the engine's step", any(l["step"] == "Warming up the GPU" for l in loads) or any(l["step"] in ("Loading the expert arena", "Starting the CPU workers", "Starting the session") for l in loads), [l["step"] for l in loads])
    first_text = next((i for i, e in enumerate(events) if e["type"] == "token"), len(events))
    check("no loading status once the reply streams", not any(e.get("load") for e in events[first_text:]))
    check("the reply still arrives", "mock model" in "".join(e.get("text", "") for e in events if e["type"] == "token"), [(e["type"], str(e.get("text"))[:60]) for e in events][-8:])
    check("the dashboard status carries the progress too", any(s and s.get("percent") for s in seen), seen)
    stored = json.load(open(os.path.join(os.path.dirname(cfg_path), "data", "load_times.json")))
    check("Studio remembers how long the load took, to pace the next bar", 5 <= stored.get("quartz:strata", 0) <= 12, stored)
    saved = sm.APP
    sm.APP = types.SimpleNamespace(load_times={"m": 100}, load_seen={})
    try:
        status = {"job": {"kind": "start", "state": "running", "started_at": 1000.0, "output": "x\nstrata generate: loaded 39.97 GiB at 3.68 GiB/s"}}
        check("a milestone sets a floor even when time says less", sm.load_progress("m", status, now=1001.0)["percent"] == 78)
        check("time moves the bar while the engine is quiet", sm.load_progress("m", {"job": {**status["job"], "output": "memory guard"}}, now=1050.0)["percent"] == 50)
        check("before any milestone too", sm.load_progress("m", {"job": {**status["job"], "output": ""}}, now=1020.0)["percent"] == 20)
        check("and it never reaches 100 by time alone", sm.load_progress("m", {"job": {**status["job"], "output": ""}}, now=1500.0)["percent"] == 98)
        check("a loaded engine has no progress", sm.load_progress("m", {**status, "running": True, "health": {"loaded": True}}) is None)
        check("nor does a stopped one", sm.load_progress("m", {"job": {"kind": "start", "state": "stopped"}}) is None)
        newer = "[strata] experts loaded: 39.97 GiB at 3.86 GiB/s (25 s so far)\n[strata] filling the GPU's expert cache (8742 experts) ...\n[strata] almost ready ..."
        got = sm.load_progress("m", {"job": {**status["job"], "output": newer}}, now=1001.0)
        check("Strata 0.2's plainer start-up lines move the bar too", got["percent"] == 94 and got["step"] == "Almost ready", got)
    finally:
        sm.APP = saved
    c_post_json(gw_port, "/_mock/reset_load", {})


def test_live(c, projects, st_port):
    print("live servers")
    sys.path.insert(0, ROOT)
    import studio as sm
    folder = os.path.join(projects, "liveapp")
    os.makedirs(folder)
    inside, outside = free_port(), free_port()
    app = subprocess.Popen([sys.executable, os.path.join(ROOT, "tests", "live_app.py"), str(inside)], cwd=folder, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    other = subprocess.Popen([sys.executable, os.path.join(ROOT, "tests", "live_app.py"), str(outside)], cwd=projects, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_port(inside, app)
        wait_port(outside, other)
        ws = c.post("/api/workspace/open", {"path": folder})[1]["workspace"]["id"]
        ports = c.get(f"/api/live/ports?ws={ws}")[1]
        check("the folder's own server is listed", inside in ports["ports"] and ports["sandboxed"] is False, ports)
        check("servers started elsewhere, and Studio itself, are not", outside not in ports["ports"] and st_port not in ports["ports"], ports)
        check("one started elsewhere cannot be opened", c.post("/api/live/open", {"ws": ws, "port": outside})[0] == 403)
        check("nor Studio itself", c.post("/api/live/open", {"ws": ws, "port": st_port})[0] == 403)
        check("a bad port is refused", c.post("/api/live/open", {"ws": ws, "port": 0})[0] == 400)
        check("signing in is needed to open one", Client(st_port).post("/api/live/open", {"ws": ws, "port": inside})[0] == 401)
        opened = c.post("/api/live/open", {"ws": ws, "port": inside})[1]
        url = opened["url"]
        check("opening gives a live address", url.startswith("/live/") and url.endswith("/") and opened["port"] == inside, opened)
        status, page, resp = c.get(url, cookie=False)
        page = page.decode() if isinstance(page, bytes) else str(page)
        check("the page comes through, without a login", status == 200 and "Hello from the live app" in page, status)
        check("root links point into the live address", f'href="{url}api/echo"' in page and f'href="{url}style.css"' in page, page[:400])
        check("pages get the script that maps root paths", "history[k]" in page and json.dumps(url[:-1]) in page)
        check("the shim makes framework cookie probes safe in the opaque sandbox", 'defineProperty(document,"cookie"' in page)
        check("programmatic full-page navigation stays in the Live address", "window.__studioAssign('/result?q=state')" in page)
        check("it runs sandboxed, away from Studio's origin", "sandbox allow-scripts" in (resp.getheader("Content-Security-Policy") or "") and resp.getheader("X-Frame-Options") is None)
        check("the app's cookies are not exposed on Studio's origin", resp.getheader("Set-Cookie") is None)
        status, echo, _ = c.get(url + "api/echo?x=1")
        check("requests reach the app at its own path", status == 200 and echo["path"] == "/api/echo?x=1" and echo["method"] == "GET", echo)
        check("the app's cookie returns through its isolated server-side jar", echo["headers"].get("Cookie") == "app=1", echo["headers"])
        check("Studio's cookie and headers never reach the app", (not c.cookie or c.cookie not in echo["headers"].get("Cookie", ""))
              and not any(k.lower().startswith("x-studio") for k in echo["headers"]), echo["headers"])
        other_url = c.post("/api/live/open", {"ws": ws, "port": inside})[1]["url"]
        other_echo = c.get(other_url + "api/echo")[1]
        check("each Live address has a separate app cookie jar", "Cookie" not in other_echo["headers"], other_echo["headers"])
        status, echo, _ = c.request("POST", url + "api/echo", {"word": "θάλασσα"})
        check("a POST and its body come through", status == 200 and echo["method"] == "POST" and json.loads(echo["body"]) == {"word": "θάλασσα"}, echo)
        check("so do other methods", c.request("PUT", url + "api/echo", {"a": 1})[1]["method"] == "PUT")
        status, _, resp = c.get(url + "go", cookie=False)
        check("a redirect stays inside the live address", status == 302 and resp.getheader("Location") == url + "api/echo?from=go", resp.getheader("Location"))
        check("stylesheets are mapped too", c.get(url + "style.css", cookie=False)[1] == f"body{{background:url({url}bg.png)}}".encode())
        check("event streams come through", c.get(url + "events", cookie=False)[1].count(b"data: tick") == 3)
        status, _, resp = c.request("OPTIONS", url + "api/echo", cookie=False)
        check("a credentialed preflight is answered for the opaque page", status == 204 and resp.getheader("Access-Control-Allow-Origin") == "null"
              and resp.getheader("Access-Control-Allow-Credentials") == "true")
        check("an unknown live address is not found", c.get("/live/nope/", cookie=False)[0] == 404)
        relay = subprocess.run([sys.executable, "-c", sm.LIVE_RELAY, str(inside)], input=b"GET /api/echo HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n", capture_output=True, timeout=20)
        check("the sandbox relay passes one request and its answer", relay.stdout.startswith(b"HTTP/1.") and b'"/api/echo"' in relay.stdout, relay.stdout[:120])

        session = c.post("/api/agent/sessions", {"ws": ws})[1]["id"]
        events = run_agent(c, ws, session, f"Please view page {inside} /", auto_commands=True)[1]
        result = next(e for e in events if e["type"] == "tool_result" and e["name"] == "view_page")
        rendered = "server saw clicked" in result["output"]
        if rendered:
            began = time.time()
            busy = run_agent(c, ws, c.post("/api/agent/sessions", {"ws": ws})[1]["id"], f"Please view page {inside} /busy", auto_commands=True)[1]
            busy = next(e for e in busy if e["type"] == "tool_result" and e["name"] == "view_page")
            check("a page that keeps fetching still reports its answer, without a loading error", "HTTP 200" in busy["output"] and "title 'Busy page'" in busy["output"]
                  and "loading the page" not in busy["output"] and time.time() - began < 40, (round(time.time() - began), busy["output"][:300]))
        check("view_page shows the model the page as a browser would", result["ok"] and "Hello from the live app" in result["output"] and "title 'Live test app'" in result["output"], result["output"][:400])
        seen = next(e for e in events if e["type"] == "page_view")
        check("its scripts ran, and the click went through", rendered if seen.get("shot") else "scripts did not run" in result["output"], result["output"][:600])
        check("the chat gets a card for it", seen["port"] == inside and seen["title"] == "Live test app" and seen["status"] == 200, seen)
        check("script errors and failed requests are counted apart, each once", seen["errors"] == 0 and seen["failed"] == (1 if seen.get("shot") else 0), seen)
        if seen.get("shot"):
            status, image, resp = c.get(f"/api/live/shot?id={seen['shot']}")
            check("its screenshot can be shown", status == 200 and resp.getheader("Content-Type") == "image/png" and image[:4] == b"\x89PNG")
            check("but only to its owner", Client(st_port).get(f"/api/live/shot?id={seen['shot']}")[0] == 401)
            check("a model that can see gets the screenshot too", "The screenshot is attached for you to look at (the first screen)." in result["output"], result["output"][:300])
        check("a screenshot id is checked", c.get("/api/live/shot?id=../../users")[0] == 404)
        shot = os.path.join(projects, "phone.png")
        phone = sm.render_page(f"http://127.0.0.1:{st_port}{url}form", [], shot, 390, True)
        if phone is not None:
            import struct
            size = struct.unpack(">II", open(shot, "rb").read()[16:24]) if os.path.isfile(shot) else (0, 0)
            check("view_page can look at a page as a phone, the whole page", phone.get("width") == 390 and size[0] == 390 and size[1] >= 3000, (phone.get("width"), size))
            sent = sm.render_page(f"http://127.0.0.1:{st_port}{url}form", [{"type": "#q", "text": "lamp"}, {"press": "Enter"}], shot, 1280, False)
            check("after Enter submits a form, it reads the page that came back", sent.get("title") == "Result 1280" and "found lamp" in (sent.get("text") or ""), {k: sent.get(k) for k in ("title", "text", "console")})
            moved = sm.render_page(f"http://127.0.0.1:{st_port}{url}", [{"click": "#state"}], "", 1280, False)
            check("location.assign to a root path remains in the Live relay", moved.get("url") == "/result?q=state" and "found state" in (moved.get("text") or ""), moved)
            held = sm.render_page(f"http://127.0.0.1:{st_port}{url}keys", [{"press": "KeyW", "hold_ms": 700}], "", 1280, False)
            check("a key can be held down, as games need", "held KeyW long" in (held.get("text") or ""), held.get("text"))
            check("warnings are kept apart from errors", sm.page_errors(held) == 1 and len(held["console"]) == 2, held.get("console"))
            # In the agent's own Chromium (the Browser tab watches it): the live page comes through the relay, the rest stays blocked.
            exe = subprocess.run(["node", "-e", "const {createRequire}=require('module');const r=createRequire(process.argv[1]);console.log(r('playwright').chromium.executablePath())",
                                  os.path.join(ROOT, "tests", "browser", "package.json")], capture_output=True, text=True).stdout.strip()
            port, profile = free_port(), tempfile.mkdtemp()
            chrome = subprocess.Popen([exe, "--headless=new", f"--remote-debugging-port={port}", f"--user-data-dir={profile}", "--proxy-server=http://127.0.0.1:9",
                                       "--proxy-bypass-list=<-loopback>", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                cdp = f"http://127.0.0.1:{port}"
                def up():
                    try:
                        return urllib.request.urlopen(cdp + "/json/version", timeout=1).status == 200
                    except OSError:
                        return False
                wait_until(up, 15)
                seen_shared = sm.render_page(f"http://127.0.0.1:{st_port}{url}form", [{"type": "#q", "text": "lamp"}, {"press": "Enter"}], "", 390, False, cdp=cdp)
                check("with the agent's browser, view_page opens the page there, through the relay past its proxy", seen_shared.get("shared") is True
                      and (seen_shared.get("title") or "").startswith("Result ") and "found lamp" in (seen_shared.get("text") or ""), {k: seen_shared.get(k) for k in ("shared", "title", "text", "console")})
                tabs = json.load(urllib.request.urlopen(cdp + "/json/list", timeout=2))
                check("and leaves its one tab open for the person to see", sum(1 for t in tabs if "/live/" in t.get("url", "")) == 1, [t.get("url") for t in tabs])
                sm.render_page(f"http://127.0.0.1:{st_port}{url}form", [], "", 1280, False, cdp=cdp)
                tabs = json.load(urllib.request.urlopen(cdp + "/json/list", timeout=2))
                check("a second check replaces that tab rather than adding one", sum(1 for t in tabs if "/live/" in t.get("url", "")) == 1, [t.get("url") for t in tabs])
            finally:
                chrome.terminate()
            check("without the browser tools, view_page keeps its own hidden Chromium", sm.agent_browser_cdp(types.SimpleNamespace(mcp_tools={})) is None)
        else:
            check("(Node or Playwright missing: phone and form checks skipped)", True)
        asked = run_agent(c, ws, c.post("/api/agent/sessions", {"ws": ws})[1]["id"], f"Please view page {inside} /", on_approval=lambda ev: ("reject", ""))[1]
        approval = next(e for e in asked if e["type"] == "approval")
        check("without auto-run it asks first, showing the address and steps", approval["kind"] == "command" and f"localhost:{inside}/" in approval["command"] and "click #go" in approval["command"], approval)
        check("a refusal opens nothing", not any(e["type"] == "page_view" for e in asked))
        refused = run_agent(c, ws, c.post("/api/agent/sessions", {"ws": ws})[1]["id"], f"Please view page {outside} /", auto_commands=True)[1]
        refused = next(e for e in refused if e["type"] == "tool_result" and e["name"] == "view_page")
        check("the model cannot open a server from outside the folder", refused["ok"] is False and "no program started in this project" in refused["output"], refused["output"][:200])
        events = run_agent(c, ws, c.post("/api/agent/sessions", {"ws": ws})[1]["id"], f"Please open preview {inside}", auto_commands=True)[1]
        opened = [e for e in events if e["type"] == "open_live"]
        result = next(e for e in events if e["type"] == "tool_result" and e["name"] == "open_preview")
        check("open_preview opens the page in the user's Live tab", opened == [{**opened[0], "port": inside, "path": "/"}] and result["ok"] and "Live tab" in result["output"], opened)
        refused = run_agent(c, ws, c.post("/api/agent/sessions", {"ws": ws})[1]["id"], f"Please open preview {outside}", auto_commands=True)[1]
        check("but not a server from outside the folder", not any(e["type"] == "open_live" for e in refused)
              and next(e for e in refused if e["type"] == "tool_result" and e["name"] == "open_preview")["ok"] is False)
        app.terminate()
        app.wait(5)
        status, _, resp = c.get(url + "api/echo")
        check("a stopped server gives a clear error the page can read", status == 502 and resp.getheader("Access-Control-Allow-Origin") == "null"
              and resp.getheader("Access-Control-Allow-Credentials") == "true")
    finally:
        for proc in (app, other):
            if proc.poll() is None:
                proc.terminate()
    saved = sm.APP
    sm.APP = types.SimpleNamespace(cfg={"delete_tool": True, "skills": True, "plan_tool": True, "live_view": False, "mcp": True})
    try:
        off = sm.execute_tool(types.SimpleNamespace(plan=None, note_progress=lambda value: None), "view_page", {"port": 80})
        check("with live_view off, view_page is an unknown tool", off[0] is False and off[1] == "Unknown tool", off)
        sm.APP.cfg["live_view"] = True
        try:
            sm.tool_view_page(types.SimpleNamespace(), {"port": 80, "width": 100})
            narrow = None
        except sm.ApiError as e:
            narrow = str(e)
        check("a window narrower than a phone is refused", narrow and "320 to 2560" in narrow, narrow)
    finally:
        sm.APP = saved


PNG_1PX = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="


def test_attachments_and_scripts(c, projects, st_port, gw_port, cfg_path):
    print("pictures, text files and mixed scripts")
    sys.path.insert(0, ROOT)
    import studio as sm
    folder = os.path.join(projects, "attached")
    os.makedirs(folder)
    ws = c.post("/api/workspace/open", {"path": folder})[1]["workspace"]["id"]
    session = c.post("/api/agent/sessions", {"ws": ws})[1]["id"]
    before = len(c_get_json(gw_port, "/_mock/requests")["requests"])
    body = {"session": session, "ws": ws, "message": "hello, what is in this picture?", "model": "quartz:strata",
            "attachments": [{"kind": "image", "name": "shot.png", "data": PNG_1PX}, {"kind": "text", "name": "notes.md", "text": "# Notes\nremember the pelican"}]}
    status, data, _ = c.post("/api/agent/send", body)
    check("a message can carry a picture and a text file", status == 200, data)
    events = [ev for _, ev in c.sse(f"/api/agent/stream?run={data['run']}", timeout=30)]
    asked = c_get_json(gw_port, "/_mock/requests")["requests"][before:]
    check("the picture reaches the model as an image part", asked and len(asked[0]["image_parts"]) == 1 and PNG_1PX.startswith(asked[0]["image_parts"][0]), asked[:1])
    stored = c.get(f"/api/agent/session?id={session}")[1]
    user = next(e for e in stored["events"] if e["type"] == "user")
    check("the chat shows the picture and the file's name", len(user.get("images", [])) == 1 and user["images"][0]["name"] == "shot.png" and user.get("files") == ["notes.md"], user)
    saved = json.load(open(os.path.join(os.path.dirname(cfg_path), "data", "sessions", session + ".json")))
    message = next(m for m in saved["messages"] if m["role"] == "user")
    check("the text file goes in as text, the picture beside the chat", isinstance(message["content"], str) and "remember the pelican" in message["content"]
          and "Attached file notes.md" in message["content"] and "base64" not in json.dumps(saved), message)
    check("the language is still read from the typed words", sm.typed_part(message["content"]) == "hello, what is in this picture?")
    pic = user["images"][0]["id"]
    status, image, resp = c.get(f"/api/agent/attachment?session={session}&id={pic}")
    check("the picture can be shown again", status == 200 and resp.getheader("Content-Type") == "image/png" and image[:4] == b"\x89PNG")
    check("only to the chat's owner", Client(st_port).get(f"/api/agent/attachment?session={session}&id={pic}")[0] == 401)
    check("and an id cannot leave the folder", c.get(f"/api/agent/attachment?session={session}&id=../{session}.json")[0] == 404)
    later = len(c_get_json(gw_port, "/_mock/requests")["requests"])
    run_agent(c, ws, session, "hello again")
    check("the picture stays in the conversation for later turns", c_get_json(gw_port, "/_mock/requests")["requests"][later]["image_parts"] != [])
    send = lambda extra: c.post("/api/agent/send", {"session": c.post("/api/agent/sessions", {"ws": ws})[1]["id"], "ws": ws, "message": "hello", "model": "quartz:strata", **extra})
    check("a model that cannot see pictures is refused one", send({"model": "qwen2.5-coder:3b", "attachments": [{"kind": "image", "name": "a.png", "data": PNG_1PX}]})[0] == 400)
    check("other kinds of file are refused", send({"attachments": [{"kind": "pdf", "name": "a.pdf"}]})[0] == 400)
    check("so is a picture that is not one", send({"attachments": [{"kind": "image", "name": "a.png", "data": "data:text/html;base64,PGI+"}]})[0] == 400)
    check("and text with NUL bytes", send({"attachments": [{"kind": "text", "name": "a.bin", "text": "a\u0000b"}]})[0] == 400)
    check("and too many files", send({"attachments": [{"kind": "text", "name": f"{i}.txt", "text": "x"} for i in range(9)]})[0] == 400)
    events = run_agent(c, ws, c.post("/api/agent/sessions", {"ws": ws})[1]["id"], "Run this: echo first; pkill -f 'marker-for-studio-42'; echo still-here", auto_commands=True)[1]
    ran = next(e for e in events if e["type"] == "tool_result" and e["name"] == "run")
    check("pkill -f with part of the command does not kill the command's own shell", ran["ok"] is True and "still-here" in ran["output"], ran["output"][-200:])

    check("deleting the chat removes its pictures", c.post("/api/agent/session/delete", {"id": session})[0] == 200
          and not os.path.exists(os.path.join(os.path.dirname(cfg_path), "data", "attachments", session)))

    greek_session = c.post("/api/agent/sessions", {"ws": ws})[1]["id"]
    events = run_agent(c, ws, greek_session, "Γράψε μου σε παρακαλώ μια σύντομη περίληψη της δουλειάς greeklish")[1]
    statuses = [e.get("text") for e in events if e["type"] == "status"]
    reply = "".join(e.get("text", "") for e in events if e["type"] == "token")
    check("a Greek reply in Latin letters is sent back once", "The reply mixed scripts or languages. Asking the model to write it again…" in statuses, statuses)
    check("and the model writes it again in Greek", "Το πρόγραμμα τρέχει κανονικά" in reply and next(e for e in events if e["type"] == "done")["reason"] == "answered")
    done = next(e for e in events if e["type"] == "done")
    check("a plain answer without tools is not called a summary", done["reason"] == "answered" and "summary_reply" not in done, done)
    marked = [e for e in events if e["type"] == "sent_back"]
    check("the page is told the reply was sent back, and why, so it can mark it", len(marked) == 1 and "Greeklish" in marked[0]["why"] and "Write it in" not in marked[0]["why"], marked)
    notes = [m["content"] for m in c.get(f"/api/agent/session?id={greek_session}")[1]["events"] if False] or \
            [m["content"] for m in json.load(open(os.path.join(os.path.dirname(cfg_path), "data", "sessions", greek_session + ".json")))["messages"] if m["role"] == "user" and m["content"].startswith("Studio note")]
    check("the note says what was wrong", len(notes) == 1 and "Greeklish" in notes[0], notes)
    events = run_agent(c, ws, c.post("/api/agent/sessions", {"ws": ws})[1]["id"], "Γράψε μου σε παρακαλώ μια σύντομη περίληψη greeklish finish", auto_apply=True, auto_commands=True)[1]
    done = next(e for e in events if e["type"] == "done")
    check("a finish summary in Greeklish is sent back too", done["reason"] == "finished" and done.get("summary", "").startswith("Το πρόγραμμα"), done)
    events = run_agent(c, ws, c.post("/api/agent/sessions", {"ws": ws})[1]["id"], "Γράψε μου σε παρακαλώ μια σύντομη περίληψη greeklish finish twice", auto_apply=True, auto_commands=True)[1]
    done = next(e for e in events if e["type"] == "done")
    shown = [e.get("output") for e in events if e["type"] == "tool_result" and e.get("name") == "finish"]
    check("after its one send-back, a summary's Chinese characters are still removed", done["reason"] == "finished" and "写" not in done.get("summary", "")
          and done["summary"].startswith("Το πρόγραμμα τρέχει κανονικά") and shown and "写" not in shown[-1], (done.get("summary"), shown[-1:]))
    events = run_agent(c, ws, c.post("/api/agent/sessions", {"ws": ws})[1]["id"], "Please tell me how the server is doing now, greek-reply")[1]
    reply = "".join(e.get("text", "") for e in events if e["type"] == "token")
    check("an English chat answered in Greek with Chinese is sent back too", "The reply mixed scripts or languages. Asking the model to write it again…" in [e.get("text") for e in events if e["type"] == "status"]
          and reply.endswith("The server is running on port 8000 and the page loads without errors."), reply[-120:])
    aside_session = c.post("/api/agent/sessions", {"ws": ws})[1]["id"]
    events = run_agent(c, ws, aside_session, "Γράψε μου σε παρακαλώ τι βρήκες στα αρχεία, chinese aside", auto_apply=True, auto_commands=True)[1]
    reply = "".join(e.get("text", "") for e in events if e["type"] == "token")
    stored = json.load(open(os.path.join(os.path.dirname(cfg_path), "data", "sessions", aside_session + ".json")))["messages"]
    at = [i for i, m in enumerate(stored) if m["role"] == "user" and str(m["content"]).startswith("Studio note: the message you wrote with those tool calls")]
    check("Chinese in text that came with a tool call gets a note after the call's result, and the run carries on",
          "Η σημείωση ήρθε" in reply and len(at) == 1 and stored[at[0] - 1]["role"] == "tool" and "需要形态" in stored[at[0]]["content"], (reply[-80:], at))
    done = next(e for e in events if e["type"] == "done")
    check("a run that used tools and ended on a written reply marks that reply as its summary", done["reason"] == "answered" and done.get("summary_reply") is True, done)
    check("one Chinese character in Greek is enough", "Chinese" in sm.mixed_scripts("Η λέξη εμφανίζεται κάτω από το πλαίσιο需 και μένει εκεί."))
    asked_greek = c.post("/api/agent/sessions", {"ws": ws})[1]["id"]
    events = run_agent(c, ws, asked_greek, "Please tell me how the server is doing now, greek-reply, and answer in Greek")[1]
    reply = "".join(e.get("text", "") for e in events if e["type"] == "token")
    notes = [m["content"] for m in json.load(open(os.path.join(os.path.dirname(cfg_path), "data", "sessions", asked_greek + ".json")))["messages"]
             if m["role"] == "user" and m["content"].startswith("Studio note: your last")]
    check("when the user asked for Greek, the note asks for clean Greek, not English", len(notes) == 1 and "Write it in Greek" in notes[0] and "user writes English" not in notes[0], notes)
    check("and the rewrite stays in Greek", reply.endswith("Οι λέξεις εμφανίζονται κανονικά κάτω από το πλαίσιο."), reply[-100:])
    check("a requested language is read from the message", sm.asked_language([{"role": "user", "content": "Do it. In your final reply, in Greek, list problems"}]) == "Greek"
          and sm.asked_language([{"role": "user", "content": "Γράψε το στα αγγλικά"}]) == "English" and sm.asked_language([{"role": "user", "content": "Greek words are fine"}]) is None)
    task = [{"role": "user", "content": "Build the island. Finish with a short summary in Greek."}, {"role": "assistant", "content": "Working."}]
    check("a steer or Continue in English keeps the task's language", sm.asked_language(task + [
        {"role": "user", "content": sm.GUIDE_NOTE + "\n\nGo on with the trailer (step 10)."}, {"role": "user", "content": "Continue with the trailer."}]) == "Greek")
    check("so does the goal's own message", sm.asked_language(task + [{"role": "user", "content": "Carry out every step of the request above."}],
                                                              goal="Carry out every step of the request above.") == "Greek")
    check("a language asked for in an earlier task does not carry over", sm.asked_language([{"role": "user", "content": "Build it. In your final reply, in Greek, list problems"},
                                                                                           {"role": "assistant", "content": "Done."}, {"role": "user", "content": "Now fix the timer"}]) is None)
    run_agent(c, ws, c.post("/api/agent/sessions", {"ws": ws})[1]["id"], "Γράψε μου σε παρακαλώ μια σύντομη περίληψη της δουλειάς", auto_apply=True, auto_commands=True)
    system = c_get_json(gw_port, "/_mock/requests")["requests"][-1]["system"]
    check("Greek chats are told to use the Greek alphabet", "never Greek words in Latin letters (Greeklish)" in system)
    check("plain Greek with English terms passes", sm.mixed_scripts("Ο server τρέχει τώρα. Τα tests περνούν όλα (16/16) και η σελίδα ελέγχθηκε με το view_page. Δείτε το data/words.json και τρέξτε `python3 server.py`.") == "")
    check("words mixing alphabets are caught", "mix Greek and Latin" in sm.mixed_scripts("Η σελίδα epveβαιώθη, τονώνou, σtypo και όλα δουλεύουν σωστά εδώ."))
    check("Chinese inside Greek is caught", "Chinese" in sm.mixed_scripts("Πάνελ σκορ με三μετρητές και μπαράκος倒计时 στην κορυφή."))
    check("one word mixing alphabets is enough, even with one Greek letter", sm.mixed_words("λουλούδι,_potάμι και Σcaffolding στα αρχείa") == ["potάμι", "Σcaffolding", "αρχείa"]
          and "potάμι" in sm.mixed_scripts("Το νησί έχει μια potάμι βάρκα που περιμένει τα παιδιά."))
    check("symbols, code and English terms do not count", sm.mixed_words("Η μεταβολή Δx και Δt, το `mapΑβγ` στο app.js, και ο server τρέχει") == [])
    check("an English reply is left alone", sm.mixed_scripts("The server runs, and all of the tests pass. I checked the page to see it works: " * 10) == "")
    check("Greeklish without a single Greek letter is caught", "Greeklish" in sm.mixed_scripts("To programma trexei kai den exei provlima. I selida einai etoimi gia tin dokimi tou xristi."))


def make_docx(path, paragraphs, table=None):
    """A small Word file, written by hand: the parts Studio reads, nothing else."""
    import zipfile
    w = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    body = "".join(f'<w:p>{"<w:pPr><w:pStyle w:val=%sHeading1%s/></w:pPr>" % (chr(34), chr(34)) if i == 0 else ""}<w:r><w:t>{p}</w:t></w:r></w:p>' for i, p in enumerate(paragraphs))
    if table:
        body += "<w:tbl>" + "".join("<w:tr>" + "".join(f"<w:tc><w:p><w:r><w:t>{c}</w:t></w:r></w:p></w:tc>" for c in row) + "</w:tr>" for row in table) + "</w:tbl>"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", f'<?xml version="1.0" encoding="UTF-8"?><w:document {w}><w:body>{body}</w:body></w:document>')


def make_xlsx(path, sheets):
    """A small Excel file with shared strings, numbers and a formula."""
    import zipfile
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    strings = []
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/workbook.xml", f'<workbook {ns}><sheets>' + "".join(f'<sheet name="{n}" sheetId="{i + 1}" r:id="rId{i + 1}"/>' for i, n in enumerate(sheets)) + "</sheets></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + "".join(
            f'<Relationship Id="rId{i + 1}" Target="worksheets/sheet{i + 1}.xml"/>' for i in range(len(sheets))) + "</Relationships>")
        for i, rows in enumerate(sheets.values()):
            xml = ""
            for r, row in enumerate(rows, 1):
                cells = ""
                for c, v in enumerate(row):
                    ref = chr(65 + c) + str(r)
                    if isinstance(v, str) and v.startswith("="):
                        cells += f'<c r="{ref}"><f>{v[1:]}</f><v>42</v></c>'
                    elif isinstance(v, str):
                        strings.append(v)
                        cells += f'<c r="{ref}" t="s"><v>{len(strings) - 1}</v></c>'
                    else:
                        cells += f'<c r="{ref}"><v>{v}</v></c>'
                xml += f'<row r="{r}">{cells}</row>'
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml", f'<worksheet {ns}><sheetData>{xml}</sheetData></worksheet>')
        z.writestr("xl/sharedStrings.xml", f'<sst {ns}>' + "".join(f"<si><t>{t}</t></si>" for t in strings) + "</sst>")


def test_office_view(c, projects):
    print("Office documents in the editor: pages by LibreOffice in bubblewrap, workbooks as sheets")
    import studio as sm
    if not (shutil.which("soffice") or shutil.which("libreoffice")) or not shutil.which("bwrap"):
        check("(LibreOffice or bubblewrap missing: the document viewer checks were skipped; sudo apt install libreoffice bubblewrap)", True)
        return
    folder = os.path.join(projects, "office-view")
    os.makedirs(folder)
    open(os.path.join(folder, "letter.docx"), "wb").write(sm.markdown_docx("# Επιστολή\n\nΚαλημέρα σας.\n\n| Α | Β |\n|---|---|\n| 1 | 2 |\n", "Επιστολή"))
    make_xlsx(os.path.join(folder, "book.xlsx"), {"Έσοδα": [["Μήνας", "Ποσό"], ["Νοέμβριος", 1200], ["Σύνολο", "=SUM(B2)"]], "Κενό": []})
    open(os.path.join(folder, "real.xlsx"), "wb").write(sm.sheets_xlsx([{"name": "Έσοδα", "rows": [["Μήνας", "Ποσό"], ["Νοέμβριος", 1200]]}]))
    open(os.path.join(folder, "notes.txt"), "w").write("plain")
    ws = c.post("/api/workspace/open", {"path": folder})[1]["workspace"]["id"]
    q = lambda route, path: c.get(f"/api/files/{route}?ws={ws}&path=" + urllib.parse.quote(path))
    began = time.time()
    s, body, resp = q("doc_pdf", "letter.docx")
    first = time.time() - began
    check("a Word file comes back as a PDF", s == 200 and isinstance(body, bytes) and body.startswith(b"%PDF") and resp.getheader("Content-Type") == "application/pdf", (s, str(body)[:300]))
    pdf = os.path.join(folder, "letter.pdf")
    open(pdf, "wb").write(body)
    text = subprocess.run(["pdftotext", pdf, "-"], capture_output=True, text=True).stdout if shutil.which("pdftotext") else "Καλημέρα σας."
    check("with its Greek text", "Καλημέρα σας." in text, text[:200])
    began = time.time()
    s2, again, _ = q("doc_pdf", "letter.docx")
    check("the second time it comes from the cache", s2 == 200 and again == body and time.time() - began < max(0.5, first / 2), (first, time.time() - began))
    s, body, _ = q("doc_pdf", "real.xlsx")
    check("a workbook can be shown as pages too", s == 200 and body.startswith(b"%PDF"), s)
    check("other files are not converted", q("doc_pdf", "notes.txt")[0] == 400 and q("doc_pdf", "missing.docx")[0] == 404 and q("doc_pdf", "../x.docx")[0] in (400, 403))
    s, d, _ = q("sheets", "book.xlsx")
    sheets = {x["name"]: x for x in d.get("sheets", [])} if s == 200 else {}
    check("a workbook comes back as sheets of cells, with formulas' values", s == 200 and sheets["Έσοδα"]["rows"][1] == ["Νοέμβριος", "1200"]
          and sheets["Έσοδα"]["rows"][2][1] == "42" and sheets["Έσοδα"]["first_row"] == 1 and sheets["Έσοδα"]["formulas"] == 1 and sheets["Κενό"]["rows"] == [], d)
    ods = os.path.join(folder, "book.ods")
    open(ods, "wb").write(sm.office_convert(os.path.join(folder, "real.xlsx"), "ods"))
    s, d, _ = q("sheets", "book.ods")
    check("an OpenDocument sheet is read through LibreOffice", s == 200 and any(x["name"] == "Έσοδα" and ["Νοέμβριος", "1200"] in x["rows"] for x in d["sheets"]), d)
    check("only workbooks have sheets", q("sheets", "letter.docx")[0] == 400)
    # The jail: LibreOffice sees no home folder and has no network.
    jail = sm.office_jail(folder)
    probe = subprocess.run(jail + ["bash", "-c", f"ls {os.path.expanduser('~')} 2>/dev/null; ls /work; (exec 3<>/dev/tcp/127.0.0.1/{c.port}) 2>/dev/null && echo NET"],
                           capture_output=True, text=True, timeout=30) if shutil.which("bash") else None
    check("documents are opened in a jail without the home folder or the network", probe and "letter.docx" in probe.stdout and "NET" not in probe.stdout
          and os.path.basename(os.path.expanduser("~")) not in probe.stdout.replace("letter.docx", ""), probe and probe.stdout[:300])
    codes = (c.post("/api/prefs", {"key": "minimap", "value": False})[0], c.post("/api/prefs", {"key": "minimap", "value": "no"})[0])
    check("the minimap is a setting that follows the person", codes == (200, 400), codes)
    codes = (c.post("/api/prefs", {"key": "models_shown", "value": ["quartz:strata", "codex:gpt-6-luna"]})[0],
             c.post("/api/prefs", {"key": "models_shown", "value": []})[0], c.post("/api/prefs", {"key": "models_shown", "value": "quartz"})[0],
             c.post("/api/prefs", {"key": "models_shown", "value": [1]})[0])
    check("the models the picker shows are a list of names that follows the person", codes == (200, 200, 400, 400), codes)
    codes = (c.post("/api/prefs", {"key": "pc_names", "value": {"DESKTOP-K6F5SDF": "DEV PC"}})[0], c.post("/api/prefs", {"key": "pc_names", "value": {}})[0],
             c.post("/api/prefs", {"key": "pc_names", "value": {"X": ""}})[0], c.post("/api/prefs", {"key": "pc_names", "value": {"X": "a" * 41}})[0],
             c.post("/api/prefs", {"key": "pc_names", "value": "DEV PC"})[0], c.post("/api/prefs", {"key": "pc_names", "value": {"X": "line\nbreak"}})[0])
    check("names for one's computers are a setting: short, printable, per machine", codes == (200, 200, 400, 400, 400, 400), codes)


def test_documents(c, projects, gw_port):
    print("Word and Excel files for the model")
    import base64, studio as sm
    folder = os.path.join(projects, "office")
    os.makedirs(folder)
    make_docx(os.path.join(folder, "report.docx"), ["Αναφορά Νοεμβρίου", "Πρώτη παράγραφος."], [["Όνομα", "Ποσό"], ["Μαρία", "12"]])
    make_xlsx(os.path.join(folder, "budget.xlsx"), {"Έσοδα": [["Μήνας", "Ποσό"], ["Νοέμβριος", 1200], ["Σύνολο", "=SUM(B2)"]], "Κενό": []})
    text = sm.read_document_file(os.path.join(folder, "report.docx"))
    check("a Word file reads as headings, paragraphs and Markdown tables", "# Αναφορά Νοεμβρίου" in text and "Πρώτη παράγραφος." in text and "| Μαρία | 12 |" in text, text)
    text = sm.read_document_file(os.path.join(folder, "budget.xlsx"))
    check("an Excel file reads sheet by sheet, with formulas' values", "## Sheet: Έσοδα" in text and "| Νοέμβριος | 1200 |" in text and "| Σύνολο | 42 |" in text
          and "1 formulas" in text and "## Sheet: Κενό (empty)" in text, text)
    open(os.path.join(folder, "old.xls"), "wb").write(b"\xd0\xcf\x11\xe0 not really")
    try:
        sm.read_document_file(os.path.join(folder, "old.xls"))
        refused = False
    except sm.ApiError as e:
        refused = "only for admins" in e.message
    check("old binary formats need an admin, since LibreOffice opens them on the server", refused)
    with open(os.path.join(folder, "bomb.docx"), "wb") as fh:
        import zipfile
        with zipfile.ZipFile(fh, "w") as z:
            z.writestr("word/document.xml", '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><w:document/>')
    try:
        sm.read_document_file(os.path.join(folder, "bomb.docx"))
        refused = False
    except sm.ApiError as e:
        refused = "does not read" in e.message
    check("XML with entity tricks is refused", refused)
    ws = c.post("/api/workspace/open", {"path": folder})[1]["workspace"]["id"]
    sid = c.post("/api/agent/sessions", {"ws": ws})[1]["id"]
    data = "data:application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;base64," + base64.b64encode(open(os.path.join(folder, "budget.xlsx"), "rb").read()).decode()
    status, r, _ = c.post("/api/agent/send", {"session": sid, "ws": ws, "message": "Τι λέει το αρχείο;", "model": "quartz:strata",
                                              "attachments": [{"kind": "document", "name": "budget.xlsx", "data": data}]})
    check("an attached Excel file is accepted", status == 200, r)
    stored = json.load(open(os.path.join(projects, "..", "app", "data", "sessions", sid + ".json")))
    first = stored["messages"][0]["content"]
    check("and the model gets its sheets as text, named in the chat", "Attached file budget.xlsx" in first and "| Νοέμβριος | 1200 |" in first
          and stored["events"][0].get("files") == ["budget.xlsx"], first[:400])
    if status == 200:
        c.post("/api/agent/stop", {"run": r["run"]})
        time.sleep(1)
    # Too large for the chat: saved in the folder's attachments/, a workbook also as CSV with every row, the model gets the start
    make_xlsx(os.path.join(projects, "contacts export.xlsx"), {"Επαφές": [["email", "name"]] + [[f"p{i}@example.com", f"Γονέας {i}"] for i in range(5000)], "Λίστες": [["list"], ["Γονείς"]]})
    big = "data:application/octet-stream;base64," + base64.b64encode(open(os.path.join(projects, "contacts export.xlsx"), "rb").read()).decode()
    sid2 = c.post("/api/agent/sessions", {"ws": ws})[1]["id"]
    status, r, _ = c.post("/api/agent/send", {"session": sid2, "ws": ws, "message": "Import them", "model": "quartz:strata",
                                              "attachments": [{"kind": "document", "name": "contacts export.xlsx", "data": big},
                                                              {"kind": "text", "name": "log.txt", "text": "line of a long log\n" * 15000}]})
    if status == 200:
        c.post("/api/agent/stop", {"run": r["run"]})
        time.sleep(1)
    saved = (r or {}).get("saved_files") or []
    check("a workbook too large for the chat is accepted and saved in the folder, with a CSV per sheet", status == 200 and saved[:3] == [
          "attachments/contacts export.xlsx", "attachments/contacts export/Επαφές.csv", "attachments/contacts export/Λίστες.csv"] and "attachments/log.txt" in saved, r)
    rows = open(os.path.join(folder, "attachments", "contacts export", "Επαφές.csv"), encoding="utf-8").read().splitlines()
    big_csv = os.path.join(folder, "big.csv")
    with open(big_csv, "w", encoding="utf-8") as fh:
        fh.write("\ufeffemail,name\n" + "".join(f"p{i}@example.com,Γονέας {i} με ένα αρκετά μακρύ όνομα για να γεμίσει\n" for i in range(40000)))
    run_ns = types.SimpleNamespace(ws={"path": os.path.realpath(folder), "id": ws}, owner="admin")
    ok, summary, body = sm.tool_read_file(run_ns, {"path": "big.csv", "start_line": 39999})
    check("read_file reads a text file over 2 MB by line range", os.path.getsize(big_csv) > 2 * 1024 * 1024 and ok and "lines 39999-40001 of 40001" in summary
          and body.rstrip().endswith("p39999@example.com,Γονέας 39999 με ένα αρκετά μακρύ όνομα για να γεμίσει"), summary)
    ok, summary, body = sm.tool_read_file(run_ns, {"path": "big.csv"})
    check("from its start, without the byte-order mark, with what remains", body.split("\n", 1)[0] == "     1\temail,name" and "39601 more lines remain; continue with start_line=401" in body, body[:80])
    check("the CSV has every row, beyond what the text shows", len(rows) == 5001 and rows[-1] == "p4999@example.com,Γονέας 4999", rows[-1:])
    stored = json.load(open(os.path.join(projects, "..", "app", "data", "sessions", sid2 + ".json")))
    first = stored["messages"][0]["content"]
    check("the model gets the start and where the rest is", "saved it in the project as attachments/contacts export.xlsx" in first
          and "attachments/contacts export/Επαφές.csv: sheet 'Επαφές', 5001 rows" in first and "| p0@example.com | Γονέας 0 |" in first
          and "p4999@example.com" not in first and len(first) < 60000 and stored["events"][0].get("saved_files") == saved, first[:600])
    status, r, _ = c.post("/api/agent/send", {"session": sid2, "ws": ws, "message": "Again", "model": "quartz:strata",
                                              "attachments": [{"kind": "document", "name": "contacts export.xlsx", "data": big}]})
    if status == 200:
        c.post("/api/agent/stop", {"run": r["run"]})
        time.sleep(1)
    check("the same name again gets a new file, not over the first", "attachments/contacts export (2).xlsx" in (r or {}).get("saved_files", []), r)
    bad = c.post("/api/agent/send", {"session": sid, "ws": ws, "message": "x", "model": "quartz:strata",
                                     "attachments": [{"kind": "document", "name": "virus.exe", "data": "data:x;base64,AAAA"}]})
    check("only document types are accepted as documents", bad[0] == 400, bad[1])
    run = types.SimpleNamespace(ws={"path": os.path.realpath(folder), "id": ws}, owner="admin")
    ok, summary, body = sm.tool_read_file(run, {"path": "report.docx"})
    check("the agent's read_file reads Word files too", ok and "| Μαρία | 12 |" in body and "report.docx" in summary, body[:300])
    # write_document: Word from Markdown and Excel from rows, approved like an edit, undone by removing the new file
    saved_app = sm.APP
    sm.APP = types.SimpleNamespace(data_dir=os.path.join(folder, "..", "docs-data"), cfg={"script_check": True, "document_tool": True})
    asked, events = [], []
    run = types.SimpleNamespace(ws={"path": os.path.realpath(folder), "id": "docws"}, owner="admin", auto_apply=False, changed=False,
                                ask=lambda **kw: (asked.append(kw), ("accept", ""))[1], emit=lambda kind, **kw: events.append((kind, kw)))
    md = "# Σχέδιο\n\nΚείμενο με **έντονα**.\n\n1. Ένα\n2. Δύο\n\n| Όνομα | Ποσό |\n| --- | --- |\n| Μαρία | 12 |\n"
    ok, summary, body = sm.tool_write_document(run, {"path": "out/plan.docx", "content": md, "title": "Αναφορά"})
    made = os.path.join(folder, "out", "plan.docx")
    text = sm.read_document_file(made) if os.path.isfile(made) else ""
    check("write_document makes a Word file that reads back with its headings, lists and table", ok and "# Σχέδιο" in text and "- Ένα" in text
          and "| Μαρία | 12 |" in text and "# Αναφορά" in text, body)
    check("it asks like an edit, showing the document's text", asked and asked[0]["action"] == "document" and "| Μαρία | 12 |" in asked[0]["new"]
          and events and events[-1][0] == "file_changed" and events[-1][1]["action"] == "document" and run.changed, asked[:1])
    ok, summary, body = sm.tool_write_document(run, {"path": "out/sums.xlsx", "sheets": [{"name": "Ποσά", "rows": [["Μήνας", "Ποσό"], ["Νοέμβριος", 1200], ["Δεκέμβριος", "300"], ["Σύνολο", "=SUM(B2:B3)"]]}]})
    text = sm.read_document_file(os.path.join(folder, "out", "sums.xlsx"))
    check("and an Excel file with numbers and a formula", ok and "## Sheet: Ποσά" in text and "| Νοέμβριος | 1200 |" in text and "=SUM(B2:B3)" in text, text)
    import zipfile
    sheet = zipfile.ZipFile(os.path.join(folder, "out", "sums.xlsx")).read("xl/worksheets/sheet1.xml").decode()
    check("numbers written as text become numbers, the header is bold and frozen", '<c r="B3"><v>300</v></c>' in sheet and 's="1"' in sheet and 'state="frozen"' in sheet, sheet[:400])
    asked.clear()
    ok, summary, body = sm.tool_write_document(run, {"path": "out/sums.xlsx", "content": "| Μήνας | Ποσό |\n|---|---|\n| Ιανουάριος | 5 |"})
    trash = os.path.join(folder, ".strata-studio", "trash")
    check("replacing one shows the change from the old text and keeps the old file in the trash", ok and asked and "Νοέμβριος" in asked[0]["old"]
          and "Ιανουάριος" in asked[0]["new"] and os.path.isdir(trash) and "in the trash" in body, body)
    run.ask = lambda **kw: ("reject", "not now")
    ok, summary, body = sm.tool_write_document(run, {"path": "out/no.docx", "content": "x"})
    check("a rejected document is not written", not ok and not os.path.exists(os.path.join(folder, "out", "no.docx")) and "not now" in body, body)
    for bad, words in (({"path": "a.pdf", "content": "x"}, ".docx (Word) and .xlsx"), ({"path": "a.docx"}, "content is required"),
                       ({"path": "a.xlsx", "sheets": [{"name": "x", "rows": "nope"}]}, "rows must be")):
        try:
            sm.tool_write_document(run, bad)
            refused = ""
        except sm.ApiError as e:
            refused = e.message
        check(f"write_document refuses {bad['path']} without what it needs", words in refused, refused)
    check("its plan-mode and runner switches exist", "write_document" in sm.PLAN_REFUSED and sm.DEFAULT_CONFIG["document_tool"] is True)
    for cmd, waits in (("Start-Sleep -Seconds 60; Write-Output ok", True), ("sleep 5", True), ("timeout /t 30 /nobreak", True), ("Start-Sleep 2", True),
                       ("sleep 5 && npm test", False), ("python wait.py", False)):
        check(f"{cmd!r} {'is' if waits else 'is not'} a wait", bool(sm.WAIT_COMMAND.fullmatch(cmd)) == waits)
    saved_cmd = sm.run_online_if_allowed
    sm.run_online_if_allowed = lambda run, command: (True, "exit 0", "exit code 0\nok")
    sm.APP.cfg["turn_guard"] = True
    waiter = types.SimpleNamespace(ws={"path": os.path.realpath(folder), "id": "docws"}, plan_mode=False, auto_commands=True, changed=False)
    outs = [sm.tool_run(waiter, {"command": "Start-Sleep -Seconds 60; Write-Output ok"})[2] for _ in range(3)]
    sm.run_online_if_allowed = saved_cmd
    check("the third wait in a run gets a note: check what you wait for, stuck is not slow", "Studio note" not in outs[1] and "wait number 3" in outs[2] and "stuck, not slow" in outs[2], outs[2])
    saved_helpers = sm.HELPERS
    sm.HELPERS = types.SimpleNamespace(get=lambda hid: {"shell": "powershell" if hid == "win" else "bash", "machine": "PC", "os": "windows", "remote_path": "C:\\x"})
    heredoc = "python - <<'PY'\nprint(1)\nPY"
    problem = sm.powershell_problem({"helper": "win"}, heredoc)
    check("a heredoc for a Windows PowerShell folder is sent back before it runs, with the way to do it", problem and "write_file" in problem and "python check.py" in problem, problem)
    check("PowerShell's own commands and bash folders pass", sm.powershell_problem({"helper": "win"}, "Import-Csv a.csv | Export-Csv b.csv; if ($?) { python x.py }") is None
          and sm.powershell_problem({"helper": "lin"}, heredoc) is None and sm.powershell_problem({}, heredoc) is None)
    check("and the prompt for such a folder says it is not bash", "no heredocs" in sm.helper_prompt({"helper": "win", "path": "/m"}) and "no heredocs" not in sm.helper_prompt({"helper": "lin", "path": "/m"}))
    told = sm.helper_prompt({"helper": "win", "path": "/home/x/Mails@PC"})
    check("it also says Studio, its tools and their services run on Studio's server, which commands on the computer cannot reach",
          f'Studio\'s server "{socket.gethostname()}"' in told and "cannot reach" in told and "/home/x/Mails@PC" in told and 'The `run` tool executes on "PC"' in told, told[:500])
    sm.HELPERS = saved_helpers
    sm.APP = saved_app


def test_updates(tmp):
    print("Updates for third-party tools (updates.py, with its fake catalogue)")
    sys.path.insert(0, ROOT)
    import updates
    state = {"busy": None}
    up = updates.Updates(os.path.join(tmp, "updates-data"), ROOT, {"busy": lambda: state["busy"], "test": True, "server_enabled": lambda n: False})
    up.check_all()
    pub = up.public()
    by = {i["id"]: i for i in pub["items"]}
    check("checking finds what is new, and lists system packages with the sudo command", by["fake-safe"]["available"] and not by["fake-manual"]["available"]
          and by["fake-apt"]["command"].startswith("sudo apt") and pub["available"] == 2 and pub["last_check"], pub)

    def finish():
        for _ in range(50):
            if up.job and up.job.get("done"):
                return up.job
            time.sleep(0.1)
        return up.job
    up.start("fake-safe")
    job = finish()
    by = {i["id"]: i for i in up.public()["items"]}
    check("an update runs in the background, is recorded, and can be rolled back", job["ok"] and by["fake-safe"]["current"] == "1.1" and by["fake-safe"]["can_rollback"]
          and up.public()["history"][0]["action"] == "update", job)
    up.start("fake-safe", "rollback")
    finish()
    check("rolling back puts the earlier version back", up.info("fake-safe")["fake_version"] == "1.0" and not up.public()["items"][0]["can_rollback"])
    try:
        up.start("fake-apt")
        refused = ""
    except updates.Fail as e:
        refused = str(e)
    check("system packages cannot be updated from Studio (sudo)", "sudo" in refused, refused)
    up.check_all()
    hour = time.localtime().tm_hour
    up.state["settings"]["window"] = [hour, (hour + 1) % 24]
    state["busy"] = "an agent run is working"
    up.tick()
    check("the nightly pass waits while Studio is busy", up.info("fake-safe")["fake_version"] == "1.0" and not up.info("fake-safe").get("auto_tried"))
    state["busy"] = None
    up.state["last_check"] = 0
    up.tick()
    finish()
    check("in its window and idle it checks, then updates the safe item by itself", up.info("fake-safe")["fake_version"] == "1.1" and up.public()["history"][0]["auto"]
          and up.info("fake-manual").get("fake_version") is None)
    up.start("fake-safe", "rollback")
    finish()
    up.check_all()
    up.tick()
    check("but only once a night", up.info("fake-safe")["fake_version"] == "1.0" and (up.job or {}).get("action") == "rollback")
    up.state["settings"]["window"] = [(hour + 2) % 24, (hour + 3) % 24]
    outside = not up.in_window()
    up.state["settings"]["window"] = [22, 5]
    at = lambda hhmm: time.mktime(time.strptime("2026-11-13 " + hhmm, "%Y-%m-%d %H:%M"))
    check("outside its window nothing happens, and a window may cross midnight", outside and up.in_window(at("23:30")) and up.in_window(at("04:10")) and not up.in_window(at("12:00")))
    again = updates.Updates(os.path.join(tmp, "updates-data"), ROOT, {"busy": lambda: None, "test": True})
    check("the state survives a restart", again.info("fake-safe").get("fake_version") == "1.0" and again.state["history"])
    real = {i["id"]: i for i in updates.catalog(ROOT)}
    check("the real catalogue: only Docker images that change no database, and npx tool servers, update by themselves",
          sorted(k for k, v in real.items() if v["auto_ok"]) == ["context7", "languagetool", "mcp-fetch", "mcp-searxng", "playwright-mcp", "postgres-mcp", "searxng"])
    # A local model checkout (PolyStrata) one commit behind its origin: checked, updated by its own setup.sh, rolled back.
    base = os.path.join(tmp, "localmodel")
    origin, clone = os.path.join(base, "origin"), os.path.join(base, "PolyStrata")
    git = lambda *a, cwd=None: subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()
    os.makedirs(origin)
    git("init", "-q", "-b", "main", cwd=origin)
    with open(os.path.join(origin, "setup.sh"), "w") as fh:
        fh.write('#!/bin/sh\necho "$@" >> calls.txt\n')
    os.chmod(os.path.join(origin, "setup.sh"), 0o755)
    for n in (1, 2):
        open(os.path.join(origin, "v.txt"), "w").write(str(n))
        git("add", "-A", cwd=origin)
        git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", f"v{n}", cwd=origin)
    git("clone", "-q", origin, clone)
    first = git("rev-parse", "HEAD~1", cwd=clone)
    git("checkout", "-q", first, cwd=clone)                    # as Tools & setup leaves it: the pinned commit, detached
    stopped = []
    lm = updates.Updates(os.path.join(tmp, "updates-local"), ROOT, {"busy": lambda: None, "server_enabled": lambda n: False,
                                                                   "config_get": {"polystrata_dir": clone}.get, "local_stop": lambda: stopped.append(1)})
    item = lm.items["polystrata"]
    seen = lm.check_localmodel(item)
    check("a PolyStrata checkout behind its origin has an update", seen["available"] and seen["detail"] == "1 new commit", seen)
    lm.job = {"log": []}
    message = lm.update_localmodel(item)
    calls = open(os.path.join(clone, "calls.txt")).read()
    check("updating stops its server, moves to the newest code and runs its own setup.sh --update",
          stopped and open(os.path.join(clone, "v.txt")).read() == "2" and "--update --yes --no-start" in calls and message.startswith("Updated to"), (calls, message))
    lm.rollback_localmodel(item, lm.info("polystrata")["prev"])
    check("rolling back returns to the earlier commit and its earlier engine",
          git("rev-parse", "HEAD", cwd=clone) == first and "--rollback-engine" in open(os.path.join(clone, "calls.txt")).read())
    lm.update_localmodel(item)
    newest = git("rev-parse", "HEAD", cwd=clone)
    with open(os.path.join(clone, "setup.sh"), "w") as fh:          # a setup that kept no earlier engine, as on 2026-11-28
        fh.write('#!/bin/sh\ncase "$*" in *--rollback-engine*) echo "no earlier engine is kept"; exit 1;; esac\n')
    try:
        lm.rollback_localmodel(item, lm.info("polystrata")["prev"])
        failed = ""
    except updates.Fail as e:
        failed = str(e)
    check("when the earlier engine cannot be put back, the code stays at the version its engine was built for",
          "stays as it was" in failed and git("rev-parse", "HEAD", cwd=clone) == newest, failed)
    git("-C", clone, "checkout", "-q", "setup.sh")
    os.makedirs(os.path.join(origin, "terminal", "serve"))
    open(os.path.join(origin, "terminal", "serve", "server.py"), "w").write("# moved\n")
    git("add", "-A", cwd=origin)
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "v3: the server moves to terminal/", cwd=origin)
    lm.check_localmodel(item)                                    # the page checks (fetches) before it updates
    lm.update_localmodel(item)
    moved = git("rev-parse", "HEAD", cwd=clone)
    try:
        lm.rollback_localmodel(item, lm.info("polystrata")["prev"])
        failed = ""
    except updates.Fail as e:
        failed = str(e)
    check("a rollback across the move to terminal/ is refused before anything changes",
          "terminal/" in failed and "nothing was changed" in failed and git("rev-parse", "HEAD", cwd=clone) == moved, failed)
    none = updates.Updates(os.path.join(tmp, "updates-none"), ROOT, {"busy": lambda: None, "config_get": {}.get})
    none.check("strata")
    check("a Strata that Studio did not install has nothing to update here, and is listed as not installed, not as a failed check",
          not none.check_localmodel(none.items["strata"])["available"] and none.info("strata").get("absent") and not none.info("strata").get("error")
          and next(i for i in none.public()["items"] if i["id"] == "strata")["absent"])


def test_installer(c):
    print("Tools & setup (installer.py): the machine check, the setup script and installing, with fake tools")
    sys.path.insert(0, ROOT)
    import installer
    status, d, _ = c.get("/api/admin/setup")
    by = {i["id"]: i for i in d.get("items", [])}
    check("the page lists the tools with their state and the machine", status == 200 and by["fake-ok"]["status"] == "ready" and by["fake-apt"]["status"] == "needs"
          and by["fake-later"]["status"] == "later" and d["machine"]["gpu"]["gfx"] == "gfx1100" and d["groups"], d)
    check("an engine larger than the card is listed as not fitting, saying why", by["fake-big"]["status"] == "no" and "48 GB" in by["fake-big"]["why"], by["fake-big"])
    check("an optional engine is marked so, and can be installed", by["fake-extra"]["extra"] and by["fake-extra"]["status"] == "ready" and by["fake-extra"]["engine"], by["fake-extra"])
    check("a tool that needs a program says which, and what the script installs", "no-such-program" in by["fake-apt"]["why"] and by["fake-apt"]["apt"] == ["no-such-program"], by["fake-apt"])
    check("installing refuses an unknown tool", c.post("/api/admin/setup/install", {"ids": ["nope"]})[0] == 409)
    status, r, _ = c.post("/api/admin/setup/install", {"ids": ["fake-ok", "fake-bad", "fake-apt"]})
    check("an install starts in the background", status == 200 and r["job"]["ids"] == ["fake-ok", "fake-bad", "fake-apt"], r)
    check("a second one waits for the first", c.post("/api/admin/setup/install", {"ids": ["fake-ok"]})[0] == 409)
    job = None
    for _ in range(150):
        job = c.get("/api/admin/setup")[1]["job"]
        if job["done"]:
            break
        time.sleep(0.2)
    check("one tool's failure does not stop the others, and a missing program skips its tool", job["done"] and job["done_ids"] == ["fake-ok"]
          and "fake-bad" in job["failed"] and "setup script" in job["skipped"].get("fake-apt", ""), job)
    servers = {s["name"]: s for s in c.get("/api/admin/mcp")[1]["servers"]}
    check("an installed tool's server is added to the tool servers, switched on and running", servers.get("fake-tool", {}).get("enabled")
          and servers["fake-tool"]["state"] == "ready", servers.get("fake-tool"))
    d = c.get("/api/admin/setup")[1]
    check("and the tool shows as installed, with a history line", {i["id"]: i for i in d["items"]}["fake-ok"]["status"] == "installed"
          and d["history"][0]["name"] and any(h["id"] == "fake-ok" and h["ok"] for h in d["history"]), d["history"])
    c.post("/api/admin/mcp", {"servers": [s for n, s in servers.items() if n != "fake-tool"]})
    check("the setup script route answers", c.post("/api/admin/setup/script", {"ids": ["fake-ok"]})[0] == 200)

    # The real catalogue against a made-up bare machine: what the sudo script would do. Its ~/.local/opt is empty, not this one's.
    saved_opt, installer.OPT = installer.OPT, tempfile.mkdtemp()
    inst = installer.Installer(tempfile.mkdtemp(), ROOT,
                               {"busy": lambda: None, "server_spec": lambda n: None, "server_ready": lambda n, t: True, "add_server": lambda s: None})
    inst.machine = {"system": "Bare", "arch": "x86_64", "programs": {}, "docker_ok": False, "in_docker_group": False, "venv_ok": False, "node_major": 0,
                    "rubberband": False, "gpus": [], "gpu": None, "linger": False, "apparmor_userns": False, "apt": True, "checked": time.time() + 3600,
                    "disk_free_gb": 100, "disk_path": "/x"}
    script = inst.sudo_script(["video", "searxng", "blender", "comfyui"])
    check("on a bare machine the script installs ffmpeg, Docker, Node, Xvfb, joins the docker group and turns on lingering",
          all(w in script for w in ("apt-get install -y", " ffmpeg", "docker.io", "nodejs", "xvfb", "python3-venv", 'usermod -aG docker "$ME"', "enable-linger")), script)
    st = inst.status(inst.items["comfyui"], inst.machine)
    check("a graphics engine without an AMD card is listed as not fitting", st["status"] == "no" and "AMD" in st["why"], st)
    check("and the script asks nothing for it", "render,video" not in inst.sudo_script(["comfyui"]))
    card = {"card": "card1", "vendor": "amd", "name": "", "vram_gb": 16.0, "gfx": "gfx1031", "rocm": True, "uid": "GPU-00000000000000ab"}
    inst.machine.update(gpus=[card], gpu=card, gpu_groups=False, disk_free_gb=500)
    st = inst.status(inst.items["comfyui"], inst.machine)
    script = inst.sudo_script(["comfyui", "voices"])
    check("with a 16 GB ROCm card, ComfyUI needs Python's venv, Git and the render and video groups, all in the script",
          st["status"] == "needs" and "render and video" in st["why"] and all(w in script for w in ("python3-venv", " git", 'usermod -aG render,video "$ME"')), (st, script))
    check("Qwen-Image does not fit a 16 GB card", inst.status(inst.items["qwen-image"], inst.machine)["status"] == "no")
    inst.machine["disk_free_gb"] = 20
    check("an engine larger than the free disk does not fit, saying so", "free on disk" in inst.status(inst.items["comfyui"], inst.machine)["why"])
    check("an engine gets its card by ROCm's id, with the override its family needs",
          installer.card_env(card) == ["ROCR_VISIBLE_DEVICES=GPU-00000000000000ab", "HSA_OVERRIDE_GFX_VERSION=10.3.0"]
          and installer.card_env({"gfx": "gfx1100", "uid": "GPU-1"}) == ["ROCR_VISIBLE_DEVICES=GPU-1"])
    alone, beside = inst.render_unit("comfyui", False), inst.render_unit("comfyui", True)
    voice = inst.render_unit("strata-voice", False)
    check("its services are written for this card and this Studio's folder", "ROCR_VISIBLE_DEVICES=GPU-00000000000000ab" in alone and "HIP_VISIBLE_DEVICES" not in alone
          and os.path.realpath(ROOT) + "/tools/voice_engine.py" in voice and "%h/Projects/Tools/StrataStudio" not in voice, voice)
    check("without a model server here ComfyUI may use the whole card and the engines unload nothing",
          "STRATA_COMFY_VRAM_GB=0" in alone and "STRATA_COMFY_VRAM_GB=8.5" in beside and "VOICE_MAKE_ROOM=0" in voice)
    locks = {name: open(inst.lock_file(name)).read().split() for name in ("comfyui.txt", "voice.txt", "sound.txt", "music.txt")}
    check("every engine's packages are pinned, PyTorch for ROCm 7.2 among ComfyUI's", all(all("==" in l for l in lines) for lines in locks.values())
          and "torch==2.14.1+rocm7.2" in locks["comfyui.txt"], {k: [l for l in v if "==" not in l] for k, v in locks.items()})
    files = [f for fs in installer.COMFY_FILES.values() for f in fs]
    check("every model file has its revision, size and sha256, and every service file is in Studio", all(len(f[1]) == 40 and f[3] > 0 and re.fullmatch(r"[0-9a-f]{64}", f[4]) for f in files)
          and all(os.path.exists(os.path.join(ROOT, s)) for s in installer.UNIT_SOURCES.values())
          and os.path.exists(os.path.join(ROOT, "sandbox", "mcp-comfyui", "strata_vram_cap.py")))
    src = os.path.join(tempfile.mkdtemp(), "model.bin")
    with open(src, "wb") as fh:
        fh.write(b"strata" * 1000)
    good = __import__("hashlib").sha256(b"strata" * 1000).hexdigest()
    dest = os.path.join(tempfile.mkdtemp(), "models", "model.bin")
    inst.fetch("file://" + src, dest, 6000, good)
    fetched = os.path.exists(dest) and not os.path.exists(dest + ".part")
    try:
        inst.fetch("file://" + src, dest + "2", 6000, "0" * 64)
        refused = False
    except installer.Fail as e:
        refused = "sha256" in str(e) and not os.path.exists(dest + "2") and not os.path.exists(dest + "2.part")
    check("a download is checked against its sha256: a match moves in, a mismatch is removed", fetched and refused)
    # Local models: which of PolyStrata's and Strata's models fit, and the install through their own setup.sh.
    saved_manager, installer.MANAGER = installer.MANAGER, "http://127.0.0.1:9"
    inst.machine.update(ram_gb=30, disk_free_gb=500)
    poly, strata = inst.items["polystrata"], inst.items["strata"]
    fit = {c["id"]: c for c in inst.choices(poly, inst.machine)}
    check("with 30 GB of RAM and a 16 GB card, Qwen3.6 fits and Ornith 122B does not, saying why; Strata's Swift fits nowhere",
          fit["qwen36-ud-q4_k_m"]["fits"] and not fit["ornith122-ud-iq3_xxs"]["fits"] and "RAM" in fit["ornith122-ud-iq3_xxs"]["why"]
          and inst.status(strata, inst.machine)["status"] == "no" and inst.status(poly, inst.machine)["status"] != "no", fit)
    inst.machine.update(ram_gb=64)
    home = os.path.join(installer.OPT, "PolyStrata")
    os.makedirs(os.path.join(home, ".git"))
    os.makedirs(os.path.join(home, "terminal", "serve"))
    open(os.path.join(home, "terminal", "serve", "server.py"), "w").write("")
    open(os.path.join(home, "terminal", "setup.py"), "w").write('ap.add_argument("--output", choices=("human", "jsonl"))\n')
    json.dump({"model_name": "qwen3.6-35b-a3b-ud-q4_k_m", "args": []}, open(os.path.join(home, "terminal", "strata-qwen36-ud-q4_k_m.json"), "w"))
    st = inst.status(poly, inst.machine)
    fit = {c["id"]: c for c in inst.choices(poly, inst.machine)}
    check("a PolyStrata with a model is installed, and its other model can still be added",
          st["status"] == "installed" and "qwen3.6" in st["why"] and fit["qwen36-ud-q4_k_m"]["installed"] and fit["ornith122-ud-iq3_xxs"]["fits"]
          and not fit["ornith122-ud-iq3_xxs"]["installed"], (st, fit))
    with open(os.path.join(home, "setup.sh"), "w") as fh:       # plays PolyStrata's setup: --output jsonl events, then the profile
        fh.write("#!/bin/sh\necho \"$@\" > args.txt\n"
                 "echo '{\"schema_version\": 1, \"event\": \"step\", \"n\": 5, \"title\": \"downloading Ornith\"}'\n"
                 "echo '{\"schema_version\": 1, \"event\": \"progress\", \"label\": \"Ornith.gguf\", \"done_bytes\": 10, \"total_bytes\": 20}'\n"
                 "echo 'plain words the setup prints'\n"
                 "echo '{\"model_name\": \"ornith-1.5-122b-ud-iq3_xxs\", \"args\": []}' > terminal/strata-ornith122-ud-iq3_xxs.json\n"
                 "echo '{\"schema_version\": 1, \"event\": \"done\", \"exit_code\": 0}'\n")
    os.chmod(os.path.join(home, "setup.sh"), 0o755)
    told = []
    inst.hooks.update(set_config=lambda k, v: told.append((k, v)), config_get=lambda k: None)
    inst.job = {"options": {"polystrata": "ornith122-ud-iq3_xxs"}, "log": [], "progress": None}
    inst.log = lambda line: inst.job["log"].append(line)
    inst.install_local(poly)
    args = open(os.path.join(home, "args.txt")).read()
    check("installing a model runs PolyStrata's own setup for it (no start, jsonl), shows its steps, and points Studio at it, the port first",
          "--family ornith122 --model UD-IQ3_XXS --context 131072 --port 8097 --no-start --no-browser --yes --setup --output jsonl" in args
          and any("downloading Ornith" in l for l in inst.job["log"]) and told == [("local_port", 8097), ("polystrata_dir", home)]
          and {c["id"]: c for c in inst.choices(poly, inst.machine)}["ornith122-ud-iq3_xxs"]["installed"], (args, inst.job["log"], told))
    try:
        inst.install_local(poly)
        again = True
    except installer.Fail as e:
        again = "all installed" in str(e)
    check("with both models installed there is nothing left to add", again)
    new_strata = os.path.join(installer.OPT, "Strata")          # Strata from 0.2.16 keeps its server and profiles in terminal/
    os.makedirs(os.path.join(new_strata, "terminal", "serve"))
    open(os.path.join(new_strata, "terminal", "serve", "server.py"), "w").write("")
    json.dump({"model_name": "swift-1.5-iq3_xxs", "args": []}, open(os.path.join(new_strata, "terminal", "strata-swift-iq3_xxs.json"), "w"))
    check("a Strata with the newer terminal/ layout is found with its model", inst.status(inst.items["strata"], inst.machine)["status"] == "installed"
          and {c["id"]: c for c in inst.choices(inst.items["strata"], inst.machine)}["swift-iq3_xxs"]["installed"])
    import studio as sm
    check("and Studio starts its server from terminal/; an older Strata from its top folder",
          sm.local_home(new_strata) == os.path.join(new_strata, "terminal") and sm.local_home(installer.OPT) == "")
    del inst.log
    installer.MANAGER = saved_manager
    installer.OPT = saved_opt
    check("the order puts what a tool needs before it", inst.order(["browser"]) == ["playwright", "browser"])
    real = installer.catalog(ROOT, "/data")
    check("every real tool server entry is accepted by Studio's own check", all(__import__("studio").clean_mcp_spec(s) for i in real for s in i.get("servers", [])))
    cards = installer.gpus()
    check("the graphics check runs on this machine", isinstance(cards, list) and all("vram_gb" in g for g in cards), cards)
    saved = os.environ.get("STRATA_MANAGER_URL")
    os.environ["STRATA_MANAGER_URL"] = ""
    try:
        spec = importlib.util.spec_from_file_location("comfyui_mcp_alone", os.path.join(ROOT, "tools", "comfyui_mcp.py"))
        alone = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(alone)
    finally:
        os.environ.pop("STRATA_MANAGER_URL") if saved is None else os.environ.__setitem__("STRATA_MANAGER_URL", saved)
    check("with no model server on the machine (STRATA_MANAGER_URL empty) the pictures tool has no Strata to unload",
          alone.strata_state() == (False, 0) and alone.no_room_beside() is False and alone.manager("/api/ps") == {})


def test_browser_tool():
    print("The agent's browser: the proxy's address check and waiting while the user holds the browser")
    import importlib.util, studio as sm
    spec = importlib.util.spec_from_file_location("browser_mcp", os.path.join(ROOT, "tools", "browser_mcp.py"))
    bm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bm)
    check("public addresses pass; this machine, the LAN, link-local and private ones do not", bm.public("93.184.215.14") and bm.public("2606:2800:21f:cb07::1")
          and not any(bm.public(ip) for ip in ("127.0.0.1", "192.168.1.20", "10.0.0.5", "172.17.0.1", "169.254.169.254", "::1", "fe80::1", "::ffff:127.0.0.1", "0.0.0.0", "224.0.0.1")))
    try:
        bm.resolve("localhost", 80)
        refused = ""
    except PermissionError as e:
        refused = str(e)
    check("a name that resolves to this machine is refused, naming the address", "not on the public internet" in refused, refused)
    view = sm.BrowserView.__new__(sm.BrowserView)
    view.lock, view.held = threading.Condition(), True
    saved, sm.APP = sm.APP, types.SimpleNamespace(browser=view)
    events = []
    run = types.SimpleNamespace(stop=False, emit=lambda kind, **kw: events.append((kind, kw)))
    done = threading.Event()
    threading.Thread(target=lambda: (sm.wait_for_browser(run), done.set()), daemon=True).start()
    time.sleep(0.6)
    waited = not done.is_set()
    with view.lock:
        view.held = False
        view.lock.notify_all()
    done.wait(2)
    sm.APP = saved
    check("while the user holds the browser the agent's call waits, saying so, and goes on when it is handed back", waited and done.is_set()
          and events and "Hand back" in events[0][1]["text"], events)
    proc = types.SimpleNamespace(poll=lambda: None)
    shown = types.SimpleNamespace(proc=proc, meta={"url": "https://github.com/knadh/listmonk/releases", "title": "Releases · knadh/listmonk"})
    users = {"admin": {"role": "admin"}, "maria": {"role": "user"}}
    fake = lambda spec, view: types.SimpleNamespace(browser=view, auth=types.SimpleNamespace(users=users), mcp=types.SimpleNamespace(spec=lambda name: spec))
    saved, sm.APP = sm.APP, fake({"enabled": True, "users": False}, shown)
    note = sm.browser_context("admin", "What is the newest release on this page?")
    greek = sm.browser_context("admin", "Τι γράφει αυτή η σελίδα;") + sm.browser_context("admin", "About the page in the browser: summarise it")
    unrelated = [sm.browser_context("admin", m) for m in ("Γράψε τη σελίδα index.html για γονείς", "Write the page about dyslexia", "Fix the tests")]
    hidden_from_user = sm.browser_context("maria", "this page")
    sm.APP = fake({"enabled": True, "users": False}, types.SimpleNamespace(proc=proc, meta={"url": "about:blank", "title": ""}))
    blank = sm.browser_context("admin", "this page")
    sm.APP = fake(None, shown)
    no_server = sm.browser_context("admin", "this page")
    sm.APP = saved
    check("with a page open, the model is told which one, to read it when the user means it", "«Releases · knadh/listmonk» at https://github.com/knadh/listmonk/releases" in note
          and "browser_snapshot" in note and "not part of this project" in note and not note.startswith(sm.LOOP_NOTES), note)
    check("also in Greek and from Ask about this page", greek.count("knadh/listmonk/releases") == 2, greek)
    check("but a message that does not point at it does not hear about it, so another project's page cannot steer the work", unrelated == ["", "", ""], unrelated)
    check("but not for someone the browser is not shared with, for an empty tab, or without the server", hidden_from_user == "" and blank == "" and no_server == "")
    saved, sm.APP = sm.APP, types.SimpleNamespace(mcp=types.SimpleNamespace(servers=[{"name": "listmonk", "description": "listmonk"}]))
    prompt = sm.build_mcp_prompt(types.SimpleNamespace(ws={"path": "/p"}, mcp_tools={"mcp__listmonk__overview": {"server": "listmonk", "client": types.SimpleNamespace(instructions="")}}))
    sm.APP = saved
    check("the tool servers' prompt says not to hunt for a service's passwords or settings when its tools fall short, but to ask", "passwords, tokens or settings files" in prompt and "ask the user" in prompt, prompt[-300:])


def test_listmonk_mcp(tmp):
    print("listmonk MCP server (tools/listmonk_mcp.py, against a fake listmonk)")
    port = free_port()
    mock = subprocess.Popen([sys.executable, os.path.join(ROOT, "tests", "mock_listmonk.py"), str(port)])
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/mock/sent", timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        folder = os.path.join(tmp, "mailing")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "parents.csv"), "w", encoding="utf-8") as f:
            f.write("email;name;attributes\nmaria@example.com;Μαρία Π.;{\"city\": \"Αθήνα\"}\nnikos@example.com;Νίκος;{}\n")

        def run(half, calls, user="tester", token="secret", env=None):
            msgs = [{"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}}, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}]
            msgs += [{"jsonrpc": "2.0", "id": i + 2, "method": "tools/call", "params": {"name": n, "arguments": a}} for i, (n, a) in enumerate(calls)]
            out = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "listmonk_mcp.py")], input="\n".join(map(json.dumps, msgs)) + "\n",
                                 capture_output=True, text=True, timeout=180,
                                 env={**os.environ, "LISTMONK_URL": f"http://127.0.0.1:{port}", "LISTMONK_USER": user, "LISTMONK_TOKEN": token,
                                      "LISTMONK_TOOLS": half, "LISTMONK_ROOTS": tmp, **(env or {})})
            answers = {a["id"]: a for a in map(json.loads, out.stdout.splitlines())}
            return [t["name"] for t in answers[1]["result"]["tools"]], [(answers[i + 2]["result"]["content"][0]["text"], answers[i + 2]["result"]["isError"]) for i in range(len(calls))]

        sent = lambda: json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/mock/sent"))["data"]
        names, r = run("manage", [("overview", {}), ("create_list", {"name": "Δάσκαλοι", "tags": ["school"]}),
                                  ("import_contacts", {"path": os.path.join(folder, "parents.csv"), "lists": ["Γονείς"]}),
                                  ("import_contacts", {"rows": [{"email": "eleni@example.com", "name": "Ελένη"}], "lists": ["δάσκαλοι"]}),
                                  ("add_contact", {"email": "Me@Example.com", "name": "Me"}),
                                  ("find_contacts", {"search": "μαρία"}), ("change_lists", {"emails": ["nikos@example.com"], "action": "unsubscribe", "lists": ["Γονείς"]}),
                                  ("update_contact", {"email": "maria@example.com", "attribs": {"child": "Γιώργος"}}),
                                  ("create_campaign", {"name": "Νοέμβριος", "subject": "Νέα του μήνα", "lists": ["Γονείς"], "body": "<p>Γεια σας {{ .Subscriber.Name }}</p>"}),
                                  ("preview_campaign", {"campaign": "νοέμβριος", "save_to": os.path.join(folder, "preview.html")}),
                                  ("get_campaign", {"campaign": "1"}),
                                  ("import_contacts", {"path": "/etc/passwd", "lists": ["Γονείς"]}),
                                  ("create_list", {"name": "γονείς"}), ("add_contact", {"email": "maria@example.com"}),
                                  ("create_campaign", {"name": "x", "subject": "y", "lists": ["Nobody"], "body": "z"})])
        check("the manage half looks and prepares, and cannot send", "start_campaign" not in names and "send_email" not in names and "create_campaign" in names, names)
        check("nothing of a front end remains: no pacer, continuation or list-group tools", not {"sending_capacity", "check_already_sent", "set_list_group"} & set(names)
              and "Mail" + "Sender" not in open(os.path.join(ROOT, "tools", "listmonk_mcp.py"), encoding="utf-8").read(), names)
        check("overview names the lists", not r[0][1] and "Γονείς" in r[0][0] and "Lists:" in r[0][0], r[0][0])
        check("a list is made, with a tag of its own", not r[1][1] and "Δάσκαλοι" in r[1][0], r[1][0])
        check("a CSV in the project is imported into a list by name", not r[2][1] and "2 of 2" in r[2][0], r[2][0])
        check("so are rows given directly", not r[3][1] and "1 of 1" in r[3][0], r[3][0])
        check("one contact is added", not r[4][1] and "me@example.com" in r[4][0], r[4][0])
        check("contacts are found by name, with their lists and attributes", "Μαρία" in r[5][0] and "Αθήνα" in r[5][0] and "Γονείς" in r[5][0], r[5][0])
        check("contacts can be unsubscribed from a list", not r[6][1] and "Unsubscribed 1" in r[6][0], r[6][0])
        check("attributes are merged, not replaced", not r[7][1], r[7][0])
        check("a campaign is made as a draft, saying how many it reaches", not r[8][1] and "draft" in r[8][0] and "about 1 contacts" in r[8][0] and "Nothing was sent" in r[8][0], r[8][0])
        check("the preview shows its text and saves the HTML into the project", not r[9][1] and "Γεια σας" in r[9][0] and os.path.isfile(os.path.join(folder, "preview.html")), r[9][0])
        check("get_campaign shows its body and reach", "reach about 1 contacts" in r[10][0] and "Γεια σας" in r[10][0], r[10][0])
        check("files outside the allowed folders are refused", r[11][1] and "outside" in r[11][0], r[11][0])
        check("a second list of the same name is refused", r[12][1], r[12][0])
        check("an existing contact is not added twice", r[13][1] and "already" in r[13][0], r[13][0])
        check("an unknown list is refused, naming the real ones", r[14][1] and "Γονείς" in r[14][0], r[14][0])
        check("nothing reached anyone while preparing", sent() == [], sent())

        names, r = run("send", [("send_test", {"campaign": "1", "emails": ["stranger@example.com"]}),
                                ("send_test", {"campaign": "1", "emails": ["me@example.com"]}),
                                ("start_campaign", {"campaign": "1", "recipients": 500}),
                                ("start_campaign", {"campaign": "1", "recipients": 1}),
                                ("pause_campaign", {"campaign": "1"}),
                                ("send_email", {"to": "maria@example.com", "subject": "Ραντεβού", "message": "<p>Σας περιμένουμε την Τρίτη.</p>"}),
                                ("send_email", {"to": "nobody@example.com", "subject": "x", "message": "y"})])
        check("the send half has only the tools that reach people or delete", names == ["send_test", "start_campaign", "schedule_campaign", "pause_campaign", "cancel_campaign", "delete_contacts", "send_email"], names)
        check("a test goes only to existing contacts", r[0][1] and "not contacts" in r[0][0] and not r[1][1], r[0][0])
        check("a campaign starts only when the expected number of recipients is right", r[2][1] and "reaches 1 contacts" in r[2][0] and not r[3][1], r[2][0])
        check("it can be paused", not r[4][1], r[4][0])
        check("one email goes to one contact through the transactional template", not r[5][1] and r[6][1] and "not a contact" in r[6][0], r[5][0])
        log = sent()
        check("and exactly that reached listmonk", [e["kind"] for e in log] == ["test", "status", "status", "tx"] and log[0]["to"] == ["me@example.com"]
              and log[1]["status"] == "running" and log[3]["template_id"] == 2 and log[3]["data"]["subject"] == "Ραντεβού", log)
        _, r = run("manage", [("create_list", {"name": "Sports", "tags": ["club", "kids"]}), ("overview", {})])
        check("a list can carry listmonk list tags, shown in the overview", not r[0][1] and "tags club, kids" in r[1][0], r[1][0])
        names, r = run("manage", [("import_contacts", {"rows": [{"email": "maria@example.com", "name": "Μαρία Π."}], "lists": ["Γονείς"], "overwrite": "True", "confirmed": "false"})])
        check("yes and no given as text count (\"True\" meant overwrite)", not r[0][1] and "existing contacts updated" in r[0][0] and "import_status" in names and "stop_import" in names, r[0][0])
        def lm(method, path, body=None):
            req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode() if body is not None else None, method=method,
                                         headers={"Authorization": "token tester:secret", "Content-Type": "application/json"})
            return json.load(urllib.request.urlopen(req))["data"]
        # Contact tags (a name under a group, in attribs.tags) and keeping unsubscribes when a contact is saved.
        names, r = run("manage", [("list_tags", {}),
                                  ("update_contact", {"email": "nikos@example.com", "name": "Νίκος Κ."}),
                                  ("find_contacts", {"search": "nikos"}),
                                  ("tag_contacts", {"tag": "Θεσσαλονίκη/Καλαμαριά", "emails": ["maria@example.com", "nikos@example.com", "ghost@example.com"]}),
                                  ("tag_contacts", {"region": "Εύοσμος", "city": "Θεσσαλονίκη", "lists": ["Γονείς"]}),
                                  ("tag_contacts", {"tag": "Καλαμαριά", "emails": ["maria@example.com"]}),
                                  ("list_tags", {}),
                                  ("find_contacts", {"tag": "Καλαμαριά"}),
                                  ("tag_contacts", {"tag": "Καλαμαριά (Θεσσαλονίκη)", "action": "remove", "emails": ["nikos@example.com"]}),
                                  ("fill_tag_list", {"tag": "Καλαμαριά"}),
                                  ("find_contacts", {"search": "nikos"}),
                                  ("tag_contacts", {"tag": "Πάτρα/Κέντρο", "action": "remove", "emails": ["maria@example.com"]}),
                                  ("tag_contacts", {"tag": "Καλαμαριά"}),
                                  ("fill_tag_list", {"tag": "Καλαμαριά"})])
        check("the manage half has the tag tools", {"list_tags", "tag_contacts", "fill_tag_list"} <= set(names), names)
        check("with no tags yet, list_tags says how they are added", not r[0][1] and "No contact has a tag yet" in r[0][0], r[0][0])
        check("update_contact keeps a list the contact unsubscribed from (listmonk drops memberships left out)",
              not r[1][1] and "Γονείς (unsubscribed)" in r[2][0], r[2][0])
        check("a tag is added by email, naming who is not a contact", not r[3][1] and "to 2 contacts" in r[3][0] and "ghost@example.com" in r[3][0]
              and "Καλαμαριά (Θεσσαλονίκη)" in r[3][0], r[3][0])
        check("a tag is added to everyone in a list, from region and city", not r[4][1] and "Εύοσμος (Θεσσαλονίκη)" in r[4][0], r[4][0])
        check("a bare region means the one tag that has it, and someone who has it already is not changed", not r[5][1] and "to 0 contacts" in r[5][0]
              and "1 already had it" in r[5][0], r[5][0])
        check("list_tags groups the regions under their city, with counts", "Θεσσαλονίκη:" in r[6][0] and "- Καλαμαριά: 2 contacts" in r[6][0]
              and "- Εύοσμος:" in r[6][0], r[6][0])
        check("find_contacts finds contacts by tag", r[7][0].startswith("2 contacts match"), r[7][0])
        check("a tag is removed, written as Region (City)", not r[8][1] and "from 1 contact" in r[8][0], r[8][0])
        check("fill_tag_list fills a list of its own with exactly the tagged contacts", not r[9][1] and "'Καλαμαριά (Θεσσαλονίκη)'" in r[9][0]
              and "holds the 1 contacts" in r[9][0] and "contact-tag" in next(l["tags"] for l in lm("GET", "/api/lists")["results"] if l["name"] == "Καλαμαριά (Θεσσαλονίκη)"), r[9][0])
        check("changing tags leaves lists and subscriptions alone (still unsubscribed)", "Γονείς (unsubscribed)" in r[10][0], r[10][0])
        check("removing a tag nobody has is refused, naming the tags there are", r[11][1] and "Καλαμαριά (Θεσσαλονίκη)" in r[11][0], r[11][0])
        check("tag_contacts needs to be told who", r[12][1] and "say who" in r[12][0], r[12][0])
        check("filling again keeps the same list", not r[13][1] and "holds the 1 contacts" in r[13][0], r[13][0])
        # Campaigns go to lists only, with no hidden settings; the body carries listmonk's own tracking tags.
        _, r = run("manage", [("create_campaign", {"name": "Εύοσμος", "subject": "Νέα", "lists": ["Γονείς"], "tags": ["νέα"],
                                                   "body": '<p>Γεια <a href="https://example.com">x</a></p>'}),
                              ("create_campaign", {"name": "κενό", "subject": "x", "lists": ["Γονείς"], "body": " "}),
                              ("create_campaign", {"name": "χωρίς", "subject": "x", "body": "y"})])
        cid = int(re.search(r"#(\d+)", r[0][0]).group(1))
        camp = lm("GET", f"/api/campaigns/{cid}")
        check("a campaign goes to lists, with only the tags the caller gave", not r[0][1] and [l["id"] for l in camp["lists"]] == [1] and camp["tags"] == ["νέα"], camp)
        check("a campaign with no body, or no lists, is refused", r[1][1] and "no body" in r[1][0] and r[2][1] and "which lists" in r[2][0], [r[1][0], r[2][0]])
        lm("PUT", f"/api/campaigns/{cid}", {**camp, "lists": [1], "media": [7]})
        _, r = run("manage", [("update_campaign", {"campaign": str(cid), "lists": ["Δάσκαλοι"], "body": '<p>Νέο</p>\n{{ TrackView }}'}),
                              ("get_campaign", {"campaign": str(cid)}), ("list_campaigns", {})])
        camp = lm("GET", f"/api/campaigns/{cid}")
        check("update_campaign changes what is given and keeps tags and attachments", not r[0][1] and camp["tags"] == ["νέα"]
              and [x["id"] for x in camp["media"]] == [7] and [l["name"] for l in camp["lists"]] == ["Δάσκαλοι"] and camp["subject"] == "Νέα", camp)
        check("get_campaign and list_campaigns say when the body has {{ TrackView }}", "Open tracking: on" in r[1][0] and f"#{cid} " in r[2][0]
              and "· open tracking" in r[2][0], [r[1][0], r[2][0]])
        names, r = run("send", [("start_campaign", {"campaign": str(cid), "recipients": 999})])
        n = int(re.search(r"reaches (\d+) contacts", r[0][0]).group(1))
        check("starting needs the right number of recipients, which the refusal gives", r[0][1] and lm("GET", f"/api/campaigns/{cid}")["status"] == "draft", r[0][0])
        _, r = run("send", [("start_campaign", {"campaign": str(cid), "recipients": n})])
        check("and then it starts through listmonk's own status route", not r[0][1] and f"about {n} contacts" in r[0][0]
              and lm("GET", f"/api/campaigns/{cid}")["status"] == "running" and sent()[-1] == {"kind": "status", "campaign": cid, "status": "running"}, [r[0][0], sent()[-1]])
        _, r = run("send", [("pause_campaign", {"campaign": str(cid)}), ("cancel_campaign", {"campaign": str(cid)}), ("start_campaign", {"campaign": str(cid), "recipients": n})])
        check("pausing and cancelling use the same route, and a cancelled campaign cannot start", not r[0][1] and not r[1][1] and r[2][1] and "cancelled" in r[2][0]
              and [e["status"] for e in sent()[-2:]] == ["paused", "cancelled"], [x[0] for x in r])
        _, r = run("manage", [("create_campaign", {"name": "Διάλεξη", "subject": "Πρόσκληση", "lists": ["Γονείς"], "body": "<p>x</p>"})])
        second = int(re.search(r"#(\d+)", r[0][0]).group(1))
        _, r = run("send", [("schedule_campaign", {"campaign": str(second), "send_at": "tomorrow", "recipients": 1}),
                            ("schedule_campaign", {"campaign": str(second), "send_at": "2026-12-01T09:00:00+02:00", "recipients": 1})])
        check("scheduling needs a real date, and goes through listmonk's status route with it", r[0][1] and "send_at" in r[0][0] and not r[1][1]
              and lm("GET", f"/api/campaigns/{second}")["status"] == "scheduled" and lm("GET", f"/api/campaigns/{second}")["send_at"] == "2026-12-01T09:00:00+02:00", r)
        lm("PUT", f"/api/campaigns/{second}", {**lm("GET", f"/api/campaigns/{second}"), "lists": [1], "media": [7], "tags": ["νέα"], "body": "<p>x</p>\n{{ TrackView }}"})
        _, r = run("manage", [("copy_campaign", {"campaign": str(second)}), ("copy_campaign", {"campaign": str(second), "lists": ["Δάσκαλοι"]})])
        check("copy_campaign needs the new lists", r[0][1] and "which lists the copy goes to" in r[0][0], r[0][0])
        copy = lm("GET", f"/api/campaigns/{int(re.search(r'#(\d+)', r[1][0]).group(1))}") if not r[1][1] else {}
        check("a copy keeps subject, text, tags and attachments, goes to the new lists, and is a draft", not r[1][1] and copy["subject"] == "Πρόσκληση"
              and [x["id"] for x in copy["media"]] == [7] and copy["tags"] == ["νέα"] and [l["name"] for l in copy["lists"]] == ["Δάσκαλοι"]
              and copy["status"] == "draft" and not copy.get("send_at") and "{{ TrackView }}" in copy["body"] and "file7.pdf" in r[1][0], [r[1][0], copy])
        bad_files = {"attribs.csv": 'email,name,attribs\nm@example.com,Μ,"{""city"": ""Αθήνα""}"\n',
                     "columns.csv": "email,name,city,mobile\nm@example.com,Μ,Αθήνα,690\n",
                     "values.csv": 'email,name,attributes\nnot-an-email,Χ,\nok@example.com,Ο,"city: Αθήνα"\nshort@example.com\n'}
        for fname, body in bad_files.items():
            with open(os.path.join(folder, fname), "w", encoding="utf-8") as fh:
                fh.write(body)
        _, r = run("manage", [("import_contacts", {"path": os.path.join(folder, f), "lists": ["Γονείς"]}) for f in bad_files])
        check("a file with an attribs column is refused before upload, naming attributes", r[0][1] and "not attribs" in r[0][0] and "Nothing was imported" in r[0][0], r[0][0])
        check("so is one column per field, saying to put them in attributes as JSON", r[1][1] and "'city', 'mobile'" in r[1][0] and "JSON object" in r[1][0], r[1][0])
        check("bad emails, attributes that are not JSON and short rows are listed by line", r[2][1] and "line 2: 'not-an-email'" in r[2][0]
              and "line 3: attributes must be a JSON object" in r[2][0] and "line 4: 1 cells" in r[2][0], r[2][0])
        post = lambda path: urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=b"{}"))
        post("/mock/stuck?mode=soft")
        fast = {"LISTMONK_STALL_S": "0", "LISTMONK_IMPORT_WAIT_S": "2", "LISTMONK_STOP_WAIT_S": "2"}
        _, r = run("manage", [("import_status", {}), ("import_contacts", {"rows": [{"email": "a1@example.com"}], "lists": ["Γονείς"]}),
                              ("stop_import", {}), ("import_contacts", {"rows": [{"email": "a1@example.com"}], "lists": ["Γονείς"]})], env=fast)
        check("an import with no progress is called stuck, not slow, with what to do", "stuck, not slow" in r[0][0] and "stop_import" in r[0][0], r[0][0])
        check("a new import is refused while it hangs, saying to stop it rather than wait", r[1][1] and "stuck, not slow" in r[1][0] and "stop_import" in r[1][0], r[1][0])
        check("stop_import stops it, and the next import runs", not r[2][1] and "Stopped" in r[2][0] and not r[3][1] and "1 of 1" in r[3][0], [r[2][0], r[3][0]])
        _, r = run("manage", [("import_status", {})], env={"LISTMONK_STALL_S": "600"})
        check("a finished import is reported as not running", "No import is running" in r[0][0], r[0][0])
        post("/mock/stuck?mode=hard")
        _, r = run("manage", [("stop_import", {})], env=fast)
        check("when listmonk cannot stop it, the model is told to restart listmonk or ask, not to wait", "could not stop it" in r[0][0] and "restart_listmonk" in r[0][0] and "Do not keep waiting" in r[0][0], r[0][0])
        restart = f"{sys.executable} -c \"import urllib.request as u; u.urlopen(u.Request('http://127.0.0.1:{port}/mock/restart', data=b'1'))\""
        names, r = run("send", [("restart_listmonk", {}), ("restart_listmonk", {"reason": "the Thessaloniki import is stuck"})], env={"LISTMONK_RESTART": restart})
        check("with LISTMONK_RESTART the send half can restart listmonk, given a reason", names[-1] == "restart_listmonk" and r[0][1] and "reason" in r[0][0]
              and not r[1][1] and "Restarted listmonk" in r[1][0] and "import it again" in r[1][0] and sent()[-1] == {"kind": "restart"}, r)
        run("manage", [("change_lists", {"emails": ["eleni@example.com"], "action": "add", "lists": ["Γονείς"]})])
        backups = os.path.join(tmp, "listmonk-backups")
        dump = f"{sys.executable} -c \"print('-- dump ' + 'x' * 2000)\""
        names, r = run("send", [("delete_contacts", {"lists": ["Γονείς"], "keep": ["Γονείς"], "count": 1}),
                                ("delete_contacts", {"lists": ["Γονείς"], "keep": ["Δάσκαλοι"], "count": 99})],
                       env={"LISTMONK_BACKUP": dump, "LISTMONK_BACKUP_DIR": backups})
        real = re.search(r"would delete (\d+) contacts", r[1][0])
        check("delete_contacts is in the send half; a list cannot be deleted and kept, and a wrong count deletes nothing but gives the real one",
              "delete_contacts" in names and r[0][1] and "both" in r[0][0] and r[1][1] and real and "keeping 1" in r[1][0]
              and not any(e["kind"] == "delete" for e in sent()), [r[0][0], r[1][0]])
        _, r = run("send", [("delete_contacts", {"lists": ["Γονείς"], "keep": ["Δάσκαλοι"], "count": int(real.group(1)) if real else 0})],
                   env={"LISTMONK_BACKUP": dump, "LISTMONK_BACKUP_DIR": backups})
        _, after = run("manage", [("find_contacts", {"search": "eleni"}), ("find_contacts", {"list": ["Γονείς"]})])
        check("with the right count it saves the database, deletes them and keeps the ones in a kept list", not r[0][1] and f"Deleted {real.group(1) if real else '?'} contacts" in r[0][0]
              and "kept 1" in r[0][0] and os.listdir(backups) and "Ελένη" in after[0][0] and "1 contacts match" in after[1][0], [r[0][0], after[1][0]])
        _, r = run("send", [("delete_contacts", {"lists": ["Δάσκαλοι"], "count": 1})], env={"LISTMONK_BACKUP": f"{sys.executable} -c \"import sys; sys.exit(3)\"", "LISTMONK_BACKUP_DIR": backups})
        check("if the database cannot be saved first, nothing is deleted", r[0][1] and "nothing was deleted" in r[0][0], r[0][0])
        r = run("manage", [("overview", {})], token="wrong")[1]
        check("a wrong token is reported clearly", r[0][1] and "refused the API user" in r[0][0], r[0][0])
        r = run("manage", [("overview", {})], user="")[1]
        check("without an API user it explains how to make one", r[0][1] and "API user" in r[0][0], r[0][0])
    finally:
        mock.terminate()


def test_languagetool_mcp():
    print("Greek checker MCP server (tools/languagetool_mcp.py, against a fake LanguageTool)")
    import http.server, urllib.parse as up

    class FakeLT(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            form = up.parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
            text, lang = form["text"][0], form["language"][0]
            spelled = {"el-GR": ["Διόρθωσεις", "tous", "server", "epveβαιώθη"], "en-US": ["tous"]}[lang]
            matches = [{"offset": text.index(w), "length": len(w), "message": "spelling", "rule": {"id": "MORFOLOGIK_RULE"},
                        "replacements": [{"value": "Διορθώσεις"}] if w == "Διόρθωσεις" else []} for w in spelled if w in text]
            if lang == "el-GR" and "δεν σώζει" in text:
                matches.append({"offset": text.index("δεν"), "length": 3, "message": "δε πριν από σ", "rule": {"id": "GREEK_PARTICLE_EXTRA_N_1"}, "replacements": [{"value": "δε"}]})
            body = json.dumps({"matches": matches}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body)
    lt = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeLT)
    threading.Thread(target=lt.serve_forever, daemon=True).start()
    calls = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, {"jsonrpc": "2.0", "method": "notifications/initialized"},
             {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
             {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "check_text", "arguments": {"text": "Διόρθωσεις.\nΑφαιρεί tous, ο server epveβαιώθη. Αυτό δεν σώζει."}}},
             {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "check_text", "arguments": {"text": "ok", "include_latin_words": True}}},
             {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "check_text", "arguments": {"text": " "}}},
             {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "nope", "arguments": {}}}]
    try:
        out = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "languagetool_mcp.py")], input="\n".join(json.dumps(c) for c in calls) + "\n",
                             capture_output=True, text=True, timeout=30, env={**os.environ, "LANGUAGETOOL_URL": f"http://127.0.0.1:{lt.server_port}"}).stdout
    finally:
        lt.shutdown()
    replies = {m["id"]: m for m in map(json.loads, out.splitlines())}
    check("it answers the handshake and lists one tool", replies[1]["result"]["serverInfo"]["name"] == "languagetool-local" and [t["name"] for t in replies[2]["result"]["tools"]] == ["check_text"])
    text = replies[3]["result"]["content"][0]["text"]
    check("it reports a misspelling with its line and suggestion", "line 1, column 1: «Διόρθωσεις» (spelling)" in text and "Suggestions: Διορθώσεις" in text, text)
    check("a word mixing Greek and Latin letters is named as such", "«epveβαιώθη» (mixed scripts)" in text, text)
    check("a Latin word that is not English is reported, an English term is not", "«tous» (latin word, not English)" in text and "server" not in text, text)
    check("grammar notes come through as such", "«δεν» (grammar or style)" in text, text)
    check("empty text is an error", replies[5]["result"]["isError"] is True)
    check("an unknown tool is refused", "error" in replies[6])


def test_greek_grammar(c, st_port, tmp):
    print("Greek grammar and glossary (tools/greek_grammar.py, on a lexicon built from tests/greek/mini-kaikki.jsonl)")
    import studio as sm
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import greek_lexicon, greek_grammar as gg
    lex = os.path.join(tmp, "greek-lexicon.sqlite")
    rows = greek_lexicon.build(os.path.join(ROOT, "tests", "greek", "mini-kaikki.jsonl"), lex)
    check("the lexicon builds from Wiktionary's entries", rows > 1000 and os.path.exists(lex), rows)
    gg.LEX = gg.Lexicon(lex)
    print("repairs of words mixing Greek and Latin letters (repair_mixed), on the same lexicon")
    english = sm.ENGLISH_DICT["words"]
    sm.ENGLISH_DICT["words"] = {"server", "check"}
    try:
        for mixed, meant, why in [("θάλaσσα", "θάλασσα", "a Latin letter that looks like a Greek one"),
                                  ("thάλασσα", "θάλασσα", "Greeklish inside the word"),
                                  ("θαlασσα", "θάλασσα", "Greeklish without the accent"),
                                  ("Βιbλίο", "Βιβλίο", "a capital kept"),
                                  ("υπάρχειnoch", "υπάρχει", "a stray word of another language dropped"),
                                  ("τοserver", "το server", "an English word stuck to a Greek one gets a space"),
                                  ("ΣεCheck", "Σε Check", "also before it")]:
            check(f"«{mixed}» becomes «{meant}»: {why}", sm.repaired_word(mixed) == meant, sm.repaired_word(mixed))
        for mixed, why in [("Σcaffolding", "nothing in the lexicon"), ("τοnoch", "a two-letter word with a stray one: too unsure"),
                           ("γreek", "not a Greek word either way")]:
            check(f"«{mixed}» is left for the send-back: {why}", sm.repaired_word(mixed) is None, sm.repaired_word(mixed))
        text = "Η θάλaσσα είναι εδώ, το `θάλaσσα` στον κώδικα μένει, και υπάρχειnoch ένα Σcaffolding."
        check("a reply is repaired outside code, and what cannot be repaired stays", sm.repair_mixed(text)
              == "Η θάλασσα είναι εδώ, το `θάλaσσα` στον κώδικα μένει, και υπάρχει ένα Σcaffolding.", sm.repair_mixed(text))
        sm.ENGLISH_DICT["words"] = set()
        check("without an English word list, a Latin part is neither dropped nor spaced", sm.repaired_word("υπάρχειnoch") is None
              and sm.repaired_word("τοserver") is None)
    finally:
        sm.ENGLISH_DICT["words"] = english
    sents = [l.rstrip("\n").split("\t") for l in open(os.path.join(ROOT, "tests", "greek", "sentences.tsv"), encoding="utf-8") if l.strip() and not l.startswith("#")]
    grammar = [r for r in sents if r[0] == "error" and r[1] != "spelling"]
    caught = [r[2] for r in grammar if gg.check(r[2])]
    flagged = [(r[2], gg.check(r[2])) for r in sents if r[0] == "ok" and gg.check(r[2])]
    check("it catches every grammar mistake in the test set", len(caught) == len(grammar), [r[2] for r in grammar if r[2] not in caught])
    check("and flags none of the correct sentences", not flagged, flagged[:3])
    def first(text):
        f = gg.check(text)
        return f[0] if f else {}
    f = first("Οι ναυτικοί βλέπει το φως.")
    check("subject and verb: the plural is suggested", f.get("kind") == "subject-verb" and f["suggestions"] == ["βλέπουν"], f)
    f = first("Η Ηλέκτρα και ο παππούς της μένει στον φάρο.")
    check("two subjects joined by «και» want a plural", f.get("kind") == "subject-verb" and "και" in f["message"] and f["suggestions"] == ["μένουν"], f)
    f = first("Η κανόνας είναι απλός.")
    check("article and noun: the article that fits is suggested", f.get("kind") == "agreement" and f["word"] == "Η κανόνας" and "Ο … κανόνας" in f["suggestions"], f)
    f = first("Χθες θα πήγα στο νησί.")
    check("θα with the simple past gets the dependent form", f.get("kind") == "tense" and f["suggestions"] == ["θα πάω"], f)
    f = first("Το φως του φάρου είναι δυνατή.")
    check("an adjective after είναι agrees with the subject before it, past «του φάρου»", f.get("kind") == "agreement" and f["word"] == "δυνατή" and f["suggestions"] == ["δυνατό"], f)
    kinds = [x["kind"] for x in gg.check("Που είναι ο φάρος?")]
    check("Που opening a question and «?» are both named", kinds == ["question", "punctuation"], kinds)
    check("a weak pronoun before its verb is not taken for an article", not gg.check("Τον είδα χθες. Της έδωσε το βιβλίο. Τους λέω την ιστορία."))
    check("a confusable is a Check, not an error", [(x["kind"], x["severity"]) for x in gg.check("Ένας ψιλός φάρος.")] == [("confusable", "check")])
    gl = {"terms": [{"use": "παιδί με δυσλεξία", "avoid": ["δυσλεξικό παιδί"], "why": "Πρώτα το παιδί."}], "accept": ["Ηλέκτρα"]}
    hits = [x for x in gg.check("Τα δυσλεξικά παιδιά διαβάζουν.", gl) if x["kind"] == "glossary"]
    check("a phrase to avoid matches its other forms", len(hits) == 1 and hits[0]["word"] == "δυσλεξικά παιδιά" and hits[0]["suggestions"] == ["παιδί με δυσλεξία"] and "Πρώτα το παιδί" in hits[0]["message"], hits)
    check("a word that is right covers its forms", gg.accepted("Ηλέκτρας", gl) and not gg.accepted("Ηλεκτρισμός", gl))
    gg.LEX = gg.Lexicon(os.path.join(tmp, "no-such-lexicon.sqlite"))
    check("without the lexicon the question and glossary rules still run", [x["kind"] for x in gg.check("Που πας? Ένα δυσλεξικό παιδί.", gl)] == ["question", "punctuation", "glossary"])

    # check_text: grouped by severity, with the glossary's words left out of spelling
    import http.server, urllib.parse as up

    class FakeLT(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            form = up.parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
            text = form["text"][0]
            m = []
            if "\n" in text and " " not in text:      # other endings tried for an unknown word: only λογοθεραπευτής is known
                at = 0
                for line in text.split("\n"):
                    if line != "λογοθεραπευτής":
                        m.append({"offset": at, "length": len(line), "message": "x", "rule": {"id": "MORFOLOGIK_RULE_EL_GR", "issueType": "misspelling", "category": {"id": "TYPOS"}}, "replacements": []})
                    at += len(line) + 1
                text = ""
            for w, rule, issue, cat in (("λογοθεραπευτή", "MORFOLOGIK_RULE_EL_GR", "misspelling", "TYPOS"), ("Ηλέκτρας", "MORFOLOGIK_RULE_EL_GR", "misspelling", "TYPOS"), ("Καλημερα", "MORFOLOGIK_RULE_EL_GR", "misspelling", "TYPOS"),
                                        ("κι", "GREEK_STYLE_AND", "style", "STYLE"), ("δεν", "GREEK_PARTICLE_EXTRA_N_1", "grammar", "ORTHOGRAPHY")):
                if w in text:
                    m.append({"offset": text.index(w), "length": len(w), "message": rule, "rule": {"id": rule, "issueType": issue, "category": {"id": cat}}, "replacements": []})
            body = json.dumps({"matches": m}).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    lt = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeLT)
    threading.Thread(target=lt.serve_forever, daemon=True).start()
    gpath = os.path.join(tmp, "glossary.json")
    json.dump(gl, open(gpath, "w"))
    call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "check_text", "arguments": {"text": "Καλημερα. Οι ναυτικοί βλέπει τον φάρο της Ηλέκτρας κι εγώ δεν σβήνω. Ένα δυσλεξικό παιδί. Ρωτήστε τον λογοθεραπευτή."}}}
    try:
        out = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "languagetool_mcp.py")], input=json.dumps(call) + "\n", capture_output=True, text=True, timeout=30,
                             env={**os.environ, "LANGUAGETOOL_URL": f"http://127.0.0.1:{lt.server_port}", "GREEK_LEXICON": lex, "GREEK_GLOSSARY": gpath}).stdout
    finally:
        lt.shutdown()
    text = json.loads(out)["result"]["content"][0]["text"]
    fix, rest = text.split("Check these")[0], text.split("Check these")[-1]
    check("check_text groups mistakes first, then what may be right, then style", "Fix these:" in text and text.index("Fix these") < text.index("Check these") < text.index("Notes"), text)
    check("spelling, agreement and glossary are mistakes", "«Καλημερα» (spelling)" in fix and "«βλέπει» (subject-verb)" in fix and "«δυσλεξικό παιδί» (glossary)" in fix and "βλέπουν" in fix, text)
    check("the final ν is a Check and «κι» a Note", "«δεν»" in rest and "«κι»" in text.split("Notes")[-1], text)
    check("a name in the glossary is not a misspelling", "Ηλέκτρας" not in text, text)
    check("an unknown form of a word LanguageTool knows is a Check, not a mistake", "«λογοθεραπευτή» (spelling) LanguageTool does not know this form, but it knows «λογοθεραπευτής»" in rest
          and "λογοθεραπευτή" not in fix, text)
    check("the summary counts each group", "3 error, 2 check, 1 note" in text.splitlines()[0], text.splitlines()[0])

    # Studio's routes
    s, d, _ = c.get("/api/greek/glossary")
    check("the glossary starts with the seed terms, and an admin may edit it", s == 200 and d["editable"] and any(t["use"] == "έχει δυσλεξία" for t in d["terms"]) and "Ηλέκτρα" in d["accept"], d)
    check("a term needs a phrase to avoid", c.post("/api/greek/glossary", {"terms": [{"use": "x", "avoid": []}], "accept": []})[0] == 400)
    check("a word that is right is one word", c.post("/api/greek/glossary", {"terms": [], "accept": ["two words"]})[0] == 400)
    s, d, _ = c.post("/api/greek/glossary", {"terms": [{"use": " παιδί με δυσλεξία ", "avoid": ["δυσλεξικό παιδί", " "], "why": "Πρώτα το παιδί."}], "accept": ["Ηλέκτρα", "Ηλέκτρα", "Φρενέλ"]})
    check("a saved glossary is tidied and kept", s == 200 and d["terms"] == [{"use": "παιδί με δυσλεξία", "avoid": ["δυσλεξικό παιδί"], "why": "Πρώτα το παιδί."}] and d["accept"] == ["Ηλέκτρα", "Φρενέλ"], d)
    stored_gl = json.load(open(os.path.join(tmp, "app", "data", "greek", "glossary.json")))
    check("it is the file the tool reads", stored_gl["accept"] == ["Ηλέκτρα", "Φρενέλ"])
    saved_app, sm.APP = getattr(sm, "APP", None), types.SimpleNamespace(data_dir=os.path.join(tmp, "app", "data"))
    try:
        check("the tool server is told where it is", sm.greek_check_env(["python3", "/x/tools/languagetool_mcp.py"]) == {"GREEK_GLOSSARY": os.path.join(tmp, "app", "data", "greek", "glossary.json")}
              and sm.greek_check_env(["python3", "other.py"]) == {})
    finally:
        sm.APP = saved_app
    token = c.post("/api/admin/invites", {})[1]["token"]
    member = Client(st_port)
    for _ in range(2):                    # earlier sign-in tests may have locked this address for a minute
        signed = member.post("/api/auth/signup", {"token": token, "username": "greekreader", "password": "member password"})
        if signed[0] != 429:
            break
        time.sleep(61)
    s, d, _ = member.get("/api/greek/glossary")
    d = d if s == 200 else {"signup": signed[:2], "get": d}
    check("a user reads the glossary but may not change it", s == 200 and not d["editable"] and member.post("/api/greek/glossary", {"terms": [], "accept": []})[0] == 403, (s, d))
    s, d, _ = c.post("/api/greek/check", {"text": "Η κανόνας."})
    check("the Try it box runs the same check", s == 200 and isinstance(d["result"], str) and d["result"], d)
    check("an empty text is refused", c.post("/api/greek/check", {"text": " "})[0] == 400)


def test_comfyui_mcp():
    print("ComfyUI MCP server (tools/comfyui_mcp.py, against a fake ComfyUI)")
    import http.server, urllib.parse as up
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/iZk9HQAAAABJRU5ErkJggg==")
    seen = {"graphs": [], "wan": False, "uploads": [], "zimage": False, "qwen": False, "strata": True, "busy": 0, "ollama": [], "stopped": 0, "full_at_prompt": [], "swap_file": None}

    class FakeComfy(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def reply(self, body, ctype="application/json"):
            body = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(200); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = up.urlparse(self.path)
            if path.path == "/object_info/CheckpointLoaderSimple":
                self.reply({"CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [["sd_xl_base_1.0.safetensors"]]}}}})
            elif path.path == "/object_info/DualCLIPLoader":
                self.reply({"DualCLIPLoader": {"input": {"required": {"clip_name1": [["qwen_0.6b_ace15.safetensors", "qwen_1.7b_ace15.safetensors", "qwen_4b_ace15.safetensors"] if seen.get("music") else []]}}}})
            elif path.path.startswith("/object_info/") and seen.get("music") and path.path.rsplit("/", 1)[1] in ("UNETLoader", "VAELoader"):
                node = path.path.rsplit("/", 1)[1]
                files = ["acestep_v1.5_turbo.safetensors", "acestep_v1.5_xl_turbo_bf16.safetensors"] if node == "UNETLoader" else ["ace_1.5_vae.safetensors"]
                self.reply({node: {"input": {"required": {"unet_name" if node == "UNETLoader" else "vae_name": [files]}}}})
            elif path.path.startswith("/object_info/"):
                node = path.path.rsplit("/", 1)[1]
                field, zfile, qfile, wfile = {"UNETLoader": ("unet_name", "z_image_turbo_int8_convrot.safetensors", "qwen_image_2512_fp8_e4m3fn.safetensors", "wan2.2_ti2v_5B_fp16.safetensors"),
                                              "CLIPLoader": ("clip_name", "qwen_3_4b_fp8_mixed.safetensors", "qwen_2.5_vl_7b_fp8_scaled.safetensors", "umt5_xxl_fp8_e4m3fn_scaled.safetensors"),
                                              "VAELoader": ("vae_name", "ae.safetensors", "qwen_image_vae.safetensors", "wan2.2_vae.safetensors")}[node]
                self.reply({node: {"input": {"required": {field: [[zfile] * seen["zimage"] + [qfile] * seen["qwen"] + [wfile] * seen["wan"]]}}}})
            elif path.path == "/api/strata/status":         # the Strata gateway, for swap mode
                self.reply({"running": seen["strata"], "server_processes": [{"pid": 1}] if seen["strata"] else [], "lifecycle": {"active_requests": seen["busy"]},
                            "subagent_pool": {"active": {"pool_mib": seen.get("pool", 9216)}}})
            elif path.path == "/api/ps":
                self.reply({"models": seen["ollama"]})
            elif path.path.startswith("/history/"):
                job = path.path.rsplit("/", 1)[1]
                if job == "slow":
                    self.reply({})
                elif job == "music1":
                    self.reply({job: {"status": {"status_str": "success", "completed": True},
                                      "outputs": {"107": {"audio": [{"filename": "studio_00001_.mp3", "subfolder": "audio", "type": "output"}]}}}})
                elif job == "video1":
                    self.reply({job: {"status": {"status_str": "success", "completed": True},
                                      "outputs": {"58": {"images": [{"filename": "studio_00001_.mp4", "subfolder": "video", "type": "output"}], "animated": [True]}}}})
                elif job == "broken":
                    self.reply({job: {"status": {"status_str": "error", "messages": [["execution_error", {"exception_message": "out of memory"}]]}, "outputs": {}}})
                else:
                    self.reply({job: {"status": {"status_str": "success", "completed": True},
                                      "outputs": {"7": {"images": [{"filename": "studio_00001_.png", "subfolder": "", "type": "output"}]}}}})
            elif path.path == "/view":
                self.reply(png, "image/png")
            elif path.path == "/queue":
                self.reply({"queue_running": [["x"]], "queue_pending": []})
            elif path.path == "/system_stats":
                held = 20 * 2 ** 30 if seen.get("freed", 0) < seen.get("stubborn", 0) else 0      # ignores the first /free calls
                self.reply({"devices": [{"name": "cuda:0 AMD Radeon RX 7800 XT", "vram_total": 24 * 2 ** 30, "vram_free": 9 * 2 ** 30,
                                         "torch_vram_total": held}]})

        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            if self.path == "/upload/image":
                seen["uploads"].append(self.headers.get("Content-Type", ""))
                return self.reply({"name": "studio-start.png", "subfolder": "", "type": "input"})
            body = json.loads(raw)
            if self.path == "/free":
                seen["freed"] = seen.get("freed", 0) + 1
                return self.reply({})
            if self.path == "/api/strata/stop":
                seen["stopped"] += 1
                seen["strata"] = False
                return self.reply({"ok": True})
            if self.path in ("/queue", "/interrupt"):
                return self.reply({})

            if seen["swap_file"]:
                seen["full_at_prompt"].append(json.load(open(seen["swap_file"])).get("full"))
            graph = body["prompt"]
            seen["graphs"].append(graph)
            if "58" in graph:
                return self.reply({"prompt_id": "video1"})
            if "107" in graph:
                return self.reply({"prompt_id": "music1"})
            self.reply({"prompt_id": "slow" if "slowly" in graph["2"]["inputs"]["text"] else "job1"})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeComfy)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    call = lambda i, name, args: {"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": name, "arguments": args}}
    calls = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, {"jsonrpc": "2.0", "method": "notifications/initialized"},
             {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
             call(3, "generate_image", {"prompt": "a red cube", "negative_prompt": "blurry", "width": 1152, "height": 896, "seed": 7}),
             call(4, "generate_image", {"prompt": "a cube", "width": 1000}),
             call(5, "generate_image", {"prompt": "a cube", "checkpoint": "nope.safetensors"}),
             call(6, "generate_image", {"prompt": "draw it slowly"}),
             call(7, "get_image", {"job": "broken"}),
             call(8, "list_models", {}), call(9, "nope", {})]
    try:
        out = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "comfyui_mcp.py")], input="\n".join(json.dumps(c) for c in calls) + "\n",
                             capture_output=True, text=True, timeout=60,
                             env={**os.environ, "COMFYUI_URL": f"http://127.0.0.1:{server.server_port}", "COMFYUI_WAIT_S": "1", "COMFYUI_OUTPUT_DIR": "/out"}).stdout
    finally:
        server.shutdown()
    replies = {m["id"]: m for m in map(json.loads, out.splitlines())}
    check("it answers the handshake and lists its six tools", replies[1]["result"]["serverInfo"]["name"] == "comfyui-local"
          and [t["name"] for t in replies[2]["result"]["tools"]] == ["generate_image", "generate_images", "generate_video", "generate_music", "get_image", "list_models"])
    made = replies[3]["result"]["content"]
    check("a picture comes back as image content, with where it was saved and how it was made",
          made[0]["type"] == "image" and base64.b64decode(made[0]["data"]) == png and "Saved as /out/studio_00001_.png" in made[1]["text"]
          and "1152x896" in made[2]["text"] and "seed 7" in made[2]["text"], made[1:])
    graph = seen["graphs"][0]
    check("the workflow carries the prompt, the negative prompt, the size and the seed",
          graph["2"]["inputs"]["text"] == "a red cube" and graph["3"]["inputs"]["text"] == "blurry" and graph["4"]["inputs"]["width"] == 1152
          and graph["5"]["inputs"]["seed"] == 7 and graph["1"]["inputs"]["ckpt_name"] == "sd_xl_base_1.0.safetensors")
    check("a size that is not a multiple of 64 is refused before ComfyUI sees it", replies[4]["result"]["isError"] and "multiples of 64" in replies[4]["result"]["content"][0]["text"])
    check("an unknown model is refused, naming the ones there are", replies[5]["result"]["isError"] and "sd_xl_base_1.0" in replies[5]["result"]["content"][0]["text"])
    check("a picture that takes longer comes back as a job id for get_image", "job slow" in replies[6]["result"]["content"][0]["text"] and "get_image" in replies[6]["result"]["content"][0]["text"])
    check("ComfyUI's error is passed on", replies[7]["result"]["isError"] and "out of memory" in replies[7]["result"]["content"][0]["text"])
    check("list_models names the models and the free graphics memory", "sd_xl_base_1.0.safetensors" in replies[8]["result"]["content"][0]["text"] and "9216 of 24576 MiB free" in replies[8]["result"]["content"][0]["text"])
    check("an unknown tool is refused", "error" in replies[9])
    check("the model is unloaded after a finished picture, to give the RAM back", seen.get("freed") == 1, seen.get("freed"))
    check("without Z-Image's files, sdxl is the default", seen["graphs"][0]["1"]["class_type"] == "CheckpointLoaderSimple" and replies[3]["result"]["content"][2]["text"].startswith("sdxl (z-image-turbo is not installed),"))
    seen.update({"zimage": True, "graphs": []})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeComfy)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    calls = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
             call(2, "generate_image", {"prompt": "a red cube", "negative_prompt": "blurry", "seed": 3}),
             call(3, "generate_image", {"prompt": "a red cube", "model": "sdxl", "seed": 3}),
             call(4, "generate_image", {"prompt": "a red cube", "model": "dalle"}), call(5, "list_models", {})]
    try:
        out = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "comfyui_mcp.py")], input="\n".join(json.dumps(c) for c in calls) + "\n",
                             capture_output=True, text=True, timeout=60,
                             env={**os.environ, "COMFYUI_URL": f"http://127.0.0.1:{server.server_port}", "COMFYUI_WAIT_S": "1"}).stdout
    finally:
        server.shutdown()
    replies = {m["id"]: m for m in map(json.loads, out.splitlines())}
    z = seen["graphs"][0]
    check("with Z-Image installed it is the default, as in ComfyUI's own template", z["1"]["class_type"] == "UNETLoader" and z["8"]["inputs"]["type"] == "lumina2"
          and z["3"]["class_type"] == "ConditioningZeroOut" and z["10"]["inputs"]["shift"] == 3 and z["4"]["class_type"] == "EmptySD3LatentImage"
          and (z["5"]["inputs"]["steps"], z["5"]["inputs"]["cfg"], z["5"]["inputs"]["sampler_name"], z["5"]["inputs"]["scheduler"]) == (8, 1.0, "res_multistep", "simple"), z)
    text = replies[2]["result"]["content"][-1]["text"]
    check("and the reply says the negative prompt was ignored", text.startswith("z-image-turbo (the default), 1024x1024, 8 steps") and "negative_prompt ignored" in text, text)
    check("sdxl can still be chosen", seen["graphs"][1]["1"]["class_type"] == "CheckpointLoaderSimple" and seen["graphs"][1]["5"]["inputs"]["steps"] == 25)
    check("an unknown model is refused", replies[4]["result"]["isError"] and "model must be one of" in replies[4]["result"]["content"][0]["text"])
    check("list_models names both, and the user's default", "z-image-turbo: installed" in replies[5]["result"]["content"][0]["text"]
          and "The user's default: z-image-turbo" in replies[5]["result"]["content"][0]["text"] and "sd_xl_base_1.0" in replies[5]["result"]["content"][0]["text"])

    # The default the user picks in Studio, read on every call.
    settings = os.path.join(tempfile.mkdtemp(), "comfyui.json")

    def run_with(default, *pictures):
        json.dump({"default_model": default}, open(settings, "w"))
        seen["graphs"].clear()
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeComfy)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        calls = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}] + [call(2 + i, "generate_image", args) for i, args in enumerate(pictures)]
        try:
            out = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "comfyui_mcp.py")], input="\n".join(json.dumps(c) for c in calls) + "\n",
                                 capture_output=True, text=True, timeout=60, env={**os.environ, "COMFYUI_URL": f"http://127.0.0.1:{server.server_port}",
                                                                                  "COMFYUI_WAIT_S": "1", "COMFYUI_SETTINGS": settings}).stdout
        finally:
            server.shutdown()
        replies = {m["id"]: m for m in map(json.loads, out.splitlines())}
        return [replies[2 + i]["result"]["content"][-1]["text"].split(",")[0] for i in range(len(pictures))]
    check("with SDXL as the user's default, a picture with no model uses it; an asked-for model still wins",
          run_with("sdxl", {"prompt": "a cat"}, {"prompt": "a cat", "model": "z-image-turbo"}) == ["sdxl (the default)", "z-image-turbo (asked for)"])
    check("choose by style: SDXL for painted and drawn styles, Z-Image for photos",
          run_with("auto", {"prompt": "a soft watercolour illustration of a fox"}, {"prompt": "a photograph of a fox"}, {"prompt": "a children's book drawing"})
          == ["sdxl (chosen by style)", "z-image-turbo (chosen by style)", "sdxl (chosen by style)"])
    json.dump({"default_model": "nonsense"}, open(settings, "w"))
    check("an unreadable setting falls back to Z-Image", run_with("nonsense", {"prompt": "a cat"}) == ["z-image-turbo (the default)"])
    check("a default that is not installed falls back to Z-Image Turbo", run_with("z-image-base", {"prompt": "a cat"}) == ["z-image-turbo (z-image-base is not installed)"])

    music_out, music_project = tempfile.mkdtemp(), tempfile.mkdtemp()
    os.makedirs(os.path.join(music_out, "audio"))
    with open(os.path.join(music_out, "audio", "studio_00001_.mp3"), "wb") as fh:
        fh.write(b"ID3fake-mp3")
    # Swap mode: qwen-image gets the whole card, with Strata unloaded through the gateway first.
    swap_file = os.path.join(os.path.dirname(settings), "swap.json")
    seen.update({"qwen": True, "swap_file": swap_file})

    def swap_run(*calls_args, **state):
        seen.update({"graphs": [], "full_at_prompt": [], "stopped": 0, "strata": True, "busy": 0, "ollama": [], "pool": 9216, **state})
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeComfy)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        calls = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}] + [call(2 + i, name, args) for i, (name, args) in enumerate(calls_args)]
        url = f"http://127.0.0.1:{server.server_port}"
        try:
            out = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "comfyui_mcp.py")], input="\n".join(json.dumps(c) for c in calls) + "\n",
                                 capture_output=True, text=True, timeout=60, env={**os.environ, "COMFYUI_URL": url, "STRATA_MANAGER_URL": url, "COMFYUI_WAIT_S": "1",
                                                                                  "COMFYUI_SETTINGS": settings, "COMFYUI_SWAP_FILE": swap_file,
                                                                                  "COMFYUI_BUSY_WAIT_S": "1", "COMFYUI_OUTPUT_DIR": music_out,
                                                                                  "COMFYUI_FILE_ROOTS": os.environ.get("COMFYUI_FILE_ROOTS") or music_out + ":" + music_project}).stdout
        finally:
            server.shutdown()
        return [m["result"] for m in map(json.loads, out.splitlines()) if m["id"] > 1]
    json.dump({"default_model": "z-image-turbo"}, open(settings, "w"))
    loaded_strata = [{"name": "quartz:strata", "runtime": "strata", "size_vram": 15 * 2 ** 30, "details": {"format": "strata"}}]
    batch = swap_run(("generate_images", {"model": "qwen-image", "pictures": [{"prompt": "a lighthouse"}, {"prompt": "a harbour", "width": 1664, "height": 928}]}),
                     ollama=loaded_strata)[0]
    texts = [c["text"] for c in batch["content"] if c["type"] == "text"]
    q = seen["graphs"][0]
    check("qwen-image unloads Strata once for a whole batch, and has the whole card only while it renders",
          seen["stopped"] == 1 and seen["full_at_prompt"] == [True, True] and json.load(open(swap_file))["full"] is False, (seen["stopped"], seen["full_at_prompt"]))
    check("with ComfyUI's own Qwen-Image settings", q["1"]["inputs"]["unet_name"] == "qwen_image_2512_fp8_e4m3fn.safetensors" and q["8"]["inputs"]["type"] == "qwen_image"
          and q["10"]["inputs"]["shift"] == 3.1 and q["3"]["class_type"] == "CLIPTextEncode" and (q["5"]["inputs"]["steps"], q["5"]["inputs"]["cfg"]) == (20, 4.0)
          and q["4"]["inputs"]["width"] == 1328 and seen["graphs"][1]["4"]["inputs"]["width"] == 1664, q)
    check("the reply has each picture, and says Strata loads again with the next request",
          sum(c["type"] == "image" for c in batch["content"]) == 2 and texts[1].startswith("Picture 1 of 2: qwen-image (asked for), 1328x1328")
          and "loads again with the next chat request" in texts[-1], texts)
    seen["freed"] = 0
    swap_run(("generate_image", {"model": "qwen-image", "prompt": "a lighthouse"}), ollama=loaded_strata, stubborn=2)
    check("after a swap, a free that ComfyUI did not act on is sent again before Strata may load (it kept 20 GB on 2026-11-13)",
          seen["freed"] == 2 and json.load(open(swap_file))["full"] is False, seen["freed"])
    seen["stubborn"] = 0
    busy = swap_run(("generate_image", {"model": "qwen-image", "prompt": "a lighthouse"}), busy=1)[0]
    check("while Strata answers another client, it is not unloaded", busy["isError"] and "another client's request" in busy["content"][0]["text"]
          and seen["stopped"] == 0 and seen["graphs"] == [], busy)
    held = swap_run(("generate_image", {"model": "qwen-image", "prompt": "a lighthouse"}), ollama=[{"name": "qwen2.5-coder:3b", "size_vram": 2 ** 30}])[0]
    check("Ollama models on the card: no swap, and Strata is left alone", held["isError"] and "Ollama models hold graphics memory" in held["content"][0]["text"]
          and seen["stopped"] == 0 and seen["graphs"] == [], held)
    small = swap_run(("generate_images", {"pictures": [{"prompt": "a cat"}, {"prompt": "a dog"}]}))[0]
    check("a batch with a model that fits beside Strata does not unload it", seen["stopped"] == 0 and sum(c["type"] == "image" for c in small["content"]) == 2
          and seen["full_at_prompt"] == [False, False], seen["full_at_prompt"])
    first = swap_run(("generate_images", {"pictures": [{"prompt": "a cat"}, {"prompt": "a dog"}]}), pool=0)[0]
    check("with Strata started without room for tools (Strata first), even a small model unloads it once and has the whole card",
          seen["stopped"] == 1 and seen["full_at_prompt"] == [True, True] and sum(c["type"] == "image" for c in first["content"]) == 2
          and "make room on the graphics card" in [c["text"] for c in first["content"] if c["type"] == "text"][-1], (seen["stopped"], seen["full_at_prompt"]))
    # Music: ACE-Step 1.5 through ComfyUI (generate_music).
    seen["music"] = True
    song = swap_run(("generate_music", {"style": "cheerful children's song, ukulele", "lyrics": "[Verse]\nΤο νησί των λέξεων", "seconds": 30,
                                        "path": os.path.join(music_project, "audio", "theme.mp3")}))[0]
    g = seen["graphs"][-1]
    texts = [c["text"] for c in song["content"] if c["type"] == "text"]
    check("generate_music builds ACE-Step 1.5's workflow, Greek lyrics sung in el, beside Strata", not song.get("isError")
          and g["104"]["inputs"]["unet_name"] == "acestep_v1.5_turbo.safetensors" and g["105"]["inputs"]["clip_name2"] == "qwen_1.7b_ace15.safetensors"
          and g["94"]["inputs"]["language"] == "el" and g["94"]["inputs"]["duration"] == 30 and g["98"]["inputs"]["seconds"] == 30
          and g["107"]["class_type"] == "SaveAudioMP3" and seen["stopped"] == 0, (texts, g.get("94")))
    check("and copies the MP3 into the project", open(os.path.join(music_project, "audio", "theme.mp3"), "rb").read() == b"ID3fake-mp3"
          and any("Copied into the project" in t for t in texts), texts)
    instrumental = swap_run(("generate_music", {"style": "calm piano", "quality": "best"}))[0]
    g = seen["graphs"][-1]
    check("without lyrics it is an instrumental; best quality is XL with the whole card", g["94"]["inputs"]["lyrics"] == "[instrumental]"
          and g["104"]["inputs"]["unet_name"] == "acestep_v1.5_xl_turbo_bf16.safetensors" and seen["stopped"] == 1, seen["stopped"])
    outside = swap_run(("generate_music", {"style": "calm piano", "path": "/etc/theme.mp3"}))[0]
    check("a path outside the project is refused", outside["isError"] and "inside" in outside["content"][0]["text"], outside)
    # HeartMuLa, Studio's second music engine (tools/music_engine.py in fake mode), chosen in Studio or by the call.
    heart_port = free_port()
    heart = subprocess.Popen([sys.executable, os.path.join(ROOT, "tools", "music_engine.py")], env={**os.environ, "MUSIC_FAKE": "1", "MUSIC_PORT": str(heart_port),
                             "MUSIC_OUT_ROOTS": music_out + ":" + music_project, "MUSIC_MAKE_ROOM": "0"}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    old_url = os.environ.get("MUSIC_URL")
    os.environ["MUSIC_URL"] = f"http://127.0.0.1:{heart_port}"
    try:
        wait_port(heart_port, heart)
        json.dump({"default_model": "z-image-turbo", "music_model": "heartmula"}, open(settings, "w"))
        target = os.path.join(music_project, "audio", "heart.mp3")
        hm = swap_run(("generate_music", {"style": "acoustic folk, guitar,  warm female vocal", "lyrics": "[Verse]\nΤο νησί", "seconds": 6, "path": target}),
                      ("generate_music", {"style": "calm piano", "model": "ace-step"}),
                      ("generate_music", {"style": "calm piano", "seconds": 300}),
                      ("generate_music", {"style": "calm piano", "model": "musicgen"}),
                      ("list_models", {}))
        texts = [c["text"] for c in hm[0]["content"]]
        check("with HeartMuLa as the user's default, a song goes to the music engine, not ComfyUI, and lands in the project",
              not hm[0].get("isError") and os.path.getsize(target) > 1000 and seen["stopped"] == 0
              and any("heartmula (HeartMuLa-oss-3B, the user's default)" in t and "tags acoustic folk,guitar,warm female vocal" in t for t in texts), texts)
        check("asking for ace-step still uses ComfyUI, and says so", seen["graphs"][-1]["104"]["inputs"]["unet_name"] == "acestep_v1.5_turbo.safetensors"
              and "ace-step (ACE-Step 1.5) made it: asked for." in [c["text"] for c in hm[1]["content"] if c["type"] == "text"][-1])
        check("HeartMuLa's 4-minute limit and unknown models are refused", hm[2]["isError"] and "between 5 and 240" in hm[2]["content"][0]["text"]
              and hm[3]["isError"] and "model must be one of ace-step, heartmula" in hm[3]["content"][0]["text"], (hm[2], hm[3]))
        check("list_models reports HeartMuLa and the music default", "heartmula (generate_music model heartmula, HeartMuLa-oss-3B): installed" in hm[4]["content"][0]["text"]
              and "The user's music default: heartmula" in hm[4]["content"][0]["text"], hm[4]["content"][0]["text"])
        import urllib.request as ur
        ask = lambda path, body: json.load(ur.urlopen(ur.Request(f"http://127.0.0.1:{heart_port}{path}", json.dumps(body).encode(), {"Content-Type": "application/json"})))
        try:
            ask("/make", {"style": "x", "out": "/etc/x.mp3"})
            refused = False
        except urllib.error.HTTPError as exc:
            refused = exc.code == 400
        check("the music engine writes only inside its folders", refused)
        os.environ["MUSIC_URL"] = f"http://127.0.0.1:{free_port()}"
        down = swap_run(("generate_music", {"style": "calm piano", "model": "heartmula"}))[0]
        check("with the engine down, the reply names its service", down["isError"] and "strata-music" in down["content"][0]["text"], down)
    finally:
        heart.terminate()
        if old_url is None:
            os.environ.pop("MUSIC_URL", None)
        else:
            os.environ["MUSIC_URL"] = old_url
        json.dump({"default_model": "z-image-turbo"}, open(settings, "w"))
    seen["music"] = False
    check("a batch of more than 8 is refused", swap_run(("generate_images", {"pictures": [{"prompt": "x"}] * 9}))[0]["isError"])

    # Video: Wan 2.2 TI2V-5B, always with the whole card.
    seen["wan"] = True
    os.makedirs(os.path.join(music_out, "video"), exist_ok=True)
    open(os.path.join(music_out, "video", "studio_00001_.mp4"), "wb").write(b"fake clip")
    start_png = os.path.join(os.path.dirname(settings), "start.png")
    open(start_png, "wb").write(png)
    os.environ["COMFYUI_FILE_ROOTS"] = os.path.dirname(settings)
    try:
        clip = swap_run(("generate_video", {"prompt": "slow dolly through a reading room", "seconds": 5}),
                        ("generate_video", {"prompt": "the fox looks up", "start_image": start_png, "size": "704x1280", "seconds": 2}),
                        ("generate_video", {"prompt": "x", "start_image": "/etc/hostname"}),
                        ("generate_video", {"prompt": "beam over the sea", "path": os.path.join(os.path.dirname(settings), "video", "beam.mp4")}),
                        ("generate_video", {"prompt": "x", "path": "/etc/beam.mp4"}))
    finally:
        os.environ.pop("COMFYUI_FILE_ROOTS", None)
    copied = os.path.join(os.path.dirname(settings), "video", "beam.mp4")
    check("with path, the clip is copied into the project", not clip[3].get("isError") and os.path.isfile(copied) and open(copied, "rb").read() == b"fake clip"
          and any(c.get("text") == f"Copied into the project as {copied}." for c in clip[3]["content"]), clip[3]["content"][:2])
    check("and a path outside the project is refused", clip[4]["isError"] and "path must be a full path ending in .mp4" in clip[4]["content"][0]["text"], clip[4])
    v1, v2 = seen["graphs"][0], seen["graphs"][1]
    check("a clip uses ComfyUI's own Wan 2.2 workflow, 5 s as 121 frames, with the whole card", v1["55"]["class_type"] == "Wan22ImageToVideoLatent"
          and v1["55"]["inputs"]["length"] == 121 and v1["38"]["inputs"]["type"] == "wan" and v1["48"]["inputs"]["shift"] == 8
          and (v1["3"]["inputs"]["steps"], v1["3"]["inputs"]["cfg"], v1["3"]["inputs"]["sampler_name"]) == (20, 5.0, "uni_pc")
          and v1["8"]["class_type"] == "VAEDecodeTiled" and v1["8"]["inputs"]["temporal_size"] == 32
          and seen["full_at_prompt"][:2] == [True, True], v1["55"])
    check("and the reply names the MP4", "Video saved as" in clip[0]["content"][0]["text"] and clip[0]["content"][0]["text"].endswith("Frames from it follow."), clip[0]["content"][0])
    check("a start picture is handed to ComfyUI and starts the clip", v2["56"]["inputs"]["image"] == "studio-start.png" and v2["55"]["inputs"]["start_image"] == ["56", 0]
          and v2["55"]["inputs"]["length"] == 49 and (v2["55"]["inputs"]["width"], v2["55"]["inputs"]["height"]) == (704, 1280)
          and seen["uploads"] and seen["uploads"][0].startswith("multipart/form-data"), v2["55"])
    check("a start picture outside the allowed folders is refused", clip[2]["isError"] and "start_image must be a full path inside" in clip[2]["content"][0]["text"])


def test_memory_residents():
    print("what is loaded in VRAM and RAM")
    import studio as sm
    found = sm.gpu_clients()
    check("the graphics memory of this account's programs can be read", isinstance(found, dict) and all(isinstance(v, dict) for v in found.values()))
    sm.RESIDENT_CACHE.update(at=0.0, value=None)
    r = sm.memory_residents([{"name": "test-model:7b", "size": 6 * 2 ** 30, "size_vram": 4 * 2 ** 30}], {"gpus": [], "gpu_name": "Card", "vram_total": 8 * 2 ** 30, "vram_used": 5 * 2 ** 30})
    item = next((i for i in r["items"] if i["name"] == "test-model:7b"), None)
    check("a loaded model shows with its part in VRAM and in RAM", item and item["vram"] == 4 * 2 ** 30 and item["ram"] == 2 * 2 ** 30, r)
    check("with the card's size", r["card"] == {"name": "Card", "total": 8 * 2 ** 30, "used": 5 * 2 ** 30}, r["card"])


def test_engine_release():
    """After a reply used the voice, sound or music engine, Studio unloads it before the model's next turn."""
    print("tool engines give memory back before the model's turn")
    import studio as sm
    import http.server
    import types
    state = {"loaded": True, "busy": None, "unloads": 0}

    class Engine(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def answer(self, obj):
            data = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self.answer({"ok": True, "loaded": state["loaded"], "busy": state["busy"]})

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            state["unloads"] += 1
            state["loaded"] = False
            self.answer({"ok": True, "loaded": False})
    engine = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Engine)
    threading.Thread(target=engine.serve_forever, daemon=True).start()
    saved = sm.APP
    try:
        sm.APP = types.SimpleNamespace(cfg={"tool_engines": [f"http://127.0.0.1:{engine.server_address[1]}"], "voice_url": ""})
        events = []
        run = types.SimpleNamespace(engines_used=False, model="quartz:strata", stop=False, emit=lambda kind, **kw: events.append(kind))
        sm.release_engines(run)
        check("without an engine call this turn, nothing is unloaded", state["unloads"] == 0 and state["loaded"])
        run.engines_used = True
        sm.release_engines(run)
        check("after one, the loaded engine is unloaded before the model is asked", state["unloads"] == 1 and not state["loaded"] and run.engines_used is False and "status" in events)
        state["loaded"], run.engines_used = True, True
        run.model = "codex:gpt-6.1-sol"
        sm.release_engines(run)
        check("but not for Codex, which thinks elsewhere", state["unloads"] == 1 and state["loaded"])
        state["busy"], run.engines_used, run.model = "job", True, "quartz:strata"
        sm.release_engines(run)
        check("and never while an engine is working for someone", state["unloads"] == 1)
        check("the voice, sound and music servers count as engines", {"voices", "sounds", "comfyui"} <= sm.ENGINE_SERVERS)

        # The failsafe when the last run ends: engines unload, ComfyUI frees, and a ComfyUI that keeps memory is restarted.
        comfy_seen = {"free": 0}

        class Comfy(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def answer(self, obj):
                data = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self.answer({"devices": [{"torch_vram_total": 1200 * 2 ** 20}]} if self.path == "/system_stats" else {"queue_running": [], "queue_pending": []})

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                comfy_seen["free"] += 1               # it is asked, but keeps its 1.2 GB, as ComfyUI did on 2026-11-18
                self.answer({})
        comfy = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Comfy)
        threading.Thread(target=comfy.serve_forever, daemon=True).start()
        waits = (sm.CLEANUP_WAIT_S, sm.COMFY_RESTART_S)
        try:
            sm.CLEANUP_WAIT_S = sm.COMFY_RESTART_S = 0
            state.update(loaded=True, busy=None)
            sm.APP = types.SimpleNamespace(lock=threading.Lock(), active={}, cfg={"run_cleanup": True, "tool_engines": [f"http://127.0.0.1:{engine.server_address[1]}"],
                                           "voice_url": "", "comfyui_url": f"http://127.0.0.1:{comfy.server_address[1]}", "comfyui_service": "studio-test-no-such-service"})
            sm.cleanup_main()
            last = sm.CLEANUP["last"] or {}
            check("when the last run ends, a loaded engine unloads and ComfyUI is asked to free its models", not state["loaded"] and comfy_seen["free"] == 1
                  and any(x.startswith("unloaded") for x in last.get("steps", [])) and "asked ComfyUI to free its models" in last.get("steps", []), last)
            check("a ComfyUI that still holds memory has its service restarted", any("studio-test-no-such-service" in x and "1.2 GB" in x or "could not restart studio-test-no-such-service" in x
                                                                                       for x in last.get("steps", [])), last)
            state["loaded"], comfy_seen["free"], sm.CLEANUP["last"] = True, 0, None
            sm.APP.active = {"r": types.SimpleNamespace(finished=False)}
            sm.cleanup_main()
            check("but nothing is touched while a run works", state["loaded"] and comfy_seen["free"] == 0 and sm.CLEANUP["last"] is None)
            sm.APP.cfg["run_cleanup"] = False
            sm.schedule_cleanup()
            check("and config run_cleanup turns it off", sm.CLEANUP["pending"] is False)
        finally:
            sm.CLEANUP_WAIT_S, sm.COMFY_RESTART_S = waits
            comfy.shutdown()
    finally:
        sm.APP = saved
        engine.shutdown()
    for name in ("voice_engine", "sound_engine", "music_engine"):
        text = open(os.path.join(ROOT, "tools", name + ".py")).read()
        check(f"{name} also makes room when system memory is short, not only the card", "ram_free_gb() < NEED_RAM_GB" in text and "def ram_free_gb" in text)


def test_video_mcp(tmp):
    print("video MCP server (tools/video_mcp.py, with the real ffmpeg)")
    if not shutil.which("ffmpeg"):
        check("ffmpeg is installed for the video tests", False, "sudo apt install ffmpeg")
        return
    root = os.path.join(tmp, "video-root")
    os.makedirs(root)
    spec = importlib.util.spec_from_file_location("video_mcp_all", os.path.join(ROOT, "tools", "video_mcp.py"))
    vm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vm)
    a, b = os.path.join(root, "a.mp4"), os.path.join(root, "b.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x180:d=1:r=12", "-y", a], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=s=320x180:d=1:r=12", "-y", b], check=True)
    failed = []

    def try_render(tl, name):
        try:
            argv, _length, _ = vm.build(tl, os.path.join(root, name + ".mp4"), True, root, roots=[root])
            done = subprocess.run(argv, capture_output=True, text=True, timeout=120)
            if done.returncode:
                failed.append((name, done.stderr.strip()[-200:]))
        except Exception as exc:          # noqa: BLE001 - reported below
            failed.append((name, str(exc)[:200]))
    for fx in vm.EFFECTS:
        try_render({"width": 320, "height": 180, "fps": 12, "clips": [{"src": a, "effects": [{"type": fx}]}]}, "fx-" + fx)
    for kind in vm.TRANSITIONS:
        try_render({"width": 320, "height": 180, "fps": 12, "clips": [{"src": a}, {"src": b, "transition": {"type": kind, "duration": 0.4}}]}, "tr-" + kind)
    check(f"every one of the {len(vm.EFFECTS)} effects and {len(vm.TRANSITIONS)} transitions renders", not failed and len(vm.EFFECTS) >= 26 and len(vm.TRANSITIONS) >= 58, failed[:4])
    clip, still, tone = os.path.join(root, "clip.mp4"), os.path.join(root, "still.png"), os.path.join(root, "tone.mp3")
    run = lambda *argv: subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *argv], check=True)
    run("-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:duration=3", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", clip)
    run("-f", "lavfi", "-i", "testsrc2=size=800x600", "-frames:v", "1", still)
    run("-f", "lavfi", "-i", "sine=frequency=220:duration=10", tone)
    timeline = {"width": 1280, "height": 720, "fps": 25,
                "clips": [{"src": clip, "in": 0, "out": 3, "effects": [{"type": "fade_in", "duration": 0.5}]},
                          {"src": still, "duration": 2, "transition": {"type": "dissolve", "duration": 0.5},
                           "effects": [{"type": "zoom", "from": 1, "to": 1.2}, {"type": "grayscale"}]},
                          {"color": "#102030", "duration": 1}],
                "titles": [{"text": "Γωνιακό Ράφι", "start": 0.5, "duration": 2, "position": "bottom", "box": True}],
                "audio": [{"src": tone, "start": 1, "volume": 0.5, "fade_out": 1}]}
    out = os.path.join(root, "edit", "final.mp4")
    call = lambda i, name, args: {"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": name, "arguments": args}}
    calls = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
             {**call(3, "render_video", {"timeline": timeline, "output": out}), "params": {"name": "render_video", "arguments": {"timeline": timeline, "output": out}, "_meta": {"progressToken": "t3"}}},
             call(4, "probe_media", {"path": out}),
             call(5, "render_video", {"timeline": timeline, "output": os.path.join(root, "preview.mp4"), "preview": True}),
             call(6, "render_video", {"timeline": {"clips": [{"src": "/etc/passwd"}]}, "output": os.path.join(root, "x.mp4")}),
             call(7, "render_video", {"timeline": {"clips": [{"src": clip, "effects": [{"type": "explode"}]}]}, "output": os.path.join(root, "x.mp4")}),
             call(8, "video_frames", {"path": out, "times": [1, 4]}),
             call(9, "render_video", {"timeline": {"clips": [{"src": "still.png", "duration": 1}]}, "output": os.path.join(root, "video", "rel.mp4")}),
             call(10, "render_video", {"timeline": {"clips": [{"src": "../../etc/passwd"}]}, "output": os.path.join(root, "x.mp4")}),
             # A Strata model's trailer on 2026-11-12: a zoomed picture, a cut, then a dissolve (concat's timebase broke xfade).
             call(11, "render_video", {"timeline": {"clips": [{"src": "still.png", "duration": 1, "effects": [{"type": "zoom"}]},
                                                              {"src": "still.png", "duration": 1, "effects": [{"type": "fade_in"}]},
                                                              {"src": "still.png", "duration": 1, "transition": {"type": "dissolve", "duration": 0.4}}]},
                                       "output": os.path.join(root, "video", "cut-then-dissolve.mp4")})]
    proc = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "video_mcp.py")], input="\n".join(json.dumps(c) for c in calls) + "\n",
                          capture_output=True, text=True, timeout=300, env={**os.environ, "VIDEO_ROOTS": root})
    messages = list(map(json.loads, proc.stdout.splitlines()))
    replies = {m["id"]: m for m in messages if "id" in m}
    notes = [m["params"] for m in messages if m.get("method") == "notifications/progress"]
    check("a render reports its progress to a caller that asks, and only to that one", notes and all(n["progressToken"] == "t3" for n in notes)
          and notes[0]["progress"] == 0 and notes[-1]["progress"] == notes[-1]["total"] > 5 and "Rendering" in notes[0]["message"], notes[:2] + notes[-1:])
    check("it answers the handshake and lists its three tools", [t["name"] for t in replies[2]["result"]["tools"]] == ["render_video", "probe_media", "video_frames"])
    made = replies[3]["result"]
    info = json.loads(replies[4]["result"]["content"][0]["text"])
    check("a timeline with a dissolve, a zoomed picture, a colour card, a title and music renders",
          not made["isError"] and os.path.isfile(out) and abs(info["duration"] - 5.5) < 0.15 and (info["width"], info["height"]) == (1280, 720) and info["audio"],
          (made["content"][0]["text"], info))
    check("with still frames of the result to look at", sum(c["type"] == "image" for c in made["content"]) == 3)
    saved = json.load(open(os.path.join(root, "edit", "final.timeline.json")))
    layout = saved.pop("_layout")
    check("and the timeline saved beside it for the next change", saved == timeline, saved)
    check("with where each part fell, for Studio's timeline view", layout["length"] == 5.5 and [c["start"] for c in layout["clips"]] == [0, 2.5, 4.5]
          and layout["clips"][1]["transition"] == "dissolve" and layout["clips"][1]["effects"] == ["zoom", "grayscale"]
          and layout["titles"][0]["label"] == "Γωνιακό Ράφι" and layout["audio"][0]["start"] == 1 and layout["video"] == "final.mp4", layout)
    preview = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", os.path.join(root, "preview.mp4")],
                                        capture_output=True, text=True).stdout)["streams"][0]
    check("a preview renders small", (preview["width"], preview["height"]) == (640, 360), preview.get("width"))
    check("files outside the allowed folders are refused", replies[6]["result"]["isError"] and "outside the folders" in replies[6]["result"]["content"][0]["text"])
    check("an unknown effect is refused, naming the ones there are", replies[7]["result"]["isError"] and "fade_in" in replies[7]["result"]["content"][0]["text"])
    check("frames can be taken from any video", sum(c["type"] == "image" for c in replies[8]["result"]["content"]) == 2)
    check("a relative path counts from the output's folder or one above it (a Strata model wrote assets/... in a trailer)",
          not replies[9]["result"]["isError"] and os.path.isfile(os.path.join(root, "video", "rel.mp4")), replies[9]["result"]["content"][0]["text"][:300])
    check("a cut followed by a transition renders", not replies[11]["result"]["isError"], replies[11]["result"]["content"][0]["text"][:400])
    check("but cannot climb out of the allowed folders", replies[10]["result"]["isError"] and "outside the folders" in replies[10]["result"]["content"][0]["text"],
          replies[10]["result"]["content"][0]["text"][:200])


def test_audio_editor(c, projects):
    print("audio editor")
    folder = os.path.join(projects, "sounds")
    os.makedirs(os.path.join(folder, "sfx"))
    song = os.path.join(folder, "sfx", "tune.mp3")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=440:duration=4", "-ac", "2", "-c:a", "libmp3lame", song], check=True)
    open(os.path.join(folder, "sfx", "fake.mp3"), "w").write("#EXTM3U\n#EXTINF:1,\n/etc/passwd\n")
    ws = c.post("/api/workspace/open", {"path": folder})[1]["workspace"]["id"]
    code, st, _ = c.post("/api/audio/open", {"ws": ws, "path": "sfx/tune.mp3"})
    check("a sound opens for editing as its first version", code == 200 and abs(st["seconds"] - 4) < 0.1 and st["channels"] == 2 and st["history"] == ["Opened"]
          and not st["can_undo"], st)
    eid = st["id"]
    code, flac, resp = c.get(f"/api/audio/sound?ws={ws}&id={eid}&file={st['version']}")
    check("the version comes as FLAC for the browser", code == 200 and flac[:4] == b"fLaC" and resp.getheader("Content-Type") == "audio/flac", code)
    check("a playlist named .mp3 is refused, so ffmpeg reads no other file", c.post("/api/audio/open", {"ws": ws, "path": "sfx/fake.mp3"})[0] == 400)
    check("only sound files open", c.post("/api/audio/open", {"ws": ws, "path": "../../etc/passwd"})[0] in (400, 403, 404))
    edit = lambda op, **params: c.post("/api/audio/edit", {"ws": ws, "id": eid, "op": op, "params": params})
    st = edit("delete", a=1, b=2)[1]
    check("delete takes out the selection", abs(st["seconds"] - 3) < 0.05 and st["history"][-1] == "Delete 1.00 s" and st["can_undo"] and st["v"] != st["saved_v"], st)
    st = edit("insert_silence", at=1, seconds=0.5)[1]
    check("silence goes in at the cursor", abs(st["seconds"] - 3.5) < 0.05, st["seconds"])
    st = edit("copy", a=0, b=1)[1]
    check("copy keeps a clip and changes nothing", st["clip"] and abs(st["seconds"] - 3.5) < 0.05 and len(st["history"]) == 3, st)
    st = edit("paste", at=0)[1]
    check("paste puts the clip in", abs(st["seconds"] - 4.5) < 0.05 and st["history"][-1] == "Paste", st)
    for op, params in [("fade_in", {"a": 0, "b": 1}), ("gain", {"db": -3}), ("normalize", {"mode": "peak", "target": -1}), ("speed", {"factor": 2, "a": 0, "b": 1}),
                       ("pitch", {"semitones": 3}), ("reverse", {"a": 1, "b": 2}), ("reverb", {"room": "room", "a": 1, "b": 2}), ("echo", {"kind": "short"}),
                       ("fx", {"name": "chorus", "amount": 0.5}), ("move", {"a": 0, "b": 0.5, "to": 2})]:
        code, out, _ = edit(op, **params)
        check(f"{op} works", code == 200 and out["v"] == st["v"] + 1, out)
        st = out if code == 200 else st
    spec = importlib.util.spec_from_file_location("audio_edit_rv", os.path.join(ROOT, "tools", "audio_edit.py"))
    ae = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ae)
    burst = os.path.join(folder, "burst.flac")
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anoisesrc=d=0.3:a=0.5,apad=pad_dur=1.7", "-ac", "1", "-ar", "48000", "-c:a", "flac", "-y", burst], check=True)

    def tail_db(path, start):
        o = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-ss", str(start), "-i", path, "-af", "astats=measure_perchannel=none:measure_overall=RMS_level", "-f", "null", "-"],
                           capture_output=True, text=True).stderr
        found = re.findall(r"RMS level dB: (-?[\d.]+|-inf)", o)
        return float(found[-1]) if found and found[-1] != "-inf" else -200.0
    dry_tail = tail_db(burst, 0.4)
    for room in ae.REVERBS:
        out = os.path.join(folder, f"verb-{room}.flac")
        ae.apply("reverb", {"room": room, "mix": 0.5}, burst, out, 2.0, 48000, 1, work=folder)
        check(f"the {room} reverb can be heard ringing on after the sound", tail_db(out, 0.4) > -55 > dry_tail, (room, tail_db(out, 0.4), dry_tail))
    check("bad settings are refused", edit("gain", db=500)[0] == 400 and edit("fx", name="nope")[0] == 400 and edit("delete")[0] == 400
          and edit("move", a=1, b=2, to=1.5)[0] == 400)
    back = c.post("/api/audio/step", {"ws": ws, "id": eid})[1]
    again = c.post("/api/audio/step", {"ws": ws, "id": eid, "redo": True})[1]
    check("undo and redo step through the versions", back["v"] == st["v"] - 1 and again["v"] == st["v"] and again["can_redo"] is False, (back["v"], again["v"]))
    before = os.path.getsize(song)
    saved = c.post("/api/audio/save", {"ws": ws, "id": eid})
    check("save writes an MP3 over the file and puts the old one in the trash", saved[0] == 200 and saved[1]["saved"] == "sfx/tune.mp3" and saved[1]["trash"]
          and saved[1]["v"] == saved[1]["saved_v"] and os.path.getsize(song) != before and open(song, "rb").read(3) in (b"ID3", b"\xff\xfb", b"\xff\xf3"), saved[:2])
    as_wav = c.post("/api/audio/save", {"ws": ws, "id": eid, "path": "sfx/tune.wav"})
    check("save as writes another format", as_wav[0] == 200 and open(os.path.join(folder, "sfx", "tune.wav"), "rb").read(4) == b"RIFF", as_wav[:2])
    check("save as over an existing file asks first", c.post("/api/audio/save", {"ws": ws, "id": eid, "path": "sfx/tune.wav"})[0] == 409)
    os.utime(song, (time.time() + 5, time.time() + 5))
    check("a file changed on disk since it was opened is not overwritten without asking", c.post("/api/audio/save", {"ws": ws, "id": eid})[0] == 409
          and c.post("/api/audio/save", {"ws": ws, "id": eid, "overwrite": True})[0] == 200)
    sliced = c.post("/api/audio/slices", {"ws": ws, "id": eid, "cuts": [1, 2.5]})
    check("slices are saved beside the sound", sliced[0] == 200 and sliced[1]["saved"] == ["sfx/tune-01.mp3", "sfx/tune-02.mp3", "sfx/tune-03.mp3"]
          and all(os.path.isfile(os.path.join(folder, p)) for p in sliced[1]["saved"]), sliced[:2])
    again = c.post("/api/audio/open", {"ws": ws, "path": "sfx/tune.mp3", "id": eid})[1]
    check("reopening with the tab's id picks up the same edit and its history", again["id"] == eid and len(again["history"]) == len(st["history"]), again["id"])
    check("someone else's edit id is refused", c.post("/api/audio/edit", {"ws": ws, "id": "0" * 16, "op": "reverse", "params": {}})[0] == 404)
    mt = c.get(f"/api/audio/mtime?ws={ws}&path=sfx/tune.mp3")
    check("an open sound can ask when its file last changed", mt[0] == 200 and abs(mt[1]["mtime"] - os.path.getmtime(song)) < 1e-3
          and c.get(f"/api/audio/mtime?ws={ws}&path=../../etc/passwd")[0] in (400, 403, 404), mt[:2])


def test_audio_mcp(tmp):
    print("audio tool server")
    root = os.path.join(tmp, "audio-mcp")
    os.makedirs(os.path.join(root, "proj", ".strata-studio"))
    os.makedirs(os.path.join(root, "proj", "sfx"))
    beep, ding = os.path.join(root, "proj", "sfx", "beep.mp3"), os.path.join(root, "proj", "sfx", "ding.wav")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=440:d=1", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-f", "lavfi", "-i", "sine=f=660:d=1",
                    "-filter_complex", "[0]volume=4[t];[1]atrim=end=1[s];[2]volume=4[u];[t][s][u]concat=n=3:v=0:a=1", "-c:a", "libmp3lame", beep], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=880:d=0.5", ding], check=True)

    def calls(*items):
        msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
        msgs += [{"jsonrpc": "2.0", "id": 3 + i, "method": "tools/call", "params": {"name": n, "arguments": a}} for i, (n, a) in enumerate(items)]
        out = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "audio_mcp.py")], input="\n".join(map(json.dumps, msgs)) + "\n",
                             capture_output=True, text=True, timeout=180, env={**os.environ, "AUDIO_ROOTS": os.path.join(root, "proj")})
        return {m["id"]: m.get("result") for m in map(json.loads, out.stdout.splitlines())}
    r = calls(("audio_info", {"path": beep}), ("audio_waveform", {"path": beep, "highlight": ["0:01", 2]}),
              ("edit_audio", {"path": beep, "edits": [{"op": "delete", "a": "0:01.000", "b": "0:01.500"}, {"op": "insert", "at": 0.5, "from": {"path": ding}},
                                                      {"op": "fade_out"}, {"op": "normalize", "mode": "peak", "target": -1}, {"op": "fx", "name": "chorus"},
                                                      {"op": "move", "a": 0, "b": 0.5, "to": 2.5}, {"op": "insert", "a": 0, "b": 0.2, "from": {"path": beep, "a": 1, "b": 1.2}}]}),
              ("edit_audio", {"path": beep, "edits": [], "output": os.path.join(root, "proj", "sfx", "beep.wav")}),
              ("edit_audio", {"path": beep, "edits": [{"op": "gain", "db": 3}, {"op": "gain", "db": 999}]}),
              ("edit_audio", {"path": "/etc/passwd", "edits": [{"op": "reverse"}]}),
              ("split_audio", {"path": beep, "cuts": [1, "0:02"]}),
              ("edit_audio", {"path": beep, "edits": [{"op": "nope"}]}))
    text = lambda i: " ".join(c["text"] for c in r[i]["content"] if c["type"] == "text")
    check("the server offers the editor's tools", r[2] and [t["name"] for t in r[2]["tools"]] == ["audio_info", "audio_waveform", "edit_audio", "split_audio"], r[2])
    info = json.loads(text(3))
    check("audio_info gives length, levels and silences", abs(info["seconds"] - 3) < 0.1 and info["channels"] == 1 and info["peak_db"] < 0
          and info["loudness_lufs"] is not None and len(info["silences"]) == 1 and abs(info["silences"][0][0] - 1) < 0.05, info)
    check("audio_waveform is a picture with what it shows", r[4]["content"][0]["type"] == "image" and r[4]["content"][0]["mimeType"] == "image/png"
          and "Waveform of beep.mp3" in text(4) and "marked 0:01.000" in text(4), text(4))
    trash = os.path.join(root, "proj", ".strata-studio", "trash")
    check("edit_audio runs a list of the editor's edits in order, saves over the file and puts the old one in the trash",
          not r[5]["isError"] and "1. Delete 0.50 s" in text(5) and "2. Insert" in text(5) and "6. Move 0.50 s" in text(5) and "7. Insert" in text(5)
          and "The earlier file is in" in text(5) and os.path.isdir(trash) and any(r[5]["content"][i]["type"] == "image" for i in range(len(r[5]["content"]))), text(5))
    check("with no edits and an output it converts", not r[6]["isError"] and open(os.path.join(root, "proj", "sfx", "beep.wav"), "rb").read(4) == b"RIFF", text(6))
    check("a bad setting stops it before anything is saved, and says which edit", r[7]["isError"] and "edit 2 (gain)" in text(7) and "nothing was saved" in text(7), text(7))
    check("files outside its folders are refused", r[8]["isError"] and "outside the folders" in text(8), text(8))
    check("split_audio saves the parts beside the sound", not r[9]["isError"] and "Saved 3 parts" in text(9)
          and all(os.path.isfile(os.path.join(root, "proj", "sfx", f"beep-0{i}.mp3")) for i in (1, 2, 3)), text(9))
    check("an unknown edit is named", r[10]["isError"] and "op must be one of" in text(10), text(10))


def test_video_routes(c, projects):
    print("video timelines in Studio")
    folder = os.path.join(projects, "reel")
    os.makedirs(os.path.join(folder, "media"))
    run = lambda *argv: subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *argv], check=True)
    clip, still = os.path.join(folder, "media", "clip.mp4"), os.path.join(folder, "media", "still.png")
    run("-f", "lavfi", "-i", "testsrc=size=320x180:rate=25:duration=3", "-f", "lavfi", "-i", "sine=duration=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest", clip)
    run("-f", "lavfi", "-i", "testsrc2=size=400x300", "-frames:v", "1", still)
    ws = c.post("/api/workspace/open", {"path": folder})[1]["workspace"]["id"]
    made = c.post("/api/video/new", {"ws": ws, "name": "trailer"})
    check("a new video starts as an empty timeline in video/", made[0] == 200 and made[1]["path"] == "video/trailer.timeline.json"
          and os.path.isfile(os.path.join(folder, "video", "trailer.timeline.json")), made[:2])
    check("the same name twice is refused", c.post("/api/video/new", {"ws": ws, "name": "trailer"})[0] == 409)
    listed = c.get(f"/api/video/list?ws={ws}")[1]
    check("the project's timelines and media are listed", [t["path"] for t in listed["timelines"]] == ["video/trailer.timeline.json"]
          and {m["path"]: m["kind"] for m in listed["media"]} == {"media/clip.mp4": "video", "media/still.png": "picture"}, listed)
    media = c.get(f"/api/media/list?ws={ws}")[1]["media"]
    check("the Media list has every picture, video and sound, newest first, with lengths", {m["path"]: m["kind"] for m in media} == {"media/clip.mp4": "video", "media/still.png": "picture"}
          and next(m for m in media if m["kind"] == "video")["seconds"] == 3.0 and all(m["size"] > 0 for m in media)
          and [m["mtime"] for m in media] == sorted((m["mtime"] for m in media), reverse=True), media)
    open(os.path.join(folder, "media", "rock.obj"), "w").write("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
    models = [m for m in c.get(f"/api/media/list?ws={ws}")[1]["media"] if m["kind"] == "model"]
    check("3D models are listed too", [m["path"] for m in models] == ["media/rock.obj"] and "seconds" not in models[0], models)
    os.remove(os.path.join(folder, "media", "rock.obj"))
    check("the Blender route takes only .blend files in the folder", c.get(f"/api/media/blend?ws={ws}&path=media/still.png")[0] == 404
          and c.get(f"/api/media/blend?ws={ws}&path=../../etc/passwd")[0] in (403, 404))
    import studio as sm
    blender = sm.blender_command()
    if blender:
        scene = os.path.join(folder, "scene.blend")
        subprocess.run([blender, "-b", "--factory-startup", "--python-expr", f"import bpy; bpy.ops.wm.save_as_mainfile(filepath={scene!r})"],
                       capture_output=True, timeout=120, check=True)
        got = c.get(f"/api/media/blend?ws={ws}&path=scene.blend")
        check("a Blender file comes back as a GLB, exported by Blender", got[0] == 200 and isinstance(got[1], bytes) and got[1][:4] == b"glTF", got[0])
        again = time.time()
        check("and the second time from the cache", c.get(f"/api/media/blend?ws={ws}&path=scene.blend")[1][:4] == b"glTF" and time.time() - again < 0.5)
        os.remove(scene)
    else:
        print("  (Blender is not installed: the .blend export is not tested)")
    os.makedirs(os.path.join(folder, "node_modules", "x"))
    open(os.path.join(folder, "node_modules", "x", "hidden.png"), "wb").write(b"x")
    check("it skips node_modules and the like", all("node_modules" not in m["path"] for m in c.get(f"/api/media/list?ws={ws}")[1]["media"]))
    path = "video/trailer.timeline.json"
    timeline = {"width": 640, "height": 360, "fps": 25, "clips": [{"src": clip, "in": 0.5, "out": 2.5}, {"src": still, "duration": 2, "transition": {"type": "dissolve", "duration": 0.5}}],
                "titles": [{"text": "Hello", "start": 0.5, "duration": 1.5}], "audio": []}
    saved = c.post("/api/video/save", {"ws": ws, "path": path, "timeline": timeline})
    lay = saved[1].get("layout", {})
    check("saving works out where everything falls, the way a render would", saved[0] == 200 and lay.get("length") == 3.5
          and [x["start"] for x in lay["clips"]] == [0, 1.5] and lay["clips"][0]["kind"] == "video" and lay["clips"][0]["source"] == 3.0
          and lay["clips"][1]["kind"] == "picture" and lay["video"] == "trailer.mp4", saved[:2])
    on_disk = json.load(open(os.path.join(folder, path)))
    bad = c.post("/api/video/save", {"ws": ws, "path": path, "timeline": {**timeline, "clips": [{"src": "/etc/hostname"}]}})
    check("an edit that cannot render is refused and the file stays as it was", bad[0] == 400 and "outside the folders" in bad[1]["error"]
          and json.load(open(os.path.join(folder, path))) == on_disk, bad[:2])
    check("only .timeline.json files are timelines", c.post("/api/video/save", {"ws": ws, "path": "notes.json", "timeline": timeline})[0] == 400)
    check("a path outside the project is refused", c.get(f"/api/video/timeline?ws={ws}&path=../outside.timeline.json")[0] in (400, 403, 404))
    started = c.post("/api/video/render", {"ws": ws, "path": path, "preview": True})
    job = started[1].get("job")
    state = {}
    during = c.get(f"/api/video/timeline?ws={ws}&path={path}")
    check("the timeline can be read while it renders (the reply used to include the ffmpeg process and fail)", during[0] == 200, during[:2])
    for _ in range(120):
        state = c.get(f"/api/video/job?id={job}")[1]
        if state.get("state") != "running":
            break
        time.sleep(0.25)
    shown = c.get(f"/api/video/timeline?ws={ws}&path={path}")[1]
    check("a render runs in the background and reports when it is done", started[0] == 200 and state.get("state") == "done" and state.get("percent") == 100
          and os.path.isfile(os.path.join(folder, "video", "trailer.preview.mp4")), state)
    check("afterwards the timeline names the video it made", shown["video"] == "video/trailer.preview.mp4" and shown["timeline"]["_layout"]["video"] == "trailer.preview.mp4", shown.get("video"))
    vids = {m["path"]: m for m in c.get(f"/api/media/list?ws={ws}")[1]["media"] if m["kind"] == "video"}
    check("in Media, a video made from a timeline names it; others do not", vids["video/trailer.preview.mp4"].get("timeline") == "video/trailer.timeline.json"
          and "timeline" not in vids["media/clip.mp4"], vids)
    check("someone else's render cannot be read", Client(c.port).get(f"/api/video/job?id={job}")[0] in (401, 404))
    check("a timeline without clips cannot render", c.post("/api/video/render", {"ws": ws, "path": "video/missing.timeline.json"})[0] == 404)
    made = c.post("/api/video/new", {"ws": ws, "name": "clip edit", "src": "media/clip.mp4"})
    again = c.post("/api/video/new", {"ws": ws, "name": "clip edit", "src": "media/clip.mp4"})
    first = json.load(open(os.path.join(folder, "video", "clip edit.timeline.json")))
    check("a timeline can start from a video (the player's Edit), with a free name the second time", made[1].get("path") == "video/clip edit.timeline.json"
          and again[1].get("path") == "video/clip edit 2.timeline.json" and first["clips"] == [{"src": os.path.join(folder, "media", "clip.mp4")}]
          and first["_layout"]["length"] == 3.0, (made[:2], again[:2], first.get("_layout")))
    check("only a video in the folder can start one", c.post("/api/video/new", {"ws": ws, "name": "x", "src": "media/still.png"})[0] == 400
          and c.post("/api/video/new", {"ws": ws, "name": "x", "src": "../../etc/hostname"})[0] in (400, 403, 404))


def test_typed_commands(c, projects, st_port, cfg_path):
    print("commands typed in the chat box")
    sys.path.insert(0, ROOT)
    import studio as sm
    folder = os.path.join(projects, "typed")
    os.makedirs(folder)
    with open(os.path.join(folder, "note.txt"), "w") as fh:
        fh.write("secret word: pelican\n")
    outside = os.path.join(projects, "outside-note.txt")
    with open(outside, "w") as fh:
        fh.write("outside word: walrus\n")
    ws = c.post("/api/workspace/open", {"path": folder})[1]["workspace"]["id"]
    session = c.post("/api/agent/sessions", {"ws": ws})[1]["id"]
    stored = lambda: json.load(open(os.path.join(os.path.dirname(cfg_path), "data", "sessions", session + ".json")))
    check("an empty command is refused", c.post("/api/command/run", {"ws": ws, "session": session, "command": " "})[0] == 400)
    check("an unknown chat is refused", c.post("/api/command/run", {"ws": ws, "session": "nope", "command": "ls"})[0] == 404)
    ran = c.post("/api/command/run", {"ws": ws, "session": session, "command": "echo hello-$((40+2)); exit 3"})
    check("a typed command runs and reports its result", ran[0] == 200 and ran[1]["ok"] is False and ran[1]["summary"] == "exit 3" and "hello-42" in ran[1]["output"], ran[:2])
    check("it runs in the folder", "typed" in c.post("/api/command/run", {"ws": ws, "session": session, "command": "pwd"})[1]["output"])
    saved = stored()
    check("it is kept in the chat", sum(e["type"] == "shell" for e in saved["events"]) == 2 and not saved["messages"], saved["events"][-1])
    guest = Client(st_port)
    check("signing out stops it", guest.post("/api/command/run", {"ws": ws, "session": session, "command": "ls"})[0] == 401)
    run_agent(c, ws, session, "hello there, Please read @note.txt", )
    first = stored()["messages"][0]["content"]
    check("the agent hears about the commands with the next message", "hello-42" in first and "Commands I ran myself" in first and first.endswith("hello there, Please read @note.txt"), first[:200])
    check("the user's own words stay as typed in the chat", any(e.get("text") == "hello there, Please read @note.txt" for e in stored()["events"]))
    run_agent(c, ws, session, "hello again")
    second = [m["content"] for m in stored()["messages"] if m["role"] == "user"][-1]
    check("it hears only once", sm.typed_part(second) == "hello again" and "hello-42" not in second, second[:200])
    body = {"session": session, "ws": ws, "model": "quartz:strata", "auto_apply": True, "auto_commands": True}
    status, data, _ = c.post("/api/agent/send", {**body, "message": "hello, see this", "mentions": ["note.txt", "../outside-note.txt", "/etc/passwd", 5, "missing.txt"]})
    for _ in c.sse(f"/api/agent/stream?run={data['run']}", timeout=30):
        pass
    attached = [m["content"] for m in stored()["messages"] if m["role"] == "user"][-1]
    check("a mentioned file goes with the message", "pelican" in attached and "--- note.txt" in attached, attached[:200])
    check("files outside the folder are left out", "walrus" not in attached and "root:" not in attached, attached[:300])
    check("attached text does not change the detected language", sm.user_language([{"role": "user", "content": "Commands I ran myself just now:\n$ ls\nthe quick and the dead and the rest of it\n" + sm.USER_EXTRAS_END + "Διόρθωσε το αρχείο τώρα."}]) == "Greek")
    saved_app = sm.APP
    sm.APP = types.SimpleNamespace(cfg={"rule_reminders": True, "rule_reminder_turns": 20})
    try:
        text = sm.earlier_instructions(types.SimpleNamespace(turns=30, session={"messages": [{"role": "user", "content": "SECRET OUTPUT" + sm.USER_EXTRAS_END + "Use tabs."}]}))
        check("reminders repeat what was typed, not attached files", "Use tabs." in text and "SECRET OUTPUT" not in text, text)
    finally:
        sm.APP = saved_app
    check("no test command is reported plainly", c.get(f"/api/command/test?ws={ws}")[1]["command"] is None)
    with open(os.path.join(folder, "Makefile"), "w") as fh:
        fh.write("test:\n\t@echo ok\n")
    check("the test command is found", c.get(f"/api/command/test?ws={ws}")[1]["command"] == "make test")
    check("changes need a repository", c.get(f"/api/git/changes?ws={ws}")[0] == 400)
    git_cli(folder, "init", "-q")
    git_cli(folder, "add", "-A")
    git_cli(folder, "commit", "-q", "-m", "start")
    with open(os.path.join(folder, "note.txt"), "a") as fh:
        fh.write("another line\n")
    with open(os.path.join(folder, "brand-new.txt"), "w") as fh:
        fh.write("x\n")
    changes = c.get(f"/api/git/changes?ws={ws}")[1]
    check("changes list edits and new files", "+another line" in changes["diff"] and changes["new_files"] == ["brand-new.txt"], changes)
    report = c.get(f"/api/agent/session/export?id={session}")[1]
    report = report.decode() if isinstance(report, bytes) else str(report)
    check("the report includes typed commands", "User ran" in report and "hello-42" in report)


def test_previews(c, projects, st_port):
    print("previews")
    folder = os.path.join(projects, "previewed")
    os.makedirs(os.path.join(folder, "site", "css"))
    os.makedirs(os.path.join(folder, ".git"))
    for name, data in (("site/index.html", b"<h1>hi</h1>"), ("site/css/a.css", b"h1{}"), ("logo.svg", b"<svg xmlns='http://www.w3.org/2000/svg'><script>x()</script></svg>"), ("doc.pdf", b"%PDF-1.4"), (".env", b"SECRET=1"), (".git/config", b"x")):
        with open(os.path.join(folder, name), "wb") as fh:
            fh.write(data)
    with open(os.path.join(projects, "outside.txt"), "w") as fh:
        fh.write("outside")
    os.symlink(os.path.join(projects, "outside.txt"), os.path.join(folder, "link.txt"))
    ws = c.post("/api/workspace/open", {"path": folder})[1]["workspace"]["id"]
    guest = Client(st_port)
    check("a preview token needs a login", guest.post("/api/preview/token", {"ws": ws})[0] == 401)
    check("and a folder of your own", c.post("/api/preview/token", {"ws": "nope"})[0] in (400, 404))
    token = c.post("/api/preview/token", {"ws": ws})[1]["token"]
    def fetch(path):
        conn = http.client.HTTPConnection("127.0.0.1", st_port, timeout=10)
        conn.request("GET", path)        # no cookie, as from a sandboxed page
        r = conn.getresponse()
        return r.status, r.read(), r
    status, body, r = fetch(f"/preview/{token}/site/index.html")
    policy = r.getheader("Content-Security-Policy") or ""
    check("a page is served with its token alone", status == 200 and body == b"<h1>hi</h1>" and r.getheader("Content-Type").startswith("text/html"), status)
    check("always in a sandbox without Studio's origin", policy.startswith("sandbox ") and "allow-scripts" in policy and "allow-same-origin" not in policy and "allow-top-navigation" not in policy and "allow-popups" not in policy, policy)
    check("it cannot sniff its way out or leak the token", r.getheader("X-Content-Type-Options") == "nosniff" and r.getheader("Referrer-Policy") == "no-referrer")
    check("only Studio can embed it", "frame-ancestors 'self'" in policy and not r.getheader("X-Frame-Options"), r.getheader("X-Frame-Options"))
    check("the rest of Studio still refuses to be embedded", c.get("/")[2].getheader("X-Frame-Options") == "DENY")
    check("relative files load too", fetch(f"/preview/{token}/site/css/a.css")[0] == 200)
    check("a folder serves its index", fetch(f"/preview/{token}/site/")[1] == b"<h1>hi</h1>")
    svg = fetch(f"/preview/{token}/logo.svg")
    check("an SVG opened directly is sandboxed too", svg[0] == 200 and "sandbox" in (svg[2].getheader("Content-Security-Policy") or ""))
    check("a PDF is served as a PDF", fetch(f"/preview/{token}/doc.pdf")[2].getheader("Content-Type") == "application/pdf")
    for label, path in (("a wrong token", "/preview/nope/site/index.html"), ("no token", "/preview//site/index.html"), ("hidden files", f"/preview/{token}/.env"), (".git", f"/preview/{token}/.git/config"), ("encoded .git", f"/preview/{token}/%2Egit/config"), ("escaping the folder", f"/preview/{token}/../outside.txt"), ("encoded escape", f"/preview/{token}/..%2F..%2Foutside.txt"), ("a link out of the folder", f"/preview/{token}/link.txt"), ("a missing file", f"/preview/{token}/nope.html")):
        check(f"refused: {label}", fetch(path)[0] in (403, 404), fetch(path)[0])
    sys.path.insert(0, ROOT)
    import studio as sm
    check("tokens run out", sm.PREVIEW_TOKEN_S <= 3600)


def test_migration():
    print("migration from the single-password format")
    sys.path.insert(0, ROOT)
    import hashlib as _h
    import studio as sm
    folder = tempfile.mkdtemp(prefix="studio-migrate-")
    salt = b"s" * 16
    legacy = {"salt": salt.hex(), "iterations": 1000, "hash": sm.Auth._hash("old password", salt, 1000)}
    json.dump(legacy, open(os.path.join(folder, "users.json"), "w"))
    token = "legacy-token"
    json.dump({_h.sha256(token.encode()).hexdigest(): time.time() + 100}, open(os.path.join(folder, "auth_sessions.json"), "w"))
    auth = sm.Auth(folder)
    check("old password still works, as admin", auth.verify("admin", "old password") and auth.users["admin"]["role"] == "admin")
    check("wrong password still fails", not auth.verify("admin", "nope"))
    check("existing session still works", auth.session_user(token)["username"] == "admin")
    check("backup of the old file is kept", json.load(open(os.path.join(folder, "users.json.pre-accounts"))) == legacy)
    check("migration survives a reload", sm.Auth(folder).verify("admin", "old password"))
    shutil.rmtree(folder)


def test_accounts(c, tmp, sample, st_port):
    print("accounts, invites and isolation between users")
    PW = "alice password"

    def guest(ip):
        g = Client(st_port)
        g.ip = ip
        return g

    def invite(role="user"):
        return c.post("/api/admin/invites", {"role": role})[1]["token"]

    alice, bob, anon = guest("10.0.0.1"), guest("10.0.0.2"), guest("10.0.0.3")
    check("anonymous cannot create invites", anon.post("/api/admin/invites", {})[0] == 401)
    check("sign-up needs an invite", anon.post("/api/auth/signup", {"username": "mallory", "password": PW})[0] == 404)
    token = invite()
    check("invite link can be checked", anon.get(f"/api/auth/invite?token={token}")[1]["role"] == "user")
    check("short password refused", anon.post("/api/auth/signup", {"token": token, "username": "alice", "password": "short"})[0] == 400)
    for bad in ("../evil", "a", "has space", "a/b", ".hidden", "x" * 40, 5):
        check(f"bad username {str(bad)[:10]!r} refused", anon.post("/api/auth/signup", {"token": token, "username": bad, "password": PW})[0] == 400)
    check("taken username refused and invite kept", anon.post("/api/auth/signup", {"token": token, "username": "admin", "password": PW})[0] == 409)
    s, _, r = alice.post("/api/auth/signup", {"token": token, "username": "Alice", "password": PW, "display_name": "Alice A"})
    check("signing up works and signs in", s == 200 and alice.get("/api/auth/state")[1]["user"]["username"] == "alice", s)
    check("an invite works only once", anon.post("/api/auth/signup", {"token": token, "username": "other", "password": PW})[0] == 404)
    check("bob signs up", bob.post("/api/auth/signup", {"token": invite(), "username": "bob", "password": "bob password"})[0] == 200)

    for path in ("/api/admin/users",):
        check(f"user cannot read {path}", alice.get(path)[0] == 403)
    for path, body in (("/api/admin/invites", {}), ("/api/admin/users/update", {"username": "alice", "role": "admin"}),
                       ("/api/admin/users/reset-link", {"username": "admin"}), ("/api/admin/users/delete", {"username": "bob"})):
        check(f"user cannot call {path}", alice.post(path, body)[0] == 403)

    cfg = alice.get("/api/config")[1]
    home = cfg["workspace_roots"][0]
    check("user sees only their private folder", cfg["workspace_roots"] == [home] and home.endswith("/alice"), cfg)
    check("user config hides server details", "gateway" not in cfg and "host" not in cfg)
    top = alice.get("/api/workspaces/browse?path=")[1]
    check("browse lists only the private folder", [d["path"] for d in top["dirs"]] == [home])
    check("called Your space, not by its path on the server", top["dirs"][0]["name"] == "Your space" and top["root_labels"] == [{"path": home, "label": "Your space"}], top)
    check("cannot browse another project", alice.get(f"/api/workspaces/browse?path={sample}")[0] == 403)
    check("cannot browse another user", alice.get(f"/api/workspaces/browse?path={os.path.dirname(home)}/bob")[0] == 403)
    check("cannot open another project", alice.post("/api/workspace/open", {"path": sample})[0] == 403)
    check("cannot make a folder elsewhere", alice.post("/api/workspaces/mkdir", {"parent": os.path.dirname(home), "name": "x"})[0] == 403)
    check("can make a folder at home", alice.post("/api/workspaces/mkdir", {"parent": home, "name": "proj"})[0] == 200)
    project = os.path.join(home, "proj")
    with open(os.path.join(project, "README.md"), "w") as fh:
        fh.write("# Sample Project\n")
    s, d, _ = alice.post("/api/workspace/open", {"path": project})
    aws = d["workspace"]["id"]
    check("user folder is always sandboxed", s == 200 and d["workspace"]["sandbox"] is True, d)
    check("user cannot turn the sandbox off", alice.post("/api/workspace/sandbox", {"ws": aws, "enabled": False})[0] == 403)
    check("user cannot publish ports", alice.post("/api/workspace/sandbox", {"ws": aws, "network": "on", "ports": [3000]})[0] == 403)
    check("user can choose the internet mode", alice.post("/api/workspace/sandbox", {"ws": aws, "network": "off"})[0] == 200)
    check("admin cannot turn it off for them either", c.post("/api/workspace/open", {"path": project})[0] == 200 and c.post("/api/workspace/sandbox", {"ws": aws, "enabled": False})[0] == 403)

    os.symlink(sample, os.path.join(home, "escape"))
    check("cannot open a symlink out of the folder", alice.post("/api/workspace/open", {"path": os.path.join(home, "escape")})[0] == 403)
    os.symlink(sample, os.path.join(project, "escape"))
    check("cannot read through a symlink", alice.get(f"/api/files/read?ws={aws}&path=escape/README.md")[0] != 200)
    os.remove(os.path.join(project, "escape")); os.remove(os.path.join(home, "escape"))

    check("bob cannot use alice's folder", bob.get(f"/api/files/tree?ws={aws}&path=")[0] == 404)
    check("bob cannot read alice's file", bob.get(f"/api/files/read?ws={aws}&path=README.md")[0] == 404)
    check("bob cannot list alice's chats", bob.get(f"/api/agent/sessions?ws={aws}")[0] == 404)
    check("bob cannot open a terminal there", bob.post("/api/term/open", {"ws": aws})[0] == 404)
    check("bob cannot open alice's folder by path", bob.post("/api/workspace/open", {"path": project})[0] == 403)
    admin_ws = c.post("/api/workspace/open", {"path": sample})[1]["workspace"]["id"]
    check("alice cannot use the admin's folder", alice.get(f"/api/files/tree?ws={admin_ws}&path=")[0] == 404)
    recent = alice.get("/api/workspace/recent")[1]["workspaces"]
    check("recent folders are private", all(w["path"].startswith(home) for w in recent))
    check("and say where they are inside the person's space, and where they live", all(w["where"].startswith("Your space") and w["place"]["kind"] in ("server", "kept", "passed", "computer") for w in recent),
          [(w["where"], w["place"]) for w in recent])

    s, d, _ = alice.post("/api/agent/sessions", {"ws": aws})
    asess = d["id"]
    check("bob cannot read alice's chat", bob.get(f"/api/agent/session?id={asess}")[0] == 404)
    check("bob cannot delete alice's chat", bob.post("/api/agent/session/delete", {"id": asess})[0] == 404)
    check("admin cannot read alice's chat", c.get(f"/api/agent/session?id={asess}")[0] == 404)
    c.post("/api/queue/release", {})
    s, d, _ = alice.post("/api/agent/send", {"session": asess, "ws": aws, "message": "Rename Sample", "model": "quartz:strata"})
    arun = d["run"]
    check("bob cannot join alice's run", bob.get(f"/api/agent/stream?run={arun}")[0] == 404)
    check("bob cannot stop alice's run", bob.post("/api/agent/stop", {"run": arun})[0] == 404)
    check("bob cannot decide for alice", bob.post("/api/agent/decision", {"run": arun, "approval_id": "x", "decision": "accept"})[0] == 404)
    check("admin cannot stop alice's run", c.post("/api/agent/stop", {"run": arun})[0] == 404)
    events = []
    for name, ev in alice.sse(f"/api/agent/stream?run={arun}"):
        events.append(ev)
        if name == "approval":
            alice.post("/api/agent/decision", {"run": arun, "approval_id": ev["approval_id"], "decision": "accept"})
    ran = [e for e in events if e["type"] == "tool_result" and e["name"] == "run"]
    check("alice's commands run in the container", ran and "IN-SANDBOX" in ran[-1]["output"], ran[-1:])
    alice.post("/api/queue/release", {})

    s, d, _ = alice.post("/api/term/open", {"ws": aws, "cols": 80, "rows": 24})
    term = d["id"]
    check("bob cannot type into alice's terminal", bob.post("/api/term/input", {"id": term, "data": "x"})[0] == 404)
    check("bob cannot watch alice's terminal", bob.get(f"/api/term/stream?id={term}")[0] == 404)
    check("bob cannot close alice's terminal", bob.post("/api/term/close", {"id": term})[0] == 404)
    check("admin cannot type into alice's terminal", c.post("/api/term/input", {"id": term, "data": "x"})[0] == 404)
    status = alice.get("/api/model/status")
    check("status works for users", status[0] == 200)

    second = guest("10.0.0.4")
    s, _, r = second.post("/api/auth/login", {"username": "ALICE", "password": PW, "remember": True})
    check("usernames ignore case; remember me lasts 30 days", s == 200 and f"Max-Age={30 * 24 * 3600}" in r.getheader("Set-Cookie"), r.getheader("Set-Cookie"))
    s, _, r = guest("10.0.0.5").post("/api/auth/login", {"username": "alice", "password": PW})
    check("without remember me the cookie ends with the browser", s == 200 and "Max-Age" not in r.getheader("Set-Cookie"), r.getheader("Set-Cookie"))
    check("wrong username fails", guest("10.0.0.6").post("/api/auth/login", {"username": "nobody", "password": PW})[0] == 401)
    check("another user's password fails", guest("10.0.0.7").post("/api/auth/login", {"username": "alice", "password": "bob password"})[0] == 401)

    check("changing password needs the current one", alice.post("/api/account/password", {"current": "nope nope", "password": "new alice pass"})[0] == 403)
    check("changing password works", alice.post("/api/account/password", {"current": PW, "password": "new alice pass"})[0] == 200)
    check("this device stays signed in", alice.get("/api/config")[0] == 200)
    check("other devices are signed out", second.get("/api/config")[0] == 401)
    check("old password stops working", guest("10.0.0.8").post("/api/auth/login", {"username": "alice", "password": PW})[0] == 401)

    s, d, _ = c.post("/api/admin/users/reset-link", {"username": "alice"})
    reset = d["token"]
    check("reset link names the user", anon.get(f"/api/auth/reset?token={reset}")[1]["username"] == "alice")
    check("an invite is not a reset link", anon.post("/api/auth/reset", {"token": invite(), "password": "another pass"})[0] == 404)
    check("reset refuses a short password and keeps the link", guest("10.0.0.9").post("/api/auth/reset", {"token": reset, "password": "x"})[0] == 400)
    check("reset works", anon.post("/api/auth/reset", {"token": reset, "password": "reset alice pass"})[0] == 200)
    check("reset links work once", guest("10.0.0.10").post("/api/auth/reset", {"token": reset, "password": "again again"})[0] == 404)
    check("reset signs out every device", alice.get("/api/config")[0] == 401)
    check("new password works", alice.post("/api/auth/login", {"username": "alice", "password": "reset alice pass"})[0] == 200)

    s, d, _ = alice.post("/api/term/open", {"ws": aws, "cols": 80, "rows": 24})
    check("disabling works", c.post("/api/admin/users/update", {"username": "alice", "disabled": True})[0] == 200)
    check("disabled user is signed out", alice.get("/api/config")[0] == 401)
    check("disabled user cannot sign in", guest("10.0.0.11").post("/api/auth/login", {"username": "alice", "password": "reset alice pass"})[0] == 403)
    check("enabling works", c.post("/api/admin/users/update", {"username": "alice", "disabled": False})[0] == 200)

    me = "admin"
    check("cannot disable yourself", c.post("/api/admin/users/update", {"username": me, "disabled": True})[0] == 409)
    check("cannot delete yourself", c.post("/api/admin/users/delete", {"username": me})[0] == 409)
    check("cannot demote the last admin", c.post("/api/admin/users/update", {"username": me, "role": "user"})[0] == 409)
    check("bad role refused", c.post("/api/admin/users/update", {"username": "alice", "role": "root"})[0] == 400)

    check("bob has a private folder", bob.get("/api/config")[0] == 200 and os.path.isdir(os.path.join(os.path.dirname(home), "bob")))
    check("deleting a user works", c.post("/api/admin/users/delete", {"username": "bob"})[0] == 200)
    check("deleted user is signed out", bob.get("/api/config")[0] == 401)
    check("a deleted user's name is not reused while their folder exists", guest("10.0.0.12").post("/api/auth/signup", {"token": invite(), "username": "bob", "password": PW})[0] == 409)
    listing = c.get("/api/admin/users")[1]
    check("admin list has users and no secrets", {u["username"] for u in listing["users"]} >= {"admin", "alice"} and "hash" not in json.dumps(listing) and "salt" not in json.dumps(listing))
    pending = listing["invites"]
    check("pending invites are listed, without tokens", pending and "token" not in pending[0])
    check("an invite can be revoked", c.post("/api/admin/invites/revoke", {"id": pending[0]["id"]})[0] == 200)
    check("revoking twice fails", c.post("/api/admin/invites/revoke", {"id": pending[0]["id"]})[0] == 404)
    stored = open(os.path.join(tmp, "app", "data", "auth_tokens.json")).read()
    check("tokens are stored only as hashes", reset not in stored and token not in stored)

    print("your account")
    a1, a2 = guest("10.0.0.20"), guest("10.0.0.21")
    for g in (a1, a2):
        g.post("/api/auth/login", {"username": "alice", "password": "reset alice pass"})
    info = a1.get("/api/account")[1]
    check("your account lists your signed-in browsers, marking this one", len(info["sessions"]) == 2 and sum(x["current"] for x in info["sessions"]) == 1
          and all(x["device"] and len(x["id"]) == 16 for x in info["sessions"]) and info["user"]["display_name"] == "Alice A", info)
    check("and never a session token", "token" not in json.dumps(info))
    s, d, _ = a1.post("/api/account/profile", {"display_name": "  Alice Anders "})
    check("you can change your display name", s == 200 and d["user"]["display_name"] == "Alice Anders" and a2.get("/api/account")[1]["user"]["display_name"] == "Alice Anders", d)
    check("a display name has limits", a1.post("/api/account/profile", {"display_name": "x" * 61})[0] == 400 and a1.post("/api/account/profile", {"display_name": "a\nb"})[0] == 400)
    other = next(x["id"] for x in info["sessions"] if not x["current"])
    mine = next(x["id"] for x in info["sessions"] if x["current"])
    check("this browser is signed out with Sign out, not from the list", a1.post("/api/account/sessions/revoke", {"id": mine})[0] == 400)
    check("nobody can end another person's session", c.post("/api/account/sessions/revoke", {"id": other})[0] == 404)
    check("a bad session id is refused", a1.post("/api/account/sessions/revoke", {"id": "../x"})[0] == 400)
    check("another browser can be signed out from the list", a1.post("/api/account/sessions/revoke", {"id": other})[0] == 200 and a2.get("/api/config")[0] == 401
          and len(a1.get("/api/account")[1]["sessions"]) == 1)
    alice_row = next(u for u in c.get("/api/admin/users")[1]["users"] if u["username"] == "alice")
    check("admins see when each person last signed in", alice_row.get("last_login") and time.time() - alice_row["last_login"] < 60, alice_row)
    import studio as sm
    check("browsers get short device names", sm.device_name("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36") == "Chrome on Windows"
          and sm.device_name("Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/141.0 Safari/537.36 Edg/141.0") == "Edge on Windows"
          and sm.device_name("Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0") == "Firefox on Linux"
          and sm.device_name("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1") == "Safari on iPhone"
          and sm.device_name("") == "A browser")


def test_sharing(c, sample, st_port):
    print("shared projects: the same folder for several people, with rights per person")
    def guest(ip):
        g = Client(st_port)
        g.ip = ip
        return g
    sara, tom, ula = guest("10.0.9.1"), guest("10.0.9.2"), guest("10.0.9.3")
    for g, name in ((sara, "sara"), (tom, "tom"), (ula, "ula")):
        g.post("/api/auth/signup", {"token": c.post("/api/admin/invites", {"role": "user"})[1]["token"], "username": name, "password": name + " password"})
    home = sara.get("/api/config")[1]["workspace_roots"][0]
    proj = sara.post("/api/workspaces/mkdir", {"parent": home, "name": "proj"})[1]["path"]
    ws = sara.post("/api/workspace/open", {"path": proj})[1]["workspace"]
    sara.post("/api/files/write", {"ws": ws["id"], "path": "notes.md", "content": "one\n"})
    check("nobody else can open a private folder", tom.post("/api/workspace/open", {"path": proj})[0] == 403 and tom.get(f"/api/share?ws={ws['id']}")[0] == 404)
    people = sara.get("/api/share/people")[1]["people"]
    check("the owner can see who to share with", any(p["username"] == "tom" for p in people) and not any(p["username"] == "sara" for p in people))
    s, d, _ = sara.post("/api/share/set", {"ws": ws["id"], "username": "tom", "rights": {"files": True}})
    check("the owner shares the folder with a person and chooses their rights", s == 200 and d["members"][0]["username"] == "tom"
          and d["members"][0]["rights"] == {"files": True, "agent": False, "terminal": False, "delete": False, "share": False}, d)
    shared = tom.get("/api/workspace/shared")[1]
    check("it shows under Shared with me, with who shares it", [w["id"] for w in shared["with_me"]] == [ws["id"]] and shared["with_me"][0]["shared_by"] == "sara"
          and sara.get("/api/workspace/shared")[1]["by_me"][0]["with"] == ["tom"], shared)
    s, d, _ = tom.post("/api/workspace/open", {"path": proj})
    check("the person opens the same folder, not a copy", s == 200 and d["workspace"]["id"] == ws["id"] and d["workspace"]["path"] == ws["path"], d)
    check("and its files", "one" in tom.get(f"/api/files/read?ws={ws['id']}&path=notes.md")[1].get("content", ""))
    check("changes by one are the other's files", tom.post("/api/files/write", {"ws": ws["id"], "path": "notes.md", "content": "one\ntwo\n"})[0] == 200
          and "two" in sara.get(f"/api/files/read?ws={ws['id']}&path=notes.md")[1]["content"])
    status, body, _ = tom.post("/api/files/delete", {"ws": ws["id"], "path": "notes.md"})
    check("a right that is off is refused, saying who decides", status == 403 and "sara has not let you delete files" in body.get("error", ""), body)
    check("the terminal and the agent need their own rights", tom.post("/api/term/open", {"ws": ws["id"]})[0] == 403
          and tom.post("/api/agent/send", {"ws": ws["id"], "session": tom.post("/api/agent/sessions", {"ws": ws["id"]})[1]["id"], "message": "hi"})[0] == 403)
    check("and managing people too", tom.post("/api/share/set", {"ws": ws["id"], "username": "ula", "rights": {"files": True}})[0] == 403)
    check("people without the folder still cannot reach it", ula.get(f"/api/files/read?ws={ws['id']}&path=notes.md")[0] == 404)
    s, d, _ = sara.post("/api/share/set", {"ws": ws["id"], "username": "tom", "rights": {"files": True, "agent": True, "terminal": True, "delete": True, "share": True}})
    check("with every switch on the person is a co-owner", all(d["members"][0]["rights"].values()), d)
    check("who may delete files", tom.post("/api/files/delete", {"ws": ws["id"], "path": "notes.md"})[0] == 200)
    check("and share it further", tom.post("/api/share/set", {"ws": ws["id"], "username": "ula", "rights": {"files": False}})[0] == 200
          and ula.get(f"/api/share?ws={ws['id']}")[1]["rights"]["files"] is False)
    check("someone who may only look is refused any change", ula.post("/api/files/write", {"ws": ws["id"], "path": "x.md", "content": "x"})[0] == 403)
    # chats: private by default; their owner can share one with the folder
    sid = sara.post("/api/agent/sessions", {"ws": ws["id"]})[1]["id"]
    run_agent(sara, ws["id"], sid, "Hello")
    check("a chat stays private", sid not in [x["id"] for x in tom.get(f"/api/agent/sessions?ws={ws['id']}")[1]["sessions"]]
          and tom.get(f"/api/agent/session?ws={ws['id']}&id={sid}")[0] == 404)
    check("only its owner can share it", tom.post("/api/agent/session/share", {"ws": ws["id"], "id": sid, "shared": True})[0] == 404)
    check("its owner shares it with the folder", sara.post("/api/agent/session/share", {"ws": ws["id"], "id": sid, "shared": True})[1]["shared"] is True)
    listed = {x["id"]: x for x in tom.get(f"/api/agent/sessions?ws={ws['id']}")[1]["sessions"]}
    check("then everyone with the folder sees it, marked whose it is", sid in listed and listed[sid]["by"] == "sara" and listed[sid]["shared"]
          and tom.get(f"/api/agent/session?ws={ws['id']}&id={sid}")[0] == 200, listed)
    sara.post("/api/queue/release", {})          # the waiting room: sara finishes her turn so tom may use the agent
    _, events = run_agent(tom, ws["id"], sid, "Hello again")
    tom.post("/api/queue/release", {})
    check("and can carry it on", events[-1]["type"] == "done", [e["type"] for e in events][-3:])
    check("someone without the folder still cannot read it", c.get(f"/api/agent/session?ws={ws['id']}&id={sid}")[0] in (200, 404))
    # leaving and removing
    check("a person can leave a folder shared with them", ula.post("/api/share/remove", {"ws": ws["id"], "username": "ula"})[1].get("left") is True
          and ula.get(f"/api/files/read?ws={ws['id']}&path=x.md")[0] == 404)
    check("the owner can take someone out", sara.post("/api/share/remove", {"ws": ws["id"], "username": "tom"})[0] == 200
          and tom.post("/api/workspace/open", {"path": proj})[0] == 403 and tom.get("/api/workspace/shared")[1]["with_me"] == [])
    # an admin's folder whose commands run on the server itself
    aws = c.post("/api/workspace/open", {"path": sample})[1]["workspace"]
    c.post("/api/workspace/sandbox", {"ws": aws["id"], "enabled": False})
    c.post("/api/share/set", {"ws": aws["id"], "username": "tom", "rights": {"files": True, "agent": True, "terminal": True}})
    status, body, _ = tom.post("/api/term/open", {"ws": aws["id"]})
    check("a folder whose commands run on the server needs its Sandbox on before others run anything", status == 403 and "Sandbox" in body.get("error", ""), body)
    check("though they may still change its files", tom.get(f"/api/files/tree?ws={aws['id']}&path=")[0] == 200)
    c.post("/api/share/remove", {"ws": aws["id"], "username": "tom"})


def test_event_types():
    print("every event a run sends is one the page listens for (SSE_TYPES)")
    src, js = open(os.path.join(ROOT, "studio.py")).read(), open(os.path.join(ROOT, "web", "app.js")).read()
    emitted = set(re.findall(r'(?:run|self)\.emit\("(\w+)"', src))
    listened = set(re.findall(r"'(\w+)'", re.search(r"const SSE_TYPES = \[(.*?)\]", js).group(1)))
    # tool_progress was sent but missing from the list, so the page dropped it and the trailer render showed no percentage
    check("the page listens for each event type the server emits", emitted and not emitted - listened, sorted(emitted - listened))


def test_model_servers(tmp):
    """llama.cpp, vLLM and LM Studio: only the OpenAI API, maybe with a key, and the gateway written with /v1."""
    print("model servers with only the OpenAI API (llama.cpp, vLLM, LM Studio)")
    folder = os.path.join(tmp, "openai-server")
    projects = os.path.join(folder, "projects")
    os.makedirs(os.path.join(projects, "app"))
    with open(os.path.join(projects, "app", "a.txt"), "w") as fh:
        fh.write("hello\n")
    gw_port, port = free_port(), free_port()
    gw = subprocess.Popen([sys.executable, os.path.join(ROOT, "mock_gateway.py"), str(gw_port)], stdout=subprocess.DEVNULL,
                          env={**os.environ, "MOCK_OPENAI_ONLY": "1", "MOCK_API_KEY": "sk-local-test"})
    app = os.path.join(folder, "app")
    os.makedirs(app)
    example = json.load(open(os.path.join(ROOT, "studio.config.example.json")))
    check("the example config is for this machine only, with no tool servers or paths of ours, and Updates on",
          example["host"] == "127.0.0.1" and "comfyui_url" not in example and example.get("updates") is True and "/home/" not in json.dumps(example))
    shutil.copy(os.path.join(ROOT, "studio.config.example.json"), app)
    config = os.path.join(app, "studio.config.json")
    servers = []
    try:
        wait_port(gw_port, gw)
        servers.append(start_studio(folder, config, port))
        made = json.load(open(config))
        check("a first start copies the example as the config", made == example, made)
        stopping = servers.pop()
        stopping.terminate()
        stopping.wait(10)
        made.update(gateway=f"http://127.0.0.1:{gw_port}/v1", workspace_roots=[projects], user_root=os.path.join(projects, "studio-users"),
                    max_turns=10, command_timeout_s=20, gateway_key="wrong")
        json.dump(made, open(config, "w"))
        servers.append(start_studio(folder, config, port))
        admin = Client(port)
        admin.post("/api/auth/setup", {"password": "admin password"})
        check("with a wrong key the model list is empty and the gateway is not reachable",
              admin.get("/api/models")[1].get("models") == [] and admin.get("/api/config")[1]["gateway_ok"] is False)
        check("and the page never sees the key", "wrong" not in json.dumps(admin.get("/api/config")[1]))
        check("a new install has Updates on, so local models can be checked and updated", admin.get("/api/config")[1].get("updates") is True)
        stopping = servers.pop()
        stopping.terminate()
        stopping.wait(10)
        made["gateway_key"] = "sk-local-test"
        json.dump(made, open(config, "w"))
        servers.append(start_studio(folder, config, port))
        admin = Client(port)
        admin.post("/api/auth/login", {"username": "admin", "password": "admin password"})
        models = admin.get("/api/models")[1].get("models") or []
        check("the models come from /v1/models, as a Model server group that may use tools",
              len(models) >= 3 and all(m["kind"] == "openai" and m["tools"] is None for m in models) and "llama3.2:3b" in [m["name"] for m in models], models)
        st = admin.get("/api/model/status?model=llama3.2:3b&full=1")[1]
        check("the dashboard says the model is ready, not that the gateway is offline", st.get("state") == "idle" and st.get("label") == "Ready"
              and st.get("server") == "openai", st)
        ws = admin.post("/api/workspace/open", {"path": os.path.join(projects, "app")})[1]["workspace"]["id"]
        sid = admin.post("/api/agent/sessions", {"ws": ws})[1]["id"]
        _, events = run_agent(admin, ws, sid, "Please list the files", model="llama3.2:3b", auto_commands=True)
        names = kinds(events)
        check("a run works through /v1/chat/completions with the key", "done" in names and "error" not in names, names[-6:])
        # connecting a model server from the page (Account & settings → Models)
        d = admin.get("/api/admin/model_server")[1]
        check("the page sees the connected server, its kind and that a key is saved, but never the key",
              d["current"]["ok"] and d["current"]["kind"] == "openai" and d["current"]["has_key"] is True and isinstance(d["found"], list)
              and "sk-local-test" not in json.dumps(d), d)
        t = admin.post("/api/admin/model_server/test", {"gateway": f"http://127.0.0.1:{gw_port}", "key": "nope"})[1]
        check("a test with a wrong key says the server asks for a key", t["ok"] is False and "key" in t["error"], t)
        t = admin.post("/api/admin/model_server/test", {"gateway": f"http://127.0.0.1:{gw_port}"})[1]
        check("a test without a key uses the saved one", t["ok"] and t["count"] >= 3, t)
        refused = free_port()
        s, d, _ = admin.post("/api/admin/model_server", {"gateway": f"http://127.0.0.1:{refused}", "key": ""})
        check("an address where nothing answers is not saved", s == 400 and "Nothing answers" in d.get("error", "")
              and json.load(open(config))["gateway"].endswith("/v1"), d)
        check("nor one that is not a web address", admin.post("/api/admin/model_server", {"gateway": "ftp://x"})[0] == 400)
        bob = Client(port)
        bob.post("/api/auth/signup", {"token": admin.post("/api/admin/invites", {})[1]["token"], "username": "bob", "password": "member password"})
        check("only admins may look for or change the model server", bob.get("/api/admin/model_server")[0] == 403
              and bob.post("/api/admin/model_server", {"gateway": "http://127.0.0.1:1"})[0] == 403)
        ollama_port = free_port()
        servers.append(subprocess.Popen([sys.executable, os.path.join(ROOT, "mock_gateway.py"), str(ollama_port)], stdout=subprocess.DEVNULL))
        wait_port(ollama_port, servers[-1])
        s, d, _ = admin.post("/api/admin/model_server", {"gateway": f"http://127.0.0.1:{ollama_port}/", "key": ""})
        saved = json.load(open(config))
        check("saving another server takes effect at once, without a restart, and forgets the old key", s == 200 and d["gateway_ok"]
              and d["server"]["kind"] == "ollama" and len(d["models"]) >= 3 and saved["gateway"] == f"http://127.0.0.1:{ollama_port}"
              and "gateway_key" not in saved and admin.get("/api/models")[1]["models"][0]["kind"] in ("strata", "ollama"), [d, saved])
    finally:
        for proc in servers + [gw]:
            proc.terminate()


def test_strata_direct(tmp):
    """Strata's own server as the model server, with no Strata gateway: names, state, gauges, loading, session cache, idle unload."""
    print("Strata's own server, without the gateway")
    folder = os.path.join(tmp, "strata-direct")
    projects = os.path.join(folder, "projects")
    os.makedirs(os.path.join(projects, "app"))
    with open(os.path.join(projects, "app", "a.txt"), "w") as fh:
        fh.write("hello\n")
    gw_port, port = free_port(), free_port()
    gw = subprocess.Popen([sys.executable, os.path.join(ROOT, "mock_gateway.py"), str(gw_port)], stdout=subprocess.DEVNULL,
                          env={**os.environ, "MOCK_STRATA": "1", "MOCK_STRATA_LOAD_S": "2"})
    config = os.path.join(folder, "app", "studio.config.json")
    os.makedirs(os.path.dirname(config))
    json.dump({"host": "127.0.0.1", "port": port, "gateway": f"http://127.0.0.1:{gw_port}", "workspace_roots": [projects],
               "user_root": os.path.join(projects, "users"), "max_turns": 10, "command_timeout_s": 20,
               "strata_idle_minutes": 1000, "strata_idle_check_s": 0.5, "session_cache_dir": os.path.join(folder, "sessions")}, open(config, "w"))
    servers = [gw]

    def mock(change=None):
        conn = http.client.HTTPConnection("127.0.0.1", gw_port, timeout=10)
        conn.request("POST" if change is not None else "GET", "/_mock/strata", json.dumps(change or {}), {"Content-Type": "application/json"})
        data = json.loads(conn.getresponse().read())
        conn.close()
        return data

    def engine():
        conn = http.client.HTTPConnection("127.0.0.1", gw_port, timeout=10)
        conn.request("POST", "/_mock/engine", "{}", {"Content-Type": "application/json"})
        data = json.loads(conn.getresponse().read())
        conn.close()
        return data
    try:
        wait_port(gw_port, gw)
        servers.append(start_studio(folder, config, port))
        admin = Client(port)
        admin.post("/api/auth/setup", {"password": "admin password"})
        models = admin.get("/api/models")[1]["models"]
        m = models[0] if models else {}
        check("Strata's own server is found: its model is a Strata model that takes tools and pictures, with a readable name",
              len(models) == 1 and m["name"] == "swift-1.5-iq3_xxs" and m["kind"] == "strata" and m["tools"] and m["vision"]
              and m["label"] == "Swift 1.5 IQ3_XXS (Strata)", models)
        s, d, _ = admin.post("/api/admin/model_names", {"name": "swift-1.5-iq3_xxs", "label": "Quartz"})
        check("an admin names it Quartz, for everyone, in the config", s == 200 and admin.get("/api/models")[1]["models"][0]["label"] == "Quartz"
              and json.load(open(config))["model_names"] == {"swift-1.5-iq3_xxs": "Quartz"}, d)
        bob = Client(port)
        bob.post("/api/auth/signup", {"token": admin.post("/api/admin/invites", {})[1]["token"], "username": "bob", "password": "member password"})
        check("only admins name models", bob.post("/api/admin/model_names", {"name": "swift-1.5-iq3_xxs", "label": "Mine"})[0] == 403)
        check("the Model server box calls it Strata", admin.get("/api/admin/model_server")[1]["current"]["kind"] == "strata")
        st = admin.get("/api/model/status?model=swift-1.5-iq3_xxs&full=1")[1]
        check("before any request it is Not loaded, and the gauges read the card and RAM from Strata", st.get("state") == "off"
              and st["label"] == "Not loaded" and st["metrics"]["gpu"]["vram_total"] == 24560 * 2 ** 20 and st["metrics"]["system"]["total"] == 62 * 2 ** 30, st)
        ws = admin.post("/api/workspace/open", {"path": os.path.join(projects, "app")})[1]["workspace"]["id"]
        sid = admin.post("/api/agent/sessions", {"ws": ws})[1]["id"]
        _, events = run_agent(admin, ws, sid, "Please list the files", model="swift-1.5-iq3_xxs", auto_commands=True)
        loads = [e.get("load") for e in events if e.get("type") == "status" and e.get("load")]
        check("while Strata loads the model by itself, the chat shows the loading bar", loads and all(0 < l["percent"] < 100 for l in loads), loads[-3:])
        check("the run finishes", any(e.get("type") == "done" for e in events) and not any(e.get("type") == "error" for e in events),
              [e.get("type") for e in events][-5:])
        time.sleep(3.2)                     # "Generating" lasts 3 s after the last streamed text
        st = admin.get("/api/model/status?model=swift-1.5-iq3_xxs")[1]
        check("afterwards it is loaded and idle, says when it will unload, and the context gauge is filled",
              st.get("state") == "idle" and "Unloads in about" in st.get("detail", "") and st["last_generation"]["context_percent"], st)
        saved = engine()["saves"]
        check("the chat's engine state is saved when the run ends (session cache through Strata's /slots)", f"studio-{sid}.bin" in saved, saved)
        mock({"loaded": False})
        _, events = run_agent(admin, ws, sid, "Please list the files", model="swift-1.5-iq3_xxs", auto_commands=True)
        check("after Strata unloaded, the next message restores the chat instead of reading it again", f"studio-{sid}.bin" in engine()["restores"]
              and any("Restored this chat" in (e.get("text") or "") for e in events if e.get("type") == "status"))
        cfg = json.load(open(config))
        cfg["strata_idle_minutes"] = 0.02
        json.dump(cfg, open(config, "w"))
        stopping = servers.pop()
        stopping.terminate()
        stopping.wait(10)
        servers.append(start_studio(folder, config, port))
        admin = Client(port)
        admin.post("/api/auth/login", {"username": "admin", "password": "admin password"})
        admin.get("/api/models")
        mock({"loaded": True, "last_request_at": int(time.time()) - 5})
        deadline = time.time() + 10
        while time.time() < deadline and mock()["loaded"]:
            time.sleep(0.3)
        check("Studio unloads Strata after strata_idle_minutes without requests", mock()["loaded"] is False and mock()["unloads"] >= 1, mock())
    finally:
        for proc in servers:
            proc.terminate()


def test_strata_started(tmp):
    """Studio starts Strata's server itself (strata_dir): every profile is a model, a chat starts its profile, another
    profile switches the server, the Graphics card choice restarts it with another VRAM reserve."""
    print("Strata started by Studio")
    folder = os.path.join(tmp, "strata-started")
    projects, strata = os.path.join(folder, "projects"), os.path.join(folder, "Strata")
    os.makedirs(os.path.join(projects, "app"))
    os.makedirs(os.path.join(strata, "serve"))
    os.makedirs(os.path.join(strata, ".venv", "bin"))
    os.symlink(sys.executable, os.path.join(strata, ".venv", "bin", "python"))
    with open(os.path.join(projects, "app", "a.txt"), "w") as fh:
        fh.write("hello\n")
    starts = os.path.join(strata, "starts.jsonl")
    with open(os.path.join(strata, "serve", "server.py"), "w") as fh:     # plays Strata's server: records the start, runs the mock
        fh.write("import argparse, json, os, sys\n"
                 "ap = argparse.ArgumentParser()\n"
                 "for a in ('--engine', '--config', '--host'): ap.add_argument(a)\n"
                 "ap.add_argument('--port', type=int)\nap.add_argument('--lazy', action='store_true')\n"
                 "a, _ = ap.parse_known_args()\ncfg = json.load(open(a.config))\n"
                 f"open({starts!r}, 'a').write(json.dumps({{'model': cfg['model_name'], 'args': cfg['args'], 'lazy': a.lazy, 'pid': os.getpid(), 'cwd': os.getcwd()}}) + '\\n')\n"
                 "os.environ.update(MOCK_STRATA='1', MOCK_STRATA_MODEL=cfg['model_name'], MOCK_STRATA_LOAD_S='1.5', MOCK_STRATA_LOG=cfg.get('log', ''))\n"
                 "import runpy\nsys.argv = ['mock_gateway.py', str(a.port)]\n"        # keeps this command line, as Strata's server does
                 f"runpy.run_path({os.path.join(ROOT, 'mock_gateway.py')!r}, run_name='__main__')\n")
    for stem, model, ctx in (("swift-iq3_xxs", "swift-1.5-iq3_xxs", "262144"), ("swift-iq3_xxs-128k", "swift-1.5-iq3_xxs-128k", "131072")):
        json.dump({"model_name": model, "args": ["--max-context", ctx, "--vram-reserve-mib", "700"], "user_args": ["--vram-reserve-mib", "700"],
                   "log": os.path.join(strata, f"strata-{stem}.log"), "vision": {"mmproj": "x"}}, open(os.path.join(strata, f"strata-{stem}.json"), "w"))
        open(os.path.join(strata, f"strata-{stem}.log"), "w").write("[strata] almost ready ... (an old load)\n")
    port, strata_port = free_port(), free_port()
    config = os.path.join(folder, "app", "studio.config.json")
    os.makedirs(os.path.dirname(config))
    json.dump({"host": "127.0.0.1", "port": port, "gateway": f"http://127.0.0.1:{strata_port}", "workspace_roots": [projects],
               "user_root": os.path.join(projects, "users"), "max_turns": 10, "command_timeout_s": 20, "strata_dir": strata,
               "session_cache_dir": os.path.join(folder, "sessions")}, open(config, "w"))
    servers = []

    def started():
        return [json.loads(l) for l in open(starts)] if os.path.exists(starts) else []

    def alive(pid):
        import studio as sm
        return sm.pid_alive(pid)
    try:
        servers.append(start_studio(folder, config, port))
        admin = Client(port)
        admin.post("/api/auth/setup", {"password": "admin password"})
        d = admin.get("/api/models")[1]
        names = sorted(m["name"] for m in d["models"])
        check("every Strata profile is a model before any server runs, and the gateway counts as reachable",
              names == ["swift-1.5-iq3_xxs", "swift-1.5-iq3_xxs-128k"] and all(m["kind"] == "strata" and m["vision"] for m in d["models"])
              and admin.get("/api/config")[1]["gateway_ok"] and not started(), d)
        st = admin.get("/api/model/status?model=swift-1.5-iq3_xxs")[1]
        check("with no server running the model is Not loaded, not Gateway offline", st.get("state") == "off" and st.get("label") == "Not loaded", st)
        ws = admin.post("/api/workspace/open", {"path": os.path.join(projects, "app")})[1]["workspace"]["id"]
        sid = admin.post("/api/agent/sessions", {"ws": ws})[1]["id"]
        _, events = run_agent(admin, ws, sid, "Please list the files", model="swift-1.5-iq3_xxs", auto_commands=True)
        texts = [e.get("text") or "" for e in events if e.get("type") == "status"]
        steps = {e["load"]["step"] for e in events if e.get("type") == "status" and e.get("load")}
        s1 = started()
        check("a chat with a Strata model starts Strata's server with that profile, unloaded at first (--lazy), in the Strata folder",
              len(s1) == 1 and s1[0]["model"] == "swift-1.5-iq3_xxs" and s1[0]["lazy"] and os.path.realpath(s1[0]["cwd"]) == os.path.realpath(strata)
              and any(t.startswith("Starting Strata with") for t in texts), [s1, texts[:4]])
        check("the loading bar follows the engine's own log lines (this load's, not an old one)",
              bool(steps & {"Reading weights", "Starting the CPU workers", "Almost ready"}), steps)
        check("the run finishes", any(e.get("type") == "done" for e in events) and not any(e.get("type") == "error" for e in events), [e.get("type") for e in events][-4:])
        sid2 = admin.post("/api/agent/sessions", {"ws": ws})[1]["id"]
        _, events = run_agent(admin, ws, sid2, "Please list the files", model="swift-1.5-iq3_xxs-128k", auto_commands=True)
        s2 = started()
        check("a chat with another profile switches Strata: the first server stops, the other starts",
              len(s2) == 2 and s2[1]["model"] == "swift-1.5-iq3_xxs-128k" and not alive(s1[0]["pid"])
              and any((e.get("text") or "").startswith("Switching Strata from swift-1.5-iq3_xxs") for e in events if e.get("type") == "status"), s2)
        check("Studio starts from its own copy of the profile and never edits the original",
              json.load(open(os.path.join(strata, "strata-swift-iq3_xxs.json")))["args"][-1] == "700"
              and os.path.exists(os.path.join(folder, "app", "data", "strata", "strata-swift-iq3_xxs-128k.json")))
        c = admin.post("/api/card", {"mode": "tools"})[1]
        check("Tools ready while Strata is loaded waits for it to be idle (pending)", c.get("pending") and c.get("strata_server") == "studio"
              and c["pool"]["direct"], c)
        c = admin.post("/api/card/restart", {})[1]
        check("Restart applies it: Studio's Strata server stops", not c.get("pending") and not alive(s2[1]["pid"]), c)
        _, events = run_agent(admin, ws, sid2, "Please list the files", model="swift-1.5-iq3_xxs-128k", auto_commands=True)
        s3 = started()
        check("the next start leaves room for the tools: --vram-reserve-mib 9216", len(s3) == 3
              and s3[2]["args"][s3[2]["args"].index("--vram-reserve-mib") + 1] == "9216", s3[-1:])
        c = admin.post("/api/card/strata_idle", {"minutes": 5})[1]
        check("the idle time is Studio's own here", c.get("strata_idle") == 5 and json.load(open(config))["strata_idle_minutes"] == 5, c)
        gone = servers.pop()
        gone.kill()                        # Studio ends without stopping its Strata (no systemd to end both)
        gone.wait(10)
        check("the Strata it started keeps running", alive(started()[-1]["pid"]))
        servers.append(start_studio(folder, config, port))
        admin = Client(port)
        admin.post("/api/auth/login", {"username": "admin", "password": "admin password"})
        _, events = run_agent(admin, ws, sid, "Please list the files", model="swift-1.5-iq3_xxs", auto_commands=True)
        s4 = started()
        check("a new Studio takes that server back as its own: it switches it instead of calling it another program's",
              len(s4) == 4 and s4[3]["model"] == "swift-1.5-iq3_xxs" and not alive(s3[2]["pid"])
              and not any(e.get("type") == "error" for e in events), [s4[-1:], [e.get("message") for e in events if e.get("type") == "error"]])
    finally:
        for proc in servers:
            proc.terminate()
        for s in started():
            try:
                os.kill(s["pid"], 15)
            except OSError:
                pass


def test_local_models(tmp):
    """A PolyStrata folder (polystrata_dir) with local_port: its models sit beside the gateway's (Ollama), a chat with one
    starts its server on local_port from terminal/, and a gateway model still goes to the gateway."""
    print("Local models beside the gateway")
    folder = os.path.join(tmp, "local-models")
    projects, poly = os.path.join(folder, "projects"), os.path.join(folder, "PolyStrata")
    term = os.path.join(poly, "terminal")
    os.makedirs(os.path.join(projects, "app"))
    os.makedirs(os.path.join(term, "serve"))
    os.makedirs(os.path.join(term, ".venv", "bin"))
    os.symlink(sys.executable, os.path.join(term, ".venv", "bin", "python"))
    open(os.path.join(projects, "app", "a.txt"), "w").write("hello\n")
    starts = os.path.join(poly, "starts.jsonl")
    with open(os.path.join(term, "serve", "server.py"), "w") as fh:
        fh.write("import argparse, json, os, sys\n"
                 "ap = argparse.ArgumentParser()\n"
                 "for a in ('--engine', '--config', '--host'): ap.add_argument(a)\n"
                 "ap.add_argument('--port', type=int)\nap.add_argument('--lazy', action='store_true')\n"
                 "a, _ = ap.parse_known_args()\ncfg = json.load(open(a.config))\n"
                 f"open({starts!r}, 'a').write(json.dumps({{'model': cfg['model_name'], 'args': cfg['args'], 'port': a.port, 'cwd': os.getcwd(), 'pid': os.getpid()}}) + '\\n')\n"
                 "os.environ.update(MOCK_STRATA='1', MOCK_STRATA_MODEL=cfg['model_name'], MOCK_STRATA_LOAD_S='0.5', MOCK_STRATA_LOG=cfg.get('log', ''))\n"
                 "import runpy\nsys.argv = ['mock_gateway.py', str(a.port)]\n"
                 f"runpy.run_path({os.path.join(ROOT, 'mock_gateway.py')!r}, run_name='__main__')\n")
    json.dump({"model_name": "ornith-1.5-122b-ud-iq3_xxs", "display_name": "Ornith 1.5 122B UD-IQ3_XXS", "args": ["--max-context", "131072"],
               "exe": "engine-poly/strata-poly", "log": os.path.join(term, "strata-ornith.log")}, open(os.path.join(term, "strata-ornith.json"), "w"))
    port, gw_port, local_port = free_port(), free_port(), free_port()
    config = os.path.join(folder, "app", "studio.config.json")
    os.makedirs(os.path.dirname(config))
    json.dump({"host": "127.0.0.1", "port": port, "gateway": f"http://127.0.0.1:{gw_port}", "workspace_roots": [projects],
               "user_root": os.path.join(projects, "users"), "max_turns": 10, "command_timeout_s": 20, "polystrata_dir": poly,
               "local_port": local_port, "session_cache_dir": os.path.join(folder, "sessions")}, open(config, "w"))
    servers = [subprocess.Popen([sys.executable, os.path.join(ROOT, "mock_gateway.py"), str(gw_port)], stdout=subprocess.DEVNULL)]

    def started():
        return [json.loads(l) for l in open(starts)] if os.path.exists(starts) else []
    try:
        wait_port(gw_port, servers[0])
        servers.append(start_studio(folder, config, port))
        admin = Client(port)
        admin.post("/api/auth/setup", {"password": "admin password"})
        d = admin.get("/api/models")[1]
        by = {m["name"]: m for m in d["models"]}
        check("PolyStrata's models are listed beside the gateway's, named as its config names them, before any server runs",
              by.get("ornith-1.5-122b-ud-iq3_xxs", {}).get("local") == "polystrata" and by["ornith-1.5-122b-ud-iq3_xxs"]["label"].startswith("Ornith 1.5 122B")
              and any(m["kind"] == "ollama" for m in d["models"]) and not started(), sorted(by))
        st = admin.get("/api/model/status?model=ornith-1.5-122b-ud-iq3_xxs")[1]
        check("a local model with no server running is Not loaded", st.get("label") == "Not loaded", st)
        ws = admin.post("/api/workspace/open", {"path": os.path.join(projects, "app")})[1]["workspace"]["id"]
        sid = admin.post("/api/agent/sessions", {"ws": ws})[1]["id"]
        _, events = run_agent(admin, ws, sid, "Please list the files", model="ornith-1.5-122b-ud-iq3_xxs", auto_commands=True)
        s1 = started()
        texts = [e.get("text") or "" for e in events if e.get("type") == "status"]
        check("a chat with it starts PolyStrata's server on local_port, from terminal/, and the run finishes",
              len(s1) == 1 and s1[0]["port"] == local_port and os.path.realpath(s1[0]["cwd"]) == os.path.realpath(term)
              and "--vram-reserve-mib" not in s1[0]["args"] and any(t.startswith("Starting PolyStrata with Ornith") for t in texts)
              and any(e.get("type") == "done" for e in events) and not any(e.get("type") == "error" for e in events), [s1, texts[:3]])
        st = admin.get("/api/model/status?model=ornith-1.5-122b-ud-iq3_xxs")[1]
        check("its status comes from the local server", st.get("state") not in (None, "offline"), st)
        ollama = next(m["name"] for m in d["models"] if m["kind"] == "ollama" and m.get("tools"))
        sid2 = admin.post("/api/agent/sessions", {"ws": ws})[1]["id"]
        _, events = run_agent(admin, ws, sid2, "Please list the files", model=ollama, auto_commands=True)
        check("a gateway model still goes to the gateway, and no second local server starts",
              any(e.get("type") == "done" for e in events) and len(started()) == 1, [e.get("type") for e in events][-3:])
    finally:
        for proc in servers:
            proc.terminate()
        for s in started():
            try:
                os.kill(s["pid"], 15)
            except OSError:
                pass


def main():
    tmp = tempfile.mkdtemp(prefix="studio-test-")
    projects, sample, outside = make_fixture(tmp)
    gw_port, st_port = free_port(), free_port()
    cfg_path = os.path.join(tmp, "app", "studio.config.json")
    os.makedirs(os.path.dirname(cfg_path))
    # A stand-in docker that records its arguments, so the sandbox can be tested without containers.
    stub_bin = os.path.join(tmp, "bin")
    os.makedirs(stub_bin)
    os.environ["STUDIO_TEST_DOCKER_LOG"] = os.path.join(tmp, "docker.log")
    # No Greek lexicon unless a test builds its own: with this machine's, replies' mixed words would be repaired
    # (repair_mixed) and the send-back tests would depend on where they run.
    os.environ["GREEK_LEXICON"] = os.path.join(tmp, "no-greek-lexicon.sqlite")
    with open(os.path.join(stub_bin, "docker"), "w") as fh:
        fh.write('#!/bin/sh\necho "$*" >> "$STUDIO_TEST_DOCKER_LOG"\n'
                 'case "$1" in inspect) exit 1;; exec) echo IN-SANDBOX;; esac\nexit 0\n')
    os.chmod(os.path.join(stub_bin, "docker"), 0o755)
    os.environ["PATH"] = stub_bin + os.pathsep + os.environ["PATH"]
    with open(cfg_path, "w") as fh:
        json.dump({"host": "127.0.0.1", "port": st_port, "gateway": f"http://127.0.0.1:{gw_port}",
                   "workspace_roots": [projects], "default_model": "quartz:strata",
                   "max_turns": 10, "command_timeout_s": 20, "user_root": os.path.join(projects, "studio-users"), "installer_catalog": "test",
                   "strata_vocab": make_vocab(tmp)}, fh)
    gw = subprocess.Popen([sys.executable, os.path.join(ROOT, "mock_gateway.py"), str(gw_port)],
                          stdout=subprocess.DEVNULL)
    studio = None
    try:
        wait_port(gw_port, gw)
        studio = start_studio(tmp, cfg_path, st_port)
        c = Client(st_port)
        # ONLY=test_goal,test_plan_mode runs just those (after the shared setup in test_all, which always runs).
        only = set(filter(None, os.environ.get("ONLY", "").split(",")))
        test_all(c, tmp, sample, outside, gw_port, cfg_path, st_port)
        if not only or "test_overnight_scoring" in only:
            test_overnight_scoring()
        if not only or "test_rule_checks" in only:
            test_rule_checks(c, projects, gw_port)
        if not only or "test_repeat_and_stray" in only:
            test_repeat_and_stray(c, projects, gw_port)
        if not only or "test_script_ban" in only:
            test_script_ban(c, projects, gw_port)
        if not only or "test_sounds" in only:
            test_sounds(tmp)
        if not only or "test_event_types" in only:
            test_event_types()
        if not only or "test_improvements" in only:
            test_improvements(c, projects, cfg_path)
        if not only or "test_sync" in only:
            test_sync(c, projects, st_port, gw_port)
        if not only or "test_typed_commands" in only:
            test_typed_commands(c, projects, st_port, cfg_path)
        if not only or "test_plan" in only:
            test_plan(c, projects, gw_port)
        if not only or "test_guide" in only:
            test_guide(c, projects, gw_port)
        if not only or "test_page_build" in only:
            test_page_build(c)
        if not only or "test_comfyui_dashboard" in only:
            test_comfyui_dashboard(c, gw_port)
        if not only or "test_card" in only:
            test_card(c, gw_port)
        if not only or "test_session_cache" in only:
            test_session_cache(c, projects, gw_port, cfg_path)
        if not only or "test_model_servers" in only:
            test_model_servers(tmp)
        if not only or "test_strata_direct" in only:
            test_strata_direct(tmp)
        if not only or "test_strata_started" in only:
            test_strata_started(tmp)
        if not only or "test_local_models" in only:
            test_local_models(tmp)
        if not only or "test_announce" in only:
            test_announce(c, projects, gw_port)
        if not only or "test_questions" in only:
            test_questions(c, projects, gw_port)
        if not only or "test_rewind_branch" in only:
            test_rewind_branch(c, projects)
        if not only or "test_plan_mode" in only:
            test_plan_mode(c, projects)
        if not only or "test_compact" in only:
            test_compact(c, projects, gw_port)
        if not only or "test_turn_guard" in only:
            test_turn_guard(c, projects, gw_port)
        if not only or "test_run_files" in only:
            test_run_files(c, projects)
        if not only or "test_goal" in only:
            test_goal(c, projects, gw_port)
        if not only or "test_tasks" in only:
            test_tasks(c, projects)
        if not only or "test_timeskip" in only:
            test_timeskip(c, projects, tmp)
        if not only or "test_user_tools_effort" in only:
            test_user_tools_effort(c, projects, gw_port)
        if not only or "test_pipes" in only:
            test_pipes(c, projects)
        if not only or "test_model_loading" in only:
            test_model_loading(c, projects, gw_port, cfg_path)
        if not only or "test_live" in only:
            test_live(c, projects, st_port)
        if not only or "test_attachments_and_scripts" in only:
            test_attachments_and_scripts(c, projects, st_port, gw_port, cfg_path)
        if not only or "test_languagetool_mcp" in only:
            test_languagetool_mcp()
        if not only or "test_greek_grammar" in only:
            test_greek_grammar(c, st_port, tmp)
        if not only or "test_documents" in only:
            test_documents(c, projects, gw_port)
            test_office_view(c, projects)
        if not only or "test_listmonk_mcp" in only:
            test_listmonk_mcp(tmp)
        if not only or "test_updates" in only:
            test_updates(tmp)
        if not only or "test_installer" in only:
            test_installer(c)
        if not only or "test_browser_tool" in only:
            test_browser_tool()
        if not only or "test_comfyui_mcp" in only:
            test_comfyui_mcp()
        if not only or "test_memory_residents" in only:
            test_memory_residents()
            test_engine_release()
        if not only or "test_video_mcp" in only:
            test_video_mcp(tmp)
        if not only or "test_video_routes" in only:
            test_video_routes(c, projects)
        if not only or "test_audio_editor" in only:
            test_audio_editor(c, projects)
        if not only or "test_audio_mcp" in only:
            test_audio_mcp(tmp)
        if not only or "test_previews" in only:
            test_previews(c, projects, st_port)
        if not only or "test_migration" in only:
            test_migration()
        if not only or "test_estimates" in only:
            test_estimates()
        if not only or "test_mcp" in only:
            test_mcp(tmp, gw_port)
        if not only or "test_stale_sandbox" in only:
            test_stale_sandbox(tmp)
        if not only or "test_presence" in only:
            test_presence(tmp, gw_port)
        if not only or "test_waiting_room" in only:
            test_waiting_room(tmp, gw_port)
        if not only or "test_codex" in only:
            test_codex(tmp, gw_port)
        if not only or "test_pc_folders" in only:
            test_pc_folders(tmp, gw_port)
        if not only or "test_voices" in only:
            test_voices(tmp, gw_port)
    finally:
        for p in (studio, gw):
            if p and p.poll() is None:
                p.terminate()
        for p in (studio, gw):
            if p:
                try:
                    p.wait(5)
                except subprocess.TimeoutExpired:
                    p.kill()
    log_path = os.path.join(tmp, "studio.log")
    log_text = open(log_path).read() if os.path.exists(log_path) else ""
    check("server log has no Traceback", "Traceback" not in log_text, log_text[-1500:])
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{RESULTS['pass']} passed, {RESULTS['fail']} failed")
    sys.exit(1 if RESULTS["fail"] else 0)


def sm_was_cut_off(result):
    sys.path.insert(0, ROOT)
    import studio as sm
    return sm.was_cut_off(result)


def make_vocab(tmp):
    """A small byte-level BPE vocab.json in the shape of Strata's tokenizer: ids 0-3 plain, 4-9 write banned scripts."""
    sys.path.insert(0, ROOT)
    import studio as sm
    encode = {b: ch for ch, b in sm.gpt2_byte_decoder().items()}
    words = ["hello", " θάλασσα", "\u03ac", " ok", " вода", "水", "の", "한", "\uff0c", "\u6c34"[:1].encode()[:2], " ماء", "ồ", " café", " ἀρχή"]
    vocab = {}
    for i, w in enumerate(words):
        raw = w if isinstance(w, bytes) else w.encode()
        vocab["".join(encode[b] for b in raw)] = i
    vocab["<|im_end|>"] = 14
    folder = os.path.join(tmp, "tokenizer")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "vocab.json"), "w") as fh:
        json.dump(vocab, fh)
    return os.path.join(folder, "vocab.json")


def test_script_ban(c, projects, gw_port):
    print("script ban (logit_bias)")
    sys.path.insert(0, ROOT)
    import studio as sm
    folder = os.path.join(projects, "banned")
    os.makedirs(folder)
    ws = c.post("/api/workspace/open", {"path": folder})[1]["workspace"]["id"]

    def sent_for(text, sid=None):
        before = len(c_get_json(gw_port, "/_mock/requests")["requests"])
        run_agent(c, ws, sid or c.post("/api/agent/sessions", {"ws": ws})[1]["id"], text)
        return c_get_json(gw_port, "/_mock/requests")["requests"][before:]

    sent = sent_for("hello there, γράψε κάτι για τη θάλασσα")
    check("a Strata model is sent the ban: the tokens that write Cyrillic, CJK, kana, Hangul, Arabic and Vietnamese, and only those",
          sent and all(r["logit_bias"] == [4, 5, 6, 7, 8, 9, 10, 11] for r in sent), [r["logit_bias"] for r in sent])
    sent = sent_for("Translate hello into Japanese")
    check("not when the user names one of those languages", sent and not any(r["logit_bias"] for r in sent))
    sent = sent_for("hello, what does the comment # 修复 in main.py say?")
    check("not when the conversation already holds such text, which the model may have to quote", sent and not any(r["logit_bias"] for r in sent))
    saved, sm.APP = sm.APP, types.SimpleNamespace(cfg={}, gateway_kind="manager", strata_models=set())
    try:
        run = types.SimpleNamespace(model="quartz:strata")
        note = {"role": "user", "content": sm.SCRIPT_NOTE.format(what="reply", why="2 Chinese characters (更正)")}
        check("Studio's own send-back note, which names Chinese and quotes the characters, does not turn the ban off",
              sm.wants_script_ban(run, [{"role": "user", "content": "Γράψε τα σχόλια στα ελληνικά"}, {"role": "assistant", "content": "Έγινε 更正"}, note]))
        check("nor does a stray word in the model's own reply", sm.wants_script_ban(run, [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "σελίδα интерфейса"}]))
        check("a file the model read still does", not sm.wants_script_ban(run, [{"role": "user", "content": "hi"}, {"role": "tool", "content": "# 注释"}]))
    finally:
        sm.APP = saved
    refused = c_post_json(gw_port, "/_mock/bias", {"refuse": True})["refused"]
    try:
        sid = c.post("/api/agent/sessions", {"ws": ws})[1]["id"]
        sent = sent_for("hello again", sid)
        check("an engine that refuses logit_bias gets the request again without it, and the run goes on",
              c_post_json(gw_port, "/_mock/bias", {})["refused"] == refused + 1 and sent and sent[0]["logit_bias"] == []
              and c.get(f"/api/agent/session?id={sid}")[1]["events"][-1]["type"] == "done", [(r["logit_bias"]) for r in sent])
        sent = sent_for("hello once more", sid)
        check("that model is then asked without it", sent and not any(r["logit_bias"] for r in sent))
    finally:
        c_post_json(gw_port, "/_mock/bias", {"refuse": False})
    check("the vocabulary's own reading: lead bytes of those scripts (Arabic too), fullwidth forms, CJK pieces and Vietnamese; "
          "accented Latin and polytonic Greek stay", sm.vocab_script_bans(make_vocab(os.path.join(projects, "..", "vocab2"))) == [4, 5, 6, 7, 8, 9, 10, 11])


def test_rule_checks(c, projects, gw_port):
    print("rule checks: docstrings the user asked for, and earlier requests before a run ends")
    sys.path.insert(0, ROOT)
    import studio as sm
    saved, sm.APP = sm.APP, types.SimpleNamespace(cfg={})
    folder = os.path.join(projects, "..", "rule-unit")
    os.makedirs(folder, exist_ok=True)
    try:
        say = lambda *texts: types.SimpleNamespace(session={"messages": [{"role": "user", "content": t} for t in texts]})
        greek = say("Κανόνες για όλη τη συζήτηση: (α) απαντάς πάντα στα ελληνικά, (β) κάθε νέα συνάρτηση Python έχει docstring στα ελληνικά. Διάβασε τα αρχεία.",
                    "Πρόσθεσε την εντολή clients")
        check("a rule from the first message is found, with its language", sm.docstring_rule(greek)
              == ("(β) κάθε νέα συνάρτηση Python έχει docstring στα ελληνικά.", True), sm.docstring_rule(greek))
        check("a question about a docstring is not a rule", sm.docstring_rule(say("What does this docstring mean?")) is None)
        path = os.path.join(folder, "m.py")
        old = 'def kept():\n    return 1\n'
        with open(path, "w") as fh:
            fh.write(old + 'def added():\n    """Προσθέτει."""\n\ndef plain():\n    """Adds."""\n\nclass T:\n    def test_it(self):\n        pass\n')
        ok, summary, text = sm.with_docstring_note(greek, (True, "Saved m.py", "Saved m.py."), "m.py", path, old)
        check("new functions without a Greek docstring are named, test methods included, old ones left alone",
              "2 new functions without a docstring in Greek (plain, T.test_it)" in text and "kept" not in text.split("«")[-1], text)
        english = say("every function you add must have a docstring")
        check("in English the language is not asked", "1 new function without a docstring (T.test_it)" in
              sm.with_docstring_note(english, (True, "Saved", "Saved."), "m.py", path, old)[2])
        check("nothing is said without such a rule, or for a file that is not Python",
              sm.with_docstring_note(say("add a feature"), (True, "Saved", "Saved."), "m.py", path, old)[2] == "Saved."
              and sm.with_docstring_note(greek, (True, "Saved", "Saved."), "m.txt", path, old)[2] == "Saved.")
    finally:
        sm.APP = saved
    folder = os.path.join(projects, "rules")
    os.makedirs(folder)
    with open(os.path.join(folder, "README.md"), "w") as fh:
        fh.write("# Sample\n")
    ws = c.post("/api/workspace/open", {"path": folder})[1]["workspace"]["id"]
    sid = c.post("/api/agent/sessions", {"ws": ws})[1]["id"]
    before = len(c_get_json(gw_port, "/_mock/requests")["requests"])
    run_agent(c, ws, sid, "Rename Sample to Demo in the README", auto_apply=True, auto_commands=True)
    first = c_get_json(gw_port, "/_mock/requests")["requests"][before:]
    check("a chat's first run is not asked to check earlier requests", not any("before you end, check" in r["last_user"] for r in first))
    with open(os.path.join(folder, "README.md"), "w") as fh:
        fh.write("# Sample\n")
    before = len(c_get_json(gw_port, "/_mock/requests")["requests"])
    events = run_agent(c, ws, sid, "Do the same once more", auto_apply=True, auto_commands=True)[1]
    sent = c_get_json(gw_port, "/_mock/requests")["requests"][before:]
    asked = [r for r in sent if "before you end, check" in r["last_user"]]
    check("a later run that changed files is asked once, with the user's requests listed", len(asked) == 1
          and "1. Rename Sample to Demo in the README" in asked[0]["last_user"] and "2. Do the same once more" in asked[0]["last_user"], [r["last_user"][:80] for r in sent])
    check("and then ends as usual", events[-1]["type"] == "done" and events[-1].get("reason") == "finished", [(e["type"], e.get("name"), str(e.get("summary") or e.get("text") or "")[:60]) for e in events if e["type"] in ("tool_call", "tool_result", "status", "done")])


def test_repeat_and_stray(c, projects, gw_port):
    print("a rejected edit sent again, and words of other languages in Greek text")
    sys.path.insert(0, ROOT)
    import studio as sm
    folder = os.path.join(projects, "insist")
    os.makedirs(folder)
    with open(os.path.join(folder, "README.md"), "w") as fh:
        fh.write("# Sample\n")
    ws = c.post("/api/workspace/open", {"path": folder})[1]["workspace"]["id"]
    shown = []

    def reject(ev):
        shown.append(ev.get("new"))
        return "reject", ""
    events = run_agent(c, ws, c.post("/api/agent/sessions", {"ws": ws})[1]["id"], "Insist on the edit", on_approval=reject)[1]
    check("the same edit with a comment added is not shown to the user again", len(shown) == 1, len(shown))
    done = [e for e in events if e["type"] == "tool_result" and e.get("name") == "finish"]
    check("and the model is told to ask or change course", done and "did not show this change" in done[-1]["output"], done[-1]["output"] if done else events[-3:])
    check("the file is unchanged", open(os.path.join(folder, "README.md")).read() == "# Sample\n")
    held_back = [e for e in events if e["type"] == "guard_blocked"]
    check("holding the repeat back is recorded as a structured guard event", held_back and held_back[0].get("guard") == "repeat_check"
          and held_back[0].get("case") == "repeat_rejected" and held_back[0].get("lapse") is True, held_back)
    sys.path.insert(0, os.path.join(ROOT, "sandbox"))
    import guard_log
    logged = guard_log.lapses([], events)
    check("the benchmark counts it from the event, not from the note's words", logged["source"] == "events"
          and logged["caught"].get("repeat_rejected") == 1 and logged["by"].get("blocked") == 1, logged)
    check("a check declares whether it is about the task or the user's rules; old results by exact label",
          guard_log.check_kind({"label": "anything", "kind": "rule"}) == "rule" and guard_log.check_kind({"label": "changed nothing"}) == "rule"
          and guard_log.check_kind({"label": "nothing changed"}) == "task")
    for answer in ("reject", "accept"):
        sneak = os.path.join(projects, f"sneak-{answer}")
        os.makedirs(sneak)
        with open(os.path.join(sneak, "README.md"), "w") as fh:
            fh.write("# Sample\n")
        sws = c.post("/api/workspace/open", {"path": sneak})[1]["workspace"]["id"]
        cards = []

        def first_no(ev):
            cards.append(ev)
            return ("reject" if len(cards) == 1 else answer), ""
        events = run_agent(c, sws, c.post("/api/agent/sessions", {"ws": sws})[1]["id"], "Sneak the edit", auto_commands=True,
                           on_approval=first_no)[1]
        text = open(os.path.join(sneak, "README.md")).read()
        done = [e for e in events if e["type"] == "tool_result" and e.get("name") == "finish"]
        out = done[-1]["output"] if done else ""
        if answer == "reject":
            check("a command that makes a rejected change without naming the file (auto-run on) is caught: the user is asked again",
                  len(cards) == 2 and "without an edit card" in (cards[1].get("warning") or "") and "MINUTE = 60" in cards[1].get("new", ""),
                  [(e.get("kind"), e.get("warning")) for e in cards])
            check("rejected again, the file is put back as it was", text == "# Sample\n", text)
            check("and the model is told not to make it by any route", "by any route" in out, out[-200:])
        else:
            check("accepted the second time, the command's change stays, with an undo point",
                  text == "# Sample\nMINUTE = 60\n" and any(e["type"] == "file_changed" and e.get("checkpoint") for e in events), text)
    held = os.path.join(projects, "held-larger")
    os.makedirs(held)
    with open(os.path.join(held, "README.md"), "w") as fh:
        fh.write("# Sample\n")
    hws = c.post("/api/workspace/open", {"path": held})[1]["workspace"]["id"]
    held_cards = []

    def reject_all(ev):
        held_cards.append(ev)
        return "reject", ""
    events = run_agent(c, hws, c.post("/api/agent/sessions", {"ws": hws})[1]["id"], "Insist with its uses", on_approval=reject_all)[1]
    done = [e for e in events if e["type"] == "tool_result" and e.get("name") == "finish"]
    check("a larger edit holding a rejected change is not shown either, and the model is told to ask (the user's rule of 2026-11-28)",
          len(held_cards) == 1 and done and "holds the change" in done[-1]["output"] and open(os.path.join(held, "README.md")).read() == "# Sample\n",
          (len(held_cards), done[-1]["output"][:200] if done else events[-3:]))
    both = os.path.join(projects, "sneak-two")
    os.makedirs(both)
    for name, head in (("README.md", "# Sample\n"), ("NOTES.md", "# Notes\n")):
        with open(os.path.join(both, name), "w") as fh:
            fh.write(head)
    bws = c.post("/api/workspace/open", {"path": both})[1]["workspace"]["id"]
    cards = []

    def answer_two(ev):
        cards.append(ev)
        return ("accept" if ev.get("kind") == "command" else "reject"), ""
    run_agent(c, bws, c.post("/api/agent/sessions", {"ws": bws})[1]["id"], "Sneak past the named file", auto_commands=True,
              on_approval=answer_two)
    command = [e for e in cards if e.get("kind") == "command"]
    check("a command naming one file with a rejected change asks, with a warning about that file",
          len(command) == 1 and "NOTES.md" in (command[0].get("warning") or ""), [(e.get("kind"), e.get("warning")) for e in cards])
    check("and the other file it changes without naming it is still checked: the rejected change is shown again and put back",
          len(cards) == 4 and cards[3].get("path") == "README.md" and "without an edit card" in (cards[3].get("warning") or "")
          and open(os.path.join(both, "README.md")).read() == "# Sample\n", [(e.get("kind"), e.get("path")) for e in cards])
    saved, sm.APP = sm.APP, types.SimpleNamespace(cfg={"script_check": True})
    try:
        old = "import time\n\ndef parse_ts(x):\n    return x * 60\n"
        first = sm.edit_signature(old, "import time\nMINUTE = 60\n\ndef parse_ts(x):\n    return x * 60\n")
        run = types.SimpleNamespace(rejected=[("t.py", first)])
        check("moved one line lower with a comment, it is the same edit", sm.repeats_rejected(
            run, "t.py", sm.edit_signature(old, "import time\n\n# seconds in a minute\nMINUTE = 60\ndef parse_ts(x):\n    return x * 60\n")))
        check("renamed with an underscore, it is the same edit", sm.repeats_rejected(
            run, "t.py", sm.edit_signature(old, "import time\n_MINUTE = 60\n\ndef parse_ts(x):\n    return x * 60\n")))
        for why, text in (("another value", "import time\nMINUTE = 3600\n\ndef parse_ts(x):\n    return x * 60\n"),
                          ("another sign", "import time\nMINUTE = -60\n\ndef parse_ts(x):\n    return x * 60\n"),
                          ("another string", "import time\nMINUTE = '60'\n\ndef parse_ts(x):\n    return x * 60\n")):
            check(f"a similar edit with {why} is a different one", not sm.repeats_rejected(run, "t.py", sm.edit_signature(old, text)), why)
        guard = sm.edit_signature("def ok(n):\n    return True\n", "def ok(n):\n    if n < 5:\n        return False\n    return True\n")
        check("a condition with <= instead of < is a different edit", not sm.repeats_rejected(
            types.SimpleNamespace(rejected=[("g.py", guard)]), "g.py",
            sm.edit_signature("def ok(n):\n    return True\n", "def ok(n):\n    if n <= 5:\n        return False\n    return True\n")))
        check("a message with one word changed is a different edit", not sm.repeats_rejected(
            types.SimpleNamespace(rejected=[("m.py", sm.edit_signature("", 'print("Saved the file")\n'))]), "m.py",
            sm.edit_signature("", 'print("Saved the files")\n')))
        whole = sm.edit_signature(old, "import time\nMINUTE = 60\n\ndef parse_ts(x):\n    return x * MINUTE\n")
        check("the rejected constant with its uses added is not sent back, but the card says it holds the rejected change",
              not sm.repeats_rejected(run, "t.py", whole) and sm.holds_rejected(run, "t.py", whole)
              and not sm.holds_rejected(run, "t.py", sm.edit_signature(old, "import time\n\ndef parse_ts(x):\n    return x * 60.0\n")))
        check("a file name in bold code does not hide the prose after it from the mixed-word check (test 14, v3)",
              sm.mixed_words("3. **`db.py`** — οι αλλαγές μέσω ενόςSingle writer, με (`BadInput`, `NotFound`).") == ["ενόςSingle"])
        about = types.SimpleNamespace(rejected=[("tallybook/timeutil.py", sm.edit_signature("import time\n", "import time\nSECONDS_PER_MINUTE = 60\n"))])
        check("a message about the rejected change lets a larger edit holding it be shown; an unrelated one or a refusal does not",
              sm.about_rejected(about, "Yes, add SECONDS_PER_MINUTE and use it.") == {"tallybook/timeutil.py"}
              and sm.about_rejected(about, "Also fix the README typo.") == set()
              and sm.about_rejected(about, "No, don't add SECONDS_PER_MINUTE.") == set()
              and sm.about_rejected(about, "Όχι, άσε το timeutil.py") == set())
        for text in ("I checked timeutil.py. Don't add that constant.", "timeutil.py looks fine as it is.",
                     "Yes, but not SECONDS_PER_MINUTE: call it MINUTE.", "OK, use timeutil.py without the constant.",
                     "Κοίταξα το timeutil.py, δεν θέλω τη σταθερά.", "SECONDS_PER_MINUTE?", "timeutil.py looks fine as it is.",
                     "Keep timeutil.py as it is."):
            check(f"naming the rejected change without an explicit yes, or with a no, is not a yes: {text}", sm.about_rejected(about, text) == set())
        check("an explicit yes that names it is (English and Greek)",
              sm.about_rejected(about, "OK, go ahead with SECONDS_PER_MINUTE in timeutil.py.") == {"tallybook/timeutil.py"}
              and sm.about_rejected(about, "Ναι, βάλε το SECONDS_PER_MINUTE στο timeutil.py.") == {"tallybook/timeutil.py"})
        check("a request that something behaves exactly as now is checked even when it is the only one",
              sm.SAME_BEHAVIOUR_RE.search("Without it, the script behaves exactly as now.") and not sm.SAME_BEHAVIOUR_RE.search("Add a --dry-run option."))
        sm.APP.cfg["ask_tool"] = False
        check("asking with ask_user where it is off tells the model to ask in its reply",
              "write the question in your reply" in sm.execute_tool(types.SimpleNamespace(note_progress=lambda p: None), "ask_user", {})[2])
        check("a command naming two files with rejected changes names both", sm.names_rejected_files(
            types.SimpleNamespace(rejected=list(run.rejected) + [("b.py", first)]), "diff t.py b.py") == ["t.py", "b.py"])
        check("a command naming a file with a rejected change is found; another file is not",
              sm.names_rejected_file(run, "sed -i 's/60/MINUTE/' t.py") == "t.py" and sm.names_rejected_file(run, "cat notes.txt") is None
              and sm.names_rejected_file(run, "python3 test.py") is None)
        kept = types.SimpleNamespace(rejected=list(run.rejected) + [("other.py", whole)])
        sm.forget_rejected(kept, "t.py", whole)
        check("once the user accepts an edit holding the rejected change, commands naming that file no longer ask",
              sm.names_rejected_file(kept, "python3 -m py_compile t.py") is None and kept.rejected == [("other.py", whole)], kept.rejected)
        renamed = sm.edit_signature("import re\n\n_TS = 1\n", "import re\n\nSECONDS_IN_MINUTE = 60\n\n_TS = 1\n")
        per = types.SimpleNamespace(rejected=[("u.py", sm.edit_signature("_F = 1\n", "_F = 1\n\nSECONDS_PER_MINUTE = 60\n"))])
        check("the rejected constant renamed and put elsewhere is the same edit (SECONDS_IN_MINUTE, a Strata model's run)",
              sm.repeats_rejected(per, "u.py", renamed))
        check("another name for another thing is not (timeout = 60 after retries = 60)", not sm.repeats_rejected(
            types.SimpleNamespace(rejected=[("v.py", sm.edit_signature("", "retries = 60\n"))]), "v.py", sm.edit_signature("", "timeout = 60\n")))
        sm.APP.cfg["repeat_check"] = False
        try:
            check("without repeat_check a command naming the file asks as usual", sm.names_rejected_file(run, "cat t.py") is None)
        finally:
            sm.APP.cfg["repeat_check"] = True
        check("two lines that differ only in names have the same skeleton", sm.edit_skeleton("SECONDS=60") == sm.edit_skeleton("_SECONDS=60")
              and sm.edit_skeleton("x<5") != sm.edit_skeleton("x<=5"))
        check("using the constant as well is a different one", not sm.repeats_rejected(
            run, "t.py", sm.edit_signature(old, "import time\nMINUTE = 60\n\ndef parse_ts(x):\n    return x * MINUTE\n")))
        say = lambda *m: types.SimpleNamespace(session={"messages": [{"role": r, "content": t} for r, t in m]})
        run = say(("user", "Γράψε μια συνάρτηση που μετρά λέξεις σε ελληνικό κείμενο, με δοκιμές, και εξήγησέ μου τι έκανες."),
                  ("tool", "Tallybook"))
        english = sm.ENGLISH_DICT["words"]
        sm.ENGLISH_DICT["words"] = {"server", "hay"}
        text = ("Έγραψα τη συνάρτηση στο αρχείο words.py και πρόσθεσα δώδεκα δοκιμές, οι οποίες **Semua** περνούν. Οι δοκιμές "
                "που**All** περνούν δείχνουν ότι η στίξη δεν μετράει. Τρέχει με το unittest και το **Tallybook**, rồi, όλα καλά, "
                "και ο server δεν χρειάστηκε. Τα αποτελέσματα που_ALLs πέρασαν.")
        try:
            check("glued, emphasised or foreign-lettered words of other languages are found; English, tools and the project's names are not",
                  [w for _, w in sm.stray_latin(run, text)] == ["All", "ALLs", "Semua", "rồi"], sm.stray_latin(run, text))
            check("and can be removed tidily", sm.drop_stray_latin(run, text) == (
                "Έγραψα τη συνάρτηση στο αρχείο words.py και πρόσθεσα δώδεκα δοκιμές, οι οποίες περνούν. Οι δοκιμές που περνούν "
                "δείχνουν ότι η στίξη δεν μετράει. Τρέχει με το unittest και το **Tallybook**, όλα καλά, και ο server δεν χρειάστηκε. "
                "Τα αποτελέσματα που πέρασαν."), sm.drop_stray_latin(run, text))
            check("an English chat is left alone", sm.stray_latin(say(("user", "Write a word counter with tests and explain it")), text) == [])
            check("merging or discarding output writes no file; a redirect into a file does",
                  [sm.writes_files(x) for x in ("python3 -m unittest 2>&1 | tail -5", "grep x a 2>/dev/null", "ls > out.txt", "cat a >> b")]
                  == [False, False, True, True])
            greek_chat = say(("user", "Πρόσθεσε μια εντολή clients στο CLI και γράψε ελληνικά docstrings σε κάθε συνάρτηση."))
            check("an English reply in a Greek chat goes back",
                  "in English, but the user writes Greek" in sm.script_reasons(greek_chat, "I noticed an important detail: the existing rules for "
                                                                           "invoices already round each entry, so the totals stay consistent with the tests."))
            check("a short English aside between tool calls in a Greek chat gets a note",
                  "in English, but the user writes Greek" in sm.script_reasons(greek_chat, "The pattern is clear. I'll add a `ClientsCliTests` class to the tests.", aside=True))
            check("an aside with more English words than Greek ones gets a note too (v4, test 14)", all(
                "in English, but the user writes Greek" in sm.script_reasons(greek_chat, t, aside=True) for t in (
                    "Θα προσθέσω το `GET /api/entries/{id}` σε 5 βήματα: (1) new rule in `db.py`, (2) routing in `server.py`, (3) test, (4) README, (5) function execution.",
                    "Το test_clients.py χρειάζεται επιπλέον imports (json, subprocess, sys, ROOT):")))
            check("but an aside that is mostly Greek with a few names does not", "in English" not in sm.script_reasons(
                greek_chat, "Διαβάζω το `db.py` και το `server.py` για να δω πώς ορίζονται οι διαδρομές των πελατών.", aside=True))
            check("but not a Greek aside with terms",
                  "in English" not in sm.script_reasons(greek_chat, "Τρέχω τις δοκιμές με το pytest και το unittest.", aside=True))
            check("a Greek reply with a few English terms does not", "in English" not in sm.script_reasons(
                greek_chat, "Πρόσθεσα την εντολή clients στο CLI και έτρεξα όλες τις δοκιμές με το pytest· περνούν και οι σαράντα, μαζί με τις τρεις νέες."))
            check("a mixed word nothing can repair leaves text that came with tool calls",
                  sm.drop_mixed("Οι δοκιμές περνούν. Τnow γράφω το `Τnow` README:") == "Οι δοκιμές περνούν. γράφω το `Τnow` README:",
                  sm.drop_mixed("Οι δοκιμές περνούν. Τnow γράφω το `Τnow` README:"))
        finally:
            sm.ENGLISH_DICT["words"] = english
        sm.APP.cfg.update(context_fit=True, smart_trim=True)
        seen, asked = [], []
        fake = types.SimpleNamespace(model="tiny:ctx8k", session={"ctx_ratio": 1.0}, quiet=False, emit=lambda kind, **kw: seen.append((kind, kw)))
        reads = [m for n in range(8) for m in (
            {"role": "assistant", "content": "", "tool_calls": [{"id": f"c{n}", "type": "function", "function": {"name": "read_file", "arguments": json.dumps({"path": f"f{n}.py"})}}]},
            {"role": "tool", "tool_call_id": f"c{n}", "content": f"f{n}.py\n" + "x = 1\n" * 700})]
        history = [{"role": "user", "content": "Read every file."}] + reads
        sm.fit_context(fake, history, ctx=8192, fixed=4000)
        check("near a small model's limit the older tool output is shortened, the newest kept",
              history[2]["content"].startswith("[older") and history[-1]["content"].endswith("x = 1\n") and not any(k == "compacted" for k, _ in seen),
              [m["content"][:30] for m in history if m["role"] == "tool"])
        calls = [{"role": "assistant", "content": "", "tool_calls": [{"id": f"w{n}", "type": "function", "function": {
            "name": name, "arguments": json.dumps({key: "Ένα μεγάλο κείμενο. " * 100})}}]}
            for n, (name, key) in enumerate([("write_file", "content"), ("finish", "summary"), ("run", "command")])]
        sm.trim_old_writes(calls, 0)
        check("older writes lose the text the file holds, but never a finish summary or a command",
              "trimmed to save room" in calls[0]["tool_calls"][0]["function"]["arguments"]
              and not any("trimmed" in m["tool_calls"][0]["function"]["arguments"] for m in calls[1:]))
        real_call = sm.call_model
        sm.call_model = lambda run, messages: asked.append(messages[-1]["content"]) or {"content": "Summary: the user asked to read every file; eight were read.", "tool_calls": []}
        real_prompt, sm.build_system_prompt = sm.build_system_prompt, (lambda run: "system")
        try:
            history = [{"role": "user", "content": "Read every file. " + "Μεγάλο μήνυμα. " * 1200}] + reads
            sm.fit_context(fake, history, ctx=8192, fixed=4000)
        finally:
            sm.call_model, sm.build_system_prompt = real_call, real_prompt
        check("when shortening is not enough the model summarizes and the newest step stays",
              asked and asked[0].startswith("Studio note: the conversation is getting too long") and history[0]["content"].startswith(sm.COMPACT_INTRO)
              and "The user's latest message" in history[0]["content"] and history[1]["role"] == "assistant" and history[-1]["role"] == "tool"
              and any(k == "compacted" for k, _ in seen), [(m["role"], str(m.get("content"))[:40]) for m in history])
        rule = "Each task has an id that is never reused, even after deletion."
        fake2 = types.SimpleNamespace(model="tiny:ctx8k", quiet=False, emit=lambda kind, **kw: seen.append((kind, kw)),
                                      session={"ctx_ratio": 1.0, "messages": [{"role": "user", "content": "Make todo.py. " + rule},
                                                                              {"role": "user", "content": "Add delete ID."}]})
        sm.call_model = lambda run, messages: {"content": "Summary: todo.py was made; ids are never reused. Delete is next.", "tool_calls": []}
        sm.build_system_prompt = lambda run: "system"
        try:
            history = ([{"role": "user", "content": "Make todo.py. " + rule}] + reads[:6]
                       + [{"role": "user", "content": "Add delete ID."}] + reads[6:])
            sm.compact_in_run(fake2, history, 8192, 9999)
        finally:
            sm.call_model, sm.build_system_prompt = real_call, real_prompt
        check("after a summary the reminders still list every request, the replaced ones first",
              sm.user_requests(fake2)[:2] == ["Make todo.py. " + rule, "Add delete ID."], sm.user_requests(fake2))
        check("after a summary the user's earlier requests are kept word for word, the latest only once",
              rule in history[0]["content"] and "word for word" in history[0]["content"]
              and sum(m["content"].count("Add delete ID.") for m in history if isinstance(m.get("content"), str)) == 1,
              history[0]["content"][-400:])
        check("Ollama's num_ctx is read from /api/show", sm.num_ctx_of({"parameters": "stop <x>\nnum_ctx 32768\ntemperature 0.7"}) == 32768
              and sm.num_ctx_of({}) is None)
    finally:
        sm.APP = saved


def test_all(c, tmp, sample, outside, gw_port, cfg_path, st_port):
    print("static files")
    s, body, r = c.get("/", cookie=False)
    check("GET / is public (index or 404 if web/ missing)", s in (200, 404))
    if s == 200:
        check("index served as HTML with no-cache", "text/html" in r.getheader("Content-Type")
              and r.getheader("Cache-Control") == "no-cache")
    s, body, r = c.get("/assets/eidoloom-mark-original.png", cookie=False)
    check("nested web assets are public and keep their image type", s == 200 and body.startswith(b"\x89PNG")
          and r.getheader("Content-Type") == "image/png")
    s, _, _ = c.get("/web/../studio.py", cookie=False)
    check("static path traversal blocked", s == 404)
    s, _, _ = c.get("/web/%2e%2e/studio.py", cookie=False)
    check("encoded static traversal blocked", s == 404)
    s, _, _ = c.get("/assets/%2e%2e/studio.py", cookie=False)
    check("encoded asset traversal blocked", s == 404)
    s, _, _ = c.get("/data/users.json", cookie=False)
    check("data/ is never served", s in (401, 404))

    print("auth")
    s, d, _ = c.get("/api/auth/state", cookie=False)
    check("state: setup needed, not logged in", s == 200 and d["setup_needed"] is True and d["logged_in"] is False and d["user"] is None, d)
    s, d, _ = c.get("/api/config", cookie=False)
    check("protected endpoint -> 401 without login", s == 401)
    s, d, _ = c.post("/api/auth/setup", {"password": "short"})
    check("setup rejects short password", s == 400)
    s, d, _ = c.post("/api/auth/setup", {"password": "correct horse"}, csrf=False)
    check("setup without X-Studio -> 403", s == 403)
    s, d, r = c.post("/api/auth/setup", {"password": "correct horse"})
    cookie_hdr = r.getheader("Set-Cookie") or ""
    check("setup ok and sets cookie", s == 200 and c.cookie, (s, d))
    check("cookie flags", all(f in cookie_hdr for f in ("HttpOnly", "SameSite=Strict", "Path=/")), cookie_hdr)
    s, d, _ = c.post("/api/auth/setup", {"password": "another pass"})
    check("setup only once", s == 403)
    s, d, _ = c.get("/api/auth/state")
    check("state: logged in", d["setup_needed"] is False and d["logged_in"] is True and d["user"]["role"] == "admin", d)
    users = json.load(open(os.path.join(tmp, "app", "data", "users.json")))
    check("password stored as PBKDF2 200k", users["users"]["admin"]["iterations"] == 200000 and "correct" not in json.dumps(users))
    s, _, _ = c.post("/api/auth/logout")
    check("logout clears session", s == 200 and c.get("/api/config")[0] == 401)
    s, _, _ = c.post("/api/auth/login", {"password": "wrong password"})
    check("wrong password -> 401", s == 401)
    s, _, _ = c.post("/api/auth/login", {"password": "correct horse"})
    check("login ok", s == 200 and c.get("/api/config")[0] == 200)

    print("csrf")
    s, _, _ = c.request("POST", "/api/workspace/open", {"path": sample}, csrf=False)
    check("POST without X-Studio -> 403", s == 403)
    s, _, _ = c.post("/api/workspace/open", {"path": sample}, headers={"Origin": "http://evil.example"})
    check("foreign Origin -> 403", s == 403)
    s, _, _ = c.post("/api/workspace/open", {"path": sample},
                     headers={"Origin": f"http://127.0.0.1:{st_port}"})
    check("same Origin accepted", s == 200)

    print("config + models")
    s, d, _ = c.get("/api/config")
    check("config has gateway_ok true", s == 200 and d["gateway_ok"] is True and d["max_turns"] == 10, d)
    s, d, _ = c.get("/api/models")
    names = [m["name"] for m in d["models"]]
    labels = {m["name"]: m["label"] for m in d["models"]}
    check("model order: Strata first, Ollama A-Z",
          names == ["quartz-sub2:strata", "quartz:strata", "qwen3.8-flash-next:125b-strata",
                    "llama3.2:3b", "qwen2.5-coder:3b"], names)
    check("labels", labels.get("quartz:strata") == "Quartz (Strata)"
          and labels.get("quartz-sub2:strata") == "Quartz (Strata) + 2 GB subagent pool"
          and labels.get("qwen3.8-flash-next:125b-strata") == "Strata default profile", labels)
    by = {m["name"]: m for m in d["models"]}
    check("hidden: -full/-sub4 and long profile", not any("-full" in n or "-sub4" in n or "swift" in n for n in names))
    check("tools flags", by["quartz:strata"]["tools"] is True and by["llama3.2:3b"]["tools"] is False
          and by["qwen2.5-coder:3b"]["tools"] is True and by["quartz:strata"]["kind"] == "strata"
          and by["llama3.2:3b"]["kind"] == "ollama")

    print("workspaces")
    projects = os.path.dirname(sample)
    s, d, _ = c.get("/api/workspaces/browse?path=")
    check("browse roots", d["roots"] == [projects] and d["path"] == "" and d["dirs"][0]["path"] == projects, d)
    s, d, _ = c.get(f"/api/workspaces/browse?path={projects}")
    check("browse root lists dirs", [x["name"] for x in d["dirs"]] == ["sample"] and d["parent"] == "", d)
    s, d, _ = c.get(f"/api/workspaces/browse?path={sample}")
    check("browse subfolder (symlinks leaving root hidden)", s == 200 and d["parent"] == projects
          and [x["name"] for x in d["dirs"]] == ["node_modules", "src"], d)
    s, _, _ = c.get(f"/api/workspaces/browse?path={outside}")
    check("browse outside roots -> 403", s == 403)
    s, d, _ = c.post("/api/workspaces/mkdir", {"parent": projects, "name": "  Νέο project  "})
    made = os.path.join(os.path.realpath(projects), "Νέο project")
    check("mkdir creates a folder, trimmed, any language", s == 200 and d["path"] == made and os.path.isdir(made), d)
    check("mkdir refuses an existing name", c.post("/api/workspaces/mkdir", {"parent": projects, "name": "Νέο project"})[0] == 409)
    for bad in ("", "   ", ".", "..", ".hidden", "a/b", "../x", "a\\b", "x" * 300, 5, None):
        check(f"mkdir refuses name {str(bad)[:12]!r}", c.post("/api/workspaces/mkdir", {"parent": projects, "name": bad})[0] == 400)
    check("mkdir refuses outside the roots", c.post("/api/workspaces/mkdir", {"parent": outside, "name": "x"})[0] == 403 and not os.path.exists(os.path.join(outside, "x")))
    check("mkdir refuses a symlink out of the roots", (os.symlink(outside, os.path.join(made, "out")), c.post("/api/workspaces/mkdir", {"parent": os.path.join(made, "out"), "name": "x"})[0])[1] == 403)
    check("mkdir needs a parent", c.post("/api/workspaces/mkdir", {"name": "x"})[0] == 400)
    check("mkdir needs the CSRF header", c.post("/api/workspaces/mkdir", {"parent": projects, "name": "y"}, csrf=False)[0] == 403)
    shutil.rmtree(made)
    s, _, _ = c.get(f"/api/workspaces/browse?path={sample}/escape")
    check("browse symlink escape -> 403", s == 403)
    s, _, _ = c.post("/api/workspace/open", {"path": outside})
    check("open outside roots -> 403", s == 403)
    s, d, _ = c.post("/api/workspace/open", {"path": sample})
    ws = d["workspace"]["id"]
    check("open returns id/path/name", len(ws) == 12 and d["workspace"]["name"] == "sample"
          and d["workspace"]["path"] == sample, d)
    s, d, _ = c.get("/api/workspace/recent")
    check("recent lists workspace", [w["id"] for w in d["workspaces"]] == [ws])
    s, _, _ = c.get("/api/files/tree?ws=deadbeef0000&path=")
    check("unknown workspace -> 404", s == 404)

    print("files")
    s, d, _ = c.get(f"/api/files/tree?ws={ws}&path=")
    entries = {e["name"]: e for e in d["entries"]}
    order = [e["name"] for e in d["entries"]]
    check("tree hides node_modules", "node_modules" not in entries and "src" in entries and "README.md" in entries, order)
    check("tree sorts dirs before files", order.index("src") < order.index("README.md"), order)
    s, d, _ = c.get(f"/api/files/tree?ws={ws}&path=&all=1")
    check("tree all=1 shows node_modules", "node_modules" in [e["name"] for e in d["entries"]])
    s, d, _ = c.get(f"/api/files/tree?ws={ws}&path=src")
    check("tree subfolder", [e["path"] for e in d["entries"]] == ["src/main.py"], d)
    s, d, _ = c.get(f"/api/files/list?ws={ws}")
    check("list: relative paths, ignored dirs skipped", "src/main.py" in d["files"] and "README.md" in d["files"]
          and not any(f.startswith("node_modules") for f in d["files"]), d)
    s, d, _ = c.get(f"/api/files/read?ws={ws}&path=README.md")
    mtime = d["mtime"]
    check("read text", d["content"].startswith("# Sample") and d["binary"] is False and isinstance(mtime, float), d)
    s, d, _ = c.get(f"/api/files/read?ws={ws}&path=blob.bin")
    check("read binary flagged", d["binary"] is True)
    s, d, _ = c.post("/api/files/write", {"ws": ws, "path": "notes.txt", "content": "one\n"})
    m1 = d["mtime"]
    check("write new file", s == 200 and open(os.path.join(sample, "notes.txt")).read() == "one\n")
    s, d, _ = c.post("/api/files/write", {"ws": ws, "path": "notes.txt", "content": "two\n", "expected_mtime": m1})
    check("write with matching expected_mtime", s == 200 and d["mtime"] >= m1)
    with open(os.path.join(sample, "notes.txt"), "w") as fh:
        fh.write("external\n")
    os.utime(os.path.join(sample, "notes.txt"), (m1 + 50, m1 + 50))
    s, d, _ = c.post("/api/files/write", {"ws": ws, "path": "notes.txt", "content": "three\n", "expected_mtime": m1})
    check("stale mtime -> 409 with mtime", s == 409 and "mtime" in d and d["error"], (s, d))
    check("409 left file untouched", open(os.path.join(sample, "notes.txt")).read() == "external\n")
    s, d, _ = c.post("/api/files/write", {"ws": ws, "path": "notes.txt", "content": "three\n"})
    check("write without expected_mtime overwrites", s == 200)
    s, _, _ = c.post("/api/files/create", {"ws": ws, "path": "newdir/sub", "type": "dir"})
    check("create dir", s == 200 and os.path.isdir(os.path.join(sample, "newdir", "sub")))
    s, _, _ = c.post("/api/files/create", {"ws": ws, "path": "newdir/a.txt", "type": "file"})
    check("create file", s == 200 and os.path.isfile(os.path.join(sample, "newdir", "a.txt")))
    s, _, _ = c.post("/api/files/create", {"ws": ws, "path": "newdir/a.txt", "type": "file"})
    check("create existing -> 409", s == 409)
    s, d, _ = c.post("/api/files/rename", {"ws": ws, "from": "newdir/a.txt", "to": "newdir/b.txt"})
    check("rename", s == 200 and os.path.isfile(os.path.join(sample, "newdir", "b.txt")))
    s, _, _ = c.post("/api/files/rename", {"ws": ws, "from": "newdir/b.txt", "to": "notes.txt"})
    check("rename onto existing -> 409", s == 409)
    s, d, _ = c.post("/api/files/delete", {"ws": ws, "path": "newdir/b.txt"})
    trash = os.path.join(sample, d.get("trash", "x"))
    check("delete moves to trash", s == 200 and not os.path.exists(os.path.join(sample, "newdir", "b.txt"))
          and os.path.isfile(trash) and ".strata-studio/trash/" in d["trash"], d)
    s, d, _ = c.get(f"/api/files/tree?ws={ws}&path=")
    check("trash hidden from tree", ".strata-studio" not in [e["name"] for e in d["entries"]])
    s, _, _ = c.post("/api/files/delete", {"ws": ws, "path": ""})
    check("cannot delete workspace root", s == 400)

    print("path confinement")
    secret = os.path.join(outside, "secret.txt")
    bad_reads = ["../outside/secret.txt", "/etc/passwd", "a/../../outside/secret.txt", "escape/secret.txt",
                 "leak.txt", "..", "src/../../x", "%2e%2e/outside"]
    for p in bad_reads:
        s, _, _ = c.get(f"/api/files/read?ws={ws}&path={p}")
        check(f"read {p!r} rejected", s in (400, 403, 404) and s != 200, s)
    s, _, _ = c.get(f"/api/files/tree?ws={ws}&path=escape")
    check("tree through symlink dir rejected", s == 403)
    s, _, _ = c.get(f"/api/files/tree?ws={ws}&path=/etc")
    check("tree absolute rejected", s == 403)
    for body, label in [({"path": "../evil.txt", "content": "x"}, "write ../"),
                        ({"path": "/tmp/evil.txt", "content": "x"}, "write absolute"),
                        ({"path": "escape/evil.txt", "content": "x"}, "write via symlink dir"),
                        ({"path": "leak.txt", "content": "pwned"}, "write via symlink file")]:
        s, _, _ = c.post("/api/files/write", dict(body, ws=ws))
        check(f"{label} rejected", s == 403, s)
    check("outside files untouched", open(secret).read() == "TOP SECRET" and not os.path.exists(os.path.join(outside, "evil.txt")))
    s, _, _ = c.post("/api/files/create", {"ws": ws, "path": "escape/x", "type": "file"})
    check("create via symlink dir rejected", s == 403)
    s, _, _ = c.post("/api/files/rename", {"ws": ws, "from": "notes.txt", "to": "../moved.txt"})
    check("rename to outside rejected", s == 403)
    s, _, _ = c.post("/api/files/rename", {"ws": ws, "from": "../outside/secret.txt", "to": "stolen.txt"})
    check("rename from outside rejected", s == 403)
    s, _, _ = c.post("/api/files/delete", {"ws": ws, "path": "escape/secret.txt"})
    check("delete through symlink dir rejected", s == 403 and os.path.exists(secret))
    s, d, _ = c.post("/api/files/delete", {"ws": ws, "path": "link2"})
    check("delete of a symlink moves only the link", s == 200 and os.path.exists(secret)
          and not os.path.lexists(os.path.join(sample, "link2")), (s, d))
    s, _, _ = c.get(f"/api/files/search?ws={ws}&q=SECRET")
    check("search does not follow symlinks out", s == 200 and not c.get(f"/api/files/search?ws={ws}&q=SECRET")[1]["matches"])

    print("search")
    s, d, _ = c.get(f"/api/files/search?ws={ws}&q=sample")
    paths = {m["path"] for m in d["matches"]}
    check("search literal, case-insensitive, skips ignored + binary",
          paths == {"README.md"} and d["matches"][0]["line"] == 1 and d["truncated"] is False, d)
    s, d, _ = c.get(f"/api/files/search?ws={ws}&q=sample&case=1")
    check("search case-sensitive", d["matches"] == [], d)
    s, d, _ = c.get(f"/api/files/search?ws={ws}&q=def%20\\w%2B&regex=1")
    check("search regex", [m["path"] for m in d["matches"]] == ["src/main.py"], d)
    s, _, _ = c.get(f"/api/files/search?ws={ws}&q=(&regex=1")
    check("bad regex -> 400", s == 400)

    print("terminal")
    s, d, _ = c.post("/api/term/open", {"ws": ws, "cols": 100, "rows": 30})
    tid = d["id"]
    check("term open", s == 200 and tid)
    seen = b""
    got = False
    stream = c.sse(f"/api/term/stream?id={tid}")
    c.post("/api/term/input", {"id": tid, "data": "echo hi-$((20+22)); pwd; stty size\n"})
    t0 = time.time()
    for name, ev in stream:
        if name == "data":
            seen += base64.b64decode(ev["b64"])
        if b"hi-42" in seen and b"30 100" in seen and sample.encode() in seen:
            got = True
            break
        if time.time() - t0 > 20:
            break
    check("terminal runs command, cwd=workspace, initial size applied", got, seen[-300:])
    s, _, _ = c.post("/api/term/resize", {"id": tid, "cols": 120, "rows": 40})
    check("term resize", s == 200)
    replay = b""
    t0 = time.time()
    for name, ev in c.sse(f"/api/term/stream?id={tid}"):
        if name == "data":
            replay += base64.b64decode(ev["b64"])
        if b"hi-42" in replay or time.time() - t0 > 10:
            break
    check("new stream replays scrollback", b"hi-42" in replay)
    exit_seen = False
    stream2 = c.sse(f"/api/term/stream?id={tid}")
    next(stream2)
    c.post("/api/term/close", {"id": tid})
    for name, ev in stream2:
        if name == "exit":
            exit_seen = True
            break
    check("close ends the stream with exit event", exit_seen)
    s, _, _ = c.post("/api/term/input", {"id": tid, "data": "x"})
    check("closed terminal -> 404", s == 404)

    print("agent: sessions")
    s, d, _ = c.post("/api/agent/sessions", {"ws": ws})
    sess = d["id"]
    check("create session", s == 200 and sess)
    readme = os.path.join(sample, "README.md")
    original = open(readme).read()

    print("agent: manual approvals")
    seen_approvals = []

    def accept_all(ev):
        seen_approvals.append(ev)
        return "accept", ""
    run1, ev1 = run_agent(c, ws, sess, "Rename the word Sample in the README", on_approval=accept_all,
                          context={"path": "README.md", "selection": {"start_line": 1, "end_line": 1, "text": "# Sample Project"}})
    k = kinds(ev1)
    check("events: status, thinking, token, tool_call, tool_result, usage", all(
        x in k for x in ("status", "thinking", "token", "tool_call", "tool_result", "usage", "approval",
                         "approval_resolved", "file_changed", "done")), sorted(set(k)))
    check("seq strictly increasing from 1", [e["seq"] for e in ev1] == list(range(1, len(ev1) + 1)))
    calls = [e["name"] for e in ev1 if e["type"] == "tool_call"]
    check("tool call order (fragments assembled)", calls == ["list_files", "read_file", "replace_in_file", "run", "finish"], calls)
    check("two approvals: edit then command", [a["kind"] for a in seen_approvals] == ["edit", "command"])
    edit = seen_approvals[0]
    check("edit approval carries path, full old and new",
          edit["path"] == "README.md" and "Sample" in edit["old"] and "Demo" in edit["new"] and "Sample" not in edit["new"])
    check("command approval carries command", seen_approvals[1]["command"] == "echo ok")
    done = ev1[-1]
    check("done reason finished with summary", done["type"] == "done" and done["reason"] == "finished"
          and done["turns"] == 5 and "Renamed" in done.get("summary", ""), done)
    check("README changed", open(readme).read() == original.replace("Sample", "Demo"))
    results = {e["name"]: e for e in ev1 if e["type"] == "tool_result"}
    check("tool_result shapes", results["run"]["ok"] is True and "ok" in results["run"]["output"]
          and "README.md" in results["list_files"]["output"] and "Sample" in results["read_file"]["output"]
          and results["replace_in_file"]["output"] == "applied")
    fc = [e for e in ev1 if e["type"] == "file_changed"]
    check("file_changed with checkpoint", len(fc) == 1 and fc[0]["path"] == "README.md" and fc[0]["checkpoint"])
    usage = [e for e in ev1 if e["type"] == "usage"][0]
    check("usage event", usage["max_turns"] == 10 and usage["turn"] == 1 and usage["prompt_tokens"] > 0)
    check("no active run after done", c.get(f"/api/agent/active?ws={ws}")[1].get("run") is None)
    s, d, _ = c.post("/api/agent/undo", {"ws": ws, "checkpoint": fc[0]["checkpoint"]})
    check("undo restores README", s == 200 and d["restored"] == ["README.md"] and open(readme).read() == original, d)
    s, _, _ = c.post("/api/agent/undo", {"ws": ws, "checkpoint": fc[0]["checkpoint"]})
    check("second undo of same checkpoint -> 404", s == 404)

    mock = lambda: c_get_json(gw_port, "/_mock/requests")["requests"]
    reqs = mock()
    check("gateway got X-Application-Name and streaming, max_tokens 8192",
          all(r["app"] == "Strata Studio" for r in reqs) and reqs[0]["stream"] and reqs[0]["max_tokens"] == 8192, reqs[0])
    sysprompt = reqs[0]["system"]
    check("system prompt: rules, tree, open file/selection",
          "expert software engineer" in sysprompt and "README.md" in sysprompt and "src/" in sysprompt
          and "node_modules" not in sysprompt and "# Sample Project" in sysprompt)

    print("agent: persistence, reattach, replay")
    s, d, _ = c.get(f"/api/agent/session?id={sess}")
    types = kinds(d["events"])
    check("session persists events incl. user message, tokens merged",
          types[0] == "user" and "done" == types[-1] and "approval" in types
          and all(not (a == b and a in ("token", "thinking")) for a, b in zip(types, types[1:])), types)
    check("session has model", d["model"] == "quartz:strata")
    s, d, _ = c.get(f"/api/agent/sessions?ws={ws}")
    check("session list: title is first 60 chars of first message",
          d["sessions"][0]["title"] == "Rename the word Sample in the README" and d["sessions"][0]["id"] == sess, d)
    replay = [e for _, e in c.sse(f"/api/agent/stream?run={run1}&after=0")]
    check("finished run replays fully", [e["seq"] for e in replay] == [e["seq"] for e in ev1])
    cut = ev1[-4]["seq"]
    tail = [e["seq"] for _, e in c.sse(f"/api/agent/stream?run={run1}&after={cut}")]
    check("replay with after=<seq>", tail == [e["seq"] for e in ev1 if e["seq"] > cut], tail)

    print("agent: reject + stop + 409")
    notes = []

    def reject_edit(ev):
        notes.append(ev["kind"])
        return ("reject", "not now") if ev["kind"] == "edit" else ("accept", "")
    run2, ev2 = run_agent(c, ws, sess, "try again", on_approval=reject_edit)
    rr = [e for e in ev2 if e["type"] == "tool_result" and e["name"] == "replace_in_file"][0]
    check("rejected edit reported to the model", rr["ok"] is False and "rejected" in rr["output"] and "not now" in rr["output"], rr)
    check("rejected edit did not write", open(readme).read() == original and not [e for e in ev2 if e["type"] == "file_changed"])
    check("rejected edit still reaches done", ev2[-1]["type"] == "done")

    s, d, _ = c.post("/api/agent/send", {"session": sess, "ws": ws, "message": "again", "model": "quartz:strata"})
    run3 = d["run"]
    check("send while chatting continues the session (run id)", s == 200)
    s2, d2, _ = c.post("/api/agent/send", {"session": sess, "ws": ws, "message": "second", "model": "quartz:strata"})
    check("second send in same workspace -> 409", s2 == 409, d2)
    check("active run reported for reattach", c.get(f"/api/agent/active?ws={ws}")[1].get("run") == run3 and c.get(f"/api/agent/active?ws={ws}")[1].get("session") == sess)
    stopped_events = []
    for name, ev in c.sse(f"/api/agent/stream?run={run3}"):
        stopped_events.append(ev)
        if name == "approval":
            c.post("/api/agent/stop", {"run": run3})
    ap = [e for e in stopped_events if e["type"] == "approval_resolved"]
    check("stop turns pending approval into a rejection and ends the run",
          ap and ap[-1]["decision"] == "reject" and stopped_events[-1]["type"] == "done"
          and stopped_events[-1]["reason"] == "stopped", [e for e in stopped_events][-3:])
    check("stopped run did not change the file", open(readme).read() == original)
    check("workspace free again after stop", c.get(f"/api/agent/active?ws={ws}")[1].get("run") is None)
    s, d, _ = c.post("/api/agent/send", {"session": sess, "ws": ws, "message": "again", "model": "quartz:strata"})
    live_run, live_events, switched = d["run"], [], False
    for name, ev in c.sse(f"/api/agent/stream?run={live_run}"):
        live_events.append(ev)
        if name == "approval" and not switched:
            switched = True
            s_other, _, _ = c.post("/api/agent/settings", {"run": live_run, "auto_commands": ev.get("kind") != "command", "auto_apply": ev.get("kind") != "edit"})
            still_pending = not [e for e in live_events if e["type"] == "approval_resolved"]
            s_on, _, _ = c.post("/api/agent/settings", {"run": live_run, "auto_apply": True, "auto_commands": True})
    resolved = [e for e in live_events if e["type"] == "approval_resolved"]
    check("settings for the other kind leaves the approval pending", switched and s_other == 200 and still_pending)
    check("switching auto on mid-run accepts the pending approval", s_on == 200 and resolved and resolved[0]["decision"] == "accept", resolved)
    check("later approvals in that run do not wait", all(e["decision"] == "accept" for e in resolved) and live_events[-1]["type"] == "done", live_events[-2:])
    with open(readme, "w") as fh:
        fh.write(original)
    s, _, _ = c.post("/api/agent/settings", {"run": "ffffffff", "auto_apply": True})
    check("settings for unknown run -> 404", s == 404)

    docker_log = os.environ["STUDIO_TEST_DOCKER_LOG"]
    docker_calls = lambda: open(docker_log).read() if os.path.exists(docker_log) else ""
    run_output = lambda events: [e for e in events if e["type"] == "tool_result" and e["name"] == "run"][-1]["output"]
    ws_info = [w for w in c.get("/api/workspace/recent")[1]["workspaces"] if w["id"] == ws][0]
    check("real folder is the default", ws_info["sandbox"] is False, ws_info)
    _, ev_host = run_agent(c, ws, sess, "again", auto_apply=True, auto_commands=True)
    check("sandbox off: commands run on the host", "ok" in run_output(ev_host) and docker_calls() == "", docker_calls())
    s, _, _ = c.post("/api/workspace/sandbox", {"ws": ws, "enabled": "yes"})
    check("sandbox needs a boolean", s == 400)
    s, d, _ = c.post("/api/workspace/sandbox", {"ws": ws, "enabled": True})
    check("sandbox on is saved", s == 200 and d["workspace"]["sandbox"] is True, d)
    started = docker_calls()
    check("container is locked down, only this folder mounted", all(x in started for x in (
        "--cap-drop ALL", "no-new-privileges", f"-v {ws_info['path']}:/work ", f"network disconnect strata-sandbox strata-sandbox-{ws}")), started)
    s, d, _ = c.post("/api/workspace/open", {"path": ws_info["path"]})
    check("preference survives reopening the folder", d["workspace"]["sandbox"] is True, d)
    with open(readme, "w") as fh:
        fh.write(original)
    _, ev_box = run_agent(c, ws, sess, "again", auto_apply=True, auto_commands=True)
    check("sandbox on: commands run in the container", "IN-SANDBOX" in run_output(ev_box), run_output(ev_box))
    check("sandbox on: edits still change the real folder", open(readme).read() != original)
    with open(readme, "w") as fh:
        fh.write(original)
    s, d, _ = c.post("/api/term/open", {"ws": ws, "cols": 80, "rows": 24})
    check("sandbox on: terminals open in the container", s == 200 and f"exec -it -e TERM=xterm-256color -w /work strata-sandbox-{ws} /bin/bash -l" in docker_calls(), docker_calls()[-300:])
    blocked = []
    def toggle_during_run(ev):
        blocked.append(c.post("/api/workspace/sandbox", {"ws": ws, "enabled": False})[0])
        return "reject", ""
    run_agent(c, ws, sess, "again", on_approval=toggle_during_run)
    check("sandbox cannot change during a run", blocked and blocked[0] == 409, blocked)
    for bad in ({"network": "maybe"}, {"image": "bad image; rm"}, {"ports": ["x"]}, {"ports": [70000]}, {"ports": [True]}):
        check(f"invalid sandbox setting {bad} -> 400", c.post("/api/workspace/sandbox", {"ws": ws, **bad})[0] == 400)
    os.remove(docker_log)
    s, d, _ = c.post("/api/workspace/sandbox", {"ws": ws, "image": "node:22", "network": "on", "ports": [5173, 3000, 3000]})
    created = docker_calls()
    check("settings are saved", s == 200 and (d["workspace"]["sandbox_image"], d["workspace"]["sandbox_network"], d["workspace"]["sandbox_ports"]) == ("node:22", "on", [3000, 5173]), d)
    check("settings recreate the container", created.startswith(f"rm -f strata-sandbox-{ws}") and all(x in created for x in (
        "--network strata-sandbox", "-p 127.0.0.1:3000:3000", "-p 127.0.0.1:5173:5173", " node:22 infinity")), created)
    check("network always: nothing is disconnected", "network disconnect" not in created)
    os.remove(docker_log)
    s, d, _ = c.post("/api/workspace/sandbox", {"ws": ws, "image": "", "network": "install", "ports": [3000]})
    created = docker_calls()
    check("clearing the image restores the default", d["workspace"]["sandbox_image"] == "strata-sandbox:universal" and "strata-sandbox:universal infinity" in created, d)
    check("install mode starts offline, ports need always", f"network disconnect strata-sandbox strata-sandbox-{ws}" in created and " -p " not in created, created)
    os.remove(docker_log)
    sys.path.insert(0, ROOT)
    import studio as studio_module
    install = studio_module.INSTALL_RE.search
    for cmd in ("npm install", "npm ci", "pnpm add x", "cd a && pip install -r r.txt", "python -m pip install x", ".venv/bin/pip install y", "cargo build",
                "go mod download", "./gradlew test", "mvn package", "bundle install", "composer install", "dotnet restore", "uv pip install z"):
        check(f"install command gets internet: {cmd}", install(cmd) is not None)
    for cmd in ("npm test", "npm run build", "python app.py", "curl example.com", "git pull", "go test ./...", "cargo test", "echo pip", "ls install"):
        check(f"other command stays offline: {cmd}", install(cmd) is None)
    state = lambda strata, loaded=(), streaming=False, active=False, model="quartz:strata": studio_module.model_state(model, strata, list(loaded), streaming, active)[:2]
    up = {"running": True, "health": {"loaded": True}}
    check("state: not loaded", state({"running": False}) == ("off", "Not loaded"))
    check("state: loading", state({"running": True, "health": {"loaded": False}, "job": {"kind": "start", "state": "running", "message": "[strata] loading"}}) == ("load", "Loading"))
    check("state: unloading", state({"running": True, "job": {"kind": "stop", "state": "stopping"}}) == ("load", "Unloading"))
    check("state: idle", state(up) == ("idle", "Idle"))
    check("state: generating", state(up, streaming=True) == ("work", "Generating"))
    check("state: reading prompt", state({**up, "lifecycle": {"active_requests": 1}, "job": {"kind": "start", "state": "running", "message": "[strata] reading the prompt: 1 of 2"}}) == ("work", "Reading prompt"))
    check("state: ollama model", state({}, model="llama3:8b") == ("off", "Not loaded") and state({}, [{"name": "llama3:8b"}], model="llama3:8b") == ("idle", "Loaded"))
    s, d, _ = c.get("/api/model/status?model=quartz:strata")
    check("a plain Ollama without the gateway's endpoints is not called offline", s == 200 and d["state"] == "off" and d["label"] == "Not loaded"
          and d["metrics"] is None, d)
    s, _, _ = c.get("/api/model/status", cookie=False)
    check("status route needs login", s == 401)
    s, d, _ = c.post("/api/workspace/sandbox", {"ws": ws, "enabled": False})
    check("sandbox off removes the container", s == 200 and d["workspace"]["sandbox"] is False and f"rm -f strata-sandbox-{ws}" in docker_calls(), d)
    os.remove(docker_log)
    _, ev_host = run_agent(c, ws, sess, "again", auto_apply=True, auto_commands=True)
    check("back on the host afterwards", "IN-SANDBOX" not in run_output(ev_host) and docker_calls() == "", docker_calls())
    with open(readme, "w") as fh:
        fh.write(original)

    s, _, _ = c.post("/api/agent/decision", {"run": run3, "approval_id": "nope", "decision": "accept"})
    check("decision for unknown approval -> 404", s == 404)
    s, _, _ = c.post("/api/agent/decision", {"run": run3, "approval_id": "nope", "decision": "maybe"})
    check("bad decision value -> 400", s == 400)

    print("agent: auto modes")
    approvals = []
    run4, ev4 = run_agent(c, ws, sess, "do it automatically", auto_apply=True, auto_commands=True,
                          on_approval=lambda e: approvals.append(e) or ("accept", ""))
    check("auto modes: no approvals asked", not approvals and "approval" not in kinds(ev4))
    fc4 = [e for e in ev4 if e["type"] == "file_changed"]
    check("auto modes: edit applied with checkpoint, run finished",
          open(readme).read() == original.replace("Sample", "Demo") and len(fc4) == 1 and ev4[-1]["reason"] == "finished")
    c.post("/api/agent/undo", {"ws": ws, "checkpoint": fc4[0]["checkpoint"]})
    check("auto-mode checkpoint undoes", open(readme).read() == original)

    print("agent: chat only, fallback, repeat nudge")
    run5, ev5 = run_agent(c, ws, sess, "hello there", on_approval=accept_all)
    k5 = kinds(ev5)
    text5 = "".join(e["text"] for e in ev5 if e["type"] == "token")
    check("hello: plain answer, no tools", "tool_call" not in k5 and text5.startswith("Hello!") and
          ev5[-1]["reason"] == "answered" and ev5[-1]["turns"] == 1, (k5, text5))
    n_before = len(mock())
    run6, ev6 = run_agent(c, ws, sess, "list the files", model="llama3.2:3b", on_approval=accept_all)
    check("model with tools=false is called without tools -> answered", "tool_call" not in kinds(ev6)
          and ev6[-1]["reason"] == "answered" and mock()[n_before]["tools"] is False)
    n_before = len(mock())
    run7, ev7 = run_agent(c, ws, sess, "hello", model="nostream:test", on_approval=accept_all)
    new_reqs = mock()[n_before:]
    text7 = "".join(e["text"] for e in ev7 if e["type"] == "token")
    check("stream failure falls back to stream:false once",
          [r["stream"] for r in new_reqs] == [True, False] and text7.startswith("Hello!") and ev7[-1]["reason"] == "answered",
          (new_reqs, ev7[-3:]))
    run8, ev8 = run_agent(c, ws, sess, "loop please", model="repeat:test", auto_apply=True, auto_commands=True,
                          on_approval=accept_all)
    check("repeat nudge sent, and the turn guard stops the run after the same call three more times", ev8[-1]["reason"] == "stuck" and ev8[-1]["turns"] == 4
          and "same call was made 4 times" in ev8[-1]["stuck"] and any(r["nudged"] for r in mock()), ev8[-1])
    run9, ev9 = run_agent(c, ws, sess, "hello", model="quartz:strata", on_approval=accept_all)
    sess_file = json.load(open(os.path.join(tmp, "app", "data", "sessions", sess + ".json")))
    check("session file holds model messages (no system prompt)",
          sess_file["messages"][0]["role"] == "user" and all(m["role"] != "system" for m in sess_file["messages"])
          and any(m["role"] == "tool" for m in sess_file["messages"]))
    s, _, _ = c.post("/api/agent/send", {"session": "zzzz", "ws": ws, "message": "x"})
    check("send with unknown session -> 404", s == 404)
    s, _, _ = c.post("/api/agent/send", {"session": sess, "ws": ws, "message": "  "})
    check("empty message -> 400", s == 400)
    s, _, _ = c.post("/api/agent/session/delete", {"id": sess})
    check("delete session", s == 200 and c.get(f"/api/agent/session?id={sess}")[0] == 404)

    print("unit: trimming + labels")
    spec = importlib.util.spec_from_file_location("studio_mod", os.path.join(ROOT, "studio.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    msgs = [{"role": "user", "content": "x"}] + [{"role": "tool", "tool_call_id": str(i), "content": "y" * 20000}
                                                 for i in range(10)]
    mod.trim_context(msgs)
    trimmed = [m["content"] for m in msgs[1:]]
    check("trim keeps last 6 tool outputs, trims older",
          trimmed[:4] == ["[older output trimmed]"] * 4 and all(len(t) == 20000 for t in trimmed[4:]))
    small = [{"role": "tool", "tool_call_id": "1", "content": "z" * 100} for _ in range(10)]
    mod.trim_context(small)
    check("no trimming below 90 000 chars", all(m["content"] == "z" * 100 for m in small))
    check("hide rules", mod.strata_hidden("quartz-full:strata") and mod.strata_hidden("a-sub6:strata")
          and mod.strata_hidden("qwen3.8-flash-next:125b-strata-swift-iq3-xxs")
          and not mod.strata_hidden("quartz-sub2:strata") and not mod.strata_hidden("qwen3.8-flash-next:125b-strata"))

    print("persistence across restart")
    # a second instance on the same config/data dir must accept the first instance's login cookie
    port2 = free_port()
    p2 = start_studio(tmp, cfg_path, port2)
    try:
        c2 = Client(port2)
        c2.cookie = c.cookie
        check("session cookie survives restart", c2.get("/api/config")[0] == 200)
        check("recent workspaces survive restart", c2.get("/api/workspace/recent")[1]["workspaces"][0]["id"] == ws)
        check("setup stays closed after restart", c2.get("/api/auth/state", cookie=False)[1]["setup_needed"] is False)
    finally:
        p2.terminate()
        p2.wait(5)

    leak_sess = c.post("/api/agent/sessions", {"ws": ws})[1]["id"]
    _, ev_cut = run_agent(c, ws, leak_sess, "A truncated call test", auto_apply=True, auto_commands=True)
    check("a cut-off reply is reported, not shown as an answer", ev_cut[-1]["reason"] == "cut_off" and any(e["type"] == "error" and "cut off" in e["text"] for e in ev_cut), ev_cut[-2:])
    check("a real answer mentioning the format still ends normally", sm_was_cut_off({"content": "The format is <tool_call> and more."}) is False and sm_was_cut_off({"content": "ok", "finish_reason": "length"}) is True and sm_was_cut_off({"content": "ok", "finish_reason": "stop"}) is False)
    run_leak, ev_leak = run_agent(c, ws, leak_sess, "A leaked call test", auto_apply=True, auto_commands=True)
    _, ev_retry = run_agent(c, ws, leak_sess, "A garbled once test", auto_apply=True, auto_commands=True)
    check("a garbled tool call is sent back, and the run goes on", ev_retry[-1]["reason"] == "finished" and not any(e["type"] == "error" for e in ev_retry), ev_retry[-2:])
    check("a reply that stays cut off still stops the run, after two more tries", sum(e["type"] == "usage" for e in ev_cut) == 3, [e for e in ev_cut if e["type"] == "usage"])
    _, ev_bare = run_agent(c, ws, leak_sess, "A bare call test", auto_apply=True, auto_commands=True)
    check("a bare JSON tool call runs, and the run goes on", [e["name"] for e in ev_bare if e["type"] == "tool_call"][0] == "list_files" and ev_bare[-1]["reason"] == "finished", ev_bare[-1])
    check("the weekend runners leave both off", all('"retry_cut_off": False, "text_tool_calls": False' in open(os.path.join(ROOT, "sandbox", name), encoding="utf-8").read() for name in ("overnight.py", "arena.py", "feature_night.py")))
    check("and keep the gateway's planning-loop guard and MCP as before", all('"no_planning_guard": False' in open(os.path.join(ROOT, "sandbox", name), encoding="utf-8").read() and '"mcp": False' in open(os.path.join(ROOT, "sandbox", name), encoding="utf-8").read() for name in ("overnight.py", "arena.py", "feature_night.py")))
    check("and leave the plan tool off, so their prompt stays as before", all('"plan_tool": False' in open(os.path.join(ROOT, "sandbox", name), encoding="utf-8").read() for name in ("overnight.py", "arena.py", "feature_night.py")))
    check("and leave view_page off, so their tool list stays as before", all('"live_view": False' in open(os.path.join(ROOT, "sandbox", name), encoding="utf-8").read() for name in ("overnight.py", "arena.py", "feature_night.py")))
    check("a tool call sent as text does not end the run", [e["name"] for e in ev_leak if e["type"] == "tool_call"][0] == "list_files" and ev_leak[-1]["reason"] == "finished", ev_leak[-1])
    fixed = [e for e in ev_leak if e["type"] == "text_fixed"]
    check("the chat is told the reply without the call, which it showed while streaming", fixed and "<tool_call>" not in fixed[0]["text"] and "Let me look." in fixed[0]["text"], fixed)
    _, ev_mixed = run_agent(c, ws, leak_sess, "A mixed call test", auto_apply=True, auto_commands=True)
    ran = [e["name"] for e in ev_mixed if e["type"] == "tool_call"]
    check("a call written as text beside a real one runs too", ran[:2] == ["list_files", "search"] and ev_mixed[-1]["reason"] == "finished", ran)
    mixed_fix = [e for e in ev_mixed if e["type"] == "text_fixed"]
    check("and its text leaves the chat", mixed_fix and "<tool_call>" not in mixed_fix[0]["text"], mixed_fix)
    stray_sess = c.post("/api/agent/sessions", {"ws": ws})[1]["id"]
    _, ev_stray = run_agent(c, ws, stray_sess, "Δοκιμή: ξένοι χαρακτήρες στο κείμενο ανάμεσα στις κλήσεις", auto_apply=True, auto_commands=True)
    stray_fix = [e for e in ev_stray if e["type"] == "text_fixed"]
    kept = c_get_json(gw_port, "/_mock/requests")["requests"][-1]["assistant_texts"]
    check("Chinese characters in the words beside a tool call leave the chat", stray_fix and stray_fix[0]["text"] == "Ο: θα κοιτάξω τα αρχεία του φακέλου.", stray_fix)
    check("and the model's own copy, while the run goes on", ev_stray[-1]["reason"] == "finished" and "Ο: θα κοιτάξω τα αρχεία του φακέλου." in kept and not any("用" in str(t) for t in kept), kept)
    with open(readme, "w") as fh:
        fh.write(original)
    unknown_sess = c.post("/api/agent/sessions", {"ws": ws})[1]["id"]
    run_unknown, ev_unknown = run_agent(c, ws, unknown_sess, "Fix this with an unknown call", auto_apply=True, auto_commands=True)
    first_result = next(e for e in ev_unknown if e["type"] == "tool_result")
    check("a text call to an unknown tool fails without changing anything", first_result["name"] == "edit_file" and first_result["ok"] is False and "Unknown tool" in first_result["output"], first_result)
    tree_sess = c.post("/api/agent/sessions", {"ws": ws})[1]["id"]
    asked_before = len(c_get_json(gw_port, "/_mock/requests")["requests"])
    new_during_run = os.path.join(os.path.dirname(readme), "made-during-run.txt")

    def add_a_file(event):
        with open(new_during_run, "w") as fh:
            fh.write("x")
        return "accept", ""
    run_agent(c, ws, tree_sess, "Please rename the word again", auto_apply=True, on_approval=add_a_file)
    asked = c_get_json(gw_port, "/_mock/requests")["requests"][asked_before:]
    check("the system prompt stays the same through a run, even when files appear", len(asked) >= 3 and len({r["system"] for r in asked}) == 1 and os.path.exists(new_during_run), len(asked))
    run_agent(c, ws, tree_sess, "hello again")
    latest = c_get_json(gw_port, "/_mock/requests")["requests"][-1]
    check("the next run sees the new file, in a note before its message", "made-during-run.txt" in latest["last_user"] and latest["last_user"].startswith("Studio context, newer than the system prompt"), latest["last_user"][:200])
    check("while the system prompt stays as it was, so the engine reuses what it read", latest["system"] == asked[0]["system"])
    os.remove(new_during_run)
    with open(readme, "w") as fh:
        fh.write(original)
    named = c_get_json(gw_port, "/_mock/requests")["requests"][-1]["system"]
    check("Studio asks for full thinking", c_get_json(gw_port, "/_mock/requests")["requests"][-1]["full_thinking"] == "1")
    check("Studio asks the gateway to leave its planning-loop guard off", c_get_json(gw_port, "/_mock/requests")["requests"][-1]["no_planning_guard"] == "1")
    check("the prompt names the user's language", "The user writes in English. Write everything you say to the user in English, unless they ask for another language." in named, named[-200:])
    check("the run goes on after an unknown tool", ev_unknown[-1]["reason"] == "finished" and any(e["type"] == "tool_result" and e["name"] == "replace_in_file" and e["ok"] for e in ev_unknown), ev_unknown[-1])
    with open(readme, "w") as fh:
        fh.write(original)
    test_accounts(c, tmp, sample, st_port)
    test_sharing(c, sample, st_port)
    print("login rate limit")
    c3 = Client(st_port)
    codes = [c3.post("/api/auth/login", {"password": "nope nope nope"})[0] for _ in range(7)]
    # one wrong password was already recorded in the auth section, so the limit trips after 4 more
    first_429 = codes.index(429) if 429 in codes else -1
    check("5 failures within a minute then 429", first_429 == 4 and set(codes[:4]) == {401}
          and set(codes[4:]) == {429}, codes)
    check("correct password also blocked while limited", c3.post("/api/auth/login", {"password": "correct horse"})[0] == 429)
    proxied = {"X-Forwarded-For": "192.168.2.50", "X-Forwarded-Proto": "https", "X-Studio": "1", "Content-Type": "application/json"}
    s, _, r = c3.request("POST", "/api/auth/login", {"password": "correct horse"}, headers=proxied)
    check("behind a proxy, each visitor has their own login limit", s == 200, s)
    check("behind an HTTPS proxy the cookie is Secure", "; Secure" in (r.getheader("Set-Cookie") or ""), r.getheader("Set-Cookie"))
    s, _, r = c3.request("POST", "/api/auth/login", {"password": "correct horse"}, headers={"X-Forwarded-Proto": "https"})
    check("X-Forwarded-Proto alone does not change anything", s == 429, s)


def c_get_json(port, path):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", path)
    data = json.loads(conn.getresponse().read())
    conn.close()
    return data


def c_post_json(port, path, body):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("POST", path, json.dumps(body), {"Content-Type": "application/json"})
    data = json.loads(conn.getresponse().read())
    conn.close()
    return data


if __name__ == "__main__":
    main()
