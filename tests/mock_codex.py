#!/usr/bin/env python3
"""A fake Codex CLI for the tests: `mock_codex.py app-server` speaks the part of Codex's app-server protocol (JSON-RPC
over stdio) that Studio uses, with scripted turns chosen by words in the message:

  "edit"   reads calc.py (a read-only command), patches it and adds NOTES.md (asks to change files), then runs python3
           calc.py (asks to run a command), and answers with what happened
  "slow"   says one word, then waits: a steer is answered with "Got: <text>", an interrupt ends the turn
  "ask"    asks the user a question and repeats the answer
  "fail"   ends the turn with an error
  "tools"  starts the `studio` tool server it was given in thread/start's config (Studio's bridge), lists its tools,
           asks Studio whether it may call mock__echo and mock__picture (as Codex does, by an elicitation), calls them and
           answers with what it got
  "helpers" spawns a sub-agent, whose own thread runs a command (asking first) and says something the chat must not show
  else     answers "Hello from mock Codex", adding "(I know the earlier chat)" when the thread was started with one

Every message it receives is appended to $MOCK_CODEX_LOG (one JSON per line), so the tests can see what Studio sent.
Threads it started are kept in $MOCK_CODEX_LOG + ".threads", so a later process can resume them.
"""
import json
import os
import sys
import threading
import time
import uuid

LOG = os.environ.get("MOCK_CODEX_LOG")
THREADS = (LOG or "/tmp/mock-codex") + ".threads"
lock = threading.Lock()
replies = {}            # our request id -> the answer
answered = threading.Condition()
inbox = []              # steer / interrupt messages for the running turn
next_id = [100]


def log(msg):
    if LOG:
        with open(LOG, "a") as fh:
            fh.write(json.dumps(msg) + "\n")


def send(msg):
    with lock:
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", **msg}) + "\n")
        sys.stdout.flush()


def notify(method, params):
    send({"method": method, "params": params})


def ask(method, params, timeout=60):
    """A request to Studio; waits for its answer."""
    with answered:
        next_id[0] += 1
        rid = next_id[0]
    send({"id": rid, "method": method, "params": params})
    deadline = time.time() + timeout
    with answered:
        while rid not in replies and time.time() < deadline:
            answered.wait(0.2)
        return replies.pop(rid, None)


def threads():
    try:
        return json.load(open(THREADS))
    except (OSError, ValueError):
        return {}


def say(tid, turn, text, phase="final_answer"):
    iid = "msg_" + uuid.uuid4().hex[:8]
    notify("item/started", {"threadId": tid, "turnId": turn, "item": {"type": "agentMessage", "id": iid, "text": "", "phase": phase}})
    for word in text.split(" "):
        notify("item/agentMessage/delta", {"threadId": tid, "turnId": turn, "itemId": iid, "delta": word + " "})
    notify("item/completed", {"threadId": tid, "turnId": turn, "item": {"type": "agentMessage", "id": iid, "text": text, "phase": phase}})
    return text


def command(tid, turn, cmd, run):
    iid = "exec-" + uuid.uuid4().hex[:8]
    item = {"type": "commandExecution", "id": iid, "command": f"/bin/bash -lc '{cmd}'", "cwd": os.getcwd(), "status": "inProgress",
            "aggregatedOutput": None, "exitCode": None}
    notify("item/started", {"threadId": tid, "turnId": turn, "item": item})
    answer = ask("item/commandExecution/requestApproval", {"threadId": tid, "turnId": turn, "itemId": iid, "kind": "command",
                                                           "command": item["command"], "cwd": os.getcwd(),
                                                           "availableDecisions": ["accept", "decline", "cancel"]})
    decision = (answer or {}).get("result", {}).get("decision")
    if decision == "accept":
        out, code = run()
        item.update(status="completed" if code == 0 else "failed", aggregatedOutput=out, exitCode=code)
    else:
        item.update(status="declined")
    notify("item/completed", {"threadId": tid, "turnId": turn, "item": item})
    return decision


def turn_edit(tid, turn):
    say(tid, turn, "I will fix calc.py.", "commentary")
    command(tid, turn, "cat calc.py", lambda: (open("calc.py").read(), 0))
    iid = "patch-" + uuid.uuid4().hex[:8]
    calc = os.path.join(os.getcwd(), "calc.py")
    notes = os.path.join(os.getcwd(), "NOTES.md")
    changes = [{"path": notes, "kind": {"type": "add"}, "diff": "fixed\n"},
               {"path": calc, "kind": {"type": "update", "move_path": None}, "diff": "@@ -1,3 +1,3 @@\n def add(a, b):\n-    return a - b\n+    return a + b\n \n"}]
    item = {"type": "fileChange", "id": iid, "changes": changes, "status": "inProgress"}
    notify("item/started", {"threadId": tid, "turnId": turn, "item": item})
    answer = ask("item/fileChange/requestApproval", {"threadId": tid, "turnId": turn, "itemId": iid, "reason": None})
    if (answer or {}).get("result", {}).get("decision") == "accept":
        open(notes, "w").write("fixed\n")
        fixed = open(calc).read().replace("a - b", "a + b")
        open(calc, "w").write(fixed)
        item["status"] = "completed"
    else:
        item["status"] = "declined"
    notify("item/completed", {"threadId": tid, "turnId": turn, "item": item})
    notify("turn/plan/updated", {"threadId": tid, "turnId": turn, "explanation": None,
                                 "plan": [{"step": "Read calc.py", "status": "completed"}, {"step": "Fix add", "status": "completed"},
                                          {"step": "Run it", "status": "inProgress"}]})
    ran = command(tid, turn, "python3 calc.py", lambda: ("5\n", 0))
    notify("thread/tokenUsage/updated", {"threadId": tid, "turnId": turn, "tokenUsage": {"total": {"inputTokens": 900, "outputTokens": 40},
                                                                                         "last": {"inputTokens": 500, "outputTokens": 20},
                                                                                         "modelContextWindow": 272000}})
    return say(tid, turn, f"Patch {item['status']}; the run was {'allowed' if ran == 'accept' else 'skipped'}.")


def turn_slow(tid, turn):
    say(tid, turn, "Working", "commentary")
    deadline = time.time() + 30
    while time.time() < deadline:
        with answered:
            while not inbox and time.time() < deadline:
                answered.wait(0.2)
            got = inbox.pop(0) if inbox else None
        if got is None:
            break
        kind, payload = got
        if kind == "interrupt":
            return None
        if kind == "steer":
            return say(tid, turn, "Got: " + payload)
    return say(tid, turn, "Slow turn ended")


def turn_long(tid, turn, stubborn):
    """A whole goal in one turn, as real Codex does: commands and token use until steered (polite: a summary) or
    interrupted (stubborn keeps going after the note)."""
    steered = False
    for i in range(40):
        notify("thread/tokenUsage/updated", {"threadId": tid, "turnId": turn, "tokenUsage": {"total": {"inputTokens": 3000 * (i + 1), "outputTokens": 500 * (i + 1)},
                                             "last": {"inputTokens": 3000, "cachedInputTokens": 1000, "outputTokens": 500}, "modelContextWindow": 272000}})
        time.sleep(0.3)
        with answered:
            got = inbox.pop(0) if inbox else None
        if got and got[0] == "interrupt":
            return None
        if got and got[0] == "steer":
            steered = True
            if not stubborn:
                return say(tid, turn, "Allowance used up: questions 1-3 are in quiz.html, the rest is left.")
        command(tid, turn, f"echo step {i}", lambda: (f"step {i}\n", 0))
    return say(tid, turn, "Done without being stopped" + (" (steered)" if steered else ""))


class Bridge:
    """The `studio` MCP server from thread/start's config, started the way Codex starts one."""

    def __init__(self, spec):
        import subprocess
        self.proc = subprocess.Popen([spec["command"], *spec.get("args", [])], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                                     env={**os.environ, **(spec.get("env") or {})}, bufsize=1)
        self.n = 0

    def call(self, method, params):
        self.n += 1
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params}) + "\n")
        self.proc.stdin.flush()
        while True:
            msg = json.loads(self.proc.stdout.readline())
            if msg.get("id") == self.n:
                return msg

    def close(self):
        self.proc.terminate()


def turn_tools(tid, turn, info, prefix="mock"):
    spec = (info.get("mcp") or {}).get("studio")
    if not spec:
        return say(tid, turn, "No studio tools.")
    bridge = Bridge(spec)
    try:
        bridge.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "codex", "version": "0"}})
        names = sorted(t["name"] for t in bridge.call("tools/list", {})["result"]["tools"])
        got = []
        for name, args in ((prefix + "__echo", {"text": "via bridge"}), (prefix + "__picture", {})):
            iid = "mcp-" + uuid.uuid4().hex[:6]
            notify("item/started", {"threadId": tid, "turnId": turn, "item": {"type": "mcpToolCall", "id": iid, "server": "studio", "tool": name, "status": "inProgress"}})
            answer = ask("mcpServer/elicitation/request", {"threadId": tid, "turnId": turn, "serverName": "studio", "mode": "form",
                                                           "_meta": {"codex_approval_kind": "mcp_tool_call"}, "message": f"Allow the studio MCP server to run tool \"{name}\"?",
                                                           "requestedSchema": {"type": "object", "properties": {}}})
            if (answer or {}).get("result", {}).get("action") != "accept":
                got.append(name + " refused")
                continue
            result = bridge.call("tools/call", {"name": name, "arguments": args})["result"]
            kinds = [c["type"] + (":" + c["text"] if c["type"] == "text" else ":" + c.get("mimeType", "")) for c in result["content"]]
            got.append(f"{name} -> {'error ' if result.get('isError') else ''}{' + '.join(kinds)}")
            notify("item/completed", {"threadId": tid, "turnId": turn, "item": {"type": "mcpToolCall", "id": iid, "server": "studio", "tool": name, "status": "completed"}})
        return say(tid, turn, "TOOLS " + ",".join(names) + " | " + " | ".join(got))
    finally:
        bridge.close()


def turn_helpers(tid, turn):
    sub, sub_turn, iid = "thread-sub-" + uuid.uuid4().hex[:6], "turn-sub", "collab-" + uuid.uuid4().hex[:6]
    spawn = {"type": "collabAgentToolCall", "id": iid, "tool": "spawnAgent", "status": "inProgress", "senderThreadId": tid,
             "receiverThreadIds": [sub], "prompt": "count the files", "model": "mock-small", "agentsStates": {}}
    notify("item/started", {"threadId": tid, "turnId": turn, "item": spawn})
    notify("item/agentMessage/delta", {"threadId": sub, "turnId": sub_turn, "itemId": "sub-msg", "delta": "SECRET SUB-AGENT CHATTER"})
    ran = command(sub, sub_turn, "touch counted.txt", lambda: ("", 0))
    notify("item/completed", {"threadId": tid, "turnId": turn, "item": {**spawn, "status": "completed", "agentsStates": {sub: {"status": "completed", "message": "3 files"}}}})
    notify("turn/completed", {"threadId": sub, "turn": {"id": sub_turn, "status": "completed", "items": []}})
    return say(tid, turn, f"The helper counted 3 files; its command was {'allowed' if ran == 'accept' else 'skipped'}.")


def turn_picture(tid, turn):
    """Codex's own image tool: the picture is saved under CODEX_HOME/generated_images and only its path is sent."""
    import base64
    iid = "ig-" + uuid.uuid4().hex[:6]
    folder = os.path.join(os.environ["CODEX_HOME"], "generated_images", tid)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, iid + ".png")
    open(path, "wb").write(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg=="))
    item = {"type": "imageGeneration", "id": iid, "status": "inProgress", "inputImages": []}
    notify("item/started", {"threadId": tid, "turnId": turn, "item": item})
    notify("item/completed", {"threadId": tid, "turnId": turn, "item": {**item, "status": "completed", "revisedPrompt": "A red lighthouse at dusk", "savedPath": path}})
    evil = {"type": "imageGeneration", "id": "ig-evil", "status": "completed", "inputImages": [], "savedPath": "/etc/hostname"}
    notify("item/completed", {"threadId": tid, "turnId": turn, "item": evil})
    return say(tid, turn, "I drew a lighthouse.")


def run_turn(tid, turn, text, info):
    notify("turn/started", {"threadId": tid, "turn": {"id": turn, "status": "inProgress", "items": []}})
    status, error, final = "completed", None, ""
    if "Codex long" in text:
        final = turn_long(tid, turn, "stubborn" in text)
        if final is None:
            status = "interrupted"
    elif "Codex budget" in text:               # a goal that never ends: 2500 new tokens a round (3000 read, 1000 of them cached, 500 written)
        notify("thread/tokenUsage/updated", {"threadId": tid, "turnId": turn, "tokenUsage": {"total": {"inputTokens": 3000, "outputTokens": 500},
                                             "last": {"inputTokens": 3000, "cachedInputTokens": 1000, "outputTokens": 500}, "modelContextWindow": 272000}})
        final = say(tid, turn, "Still working on it.")
    elif "Codex goal" in text:               # a goal: one round of work, then done when sent back to it
        final = say(tid, turn, "GOAL COMPLETE: both steps are done." if "(round 1)" in text else "Step one is done; step two next.")
    elif "draw" in text:
        final = turn_picture(tid, turn)
    elif "edit" in text:
        final = turn_edit(tid, turn)
    elif "slow" in text:
        final = turn_slow(tid, turn)
        if final is None:
            status = "interrupted"
    elif "ask" in text:
        answer = ask("item/tool/requestUserInput", {"threadId": tid, "turnId": turn, "itemId": "q1", "isBlocking": True, "autoResolutionMs": None,
                                                   "questions": [{"id": "color", "header": "Colour", "question": "Which colour?", "isOther": True, "isSecret": False,
                                                                  "options": [{"label": "Red", "description": ""}, {"label": "Blue", "description": ""}]}]})
        picked = ((answer or {}).get("result", {}).get("answers") or {}).get("color", {}).get("answers") or ["nothing"]
        final = say(tid, turn, "You picked " + ", ".join(picked))
    elif "tools" in text:
        final = turn_tools(tid, turn, info, "blender" if "blender" in text else "mock")
    elif "helpers" in text:
        final = turn_helpers(tid, turn)
    elif "fail" in text:
        status, error = "failed", {"message": "usage limit reached", "codexErrorInfo": None}
    else:
        notify("account/rateLimits/updated", {"rateLimits": {"limitId": "codex", "primary": {"usedPercent": 13, "windowDurationMins": 300, "resetsAt": int(time.time()) + 7000},
                                                             "secondary": None}})
        final = say(tid, turn, "Hello from mock Codex" + (" (I know the earlier chat)" if info.get("earlier") else ""))
    notify("turn/completed", {"threadId": tid, "turn": {"id": turn, "status": status, "error": error,
                                                        "items": [{"type": "agentMessage", "id": "x", "text": final}] if final else []}})


def main():
    if sys.argv[1:2] != ["app-server"]:
        print("mock codex: only app-server", file=sys.stderr)
        return 2
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        log(msg)
        method, params, rid = msg.get("method"), msg.get("params") or {}, msg.get("id")
        if method is None and rid is not None:          # an answer to one of our requests
            with answered:
                replies[rid] = msg
                answered.notify_all()
            continue
        if method == "initialize":
            send({"id": rid, "result": {"userAgent": "mock", "codexHome": "/tmp", "platformFamily": "unix", "platformOs": "linux"}})
        elif method == "initialized":
            pass
        elif method == "account/read":
            signed = not os.path.exists(THREADS + ".signed-out")
            send({"id": rid, "result": {"account": {"type": "chatgpt", "email": "tester@example.com", "planType": "plus", "workspaceName": None} if signed else None,
                                        "requiresOpenaiAuth": True, "userId": None}})
        elif method == "account/logout":
            open(THREADS + ".signed-out", "w").close()
            send({"id": rid, "result": {}})
        elif method == "account/login/start":
            kind = params.get("type")
            if kind == "apiKey":
                if os.path.exists(THREADS + ".signed-out"):
                    os.remove(THREADS + ".signed-out")
                send({"id": rid, "result": {"type": "apiKey"}})
            elif kind == "chatgptDeviceCode":
                send({"id": rid, "result": {"type": "chatgptDeviceCode", "loginId": "login-1", "verificationUrl": "https://auth.example/device", "userCode": "ABCD-1234"}})

                def finish():
                    time.sleep(1.5)
                    if os.path.exists(THREADS + ".signed-out"):
                        os.remove(THREADS + ".signed-out")
                    notify("account/login/completed", {"loginId": "login-1", "success": True, "error": None})
                threading.Thread(target=finish, daemon=True).start()
            else:                          # the browser link: a small server on localhost takes the callback
                import http.server
                port = int(os.environ.get("MOCK_CODEX_CALLBACK_PORT", "0"))

                class Callback(http.server.BaseHTTPRequestHandler):
                    def log_message(self, *a):
                        pass

                    def do_GET(self):
                        ok = self.path.startswith("/auth/callback?code=good")
                        self.send_response(200 if ok else 400); self.end_headers(); self.wfile.write(b"done" if ok else b"bad")
                        if ok:
                            if os.path.exists(THREADS + ".signed-out"):
                                os.remove(THREADS + ".signed-out")
                            notify("account/login/completed", {"loginId": "login-2", "success": True, "error": None})
                srv = http.server.HTTPServer(("127.0.0.1", port), Callback)
                threading.Thread(target=srv.serve_forever, daemon=True).start()
                redirect = f"http://localhost:{srv.server_address[1]}/auth/callback"
                send({"id": rid, "result": {"type": "chatgpt", "loginId": "login-2",
                                            "authUrl": "https://auth.example/oauth/authorize?redirect_uri=" + __import__("urllib.parse").parse.quote(redirect, safe="")}})
        elif method == "account/login/cancel":
            send({"id": rid, "result": {"status": "canceled"}})
        elif method == "account/rateLimits/read":
            send({"id": rid, "result": {"ordinaryUsageAllowed": True, "rateLimitResetCredits": {"availableCount": 2, "credits": []},
                                        "rateLimits": {"limitId": "codex", "planType": "plus", "rateLimitReachedType": None,
                                                       "primary": {"usedPercent": 12, "windowDurationMins": 300, "resetsAt": int(time.time()) + 7200},
                                                       "secondary": {"usedPercent": 40, "windowDurationMins": 10080, "resetsAt": int(time.time()) + 300000}}}})
        elif method == "model/list":
            send({"id": rid, "result": {"data": [
                {"id": "mock-big", "model": "mock-big", "displayName": "Mock Big", "description": "the big one", "hidden": False, "inputModalities": ["text", "image"],
                 "defaultReasoningEffort": "low", "supportedReasoningEfforts": [{"reasoningEffort": e, "description": e + " thinking"} for e in ("low", "medium", "high", "ultra")]},
                {"id": "mock-small", "model": "mock-small", "displayName": "Mock Small", "description": "", "hidden": False, "inputModalities": ["text"]},
                {"id": "mock-hidden", "model": "mock-hidden", "displayName": "Hidden", "description": "", "hidden": True}], "nextCursor": None}})
        elif method == "thread/start":
            tid = "thread-" + uuid.uuid4().hex[:8]
            known = threads()
            known[tid] = {"earlier": "before you joined" in (params.get("developerInstructions") or ""), "cwd": params.get("cwd"),
                          "mcp": (params.get("config") or {}).get("mcp_servers")}
            json.dump(known, open(THREADS, "w"))
            send({"id": rid, "result": {"thread": {"id": tid}, "model": params.get("model"), "cwd": params.get("cwd")}})
        elif method == "thread/resume":
            if params.get("threadId") in threads():
                known = threads()
                known[params["threadId"]]["mcp"] = (params.get("config") or {}).get("mcp_servers")
                json.dump(known, open(THREADS, "w"))
                send({"id": rid, "result": {"thread": {"id": params["threadId"]}, "model": params.get("model")}})
            else:
                send({"id": rid, "error": {"code": -32600, "message": "no rollout found for thread id"}})
        elif method == "turn/start":
            tid = params["threadId"]
            turn = "turn-" + uuid.uuid4().hex[:8]
            text = " ".join(i.get("text", "") for i in params.get("input") or [] if i.get("type") == "text")
            send({"id": rid, "result": {"turn": {"id": turn, "status": "inProgress", "items": []}}})
            threading.Thread(target=run_turn, args=(tid, turn, text, threads().get(tid, {})), daemon=True).start()
        elif method == "turn/steer":
            text = " ".join(i.get("text", "") for i in params.get("input") or [] if i.get("type") == "text")
            send({"id": rid, "result": {"turnId": params.get("expectedTurnId")}})
            with answered:
                inbox.append(("steer", text))
                answered.notify_all()
        elif method == "turn/interrupt":
            send({"id": rid, "result": {}})
            with answered:
                inbox.append(("interrupt", None))
                answered.notify_all()
        elif rid is not None:
            send({"id": rid, "error": {"code": -32601, "message": f"mock codex does not know {method}"}})
    return 0


if __name__ == "__main__":
    sys.exit(main())
