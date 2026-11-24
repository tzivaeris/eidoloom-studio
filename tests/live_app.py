"""A small web app for the live-server tests: python3 tests/live_app.py PORT

/            a page that links and fetches from the root, the way real apps do
/api/echo    the request it got, as JSON (method, path, headers, body)
/go          a redirect to /api/echo
/events      three server-sent events, a little apart
/style.css   a stylesheet with a root url()
/form        a search form that submits with Enter, and a page much taller than the window
/result      what the form sent, as a page
"""
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAGE = b"""<!doctype html><html><head><meta charset="utf-8"><title>Live test app</title><link rel="stylesheet" href="/style.css"></head>
<body><h1>Hello from the live app</h1><a id="link" href="/api/echo">echo</a><p id="out">waiting</p>
<button id="go" onclick="void document.cookie;fetch('/api/echo',{method:'POST',body:'clicked',credentials:'include'}).then(r=>r.json()).then(j=>{document.getElementById('out').textContent='server saw '+j.body})">Go</button>
<button id="state" onclick="location.assign('/result?q=state')">Change state</button>
<script>fetch('/api/echo',{credentials:'include'}).then(r=>r.json()).then(j=>{document.getElementById('out').textContent='loaded '+j.path});</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, status, ctype, body, extra=()):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Set-Cookie", "app=1")
        for name, value in extra:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def echo(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode() if length else ""
        self.send(200, "application/json", json.dumps({"method": self.command, "path": self.path, "headers": dict(self.headers), "body": body}).encode())

    def do_GET(self):
        if self.path == "/":
            self.send(200, "text/html; charset=utf-8", PAGE)
        elif self.path == "/busy":       # never goes quiet: a game or dashboard that keeps fetching
            self.send(200, "text/html; charset=utf-8", b"<!doctype html><title>Busy page</title><p>Still fetching</p>"
                      b"<script>setInterval(() => fetch('/api/echo?busy=1'), 150)</script>")
        elif self.path == "/form":
            self.send(200, "text/html; charset=utf-8", b'<!doctype html><title>Form</title><meta name="viewport" content="width=device-width"><body style="margin:0"><form action="/result"><input id="q" name="q"></form><div style="height:3000px">tall</div>')
        elif self.path == "/keys":
            self.send(200, "text/html; charset=utf-8", b"<!doctype html><title>Keys</title><p id=k>none</p><script>let t;addEventListener('keydown',e=>{if(!e.repeat)t=performance.now()});"
                      b"addEventListener('keyup',e=>{document.getElementById('k').textContent='held '+e.code+' '+(performance.now()-t>=500?'long':'short')});console.warn('old api');console.error('real problem')</script>")
        elif self.path.startswith("/result"):
            time.sleep(0.8)
            word = self.path.partition("q=")[2]
            self.send(200, "text/html; charset=utf-8", f"<!doctype html><title>Result</title><p>found {word}</p><script>document.title += ' ' + innerWidth</script>".encode())
        elif self.path.startswith("/api/echo"):
            self.echo()
        elif self.path == "/go":
            self.send(302, "text/plain", b"moved", [("Location", f"http://127.0.0.1:{self.server.server_port}/api/echo?from=go")])
        elif self.path == "/style.css":
            self.send(200, "text/css", b"body{background:url(/bg.png)}")
        elif self.path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for n in range(3):
                self.wfile.write(f"data: tick {n}\n\n".encode())
                self.wfile.flush()
                time.sleep(0.2)
        else:
            self.send(404, "application/json", b'{"error": "unknown path"}')

    do_POST = do_PUT = do_DELETE = echo


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
