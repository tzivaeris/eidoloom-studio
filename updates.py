"""Updates for the third-party tools Studio uses (Account & settings → Updates, admins).

Each item says what is installed, what is newest, and how to update, test and roll back:
  docker   an image, its container recreated (Docker Compose, a plain `docker run`, or the tool servers that use it);
           the old image is kept as <repo>:studio-prev for rolling back. This machine's Docker cannot pull by itself
           (credsStore: desktop without its helper), so pulls use an empty DOCKER_CONFIG.
  npx      a tool server run by `npx -y <package>`: its npx cache folder is set aside, the server restarts and fetches
           the newest, and the folder comes back on failure.
  git      ComfyUI and the sound engine: git pull, pip with the AMD PyTorch build held where it is, the service restarted.
  pip      one package in a virtual environment (the voice engine, MCP for Blender), PyTorch held.
  blender  a new Blender release beside the old one, ~/.local/bin/blender moved to it, its settings and add-ons copied.
  npm      Playwright and its Chromium in tests/browser.
  weblib   a library Studio's page loads from jsdelivr at a fixed version (Monaco, xterm, three.js, KaTeX).
  apt      system packages: only listed, with the sudo command, since Studio cannot use sudo.
Automatic updates (settings `auto`, `window`) run only for items marked safe: Docker images that are not databases and do
not change one and npx tool servers, at night, and only while
Studio is idle. Every update is tested; a failed test rolls it back.
"""
import glob
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request

HOME = os.path.expanduser("~")
OPT = os.path.join(HOME, ".local", "opt")
UA = {"User-Agent": "eidoloom-studio-updates/1.0"}
TORCH = re.compile(r"^(torch|torchvision|torchaudio|pytorch-triton-rocm|triton)==", re.I)


def web(url, method="GET", headers=None, timeout=20):
    req = urllib.request.Request(url, method=method, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, dict(resp.headers), (resp.read() if method != "HEAD" else b"")


def web_json(url, headers=None):
    return json.loads(web(url, headers=headers)[2])


def answers(url, timeout=60, want=200):
    """True once url answers want within timeout seconds."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            if web(url, timeout=5)[0] == want:
                return True
        except (OSError, urllib.error.URLError, ValueError):
            pass
        time.sleep(2)
    return False


def catalog(root):
    """Everything Studio knows how to keep up to date, in the order the page shows it."""
    py = lambda name: os.path.join(OPT, name, ".venv", "bin", "python")
    return [
        # ---- Docker services and images
        {"id": "searxng", "group": "docker", "name": "SearXNG (web search)", "kind": "docker", "image": "searxng/searxng:latest",
         "container": "studio-searxng", "run": ["--restart", "unless-stopped", "-p", "127.0.0.1:8888:8080",
                                                "-v", os.path.join(root, "sandbox", "mcp-searxng", "config") + ":/etc/searxng"],
         "test": "http://127.0.0.1:8888/search?q=strata&format=json", "auto_ok": True},
        {"id": "languagetool", "group": "docker", "name": "LanguageTool (Greek checks)", "kind": "docker", "image": "erikvl87/languagetool:latest",
         "container": "studio-languagetool", "run": ["--restart", "unless-stopped", "-p", "127.0.0.1:8010:8010", "-e", "Java_Xms=256m", "-e", "Java_Xmx=1g"],
         "test": "http://127.0.0.1:8010/v2/languages", "test_wait": 120, "auto_ok": True},
        {"id": "mcp-fetch", "group": "docker", "name": "Fetch tool server", "kind": "docker", "image": "mcp/fetch:latest", "servers": ["fetch"], "auto_ok": True},
        {"id": "postgres-mcp", "group": "docker", "name": "Postgres MCP Pro tool server", "kind": "docker", "image": "crystaldba/postgres-mcp:latest",
         "servers": ["tutoring-db"], "auto_ok": True},
        {"id": "sandbox-image", "group": "docker", "name": "Sandbox image (strata-sandbox:universal)", "kind": "sandbox", "auto_ok": False,
         "dockerfile": os.path.join(root, "sandbox", "images", "universal.Dockerfile"),
         "note": "Rebuilt from its Dockerfile with fresh base images and tools (a large download). New sandboxes use it; running ones keep theirs until recreated."},
        # ---- Local models: the checkouts Tools & setup installed (or the config names); their own setup.sh updates them
        {"id": "polystrata", "group": "models", "name": "PolyStrata (local models)", "kind": "localmodel", "key": "polystrata_dir", "home": "terminal",
         "auto_ok": False, "note": "Gets the newest code, rebuilds the engine for the card and updates the models' settings (its own setup.sh --update). "
                                   "Its models stay; the earlier engine is kept for rolling back."},
        {"id": "strata", "group": "models", "name": "Strata", "kind": "localmodel", "key": "strata_dir", "home": "", "auto_ok": False,
         "note": "The same through Strata's own setup.sh --update. A Strata the Ollama Manager runs is updated by the Manager instead."},
        # ---- Tool servers and engines
        {"id": "context7", "group": "engines", "name": "Context7 tool server", "kind": "npx", "package": "@upstash/context7-mcp", "servers": ["context7"], "auto_ok": True},
        {"id": "playwright-mcp", "group": "engines", "name": "Browser tool server (Playwright MCP)", "kind": "npx", "package": "@playwright/mcp", "servers": ["browser"], "auto_ok": True},
        {"id": "mcp-searxng", "group": "engines", "name": "SearXNG tool server", "kind": "npx", "package": "mcp-searxng", "servers": ["web-search"], "auto_ok": True},
        {"id": "comfyui", "group": "engines", "name": "ComfyUI (pictures, music, video)", "kind": "git", "path": os.path.join(OPT, "comfyui"), "python": py("comfyui"),
         "pip": ["-r", "requirements.txt"], "service": "comfyui", "test": "http://127.0.0.1:8188/system_stats", "test_wait": 180, "auto_ok": False},
        {"id": "sound-engine", "group": "engines", "name": "Sound engine (MOSS-SoundEffect)", "kind": "git", "path": os.path.join(OPT, "sound", "src"), "python": py("sound"),
         "pip": ["--no-deps", "-e", "moss_soundeffect_v2"], "service": "strata-sound", "test": "http://127.0.0.1:8190/health", "auto_ok": False},
        {"id": "voice-engine", "group": "engines", "name": "Voice engine (VoxCPM2)", "kind": "pip", "python": py("voice"), "package": "voxcpm",
         "service": "strata-voice", "test": "http://127.0.0.1:8189/health", "auto_ok": False},
        {"id": "blender-mcp", "group": "engines", "name": "MCP for Blender", "kind": "pip", "python": os.path.join(OPT, "mcp-for-blender", "bin", "python"),
         "package": "mcp-for-blender", "servers": ["blender"], "auto_ok": False},
        # ---- Programs and web libraries
        {"id": "blender", "group": "programs", "name": "Blender", "kind": "blender", "auto_ok": False,
         "note": "Downloads the new release (about 400 MB) beside the old one; the old one stays for rolling back."},
        {"id": "codex", "group": "programs", "name": "Codex CLI (OpenAI)", "kind": "codex", "auto_ok": False,
         "note": "New Codex versions bring new models to the picker. The earlier version stays beside the new one for rolling back."},
        {"id": "playwright", "group": "programs", "name": "Playwright and Chromium (view_page, tests)", "kind": "npm", "path": os.path.join(root, "tests", "browser"),
         "package": "playwright", "auto_ok": False},
        {"id": "monaco", "group": "programs", "name": "Monaco editor", "kind": "weblib", "package": "monaco-editor", "check": "min/vs/loader.js", "auto_ok": False},
        {"id": "xterm", "group": "programs", "name": "xterm.js terminal", "kind": "weblib", "package": "@xterm/xterm", "check": "lib/xterm.js", "auto_ok": False},
        {"id": "xterm-fit", "group": "programs", "name": "xterm.js fit add-on", "kind": "weblib", "package": "@xterm/addon-fit", "check": "lib/addon-fit.js", "auto_ok": False},
        {"id": "three", "group": "programs", "name": "three.js (3D viewer)", "kind": "weblib", "package": "three", "check": "build/three.module.js", "auto_ok": False},
        {"id": "katex", "group": "programs", "name": "KaTeX (math in replies)", "kind": "weblib", "package": "katex", "check": "dist/katex.min.js", "auto_ok": False},
        # ---- System packages
        {"id": "system", "group": "system", "name": "System packages", "kind": "apt", "auto_ok": False,
         "packages": ["ffmpeg", "libreoffice-core", "libreoffice-writer", "libreoffice-calc", "poppler-utils", "xvfb", "nodejs", "docker.io",
                      "docker-ce", "docker-ce-cli", "containerd.io", "docker-compose-v2", "docker-compose-plugin", "caddy", "git", "python3"]},
    ]


GROUPS = [("models", "Local models"), ("docker", "Docker services and images"), ("engines", "Tool servers and engines"), ("programs", "Programs and web libraries"),
          ("system", "System packages")]


class Fail(Exception):
    pass


def test_catalog():
    """Two fake items, for Studio's tests (config updates_catalog "test"): nothing on this machine is touched."""
    return [{"id": "fake-safe", "group": "docker", "name": "Fake service", "kind": "fake", "auto_ok": True, "version": "1.0", "newest": "1.1"},
            {"id": "fake-manual", "group": "engines", "name": "Fake engine", "kind": "fake", "auto_ok": False, "version": "2.0", "newest": "2.0", "note": "Waits for a click."},
            {"id": "fake-apt", "group": "system", "name": "System packages", "kind": "apt", "fake": True, "auto_ok": False}]


class Updates:
    def __init__(self, data_dir, root, hooks):
        self.root, self.hooks = root, hooks
        self.dir = os.path.join(data_dir, "updates")
        os.makedirs(self.dir, exist_ok=True)
        self.path = os.path.join(self.dir, "state.json")
        self.lock = threading.RLock()
        self.items = {i["id"]: i for i in (test_catalog() if hooks.get("test") else catalog(root))}
        try:
            with open(self.path) as fh:
                self.state = json.load(fh)
        except (OSError, ValueError):
            self.state = {}
        self.state.setdefault("settings", {"auto": True, "window": [3, 5]})
        self.state.setdefault("items", {})
        self.state.setdefault("history", [])
        self.job = None

    # ------------------------------------------------------------------ state
    def save(self):
        with self.lock:
            tmp = self.path + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(self.state, fh, indent=1)
            os.replace(tmp, self.path)

    def info(self, iid):
        return self.state["items"].setdefault(iid, {})

    def auto_on(self, iid):
        return self.items[iid]["auto_ok"] and self.info(iid).get("auto", True)

    def public(self):
        with self.lock:
            out = []
            for iid, item in self.items.items():
                st = self.info(iid)
                out.append({"id": iid, "name": item["name"], "group": item["group"], "kind": item["kind"], "note": item.get("note", ""),
                            "auto_ok": item["auto_ok"], "auto": self.auto_on(iid), "current": st.get("current"), "latest": st.get("latest"),
                            "available": bool(st.get("available")), "detail": st.get("detail", ""), "error": st.get("error", ""),
                            "command": st.get("command", ""), "packages": st.get("packages", []), "checked": st.get("checked"),
                            "last": st.get("last"), "can_rollback": bool(st.get("prev")), "absent": bool(st.get("absent"))})
            return {"settings": self.state["settings"], "items": out, "groups": GROUPS, "job": self.job,
                    "last_check": self.state.get("last_check"), "history": self.state["history"][-40:][::-1],
                    "available": sum(1 for i in out if i["available"])}

    def remember(self, iid, action, ok, message, auto=False):
        with self.lock:
            entry = {"at": time.time(), "id": iid, "name": self.items[iid]["name"], "action": action, "ok": ok, "message": message[:600], "auto": auto}
            self.info(iid)["last"] = entry
            self.state["history"] = (self.state["history"] + [entry])[-200:]
            self.save()

    def log(self, line):
        if self.job is not None:
            self.job["log"] = (self.job["log"] + [line[:400]])[-60:]

    def sh(self, argv, cwd=None, env=None, timeout=900, check=True, quiet=False):
        if not quiet:
            self.log("$ " + " ".join(shlex.quote(a) for a in argv))
        try:
            done = subprocess.run(argv, cwd=cwd, env={**os.environ, **(env or {})}, capture_output=True, text=True, timeout=timeout)
        except FileNotFoundError:
            raise Fail(f"{argv[0]} is not installed")
        except subprocess.TimeoutExpired:
            raise Fail(f"{argv[0]} took longer than {timeout // 60} minutes")
        out = (done.stdout + done.stderr).strip()
        for line in ([] if quiet else out.splitlines()[-6:]):
            self.log("  " + line)
        if check and done.returncode != 0:
            raise Fail(f"{' '.join(argv[:3])} exited {done.returncode}: {out[-300:]}")
        return done.returncode, out

    # ------------------------------------------------------------------ checking
    def check_all(self):
        for iid in self.items:
            self.check(iid)
        with self.lock:
            self.state["last_check"] = time.time()
            self.save()

    def absent(self, item):
        """Whether the tool is not on this machine at all, so there is nothing to update (a new machine has most of them
        missing; they were listed as failed checks)."""
        kind = item["kind"]
        if self.hooks.get("test") or kind in ("weblib", "apt", "fake"):
            return False
        if kind in ("docker", "sandbox"):
            return not shutil.which("docker") or self.docker_local(item["image"] if kind == "docker" else "strata-sandbox:universal") is None
        if kind == "git":
            return not os.path.isdir(os.path.join(item["path"], ".git"))
        if kind == "pip":
            return not os.path.exists(item["python"])
        if kind == "localmodel":
            return not self.local_path(item)
        if kind == "npx":
            return self.npx_dir(item["package"])[1] is None
        if kind == "npm":
            return not os.path.exists(os.path.join(item["path"], "node_modules", item["package"], "package.json"))
        if kind == "blender":
            return self.blender_now()[0] is None
        if kind == "codex":
            return self.codex_version() is None
        return False

    def check(self, iid):
        item, st = self.items[iid], self.info(iid)
        if self.absent(item):
            st.update({"current": "not installed here", "latest": None, "detail": "", "error": "", "available": False, "absent": True,
                       "checked": time.time()})
            with self.lock:
                self.save()
            return
        st.pop("absent", None)
        try:
            found = getattr(self, "check_" + item["kind"])(item)
            st.update({"current": None, "latest": None, "detail": "", "command": "", "packages": [], **found, "error": "", "checked": time.time()})
        except Exception as e:                   # one tool's failure must not stop the others
            st.update({"error": str(e)[:300], "checked": time.time(), "available": False})
        with self.lock:
            self.save()

    def docker_local(self, image):
        code, out = self.sh(["docker", "image", "inspect", image], check=False, quiet=True)
        if code != 0:
            return None
        info = (json.loads(out) or [{}])[0]
        labels = (info.get("Config") or {}).get("Labels") or {}
        return {"digests": info.get("RepoDigests") or [], "created": (info.get("Created") or "")[:10],
                "version": labels.get("org.opencontainers.image.version", ""), "id": info.get("Id", "")}

    def hub_digest(self, image):
        repo, tag = image.rsplit(":", 1) if ":" in image.split("/")[-1] else (image, "latest")
        name = repo if "/" in repo else "library/" + repo
        token = web_json(f"https://auth.docker.io/token?service=registry.docker.io&scope=repository:{name}:pull")["token"]
        accept = ", ".join(["application/vnd.oci.image.index.v1+json", "application/vnd.docker.distribution.manifest.list.v2+json",
                            "application/vnd.docker.distribution.manifest.v2+json", "application/vnd.oci.image.manifest.v1+json"])
        _, headers, _ = web(f"https://registry-1.docker.io/v2/{name}/manifests/{tag}", method="HEAD",
                            headers={"Authorization": "Bearer " + token, "Accept": accept})
        digest = {k.lower(): v for k, v in headers.items()}.get("docker-content-digest", "")
        when = ""
        try:
            when = (web_json(f"https://hub.docker.com/v2/repositories/{name}/tags/{tag}").get("last_updated") or "")[:10]
        except (OSError, ValueError, urllib.error.URLError):
            pass
        return digest, when

    def check_docker(self, item):
        local = self.docker_local(item["image"])
        digest, when = self.hub_digest(item["image"])
        if not local:
            return {"current": "not installed", "latest": when or digest[7:19], "available": False, "detail": "This image is not on this machine."}
        current = local["version"] or f"image of {local['created']}"
        have = any(d.endswith("@" + digest) for d in local["digests"])
        return {"current": current, "latest": "same" if have else (f"image of {when}" if when else digest[7:19]), "available": bool(digest) and not have}

    def check_sandbox(self, item):
        local = self.docker_local("strata-sandbox:universal")
        built = local["created"] if local else None
        age = (time.time() - time.mktime(time.strptime(built, "%Y-%m-%d"))) / 86400 if built else None
        return {"current": f"built {built}" if built else "not built", "latest": "rebuild for fresh tools", "available": age is None or age > 30,
                "detail": "Offered once the image is more than 30 days old." if age is not None else ""}

    def npx_dir(self, package):
        found = [d for d in glob.glob(os.path.join(HOME, ".npm", "_npx", "*", "node_modules", *package.split("/"), "package.json"))]
        if not found:
            return None, None
        newest = max(found, key=os.path.getmtime)
        with open(newest) as fh:
            version = json.load(fh).get("version")
        return newest.split(os.sep + "node_modules" + os.sep)[0], version

    def check_npx(self, item):
        _, current = self.npx_dir(item["package"])
        latest = web_json(f"https://registry.npmjs.org/{item['package']}/latest")["version"]
        return {"current": current or "not fetched yet", "latest": latest, "available": bool(current) and current != latest}

    def check_git(self, item):
        path = item["path"]
        if not os.path.isdir(os.path.join(path, ".git")):
            raise Fail(f"{path} is not a git checkout")
        self.sh(["git", "-C", path, "fetch", "--quiet", "--tags"], timeout=120)
        here = self.sh(["git", "-C", path, "log", "-1", "--format=%h %cs"])[1]
        there = self.sh(["git", "-C", path, "log", "-1", "--format=%h %cs", "@{u}"])[1]
        behind = int(self.sh(["git", "-C", path, "rev-list", "--count", "HEAD..@{u}"])[1] or 0)
        tag = self.sh(["git", "-C", path, "describe", "--tags", "--abbrev=0", "@{u}"], check=False)[1].splitlines()
        return {"current": here, "latest": there + (f" ({tag[-1]})" if tag and not tag[-1].startswith("fatal") else ""), "available": behind > 0,
                "detail": f"{behind} new commit{'s' if behind != 1 else ''}" if behind else ""}

    def local_path(self, item):
        """The checkout Studio starts the item's models from (config strata_dir / polystrata_dir), or None."""
        get = self.hooks.get("config_get")
        path = os.path.expanduser((get(item["key"]) if get else "") or "")
        return path if path and os.path.isdir(os.path.join(path, ".git")) else None

    def check_localmodel(self, item):
        path = self.local_path(item)
        if not path:
            return {"current": "not installed by Studio", "latest": "", "available": False,
                    "detail": "Install it in Tools & setup; a Strata the Ollama Manager runs is updated there."}
        self.sh(["git", "-C", path, "fetch", "--quiet", "--tags", "origin"], timeout=300)
        branch = self.sh(["git", "-C", path, "rev-parse", "--abbrev-ref", "origin/HEAD"], check=False)[1].strip() or "origin/main"
        here = self.sh(["git", "-C", path, "log", "-1", "--format=%h %cs"])[1]
        there = self.sh(["git", "-C", path, "log", "-1", "--format=%h %cs", branch])[1]
        behind = int(self.sh(["git", "-C", path, "rev-list", "--count", f"HEAD..{branch}"])[1] or 0)
        tag = self.sh(["git", "-C", path, "describe", "--tags", "--abbrev=0", branch], check=False)[1].splitlines()
        return {"current": here, "latest": there + (f" ({tag[-1]})" if tag and not tag[-1].startswith("fatal") else ""), "available": behind > 0,
                "detail": f"{behind} new commit{'s' if behind != 1 else ''}" if behind else ""}

    def update_localmodel(self, item):
        path = self.local_path(item)
        if not path:
            raise Fail("not installed by Studio")
        busy = self.hooks["busy"]()
        if busy:
            raise Fail(f"not now: {busy}")
        stop = self.hooks.get("local_stop")
        if stop:
            stop()                              # the server must not run the engine being rebuilt
        before = self.sh(["git", "-C", path, "rev-parse", "HEAD"])[1].strip()
        self.info(item["id"])["prev"] = {"commit": before}
        branch = self.sh(["git", "-C", path, "rev-parse", "--abbrev-ref", "origin/HEAD"], check=False)[1].strip() or "origin/main"
        self.sh(["git", "-C", path, "checkout", "--quiet", "-B", branch.split("/", 1)[1], branch], timeout=300)
        self.sh([os.path.join(path, "setup.sh"), "--update", "--yes", "--no-start", "--no-browser"], cwd=path, timeout=6 * 3600,
                env={"PYTHONUNBUFFERED": "1", "NO_COLOR": "1"})
        return f"Updated to {self.sh(['git', '-C', path, 'log', '-1', '--format=%h %cs'])[1]}; its models start with the new engine next time."

    def rollback_localmodel(self, item, prev):
        path = self.local_path(item)
        if not path or not prev.get("commit"):
            raise Fail("no earlier version is recorded")
        busy = self.hooks["busy"]()
        if busy:
            raise Fail(f"not now: {busy}")
        stop = self.hooks.get("local_stop")
        if stop:
            stop()
        # Strata from 0.2.16 keeps its server, engine and profiles in terminal/: an older commit without it would leave the
        # built engine and the profiles where its code does not look (tried on a fresh machine on 2026-11-28, the list of
        # models went empty), so a rollback across that move is refused before anything changes.
        here = lambda rev: self.sh(["git", "-C", path, "cat-file", "-e", f"{rev}:terminal/serve/server.py"], check=False)[0] == 0
        if here("HEAD") != here(prev["commit"]):
            raise Fail("the update moved Strata's server into terminal/, so it cannot go back to the version before; nothing was changed")
        now = self.sh(["git", "-C", path, "rev-parse", "HEAD"])[1].strip()
        self.sh(["git", "-C", path, "checkout", "--quiet", prev["commit"]], timeout=300)
        try:
            self.sh([os.path.join(path, "setup.sh"), "--rollback-engine", "--yes"], cwd=path, timeout=1800, env={"NO_COLOR": "1"})
        except Fail as e:
            self.sh(["git", "-C", path, "checkout", "--quiet", now], timeout=300)        # the code goes back with the engine it was built for
            raise Fail(f"the earlier engine could not be put back ({e}); Strata stays as it was")
        return f"Back to {self.sh(['git', '-C', path, 'log', '-1', '--format=%h %cs'])[1]} and its earlier engine."

    def pip_version(self, item):
        code, out = self.sh([item["python"], "-m", "pip", "show", item["package"]], check=False)
        m = re.search(r"^Version:\s*(\S+)", out, re.M)
        return m.group(1) if code == 0 and m else None

    def check_pip(self, item):
        current = self.pip_version(item)
        latest = web_json(f"https://pypi.org/pypi/{item['package']}/json")["info"]["version"]
        return {"current": current or "not installed", "latest": latest, "available": bool(current) and current != latest}

    def blender_now(self):
        link = os.path.join(HOME, ".local", "bin", "blender")
        target = os.path.realpath(link) if os.path.islink(link) else ""
        m = re.search(r"blender-(\d+\.\d+\.\d+)-linux-x64", target)
        return (m.group(1) if m else None), link, target

    def blender_latest(self):
        listing = web("https://download.blender.org/release/")[2].decode("utf-8", "replace")
        series = sorted({tuple(map(int, m)) for m in re.findall(r'href="Blender(\d+)\.(\d+)/"', listing)})
        for major, minor in reversed(series[-3:]):
            page = web(f"https://download.blender.org/release/Blender{major}.{minor}/")[2].decode("utf-8", "replace")
            files = re.findall(rf'href="(blender-({major}\.{minor}\.\d+)-linux-x64\.tar\.xz)"', page)
            if files:
                name, version = max(files, key=lambda f: tuple(map(int, f[1].split("."))))
                return version, f"https://download.blender.org/release/Blender{major}.{minor}/{name}"
        raise Fail("no Linux release found on download.blender.org")

    def check_blender(self, item):
        current, _, _ = self.blender_now()
        latest, url = self.blender_latest()
        size = ""
        try:
            length = int({k.lower(): v for k, v in web(url, method="HEAD")[1].items()}.get("content-length", 0))
            size = f"a {length // 1048576} MB download" if length else ""
        except (OSError, ValueError, urllib.error.URLError):
            pass
        newer = bool(current) and tuple(map(int, latest.split("."))) > tuple(map(int, current.split(".")))
        major = newer and latest.split(".")[0] != current.split(".")[0]
        return {"current": current or "not found", "latest": latest, "available": newer,
                "detail": ", ".join(filter(None, [size, "a new major version: check the Blender tools afterwards" if major else ""]))}

    def check_npm(self, item):
        try:
            with open(os.path.join(item["path"], "node_modules", item["package"], "package.json")) as fh:
                current = json.load(fh)["version"]
        except (OSError, ValueError, KeyError):
            current = None
        latest = web_json(f"https://registry.npmjs.org/{item['package']}/latest")["version"]
        return {"current": current or "not installed", "latest": latest, "available": bool(current) and current != latest}

    def web_files(self):
        folder = os.path.join(self.root, "web")
        return [os.path.join(folder, n) for n in sorted(os.listdir(folder)) if n.endswith((".html", ".js", ".css"))]

    def pinned(self, package):
        pattern = re.compile(re.escape(package) + r"@(\d+\.\d+\.\d+)")
        found = set()
        for path in self.web_files():
            with open(path, encoding="utf-8") as fh:
                found.update(pattern.findall(fh.read()))
        return sorted(found)

    def check_weblib(self, item):
        pins = self.pinned(item["package"])
        latest = web_json(f"https://registry.npmjs.org/{item['package']}/latest")["version"]
        current = pins[-1] if pins else None
        detail = ""
        if current and current != latest:
            a, b = current.split("."), latest.split(".")
            if a[0] != b[0] or (a[0] == "0" and a[1] != b[1]):
                detail = "a version with breaking changes: reload and try it, and roll back if anything is wrong"
        return {"current": current or "not used", "latest": latest, "available": bool(current) and current != latest, "detail": detail}

    def check_apt(self, item):
        if item.get("fake"):
            return self.check_apt_fake()
        lists = os.path.join("/var", "lib", "apt", "lists")
        stamp = max((os.path.getmtime(p) for p in glob.glob(os.path.join(lists, "*Packages*"))), default=0)
        code, out = self.sh(["apt", "list", "--upgradable"], check=False, timeout=120)
        wanted, rows = set(item["packages"]), []
        for line in out.splitlines():
            m = re.match(r"^([^/\s]+)/\S+\s+(\S+)\s+\S+\s+\[upgradable from: ([^\]]+)\]", line)
            if m and m.group(1) in wanted:
                rows.append({"name": m.group(1), "current": m.group(3), "latest": m.group(2)})
        names = " ".join(r["name"] for r in rows)
        return {"current": f"{len(rows)} to update" if rows else "up to date", "latest": "", "available": bool(rows), "packages": rows,
                "command": f"sudo apt update && sudo apt install --only-upgrade {names}" if rows else "sudo apt update && apt list --upgradable",
                "detail": "As of the last apt update on " + time.strftime("%Y-%m-%d", time.localtime(stamp)) + "; Studio cannot use sudo, so run the command yourself."}

    # ------------------------------------------------------------------ updating
    def start(self, iid, action="update", auto=False):
        """Runs an update or a rollback in the background; one at a time."""
        with self.lock:
            if self.job and not self.job.get("done"):
                raise Fail(f"{self.job['name']} is being updated; wait for it")
            if iid not in self.items:
                raise Fail("unknown item")
            if self.items[iid]["kind"] == "apt":
                raise Fail("system packages need sudo: run the command shown")
            self.job = {"id": iid, "name": self.items[iid]["name"], "action": action, "started": time.time(), "log": [], "done": False, "ok": None, "auto": auto}
        threading.Thread(target=self.run_job, args=(iid, action, auto), daemon=True).start()
        return self.job

    def run_job(self, iid, action, auto):
        item = self.items[iid]
        try:
            if action == "rollback":
                message = getattr(self, "rollback_" + item["kind"])(item, self.info(iid).get("prev") or {})
                self.info(iid).pop("prev", None)
            else:
                busy = self.hooks["busy"]()
                if busy and auto:
                    raise Fail(f"waiting: {busy}")
                message = getattr(self, "update_" + item["kind"])(item)
            ok = True
        except Exception as e:
            ok, message = False, str(e)
        self.remember(iid, action, ok, message, auto)
        self.check(iid)                              # before done, so the page shows the new state with the result
        self.job.update(done=True, ok=ok, message=message, ended=time.time())

    def tested(self, item, rollback):
        """Runs the item's test; on failure rolls back and raises."""
        ok, why = self.test(item)
        if ok:
            return
        self.log("Test failed: " + why + "; rolling back")
        try:
            rollback()
            again, _ = self.test(item)
            raise Fail(f"the new version failed its test ({why}), so the earlier one was put back" + ("" if again else ", but that one does not answer either; look at it"))
        except Fail:
            raise
        except Exception as e:
            raise Fail(f"the new version failed its test ({why}) and rolling back failed too: {e}")

    def test(self, item):
        for name in item.get("servers", []):
            if self.hooks["server_enabled"](name) and not self.hooks["server_ready"](name, 90):
                return False, f"the {name} tool server did not start with its tools"
        if item.get("test") and not answers(item["test"], item.get("test_wait", 60)):
            return False, f"{item['test']} did not answer"
        return True, ""

    def restart_servers(self, item):
        for name in item.get("servers", []):
            if self.hooks["server_enabled"](name):
                self.log(f"Restarting the {name} tool server")
                self.hooks["restart_server"](name)

    # docker
    def pull(self, image):
        with tempfile.TemporaryDirectory() as empty:            # this Docker's own credential helper is missing
            self.sh(["docker", "pull", image], env={"DOCKER_CONFIG": empty}, timeout=1800)

    def recreate(self, item):
        if item.get("compose"):
            folder, service = item["compose"]
            self.sh(["docker", "compose", "up", "-d", "--no-deps", service], cwd=folder, timeout=300)
        elif item.get("container"):
            self.sh(["docker", "rm", "-f", item["container"]], check=False)
            self.sh(["docker", "run", "-d", "--name", item["container"], *item["run"], item["image"]], timeout=300)
        self.restart_servers(item)

    def backup_db(self, item):
        if not item.get("backup_db"):
            return ""
        target = os.path.join(self.dir, "backups", f"{item['backup_db']}-{time.strftime('%Y%m%d-%H%M%S')}.sql")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        self.log(f"Saving the database to {target}")
        with open(target, "wb") as fh:
            done = subprocess.run(["docker", "exec", item["backup_db"], "pg_dumpall", "-U", "listmonk"], stdout=fh, stderr=subprocess.PIPE, timeout=900)
        if done.returncode != 0:
            os.remove(target)
            raise Fail("could not save the database first, so nothing was updated: " + done.stderr.decode("utf-8", "replace")[-300:])
        os.chmod(target, 0o600)
        return target

    def update_docker(self, item):
        before = self.docker_local(item["image"])
        saved = self.backup_db(item)
        prev = item["image"].rsplit(":", 1)[0] + ":studio-prev"
        if before:
            self.sh(["docker", "tag", before["id"], prev])
        self.pull(item["image"])
        after = self.docker_local(item["image"])
        if before and after and after["id"] == before["id"]:
            return "Already the newest image."
        self.info(item["id"])["prev"] = {"image": prev, "backup": saved}
        self.recreate(item)

        def back():
            self.sh(["docker", "tag", prev, item["image"]])
            self.recreate(item)
        self.tested(item, back)
        return f"Updated to {after['version'] or 'the image of ' + after['created']}" + (f"; the database was saved first in {saved}" if saved else "") + "."

    def rollback_docker(self, item, prev):
        if not prev.get("image"):
            raise Fail("no earlier image is kept")
        self.sh(["docker", "tag", prev["image"], item["image"]])
        self.recreate(item)
        ok, why = self.test(item)
        note = (f" If the service's database was changed by the newer version, restore it from {prev['backup']}." if prev.get("backup") else "")
        if not ok:
            raise Fail(f"rolled back, but {why}.{note}")
        return "Rolled back to the earlier image." + note

    def update_sandbox(self, item):
        folder = os.path.dirname(item["dockerfile"])
        with tempfile.TemporaryDirectory() as empty:
            self.sh(["docker", "build", "--pull", "-t", "strata-sandbox:universal", "-f", item["dockerfile"], folder],
                    env={"DOCKER_CONFIG": empty}, timeout=3600)
        return "Rebuilt the sandbox image; new sandboxes use it."

    def rollback_sandbox(self, item, prev):
        raise Fail("the sandbox image has no earlier copy to go back to")

    # npx
    def update_npx(self, item):
        folder, before = self.npx_dir(item["package"])
        if not folder:
            raise Fail("npx has not fetched it yet; it does so the first time the server starts")
        prev = folder + ".studio-prev"
        shutil.rmtree(prev, ignore_errors=True)
        os.rename(folder, prev)
        self.info(item["id"])["prev"] = {"folder": folder, "saved": prev, "version": before}
        self.restart_servers(item)

        def back():
            new, _ = self.npx_dir(item["package"])
            if new and new != folder:
                shutil.rmtree(new, ignore_errors=True)
            shutil.rmtree(folder, ignore_errors=True)
            os.rename(prev, folder)
            self.restart_servers(item)
        self.tested(item, back)
        _, now = self.npx_dir(item["package"])
        return f"Updated from {before} to {now}."

    def rollback_npx(self, item, prev):
        if not prev.get("saved") or not os.path.isdir(prev["saved"]):
            raise Fail("no earlier version is kept")
        new, _ = self.npx_dir(item["package"])
        for path in {new, prev["folder"]} - {None}:
            shutil.rmtree(path, ignore_errors=True)
        os.rename(prev["saved"], prev["folder"])
        self.restart_servers(item)
        return f"Rolled back to {prev.get('version')}."

    # python environments
    def hold_torch(self, python):
        """A constraints file keeping the AMD PyTorch build exactly as installed."""
        _, out = self.sh([python, "-m", "pip", "freeze"])
        held = [line for line in out.splitlines() if TORCH.match(line)]
        path = os.path.join(self.dir, "torch-constraints.txt")
        with open(path, "w") as fh:
            fh.write("\n".join(held) + "\n")
        return path, out

    def restart_service(self, item):
        if item.get("service"):
            self.sh(["systemctl", "--user", "restart", item["service"]], timeout=180)
        self.restart_servers(item)

    def update_git(self, item):
        path, python = item["path"], item["python"]
        busy = self.hooks["busy"]()
        if busy:
            raise Fail(f"not now: {busy}")
        constraints, freeze = self.hold_torch(python)
        frozen = os.path.join(self.dir, f"{item['id']}-pip-{time.strftime('%Y%m%d-%H%M%S')}.txt")
        with open(frozen, "w") as fh:
            fh.write("\n".join(line for line in freeze.splitlines() if not TORCH.match(line) and not line.startswith("-e ")) + "\n")
        before = self.sh(["git", "-C", path, "rev-parse", "HEAD"])[1].strip()
        self.info(item["id"])["prev"] = {"commit": before, "freeze": frozen}
        self.sh(["git", "-C", path, "pull", "--ff-only"], timeout=300)
        self.sh([python, "-m", "pip", "install", "-c", constraints, *item["pip"]], cwd=path, timeout=1800)
        self.restart_service(item)
        self.tested(item, lambda: self.rollback_git(item, self.info(item["id"])["prev"]))
        return f"Updated to {self.sh(['git', '-C', path, 'log', '-1', '--format=%h %cs'])[1]}."

    def rollback_git(self, item, prev):
        if not prev.get("commit"):
            raise Fail("no earlier version is recorded")
        path, python = item["path"], item["python"]
        self.sh(["git", "-C", path, "reset", "--hard", prev["commit"]])
        constraints, _ = self.hold_torch(python)
        if prev.get("freeze") and os.path.isfile(prev["freeze"]):
            self.sh([python, "-m", "pip", "install", "-c", constraints, "-r", prev["freeze"]], cwd=path, timeout=1800)
        self.sh([python, "-m", "pip", "install", "-c", constraints, *item["pip"]], cwd=path, timeout=1800)
        self.restart_service(item)
        return f"Rolled back to {prev['commit'][:9]}."

    def update_pip(self, item):
        busy = self.hooks["busy"]()
        if busy:
            raise Fail(f"not now: {busy}")
        before = self.pip_version(item)
        constraints, _ = self.hold_torch(item["python"])
        self.info(item["id"])["prev"] = {"version": before}
        self.sh([item["python"], "-m", "pip", "install", "-U", "-c", constraints, item["package"]], timeout=1800)
        self.restart_service(item)
        self.tested(item, lambda: self.rollback_pip(item, {"version": before}))
        return f"Updated from {before} to {self.pip_version(item)}."

    def rollback_pip(self, item, prev):
        if not prev.get("version"):
            raise Fail("no earlier version is recorded")
        constraints, _ = self.hold_torch(item["python"])
        self.sh([item["python"], "-m", "pip", "install", "-c", constraints, f"{item['package']}=={prev['version']}"], timeout=1800)
        self.restart_service(item)
        return f"Rolled back to {prev['version']}."

    # blender
    def update_blender(self, item):
        current, link, target = self.blender_now()
        latest, url = self.blender_latest()
        folder = os.path.join(OPT, f"blender-{latest}-linux-x64")
        if not os.path.isdir(folder):
            archive = os.path.join(OPT, os.path.basename(url))
            self.log(f"Downloading {url}")
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as resp, open(archive + ".part", "wb") as fh:
                shutil.copyfileobj(resp, fh, 1 << 20)
            os.replace(archive + ".part", archive)
            self.sh(["tar", "-xf", archive, "-C", OPT], timeout=900)
            os.remove(archive)
        binary = os.path.join(folder, "blender")
        self.sh([binary, "-b", "--factory-startup", "--version"], timeout=120)
        old_series, new_series = ".".join((current or "0.0").split(".")[:2]), ".".join(latest.split(".")[:2])
        config = os.path.join(HOME, ".config", "blender")
        if old_series != new_series and os.path.isdir(os.path.join(config, old_series)) and not os.path.exists(os.path.join(config, new_series)):
            self.log(f"Copying Blender's settings and add-ons from {old_series} to {new_series}")
            shutil.copytree(os.path.join(config, old_series), os.path.join(config, new_series), symlinks=True)
        self.info(item["id"])["prev"] = {"target": target, "version": current}
        tmp = link + ".new"
        if os.path.lexists(tmp):
            os.remove(tmp)
        os.symlink(binary, tmp)
        os.replace(tmp, link)
        self.restart_servers({"servers": ["blender"]})
        return f"Blender {latest} is now ~/.local/bin/blender; {current} stays in {os.path.dirname(target)} for rolling back."

    def rollback_blender(self, item, prev):
        if not prev.get("target") or not os.path.isfile(prev["target"]):
            raise Fail("the earlier Blender is no longer there")
        link = os.path.join(HOME, ".local", "bin", "blender")
        tmp = link + ".new"
        if os.path.lexists(tmp):
            os.remove(tmp)
        os.symlink(prev["target"], tmp)
        os.replace(tmp, link)
        return f"Back to Blender {prev.get('version')}."

    # Codex CLI: OpenAI's standalone package in ~/.codex/packages/standalone, `current` pointing at one of its releases.
    CODEX_HOME = os.path.join(HOME, ".codex", "packages", "standalone")

    def codex_version(self):
        binary = shutil.which("codex") or os.path.join(HOME, ".local", "bin", "codex")
        if not os.access(binary, os.X_OK):
            return None
        code, out = self.sh([binary, "--version"], check=False, quiet=True, timeout=30)
        m = re.search(r"(\d+\.\d+\.\d+)", out)
        return m.group(1) if code == 0 and m else None

    def check_codex(self, item):
        current = self.codex_version()
        latest = web_json("https://registry.npmjs.org/@openai/codex/latest")["version"]
        newer = bool(current) and tuple(map(int, latest.split("."))) > tuple(map(int, current.split(".")))
        return {"current": current or "not installed", "latest": latest, "available": newer}

    def update_codex(self, item):
        before = self.codex_version()
        if not before:
            raise Fail("Codex is not installed: install it from Tools & setup")
        current = os.path.join(self.CODEX_HOME, "current")
        self.info(item["id"])["prev"] = {"target": os.path.realpath(current), "version": before}
        self.sh(["codex", "update"], timeout=900)
        after = self.codex_version()
        if not after or after == before:
            raise Fail(f"Codex is still {before} after `codex update`")
        code, out = self.sh(["codex", "app-server", "--help"], check=False, quiet=True, timeout=60)
        if code != 0:
            self.rollback_codex(item, self.info(item["id"])["prev"])
            raise Fail("the new Codex has no app server, which Studio needs, so the earlier version was put back")
        return f"Updated from {before} to {after}; {before} stays for rolling back. New models reach the picker within ten minutes."

    def rollback_codex(self, item, prev):
        target = prev.get("target")
        if not target or not os.path.isdir(target):
            raise Fail("the earlier Codex is no longer there")
        link = os.path.join(self.CODEX_HOME, "current")
        tmp = link + ".new"
        if os.path.lexists(tmp):
            os.remove(tmp)
        os.symlink(target, tmp)
        os.replace(tmp, link)
        return f"Back to Codex {prev.get('version')}."

    # npm (Playwright)
    def update_npm(self, item):
        before = self.check_npm(item)["current"]
        latest = web_json(f"https://registry.npmjs.org/{item['package']}/latest")["version"]
        self.info(item["id"])["prev"] = {"version": before}
        self.sh(["npm", "install", "--save-exact", f"{item['package']}@{latest}"], cwd=item["path"], timeout=900)
        self.sh(["npx", "playwright", "install", "chromium"], cwd=item["path"], timeout=1800)
        code, out = self.sh(["node", "-e", "import('playwright').then(async p => { const b = await p.chromium.launch(); await b.close(); console.log('ok'); })"],
                            cwd=item["path"], timeout=120, check=False)
        if code != 0 or "ok" not in out:
            self.rollback_npm(item, {"version": before})
            raise Fail("the new Chromium did not start, so the earlier version was put back")
        return f"Updated from {before} to {latest}."

    def rollback_npm(self, item, prev):
        if not prev.get("version"):
            raise Fail("no earlier version is recorded")
        self.sh(["npm", "install", "--save-exact", f"{item['package']}@{prev['version']}"], cwd=item["path"], timeout=900)
        self.sh(["npx", "playwright", "install", "chromium"], cwd=item["path"], timeout=1800)
        return f"Rolled back to {prev['version']}."

    # web libraries
    def repin(self, package, old, new):
        changed = []
        for path in self.web_files():
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            fixed = text.replace(f"{package}@{old}", f"{package}@{new}")
            if fixed != text:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(fixed)
                changed.append(os.path.basename(path))
        return changed

    def update_weblib(self, item):
        pins = self.pinned(item["package"])
        latest = web_json(f"https://registry.npmjs.org/{item['package']}/latest")["version"]
        status, _, _ = web(f"https://cdn.jsdelivr.net/npm/{item['package']}@{latest}/{item['check']}", method="HEAD")
        if status != 200:
            raise Fail(f"jsdelivr does not serve {item['package']}@{latest} yet")
        changed = []
        for old in pins:
            changed += self.repin(item["package"], old, latest)
        self.info(item["id"])["prev"] = {"version": pins[-1] if pins else None}
        return (f"Studio's page now loads {item['package']} {latest} ({', '.join(sorted(set(changed)))}). Reload Studio to use it, "
                "and roll back if anything looks wrong.")

    def rollback_weblib(self, item, prev):
        if not prev.get("version"):
            raise Fail("no earlier version is recorded")
        for pin in self.pinned(item["package"]):
            self.repin(item["package"], pin, prev["version"])
        return f"Studio's page loads {item['package']} {prev['version']} again; reload it."

    # fake items for the tests
    def check_fake(self, item):
        have = self.info(item["id"]).get("fake_version", item["version"])
        return {"current": have, "latest": item["newest"], "available": have != item["newest"]}

    def update_fake(self, item):
        time.sleep(0.5)
        self.info(item["id"])["prev"] = {"version": self.info(item["id"]).get("fake_version", item["version"])}
        self.info(item["id"])["fake_version"] = item["newest"]
        return f"Updated to {item['newest']}."

    def rollback_fake(self, item, prev):
        self.info(item["id"])["fake_version"] = prev["version"]
        return f"Rolled back to {prev['version']}."

    def check_apt_fake(self):
        return {"current": "1 to update", "latest": "", "available": True, "packages": [{"name": "ffmpeg", "current": "7:6.1.1-3", "latest": "7:6.1.1-4"}],
                "command": "sudo apt update && sudo apt install --only-upgrade ffmpeg", "detail": "Studio cannot use sudo, so run the command yourself."}

    # ------------------------------------------------------------------ automatic updates
    def in_window(self, now=None):
        start, end = self.state["settings"].get("window", [3, 5])
        hour = time.localtime(now or time.time()).tm_hour
        return start <= hour < end if start <= end else (hour >= start or hour < end)

    def tick(self, now=None):
        """One pass of the nightly job: check once a day, then update the safe items with something new, one by one."""
        settings = self.state["settings"]
        if not settings.get("auto", True) or not self.in_window(now):
            return
        now = now or time.time()
        if now - (self.state.get("last_check") or 0) > 20 * 3600:
            self.check_all()
        tonight = time.strftime("%Y-%m-%d", time.localtime(now))
        for iid in self.items:
            st = self.info(iid)
            if not (self.auto_on(iid) and st.get("available")) or st.get("auto_tried") == tonight:
                continue
            if self.job and not self.job.get("done"):
                return
            busy = self.hooks["busy"]()
            if busy:
                self.log(f"Waiting: {busy}")
                return
            st["auto_tried"] = tonight
            self.start(iid, auto=True)
            return                                  # the next pass takes the next item

    def loop(self, every=300):
        while True:
            try:
                self.tick()
            except Exception as e:                 # the loop must keep going
                print("updates:", e, flush=True)
            time.sleep(every)
