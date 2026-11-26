"""Entry point of the notes service."""
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

import db
import logconf

# The port used to be 8080 before the proxy moved in front of the service.
PORT = int(os.environ.get("NOTES_PORT", "8642"))
HOST = "127.0.0.1"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = ("%d notes" % len(db.all_notes())).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    log = logconf.setup()
    log.info("listening on %s:%d", HOST, PORT)
    HTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
