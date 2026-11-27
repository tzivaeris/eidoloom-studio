"""Installing Studio's tools on a machine (Account & settings → Tools & setup, admins).

Studio's tools come from many places (Docker images, npm packages, Python environments, a Blender release, Studio's own
tool servers in tools/), and a new machine has none of them. This module looks at the machine (system, graphics card,
memory, disk, the programs already there), says for each tool whether it is installed, can be installed, needs
something first, or will not fit, and installs the ones an admin picks, one after another, in the background:
  - every step can run twice (a resumed or repeated install does what is missing and nothing else);
  - each tool ends with its own check, and its tool server is added to Studio's list (data/mcp.json) switched on, with
    the approval rule it has on Studio's own machine;
  - Studio runs without sudo, so system packages, the Docker group, AppArmor and the like are gathered into one script
    the admin runs once (`sudo_script`), and the tools that need them wait until a new check finds them.
Graphics: AMD only for now (ROCm). The engines on the card (ComfyUI and its models, voices, sound effects, HeartMuLa) are
pinned to the exact sources, packages and model files this Studio was tested with (sandbox/engines/), each model file
checked against Hugging Face's sha256, and run as systemd user services given that one card by ROCm's id for it.
"""
import glob
import json
import os
import platform
import re
import secrets
import shlex
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request

HOME = os.path.expanduser("~")
OPT = os.path.join(HOME, ".local", "opt")
LOCAL_BIN = os.path.join(HOME, ".local", "bin")
UA = {"User-Agent": "eidoloom-studio-installer/1.0"}


class Fail(Exception):
    pass


# ---------------------------------------------------------------------------- the machine

def read(path, default=""):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read().strip()
    except OSError:
        return default


def run_text(argv, timeout=10):
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return done.returncode, (done.stdout + done.stderr).strip()
    except (OSError, subprocess.TimeoutExpired):
        return None, ""


def os_release():
    info = {}
    for line in read("/etc/os-release").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            info[k] = v.strip().strip('"')
    return info


# gfx targets the ROCm builds of PyTorch support (RDNA2, RDNA3, RDNA3.5, RDNA4 and the Instinct cards).
ROCM_GFX = re.compile(r"^gfx(90a|942|950|103[0-6]|110[0-3]|1150|1151|1152|120[01])$")
AMD_NAMES = {"744c": "Radeon RX 7900 XT / XTX", "7448": "Radeon Pro W7900", "747e": "Radeon RX 7800 XT / 7700 XT", "7480": "Radeon RX 7600",
             "73bf": "Radeon RX 6800 / 6900", "73df": "Radeon RX 6700", "73ff": "Radeon RX 6600", "7550": "Radeon RX 9070 / 9070 XT",
             "7590": "Radeon RX 9060", "699f": "Radeon RX 540 / 550", "67df": "Radeon RX 470 / 480 / 570 / 580"}


def gpus():
    """Every graphics card the kernel knows (/sys/class/drm), with its memory, and for AMD cards the compute target
    from the ROCm kernel driver (/sys/class/kfd), which says whether PyTorch can use it."""
    kfd, uids = {}, {}
    for props in glob.glob("/sys/class/kfd/kfd/topology/nodes/*/properties"):
        values = dict(line.split(" ", 1) for line in read(props).splitlines() if " " in line)
        version = int(values.get("gfx_target_version", "0") or 0)
        if version:
            major, minor, step = version // 10000, version // 100 % 100, version % 100
            minor_render = int(values.get("drm_render_minor", "0"))
            kfd[minor_render] = f"gfx{major}{minor:x}{step:x}" if major >= 10 else f"gfx{major}{minor}{step:x}"
            uids[minor_render] = int(values.get("unique_id", "0") or 0)
    cards = []
    for card in sorted(glob.glob("/sys/class/drm/card[0-9]*")):
        if not re.search(r"card\d+$", card):
            continue
        dev = os.path.join(card, "device")
        vendor = read(os.path.join(dev, "vendor")).lower()
        if not vendor:
            continue
        device = read(os.path.join(dev, "device")).lower().replace("0x", "")
        vram = int(read(os.path.join(dev, "mem_info_vram_total"), "0") or 0)
        render = [int(r[len("renderD"):]) for r in os.listdir(os.path.join(dev, "drm")) if r.startswith("renderD")] if os.path.isdir(os.path.join(dev, "drm")) else []
        gfx = next((kfd[r] for r in render if r in kfd), None)
        uid = next((uids[r] for r in render if r in uids), 0)
        kind = {"0x1002": "amd", "0x10de": "nvidia", "0x8086": "intel"}.get(vendor, "other")
        cards.append({"card": os.path.basename(card), "vendor": kind, "name": AMD_NAMES.get(device, "") if kind == "amd" else "",
                      "vram_gb": round(vram / 2 ** 30, 1), "gfx": gfx, "rocm": bool(gfx and ROCM_GFX.match(gfx)),
                      "uid": f"GPU-{uid:016x}" if uid else ""})
    return cards


# Cards ROCm runs as a close relative (the PyTorch wheels carry kernels for gfx1030 and gfx1100, not for these).
HSA_OVERRIDE = {**{f"gfx103{i}": "10.3.0" for i in range(1, 7)}, "gfx1103": "11.0.0"}


def card_env(gpu):
    """The environment that gives an engine exactly this card: ROCm's own id for it (checked here: a wrong id leaves
    PyTorch no card), so a second card, or one ROCm does not support, is never picked by mistake."""
    parts = [f"ROCR_VISIBLE_DEVICES={gpu['uid']}" if gpu and gpu.get("uid") else "HIP_VISIBLE_DEVICES=0"]
    if gpu and HSA_OVERRIDE.get(gpu.get("gfx")):
        parts.append(f"HSA_OVERRIDE_GFX_VERSION={HSA_OVERRIDE[gpu['gfx']]}")
    return parts


PROGRAMS = ["docker", "node", "npx", "npm", "git", "ffmpeg", "ffprobe", "Xvfb", "python3", "curl", "soffice", "pdftotext", "blender", "rubberband"]


def probe():
    """What this machine is and has: the page's machine card and every tool's needs are judged from this."""
    rel = os_release()
    mem = {k: int(v.split()[0]) * 1024 for k, v in (line.split(":", 1) for line in read("/proc/meminfo").splitlines() if ":" in line)
           if k in ("MemTotal", "SwapTotal")}
    os.makedirs(OPT, exist_ok=True)
    disk = shutil.disk_usage(OPT)
    found = {p: shutil.which(p) or (os.path.join(LOCAL_BIN, p) if os.access(os.path.join(LOCAL_BIN, p), os.X_OK) else None) for p in PROGRAMS}
    docker_ok = False
    if found["docker"]:
        code, _ = run_text(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=15)
        docker_ok = code == 0
    py = sys_python()
    venv_ok = False
    if py:
        code, _ = run_text([py, "-c", "import ensurepip, venv"])
        venv_ok = code == 0
    ffmpeg_filters = ""
    if found["ffmpeg"]:
        ffmpeg_filters = run_text(["ffmpeg", "-hide_banner", "-filters"], timeout=15)[1]
    node_major = 0
    if found["node"]:
        m = re.match(r"v(\d+)", run_text(["node", "--version"])[1])
        node_major = int(m.group(1)) if m else 0
    groups = run_text(["id", "-nG"])[1].split()
    linger = run_text(["loginctl", "show-user", os.environ.get("USER", ""), "--property=Linger"])[1].strip() == "Linger=yes"
    cards = gpus()
    best = max((c for c in cards if c["rocm"]), key=lambda c: c["vram_gb"], default=None)
    userns = read("/proc/sys/kernel/apparmor_restrict_unprivileged_userns", "0") == "1"
    return {"system": rel.get("PRETTY_NAME") or platform.platform(), "distro": rel.get("ID", ""), "distro_like": rel.get("ID_LIKE", ""),
            "version": rel.get("VERSION_ID", ""), "arch": platform.machine(), "kernel": platform.release(), "cpus": os.cpu_count() or 1,
            "ram_gb": round(mem.get("MemTotal", 0) / 2 ** 30, 1), "swap_gb": round(mem.get("SwapTotal", 0) / 2 ** 30, 1),
            "disk_free_gb": round(disk.free / 2 ** 30, 1), "disk_path": OPT, "programs": {k: bool(v) for k, v in found.items()},
            "docker_ok": docker_ok, "in_docker_group": "docker" in groups, "python": run_text([py, "--version"])[1] if py else "",
            "venv_ok": venv_ok, "node_major": node_major, "rubberband": " rubberband " in ffmpeg_filters,
            "gpus": cards, "gpu": best, "gpu_groups": all(g in groups for g in ("render", "video")), "linger": linger,
            "apparmor_userns": userns, "apt": bool(shutil.which("apt-get")), "checked": time.time()}


def sys_python():
    return shutil.which("python3")


# ---------------------------------------------------------------------------- what each program comes from

# apt package for each program a tool needs (Debian and Ubuntu); the sudo script installs only the missing ones.
APT_FOR = {"docker": ["docker.io"], "node": ["nodejs", "npm"], "npx": ["nodejs", "npm"], "npm": ["npm"], "git": ["git"],
           "ffmpeg": ["ffmpeg"], "ffprobe": ["ffmpeg"], "Xvfb": ["xvfb"], "curl": ["curl"], "pdftotext": ["poppler-utils"],
           "soffice": ["libreoffice-core", "libreoffice-writer", "libreoffice-calc", "libreoffice-impress"],
           "venv": ["python3-venv", "python3-pip"], "xz": ["xz-utils"], "fonts": ["fonts-dejavu-core"], "cc": ["build-essential"],
           "blender-libs": ["libxi6", "libxkbcommon0", "libsm6", "libgl1", "libegl1", "libxrender1", "libxxf86vm1", "libxfixes3"]}
OPTIONAL_SUDO = {"apparmor"}   # Chromium runs without its own sandbox when this is missing
PLAYWRIGHT = "1.65.1"           # tests/browser/package.json pins the same


def blender_release():
    """The newest Blender for Linux on download.blender.org: (version, tarball URL)."""
    import updates
    listing = updates.web("https://download.blender.org/release/")[2].decode("utf-8", "replace")
    series = sorted({tuple(map(int, m)) for m in re.findall(r'href="Blender(\d+)\.(\d+)/"', listing)})
    for major, minor in reversed(series[-3:]):
        page = updates.web(f"https://download.blender.org/release/Blender{major}.{minor}/")[2].decode("utf-8", "replace")
        files = re.findall(rf'href="(blender-({major}\.{minor}\.\d+)-linux-x64\.tar\.xz)"', page)
        if files:
            name, version = max(files, key=lambda f: tuple(map(int, f[1].split("."))))
            return version, f"https://download.blender.org/release/Blender{major}.{minor}/{name}"
    raise Fail("no Linux release of Blender found on download.blender.org")


def manager_strata_dir():
    """The Strata checkout the Ollama Manager on this machine runs, or ""."""
    try:
        with urllib.request.urlopen(urllib.request.Request(MANAGER + "/api/strata/status", headers=UA), timeout=3) as resp:
            folder = json.load(resp).get("directory") or ""
    except (OSError, ValueError, AttributeError):
        return ""
    return folder if folder and os.path.isfile(os.path.join(folder, "serve", "server.py")) else ""


def codex_binary():
    """The Codex CLI on this machine, or None."""
    found = shutil.which("codex") or os.path.join(LOCAL_BIN, "codex")
    return found if os.access(found, os.X_OK) else None


def catalog(root, data_dir):
    """Every tool the installer knows, in the order the page shows and installs them.
    needs: programs from probe(); apt: what the sudo script adds for them; after: tools that must be in place first;
    servers: tool-server entries added to data/mcp.json; download/disk: rough sizes in MB for the page's totals;
    later: listed, with what it needs, but not installable yet; engine: needs an AMD card with gpu_gb that ROCm supports;
    extra: optional, left out of "Install everything"; files: ComfyUI model files; unit: its systemd user service."""
    py = "python3"
    tool = lambda name: os.path.join(root, "tools", name)
    roots = ":".join([os.path.join(HOME, "Projects"), os.path.join(HOME, "Videos")])
    projects = os.path.join(HOME, "Projects")
    return [
        # ---- local models: a model server and the model the person chooses; Studio starts the server itself
        {"id": "polystrata", "group": "models", "name": "PolyStrata (local models)", "local": "polystrata",
         "what": "Runs large models on this machine's card and RAM, with 128K of context: Qwen3.6-35B, fast, or Ornith 1.5 122B, "
                 "the strongest at following rules. Studio starts it when a chat picks one of its models.",
         "needs": ["git", "venv", "cc"], "repo": POLYSTRATA, "choices": LOCAL_CHOICES["polystrata"],
         "download": 23200 + 10000, "disk": 23200 + 12000},
        {"id": "strata", "group": "models", "name": "Strata (Swift 1.5)", "local": "strata",
         "what": "Strata's engine with Swift 1.5, the Qwen3.8-Flash-Next fine-tune Studio was built with, and 256K of context; "
                 "it needs about 64 GB of RAM. Studio starts it when a chat picks it.",
         "needs": ["git", "venv", "cc"], "repo": STRATA, "choices": LOCAL_CHOICES["strata"],
         "download": 77600 + 10000, "disk": 77600 + 12000},
        # ---- services in Docker
        {"id": "searxng", "group": "services", "name": "Web search (SearXNG)", "what": "Private web search for the agent: a local SearXNG and its tool server.",
         "needs": ["docker", "npx"], "download": 400, "disk": 450, "docker": {"image": "searxng/searxng:latest", "container": "studio-searxng",
            "run": ["--restart", "unless-stopped", "-p", "127.0.0.1:8888:8080", "-v", os.path.join(root, "sandbox", "mcp-searxng", "config") + ":/etc/searxng"],
            "test": "http://127.0.0.1:8888/search?q=strata&format=json", "wait": 90},
         "servers": [{"name": "web-search", "command": "npx", "args": ["-y", "mcp-searxng"], "env": {"SEARXNG_URL": "http://127.0.0.1:8888"},
                      "approval": "with_commands", "users": True, "description": "Web search through the local SearXNG, and reading pages."}]},
        {"id": "fetch", "group": "services", "name": "Fetch pages", "what": "The reference Fetch tool server, which reads a web page as text, in Docker.",
         "needs": ["docker"], "download": 430, "disk": 450, "docker": {"image": "mcp/fetch:latest"},
         "servers": [{"name": "fetch", "command": "docker", "args": ["run", "-i", "--rm", "mcp/fetch"], "approval": "with_commands", "users": True,
                      "description": "Reads a web page as text."}]},
        {"id": "languagetool", "group": "services", "name": "Greek checks (LanguageTool)", "what": "A local LanguageTool for spelling and grammar in Greek and other languages.",
         "needs": ["docker"], "download": 1200, "disk": 1250, "docker": {"image": "erikvl87/languagetool:latest", "container": "studio-languagetool",
            "run": ["--restart", "unless-stopped", "-p", "127.0.0.1:8010:8010", "-e", "Java_Xms=256m", "-e", "Java_Xmx=1g"],
            "test": "http://127.0.0.1:8010/v2/languages", "wait": 180},
         "servers": [{"name": "greek-check", "command": py, "args": [tool("languagetool_mcp.py")], "env": {"LANGUAGETOOL_URL": "http://127.0.0.1:8010"},
                      "approval": "with_commands", "users": True, "description": "Checks spelling and grammar with the local LanguageTool."}]},
        {"id": "sandbox", "group": "services", "name": "Sandbox image", "what": "The container image agent commands run in when a folder's Sandbox is on (Debian with the usual languages).",
         "needs": ["docker"], "download": 1500, "disk": 3200},
        # ---- tools on this machine
        {"id": "context7", "group": "tools", "name": "Library docs (Context7)", "what": "Up-to-date documentation of programming libraries for the agent.",
         "needs": ["npx"], "download": 20, "disk": 40,
         "servers": [{"name": "context7", "command": "npx", "args": ["-y", "@upstash/context7-mcp"], "approval": "with_commands", "users": True,
                      "description": "Current documentation of programming libraries."}]},
        {"id": "playwright", "group": "tools", "name": "Page checks (Playwright and Chromium)", "what": "Lets the agent look at the pages it builds (view_page) and powers the Browser tab.",
         "needs": ["npm", "npx"], "sudo": ["playwright-deps"], "download": 180, "disk": 650},
        {"id": "browser", "group": "tools", "name": "The agent's web browser", "what": "A Chromium the agent drives (Playwright MCP), shown live in the Browser tab, where you can take over.",
         "needs": ["npx"], "after": ["playwright"], "sudo": ["apparmor"], "download": 15, "disk": 30,
         "servers": [{"name": "browser", "command": py, "args": [tool("browser_mcp.py")], "env": {"BROWSER_STATE": os.path.join(data_dir, "browser")},
                      "approval": "with_commands", "users": False, "timeout_s": 180, "description": "A web browser the agent drives; admins only."}]},
        {"id": "video", "group": "tools", "name": "Video editing", "what": "render_video, probe_media and video_frames: the agent cuts and renders videos with ffmpeg.",
         "needs": ["ffmpeg", "ffprobe", "fonts"], "download": 0, "disk": 0,
         "servers": [{"name": "video", "command": py, "args": [tool("video_mcp.py")], "env": {"VIDEO_ROOTS": roots},
                      "approval": "with_commands", "users": True, "timeout_s": 1800, "description": "Renders videos from a timeline with ffmpeg."}]},
        {"id": "audio", "group": "tools", "name": "Sound editing", "what": "The wave editor's engine for the agent: cut, fade, mix, pitch and effects with ffmpeg.",
         "needs": ["ffmpeg", "ffprobe", "rubberband"], "download": 0, "disk": 0,
         "servers": [{"name": "audio", "command": py, "args": [tool("audio_mcp.py")], "env": {"AUDIO_ROOTS": roots},
                      "approval": "with_commands", "users": True, "timeout_s": 900, "description": "Edits sound files with ffmpeg."}]},
        {"id": "documents", "group": "tools", "name": "Office documents and PDFs", "what": "Reading .doc, .xls, .ppt, .rtf (LibreOffice) and PDF (pdftotext) files; .docx and .xlsx need nothing.",
         "needs": ["soffice", "pdftotext"], "download": 0, "disk": 0},
        {"id": "blender", "group": "tools", "name": "Blender (3D)", "what": "Blender with MCP for Blender: the agent builds 3D scenes; Studio starts Blender on an invisible screen.",
         "needs": ["venv", "Xvfb", "xz", "blender-libs"], "download": 450, "disk": 1600,
         "servers": [{"name": "blender", "command": os.path.join(OPT, "mcp-for-blender", "bin", "mcp-for-blender"), "args": [],
                      "env": {"BLENDER_HOST": "localhost", "BLENDER_PORT": "9876", "BLENDER_MCP_SAFE_MODE": "1", "DISABLE_TELEMETRY": "true"},
                      "approval": "with_commands", "users": True, "app_command": os.path.join(LOCAL_BIN, "blender"), "app_port": 9876, "app_window": "hidden",
                      "description": "Blender: scenes, objects, materials and Python in Blender."}]},
        {"id": "codex", "group": "tools", "name": "Codex (OpenAI)", "what": "OpenAI's Codex CLI, so admins can pick Codex models as the chat's agent, "
         "with this machine's ChatGPT login; Studio's tools reach it through Studio. After installing, sign in once in a terminal: codex login --device-auth.",
         "needs": ["curl"], "download": 60, "disk": 200, "config": {"codex": True}},
        # ---- engines on the graphics card (AMD with ROCm). Each borrows ComfyUI's PyTorch, so ComfyUI comes first.
        {"id": "comfyui", "group": "engines", "name": "ComfyUI with Z-Image Turbo", "engine": True,
         "what": "Pictures on the graphics card (Z-Image Turbo, the default model), and the base the other engines share: ComfyUI with PyTorch for AMD (ROCm 7.2).",
         "needs": ["venv", "git"], "gpu_gb": 12, "files": COMFY_FILES["z-image-turbo"], "download": 5500 + mb(COMFY_FILES["z-image-turbo"]),
         "disk": 17500 + mb(COMFY_FILES["z-image-turbo"]), "unit": "comfyui", "health": "http://127.0.0.1:8188/system_stats", "config": {"comfyui_url": "http://127.0.0.1:8188"},
         "servers": [{"name": "comfyui", "command": py, "args": [tool("comfyui_mcp.py")], "approval": "with_commands", "users": True, "timeout_s": 1800,
                      "env": {"COMFYUI_URL": "http://127.0.0.1:8188", "COMFYUI_SETTINGS": os.path.join(data_dir, "comfyui.json"),
                              "COMFYUI_SWAP_FILE": os.path.join(OPT, "comfyui", "user", "strata_swap.json"), "COMFYUI_WAIT_S": "1700", "COMFYUI_SWAP_WAIT_S": "1500"},
                      "description": "Pictures, video and music from the local ComfyUI: generate_image(s), generate_video, generate_music"}]},
        {"id": "ace-music", "group": "engines", "name": "Music (ACE-Step 1.5)", "engine": True, "after": ["comfyui"],
         "what": "Songs with lyrics in 50 languages, Greek too, or instrumentals, in ComfyUI (generate_music).",
         "gpu_gb": 8, "files": COMFY_FILES["ace-music"], "download": mb(COMFY_FILES["ace-music"]), "disk": mb(COMFY_FILES["ace-music"])},
        {"id": "voices", "group": "engines", "name": "Voices (VoxCPM2)", "engine": True, "after": ["comfyui"],
         "what": "Reading aloud in 30 languages with Greek, in voices designed from a description: the Voices view and the speak tool.",
         "needs": ["venv"], "gpu_gb": 8, "download": 5000, "disk": 5400, "unit": "strata-voice", "health": "http://127.0.0.1:8189/health",
         "config": {"voice_url": "http://127.0.0.1:8189"},
         "servers": [{"name": "voices", "command": py, "args": [tool("voice_mcp.py")], "approval": "with_commands", "users": True, "timeout_s": 1800,
                      "env": {"VOICE_URL": "http://127.0.0.1:8189", "VOICES_FILE": os.path.join(data_dir, "voices", "voices.json"), "VOICE_ROOTS": projects},
                      "description": "Read text aloud in Studio's voices (VoxCPM2): list_voices, speak"}]},
        {"id": "sounds", "group": "engines", "name": "Sound effects (MOSS-SoundEffect)", "engine": True, "after": ["comfyui"],
         "what": "Sound effects and ambience from a description: nature, city, animals, objects (make_sound).",
         "needs": ["venv", "git"], "gpu_gb": 12, "download": 11300, "disk": 11800, "unit": "strata-sound", "health": "http://127.0.0.1:8190/health",
         "servers": [{"name": "sounds", "command": py, "args": [tool("sound_mcp.py")], "approval": "with_commands", "users": True, "timeout_s": 1800,
                      "env": {"SOUND_URL": "http://127.0.0.1:8190", "SOUND_ROOTS": projects},
                      "description": "Sound effects and ambience (MOSS-SoundEffect v2.0): make_sound"}]},
        # ---- optional extras: not part of "everything", since each is large and adds a choice rather than a tool
        {"id": "pictures-more", "group": "engines", "name": "More picture models: Z-Image Base and SDXL", "engine": True, "extra": True, "after": ["comfyui"],
         "what": "Z-Image Base (slower, more natural light and skin, real negative prompts) and SDXL (paintings and illustration).",
         "gpu_gb": 12, "files": COMFY_FILES["pictures-more"], "download": mb(COMFY_FILES["pictures-more"]), "disk": mb(COMFY_FILES["pictures-more"])},
        {"id": "qwen-image", "group": "engines", "name": "Qwen-Image 2512 (20B)", "engine": True, "extra": True, "after": ["comfyui"],
         "what": "The best pictures and lettering of all, using the whole card: an idle Strata on this machine is unloaded while it draws.",
         "gpu_gb": 24, "files": COMFY_FILES["qwen-image"], "download": mb(COMFY_FILES["qwen-image"]), "disk": mb(COMFY_FILES["qwen-image"])},
        {"id": "wan-video", "group": "engines", "name": "Video clips (Wan 2.2 5B)", "engine": True, "extra": True, "after": ["comfyui"],
         "what": "Short video clips from text or a picture (generate_video), using the whole card.",
         "gpu_gb": 20, "files": COMFY_FILES["wan-video"], "download": mb(COMFY_FILES["wan-video"]), "disk": mb(COMFY_FILES["wan-video"])},
        {"id": "ace-music-xl", "group": "engines", "name": "Music, best quality (ACE-Step 1.5 XL)", "engine": True, "extra": True, "after": ["ace-music"],
         "what": "The 4B music model for quality: best, using the whole card.",
         "gpu_gb": 20, "files": COMFY_FILES["ace-music-xl"], "download": mb(COMFY_FILES["ace-music-xl"]), "disk": mb(COMFY_FILES["ace-music-xl"])},
        {"id": "music", "group": "engines", "name": "Songs (HeartMuLa 3B)", "engine": True, "extra": True, "after": ["comfyui"],
         "what": "A second music model that follows lyrics closely, chosen in the ComfyUI panel's Music setting.",
         "needs": ["venv", "git", "ffmpeg"], "gpu_gb": 16, "download": 22000, "disk": 22600, "unit": "strata-music", "health": "http://127.0.0.1:8191/health"},
    ]


def mb(files):
    return sum(f[3] for f in files) // 2 ** 20


# ComfyUI's model files: (Hugging Face repo, revision, path, size, sha256, folder under models/). Pinned to the files this
# Studio was set up and tested with; the sha256 is Hugging Face's own LFS checksum, checked after every download.
def _f(repo, rev, path, size, sha, folder):
    return (repo, rev, path, size, sha, folder)


_ZT, _Z, _QI = "Comfy-Org/z_image_turbo", "Comfy-Org/z_image", "Comfy-Org/Qwen-Image_ComfyUI"
_ZT_REV, _Z_REV, _QI_REV = "6fc90a3b1b653e935a0d175e260736de25b84df5", "94d47a468ec820d29a5dc09d4642539d76d49542", "1f12b17be14c89b026c51a91d67c32f84bb047bc"
_W22, _W21, _ACE = "Comfy-Org/Wan_2.2_ComfyUI_Repackaged", "Comfy-Org/Wan_2.1_ComfyUI_repackaged", "Comfy-Org/ace_step_1.5_ComfyUI_files"
_W22_REV, _W21_REV, _ACE_REV = "ee6f4a40737a995bf5818954cfce6d59443b0f04", "123acf1cc74bccbb9bfff8ac1ee72edc08c2341d", "371bb1680f8c88d8c1083f6ffb6330d3d55bff76"
COMFY_FILES = {
    "z-image-turbo": [
        _f(_ZT, _ZT_REV, "split_files/diffusion_models/z_image_turbo_int8_convrot.safetensors", 6201001296, "be517ebd47c912a5626a588e1aeea43e6be4a43c0cdcd2b48a2a780d9f358635", "diffusion_models"),
        _f(_ZT, _ZT_REV, "split_files/text_encoders/qwen_3_4b_fp8_mixed.safetensors", 5631994051, "72450b19758172c5a7273cf7de729d1c17e7f434a104a00167624cba94f68f15", "text_encoders"),
        _f(_ZT, _ZT_REV, "split_files/vae/ae.safetensors", 335304388, "afc8e28272cd15db3919bacdb6918ce9c1ed22e96cb12c4d5ed0fba823529e38", "vae")],
    "pictures-more": [
        _f(_Z, _Z_REV, "split_files/diffusion_models/z_image_int8_convrot.safetensors", 6201001296, "eb2b470694fbd9778f15efd309000ea0091312c018c10a352c120b1da9bf4279", "diffusion_models"),
        _f("stabilityai/stable-diffusion-xl-base-1.0", "462165984030d82259a11f4367a4eed129e94a7b", "sd_xl_base_1.0.safetensors", 6938078334,
           "31e35c80fc4829d14f90153f4c74cd59c90b779f6afe05a74cd6120b893f7e5b", "checkpoints")],
    "qwen-image": [
        _f(_QI, _QI_REV, "split_files/diffusion_models/qwen_image_2512_fp8_e4m3fn.safetensors", 20430679144, "5dc80554d5d83390046a2f4a94ece06afb7700bf7b0aaf8bde9769793875876b", "diffusion_models"),
        _f(_QI, _QI_REV, "split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors", 9384670680, "cb5636d852a0ea6a9075ab1bef496c0db7aef13c02350571e388aea959c5c0b4", "text_encoders"),
        _f(_QI, _QI_REV, "split_files/vae/qwen_image_vae.safetensors", 253806246, "a70580f0213e67967ee9c95f05bb400e8fb08307e017a924bf3441223e023d1f", "vae")],
    "wan-video": [
        _f(_W22, _W22_REV, "split_files/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors", 9999658848, "456f901338bd9eadbded3828b819109a9b68e8a525ca5cf8d0049a69fcfeca1e", "diffusion_models"),
        _f(_W21, _W21_REV, "split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors", 6735906897, "c3355d30191f1f066b26d93fba017ae9809dce6c627dda5f6a66eaa651204f68", "text_encoders"),
        _f(_W22, _W22_REV, "split_files/vae/wan2.2_vae.safetensors", 1409400960, "e40321bd36b9709991dae2530eb4ac303dd168276980d3e9bc4b6e2b75fed156", "vae")],
    "ace-music": [
        _f(_ACE, _ACE_REV, "split_files/diffusion_models/acestep_v1.5_turbo.safetensors", 4787825604, "3f6e0797fad420a39bd33979eb6e840e30989e34a3794e843d23b60ec6e422d7", "diffusion_models"),
        _f(_ACE, _ACE_REV, "split_files/text_encoders/qwen_0.6b_ace15.safetensors", 1191588248, "fd4590c82153b8ddb67e15a2e7aaa8afa8b83a858c8a9b82a4831063156aa7a7", "text_encoders"),
        _f(_ACE, _ACE_REV, "split_files/text_encoders/qwen_1.7b_ace15.safetensors", 3708523360, "ed63e9247d1f55f3ace04fa11e95b085fc82d459c82c5626f0b2e37b91ebd710", "text_encoders"),
        _f(_ACE, _ACE_REV, "split_files/vae/ace_1.5_vae.safetensors", 337431732, "6de92e3a862acd287e08b024ac90f0783a8635451b728721a33ff03565bcb2bb", "vae")],
    "ace-music-xl": [
        _f(_ACE, _ACE_REV, "split_files/diffusion_models/acestep_v1.5_xl_turbo_bf16.safetensors", 9974719892, "86a1afb0a1f711f0e3304ff65d874df3ae6783db683dcf982513fb9b6d14ae71", "diffusion_models"),
        _f(_ACE, _ACE_REV, "split_files/text_encoders/qwen_4b_ace15.safetensors", 8379154232, "ffe5ffb855086c2ab55e467e9859fb01894781020a0376484dd19de166b79873", "text_encoders")],
}
# Everything else an engine is built from, pinned the same way (sandbox/engines/*.txt hold each environment's packages).
TORCH_INDEX = "https://download.pytorch.org/whl/rocm7.2"
COMFY_GIT = ("https://github.com/comfyanonymous/ComfyUI.git", "e9afbccc2669677076feb08e52658f129cd8c12b")
MOSS_GIT = ("https://github.com/OpenMOSS/MOSS-TTS.git", "934d6826b084c46a0d033402174d5f8ac4ed2519")
HEARTLIB_GIT = ("https://github.com/HeartMuLa/heartlib.git", "cd238827ff91db686d46f2d5eea185d578ce4081")
SNAPSHOTS = {   # whole Hugging Face repos: (repo, revision, folder under ~/.local/opt)
    "voices": [("openbmb/VoxCPM2", "7936080b41163874f38610f46f438e2c0757f9fd", "voice/models/VoxCPM2")],
    "sounds": [("OpenMOSS-Team/MOSS-SoundEffect-v2.0", "e35df4d82fbe87fcd5d14e5d100e349c0c3c076d", "sound/models/MOSS-SoundEffect-v2.0")],
    "music": [("HeartMuLa/HeartMuLaGen", "6f76e891c10ef8ae50fa6f2fb48fab783f3ce1d7", "music/ckpt"),
              ("HeartMuLa/HeartMuLa-oss-3B-happy-new-year", "54765694c890ab5de89b366a1b1669a1b6e797fd", "music/ckpt/HeartMuLa-oss-3B"),
              ("HeartMuLa/HeartCodec-oss-20260123", "e2363f7b92010c82fa9d78bdca1a66b42569a39f", "music/ckpt/HeartCodec-oss")],
}
# Each engine's environment: (folder under ~/.local/opt, lock file, the module that shows it is installed, editable source).
ENVS = {"voices": ("voice", "voice.txt", "voxcpm", None), "sounds": ("sound", "sound.txt", "moss_soundeffect_v2", "sound/src/moss_soundeffect_v2"),
        "music": ("music", "music.txt", "heartlib", "heartlib")}
UNIT_SOURCES = {"comfyui": "sandbox/mcp-comfyui/comfyui.service", "strata-voice": "sandbox/mcp-voice/strata-voice.service",
                "strata-sound": "sandbox/mcp-sound/strata-sound.service", "strata-music": "sandbox/mcp-sound/strata-music.service"}
MAKE_ROOM_ENV = {"strata-voice": "VOICE_MAKE_ROOM", "strata-sound": "SOUND_MAKE_ROOM", "strata-music": "MUSIC_MAKE_ROOM"}
MANAGER = "http://127.0.0.1:8787"

# Local models (2026-11-27, the user's request: everything from Studio, no Ollama Manager). Pinned to what this machine
# runs; their own setup.sh builds the engine for the card and downloads the model. ram_gb/vram_gb are their setups'
# own estimates; a choice whose profile exists is installed (the profile is strata-<family>-<size>.json).
STRATA = ("https://github.com/Niko1221/Strata.git", "c77572806996349b49995e4f98d41b5294d36a21")
POLYSTRATA = ("https://github.com/simplexai-labs/PolyStrata.git", "42e489d145aaf4430b54f2c58679be27d45faf17")
LOCAL_CHOICES = {
    "polystrata": [
        {"id": "qwen36-ud-q4_k_m", "family": "qwen36", "model": "UD-Q4_K_M", "context": 131072, "download_gb": 22.7, "ram_gb": 10, "vram_gb": 8,
         "label": "Qwen3.6-35B-A3B UD-Q4_K_M", "about": "fast (110-200 tokens/s on a 24 GB card), good Greek; weaker at keeping a long chat's rules"},
        {"id": "ornith122-ud-iq3_xxs", "family": "ornith122", "model": "UD-IQ3_XXS", "context": 131072, "download_gb": 49.5, "ram_gb": 37, "vram_gb": 16,
         "label": "Ornith 1.5 122B UD-IQ3_XXS", "about": "the strongest at following rules and the best Greek; 25-47 tokens/s"},
    ],
    "strata": [
        {"id": "swift-iq3_xxs", "family": "swift", "model": "IQ3_XXS", "context": 262144, "download_gb": 75.8, "ram_gb": 60, "vram_gb": 12,
         "label": "Swift 1.5 IQ3_XXS", "about": "3-bit, the quality Studio's tests were run with; about 60 tokens/s"},
        {"id": "swift-iq2_xs", "family": "swift", "model": "IQ2_XS", "context": 262144, "download_gb": 68.0, "ram_gb": 48, "vram_gb": 12,
         "label": "Swift 1.5 IQ2_XS", "about": "2-bit: less RAM, a little lower quality"},
    ],
}
LOCAL_KEYS = {"strata": "strata_dir", "polystrata": "polystrata_dir"}
LOCAL_HOMES = {"strata": "", "polystrata": "terminal"}          # where server.py, .venv and the profiles are
LOCAL_PORT = 8097


GROUPS = [("models", "Local models"), ("services", "Services in Docker"), ("tools", "Tools on this machine"), ("engines", "Engines on the graphics card")]
LATER_NOTE = "Comes in the next version of this installer (AMD graphics cards first)."
NOT_INSTALLABLE = ["listmonk (email) is its own project: set it up from listmonk.app, then add the two listmonk tool servers in Tool servers with its API user.",
                   "Ollama and the Ollama Manager are their own projects: Studio uses them when Account & settings → Models points at one."]


def test_catalog(root, data_dir):
    """Three fake tools for Studio's tests (config installer_catalog "test"): nothing on the machine is touched."""
    return [{"id": "fake-ok", "group": "services", "name": "Fake service", "what": "Installs in a second.", "needs": [], "download": 1, "disk": 2, "fake": "ok",
             "servers": [{"name": "fake-tool", "command": "python3", "args": [os.path.join(root, "tests", "mock_mcp.py")], "approval": "with_commands", "users": True}]},
            {"id": "fake-bad", "group": "tools", "name": "Fake tool that fails", "what": "Its check fails.", "needs": [], "download": 0, "disk": 0, "fake": "bad"},
            {"id": "fake-apt", "group": "tools", "name": "Fake tool with a missing program", "what": "Needs a program that is not there.",
             "needs": ["no-such-program"], "download": 0, "disk": 0, "fake": "ok"},
            {"id": "fake-later", "group": "engines", "name": "Fake engine", "what": "Not yet.", "later": True, "gpu_gb": 16, "download": 100, "disk": 200},
            {"id": "fake-big", "group": "engines", "name": "Fake engine too big", "what": "Needs a larger card.", "engine": True, "gpu_gb": 48,
             "needs": [], "download": 100, "disk": 200, "fake": "ok"},
            {"id": "fake-extra", "group": "engines", "name": "Fake optional engine", "what": "Optional.", "engine": True, "extra": True, "gpu_gb": 8,
             "needs": [], "after": ["fake-ok"], "download": 100, "disk": 200, "fake": "ok"}]


APPARMOR_PROFILE = """abi <abi/4.0>,
include <tunables/global>

profile studio-chromium @{HOME}/.cache/ms-playwright/chromium-*/chrome-linux64/chrome flags=(unconfined) {
  userns,
  include if exists <local/studio-chromium>
}
"""
SEARXNG_SETTINGS = """# Made by Eidoloom Studio's installer: SearXNG's defaults, with JSON answers on for the tool server.
use_default_settings: true
server:
  secret_key: "{key}"
  limiter: false
  image_proxy: false
search:
  formats:
    - html
    - json
"""
BLENDER_LIBS = ["libXi.so.6", "libxkbcommon.so.0", "libSM.so.6", "libGL.so.1", "libEGL.so.1", "libXrender.so.1", "libXxf86vm.so.1", "libXfixes.so.3"]
DEJAVU = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"]


class Installer:
    def __init__(self, data_dir, root, hooks):
        self.root, self.data_dir, self.hooks = root, data_dir, hooks
        self.test = bool(hooks.get("test"))
        self.dir = os.path.join(data_dir, "installer")
        os.makedirs(self.dir, exist_ok=True)
        self.path = os.path.join(self.dir, "state.json")
        self.lock = threading.RLock()
        self.items = {i["id"]: i for i in (test_catalog if self.test else catalog)(root, data_dir)}
        try:
            with open(self.path) as fh:
                self.state = json.load(fh)
        except (OSError, ValueError):
            self.state = {}
        self.state.setdefault("history", [])
        self.machine, self.job, self.cancel = None, None, False

    # ------------------------------------------------------------------ state
    def save(self):
        with self.lock:
            tmp = self.path + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(self.state, fh, indent=1)
            os.replace(tmp, self.path)

    def remember(self, iid, ok, message):
        with self.lock:
            entry = {"at": time.time(), "id": iid, "name": self.items[iid]["name"], "ok": ok, "message": message[:600]}
            self.state["history"] = (self.state["history"] + [entry])[-200:]
            self.save()

    def log(self, line):
        if self.job is not None:
            self.job["log"] = (self.job["log"] + [line[:400]])[-80:]

    def sh(self, argv, cwd=None, env=None, timeout=900, check=True, quiet=False):
        if self.cancel:
            raise Fail("stopped")
        if not quiet:
            self.log("$ " + " ".join(shlex.quote(a) for a in argv))
        try:
            done = subprocess.run(argv, cwd=cwd, env={**os.environ, **(env or {})}, capture_output=True, text=True, timeout=timeout)
        except FileNotFoundError:
            raise Fail(f"{argv[0]} is not installed")
        except subprocess.TimeoutExpired:
            raise Fail(f"{argv[0]} took longer than {timeout // 60} minutes")
        out = (done.stdout + done.stderr).strip()
        for line in ([] if quiet else out.splitlines()[-5:]):
            self.log("  " + line)
        if check and done.returncode != 0:
            raise Fail(f"{' '.join(argv[:3])} exited {done.returncode}: {out[-300:]}")
        return done.returncode, out

    # ------------------------------------------------------------------ judging
    def look(self, again=False):
        if again or self.machine is None or time.time() - self.machine["checked"] > 120:
            if self.test:
                self.machine = {"system": "Test machine", "arch": "x86_64", "kernel": "test", "cpus": 4, "ram_gb": 16, "swap_gb": 0, "disk_free_gb": 500,
                                "disk_path": OPT, "programs": {}, "docker_ok": True, "in_docker_group": True, "python": "Python 3", "venv_ok": True,
                                "node_major": 22, "rubberband": True, "gpus": [{"card": "card0", "vendor": "amd", "name": "Radeon RX 7900 XT / XTX",
                                "vram_gb": 24, "gfx": "gfx1100", "rocm": True}], "gpu": None, "gpu_groups": True, "linger": True,
                                "apparmor_userns": False, "apt": True, "checked": time.time()}
                self.machine["gpu"] = self.machine["gpus"][0]
            else:
                self.machine = probe()
        return self.machine

    def has(self, need, m):
        """Whether the machine has one of an item's needs, and the apt packages that would give it."""
        if self.test:
            return need != "no-such-program", ["no-such-program"] if need == "no-such-program" else []
        progs = m["programs"]
        if need == "docker":
            return m["docker_ok"], ([] if progs.get("docker") else APT_FOR["docker"])
        if need in ("node", "npx", "npm"):
            return bool(progs.get(need)) and m["node_major"] >= 18, APT_FOR[need]
        if need == "venv":
            return m["venv_ok"], APT_FOR["venv"]
        if need == "rubberband":
            return m["rubberband"], ["ffmpeg"]
        if need == "fonts":
            return any(os.path.exists(f) for f in DEJAVU), APT_FOR["fonts"]
        if need == "xz":
            return bool(shutil.which("xz")), APT_FOR["xz"]
        if need == "cc":
            return bool(shutil.which("g++") and shutil.which("make")), APT_FOR["cc"]
        if need == "blender-libs":
            libs = run_text(["ldconfig", "-p"])[1]
            return all(lib in libs for lib in BLENDER_LIBS), APT_FOR["blender-libs"]
        return bool(progs.get(need)), APT_FOR.get(need, [])

    def missing(self, item, m):
        """(what is missing in words, apt packages for it, other sudo steps)."""
        words, apt, extra = [], [], []
        for need in item.get("needs", []):
            ok, packages = self.has(need, m)
            if ok:
                continue
            if need == "docker" and (self.test or m["programs"].get("docker")):
                if not m["in_docker_group"]:
                    words.append("you in the docker group (then restart the computer)")
                    extra.append("docker-group")
                else:
                    words.append("Docker running")
                    extra.append("docker-start")
                continue
            if need in ("node", "npx", "npm") and m.get("node_major") and m["node_major"] < 18:
                words.append(f"Node.js 18 or newer (this one is {m['node_major']})")
            else:
                words.append({"docker": "Docker", "venv": "Python's venv", "fonts": "the DejaVu fonts", "xz": "xz",
                              "blender-libs": "the libraries Blender draws with", "rubberband": "ffmpeg with rubberband",
                              "cc": "a C++ compiler (build-essential)"}.get(need, need))
            apt += [p for p in packages if p not in apt]
        if "docker.io" in apt and not m.get("in_docker_group"):
            extra.append("docker-group")
        return words, apt, extra

    def server_state(self, spec):
        have = self.hooks["server_spec"](spec["name"])
        return "missing" if not have else "on" if have.get("enabled") else "off"

    def installed(self, item):
        """(installed, words). Cheap: files, images and the tool-server list, no network."""
        if self.test:
            done = self.state.get("fake_done", {}).get(item["id"])
            return bool(done), "installed" if done else ""
        iid, found = item["id"], []
        if item.get("docker"):
            d = item["docker"]
            if self.sh(["docker", "image", "inspect", d["image"]], check=False, quiet=True, timeout=30)[0] != 0:
                return False, ""
            if d.get("container"):
                code, out = self.sh(["docker", "inspect", "-f", "{{.State.Running}}", d["container"]], check=False, quiet=True, timeout=30)
                if code != 0 or out.strip() != "true":
                    return False, "its container is not running"
        if iid == "sandbox" and self.sh(["docker", "image", "inspect", "strata-sandbox:universal"], check=False, quiet=True, timeout=30)[0] != 0:
            return False, ""
        if iid == "playwright":
            if not os.path.exists(os.path.join(self.root, "tests", "browser", "node_modules", "playwright", "package.json")):
                return False, ""
            if not glob.glob(os.path.join(HOME, ".cache", "ms-playwright", "chromium-*", "chrome-linux*", "chrome")):
                return False, "Chromium is not downloaded"
        if iid == "blender":
            if not os.path.exists(os.path.join(LOCAL_BIN, "blender")):
                return False, ""
            if not os.path.exists(os.path.join(OPT, "mcp-for-blender", "bin", "mcp-for-blender")):
                return False, "MCP for Blender is not installed"
            if not glob.glob(os.path.join(HOME, ".config", "blender", "*", "scripts", "addons", "blender_mcp.py")):
                return False, "its add-on is not in Blender"
        if iid == "documents" and not (shutil.which("soffice") and shutil.which("pdftotext")):
            return False, ""
        if item.get("local"):
            models = self.local_models(item)
            if not models:
                return False, ""
            return True, "models: " + ", ".join(sorted(set(models.values())))
        if iid == "codex":
            binary = codex_binary()
            if not binary:
                return False, ""
            get = self.hooks.get("config_get")
            if get and not get("codex"):
                return False, "installed, but not turned on in Studio"
            version = self.sh([binary, "--version"], check=False, quiet=True, timeout=30)[1].strip().replace("codex-cli ", "")
            signed = os.path.exists(os.path.join(HOME, ".codex", "auth.json"))
            return True, f"version {version}" + ("" if signed else "; not signed in yet: run codex login --device-auth in a terminal")
        if item.get("engine"):
            gone = self.engine_missing(item)
            if gone:
                return False, gone if gone != "-" else ""
        for spec in item.get("servers", []):
            state = self.server_state(spec)
            if state == "missing":
                return False, f"tool server {spec['name']} is not in Studio's list"
            found.append(f"{spec['name']} {'on' if state == 'on' else 'off'}")
        return True, ("tool server " + ", ".join(found)) if found else "installed"

    def local_folder(self, item):
        """The checkout: the one Studio's config names, one the Ollama Manager runs (Strata), or Studio's own in ~/.local/opt."""
        get = self.hooks.get("config_get")
        named = get(LOCAL_KEYS[item["local"]]) if get else ""
        if named and os.path.isdir(os.path.expanduser(named)):
            return os.path.expanduser(named)
        if item["local"] == "strata":
            here = manager_strata_dir()
            if here:
                return here
        return os.path.join(OPT, "Strata" if item["local"] == "strata" else "PolyStrata")

    def local_models(self, item):
        """{profile stem: model name} of the models installed in the item's checkout."""
        home = os.path.join(self.local_folder(item), LOCAL_HOMES[item["local"]])
        if not os.path.isfile(os.path.join(home, "serve", "server.py")):
            return {}
        out = {}
        for f in sorted(glob.glob(os.path.join(home, "strata-*.json"))):
            try:
                config = json.load(open(f, encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(config, dict) and isinstance(config.get("model_name"), str):
                out[os.path.basename(f)[len("strata-"):-len(".json")]] = config["model_name"]
        return out

    def choices(self, item, m):
        """The models the item can install, each with whether it fits this machine and whether it is installed."""
        have, out = self.local_models(item), []
        vram = max([g.get("vram_gb") or 0 for g in m.get("gpus") or []] or [0])
        for c in item.get("choices", []):
            why = ""
            if not vram:
                why = "needs a graphics card (AMD or NVIDIA)"
            elif vram + 0.5 < c["vram_gb"]:
                why = f"needs a card with {c['vram_gb']} GB; this one has {vram:g} GB"
            elif m.get("ram_gb", 0) + 4 < c["ram_gb"]:
                why = f"needs about {c['ram_gb']} GB of RAM; this machine has {m.get('ram_gb', 0):g} GB"
            elif c["download_gb"] + 12 > m.get("disk_free_gb", 0):
                why = f"needs {c['download_gb'] + 12:.0f} GB free on disk; {m.get('disk_free_gb', 0):g} GB are free"
            out.append({"id": c["id"], "label": c["label"], "about": c["about"], "download_gb": c["download_gb"], "ram_gb": c["ram_gb"],
                        "fits": not why, "why": why, "installed": any(s == c["id"] or s.startswith(c["id"] + "-") for s in have)})
        return out

    def status(self, item, m):
        if item.get("later"):
            gpu = m.get("gpu")
            fits = bool(gpu) and gpu["vram_gb"] >= item.get("gpu_gb", 0)
            why = (f"needs an AMD card with {item['gpu_gb']} GB that ROCm supports" if not fits else "") or LATER_NOTE
            return {"status": "later", "why": why, "fits": fits, "apt": [], "extra": []}
        ok, detail = self.installed(item)
        if ok:
            return {"status": "installed", "why": detail, "apt": [], "extra": []}
        if item.get("local") and not self.test and not any(c["fits"] for c in self.choices(item, m)):
            return {"status": "no", "why": "None of its models fits this machine: " + "; ".join(c["why"] for c in self.choices(item, m)),
                    "apt": [], "extra": []}
        if item.get("engine"):
            gpu = m.get("gpu")
            if not gpu:
                return {"status": "no", "why": f"Needs an AMD graphics card with {item['gpu_gb']} GB that ROCm supports; this machine has none.", "apt": [], "extra": []}
            if gpu["vram_gb"] + 0.5 < item["gpu_gb"]:
                return {"status": "no", "why": f"Needs a card with {item['gpu_gb']} GB; this one has {gpu['vram_gb']:g} GB.", "apt": [], "extra": []}
        if item.get("disk", 0) / 1024 > m.get("disk_free_gb", 0) - 2:
            return {"status": "no", "why": f"Needs {item['disk'] / 1024:.0f} GB free on disk; {m.get('disk_free_gb', 0):g} GB are free in {m.get('disk_path', OPT)}.",
                    "apt": [], "extra": []}
        words, apt, extra = self.missing(item, m)
        if item.get("engine") and not self.test and not m.get("gpu_groups"):
            words.append("you in the render and video groups, which may use the card (then restart the computer)")
            extra.append("gpu-groups")
        for dep in item.get("after", []):
            if dep in self.items and not self.items[dep].get("later") and not self.installed(self.items[dep])[0]:
                words.append(f"{self.items[dep]['name']} (installed with it)")
        extra += [s for s in item.get("sudo", []) if self.sudo_needed(s, m)]
        blocking = [w for w in words if not w.endswith("(installed with it)")]
        return {"status": "needs" if blocking else "ready", "why": "Needs " + ", ".join(words) if words else detail,
                "apt": apt, "extra": extra}

    def sudo_needed(self, step, m):
        if self.test:
            return False
        if step == "apparmor":
            return m.get("apparmor_userns") and not os.path.exists("/etc/apparmor.d/studio-chromium")
        if step == "playwright-deps":
            chrome = glob.glob(os.path.join(HOME, ".cache", "ms-playwright", "chromium-*", "chrome-linux*", "chrome"))
            return not chrome or "not found" in run_text(["ldd", max(chrome)])[1]
        return True

    def sudo_script(self, ids, m=None):
        """One script for everything the chosen tools need that only root can do, or "" when nothing is needed."""
        m = m or self.look()
        apt, extra = [], []
        for iid in ids:
            item = self.items.get(iid)
            if not item or item.get("later"):
                continue
            st = self.status(item, m)
            apt += [p for p in st["apt"] if p not in apt]
            extra += [e for e in st["extra"] if e not in extra]
        if not m.get("linger"):
            extra.append("linger")
        if not apt and not [e for e in extra if e not in OPTIONAL_SUDO]:
            return ""                   # an optional step alone is no reason to ask for root
        lines = ["#!/usr/bin/env bash", "# Made by Eidoloom Studio's installer. Run it once with: sudo bash studio-setup.sh", "set -e",
                 f'ME="{os.environ.get("USER") or os.path.basename(HOME)}"']
        if apt:
            if not m.get("apt"):
                lines.append(f"# This system has no apt: install these with its own package manager: {' '.join(apt)}")
            else:
                lines += ["apt-get update", "DEBIAN_FRONTEND=noninteractive apt-get install -y " + " ".join(apt)]
        if "docker-group" in extra:
            lines += ["# Lets Studio use Docker without sudo. Restart the computer afterwards: Studio's service keeps its old groups until then.",
                      'usermod -aG docker "$ME"']
        if "gpu-groups" in extra:
            lines += ["# Lets Studio's engines use the graphics card. Restart the computer afterwards: Studio's service keeps its old groups until then.", 'usermod -aG render,video "$ME"']
        if "docker-start" in extra:
            lines += ["systemctl enable --now docker"]
        if "playwright-deps" in extra:
            lines += ["# The libraries Chromium needs", f"npx -y playwright@{PLAYWRIGHT} install-deps chromium"]
        if "apparmor" in extra:
            lines += ["# Optional: lets the agent's Chromium use its own sandbox (Ubuntu 23.10+ blocks it otherwise; it then runs without one)",
                      "cat > /etc/apparmor.d/studio-chromium <<'EOF'", APPARMOR_PROFILE.rstrip(), "EOF", "apparmor_parser -r /etc/apparmor.d/studio-chromium"]
        if "linger" in extra:
            lines += ["# Keeps Studio's services running when you are signed out", 'loginctl enable-linger "$ME"']
        lines.append('echo "Done. Back in Studio, press Check again."')
        return "\n".join(lines) + "\n"

    def public(self):
        m = self.look()
        items = []
        for iid, item in self.items.items():
            st = self.status(item, m)
            items.append({"id": iid, "name": item["name"], "group": item["group"], "what": item["what"], "status": st["status"], "why": st["why"],
                          "apt": st["apt"], "sudo": bool(st["apt"] or st["extra"]), "download_mb": item.get("download", 0), "disk_mb": item.get("disk", 0),
                          "after": item.get("after", []), "fits": st.get("fits"), "servers": [s["name"] for s in item.get("servers", [])],
                          "extra": bool(item.get("extra")), "engine": bool(item.get("engine")), "gpu_gb": item.get("gpu_gb", 0),
                          "choices": self.choices(item, m) if item.get("choices") else None})
        return {"machine": m, "items": items, "groups": GROUPS, "job": self.job, "not_here": NOT_INSTALLABLE,
                "history": self.state["history"][-40:][::-1]}

    # ------------------------------------------------------------------ installing
    def order(self, ids):
        """The chosen tools plus what they need first, in catalog order."""
        want = set()

        def add(iid):
            if iid in self.items and iid not in want and not self.items[iid].get("later"):
                want.add(iid)
                for dep in self.items[iid].get("after", []):
                    add(dep)
        for iid in ids:
            add(iid)
        return [iid for iid in self.items if iid in want]

    def start(self, ids, options=None):
        if not isinstance(ids, list) or not ids or not all(isinstance(i, str) and i in self.items for i in ids):
            raise Fail("choose tools from the list")
        options = options if isinstance(options, dict) else {}
        for iid, choice in options.items():
            if iid not in self.items or not isinstance(choice, str) or choice not in {c["id"] for c in self.items[iid].get("choices", [])}:
                raise Fail("choose a model from the list")
        with self.lock:
            if self.job and not self.job["done"]:
                raise Fail("an install is already running")
            # No waiting for agent runs: an install only adds programs and tool servers, and restarts nothing in use.
            todo = self.order(ids)
            if not todo:
                raise Fail("those are not installable yet")
            self.cancel = False
            self.job = {"ids": todo, "current": None, "done_ids": [], "failed": {}, "skipped": {}, "log": [], "started": time.time(),
                        "done": False, "ok": None, "message": "", "options": options}
        threading.Thread(target=self.run_job, daemon=True).start()
        return self.job

    def stop(self):
        if self.job and not self.job["done"]:
            self.cancel = True
            return True
        return False

    def run_job(self):
        job = self.job
        m = self.look(again=True)
        for iid in job["ids"]:
            item = self.items[iid]
            if self.cancel:
                job["skipped"][iid] = "stopped"
                continue
            blocked = [d for d in item.get("after", []) if d in job["failed"] or d in job["skipped"]]
            if blocked:
                job["skipped"][iid] = f"{self.items[blocked[0]]['name']} did not install"
                continue
            st = self.status(item, m)
            if st["status"] == "no":
                job["skipped"][iid] = st["why"]
                continue
            words, _, _ = self.missing(item, m)
            if item.get("engine") and "gpu-groups" in st["extra"]:
                words.append("you in the render and video groups")
            if words:
                job["skipped"][iid] = "Needs " + ", ".join(words) + ": run the setup script first"
                self.remember(iid, False, job["skipped"][iid])
                continue
            job["current"] = iid
            self.log(f"── {item['name']}")
            try:
                message = self.install(item)
                job["done_ids"].append(iid)
                self.remember(iid, True, message)
                self.log("   ✓ " + message)
            except Exception as e:                  # one tool's failure must not stop the others
                text = str(e)[:400] if isinstance(e, Fail) else f"{type(e).__name__}: {e}"[:400]
                job["failed"][iid] = text
                self.remember(iid, False, text)
                self.log("   ✗ " + text)
        job.update(current=None, done=True, ok=not job["failed"] and not job["skipped"], finished=time.time())
        parts = [f"{len(job['done_ids'])} installed"] + ([f"{len(job['failed'])} failed"] if job["failed"] else []) \
            + ([f"{len(job['skipped'])} skipped"] if job["skipped"] else [])
        job["message"] = ", ".join(parts)
        self.machine = None

    def install(self, item):
        if item.get("fake"):
            time.sleep(0.3)
            if item["fake"] == "bad":
                raise Fail("the fake tool's check failed")
            with self.lock:
                self.state.setdefault("fake_done", {})[item["id"]] = time.time()
                self.save()
        else:
            getattr(self, "install_" + item["id"].replace("-", "_"), lambda item: None)(item)
            if item.get("docker"):
                self.docker_service(item["docker"])
        added = []
        for spec in item.get("servers", []):
            if self.server_state(spec) == "missing":
                self.hooks["add_server"](spec)
                added.append(spec["name"])
                self.log(f"   added tool server {spec['name']}")
            elif self.server_state(spec) == "off":
                self.log(f"   tool server {spec['name']} is in the list but switched off; left as it is")
                continue
            if not self.hooks["server_ready"](spec["name"], 240):
                raise Fail(f"tool server {spec['name']} did not start: see its log in Tool servers")
        return ("ready" if not added else "ready, tool server " + ", ".join(added) + " added")

    # ------------------------------------------------------------------ recipes
    def install_polystrata(self, item):
        return self.install_local(item)

    def install_strata(self, item):
        return self.install_local(item)

    def install_local(self, item):
        """Clones the server at its pinned commit, runs its own setup for the chosen model (it builds the engine for the
        card and downloads the model), then tells Studio where it is and which port to start it on."""
        m = self.look()
        wanted = (self.job or {}).get("options", {}).get(item["id"])
        choices = {c["id"]: c for c in self.choices(item, m)}
        choice = choices.get(wanted) if wanted else next((c for c in choices.values() if c["fits"] and not c["installed"]), None)
        if not choice:
            raise Fail("no model of it fits this machine" if not any(c["fits"] for c in choices.values()) else "its models are all installed")
        if choice["installed"]:
            self.log(f"   {choice['label']} is already installed")
            return
        if not choice["fits"]:
            raise Fail(f"{choice['label']} does not fit: {choice['why']}")
        spec = next(c for c in item["choices"] if c["id"] == choice["id"])
        folder = self.local_folder(item)
        self.git_at(item["repo"], folder)
        get = self.hooks.get("config_get")
        port = int((get("local_port") if get else 0) or LOCAL_PORT)
        argv = [os.path.join(folder, "setup.sh"), "--family", spec["family"], "--model", spec["model"], "--context", str(spec["context"]),
                "--port", str(port), "--no-start", "--no-browser", "--yes"] + (["--setup"] if self.local_models(item) else [])
        if item["local"] == "polystrata":
            argv += ["--output", "jsonl"]
        self.log(f"   {choice['label']}: its setup builds the engine for this card and downloads {spec['download_gb']:g} GB")
        self.run_setup(argv, folder)
        if not any(c["id"] == choice["id"] and c["installed"] for c in self.choices(item, m)):
            raise Fail(f"its setup finished, but no profile for {choice['label']} was written")
        set_config = self.hooks.get("set_config")
        if set_config:              # the port first: a folder without one would make its server the gateway
            set_config("local_port", port)
            set_config(LOCAL_KEYS[item["local"]], folder)
        self.log(f"   {choice['label']} installed; Studio starts it on port {port} when a chat picks it")

    def run_setup(self, argv, folder):
        """Runs Strata's or PolyStrata's setup.sh: its steps reach the log, its downloads the progress bar (from --output
        jsonl's events, or from the plain "file: 1.2 / 22.7 GB (5%)" lines), and Stop ends it."""
        self.log("$ " + " ".join(shlex.quote(a) for a in argv))
        try:
            proc = subprocess.Popen(argv, cwd=folder, env={**os.environ, "PYTHONUNBUFFERED": "1", "NO_COLOR": "1"}, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        except OSError as e:
            raise Fail(f"its setup could not start: {e}")
        end, tail, buf = time.time() + 6 * 3600, [], b""
        threading.Thread(target=self._watch, args=(proc, end), daemon=True).start()
        progress = re.compile(r"^\s*(.+?):\s+([\d.]+)\s*/\s*([\d.]+)\s*GB")
        while True:
            chunk = proc.stdout.read1(65536) if hasattr(proc.stdout, "read1") else proc.stdout.read(4096)
            if not chunk:
                break
            buf += chunk
            parts = re.split(rb"[\r\n]", buf)
            buf = parts.pop()
            for raw in parts:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                tail = (tail + [line])[-12:]
                if line.startswith("{"):
                    try:
                        ev = json.loads(line)
                    except ValueError:
                        ev = None
                    if isinstance(ev, dict):
                        kind = ev.get("event")
                        if kind == "progress":
                            self.job["progress"] = {"name": ev.get("label") or "download", "done": ev.get("done_bytes") or 0, "total": ev.get("total_bytes") or 0}
                        elif kind == "step":
                            self.job["progress"] = None
                            self.log(f"   · {ev.get('title', '')}")
                        elif kind in ("ok", "warning", "error"):
                            self.log(("   ✗ " if kind == "error" else "   ! " if kind == "warning" else "     ") + str(ev.get("message") or ev)[:300])
                        continue
                got = progress.match(line)
                if got:
                    self.job["progress"] = {"name": got.group(1), "done": float(got.group(2)) * 2 ** 30, "total": float(got.group(3)) * 2 ** 30}
                elif line.startswith("===") or line.startswith("[ok]") or line.startswith("[!]") or "error" in line.lower():
                    self.job["progress"] = None if line.startswith("===") else self.job.get("progress")
                    self.log("   " + line[:300])
        code = proc.wait()
        self.job["progress"] = None
        if self.cancel:
            raise Fail("stopped")
        if code != 0:
            raise Fail(f"its setup exited {code}: " + " | ".join(tail[-4:])[-400:])

    def pull(self, image):
        empty = os.path.join(self.dir, "docker-config")             # no credential helper: public images only
        os.makedirs(empty, exist_ok=True)
        self.sh(["docker", "pull", image], env={"DOCKER_CONFIG": empty}, timeout=3600)

    def docker_service(self, d):
        if self.sh(["docker", "image", "inspect", d["image"]], check=False, quiet=True, timeout=30)[0] != 0:
            self.pull(d["image"])
        name = d.get("container")
        if not name:
            return
        code, out = self.sh(["docker", "inspect", "-f", "{{.State.Running}}", name], check=False, quiet=True, timeout=30)
        if code == 0 and out.strip() == "true":
            self.log(f"   {name} is running")
        elif code == 0:
            self.sh(["docker", "start", name], timeout=120)
        else:
            self.sh(["docker", "run", "-d", "--name", name, *d["run"], d["image"]], timeout=300)
        if d.get("test") and not self.answers(d["test"], d.get("wait", 60)):
            raise Fail(f"{name} started but {d['test']} does not answer")

    def answers(self, url, wait):
        end = time.time() + wait
        while time.time() < end and not self.cancel:
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=5) as resp:
                    if resp.status == 200:
                        return True
            except (OSError, ValueError):
                pass
            time.sleep(2)
        return False

    def install_searxng(self, item):
        folder = os.path.join(self.root, "sandbox", "mcp-searxng", "config")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "settings.yml")
        if not os.path.exists(path):
            with open(path, "w") as fh:
                fh.write(SEARXNG_SETTINGS.replace("{key}", secrets.token_hex(32)))
            os.chmod(path, 0o644)
            self.log("   wrote SearXNG's settings with JSON answers on")

    def install_sandbox(self, item):
        if self.sh(["docker", "image", "inspect", "strata-sandbox:universal"], check=False, quiet=True, timeout=30)[0] == 0:
            self.log("   the image is already built")
            return
        folder = os.path.join(self.root, "sandbox", "images")
        empty = os.path.join(self.dir, "docker-config")
        os.makedirs(empty, exist_ok=True)
        self.sh(["docker", "build", "-t", "strata-sandbox:universal", "-f", os.path.join(folder, "universal.Dockerfile"),
                 "--build-arg", f"SANDBOX_UID={os.getuid()}", "--build-arg", f"SANDBOX_GID={os.getgid()}", folder],
                env={"DOCKER_CONFIG": empty}, timeout=3600)

    def install_codex(self, item):
        """OpenAI's own installer (the one `codex update` runs), into ~/.codex/packages with ~/.local/bin/codex; then Codex is
        turned on in Studio. Signing in stays with the person: codex login --device-auth."""
        if not codex_binary():
            self.sh(["sh", "-c", "curl -fsSL https://chatgpt.com/codex/install.sh | CODEX_NON_INTERACTIVE=1 sh"], timeout=900)
        binary = codex_binary()
        if not binary:
            raise Fail("the Codex installer finished, but no codex program is there")
        self.log("   " + self.sh([binary, "--version"], timeout=60)[1].strip())
        if self.hooks.get("set_config"):
            self.hooks["set_config"]("codex", True)
            self.log("   turned Codex on in studio.config.json")
        if not os.path.exists(os.path.join(HOME, ".codex", "auth.json")):
            self.log("   sign in once in a terminal on this machine: codex login --device-auth")

    def install_playwright(self, item):
        folder = os.path.join(self.root, "tests", "browser")
        if not os.path.exists(os.path.join(folder, "node_modules", "playwright", "package.json")):
            self.sh(["npm", "ci" if os.path.exists(os.path.join(folder, "package-lock.json")) else "install", "--no-audit", "--no-fund"], cwd=folder, timeout=900)
        self.sh(["npx", "playwright", "install", "chromium"], cwd=folder, timeout=1800)
        chrome = glob.glob(os.path.join(HOME, ".cache", "ms-playwright", "chromium-*", "chrome-linux*", "chrome"))
        if chrome and "not found" in run_text(["ldd", max(chrome)])[1]:
            raise Fail("Chromium is missing system libraries: run the setup script (it has the playwright install-deps line), then install again")

    def install_blender(self, item):
        link = os.path.join(LOCAL_BIN, "blender")
        os.makedirs(LOCAL_BIN, exist_ok=True)
        if not os.path.exists(link):
            version, url = blender_release()
            folder = os.path.join(OPT, f"blender-{version}-linux-x64")
            if not os.path.isdir(folder):
                archive = os.path.join(OPT, os.path.basename(url))
                self.log(f"   downloading Blender {version}")
                self.download(url, archive)
                want = self.blender_sum(url)
                if want and self.sha256(archive) != want:
                    os.remove(archive)
                    raise Fail("the Blender download does not match blender.org's checksum")
                self.sh(["tar", "-xf", archive, "-C", OPT], timeout=900)
                os.remove(archive)
            os.symlink(os.path.join(folder, "blender"), link)
        binary = os.path.realpath(link)
        out = self.sh([binary, "-b", "--factory-startup", "--version"], timeout=120)[1]
        series = re.search(r"Blender (\d+\.\d+)", out)
        if not series:
            raise Fail("Blender does not start: " + out[-200:])
        venv = os.path.join(OPT, "mcp-for-blender")
        if not os.path.exists(os.path.join(venv, "bin", "mcp-for-blender")):
            self.sh([sys_python(), "-m", "venv", venv], timeout=300)
            self.sh([os.path.join(venv, "bin", "pip"), "install", "--quiet", "mcp-for-blender"], timeout=900)
        bundled = glob.glob(os.path.join(venv, "lib", "python3*", "site-packages", "blender_mcp", "bundled", "addon.py"))
        if not bundled:
            raise Fail("MCP for Blender has no bundled add-on")
        addons = os.path.join(HOME, ".config", "blender", series.group(1), "scripts", "addons")
        os.makedirs(addons, exist_ok=True)
        shutil.copyfile(bundled[0], os.path.join(addons, "blender_mcp.py"))
        enable = ("import addon_utils, bpy; addon_utils.enable('blender_mcp', default_set=True, persistent=True); "
                  "bpy.ops.wm.save_userpref(); print('STUDIO-ADDON', addon_utils.check('blender_mcp'))")
        out = self.sh([binary, "-b", "--python-expr", enable], timeout=180)[1]
        if "STUDIO-ADDON (True, True)" not in out:
            raise Fail("Blender did not turn on the MCP add-on: " + out[-200:])

    # ------------------------------------------------------------------ engines on the graphics card
    def engine_missing(self, item):
        """"" when an engine is in place with its service running, "-" when nothing of it is here, else what is missing."""
        iid, comfy = item["id"], os.path.join(OPT, "comfyui")
        if iid == "comfyui" and not (os.path.exists(os.path.join(comfy, "main.py")) and self.venv_has(os.path.join(comfy, ".venv"), "torch")):
            return "-"
        if iid in ENVS:
            folder, _, module, _ = ENVS[iid]
            if not self.venv_has(os.path.join(OPT, folder, ".venv"), module):
                return "-"
        for _, _, folder in SNAPSHOTS.get(iid, []):
            path = os.path.join(OPT, folder)
            if not os.path.exists(os.path.join(path, ".studio-complete")) and not os.path.isdir(os.path.join(path, ".cache", "huggingface")):
                return "its model is not downloaded"
        files = item.get("files", [])
        short = [f for f in files if self.model_size(f) != f[3]]
        if short:
            return "-" if len(short) == len(files) else f"{len(short)} of its model files are missing"
        if iid == "comfyui" and not os.path.exists(os.path.join(comfy, "custom_nodes", "strata_vram_cap.py")):
            return "Studio's memory add-on is not in ComfyUI"
        unit = item.get("unit")
        if unit:
            if not os.path.exists(self.unit_path(unit)):
                return f"its service {unit} is not set up"
            if self.sh(["systemctl", "--user", "is-active", unit], check=False, quiet=True, timeout=15)[1].strip() != "active":
                return f"its service {unit} is not running"
        get = self.hooks.get("config_get")
        for key in item.get("config", {}):
            if get and not get(key):
                return f"{key} is not set in studio.config.json"
        return ""

    @staticmethod
    def model_size(f):
        try:
            return os.path.getsize(os.path.join(OPT, "comfyui", "models", f[5], os.path.basename(f[2])))
        except OSError:
            return -1

    @staticmethod
    def unit_path(unit):
        return os.path.join(HOME, ".config", "systemd", "user", unit + ".service")

    def venv_has(self, venv, module):
        python = os.path.join(venv, "bin", "python")
        if not os.path.exists(python):
            return False
        code = run_text([python, "-c", "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec(sys.argv[1]) else 1)", module], timeout=30)[0]
        return code == 0

    def lock_file(self, name):
        return os.path.join(self.root, "sandbox", "engines", name)

    def manager_here(self):
        """Whether a model server (the Ollama Manager with Strata) runs on this machine, sharing the card with the engines."""
        try:
            with urllib.request.urlopen(urllib.request.Request(MANAGER + "/api/strata/status", headers=UA), timeout=4) as resp:
                return resp.status == 200
        except (OSError, ValueError):
            return False

    def comfy_python(self):
        """The Python ComfyUI's environment was made with: the other engines borrow its PyTorch, so they need the same one."""
        for line in read(os.path.join(OPT, "comfyui", ".venv", "pyvenv.cfg")).splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "executable" and os.path.exists(value.strip()):
                return value.strip()
        return sys_python()

    def git_at(self, source, folder):
        url, commit = source
        if os.path.isdir(os.path.join(folder, ".git")):
            self.log(f"   {os.path.basename(folder)} is already here; left as it is")
            return
        if os.path.exists(folder) and os.listdir(folder):
            raise Fail(f"{folder} exists and is not a Git checkout; move it away and install again")
        self.sh(["git", "clone", "--filter=blob:none", "--no-checkout", url, folder], timeout=1800)
        self.sh(["git", "-C", folder, "checkout", "--quiet", commit], timeout=600)

    def pip(self, venv, args, what=""):
        if what:
            self.log("   " + what)
        self.sh_live([os.path.join(venv, "bin", "pip"), "install", "--no-deps", "--no-input", "--disable-pip-version-check", "--progress-bar", "off", *args],
                     timeout=7200)

    def sh_live(self, argv, env=None, timeout=3600, cwd=None):
        """Like sh, for long steps: their lines reach the page's log as they come, and Stop ends them."""
        self.log("$ " + " ".join(shlex.quote(a) for a in argv))
        try:
            proc = subprocess.Popen(argv, cwd=cwd, env={**os.environ, **(env or {})}, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        except FileNotFoundError:
            raise Fail(f"{argv[0]} is not installed")
        end, tail = time.time() + timeout, []
        stopper = threading.Thread(target=self._watch, args=(proc, end), daemon=True)
        stopper.start()
        for line in proc.stdout:
            line = line.rstrip()
            tail = (tail + [line])[-12:]
            if re.match(r"(Collecting|Downloading|Installing|Successfully|ERROR|error|Obtaining|Building)", line.strip()):
                self.log("  " + line.strip())
        code = proc.wait()
        if self.cancel:
            raise Fail("stopped")
        if time.time() > end:
            raise Fail(f"{argv[0]} took longer than {timeout // 60} minutes")
        if code != 0:
            raise Fail(f"{os.path.basename(argv[0])} exited {code}: " + " | ".join(tail[-4:])[-400:])

    def _watch(self, proc, end):
        while proc.poll() is None:
            if self.cancel or time.time() > end:
                proc.terminate()
                return
            time.sleep(1)

    def card_check(self, venv):
        env = dict(kv.split("=", 1) for kv in card_env(self.look()["gpu"]))
        code, out = self.sh([os.path.join(venv, "bin", "python"), "-c",
                             "import torch; ok = torch.cuda.is_available(); print('STUDIO-CARD', ok, torch.cuda.get_device_name(0) if ok else '')"],
                            env=env, check=False, timeout=600)
        if "STUDIO-CARD True" not in out:
            raise Fail("PyTorch cannot use the graphics card: " + out[-200:] + ". Studio's service must be in the render and video groups: "
                       "run the setup script, restart the computer, then install again.")
        self.log("   " + out.split("STUDIO-CARD True", 1)[1].strip() + " is ready for PyTorch")

    def engine_files(self, item):
        for repo, rev, path, size, sha, folder in item.get("files", []):
            dest = os.path.join(OPT, "comfyui", "models", folder, os.path.basename(path))
            if self.model_size((repo, rev, path, size, sha, folder)) == size:
                self.log(f"   {os.path.basename(path)} is already here")
                continue
            self.log(f"   downloading {os.path.basename(path)} ({size / 2 ** 30:.1f} GB) from {repo}")
            self.fetch(f"https://huggingface.co/{repo}/resolve/{rev}/{path}", dest, size, sha)

    def hf_snapshot(self, repo, rev, dest):
        """A whole Hugging Face repo at one revision, each large file checked against its sha256."""
        if os.path.exists(os.path.join(dest, ".studio-complete")):
            return
        url, files = f"https://huggingface.co/api/models/{repo}/tree/{rev}?recursive=true", []
        while url:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as resp:
                files += [f for f in json.load(resp) if f.get("type") == "file"]
                link = re.search(r'<([^>]+)>;\s*rel="next"', resp.headers.get("Link", "") or "")
                url = link.group(1) if link else None
        total = sum(f.get("size", 0) for f in files)
        self.log(f"   {repo}: {len(files)} files, {total / 2 ** 30:.1f} GB")
        for f in files:
            path = os.path.join(dest, f["path"])
            if f["path"].startswith("/") or ".." in f["path"].split("/"):
                raise Fail(f"{repo} lists a file outside its folder: {f['path']}")
            if os.path.exists(path) and os.path.getsize(path) == f.get("size"):
                continue
            self.fetch(f"https://huggingface.co/{repo}/resolve/{rev}/{f['path']}", path, f.get("size"), (f.get("lfs") or {}).get("oid"))
        with open(os.path.join(dest, ".studio-complete"), "w") as fh:
            fh.write(f"{repo}@{rev}\n")

    def fetch(self, url, path, size=None, sha=None, tries=4):
        """Downloads to path.part and moves it in place when whole: resumes after a broken connection or a stop, and checks
        the size and sha256 when known. The page shows its progress."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        part, name = path + ".part", os.path.basename(path)
        for attempt in range(tries):
            have = os.path.getsize(part) if os.path.exists(part) else 0
            if size is not None and have > size:
                os.remove(part)
                have = 0
            if size is not None and have == size:
                break
            headers = dict(UA, **({"Range": f"bytes={have}-"} if have else {}))
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as resp:
                    if have and resp.status != 206:
                        have = 0
                    total = size or (have + int(resp.headers.get("Content-Length") or 0)) or None
                    with open(part, "ab" if have else "wb") as fh:
                        done = have
                        while True:
                            if self.cancel:
                                raise Fail("stopped")
                            block = resp.read(4 << 20)
                            if not block:
                                break
                            fh.write(block)
                            done += len(block)
                            if self.job is not None:
                                self.job["progress"] = {"name": name, "done": done, "total": total}
                break
            except (OSError, urllib.error.URLError) as e:
                if attempt == tries - 1:
                    raise Fail(f"could not download {name}: {e}")
                self.log(f"   {name}: {e}; trying again")
                time.sleep(5 * (attempt + 1))
        if self.job is not None:
            self.job["progress"] = None
        if size is not None and os.path.getsize(part) != size:
            raise Fail(f"{name} came incomplete ({os.path.getsize(part)} of {size} bytes); install again to resume")
        if sha:
            self.log(f"   checking {name}")
            if self.sha256(part) != sha:
                os.remove(part)
                raise Fail(f"{name} does not match its sha256 on Hugging Face, so it was removed")
        os.replace(part, path)

    def download(self, url, path):
        self.fetch(url, path)

    def render_unit(self, unit, here):
        src = UNIT_SOURCES[unit]
        text = open(os.path.join(self.root, src), encoding="utf-8").read()
        text = text.replace("%h/Projects/Tools/StrataStudio", os.path.realpath(self.root))
        text = re.sub(r"HIP_VISIBLE_DEVICES=\S+", " ".join(card_env(self.look()["gpu"])), text)
        if not here:            # no Strata on this card: ComfyUI may use all of it, and there is nothing to unload
            text = text.replace("STRATA_COMFY_VRAM_GB=8.5", "STRATA_COMFY_VRAM_GB=0")
            if unit in MAKE_ROOM_ENV:
                text = text.replace("[Service]\n", f"[Service]\nEnvironment={MAKE_ROOM_ENV[unit]}=0\n", 1)
        return (f"# Made by Eidoloom Studio's installer from {src}, for this machine's card. The notes below describe the\n"
                "# machine it was first set up on (an RX 7900 XTX beside the Strata engine).\n" + text)

    def service(self, item, here):
        unit = item["unit"]
        path = self.unit_path(unit)
        if os.path.exists(path):
            self.log(f"   {unit}.service is already set up; left as it is")
        else:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self.render_unit(unit, here))
            self.log(f"   wrote {path}")
        self.sh(["systemctl", "--user", "daemon-reload"], timeout=60)
        self.sh(["systemctl", "--user", "enable", "--now", unit], timeout=120)
        if item.get("health") and not self.answers(item["health"], 300):
            journal = self.sh(["journalctl", "--user", "-u", unit, "-n", "8", "--no-pager", "-o", "cat"], check=False, quiet=True, timeout=30)[1]
            raise Fail(f"{unit} started but {item['health']} does not answer: {journal[-300:]}")
        for key, value in item.get("config", {}).items():
            if self.hooks.get("set_config"):
                self.hooks["set_config"](key, value)
                self.log(f"   set {key} in studio.config.json")

    def engine_env(self, item):
        """An engine's own environment, borrowing ComfyUI's PyTorch through a .pth file so ComfyUI is never touched."""
        folder, lock, module, editable = ENVS[item["id"]]
        base = os.path.join(OPT, folder)
        os.makedirs(base, exist_ok=True)
        if item["id"] == "sounds":
            self.git_at(MOSS_GIT, os.path.join(base, "src"))
        if item["id"] == "music":
            self.git_at(HEARTLIB_GIT, os.path.join(OPT, "heartlib"))
        venv = os.path.join(base, ".venv")
        if not os.path.exists(os.path.join(venv, "bin", "python")):
            self.sh([self.comfy_python(), "-m", "venv", venv], timeout=300)
        mine = glob.glob(os.path.join(venv, "lib", "python3*", "site-packages"))
        comfy = glob.glob(os.path.join(OPT, "comfyui", ".venv", "lib", "python3*", "site-packages"))
        if not mine or not comfy or os.path.basename(os.path.dirname(mine[0])) != os.path.basename(os.path.dirname(comfy[0])):
            raise Fail("this engine's Python does not match ComfyUI's, so it cannot borrow its PyTorch")
        pth = os.path.join(mine[0], "zz-comfyui-torch.pth")
        if not os.path.exists(pth):
            with open(pth, "w") as fh:
                fh.write(comfy[0] + "\n")
        self.pip(venv, ["-r", self.lock_file(lock)], f"{item['name']}: its own packages")
        if editable:
            self.pip(venv, ["-e", os.path.join(OPT, editable)])
        if not self.venv_has(venv, module):
            raise Fail(f"{module} is not importable after the install")
        return venv

    def install_comfyui(self, item):
        comfy = os.path.join(OPT, "comfyui")
        self.git_at(COMFY_GIT, comfy)
        venv = os.path.join(comfy, ".venv")
        if not os.path.exists(os.path.join(venv, "bin", "python")):
            self.sh([sys_python(), "-m", "venv", venv], timeout=300)
        self.pip(venv, ["-r", self.lock_file("comfyui.txt"), "--extra-index-url", TORCH_INDEX], "ComfyUI's packages and PyTorch for AMD (about 5 GB to download)")
        self.card_check(venv)
        addon = os.path.join(comfy, "custom_nodes", "strata_vram_cap.py")
        if not os.path.exists(addon):
            shutil.copyfile(os.path.join(self.root, "sandbox", "mcp-comfyui", "strata_vram_cap.py"), addon)
        os.makedirs(os.path.join(comfy, "user"), exist_ok=True)
        self.engine_files(item)
        here = self.manager_here()
        self.log("   " + ("the model server runs here, so ComfyUI keeps beside Strata (8.5 GB)" if here else "no model server here: ComfyUI may use the whole card"))
        for spec in item.get("servers", []):
            spec["env"]["STRATA_MANAGER_URL"] = MANAGER if here else ""
        self.service(item, here)

    def install_ace_music(self, item):
        self.engine_files(item)

    install_pictures_more = install_qwen_image = install_wan_video = install_ace_music_xl = install_ace_music

    def install_voices(self, item):
        self.engine_env(item)
        for repo, rev, folder in SNAPSHOTS[item["id"]]:
            self.hf_snapshot(repo, rev, os.path.join(OPT, folder))
        self.service(item, self.manager_here())

    install_sounds = install_music = install_voices

    def blender_sum(self, url):
        try:
            name = os.path.basename(url)
            folder = url.rsplit("/", 1)[0]
            version = re.search(r"blender-([\d.]+)-", name).group(1)
            with urllib.request.urlopen(urllib.request.Request(f"{folder}/blender-{version}.sha256", headers=UA), timeout=30) as resp:
                for line in resp.read().decode().splitlines():
                    if line.strip().endswith(name):
                        return line.split()[0]
        except (OSError, ValueError, AttributeError):
            pass
        return None

    @staticmethod
    def sha256(path):
        import hashlib
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                h.update(block)
        return h.hexdigest()
