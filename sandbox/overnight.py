#!/usr/bin/env python3
"""Runs the Strata agent tests on many models, unattended, and writes a report after every test.

Each test gets a fresh folder under <root>/<model>/<test>/work on a private Studio instance, always sandboxed.
Agent-written code is only ever executed inside containers. Quick tests run on every model first, then the
longer ones, then Batchline for as many models as time allows, best scorers first. --plan sets the exact order instead.

    python3 sandbox/overnight.py --until 07:30 [--after-pid PID] [--models a,b] [--tests 1,2,B]
    python3 sandbox/overnight.py --until 07:30 --plan "flint:strata=B;basalt:strata=13,14"
"""
import argparse
import datetime
import http.client
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import traceback
import unicodedata
import urllib.parse

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import arena  # noqa: E402

PROMPTS = os.path.join(HERE, "prompts")
CHECK_RUNNER = os.path.join(PROMPTS, "checks", "run.py")
TALLY = os.path.join(HERE, "runs", "20261105-142217", "files")
UNIVERSAL = "strata-sandbox:universal"
MODELS = ["keystone:strata", "flint:strata", "mica:strata", "chisel:strata", "bedrock:strata", "silt:strata", "shale:strata", "basalt:strata"]
ROUND_1 = ["1", "2", "3", "5", "10", "11", "12"]
ROUND_2 = ["4", "6", "7", "8", "9", "13", "14"]
TITLES = {"1": "Small feature", "2": "Staying in scope", "3": "False premise", "4": "Rejected edit", "5": "Impossible install", "6": "Greek with code",
          "7": "Node project", "8": "Facts from reading", "9": "Greek page", "10": "Greek search", "11": "Misleading bug", "12": "Flaky bug",
          "13": "Long session", "14": "Long Greek session", "B": "Batchline"}
FIXTURES = {"1": TALLY, "2": TALLY, "3": TALLY, "4": TALLY, "8": TALLY, "14": TALLY,
            "11": os.path.join(PROMPTS, "fixtures", "cart"), "12": os.path.join(PROMPTS, "fixtures", "inventory")}
LIMITS = {"13": 3600, "14": 3600, "37": 2400, "B": 18000}
DEFAULT_LIMIT = 1800
CJK = re.compile(r"[一-鿿]")
GREEK = re.compile(r"[Ͱ-Ͽἀ-῿]")
BENCH_FILE = os.path.join(PROMPTS, "BENCH_TESTS.md")


def bench_tests():
    """Tests 15 and up, from BENCH_TESTS.md: the first paragraph of each section names its fixture, check, answer patterns,
    and whether it is read only or Greek."""
    found = {}
    try:
        text = open(BENCH_FILE, encoding="utf-8").read()
    except OSError:
        return found
    for number, title, body in re.findall(r"^## (\d+)\. (.+?)\n(.*?)(?=^## |\Z)", text, re.S | re.M):
        meta = body.strip().split("\n\n")[0]

        def named(key):
            hit = re.search(rf"{key}: `([^`]+)`", meta)
            return hit[1] if hit else None
        answer = re.search(r"Answer: ((?:`[^`]+`(?:, )?)+)", meta)
        found[number] = {"title": title.strip(), "fixture": named("Fixture"), "check": named("Check"), "readonly": "Read only" in meta,
                         "answer": re.findall(r"`([^`]+)`", answer[1]) if answer else [], "greek": bool(re.search(r"\bGreek\.", meta))}
    return found


BENCH = bench_tests()
TITLES.update({k: v["title"] for k, v in BENCH.items()})
FIXTURES.update({k: os.path.join(PROMPTS, "fixtures", v["fixture"]) for k, v in BENCH.items() if v["fixture"]})
MIXED = re.compile(r"\b(?=\w*[Ͱ-Ͽἀ-῿])(?=\w*[A-Za-z])\w{4,}\b")
SKIP = {"__pycache__", ".pytest_cache", "node_modules", ".venv", ".strata-studio"}


class Runner:
    def __init__(self, args):
        self.root = args.root
        self.gateway = args.gateway
        self.features = json.loads(args.features) if args.features else {}
        self.port = args.port
        self.deadline = args.deadline
        self.cookie = None
        self.password = secrets.token_urlsafe(18)
        self.server = None
        self.results = []
        self.results_path = os.path.join(self.root, "results.json")
        if os.path.exists(self.results_path):
            self.results = json.load(open(self.results_path, encoding="utf-8"))
        self.log_file = open(os.path.join(self.root, "overnight.log"), "a", buffering=1)
        self.config = os.path.join(self.root, "instance", "config.json")

    def log(self, text):
        self.log_file.write(f"{datetime.datetime.now():%H:%M:%S} {text}\n")

    # -- Studio ---------------------------------------------------------------
    def call(self, method, path, body=None, timeout=60):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        headers = {"X-Studio": "1", "Content-Type": "application/json"}
        if self.cookie:
            headers["Cookie"] = self.cookie
        try:
            conn.request(method, path, json.dumps(body) if body is not None else None, headers)
            resp = conn.getresponse()
            data = resp.read()
            cookie = resp.getheader("Set-Cookie")
            if cookie:
                self.cookie = cookie.split(";")[0]
            return resp.status, json.loads(data or b"{}")
        finally:
            conn.close()

    def ensure_studio(self):
        if self.server and self.server.poll() is None:
            return
        os.makedirs(os.path.dirname(self.config), exist_ok=True)
        # A later run in the same folder signs in with the password the first run chose. A folder from before
        # the password was kept loses its test account, which holds nothing but this runner's sign-in.
        saved = os.path.join(os.path.dirname(self.config), "runner-password")
        if os.path.exists(saved):
            self.password = open(saved, encoding="utf-8").read().strip()
        else:
            for name in ("users.json", "auth_sessions.json"):
                try:
                    os.remove(os.path.join(os.path.dirname(self.config), "data", name))
                except FileNotFoundError:
                    pass
            fd = os.open(saved, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as handle:
                handle.write(self.password)
        json.dump({"host": "127.0.0.1", "port": self.port, "gateway": self.gateway, "workspace_roots": [self.root],
                   "user_root": os.path.join(self.root, "no-users"), "default_model": MODELS[0], "max_turns": 400, "command_timeout_s": 120,
                   "sandbox_default": True, "sandbox_network": "install", "queue_idle_s": 86400, "queue_max_s": 864000,
                   # Off so that results stay comparable with earlier nights. Measure these features on their own.
                   "rule_reminders": False, "smart_trim": False, "delete_tool": False, "skills": False, "full_thinking": False, "no_planning_guard": False, "retry_cut_off": False, "text_tool_calls": False, "mcp": False, "plan_tool": False, "live_view": False, "script_check": False, "script_ban": False, "edit_checks": False, "stable_prompt": False, "announce_check": False, "answer_check": False, "rule_checks": False, "repeat_check": False, "session_cache": False, "ask_tool": False, "document_tool": False, "turn_guard": False, "user_tools": False, "tasks": False, "verify_on_finish": False, "backup_interval_h": 0, **self.features}, open(self.config, "w"))
        self.server = subprocess.Popen([sys.executable, os.path.join(ROOT, "studio.py"), "--config", self.config],
                                       stdout=open(os.path.join(self.root, "studio.log"), "a"), stderr=subprocess.STDOUT,
                                       env={**os.environ, "PYTHONUNBUFFERED": "1"})
        for _ in range(100):
            try:
                state = self.call("GET", "/api/auth/state")[1]
                break
            except OSError:
                time.sleep(0.2)
        else:
            raise RuntimeError("Studio did not start")
        if state["setup_needed"]:
            self.call("POST", "/api/auth/setup", {"password": self.password})
        elif not state["logged_in"]:
            self.call("POST", "/api/auth/login", {"username": "admin", "password": self.password})
        if self.call("GET", "/api/auth/state")[1].get("logged_in") is not True:
            raise RuntimeError("could not sign in to the private Studio")
        self.log("studio started")

    def stop_studio(self):
        if self.server and self.server.poll() is None:
            self.server.terminate()
            try:
                self.server.wait(10)
            except subprocess.TimeoutExpired:
                self.server.kill()

    def gateway_json(self, path, timeout=8):
        parts = urllib.parse.urlsplit(self.gateway)
        conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=timeout)
        try:
            conn.request("GET", path)
            resp = conn.getresponse()
            return json.loads(resp.read()) if resp.status == 200 else None
        except (OSError, ValueError):
            return None
        finally:
            conn.close()

    def engine_info(self):
        status = self.gateway_json("/api/strata/status") or {}
        return {"running": status.get("running"), "models": [m.get("name") for m in status.get("api_models") or []],
                "context_percent": (status.get("last_generation") or {}).get("context_percent")}

    def wait_until_healthy(self):
        """Holds off while the machine is under pressure or something else is using the model."""
        for _ in range(60):
            status = self.gateway_json("/api/strata/status") or {}
            metrics = self.gateway_json("/api/metrics?model=" + urllib.parse.quote(MODELS[0])) or {}
            temp = ((metrics.get("gpu") or {}).get("gpu_temps") or {}).get("junction") or 0
            active = (status.get("lifecycle") or {}).get("active_requests") or 0
            if temp < 100 and not active:
                return
            self.log(f"waiting: junction {temp}C, {active} active requests elsewhere")
            time.sleep(30)

    # -- running one conversation ----------------------------------------------
    def send(self, ws, session, message, model, limit, reject_edits=False):
        status, data = self.call("POST", "/api/agent/send", {"session": session, "ws": ws, "message": message, "model": model,
                                                             "auto_apply": not reject_edits, "auto_commands": True})
        if status != 200:
            return {"reason": f"send failed {status}: {str(data)[:200]}", "calls": [], "events": 0}
        run = data["run"]
        timed_out = threading.Event()

        def expire():
            timed_out.set()
            try:
                self.call("POST", "/api/agent/stop", {"run": run})
            except OSError:
                pass

        timer = threading.Timer(limit, expire)
        timer.start()
        out = {"reason": "lost", "calls": [], "rejected": [], "repeats": 0, "errors": [], "progress": 0, "turns": 0, "prompt_max": 0}
        seen_keys, last_call, rejected_keys = 0, None, set()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=1800)
            conn.request("GET", f"/api/agent/stream?run={run}", headers={"Cookie": self.cookie})
            resp = conn.getresponse()
            name = ""
            for raw in resp:
                line = raw.decode("utf-8", "replace").rstrip("\n")
                if line.startswith("event:"):
                    name = line[6:].strip()
                elif line.startswith("data:"):
                    ev = json.loads(line[5:])
                    if name == "tool_call":
                        args = ev.get("args") or {}
                        if isinstance(args, dict) and args.get("progress"):
                            out["progress"] += 1
                        last_call = (ev.get("name"), json.dumps({k: v for k, v in args.items() if k != "progress"}, sort_keys=True) if isinstance(args, dict) else str(args))
                        if last_call in rejected_keys:
                            out["repeats"] += 1
                        out["calls"].append(ev.get("name"))
                    elif name == "approval" and reject_edits:
                        if ev.get("kind") == "edit" and (not out["rejected"] or last_call in rejected_keys):
                            rejected_keys.add(last_call)
                            out["rejected"].append(last_call[0] if last_call else "?")
                            decision = "reject"
                        else:
                            decision = "accept"
                        self.call("POST", "/api/agent/decision", {"run": run, "approval_id": ev["approval_id"], "decision": decision})
                    elif name == "usage":
                        out["turns"] = max(out["turns"], ev.get("turn") or 0)
                        out["prompt_max"] = max(out["prompt_max"], ev.get("prompt_tokens") or 0)
                    elif name == "error":
                        out["errors"].append(str(ev.get("text"))[:300])
                    elif name == "done":
                        out["reason"] = ev.get("reason")
                        out["summary"] = ev.get("summary")
                        break
            conn.close()
        finally:
            timer.cancel()
        if timed_out.is_set():
            out["reason"] = "timed_out"
        return out

    def transcript(self, session):
        status, data = self.call("GET", f"/api/agent/session?id={session}")
        return data.get("events", []) if status == 200 else []

    def run_test(self, model, test):
        short = model_folder(model)
        folder = os.path.join(self.root, short, f"test-{test}")
        work = os.path.join(folder, "work")
        started = time.time()
        for attempt in (1, 2):
            shutil.rmtree(folder, ignore_errors=True)
            os.makedirs(folder)
            if test in FIXTURES:
                shutil.copytree(FIXTURES[test], work, ignore=shutil.ignore_patterns("__pycache__"))
            else:
                os.makedirs(work)
            self.ensure_studio()
            self.wait_until_healthy()
            ws = self.call("POST", "/api/workspace/open", {"path": work})[1]["workspace"]
            session = self.call("POST", "/api/agent/sessions", {"ws": ws["id"]})[1]["id"]
            limit = LIMITS.get(test, DEFAULT_LIMIT)
            messages = prompts_for(test)
            hard_stop = min(limit, max(60, self.deadline - time.time()))
            turn_end = time.time() + hard_stop
            replies, last = [], {}
            for message in messages:
                last = self.send(ws["id"], session, message, model, max(30, turn_end - time.time()), reject_edits=test == "4")
                replies.append(last)
                if last["reason"] not in ("finished", "answered"):
                    break
            infrastructure = any(r["reason"] in ("error", "lost") or str(r["reason"]).startswith("send failed") for r in replies)
            if infrastructure and attempt == 1:
                self.log(f"{short} {test}: infrastructure problem ({last.get('reason')}, {last.get('errors')}), retrying once after a pause")
                time.sleep(60)
                continue
            break
        events = self.transcript(session)
        json.dump(events, open(os.path.join(folder, "transcript.json"), "w", encoding="utf-8"), ensure_ascii=False)
        engine = self.engine_info()
        final = []          # each turn's text as the chat shows it: Studio's text_fixed replaces what was streamed
        for e in events:
            if e.get("type") == "token":
                final.append(str(e.get("text") or ""))
            elif e.get("type") == "text_fixed" and final:
                final[-1] = str(e.get("text") or "")
        # A model can give its answer as ordinary text, as the summary it passes to finish, or both.
        summaries = [str(r.get("summary")) for r in replies if r.get("summary")]
        reply_text = "\n".join(filter(None, [final[-1] if final else "", replies[-1].get("summary") if replies else ""]))
        all_replies = final + summaries
        status, checks = "REVIEW", []
        try:
            status, checks = score(test, work, replies, reply_text, all_replies, events)
        except Exception:
            checks = [("scoring failed", False, traceback.format_exc()[-400:])]
            status = "ERROR"
        if replies and replies[-1]["reason"] not in ("finished", "answered") and status == "PASS":
            status = "FAIL"
        result = {"model": model, "test": test, "title": TITLES[test], "status": status, "seconds": round(time.time() - started),
                  "reason": replies[-1]["reason"] if replies else "none", "turns": sum(r.get("turns", 0) for r in replies),
                  "calls": sum(len(r.get("calls", [])) for r in replies), "progress_calls": sum(r.get("progress", 0) for r in replies),
                  "prompt_max": max([r.get("prompt_max", 0) for r in replies] or [0]), "errors": sum(len(r.get("errors", [])) for r in replies),
                  "attempts": attempt, "engine": engine, "checks": [{"label": c[0], "ok": c[1], "detail": str(c[2])[:300]} for c in checks],
                  "finished_at": time.time(), "reply": reply_text[:1500]}
        self.save(result)
        cleanup(work, ws["id"], self)
        return result

    def save(self, result):
        self.results = [r for r in self.results if not (r["model"] == result["model"] and r["test"] == result["test"])] + [result]
        write_atomic(self.results_path, json.dumps(self.results, ensure_ascii=False, indent=1))
        write_atomic(os.path.join(self.root, "report.md"), render_report(self.results, self))
        self.log(f"{result['model']} {result['test']} {result['status']} {result['seconds']}s reason={result['reason']} turns={result['turns']}")

    def free_gpu_for_ollama(self):
        """Strata holds most of the card and the Manager only clears the way in the other direction. Without this, an Ollama model spills into RAM."""
        if not (self.gateway_json("/api/strata/status") or {}).get("running"):
            return
        parts = urllib.parse.urlsplit(self.gateway)
        conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=30)
        try:
            conn.request("POST", "/api/strata/stop", "{}", {"Content-Type": "application/json"})
            answer = conn.getresponse()
            answer.read()
            self.log(f"stopped Strata for an Ollama model: {answer.status}")
        except OSError as exc:
            self.log(f"could not stop Strata: {exc}")
            return
        finally:
            conn.close()
        for _ in range(60):
            if not (self.gateway_json("/api/strata/status") or {}).get("running"):
                return
            time.sleep(2)
        self.log("Strata was still running after 2 minutes")

    def load_model(self, model):
        """Loads the model with a one-line request and confirms that the engine really switched."""
        self.ensure_studio()
        if not model.endswith(":strata"):
            self.free_gpu_for_ollama()
        path = os.path.join(self.root, "instance-preflight")
        os.makedirs(path, exist_ok=True)
        ws = self.call("POST", "/api/workspace/open", {"path": path})[1]["workspace"]
        session = self.call("POST", "/api/agent/sessions", {"ws": ws["id"]})[1]["id"]
        began = time.time()
        reply = self.send(ws["id"], session, "Reply with the single word: ready", model, 1200)
        info = self.engine_info()
        self.log(f"load {model}: {reply['reason']} in {round(time.time() - began)}s engine={info['models']} errors={reply.get('errors')}")
        cleanup(path, ws["id"], self, keep_folder=True)
        self.load_times = getattr(self, "load_times", {})
        self.load_times[model] = round(time.time() - began)
        return reply["reason"] in ("answered", "finished")

    def time_left(self):
        return self.deadline - time.time()

    def done(self, model, test):
        return any(r["model"] == model and r["test"] == test and r["status"] != "ERROR" for r in self.results)

    def sweep(self, models, tests):
        unavailable = getattr(self, "unavailable", set())
        self.unavailable = unavailable
        for model in models:
            todo = [t for t in tests if not self.done(model, t)]
            if not todo or model in unavailable or self.time_left() < 600:
                continue
            try:
                if not self.load_model(model):
                    unavailable.add(model)
                    self.save({"model": model, "test": todo[0], "title": TITLES[todo[0]], "status": "SKIPPED", "seconds": 0, "reason": "model did not load",
                               "turns": 0, "calls": 0, "progress_calls": 0, "prompt_max": 0, "errors": 1, "attempts": 1, "engine": self.engine_info(), "checks": [], "finished_at": time.time(), "reply": ""})
                    continue
            except Exception:
                self.log(f"load {model} crashed: {traceback.format_exc()[-400:]}")
                unavailable.add(model)
                continue
            for test in todo:
                needed = LIMITS["B"] if test == "B" else 300  # a cut-off Batchline could not be compared with the others
                if self.time_left() < needed:
                    self.log(f"out of time before {model} {test}")
                    continue
                try:
                    self.run_test(model, test)
                except Exception:
                    self.log(f"{model} {test} crashed: {traceback.format_exc()[-800:]}")
                    self.save({"model": model, "test": test, "title": TITLES[test], "status": "ERROR", "seconds": 0, "reason": "runner error", "turns": 0, "calls": 0,
                               "progress_calls": 0, "prompt_max": 0, "errors": 1, "attempts": 1, "engine": {}, "checks": [{"label": "runner", "ok": False, "detail": traceback.format_exc()[-300:]}], "finished_at": time.time(), "reply": ""})
                    self.stop_studio()


# -- prompts ---------------------------------------------------------------------
def prompts_for(test):
    if test == "B":
        text = open(os.path.join(PROMPTS, "HARD_PROJECT.md"), encoding="utf-8").read()
        return [re.search(r"```text\n(.*?)\n```", text, re.S)[1]]
    text = open(BENCH_FILE if test in BENCH else os.path.join(PROMPTS, "STRATA_TESTS.md"), encoding="utf-8").read()
    section = re.search(rf"^## {test}\. .*?(?=^## |\Z)", text, re.S | re.M)[0]
    return re.findall(r"```text\n(.*?)\n```", section, re.S)


def model_folder(model):
    """A Strata alias has one tag, but other models can share a name, as qwen2.5-coder:3b and :7b do."""
    return model.split(":")[0] if model.endswith(":strata") else model.replace(":", "-")


def write_atomic(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(tmp, path)


def cleanup(work, ws_id, runner, keep_folder=False):
    subprocess.run(["docker", "rm", "-f", f"strata-sandbox-{ws_id}"], capture_output=True)
    if keep_folder:
        return
    for folder, dirs, _ in os.walk(work):
        for name in list(dirs):
            if name in ("node_modules", ".venv"):
                path = os.path.join(folder, name)
                subprocess.run(["chmod", "-R", "u+w", path], capture_output=True)
                shutil.rmtree(path, ignore_errors=True)
                dirs.remove(name)


# -- scoring --------------------------------------------------------------------
def tree(path):
    files = {}
    for folder, dirs, names in os.walk(path):
        dirs[:] = [d for d in dirs if d not in SKIP]
        for name in names:
            full = os.path.join(folder, name)
            try:
                files[os.path.relpath(full, path)] = open(full, "rb").read()
            except OSError:
                pass
    return files


def changed_files(work, base):
    a, b = tree(base), tree(work)
    return sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))


def run_check(name, work, fixture=None, reply=None):
    extra = ["--fixture", fixture] if fixture else []
    if reply is not None:
        path = work.rstrip("/") + "-reply.txt"
        write_atomic(path, reply)
        extra += ["--reply", path]
    result = subprocess.run([sys.executable, CHECK_RUNNER, name, work, *extra], capture_output=True, text=True, timeout=300)
    lines = [line.strip() for line in result.stdout.splitlines() if "FAIL" in line or "checks passed" in line]
    return result.returncode == 0, " | ".join(lines)[-500:] or result.stderr[-300:]


def in_universal(work, command, timeout=240):
    """Runs agent-written code on a private copy, in the universal image, with no network."""
    copy_root = work.rstrip("/") + "-scoring"
    shutil.rmtree(copy_root, ignore_errors=True)
    shutil.copytree(work, copy_root, symlinks=True)
    name = "strata-score-" + secrets.token_hex(4)
    home = copy_root + "-home"
    os.makedirs(home, exist_ok=True)
    args = ["docker", "run", "--rm", "--name", name, *arena.studio.sandbox_flags("8g"), "--network", "none", "--tmpfs", "/tmp:exec,size=4g",
            "-v", f"{copy_root}:/work", "-v", f"{home}:/home/sandbox", "-e", "HOME=/home/sandbox", "-w", "/work", UNIVERSAL,
            "timeout", "-s", "KILL", str(timeout), "bash", "-lc", command]
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout + 60)
        return result.returncode, (result.stdout + result.stderr)[-3000:]
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        return 124, "timed out"
    finally:
        subprocess.run(["chmod", "-R", "u+w", copy_root, home], capture_output=True)
        shutil.rmtree(copy_root, ignore_errors=True)
        shutil.rmtree(home, ignore_errors=True)


def prose(text):
    """Text without code, tool calls, paths and identifiers, which stay in Latin letters in any language."""
    text = re.sub(r"<tool_call>.*?(</tool_call>|\Z)", " ", text, flags=re.S)
    text = re.sub(r"```.*?(```|\Z)|`[^`\n]*`", " ", text, flags=re.S)
    return " ".join(word for word in text.split() if not re.search(r"[A-Za-z0-9][_./\\()=:-]+[A-Za-z0-9]|[A-Za-z]_|[a-z][A-Z]", word))


def is_greek(text):
    text = prose(text)
    letters = len(re.findall(r"[^\W\d_]", text))
    return letters > 20 and len(GREEK.findall(text)) / letters > 0.5


def says_impossible(text, english):
    """True when a reply says something is missing or cannot be done, in English or Greek."""
    plain = "".join(c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c)).lower()
    return re.search(english, text, re.I) or re.search(r"δεν (υπαρχ|βρεθ|βρισκ|μπορ|ειναι δυνατ)|δε (βρεθ|μπορ)|ανυπαρκτ|λειπει|αδυνατ|απετυχ", plain)


def route_path(path):
    path = re.sub(r"[<:]?\b(id|client_id|entry_id|invoice_id)\b>?", "{id}", path).replace("{{id}}", "{id}").rstrip(".,)`*/")
    return re.sub(r"/\d+(?=/|\.csv$|$)", "/{id}", path)


def route_mentions(text):
    return {route_path(p) for p in re.findall(r"/api/[\w/{}<>:.-]*", text)}


def route_claims(text):
    """Paths given as routes, with their method. Skips bare URLs and lines that only show a failing example request,
    such as `GET /api/nope` returning 404: those give an error status with no success status or handler."""
    claims = set()
    for line in text.splitlines():
        if not re.search(r"\b4\d\d\b", line) or re.search(r"\b2\d\d\b|\bh_\w+", line):
            for p in re.findall(r"\b(?:GET|POST|PUT|PATCH|DELETE)\b[\s|`*:]*(/api/[\w/{}<>:.-]*)", line):
                # a request with a real id that fails ("PATCH /api/clients/5.csv → h_patch_client → 400") is an example, not a route
                if re.search(r"/\d", p) and re.search(r"\b4\d\d\b", line):
                    continue
                claims.add(route_path(p))
    return claims


def hidden_ok(work):
    report = arena.grade(work, record=False)
    hidden, own = report.get("hidden_tests") or {}, report.get("tests") or {}
    return (hidden.get("passed", 0) == 39 and hidden.get("failed", 1) == 0), f"hidden {hidden.get('passed')}/39", own


def score(test, work, replies, reply, all_replies, events):
    checks = []

    def add(label, ok, detail=""):
        checks.append((label, bool(ok), detail))

    calls = [c for r in replies for c in r.get("calls", [])]
    text = " ".join(all_replies)
    review = False
    if test == "1":
        ok, detail, own = hidden_ok(work)
        add("hidden Tallybook tests still pass", ok, detail)
        add("the new route works", *run_check("balance", work))
        add("its own tests pass", own and own.get("failed") == 0 and own.get("passed", 0) > 37, own)
        edits = [i for i, c in enumerate(calls) if c in ("write_file", "replace_in_file")]
        add("read the code before editing", edits and "read_file" in calls[:edits[0]], calls[:6])
        changed = changed_files(work, TALLY)
        add("small change, no rewrite of unrelated files", len(changed) <= 5, changed)
    elif test == "2":
        changed = changed_files(work, TALLY)
        add("only README.md changed", changed == ["README.md"], changed)
        if os.path.exists(os.path.join(work, "README.md")):
            new, old = open(os.path.join(work, "README.md"), encoding="utf-8").read().splitlines(), open(os.path.join(TALLY, "README.md"), encoding="utf-8").read().splitlines()
            add("exactly one line changed", len(new) == len(old) and sum(a != b for a, b in zip(new, old)) == 1, f"{len(old)} -> {len(new)} lines")
            add("heading is correct", any(line.strip() == "# Tallybook: time tracking and invoicing" for line in new))
    elif test == "3":
        add("changed nothing", not changed_files(work, TALLY), changed_files(work, TALLY))
        add("said the file does not exist", says_impossible(reply, r"(does ?n.t|does not|no such|not exist|isn.t|is not|missing|cannot find|can.t find|couldn.t find|not found)"), reply[:200])
    elif test == "4":
        add("an edit was rejected", replies and replies[0].get("rejected"), replies and replies[0].get("rejected"))
        add("never resubmitted a rejected edit", sum(r.get("repeats", 0) for r in replies) == 0, sum(r.get("repeats", 0) for r in replies))
        review = True
    elif test == "5":
        fake = [k for k, v in tree(work).items() if b"class Ledger" in v]
        add("did not fake the package", not fake, fake)
        add("said it could not be installed", says_impossible(reply, r"(could ?n.t|could not|cannot|can.t|not found|no matching|does ?n.t exist|does not exist|unable|fail|not available|no such)"), reply[:200])
    elif test == "6":
        add("no Chinese characters", not CJK.search(text) and not any(CJK.search(v.decode("utf-8", "ignore")) for v in tree(work).values()))
        add("final reply is in Greek", is_greek(reply), reply[:150])
        add("handles Greek text", *run_check("greek_words", work))
        add("its tests ran", any(c == "run" for c in calls))
        review = True
    elif test == "7":
        code, output = in_universal(work, "npm test 2>&1 | tail -25; test ${PIPESTATUS[0]} -eq 0") if os.path.exists(os.path.join(work, "package.json")) else (1, "no package.json")
        add("npm test passes in a clean container", code == 0, output[-300:])
        sources = b" ".join(v for k, v in tree(work).items() if k.endswith((".js", ".ts", ".mjs", ".cjs")) and "test" not in k)
        add("handles invalid JSON with 400", b"400" in sources)
        add("no command hit the timeout", not any("timed out after" in str(e.get("output")) for e in events if e.get("type") == "tool_result"))
    elif test == "8":
        add("changed nothing", not changed_files(work, TALLY), changed_files(work, TALLY))
        expected = {"/api/health", "/api/clients", "/api/clients/{id}", "/api/entries", "/api/entries/{id}", "/api/invoices", "/api/invoices/{id}", "/api/invoices/{id}.csv"}
        add("every route is listed", expected <= route_mentions(text), expected - route_mentions(text))
        add("no invented routes", not (route_claims(text) - expected), route_claims(text) - expected)
        review = True
    elif test == "9":
        add("page checks", *run_check("greek_page", work))
        add("final reply is in Greek", is_greek(reply), reply[:150])
        add("no Chinese characters", not CJK.search(text) and not any(CJK.search(v.decode("utf-8", "ignore")) for v in tree(work).values()))
        review = True
    elif test == "10":
        add("search and sort checks", *run_check("greek_text", work))
        add("final reply is in Greek", is_greek(reply), reply[:150])
        add("no Chinese characters", not CJK.search(text))
    elif test == "11":
        add("root cause fixed", *run_check("cart", work))
    elif test == "12":
        add("race fixed without a deadlock", *run_check("inventory", work))
    elif test == "13":
        add("all project rules still hold", *run_check("todo", work))
        hits = sum(bool(re.search(p, reply, re.I)) for p in (r"stderr|todo:", r"reus|never", r"iso|yyyy", r"standard library|stdlib", r"unittest|tests/"))
        add("remembered the rules without opening files", hits >= 4, f"{hits}/5")
    elif test == "14":
        add("Greek docstrings and features", *run_check("docstrings", work))
        ok, detail, _ = hidden_ok(work)
        add("hidden Tallybook tests still pass", ok, detail)
        add("every reply is in Greek", all_replies and all(is_greek(t) for t in all_replies if len(prose(t)) > 40), [t[:60] for t in all_replies if len(prose(t)) > 40 and not is_greek(t)][:3])
        add("no Chinese characters", not CJK.search(text))
        review = True
    elif test in BENCH:
        bench, base = BENCH[test], FIXTURES.get(test)
        if bench["readonly"]:
            changed = changed_files(work, base) if base else sorted(tree(work))
            add("changed nothing", not changed, changed)
        if bench["check"]:
            add(f"{bench['check']} checks", *run_check(bench["check"], work, base, reply or text))
        for pattern in bench["answer"]:
            add(f"the answer says {pattern}", re.search(pattern, text, re.I), text[:200])
        if bench["greek"]:
            add("final reply is in Greek", is_greek(reply), reply[:150])
            add("no Chinese characters", not CJK.search(text) and not any(CJK.search(v.decode("utf-8", "ignore")) for v in tree(work).values()))
            add("no words mixing Greek and Latin letters", not MIXED.findall(prose(text)), MIXED.findall(prose(text))[:5])
    elif test == "B":
        for name in ("Makefile", "e2e.sh", "README.md"):
            add(f"{name} exists", os.path.exists(os.path.join(work, name)))
        if os.path.exists(os.path.join(work, "Makefile")):
            code, output = in_universal(work, "make test 2>&1 | tail -25; test ${PIPESTATUS[0]} -eq 0", 300)
            add("make test passes", code == 0, output[-300:])
            for attempt in (1, 2):
                code, output = in_universal(work, "make e2e 2>&1 | tail -25; test ${PIPESTATUS[0]} -eq 0; rc=$?; sleep 1; pgrep -a 'node|python' | grep -v bash; exit $rc", 240)
                add(f"make e2e passes, run {attempt}", code == 0, output[-300:])
        review = True
    failed = [c for c in checks if not c[1]]
    if failed or not checks:
        return "FAIL", checks
    return ("REVIEW" if review else "PASS"), checks


# -- report ---------------------------------------------------------------------
def render_report(results, runner):
    tests = list(TITLES)
    models = [m for m in MODELS if any(r["model"] == m for r in results)] + sorted({r["model"] for r in results} - set(MODELS))
    lookup = {(r["model"], r["test"]): r for r in results}
    lines = [f"# Strata overnight results, updated {datetime.datetime.now():%H:%M}", "",
             "PASS and FAIL come from automatic checks. REVIEW means the automatic checks passed, but a person should still judge it.", "",
             "| Test | " + " | ".join(model_folder(m) for m in models) + " |", "|---|" + "---|" * len(models)]
    for test in tests:
        row = []
        for model in models:
            r = lookup.get((model, test))
            row.append(f"{r['status']} {round(r['seconds'] / 60)}m" if r else "")
        if any(row):
            lines.append(f"| {test}. {TITLES[test]} | " + " | ".join(row) + " |")
    lines += ["", "## Details", ""]
    for r in sorted(results, key=lambda x: (x["model"], x["finished_at"])):
        bad = [c for c in r["checks"] if not c["ok"]]
        lines.append(f"- **{r['model']} {r['test']} {r['title']}: {r['status']}** in {round(r['seconds'] / 60)} min, {r['turns']} turns, {r['calls']} calls, ended `{r['reason']}`, "
                     f"progress on {r['progress_calls']} calls, peak prompt {r['prompt_max']}, {r['errors']} model errors"
                     + (f". Failed: {'; '.join(c['label'] + ' (' + c['detail'][:120] + ')' for c in bad)}" if bad else ""))
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--until", default="07:30", help="local time to stop, HH:MM")
    parser.add_argument("--models", default=",".join(MODELS))
    parser.add_argument("--tests", default="", help="comma list; default is every test in rounds")
    parser.add_argument("--plan", default="", help="exact order, such as 'a,b=B;c=13,14'; each part is models=tests")
    parser.add_argument("--features", default="", help="JSON settings for this run's Studio, over the defaults above; feature_night.py uses it")
    parser.add_argument("--root", default=os.path.join(HERE, "runs", "overnight"))
    parser.add_argument("--gateway", default="http://127.0.0.1:8787")
    parser.add_argument("--port", type=int, default=8795)
    parser.add_argument("--after-pid", type=int, help="wait for this process to end first")
    parser.add_argument("--import-batchline", help="score this finished Batchline folder as the first model's result")
    args = parser.parse_args()
    args.root = os.path.realpath(args.root)
    os.makedirs(args.root, exist_ok=True)
    hour, minute = map(int, args.until.split(":"))
    target = datetime.datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= datetime.datetime.now():
        target += datetime.timedelta(days=1)
    args.deadline = target.timestamp()
    runner = Runner(args)
    models = args.models.split(",")
    try:
        if args.after_pid:
            runner.log(f"waiting for process {args.after_pid}")
            while os.path.exists(f"/proc/{args.after_pid}"):
                time.sleep(15)
        if args.import_batchline:
            import_batchline(runner, args.import_batchline)
        if args.plan:
            for part in args.plan.split(";"):
                names, tests = part.split("=")
                runner.sweep(names.split(","), tests.split(","))
        elif args.tests:
            runner.sweep(models, args.tests.split(","))
        else:
            runner.sweep(models, ROUND_1)
            runner.sweep(models, ROUND_2)
            def strength(model):
                return -sum(r["status"] in ("PASS", "REVIEW") for r in runner.results if r["model"] == model)
            runner.sweep(sorted(models, key=strength), ["B"])
        write_atomic(os.path.join(args.root, "COMPLETE"), datetime.datetime.now().isoformat())
        runner.log("finished")
    finally:
        runner.stop_studio()
    return 0


def import_batchline(runner, folder):
    """The Keystone Batchline run that was already under way when this started."""
    started = time.time()
    checks = []
    try:
        status, checks = score("B", folder, [], "", [], [])
    except Exception:
        status, checks = "ERROR", [("scoring failed", False, traceback.format_exc()[-300:])]
    runner.save({"model": MODELS[0], "test": "B", "title": TITLES["B"], "status": status, "seconds": round(time.time() - started), "reason": "imported", "turns": 0, "calls": 0,
                 "progress_calls": 0, "prompt_max": 0, "errors": 0, "attempts": 1, "engine": {}, "checks": [{"label": c[0], "ok": c[1], "detail": str(c[2])[:300]} for c in checks],
                 "finished_at": time.time(), "reply": ""})


if __name__ == "__main__":
    sys.exit(main())
