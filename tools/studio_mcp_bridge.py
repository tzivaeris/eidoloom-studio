#!/usr/bin/env python3
"""Studio's tools for Codex: a stdio MCP server that Codex starts (as the `studio` server) during a Codex chat in Studio.

It holds no tools of its own. tools/list and tools/call go back to the Studio that started Codex
(STUDIO_BRIDGE_URL, with the run's one-time STUDIO_BRIDGE_TOKEN), which runs each call exactly as for its own models:
the approval card, the tool server's settings and secrets, Blender started on demand, progress, and pictures kept beside
the chat. Pictures come back to Codex as MCP image content, so it can look at them.

Standard library only.
"""
import json
import os
import sys
import urllib.error
import urllib.request

URL = os.environ.get("STUDIO_BRIDGE_URL", "").rstrip("/")
TOKEN = os.environ.get("STUDIO_BRIDGE_TOKEN", "")
TIMEOUT = float(os.environ.get("STUDIO_BRIDGE_TIMEOUT", "1900"))
LOCK = __import__("threading").Lock()


def studio(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(URL + path, data=data, method=method,
                                 headers={"X-Studio": "1", "X-Studio-Bridge": TOKEN, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            message = json.loads(exc.read() or b"{}").get("error") or str(exc)
        except ValueError:
            message = str(exc)
        raise RuntimeError(message) from None


def send(msg):
    with LOCK:
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", **msg}) + "\n")
        sys.stdout.flush()


def handle(msg):
    method, rid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if rid is None:                       # notifications (initialized, cancelled) need no answer
        return
    try:
        if method == "initialize":
            result = {"protocolVersion": params.get("protocolVersion") or "2025-06-18", "capabilities": {"tools": {"listChanged": False}},
                      "serverInfo": {"name": "strata-studio", "version": "1"},
                      "instructions": "Eidoloom Studio's tool servers (pictures, video, music, voices, sounds, audio, 3D, web, "
                                      "Greek checks, email). Each call is shown to the person in Studio's chat, which may ask them first."}
        elif method == "tools/list":
            result = {"tools": studio("GET", "/api/codex/bridge/tools").get("tools", [])}
        elif method == "tools/call":
            result = studio("POST", "/api/codex/bridge/call", {"name": params.get("name"), "arguments": params.get("arguments") or {}})
        elif method == "ping":
            result = {}
        else:
            return send({"id": rid, "error": {"code": -32601, "message": f"unknown method {method}"}})
    except (RuntimeError, OSError) as exc:
        if method == "tools/call":
            return send({"id": rid, "result": {"isError": True, "content": [{"type": "text", "text": f"Studio: {exc}"}]}})
        return send({"id": rid, "error": {"code": -32000, "message": f"Studio: {exc}"}})
    send({"id": rid, "result": result})


def main():
    import threading
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        # A call can take minutes (a song, a video), so each runs in its own thread and others are answered meanwhile.
        threading.Thread(target=handle, args=(msg,), daemon=True).start()


if __name__ == "__main__":
    main()
