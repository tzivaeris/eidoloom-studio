#!/usr/bin/env python3
"""The agent's web browser: an MCP server (stdio) that is Microsoft's Playwright MCP driving a Chromium started here.

Started here so it is safe and can be watched:
  - a fresh, empty profile each time (no saved sign-ins), deleted when the server stops;
  - every request goes through a small proxy in this program that refuses addresses that are not public internet
    (this machine, the LAN, link-local, private and reserved ranges), checked on the address it actually connects to,
    so a name that resolves to 127.0.0.1 or a 192.168.x.x address is refused too; WebRTC may not go around it;
  - Chromium's DevTools port is on 127.0.0.1 and written to $BROWSER_STATE (session.json), so Studio's Browser tab
    can show the page live and pass on the user's clicks and typing.
Environment: BROWSER_STATE (folder for session.json and downloads; default ./browser-state), STUDIO_CHROMIUM (the
chrome binary; default the newest Playwright Chromium), BROWSER_ALLOW_PRIVATE=1 (tests only: allow 127.0.0.1),
PLAYWRIGHT_MCP (the npx package, default @playwright/mcp).
"""
import ctypes
import glob
import ipaddress
import json
import os
import select
import shutil
import signal
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request

STATE = os.path.abspath(os.environ.get("BROWSER_STATE") or "browser-state")
ALLOW_PRIVATE = os.environ.get("BROWSER_ALLOW_PRIVATE") == "1"
PACKAGE = os.environ.get("PLAYWRIGHT_MCP") or "@playwright/mcp"


def log(*parts):
    print("browser:", *parts, file=sys.stderr, flush=True)


def die_with_parent():
    """Children end when this program ends, even if it is killed."""
    try:
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGKILL)   # PR_SET_PDEATHSIG
    except OSError:
        pass


# ------------------------------------------------------------------ the proxy
def public(ip):
    addr = ipaddress.ip_address(ip)
    if getattr(addr, "ipv4_mapped", None):
        addr = addr.ipv4_mapped
    if ALLOW_PRIVATE and addr.is_loopback:
        return True
    return addr.is_global and not addr.is_multicast


def resolve(host, port):
    """The first public address for host:port, or raises PermissionError naming why."""
    infos = socket.getaddrinfo(host.strip("[]"), port, type=socket.SOCK_STREAM)
    addrs = [info[4] for info in infos]
    if not addrs:
        raise OSError("no address")
    blocked = [a[0] for a in addrs if not public(a[0])]
    if blocked:                                  # one private answer is enough to refuse: no DNS games
        raise PermissionError(f"{host} is {blocked[0]}, which is not on the public internet")
    return addrs[0]


def pipe(a, b):
    socks = [a, b]
    try:
        while True:
            ready, _, broken = select.select(socks, [], socks, 120)
            if broken or not ready:
                return
            for s in ready:
                data = s.recv(65536)
                if not data:
                    return
                (b if s is a else a).sendall(data)
    except OSError:
        return


class Proxy(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


class ProxyHandler(socketserver.BaseRequestHandler):
    def refuse(self, why):
        body = f"Studio's browser does not open addresses on this machine or its network: {why}. To check a page that the project serves on this machine, use Studio's view_page tool (and open_preview to show it to the person).\n".encode()
        self.request.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Length: "
                             + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)

    def handle(self):
        conn = self.request
        conn.settimeout(30)
        head = b""
        try:
            while b"\r\n\r\n" not in head and len(head) < 65536:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                head += chunk
        except OSError:
            return
        first, _, rest = head.partition(b"\r\n")
        try:
            method, target, version = first.decode("latin-1").split(" ", 2)
        except ValueError:
            return
        try:
            if method.upper() == "CONNECT":
                host, _, port = target.rpartition(":")
                addr = resolve(host, int(port))
                upstream = socket.create_connection(addr[:2], timeout=30)
                conn.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                extra = rest.partition(b"\r\n\r\n")[2]
                if extra:
                    upstream.sendall(extra)
            else:
                url = urllib.parse.urlsplit(target)
                if url.scheme != "http" or not url.hostname:
                    return self.refuse("only http and https pages")
                addr = resolve(url.hostname, url.port or 80)
                upstream = socket.create_connection(addr[:2], timeout=30)
                path = (url.path or "/") + (("?" + url.query) if url.query else "")
                block, _, body = rest.partition(b"\r\n\r\n")
                headers = [h for h in block.split(b"\r\n") if h and not h.lower().startswith((b"proxy-", b"connection:"))]
                upstream.sendall(f"{method} {path} {version}\r\n".encode("latin-1") + b"".join(h + b"\r\n" for h in headers)
                                 + b"Connection: close\r\n\r\n" + body)
        except PermissionError as e:
            return self.refuse(str(e))
        except (OSError, ValueError) as e:
            try:
                conn.sendall(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\nContent-Length: 0\r\n\r\n")
            except OSError:
                pass
            return log("proxy:", target, e)
        conn.settimeout(None)
        upstream.settimeout(None)
        try:
            pipe(conn, upstream)
        finally:
            upstream.close()


# ------------------------------------------------------------------ Chromium
def chromium():
    path = os.environ.get("STUDIO_CHROMIUM")
    if path:
        return path
    found = sorted(glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome")),
                   key=lambda p: int(p.split("chromium-")[1].split("/")[0]))
    if not found:
        raise SystemExit("no Chromium found: run `npx playwright install chromium` in Studio's tests/browser, or set STUDIO_CHROMIUM")
    return found[-1]


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def read_log():
    try:
        with open(os.path.join(STATE, "chromium.log")) as fh:
            return fh.read()
    except OSError:
        return ""


def launch(profile, port, proxy_url, sandbox):
    return subprocess.Popen([chromium(), "--headless=new", f"--remote-debugging-port={port}", "--remote-debugging-address=127.0.0.1",
                             f"--user-data-dir={profile}", f"--proxy-server={proxy_url}", "--proxy-bypass-list=<-loopback>",
                             "--force-webrtc-ip-handling-policy=disable_non_proxied_udp", "--no-first-run", "--no-default-browser-check",
                             "--disable-background-networking", "--disable-sync", "--window-size=1280,800", "--lang=el-GR,en-US",
                             *([] if sandbox else ["--no-sandbox"]), "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=open(os.path.join(STATE, "chromium.log"), "w"), preexec_fn=die_with_parent)


def wait_up(chrome, cdp):
    for _ in range(300):
        if chrome.poll() is not None:
            return False
        try:
            urllib.request.urlopen(cdp + "/json/version", timeout=1).read()
            return True
        except OSError:
            time.sleep(0.1)
    chrome.kill()
    return False


def main():
    os.makedirs(STATE, exist_ok=True)
    proxy = Proxy(("127.0.0.1", 0), ProxyHandler)
    threading.Thread(target=proxy.serve_forever, daemon=True).start()
    proxy_url = f"http://127.0.0.1:{proxy.server_address[1]}"
    profile = tempfile.mkdtemp(prefix="studio-browser-")
    port = free_port()
    chrome, sandboxed = launch(profile, port, proxy_url, sandbox=True), True
    cdp = f"http://127.0.0.1:{port}"
    if wait_up(chrome, cdp):
        time.sleep(1.5)                       # without a usable sandbox it answers first, then stops
    if chrome.poll() is not None and "No usable sandbox" in read_log():
        # Ubuntu's AppArmor blocks the user namespaces Chromium's sandbox needs (see sandbox/mcp-browser/README.md);
        # Playwright runs Chromium this way by default too.
        log("Chromium's sandbox is blocked here, so it runs without it")
        port = free_port()
        cdp = f"http://127.0.0.1:{port}"
        chrome, sandboxed = launch(profile, port, proxy_url, sandbox=False), False
        wait_up(chrome, cdp)
    if chrome.poll() is not None:
        raise SystemExit("Chromium did not start: " + " | ".join(read_log().strip().splitlines()[-3:]))
    session = os.path.join(STATE, "session.json")
    with open(session + ".tmp", "w") as fh:
        json.dump({"cdp": cdp, "pid": chrome.pid, "started": time.time(), "proxy": proxy_url, "sandboxed": sandboxed}, fh)
    os.replace(session + ".tmp", session)
    log("Chromium on", cdp, "through", proxy_url, "" if sandboxed else "(without its sandbox)")
    out_dir = os.path.join(STATE, "files")
    os.makedirs(out_dir, exist_ok=True)
    mcp = subprocess.Popen(["npx", "-y", PACKAGE, "--cdp-endpoint", cdp, "--console-level", "warning"], cwd=out_dir,
                           preexec_fn=die_with_parent)            # stdin and stdout are Studio's, passed straight through

    def stop(*_):
        for p in (mcp, chrome):
            if p.poll() is None:
                p.terminate()
        sys.exit(0)
    signal.signal(signal.SIGTERM, stop)
    try:
        mcp.wait()
    finally:
        for p in (mcp, chrome):
            if p.poll() is None:
                p.kill()
        try:
            os.remove(session)
        except OSError:
            pass
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    main()
