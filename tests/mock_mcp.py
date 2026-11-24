#!/usr/bin/env python3
"""A small MCP server for the tests: over stdio by default, or over Streamable HTTP with --http PORT.

Tools: echo(text), add(a, b) with structured output only, fail (an error result), slow(seconds), picture (an image),
pinged (whether the client answered the server's ping), env(name). tools/list comes in two pages.
"""
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

NOTIFY = None
TOOLS = [
    {"name": "echo", "description": "Repeat the text.", "inputSchema": {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
                                                                      "properties": {"text": {"type": "string"}}, "required": ["text"], "additionalProperties": False}},
    {"name": "add", "description": "Add two numbers.", "inputSchema": {"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}}},
    {"name": "fail", "description": "Always fails.", "inputSchema": {"type": "object"}},
    {"name": "slow", "description": "Waits.", "inputSchema": {"type": "object", "properties": {"seconds": {"type": "number"}}}},
    {"name": "count", "description": "Counts to three, sending progress notifications when asked.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "picture", "description": "Returns a picture.", "inputSchema": {"type": "object"}},
    {"name": "pinged", "description": "Whether the client answered a ping.", "inputSchema": {"type": "object"}},
    {"name": "env", "description": "Reads an environment variable.", "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}}},
]
STATE = {"pinged": False, "calls": []}


def call(name, args):
    STATE["calls"].append([name, args])
    if "progress" in args:
        return {"content": [{"type": "text", "text": "the progress argument reached the server"}], "isError": True}
    if name == "echo":
        return {"content": [{"type": "text", "text": "echo: " + str(args.get("text"))}]}
    if name == "add":
        return {"content": [], "structuredContent": {"sum": args.get("a", 0) + args.get("b", 0)}}
    if name == "fail":
        return {"content": [{"type": "text", "text": "it broke"}], "isError": True}
    if name == "count":           # notifications/progress for the caller's progress token, if it sent one
        token = STATE.get("token")
        for n in range(1, 4):
            if token is not None and NOTIFY:
                NOTIFY({"jsonrpc": "2.0", "method": "notifications/progress", "params": {"progressToken": token, "progress": n, "total": 3, "message": f"counted {n} of 3"}})
            time.sleep(0.6)
        return {"content": [{"type": "text", "text": "counted to 3" + (" with progress" if token is not None else "")}]}
    if name == "slow":
        time.sleep(float(args.get("seconds", 5)))
        return {"content": [{"type": "text", "text": "slept"}]}
    if name == "picture":
        png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
        return {"content": [{"type": "image", "data": png, "mimeType": "image/png"}, {"type": "text", "text": "a red square"}]}
    if name == "pinged":
        return {"content": [{"type": "text", "text": "yes" if STATE["pinged"] else "no"}]}
    if name == "env":
        return {"content": [{"type": "text", "text": os.environ.get(str(args.get("name")), "(unset)")}]}
    return None


def answer(message):
    """The reply to one request from the client, or None for notifications and replies."""
    method, rid, params = message.get("method"), message.get("id"), message.get("params") or {}
    if method is None:
        if rid == "server-ping" and "result" in message:
            STATE["pinged"] = True
        return None
    if rid is None:
        return None
    if method == "initialize":
        result = {"protocolVersion": params.get("protocolVersion"), "capabilities": {"tools": {"listChanged": True}},
                  "serverInfo": {"name": "mock-mcp", "version": "0.1"}, "instructions": "A test server."}
    elif method == "tools/list":
        result = {"tools": TOOLS[:3], "nextCursor": "page2"} if not params.get("cursor") else {"tools": TOOLS[3:]}
    elif method == "tools/call":
        STATE["token"] = (params.get("_meta") or {}).get("progressToken")
        result = call(params.get("name"), params.get("arguments") or {})
        if result is None:
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": "unknown tool " + str(params.get("name"))}}
    else:
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "method not found"}}
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def serve_stdio():
    global NOTIFY
    lock = threading.Lock()

    def send(message):
        with lock:
            sys.stdout.write(json.dumps(message) + "\n")
            sys.stdout.flush()
    NOTIFY = send

    def handle(message):
        reply = answer(message)
        if reply:
            send(reply)
        if message.get("method") == "notifications/initialized":
            send({"jsonrpc": "2.0", "id": "server-ping", "method": "ping"})

    print("mock MCP server started", file=sys.stderr, flush=True)
    for line in sys.stdin:
        if line.strip():
            # Calls run on their own thread, so a slow one does not hold up the rest, as in real servers.
            threading.Thread(target=handle, args=(json.loads(line),), daemon=True).start()


class Http(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        if self.headers.get("Authorization") != "Bearer test-token":
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b"no token")
            return
        message = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
        reply = answer(message)
        if reply is None:
            self.send_response(202)
            self.end_headers()
            return
        if message.get("method") == "tools/call":       # answered as an event stream, with a notification first
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b'event: message\ndata: {"jsonrpc":"2.0","method":"notifications/progress","params":{}}\n\n')
            self.wfile.write(b"event: message\ndata: " + json.dumps(reply).encode() + b"\n\n")
            return
        body = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        if message.get("method") == "initialize":
            self.send_header("Mcp-Session-Id", "session-1")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_DELETE(self):
        self.send_response(200)
        self.end_headers()


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--http":
        ThreadingHTTPServer(("127.0.0.1", int(sys.argv[2])), Http).serve_forever()
    else:
        serve_stdio()
