#!/usr/bin/env python3
"""Eidoloom Studio backend: a browser IDE server with an AI coding agent.

Python 3.10+, standard library only. See SPEC.md for the contract.
Run:  python3 studio.py [--host H] [--port P] [--config FILE]
"""
import argparse
import base64
import collections
import concurrent.futures
import difflib
import email.utils
import fcntl
import getpass
import glob
import hashlib
import hmac
import html
import http.client
import io
import http.cookies
import http.server
import importlib.util
import json
import mimetypes
import math
import os
import platform
import pty
import queue
import re
import secrets
import shlex
import shutil
import stat
import signal
import socket
import struct
import subprocess
import tempfile
import sys
import tarfile
import termios
import threading
import zipfile
import time
import traceback
import types
import urllib.error
import urllib.parse
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
WEB_DIR = os.path.join(SCRIPT_DIR, "web")

DEFAULT_CONFIG = {
    "host": "127.0.0.1",          # this machine only; "0.0.0.0" opens it to the network, so it must be chosen
    "port": 8790,
    "gateway": "http://127.0.0.1:8787",
    "gateway_key": "",
    "model_names": {},            # model id -> the name people see ("swift-1.5-iq3_xxs": "Keystone"); Account & settings → Models
    "strata_idle_minutes": 15,
    "strata_dir": "",             # a Strata checkout (e.g. ~/Projects/Tools/Strata): Studio starts its server itself, one profile at a
                                  # time, on the gateway's port, and switches profiles when a chat picks another; empty: Studio does not
    "strata_tools_reserve_mib": 9216,   # Graphics card → Tools ready, when Studio starts Strata: VRAM left free for pictures and voices    # Strata's own server (no Manager): Studio unloads the model after this long without requests; 0 never            # sent as "Authorization: Bearer ..." to model servers that need one (vLLM --api-key, llama.cpp --api-key)
    "workspace_roots": ["~/Projects"],
    "default_model": "keystone:strata",
    "max_turns": 40,
    "command_timeout_s": 120,
    "sandbox_default": False,
    "sandbox_required": False,
    "sandbox_image": "strata-sandbox:universal",
    "sandbox_network": "install",
    "sandbox_memory": "8g",
    "user_root": "~/Projects/studio-users",
    "public_url": "",
    "queue_idle_s": 600,
    "queue_max_s": 7200,
    "queue_offer_s": 120,
    "queue_warn_s": 120,
    "queue_presence_s": 60,
    "rule_reminders": True,
    "rule_reminder_turns": 20,
    "smart_trim": True,
    "delete_tool": True,
    "skills": True,
    "plan_tool": True,
    "live_view": True,
    "script_check": True,
    "script_ban": True,           # Strata models: tokens of Chinese, Japanese, Korean and Cyrillic text banned by logit_bias (script_ban_bias)
    "strata_vocab": "",           # the Strata tokenizer's vocab.json; empty: found from the Manager's or the checkout's profiles
    "edit_checks": True,
    "stable_prompt": True,
    "session_cache": True,        # save each chat's engine state to disk (Strata session files) and restore it after a reload
    "session_cache_dir": "~/Projects/Tools/Strata-data/sessions",   # the profiles' slot_save_path; Studio prunes its own files there
    "session_cache_keep": 6,      # chats kept on disk (about 1.2 GB per 63K tokens each)
    "announce_check": True,       # a run that worked, then stops on "I'll do X next" with no tool call, is sent back once
    "answer_check": True,         # a run that changed nothing and ends on a one-line finish, with no answer written, is sent back once
    "ask_tool": True,             # ask_user: the agent asks the user questions with choices, answered on a card
    "document_tool": True,        # write_document: the agent makes Word (.docx) and Excel (.xlsx) files
    "goal_max_rounds": 20,
    "tasks": True,
    "user_tools": True,           # the user's own simple tools: instructions, or a command with the model's input in STUDIO_INPUT
    "handoff_max_mb": 2048,       # passing a chat to someone copies its folder; larger folders are refused
    "timeskip_max_mb": 2048,      # Time-skip copies the folder; larger folders are refused                # scheduled agent runs and /loop, run by the server while nobody watches        # /goal: how many times a run is sent back to its goal before it stops to ask
    "turn_guard": True,           # a run going in circles (failing calls, plans rewritten, the same call again) gets a note, then stops
    "full_thinking": True,
    "no_planning_guard": True,
    "retry_cut_off": True,
    "text_tool_calls": True,
    "trim_chars": 90000,
    "verify_on_finish": True,
    "backup_interval_h": 24,
    "backup_keep": 14,
    "mcp": True,
    "mcp_call_timeout_s": 120,
    "tool_engines": [],           # sound and music engines (http://127.0.0.1:8190, :8191), released before a model turn, as voice_url is
    "voice_url": "",              # e.g. http://127.0.0.1:8189: tools/voice_engine.py, for the Voices view and the voice MCP server
    "updates": False,             # Account & settings → Updates for the third-party tools (updates.py); on only for this machine's own Studio
    "updates_catalog": "",        # "test": fake items, so the tests touch nothing
    "installer": True,            # Account & settings → Tools & setup: install Studio's tools on this machine (installer.py)
    "installer_catalog": "",      # "test": fake tools, so the tests install nothing
    "run_cleanup": True,          # when the last run ends: tool engines unload and ComfyUI frees its models (cleanup_main)
    "comfyui_service": "",        # a systemd user service restarted when ComfyUI still holds memory after a cleanup (e.g. comfyui)
    "comfyui_url": "",            # e.g. http://127.0.0.1:8188: a dashboard of what that ComfyUI makes for the comfyui MCP server
    "comfyui_swap_file": "",      # the comfyui MCP server's COMFYUI_SWAP_FILE, so the dashboard can say when Strata is unloaded
    "codex": False,               # admins may pick OpenAI's Codex (through `codex app-server` and their ChatGPT login) as the chat's agent
    "codex_command": "codex",     # the Codex CLI; the tests point it at tests/mock_codex.py
    "codex_free_card": True,      # unload an idle Strata before a Codex chat's first picture, voice, sound or Blender call
}

IGNORE_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
               ".next", ".strata-studio", ".pytest_cache"}
MAX_TEXT_BYTES = 2 * 1024 * 1024        # larger files are treated as binary by the files API
MAX_BODY_BYTES = 48 * 1024 * 1024        # a 25 MB document grows by a third as base64
SESSION_TTL = 24 * 3600
REMEMBER_TTL = 30 * 24 * 3600
INVITE_TTL = 7 * 24 * 3600
RESET_TTL = 3600
CHAT_MAX_CHARS = 1000
CHAT_KEEP = 200
CHAT_KEEP_S = 24 * 3600
CHAT_HISTORY = 50
USERNAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]{1,31}")
PBKDF2_ITERATIONS = 200_000
SSE_PING_S = 15
POLL_WAIT_S = 20             # how long a polling request waits for news. Quick tunnels hold back streams, so pages poll there
POLL_ONLINE_S = 15
TERM_BUFFER = 64 * 1024
TERM_IDLE_S = 2 * 3600
DEFAULT_STRATA_PROFILE = "qwen3.8-flash-next:125b-strata"
ID_RE = re.compile(r"^[0-9a-f]{6,32}$")
GATEWAY_HEADERS = {"Content-Type": "application/json", "X-Application-Name": "Strata Studio"}   # keeps the old name: the Manager exempts this value from its long-agent guard
DISCONNECT_ERRORS = (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError)
SANDBOX_LOCK = threading.Lock()
SANDBOX_NETWORKS = ("off", "install", "on")
# Sandboxes share one Docker network with a fixed bridge name, so sandbox/firewall.sh can target it.
SANDBOX_BRIDGE_NETWORK = "strata-sandbox"
SANDBOX_BRIDGE_INTERFACE = "strata-sbx0"
SANDBOX_IMAGE_RE = re.compile(r"[a-z0-9][a-zA-Z0-9._/:@-]{0,199}")
# Commands that fetch dependencies get internet in the "install" network mode. A heuristic: the whole command gets it.
INSTALL_RE = re.compile(
    r"\b(?:(?:npm|pnpm|yarn|bun)\s+(?:i|install|ci|add|update)\b"
    r"|(?:pip3?|uv|poetry|pipenv)\s+(?:pip\s+)?(?:install|add|sync|lock|update)\b"
    r"|python3?\s+-m\s+pip\s+install\b"
    r"|cargo\s+(?:build|fetch|install|add|update)\b"
    r"|go\s+(?:get|install|build|mod\s+(?:download|tidy))\b"
    r"|(?:mvn|mvnw|gradle|gradlew)\b"
    r"|(?:bundle|gem)\s+install\b"
    r"|composer\s+(?:install|require|update)\b"
    r"|dotnet\s+(?:restore|add|build)\b)")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

class ApiError(Exception):
    """An error that maps onto an HTTP status and a {"error": ...} body."""

    def __init__(self, status, message, **extra):
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra


def page_build():
    """A short fingerprint of the page's files, so an open page can tell that Studio now serves newer ones."""
    parts = []
    for folder, dirs, files in os.walk(WEB_DIR):
        dirs.sort()
        for name in sorted(files):
            try:
                st = os.stat(os.path.join(folder, name))
            except OSError:
                continue
            parts.append(f"{os.path.relpath(os.path.join(folder, name), WEB_DIR)}:{st.st_size}:{st.st_mtime_ns}")
    return hashlib.sha1("\n".join(parts).encode()).hexdigest()[:12]


def within(path, base):
    """True if absolute `path` equals or lies inside absolute `base`."""
    try:
        return os.path.commonpath([path, base]) == base
    except ValueError:
        return False


def write_json_atomic(path, obj, private=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{secrets.token_hex(4)}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600 if private else 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)
    os.replace(tmp, path)


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def to_int(value, default, lo=None, hi=None):
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    if lo is not None:
        n = max(lo, n)
    if hi is not None:
        n = min(hi, n)
    return n


def rel_posix(path, root):
    rel = os.path.relpath(path, root).replace(os.sep, "/")
    return rel


def decode_text(data):
    """Decode bytes as UTF-8 text; None when the data looks binary."""
    if b"\0" in data[:8192]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def load_config(path):
    if not os.path.exists(path):
        # a new install starts from the example beside it: this machine only, the code agent and no tool servers
        example = os.path.join(os.path.dirname(os.path.abspath(path)), "studio.config.example.json")
        write_json_atomic(path, read_json(example, None) if os.path.exists(example) else DEFAULT_CONFIG)
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(read_json(path, {}))
    return cfg


# ---------------------------------------------------------------------------
# Authentication (PBKDF2 password, cookie sessions, login rate limit)
# ---------------------------------------------------------------------------

class Auth:
    def __init__(self, data_dir):
        self.users_path = os.path.join(data_dir, "users.json")
        self.sessions_path = os.path.join(data_dir, "auth_sessions.json")
        self.tokens_path = os.path.join(data_dir, "auth_tokens.json")
        self.lock = threading.RLock()
        stored = read_json(self.users_path, None) or {}
        if "hash" in stored:                    # the single-password format becomes the "admin" account
            write_json_atomic(self.users_path + ".pre-accounts", stored, private=True)
            stored = {"users": {"admin": {**stored, "username": "admin", "display_name": "", "role": "admin",
                                          "disabled": False, "created": time.time()}}}
            write_json_atomic(self.users_path, stored, private=True)
        self.users = stored.get("users", {})
        now = time.time()
        # sessions and tokens are stored by hash so a leaked file is not a login
        self.sessions = {}
        for key, value in read_json(self.sessions_path, {}).items():
            if isinstance(value, (int, float)):
                value = {"expires": value, "user": "admin", "created": now, "remember": True}
            if value["expires"] > now:
                self.sessions[key] = value
        self.tokens = {k: v for k, v in read_json(self.tokens_path, {}).items() if v["expires"] > now}
        self.failures = collections.defaultdict(list)

    @staticmethod
    def _hash(password, salt, iterations):
        return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations).hex()

    @staticmethod
    def _key(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def _save_users(self):
        write_json_atomic(self.users_path, {"users": self.users}, private=True)

    def _save_sessions(self):
        write_json_atomic(self.sessions_path, self.sessions, private=True)

    def _save_tokens(self):
        write_json_atomic(self.tokens_path, self.tokens, private=True)

    @staticmethod
    def public_user(user):
        return {key: user.get(key) for key in ("username", "display_name", "role", "disabled", "created", "pc_folders", "card", "last_login")}

    def setup_needed(self):
        return not self.users

    def create_user(self, username, password, role, display_name=""):
        salt = secrets.token_bytes(16)
        with self.lock:
            if username in self.users:
                raise ApiError(409, "that username is taken")
            self.users[username] = {"username": username, "display_name": display_name, "role": role, "disabled": False,
                                    "created": time.time(), "salt": salt.hex(), "iterations": PBKDF2_ITERATIONS,
                                    "hash": self._hash(password, salt, PBKDF2_ITERATIONS)}
            self._save_users()

    def set_password(self, password, username="admin", display_name=""):
        with self.lock:
            if self.users:
                raise ApiError(403, "already set up")
            self.create_user(username, password, "admin", display_name)

    def change_password(self, username, password):
        salt = secrets.token_bytes(16)
        with self.lock:
            user = self.users[username]
            user.update(salt=salt.hex(), iterations=PBKDF2_ITERATIONS, hash=self._hash(password, salt, PBKDF2_ITERATIONS))
            self._save_users()

    def verify(self, username, password):
        user = self.users.get(username)
        # Unknown names cost the same as known ones, so timing does not reveal which usernames exist.
        salt, iterations = (bytes.fromhex(user["salt"]), user["iterations"]) if user else (b"\0" * 16, PBKDF2_ITERATIONS)
        digest = self._hash(password, salt, iterations)
        return user if user and hmac.compare_digest(digest, user["hash"]) else None

    def _active_admins(self):
        return [u for u in self.users.values() if u["role"] == "admin" and not u.get("disabled")]

    def update_user(self, username, role=None, disabled=None, pc_folders=None, card=None):
        with self.lock:
            user = self.users.get(username)
            if not user:
                raise ApiError(404, "unknown user")
            removes_admin = user["role"] == "admin" and not user.get("disabled") and (role == "user" or disabled is True)
            if removes_admin and len(self._active_admins()) == 1:
                raise ApiError(409, "this is the last active admin")
            if role is not None:
                user["role"] = role
            if disabled is not None:
                user["disabled"] = disabled
            if pc_folders is not None:
                user["pc_folders"] = pc_folders
            if card is not None:
                user["card"] = card
            self._save_users()
            return user

    def delete_user(self, username):
        with self.lock:
            user = self.users.get(username)
            if not user:
                raise ApiError(404, "unknown user")
            if user["role"] == "admin" and not user.get("disabled") and len(self._active_admins()) == 1:
                raise ApiError(409, "this is the last active admin")
            del self.users[username]
            self._save_users()
            self.revoke_sessions(username)

    def locked(self, ip):
        with self.lock:
            recent = [t for t in self.failures[ip] if time.time() - t < 60]
            self.failures[ip] = recent
            return len(recent) >= 5

    def record_failure(self, ip):
        with self.lock:
            self.failures[ip].append(time.time())

    def new_session(self, username, remember=False, device=""):
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self.lock:
            self.sessions[self._key(token)] = {"expires": now + (REMEMBER_TTL if remember else SESSION_TTL), "user": username,
                                               "created": now, "remember": remember, "device": device[:80]}
            if username in self.users:
                self.users[username]["last_login"] = now
                self._save_users()
            self._save_sessions()
        return token

    def sessions_of(self, username, current_token=None):
        """This person's signed-in browsers: an id (part of the stored hash, never the token), when, which device."""
        current = self._key(current_token) if current_token else None
        now = time.time()
        with self.lock:
            return sorted(({"id": k[:16], "created": v.get("created"), "device": v.get("device") or "Unknown browser", "remember": v.get("remember", False),
                            "current": k == current} for k, v in self.sessions.items() if v["user"] == username and v["expires"] > now),
                          key=lambda x: -(x["created"] or 0))

    def revoke_session(self, username, sid):
        with self.lock:
            found = [k for k, v in self.sessions.items() if v["user"] == username and k.startswith(sid)]
            if len(found) != 1:
                return False
            del self.sessions[found[0]]
            self._save_sessions()
            return True

    def set_display_name(self, username, name):
        with self.lock:
            self.users[username]["display_name"] = name
            self._save_users()

    def session_user(self, token):
        """The signed-in account for a session token, or None."""
        if not token:
            return None
        key = self._key(token)
        with self.lock:
            session = self.sessions.get(key)
            if session is None:
                return None
            if session["expires"] < time.time():
                del self.sessions[key]
                return None
            user = self.users.get(session["user"])
            return user if user and not user.get("disabled") else None

    def valid(self, token):
        return self.session_user(token) is not None

    def drop(self, token):
        if not token:
            return
        with self.lock:
            self.sessions.pop(self._key(token), None)
            self._save_sessions()

    def revoke_sessions(self, username, keep_token=None):
        keep = self._key(keep_token) if keep_token else None
        with self.lock:
            self.sessions = {k: v for k, v in self.sessions.items() if v["user"] != username or k == keep}
            self._save_sessions()

    def issue_token(self, kind, ttl, **fields):
        token = secrets.token_urlsafe(32)
        with self.lock:
            if kind == "reset":                 # a new reset link replaces any older one for that user
                self.tokens = {k: v for k, v in self.tokens.items() if not (v["kind"] == "reset" and v.get("username") == fields.get("username"))}
            self.tokens[self._key(token)] = {"kind": kind, "created": time.time(), "expires": time.time() + ttl, **fields}
            self._save_tokens()
        return token

    def peek_token(self, token, kind):
        if not isinstance(token, str) or not token:
            return None
        with self.lock:
            record = self.tokens.get(self._key(token))
            return record if record and record["kind"] == kind and record["expires"] > time.time() else None

    def take_token(self, token, kind):
        """Use up a one-time token. Only one request can ever succeed."""
        with self.lock:
            record = self.peek_token(token, kind)
            if record:
                del self.tokens[self._key(token)]
                self._save_tokens()
            return record

    def list_invites(self):
        now = time.time()
        with self.lock:
            return [{"id": key[:16], "role": v.get("role"), "created": v["created"], "expires": v["expires"], "by": v.get("by")}
                    for key, v in self.tokens.items() if v["kind"] == "invite" and v["expires"] > now]

    def revoke_invite(self, invite_id):
        with self.lock:
            for key, value in list(self.tokens.items()):
                if value["kind"] == "invite" and isinstance(invite_id, str) and len(invite_id) == 16 and key.startswith(invite_id):
                    del self.tokens[key]
                    self._save_tokens()
                    return True
        return False


# ---------------------------------------------------------------------------
# Waiting room: one person at a time holds the agent's seat
# ---------------------------------------------------------------------------

TURNS_KEEP = 30


class WaitingRoom:
    """Only the seat holder can start agent runs. Everyone else waits in line and keeps editing.

    The seat passes on when the holder finishes, stays idle while others wait, or reaches the session limit
    between runs. The next person gets a short offer to accept. A run in progress is never cut off by a timer,
    unless it has been sitting at an unanswered approval while others wait."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.lock = threading.RLock()
        self.cond = threading.Condition(self.lock)
        self.holder = None
        self.queue = []
        self.streams = collections.Counter()
        self.seen = {}
        self.polled = {}
        self.external = None
        self.messages = []
        self.chat_epoch = 0
        self.chat_sent = collections.defaultdict(list)
        self.turns = []          # (ended, seconds) of recent turns, for the wait estimate and the day's count

    def end_turn(self):
        if self.holder:
            now = time.time()
            self.turns = [t for t in self.turns if now - t[0] < 86400][-(TURNS_KEEP - 1):] + [(now, now - self.holder["since"])]
        self.holder = None
        self.changed()

    def typical_turn(self):
        """The middle length of recent turns, or a guess from the rules before there are three."""
        lengths = sorted(seconds for _, seconds in self.turns[-TURNS_KEEP:] if seconds >= 30)
        if len(lengths) < 3:
            return min(self.rule("queue_max_s"), 20 * 60)
        return min(lengths[len(lengths) // 2], self.rule("queue_max_s"))

    def rule(self, name):
        return self.cfg.get(name, DEFAULT_CONFIG[name])

    def changed(self):
        with self.cond:
            self.cond.notify_all()

    @staticmethod
    def run_for(username):
        return next((run for run in list(APP.active.values()) if run.owner == username), None)

    def find(self, username):
        return next((entry for entry in self.queue if entry["username"] == username), None)

    def activity(self, username):
        with self.lock:
            if self.holder and self.holder["username"] == username:
                self.holder["activity"] = time.time()

    def mark_seen(self, username):
        with self.lock:
            self.seen[username] = time.time()

    def mark_polled(self, username):
        """A window that polls is present like one with an open stream, for as long as it keeps asking."""
        with self.lock:
            self.seen[username] = self.polled[username] = time.time()

    def connect(self, username):
        with self.lock:
            self.streams[username] += 1
            self.seen[username] = time.time()

    def disconnect(self, username):
        with self.lock:
            self.streams[username] -= 1
            self.seen[username] = time.time()

    def grant(self, user):
        now = time.time()
        self.holder = {"username": user["username"], "name": user.get("display_name") or user["username"], "since": now, "activity": now}
        self.changed()

    def enter_line(self, user):
        if not self.find(user["username"]):
            self.queue.append({"username": user["username"], "name": user.get("display_name") or user["username"],
                               "joined": time.time(), "offer_expires": None})
            self.changed()

    def over_limit(self):
        return bool(self.holder and self.queue and time.time() - self.holder["since"] >= self.rule("queue_max_s"))

    def idle_deadline(self):
        """Someone joining gets the holder a fair warning, even if the holder has been idle for a long time."""
        deadline = self.holder["activity"] + self.rule("queue_idle_s")
        if self.queue:
            deadline = max(deadline, self.queue[0]["joined"] + self.rule("queue_warn_s"))
        return deadline

    def claim(self, user):
        """Called before an agent run starts. Takes a free seat, or joins the line and refuses."""
        username = user["username"]
        with self.lock:
            self.seen[username] = time.time()
            if self.holder and self.holder["username"] == username:
                if self.over_limit():
                    raise ApiError(409, "your session limit has passed and others are waiting. Your turn ends now", waiting_room=True)
                self.holder["activity"] = time.time()
                return
            if not self.holder and (not self.queue or self.queue[0]["username"] == username):
                if self.queue:
                    self.queue.pop(0)
                self.grant(user)
                return
            self.enter_line(user)
            raise ApiError(423, f"{self.holder['name'] if self.holder else 'Someone'} has the agent right now. You are in line", waiting_room=True)

    def join(self, user):
        with self.lock:
            self.seen[user["username"]] = time.time()
            if self.holder and self.holder["username"] == user["username"]:
                return
            if not self.holder and not self.queue:
                self.grant(user)
            else:
                self.enter_line(user)

    def leave(self, username):
        with self.lock:
            entry = self.find(username)
            if not entry:
                raise ApiError(404, "you are not in line")
            self.queue.remove(entry)
            self.changed()

    def accept(self, user):
        with self.lock:
            entry = self.queue[0] if self.queue else None
            if self.holder or not entry or entry["username"] != user["username"] or not entry["offer_expires"]:
                raise ApiError(409, "it is not your turn")
            self.queue.pop(0)
            self.grant(user)

    def release(self, username):
        with self.lock:
            if not self.holder or self.holder["username"] != username:
                raise ApiError(409, "you do not have the agent")
            if self.run_for(username):
                raise ApiError(409, "stop the agent before finishing your turn")
            self.end_turn()
            self.changed()

    def keep(self, username):
        with self.lock:
            if not self.holder or self.holder["username"] != username:
                raise ApiError(409, "you do not have the agent")
            self.holder["activity"] = time.time()
            self.changed()

    def remove_user(self, username):
        with self.lock:
            entry = self.find(username)
            if entry:
                self.queue.remove(entry)
            if self.holder and self.holder["username"] == username:
                self.end_turn()
            self.changed()

    def admin_end(self):
        with self.lock:
            if not self.holder:
                raise ApiError(409, "nobody has the agent")
            run = self.run_for(self.holder["username"])
            self.end_turn()
            self.changed()
        if run:
            run.request_stop()

    def admin_remove(self, username):
        with self.lock:
            entry = self.find(username)
            if not entry:
                raise ApiError(404, "that user is not in line")
            self.queue.remove(entry)
            self.changed()

    def admin_move(self, username, position):
        with self.lock:
            entry = self.find(username)
            if not entry:
                raise ApiError(404, "that user is not in line")
            self.queue.remove(entry)
            self.queue.insert(max(0, min(position, len(self.queue))), entry)
            for other in self.queue:
                other["offer_expires"] = None   # an offer belongs to whoever is first now
            self.changed()

    def tick(self):
        now = time.time()
        stopping = None
        with self.lock:
            for entry in list(self.queue):
                name = entry["username"]
                if self.streams[name] <= 0 and now - self.seen.get(name, 0) > self.rule("queue_presence_s"):
                    self.queue.remove(entry)
                    self.changed()
            if self.holder:
                run = self.run_for(self.holder["username"])
                if not run:
                    if now >= self.idle_deadline() or self.over_limit():
                        self.end_turn()
                        self.changed()
                elif run.approvals and self.queue and now >= self.idle_deadline():
                    stopping = run
                    self.end_turn()
                    self.changed()
            if not self.holder:
                while self.queue:
                    first = self.queue[0]
                    if first["offer_expires"] is None:
                        first["offer_expires"] = now + self.rule("queue_offer_s")
                        self.changed()
                    if first["offer_expires"] > now:
                        break
                    self.queue.pop(0)
                    self.changed()
        if stopping:
            stopping.request_stop()

    def wait_for(self, position, now):
        """Roughly how long until this place in line gets the agent: what is left of the current turn, then a typical turn
        for each person ahead. An offer waiting to be taken counts as a short wait."""
        left = 0
        if self.holder:
            run = self.run_for(self.holder["username"])
            estimate = estimate_run(run, now) if run else None
            if estimate and estimate.get("state") == "range":
                left = estimate["high_min"] * 60
            elif run:
                left = max(0, self.holder["since"] + self.rule("queue_max_s") - now) if self.queue else self.typical_turn() / 2
            else:
                left = max(0, self.idle_deadline() - now)
        elif self.queue and self.queue[0]["offer_expires"]:
            left = max(0, self.queue[0]["offer_expires"] - now)
        return left + (position - 1) * self.typical_turn()

    def post_message(self, user, text, to=None):
        """Chat between people. It lives only here and is never part of what the model receives."""
        text = text.strip() if isinstance(text, str) else ""
        if not text:
            raise ApiError(400, "write a message first")
        if len(text) > CHAT_MAX_CHARS:
            raise ApiError(400, f"messages can have up to {CHAT_MAX_CHARS} characters")
        username, now = user["username"], time.time()
        target = APP.auth.users.get(to) if to else None
        if to and not target:
            raise ApiError(404, "unknown user")
        if to == username:
            raise ApiError(400, "you cannot message yourself")
        with self.lock:
            recent = [t for t in self.chat_sent[username] if now - t < 10]
            if len(recent) >= 8:
                raise ApiError(429, "slow down a little, then try again")
            self.chat_sent[username] = recent + [now]
            newest = self.messages[-1]["id"] if self.messages else 0
            message = {"id": max(int(now * 1000), newest + 1), "from": username, "name": user.get("display_name") or username,
                       "to": to or None, "to_name": (target.get("display_name") or to) if target else None, "text": text, "at": now}
            self.messages = (self.messages + [message])[-CHAT_KEEP:]
            self.changed()
        return message

    def messages_for(self, username, after=0):
        """Everything public, plus private messages this person sent or received. Admins get no extra access."""
        cutoff = time.time() - CHAT_KEEP_S
        with self.lock:
            visible = [m for m in self.messages if m["at"] >= cutoff and m["id"] > after and (not m["to"] or username in (m["to"], m["from"]))]
        return visible[-CHAT_HISTORY:]

    def clear_chat(self):
        with self.lock:
            self.messages = []
            self.chat_epoch += 1
            self.changed()

    def state_for(self, user):
        now = time.time()
        username = user["username"]
        with self.lock:
            # Also the holder and the line, even during a brief disconnect, so they can always be messaged.
            online = sorted({name for name, count in self.streams.items() if count > 0}
                            | {name for name, when in self.polled.items() if now - when < POLL_ONLINE_S}
                            | {entry["username"] for entry in self.queue} | ({self.holder["username"]} if self.holder else set()))
            places = {e["username"]: i + 1 for i, e in enumerate(self.queue)}
            midnight = time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, -1))
            out = {"free": not self.holder, "holder": None, "external": self.external, "chat_epoch": self.chat_epoch,
                   "online": [{"username": name, "name": APP.auth.users[name].get("display_name") or name,
                               "holds": bool(self.holder and self.holder["username"] == name), "position": places.get(name)}
                              for name in online if name in APP.auth.users],
                   "stats": {"turns_today": sum(1 for ended, _ in self.turns if ended >= midnight) + (1 if self.holder else 0),
                             "typical_turn_s": int(self.typical_turn()), "measured": len(self.turns) >= 3},
                   "handoffs": handoffs_for(username),
                   "rules": {key: self.rule("queue_" + key + "_s") for key in ("idle", "max", "offer")},
                   "queue": [{"username": e["username"], "name": e["name"], "waited": int(now - e["joined"]),
                              "offered": bool(e["offer_expires"]), "wait_s": int(self.wait_for(i + 1, now))} for i, e in enumerate(self.queue)]}
            # What joining now would mean, for the Join button of someone neither holding the agent nor in line.
            if (self.holder or self.queue) and not self.find(username) and not (self.holder and self.holder["username"] == username):
                out["join_wait_s"] = int(self.wait_for(len(self.queue) + 1, now))
            me = {"holds": False, "position": None, "offered": False, "offer_left": None, "warn": None, "idle_left": None, "limit_left": None}
            if self.holder:
                run = self.run_for(self.holder["username"])
                out["holder"] = {"username": self.holder["username"], "name": self.holder["name"], "held_for": int(now - self.holder["since"]),
                                 "running": bool(run), "waiting_approval": bool(run and run.approvals), "model": run.model if run else None,
                                 "estimate": estimate_run(run, now) if run else None,
                                 "idle_left": int(self.idle_deadline() - now) if not run and self.queue else None}
                if self.holder["username"] == username:
                    limit_left = int(self.holder["since"] + self.rule("queue_max_s") - now)
                    idle_left = int(self.idle_deadline() - now)
                    me.update(holds=True, limit_left=limit_left if self.queue else None, idle_left=idle_left if not run or run.approvals else None)
                    warn = self.rule("queue_warn_s")
                    if self.queue and limit_left <= warn:
                        me["warn"] = "limit"
                    elif self.queue and (not run or run.approvals) and idle_left <= warn:
                        me["warn"] = "idle"
            entry = self.find(username)
            if entry:
                me["position"] = self.queue.index(entry) + 1
                if me["position"] == 1 and entry["offer_expires"]:
                    me.update(offered=True, offer_left=max(0, int(entry["offer_expires"] - now)))
                me["wait_s"] = int(self.wait_for(me["position"], now))
            out["me"] = me
            return out


def estimate_run(run, now=None):
    """How long a run may have left, from the model's step count and the time its finished steps really took.

    Never a promise: "early" until one step is finished, and "over" once the measured pace has been passed."""
    now = now or time.time()
    out = {"step": None, "total": None}
    if run.progress:
        out.update(step=run.progress[0], total=run.progress[1])
    if run.approvals:
        return {**out, "state": "approval"}
    if not run.progress or run.progress[0] <= 1:
        return {**out, "state": "early"}
    step, total, step_began = run.progress
    per_step = (step_began - run.started) / (step - 1)
    remaining = (total - step + 1) * per_step - (now - step_began)
    if remaining <= 0:
        return {**out, "state": "over"}
    low = max(1, round(remaining * 0.7 / 60))
    return {**out, "state": "range", "low_min": low, "high_min": max(low, math.ceil(remaining * 1.5 / 60))}


def waiting_room_loop():
    while True:
        time.sleep(0.5)
        try:
            APP.room.tick()
        except Exception:
            traceback.print_exc()


def external_activity_loop():
    """Strata can also be used from the Manager, its arena or the bridge, which this lock cannot stop."""
    while True:
        time.sleep(5)
        try:
            lifecycle = strata_status(timeout=4).get("lifecycle") or {}
            busy = bool(lifecycle.get("active_requests")) and not APP.active
            external = {"source": lifecycle.get("wake_source") or "another app"} if busy else None
        except (OSError, ValueError, http.client.HTTPException):
            external = None
        if external != APP.room.external:
            APP.room.external = external
            APP.room.changed()


# ---------------------------------------------------------------------------
# Application state
# ---------------------------------------------------------------------------

class App:
    def __init__(self, cfg, config_path):
        self.cfg = cfg
        self.config_path = config_path
        self.data_dir = os.path.join(os.path.dirname(os.path.realpath(config_path)), "data")
        for sub in ("sessions", "checkpoints"):
            os.makedirs(os.path.join(self.data_dir, sub), exist_ok=True)
        # Browsers share cookies across ports, so each data directory needs its own cookie name.
        self.cookie_name = "studio_session_" + hashlib.sha1(self.data_dir.encode()).hexdigest()[:8]
        self.auth = Auth(self.data_dir)
        self.lock = threading.RLock()
        self.prefs_lock = threading.Lock()
        self.skills_lock = threading.Lock()
        self.preview_tokens = {}
        self.live_tokens = {}
        self.sync_seq = 0
        self.term_seq = 0
        self.sync_log = collections.deque(maxlen=SYNC_LOG_SIZE)
        self.workspaces_path = os.path.join(self.data_dir, "workspaces.json")
        self.workspaces = read_json(self.workspaces_path, {})
        self.terminals = {}
        self.runs = collections.OrderedDict()
        self.active = {}                       # workspace id -> Run
        self.models_cache = {"t": 0.0, "models": [], "ok": False}
        self.strata_models = set()          # models served by Strata's own server (gateway_kind "strata")
        self.gateway_kind = ""            # "ollama" (Ollama or the Manager) or "openai" (llama.cpp, vLLM, LM Studio), from the last model list
        self.stream_chars = 0                  # model text streamed so far, for the dashboard's speed gauge
        self.stream_at = 0.0
        self.load_times = read_json(os.path.join(self.data_dir, "load_times.json"), {})   # model -> seconds its last load took
        self.load_seen = {}                    # model -> start time of a load being watched, to time it
        self.room = WaitingRoom(cfg)
        self.mcp = McpManager(self.data_dir)
        self.mcp_edit = threading.Lock()      # the panel's saves and the installer's additions, one at a time
        self.updates = None
        self.installer = None
        self.tasks = TaskBook(self.data_dir)
        self.handoff_lock = threading.RLock()

    # -- workspaces ---------------------------------------------------------
    def roots(self):
        out = []
        for r in self.cfg["workspace_roots"]:
            real = os.path.realpath(os.path.expanduser(r))
            if os.path.isdir(real) and real not in out:
                out.append(real)
        return out

    def in_roots(self, real):
        return any(within(real, r) for r in self.roots())

    def user_base(self):
        return os.path.realpath(os.path.expanduser(self.cfg.get("user_root") or DEFAULT_CONFIG["user_root"]))

    def roots_for(self, user):
        """Admins see every configured root. Everyone else sees only their private folder."""
        if user["role"] == "admin":
            return self.roots()
        home = os.path.join(self.user_base(), user["username"])
        os.makedirs(home, exist_ok=True)
        return [home]

    def in_roots_for(self, real, user):
        return any(within(real, r) for r in self.roots_for(user))

    def open_workspace(self, path, user):
        real = os.path.realpath(os.path.expanduser(path or ""))
        if not self.in_roots_for(real, user):
            shared = next((w for w in self.workspaces.values() if os.path.realpath(w["path"]) == real
                           and user["username"] in (w.get("members") or {})), None)
            if shared and os.path.isdir(real):
                return self.public_ws(shared)          # shared with this person: opened as it is, owner unchanged
            raise ApiError(403, "folder is outside the allowed workspace roots")
        if not os.path.isdir(real):
            raise ApiError(400, "not a directory")
        wid = hashlib.sha1(real.encode()).hexdigest()[:12]
        with self.lock:
            previous = self.workspaces.get(wid) or {}
            base = self.user_base()
            # Anything inside a user's private folder belongs to that user, whoever opens it.
            owner = os.path.relpath(real, base).split(os.sep)[0] if real != base and within(real, base) else None
            self.workspaces[wid] = {**previous, "id": wid, "path": real, "owner": owner,
                                    "name": os.path.basename(real) or real, "opened": time.time()}
            write_json_atomic(self.workspaces_path, self.workspaces)
            return self.public_ws(self.workspaces[wid])

    @staticmethod
    def public_ws(ws):
        return {"id": ws["id"], "path": ws["path"], "name": ws["name"], "owner": ws.get("owner"), "sandbox": sandboxed(ws),
                "helper": HELPERS.public(ws["helper"]) if ws.get("helper") else None, "place": folder_place(ws),
                "shared_by": ws.get("owner") or ws.get("manager"), "members": len(ws.get("members") or {}),
                **{f"sandbox_{key}": value for key, value in sandbox_settings(ws).items()}}

    def get_workspace(self, wid, user):
        ws = self.workspaces.get(wid) if isinstance(wid, str) else None
        member = bool(ws) and user["username"] in (ws.get("members") or {})
        if not ws or (user["role"] != "admin" and ws.get("owner") != user["username"] and not member):
            raise ApiError(404, "unknown workspace")
        if not os.path.isdir(ws["path"]) or not (member or self.in_roots_for(ws["path"], user)):
            raise ApiError(403, "workspace is no longer available")
        return ws

    def recent_workspaces(self, user):
        items = sorted(self.workspaces.values(), key=lambda w: w.get("opened", 0), reverse=True)
        out = []
        for w in items:
            member = user["username"] in (w.get("members") or {})
            if os.path.isdir(w["path"]) and (member or self.in_roots_for(w["path"], user)):
                root = next((r for r in self.roots_for(user) if within(w["path"], r)), "")
                if member and not root:
                    out.append({**self.public_ws(w), "where": f"Shared by {w.get('owner') or w.get('manager') or 'an admin'}"})
                    continue
                rel = os.path.relpath(w["path"], root) if root else w["path"]
                out.append({**self.public_ws(w), "where": root_label(user, root) + ("" if rel == "." else " › " + rel.replace("/", " › "))})
        return out[:10]


APP = None


# ---------------------------------------------------------------------------
# Model gateway: the Ollama Manager, Ollama itself, or any server with the OpenAI API (llama.cpp's llama-server,
# vLLM, LM Studio). Every one of them takes /v1/chat/completions; the model list comes from Ollama's /api/tags or,
# failing that, from /v1/models. Only the Manager has /api/strata/*, and only Ollama and the Manager have /api/ps.
# ---------------------------------------------------------------------------

class GatewayStatus(OSError):
    """The gateway answered with something other than 200."""

    def __init__(self, status):
        super().__init__(f"gateway returned {status}")
        self.status = status


def gateway_connect(timeout):
    parts = urllib.parse.urlsplit(APP.cfg["gateway"])
    https = parts.scheme == "https"
    cls = http.client.HTTPSConnection if https else http.client.HTTPConnection
    conn = cls(parts.hostname, parts.port or (443 if https else 80), timeout=timeout)
    prefix = parts.path.rstrip("/")
    return conn, prefix[:-3] if prefix.endswith("/v1") else prefix      # "http://host:8000/v1" is the same server


def gateway_headers():
    key = APP.cfg.get("gateway_key") or ""
    return {**GATEWAY_HEADERS, **({"Authorization": f"Bearer {key}"} if key else {})}


def gateway_json(method, path, payload=None, timeout=8):
    conn, prefix = gateway_connect(timeout)
    try:
        body = json.dumps(payload).encode() if payload is not None else None
        conn.request(method, prefix + path, body, gateway_headers())
        resp = conn.getresponse()
        data = resp.read()
        if resp.status != 200:
            raise GatewayStatus(resp.status)
        return json.loads(data)
    finally:
        conn.close()


def openai_models():
    """The models of a server with only the OpenAI API: /v1/models' ids."""
    data = gateway_json("GET", "/v1/models").get("data")
    if not isinstance(data, list):
        raise ValueError("no model list")
    return [{"name": m["id"]} for m in data if isinstance(m, dict) and isinstance(m.get("id"), str) and m["id"]]


def strata_label(name):
    if name == DEFAULT_STRATA_PROFILE:
        return "Strata default profile"
    m = re.match(r"^(.+?)-sub(\d+):strata$", name)
    if m:
        return f"{m.group(1).replace('-', ' ').title()} (Strata) + {m.group(2)} GB subagent pool"
    base = name.split(":")[0].replace("-", " ").title()
    return f"{base} (Strata)"


def strata_hidden(name):
    if name == DEFAULT_STRATA_PROFILE:
        return False
    if re.search(r"-(full|sub4|sub6):strata$", name):
        return True
    return bool(re.search(r":[^:]*strata-", name))       # long profile names


def model_label(name, default):
    """The name an admin gave the model (config model_names), else the default."""
    given = (APP.cfg.get("model_names") or {}).get(name)
    return given if isinstance(given, str) and given.strip() else default


def strata_direct_label(name):
    """ "swift-1.5-iq3_xxs" → "Swift 1.5 IQ3_XXS (Strata)"."""
    words = [w.upper() if "_" in w or re.fullmatch(r"[a-z]*\d[a-z\d]*", w) and not re.fullmatch(r"[\d.]+", w) else w.capitalize()
             for w in re.split(r"[-\s]+", name.split(":")[0]) if w]
    return " ".join(words) + " (Strata)"


def fetch_models():
    profiles = strata_profiles()
    if profiles:              # Studio starts Strata itself: every profile is a model, whether or not its server runs now
        APP.strata_models, APP.gateway_kind = set(profiles), "strata"
        return sorted(({"name": name, "label": model_label(name, strata_direct_label(name)), "kind": "strata", "tools": True,
                        "vision": bool(p["config"].get("vision")), "size": None, "direct": True, "profile": p["stem"]}
                       for name, p in profiles.items()), key=lambda m: m["label"].lower())
    try:
        tags = gateway_json("GET", "/api/tags")
        entries = [m for m in tags.get("models", []) if isinstance(m, dict) and m.get("name")]
    except (GatewayStatus, ValueError):
        # llama.cpp, vLLM, LM Studio: no Ollama API, so nothing says whether a model takes tools or pictures
        # (None: tools are offered, pictures accepted, and screenshots are not sent as pictures)
        listed = openai_models()
        st = strata_direct_status()
        if st:                    # Strata's own server: its models take tools and, with its picture reader, pictures
            APP.strata_models = {m["name"] for m in listed}
            APP.gateway_kind = "strata"
            vision = bool((st.get("vision") or {}).get("enabled"))
            return [{"name": m["name"], "label": model_label(m["name"], strata_direct_label(m["name"])), "kind": "strata", "tools": True,
                     "vision": vision, "size": None, "direct": True} for m in listed]
        APP.gateway_kind = "openai"
        return [{"name": m["name"], "label": model_label(m["name"], m["name"]), "kind": "openai", "tools": None, "vision": None, "size": None}
                for m in listed]
    APP.gateway_kind = "ollama"

    def tools_support(name):
        """(tools, vision) for a model: True, False, or None when the gateway does not say."""
        if "strata" in name.lower():
            return True, True
        try:
            caps = gateway_json("POST", "/api/show", {"model": name}).get("capabilities")
            return (("tools" in caps), ("vision" in caps)) if isinstance(caps, list) else (None, None)
        except (OSError, ValueError, http.client.HTTPException):
            return None, None

    visible = [m for m in entries if not ("strata" in m["name"].lower() and strata_hidden(m["name"]))]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        tools = list(pool.map(lambda m: tools_support(m["name"]), visible))
    out = []
    for m, (t, vision) in zip(visible, tools):
        strata = "strata" in m["name"].lower()
        out.append({"name": m["name"], "label": model_label(m["name"], strata_label(m["name"]) if strata else m["name"]),
                    "kind": "strata" if strata else "ollama", "tools": t, "vision": vision, "size": m.get("size")})
    out.sort(key=lambda m: (m["kind"] != "strata", m["name"].lower()))
    return out


def get_models():
    """Return (models, gateway_ok), cached for 30 s."""
    with APP.lock:
        cache = APP.models_cache
        if time.time() - cache["t"] < 30:
            return cache["models"], cache["ok"]
    try:
        models, ok = fetch_models(), True
    except (OSError, ValueError, http.client.HTTPException):
        models, ok = [], False
    with APP.lock:
        APP.models_cache = {"t": time.time(), "models": models, "ok": ok}
    return models, ok


# ---------------------------------------------------------------------------
# Path confinement and file helpers
# ---------------------------------------------------------------------------

def resolve(ws, rel, nofollow=False):
    """Map a workspace-relative path to an absolute one that is guaranteed to stay inside the workspace.

    With nofollow the final component is not dereferenced (so a symlink itself can be renamed or trashed).
    """
    if not isinstance(rel, str) or "\0" in rel:
        raise ApiError(400, "invalid path")
    rel = rel.replace("\\", "/")
    if rel.startswith("/") or re.match(r"^[A-Za-z]:", rel):
        raise ApiError(403, "path outside workspace")
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if ".." in parts:
        raise ApiError(403, "path outside workspace")
    full = os.path.join(ws["path"], *parts)
    if nofollow and parts:
        target = os.path.join(os.path.realpath(os.path.dirname(full)), parts[-1])
    else:
        target = os.path.realpath(full)
    if not within(target, ws["path"]):
        raise ApiError(403, "path outside workspace")
    return target


def read_text_file(path):
    """Return (text, mtime); text is None for binary or oversized files."""
    st = os.stat(path)
    if st.st_size > MAX_TEXT_BYTES:
        return None, st.st_mtime
    with open(path, "rb") as fh:
        return decode_text(fh.read()), st.st_mtime


def write_bytes(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def move_to_trash(ws, abs_path):
    rel = rel_posix(abs_path, ws["path"])
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(ws["path"], ".strata-studio", "trash", stamp, rel)
    n = 1
    while os.path.lexists(dest):
        n += 1
        dest = os.path.join(ws["path"], ".strata-studio", "trash", f"{stamp}-{n}", rel)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.move(abs_path, dest)
    return rel_posix(dest, ws["path"])


def iter_files(root, start):
    """Yield (absolute, relative) text-candidate files below `start`, skipping ignored dirs."""
    if os.path.isfile(start):
        yield start, rel_posix(start, root)
        return
    for dirpath, dirnames, filenames in os.walk(start):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORE_DIRS)
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            yield full, rel_posix(full, root)


def search_tree(root, start, query, regex, case, limit, seconds):
    flags = 0 if case else re.IGNORECASE
    try:
        pat = re.compile(query if regex else re.escape(query), flags)
    except re.error as exc:
        raise ApiError(400, f"invalid regex: {exc}")
    deadline = time.monotonic() + seconds
    matches = []
    for full, rel in iter_files(root, start):
        if time.monotonic() > deadline:
            return matches, True
        try:
            if os.path.islink(full) and not within(os.path.realpath(full), root):
                continue
            text, _ = read_text_file(full)
        except OSError:
            continue
        if text is None:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if pat.search(line[:2000]):
                matches.append({"path": rel, "line": lineno, "text": line[:300]})
                if len(matches) >= limit:
                    return matches, True
    return matches, False


def tree_lines(root_abs, depth, limit):
    """Indented listing of a directory (dirs first), used by the agent and its system prompt."""
    lines = []

    def walk(directory, level):
        try:
            entries = sorted(os.scandir(directory),
                             key=lambda e: (not e.is_dir(), e.name.lower()))
        except OSError:
            return
        for e in entries:
            if len(lines) >= limit:
                return
            if e.is_dir() and e.name in IGNORE_DIRS:
                continue
            lines.append("  " * level + e.name + ("/" if e.is_dir() else ""))
            if e.is_dir() and not e.is_symlink() and level + 1 < depth:
                walk(e.path, level + 1)

    walk(root_abs, 0)
    return lines, len(lines) >= limit


# ---------------------------------------------------------------------------
# HTTP plumbing: routing, SSE helpers, static files
# ---------------------------------------------------------------------------

ROUTES = {}                      # (method, path) -> (function, public, admin only)


def route(method, path, public=False, admin=False):
    def deco(fn):
        ROUTES[(method, path)] = (fn, public, admin)
        return fn
    return deco


CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
                 ".json": "application/json; charset=utf-8", ".svg": "image/svg+xml",
                 ".map": "application/json; charset=utf-8", ".woff2": "font/woff2",
                 ".ico": "image/x-icon", ".txt": "text/plain; charset=utf-8"}


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "StrataStudio"
    sys_version = ""
    timeout = 300

    # -- logging: only errors ----------------------------------------------
    def log_request(self, code="-", size="-"):
        if to_int(code, 0) >= 400:
            path = urllib.parse.urlsplit(self.path).path
            sys.stderr.write(f"{self.log_date_time_string()} {self.command} {path} -> {code}\n")

    def log_message(self, fmt, *args):
        sys.stderr.write(f"{self.log_date_time_string()} {fmt % args}\n")

    # -- response helpers ---------------------------------------------------
    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        super().end_headers()

    def send_json(self, status, obj):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Studio-Build", page_build())          # an open page compares it with its own
        for name, value in self.extra_headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def sse_start(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.close_connection = True
        self.sse_started = True

    def sse_event(self, name, data):
        self.wfile.write(f"event: {name}\ndata: {json.dumps(data)}\n\n".encode())

    def sse_ping(self):
        self.wfile.write(b": ping\n\n")

    # -- request helpers ----------------------------------------------------
    def arg(self, name, default=""):
        return self.query.get(name, default)

    def field(self, name, default=None):
        return self.body.get(name, default)

    def workspace(self):
        wid = self.query.get("ws") if self.command == "GET" else self.body.get("ws")
        ws = APP.get_workspace(wid, self.current_user)
        need = share_need(self.command, getattr(self, "route_path", ""))
        if need:
            check_right(ws, self.current_user, need)
        return ws

    def token(self):
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        try:
            jar = http.cookies.SimpleCookie(raw)
        except http.cookies.CookieError:
            return None
        morsel = jar.get(APP.cookie_name)
        return morsel.value if morsel else None

    def behind_proxy(self):
        """A reverse proxy on this machine (Caddy) connects over loopback and sets X-Forwarded-*."""
        return self.client_address[0] in ("127.0.0.1", "::1") and bool(self.headers.get("X-Forwarded-For"))

    def client_ip(self):
        if self.behind_proxy():
            return self.headers["X-Forwarded-For"].split(",")[-1].strip()
        return self.client_address[0]

    # -- dispatch -------------------------------------------------------------
    def do_GET(self):
        self.handle_method("GET")

    def do_POST(self):
        self.handle_method("POST")

    # Other methods only reach the live servers a folder runs.
    def do_PUT(self):
        self.handle_method("PUT")

    def do_PATCH(self):
        self.handle_method("PATCH")

    def do_DELETE(self):
        self.handle_method("DELETE")

    def do_HEAD(self):
        self.handle_method("HEAD")

    def do_OPTIONS(self):
        self.handle_method("OPTIONS")

    def handle_method(self, method):
        self.extra_headers = []
        self.sse_started = False
        try:
            self.dispatch(method)
        except ApiError as exc:
            if not self.sse_started:
                self.send_json(exc.status, {"error": exc.message, **exc.extra})
        except DISCONNECT_ERRORS:
            self.close_connection = True
        except Exception:
            traceback.print_exc()
            self.close_connection = True
            if not self.sse_started:
                try:
                    self.send_json(500, {"error": "internal server error"})
                except DISCONNECT_ERRORS:
                    pass

    def dispatch(self, method):
        parts = urllib.parse.urlsplit(self.path)
        path = parts.path
        self.route_path = path
        self.query = {k: v[0] for k, v in urllib.parse.parse_qs(parts.query, keep_blank_values=True).items()}
        self.body = {}
        if method == "GET" and path.startswith("/preview/"):
            return self.serve_preview(path)
        if path.startswith("/live/"):
            return self.serve_live(method, path)
        if method not in ("GET", "POST"):
            raise ApiError(405, "method not allowed")
        if method == "GET" and (path == "/" or path.startswith("/web/") or path.startswith("/assets/")
                                or (not path.startswith("/api/") and path.count("/") == 1)):
            return self.serve_static(path)
        entry = ROUTES.get((method, path))
        if method == "POST":
            if path not in RAW_BODY_ROUTES:      # these read their own body, as a stream
                self.read_body()
            else:
                self.close_connection = True     # a refusal leaves the body unread, so the connection cannot be reused
            self.check_csrf()
        if entry is None:
            raise ApiError(404, "not found")
        fn, public, admin = entry
        self.current_user = APP.auth.session_user(self.token())
        if not public and not self.current_user:
            raise ApiError(401, "login required")
        if admin and self.current_user["role"] != "admin":
            raise ApiError(403, "admins only")
        result = fn(self)
        if result is not None:
            self.send_json(200, result)
        if method == "POST" and self.current_user:
            self.announce_change(path)

    def announce_change(self, path):
        """Tell the user's other windows what this request changed."""
        kind = SYNC_ROUTES.get(path) or ("git" if path.startswith("/api/git/") else None)
        if kind:
            fields = {"ws": self.field("ws"), "path": self.field("path"), "id": self.field("id")}
            data = {k: v for k, v in fields.items() if isinstance(v, str)}
            sync_publish(self.current_user["username"], self.sync_origin(), kind, **data)
            ws = APP.workspaces.get(data.get("ws") or "")
            if ws and kind in ("files", "git", "terminals", "workspace"):      # others who share the folder see it change too
                for other in folder_people(ws) - {self.current_user["username"]}:
                    sync_publish(other, "", kind, **data)

    def sync_origin(self):
        return self.headers.get("X-Studio-Client") or ""

    def serve_preview(self, path):
        token, _, rel = path[len("/preview/"):].partition("/")
        full = preview_file(token, urllib.parse.unquote(rel))
        ext = os.path.splitext(full)[1].lower()
        ctype = CONTENT_TYPES.get(ext) or mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") and "charset" not in ctype:
            ctype += "; charset=utf-8"
        size = os.path.getsize(full)
        # Byte ranges, so a video can be seeked: browsers ask for the part they need.
        wanted = re.fullmatch(r"bytes=(\d*)-(\d*)", (self.headers.get("Range") or "").strip())
        first, last = 0, size - 1
        if wanted and (wanted[1] or wanted[2]):
            if wanted[1]:
                first, last = int(wanted[1]), min(int(wanted[2]) if wanted[2] else size - 1, size - 1)
            else:
                first = max(0, size - int(wanted[2]))
            if first > last or first >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                http.server.BaseHTTPRequestHandler.end_headers(self)
                return
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {first}-{last}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(last - first + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        # Browsers' PDF viewers cannot run in a sandbox, and a file served as a PDF runs no page scripts.
        self.send_header("Content-Security-Policy", "frame-ancestors 'self'" if ctype == "application/pdf" else PREVIEW_POLICY)
        # Not self.end_headers: that adds X-Frame-Options: DENY, and a preview has to show in Studio's frame.
        http.server.BaseHTTPRequestHandler.end_headers(self)
        with open(full, "rb") as fh:
            fh.seek(first)
            left = last - first + 1
            while left > 0:
                chunk = fh.read(min(1 << 20, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    def serve_live(self, method, path):
        """Relays a request to a server the folder runs, at /live/<token>/<its own path>."""
        token = path[len("/live/"):].partition("/")[0]
        # Live pages have an opaque ("null") origin.  Naming that origin, rather than using
        # '*', also lets cookie-authenticated apps use XHR/fetch with credentials enabled.
        self.extra_headers.append(("Access-Control-Allow-Origin", "null"))
        self.extra_headers.append(("Access-Control-Allow-Credentials", "true"))
        entry = live_entry(token)
        prefix = f"/live/{token}"
        if method == "OPTIONS":            # the page runs at an opaque origin, so its JSON requests are preflighted
            self.send_response(204)
            for name, value in (("Access-Control-Allow-Origin", "null"), ("Access-Control-Allow-Credentials", "true"),
                                ("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, HEAD"),
                                ("Access-Control-Allow-Headers", self.headers.get("Access-Control-Request-Headers") or "*"), ("Access-Control-Max-Age", "600")):
                self.send_header(name, value)
            http.server.BaseHTTPRequestHandler.end_headers(self)
            return
        length = to_int(self.headers.get("Content-Length"), 0, lo=0)
        if length > MAX_BODY_BYTES:
            raise ApiError(413, "request too large")
        body = self.rfile.read(length) if length else b""
        target = self.path[len(prefix):] or "/"
        if not target.startswith("/"):
            target = "/" + target
        try:
            secure = self.behind_proxy() and self.headers.get("X-Forwarded-Proto") == "https"
            app_cookie = live_cookie_header(entry, target, secure)
            resp, close = live_fetch(entry, method, target, list(self.headers.items()), body, app_cookie)
        except LiveError as exc:
            raise ApiError(502, str(exc))
        try:
            self.send_response(resp.status, resp.reason)
            ctype = resp.getheader("Content-Type") or ""
            encoded = (resp.getheader("Content-Encoding") or "identity").lower() != "identity"
            rewrite = not encoded and method != "HEAD" and ("text/html" in ctype or "text/css" in ctype or "javascript" in ctype)
            response_headers = resp.getheaders()
            live_store_cookies(entry, target, response_headers)
            for name, value in response_headers:
                low = name.lower()
                if low in LIVE_DROP_RESPONSE or (rewrite and low == "content-length"):
                    continue
                if low == "location":
                    value = live_location(value, prefix, entry["port"])
                self.send_header(name, value)
            self.send_header("Content-Security-Policy", LIVE_POLICY)
            self.send_header("Access-Control-Allow-Origin", "null")
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Referrer-Policy", "no-referrer")
            if rewrite:
                data = live_rewrite(resp.read(MAX_PREVIEW_BYTES), ctype, prefix)
                self.send_header("Content-Length", str(len(data)))
                http.server.BaseHTTPRequestHandler.end_headers(self)
                self.wfile.write(data)
                return
            if resp.getheader("Content-Length") is None and method != "HEAD":
                self.send_header("Connection", "close")    # the answer ends when the connection does, as event streams do
                self.close_connection = True
            # Not self.end_headers: that adds X-Frame-Options: DENY, and the page shows in Studio's frame.
            http.server.BaseHTTPRequestHandler.end_headers(self)
            if method == "HEAD":
                return
            while True:
                chunk = resp.read1(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()     # event streams arrive as they are sent
        finally:
            close()

    def read_body(self):
        length = to_int(self.headers.get("Content-Length"), 0, lo=0)
        if length > MAX_BODY_BYTES:
            self.close_connection = True
            raise ApiError(413, "request too large")
        raw = self.rfile.read(length) if length else b""
        if raw:
            try:
                body = json.loads(raw)
            except ValueError:
                raise ApiError(400, "invalid JSON")
            if not isinstance(body, dict):
                raise ApiError(400, "JSON object expected")
            self.body = body

    def check_csrf(self):
        if self.headers.get("X-Studio") != "1":
            raise ApiError(403, "missing X-Studio header")
        origin = self.headers.get("Origin")
        if origin and urllib.parse.urlsplit(origin).netloc != self.headers.get("Host", ""):
            raise ApiError(403, "cross-origin request rejected")

    def serve_static(self, path):
        if path == "/":
            rel = "index.html"
        elif path.startswith("/web/"):
            rel = urllib.parse.unquote(path[len("/web/"):]) or "index.html"
        else:
            rel = urllib.parse.unquote(path[1:])
        full = os.path.realpath(os.path.join(WEB_DIR, rel))
        if not within(full, WEB_DIR) or not os.path.isfile(full):
            raise ApiError(404, "not found")
        ext = os.path.splitext(full)[1].lower()
        ctype = CONTENT_TYPES.get(ext) or mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as fh:
            data = fh.read()
        if rel == "index.html":
            data = data.replace(b'<meta name="studio-build" content="">', f'<meta name="studio-build" content="{page_build()}">'.encode())
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)


# ---------------------------------------------------------------------------
# Keeping a user's windows in step
# ---------------------------------------------------------------------------

SYNC_ROUTES = {"/api/files/write": "files", "/api/files/create": "files", "/api/files/rename": "files", "/api/files/delete": "files", "/api/files/move": "files", "/api/agent/undo": "files",
               "/api/agent/sessions": "sessions", "/api/agent/session/delete": "sessions",
               "/api/timeskip/decide": "files", "/api/tasks/save": "tasks", "/api/tasks/action": "tasks", "/api/tasks/loop": "tasks",
               "/api/agent/session/rewind": "session", "/api/agent/session/settings": "session_list", "/api/agent/goal": "session", "/api/agent/session/branch": "session_list", "/api/workspace/sandbox": "workspace", "/api/skills/delete": "skills", "/api/skills/save": "skills",
               "/api/tools/save": "skills", "/api/tools/delete": "skills", "/api/audio/save": "files", "/api/audio/slices": "files"}
SYNC_LOG_SIZE = 500
PREF_RE = re.compile(r"theme|auto_apply|auto_commands|model|models_shown|pc_names|wrap|minimap|last_ws|(session|git_branch):[0-9a-f]{1,40}")
THEME_NAMES = ("dark", "light", "sepia")


def sync_publish(username, origin, kind, **data):
    """Queue a change for that user's other windows. It travels on the waiting room stream every window keeps open."""
    room = APP.room
    with room.cond:
        APP.sync_seq += 1
        APP.sync_log.append({"seq": APP.sync_seq, "user": username, "event": {"type": kind, "origin": origin, **data}})
        room.cond.notify_all()


def sync_events(username, after):
    """Events for this user newer than `after`, and the newest sequence number."""
    with APP.room.cond:
        found = [e for e in APP.sync_log if e["seq"] > after and e["user"] == username]
        return found, APP.sync_seq


def prefs_path(username):
    return os.path.join(APP.data_dir, "prefs", hashlib.sha1(username.encode()).hexdigest() + ".json")


def valid_pref(key, value):
    if key == "theme":
        return value in THEME_NAMES
    if key in ("auto_apply", "auto_commands", "wrap", "minimap") or key.startswith("git_branch:"):
        return isinstance(value, bool)
    if key == "models_shown":            # the models the picker shows; [] means all of them
        return isinstance(value, list) and len(value) <= 500 and all(isinstance(v, str) and 0 < len(v) <= 200 for v in value)
    if key == "last_ws":
        return isinstance(value, str) and len(value) <= 4096
    if key == "pc_names":                # the person's own names for their computers: {"DESKTOP-K6F5SDF": "DEV PC"}
        return isinstance(value, dict) and len(value) <= 200 and all(
            isinstance(k, str) and 0 < len(k) <= 100 and isinstance(v, str) and 0 < len(v.strip()) <= 40 and v.isprintable()
            for k, v in value.items())
    return isinstance(value, str) and len(value) <= 200      # model, session


@route("GET", "/api/prefs")
def prefs_get(req):
    with APP.prefs_lock:
        return {"prefs": read_json(prefs_path(req.current_user["username"]), {})}


@route("POST", "/api/prefs")
def prefs_set(req):
    key, value = req.field("key"), req.field("value")
    if not isinstance(key, str) or not PREF_RE.fullmatch(key) or not valid_pref(key, value):
        raise ApiError(400, "invalid setting")
    username = req.current_user["username"]
    with APP.prefs_lock:
        prefs = read_json(prefs_path(username), {})
        if len(prefs) >= 1000 and key not in prefs:
            raise ApiError(400, "too many settings")
        prefs[key] = value
        os.makedirs(os.path.dirname(prefs_path(username)), exist_ok=True)
        write_json_atomic(prefs_path(username), prefs)
    sync_publish(username, req.sync_origin(), "prefs", key=key, value=value)
    return {"ok": True}


@route("GET", "/api/skills")
def skills_get(req):
    return {"skills": skills_for(req.current_user["username"])}


@route("POST", "/api/skills/save")
def skills_save(req):
    """The user writes a new skill, or edits one; old_name names the skill being edited, which may be renamed."""
    fields = skill_fields(req.body)
    old = req.field("old_name")
    old_key = old.strip().casefold() if isinstance(old, str) and old.strip() else None

    def store(skills):
        key, now = fields["name"].casefold(), time.time()
        previous = skills.get(old_key) if old_key else None
        if old_key and previous is None:
            raise ApiError(404, "no such skill")
        if key in skills and key != old_key:
            raise ApiError(409, "a skill with this name already exists")
        if previous is None and len(skills) >= MAX_SKILLS:
            raise ApiError(400, f"there are already {MAX_SKILLS} skills; delete one first")
        if old_key:
            del skills[old_key]
        base = previous or {}
        skills[key] = {**fields, "created": base.get("created", now), "updated": now, "uses": base.get("uses", 0), "folder": base.get("folder", "")}
        return skills[key]
    return {"skill": change_skills(req.current_user["username"], store)}


@route("POST", "/api/skills/delete")
def skills_delete(req):
    name = req.field("name")
    if not isinstance(name, str):
        raise ApiError(400, "name required")

    def remove(skills):
        if skills.pop(name.casefold(), None) is None:
            raise ApiError(404, "no such skill")
    change_skills(req.current_user["username"], remove)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Previews of project files
# ---------------------------------------------------------------------------
# A project's HTML can run scripts, and it might have been written by a model. On Studio's own origin those scripts
# could act as the signed-in user. So previews are always served with a sandbox header, which gives them an origin of
# their own and no access to Studio's cookies or API, and are reached with a separate token instead of the session.

PREVIEW_TOKEN_S = 3600
MAX_PREVIEW_BYTES = 200 * 1024 * 1024
PREVIEW_POLICY = ("sandbox allow-scripts allow-forms allow-modals; default-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob:; "
                  "base-uri 'self'; form-action 'self'; frame-ancestors 'self'")


@route("POST", "/api/preview/token")
def preview_token(req):
    ws = req.workspace()
    token, now = secrets.token_urlsafe(24), time.time()
    with APP.lock:
        for old in [t for t, entry in APP.preview_tokens.items() if entry["expires"] < now]:
            del APP.preview_tokens[old]
        APP.preview_tokens[token] = {"ws": ws["id"], "username": req.current_user["username"], "expires": now + PREVIEW_TOKEN_S}
    return {"token": token, "expires_in": PREVIEW_TOKEN_S}


def preview_file(token, rel):
    """The file a preview may read, or an ApiError. It stays inside the folder, and hidden files are never served."""
    with APP.lock:
        entry = APP.preview_tokens.get(token)
    user = APP.auth.users.get(entry["username"]) if entry and entry["expires"] >= time.time() else None
    if not user or user.get("disabled"):
        raise ApiError(404, "not found")
    ws = APP.get_workspace(entry["ws"], {**user, "username": entry["username"]})
    if any(part.startswith(".") for part in rel.replace("\\", "/").split("/")):
        raise ApiError(404, "not found")
    path = resolve(ws, rel)
    if os.path.isdir(path):
        path = os.path.join(path, "index.html")
    if not within(path, ws["path"]) or not os.path.isfile(path):
        raise ApiError(404, "not found")
    if os.path.getsize(path) > MAX_PREVIEW_BYTES:
        raise ApiError(413, "this file is too large to preview")
    return path


# ---------------------------------------------------------------------------
# Live servers: pages of servers a folder runs, shown in Studio and to the agent
# ---------------------------------------------------------------------------

LIVE_TOKEN_S = 3600
LIVE_POLICY = ("sandbox allow-scripts allow-forms allow-modals allow-popups; default-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob:; "
               "base-uri 'self'; form-action 'self'; frame-ancestors 'self'")
# Studio's cookie and the browser's view of Studio never reach the app; compression is turned off so pages can be rewritten.
LIVE_DROP_REQUEST = {"connection", "keep-alive", "proxy-authorization", "te", "trailer", "transfer-encoding", "upgrade", "host",
                     "cookie", "referer", "origin", "content-length", "accept-encoding"}
LIVE_DROP_RESPONSE = {"connection", "keep-alive", "transfer-encoding", "set-cookie", "content-security-policy", "x-frame-options",
                      "access-control-allow-origin", "access-control-allow-credentials", "strict-transport-security"}
# Runs inside the folder's container: one request in on stdin, the server's answer out on stdout, as it arrives.
LIVE_RELAY = ("import socket,sys\n"
              "s=socket.create_connection(('127.0.0.1',int(sys.argv[1])),timeout=10)\n"
              "s.settimeout(None)\n"
              "s.sendall(sys.stdin.buffer.read())\n"
              "o=sys.stdout.buffer\n"
              "while True:\n"
              " d=s.recv(65536)\n"
              " if not d: break\n"
              " o.write(d); o.flush()\n")
LIVE_ATTR_RE = re.compile(rb"""(\s(?:href|src|action|formaction|poster)\s*=\s*["'])/(?![/])""", re.I)
LIVE_CSS_RE = re.compile(rb"""(url\(\s*["']?)/(?![/])""", re.I)
LIVE_HEAD_RE = re.compile(rb"<head\b[^>]*>", re.I)
LIVE_JS_ASSIGN_RE = re.compile(rb"(?<![\w$])(?:window\.)?location\.assign\s*\(")
LIVE_JS_REPLACE_RE = re.compile(rb"(?<![\w$])(?:window\.)?location\.replace\s*\(")
# Pages ask for /api/... and /static/... from the root; inside Studio that root is /live/<token>.
LIVE_SHIM = """<script>(()=>{const P=%s,O=location.origin;
// Opaque sandbox origins throw when frameworks inspect document.cookie before a mutating request
// (Angular's XSRF interceptor does this for POST/PUT/DELETE). App cookies live in Studio's isolated
// server-side jar, so the safe and accurate script-visible value here is always empty.
try{Object.defineProperty(document,"cookie",{configurable:false,get:()=>"",set:()=>{}});}catch{}
const fix=u=>{if(typeof u!=="string")return u;if(u.startsWith("/")&&!u.startsWith("//")&&!u.startsWith(P+"/"))return P+u;
if(u.startsWith(O+"/")&&!u.startsWith(O+P+"/"))return P+u.slice(O.length);return u;};
window.__studioAssign=u=>location.assign(fix(u instanceof URL?u.href:String(u)));
window.__studioReplace=u=>location.replace(fix(u instanceof URL?u.href:String(u)));
const F=window.fetch;window.fetch=(i,o)=>F(i instanceof Request?new Request(fix(i.url),i):fix(i instanceof URL?i.href:i),o);
const X=XMLHttpRequest.prototype.open;XMLHttpRequest.prototype.open=function(m,u,...r){return X.call(this,m,fix(u instanceof URL?u.href:u),...r);};
if(window.EventSource){const E=window.EventSource;const S=function(u,c){return new E(fix(u instanceof URL?u.href:u),c);};S.prototype=E.prototype;window.EventSource=S;}
for(const k of["pushState","replaceState"]){const h=history[k];history[k]=function(s,t,u){return h.call(this,s,t,u==null?u:fix(String(u)));};}
const attrs=["src","href","action"];new MutationObserver(l=>{for(const r of l)for(const n of r.addedNodes)if(n.nodeType===1)for(const a of attrs){const v=n.getAttribute&&n.getAttribute(a);if(v&&fix(v)!==v)n.setAttribute(a,fix(v));}}).observe(document.documentElement,{childList:true,subtree:true});
})();</script>"""


class LiveError(Exception):
    pass


def live_entry(token):
    """The folder, port and user behind a live token, or a 404."""
    with APP.lock:
        entry = APP.live_tokens.get(token)
    user = APP.auth.users.get(entry["username"]) if entry and entry["expires"] >= time.time() else None
    if not user or user.get("disabled"):
        raise ApiError(404, "not found")
    return {**entry, "ws_obj": APP.get_workspace(entry["ws"], {**user, "username": entry["username"]})}


def make_live_token(ws, username, port):
    token, now = secrets.token_urlsafe(24), time.time()
    with APP.lock:
        for old in [t for t, entry in APP.live_tokens.items() if entry["expires"] < now]:
            del APP.live_tokens[old]
        APP.live_tokens[token] = {"ws": ws["id"], "username": username, "port": port, "expires": now + LIVE_TOKEN_S, "cookies": {}}
    return token


def live_cookie_path(request_path):
    """RFC 6265's default path for a cookie which omitted Path."""
    path = urllib.parse.urlsplit(request_path).path
    if not path.startswith("/") or path.count("/") <= 1:
        return "/"
    return path.rsplit("/", 1)[0] or "/"


def live_cookie_matches(cookie_path, request_path):
    path = urllib.parse.urlsplit(request_path).path or "/"
    return path == cookie_path or (path.startswith(cookie_path) and (cookie_path.endswith("/") or path[len(cookie_path):].startswith("/")))


def live_store_cookies(entry, request_path, headers):
    """Keep an app's cookies behind its Live token, never on Studio's browser origin."""
    now = time.time()
    with APP.lock:
        jar = entry["cookies"]
        for name, raw in headers:
            if name.lower() != "set-cookie":
                continue
            parsed = http.cookies.SimpleCookie()
            try:
                parsed.load(raw)
            except http.cookies.CookieError:
                continue
            for cookie_name, morsel in parsed.items():
                path = morsel["path"] if morsel["path"].startswith("/") else live_cookie_path(request_path)
                key = (cookie_name, path)
                expires = None
                try:
                    if morsel["max-age"]:
                        age = int(morsel["max-age"])
                        expires = now + age
                        if age <= 0:
                            jar.pop(key, None)
                            continue
                    elif morsel["expires"]:
                        expires = email.utils.parsedate_to_datetime(morsel["expires"]).timestamp()
                        if expires <= now:
                            jar.pop(key, None)
                            continue
                except (TypeError, ValueError, OverflowError):
                    expires = None
                jar[key] = {"value": morsel.coded_value, "path": path, "secure": bool(morsel["secure"]), "expires": expires}


def live_cookie_header(entry, request_path, secure=False):
    """Return only this Live token's applicable app cookies for an upstream request."""
    now, values = time.time(), []
    with APP.lock:
        jar = entry["cookies"]
        for key, cookie in list(jar.items()):
            if cookie["expires"] is not None and cookie["expires"] <= now:
                del jar[key]
                continue
            if (not cookie["secure"] or secure) and live_cookie_matches(cookie["path"], request_path):
                values.append((len(cookie["path"]), key[0], cookie["value"]))
    values.sort(reverse=True)
    return "; ".join(f"{name}={value}" for _, name, value in values)


def parse_listening(text):
    """{socket inode: port} for the listening TCP sockets in /proc/net/tcp and tcp6 text."""
    found = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) > 9 and fields[3] == "0A" and ":" in fields[1]:
            try:
                found[fields[9]] = int(fields[1].rsplit(":", 1)[1], 16)
            except ValueError:
                continue
    return found


def live_ports(ws):
    if ws.get("helper"):
        try:
            return [int(p) for p in helper_ask(ws, {"type": "ports"}, 20).get("ports") or [] if isinstance(p, int)]
        except LiveError as exc:
            raise ApiError(502, str(exc)) from None
    return live_ports_here(ws)


HELPER_LIVE_VERSION = "0.06"       # the first helper that lists and relays the computer's web servers


def helper_ask(ws, job, wait):
    """A question for the helper of a folder on someone's computer (its servers, one web request), and its answer."""
    hid = ws["helper"]
    h = HELPERS.get(hid) or {}
    if helper_older(h.get("version"), HELPER_LIVE_VERSION):
        raise LiveError(f"the studio-helper on {h.get('machine', 'that computer')} is version {h.get('version') or 'older than 0.04'}; "
                        f"version {HELPER_LIVE_VERSION} or later shows its web servers in Studio. Download the new helper from Open folder → Connect a folder on my computer, and start it again.")
    if not HELPERS.online(hid):
        raise LiveError(f"{h.get('machine', 'that computer')} is not connected: start studio-helper there")
    job = dict(job, id=secrets.token_hex(8))
    HELPERS.send(hid, job)
    answer = HELPERS.wait_result(job["id"], time.time() + wait)
    if answer is None:
        raise LiveError(f"{h.get('machine', 'that computer')} did not answer in time")
    if answer.get("error"):
        raise LiveError(str(answer["error"])[:300])
    return answer


def live_ports_here(ws):
    """Ports the folder's servers listen on. In a sandbox that is everything in its container; on the host only
    sockets of programs started in this folder, so Studio, the Manager and other services stay out of reach."""
    if sandboxed(ws):
        result = subprocess.run(sandbox_exec(ws, ["cat", "/proc/net/tcp", "/proc/net/tcp6"]), capture_output=True, text=True, timeout=20)
        return sorted(set(parse_listening(result.stdout).values()))
    inodes = {}
    for name in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(name, encoding="ascii", errors="replace") as fh:
                inodes.update(parse_listening(fh.read()))
        except OSError:
            continue
    own = {APP.cfg.get("port"), urllib.parse.urlsplit(APP.cfg.get("gateway") or "").port}
    ports = set()
    for pid in os.listdir("/proc"):
        if not pid.isdigit() or int(pid) == os.getpid():
            continue
        try:
            if not within(os.readlink(f"/proc/{pid}/cwd"), ws["path"]):
                continue
            fds = os.listdir(f"/proc/{pid}/fd")
        except OSError:
            continue
        for fd in fds:
            try:
                link = os.readlink(f"/proc/{pid}/fd/{fd}")
            except OSError:
                continue
            if link.startswith("socket:[") and link[8:-1] in inodes:
                ports.add(inodes[link[8:-1]])
    return sorted(ports - own)


class _StreamSock:
    """Lets http.client read a response from any byte stream."""
    def __init__(self, stream):
        self.stream = stream

    def makefile(self, *args, **kwargs):
        return self.stream


def live_fetch(entry, method, target, headers, body, app_cookie=""):
    """Sends one request to the folder's server; returns (http.client response, close)."""
    port, ws = entry["port"], entry["ws_obj"]
    lines = [f"{method} {target} HTTP/1.1", f"Host: 127.0.0.1:{port}", "Connection: close"]
    for name, value in headers:
        low = name.lower()
        if low in LIVE_DROP_REQUEST or low.startswith(("x-studio", "x-forwarded", "sec-")):
            continue
        lines.append(f"{name}: {value}")
    if app_cookie:
        lines.append(f"Cookie: {app_cookie}")
    if body or method in ("POST", "PUT", "PATCH"):
        lines.append(f"Content-Length: {len(body)}")
    raw = ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1", "replace") + body
    if ws.get("helper"):
        # The helper makes the request on that computer and sends back the whole answer, which is read like any other.
        safe_headers = [[n, v] for n, v in headers if n.lower() not in LIVE_DROP_REQUEST and not n.lower().startswith(("x-studio", "x-forwarded", "sec-"))]
        if app_cookie:
            safe_headers.append(["Cookie", app_cookie])
        answer = helper_ask(ws, {"type": "fetch", "port": port, "method": method, "path": target, "b64": base64.b64encode(body).decode(),
                                 "headers": safe_headers}, 130)
        try:
            data = base64.b64decode(answer.get("b64") or "")
        except ValueError:
            raise LiveError("the helper's answer could not be read") from None
        head = [f"HTTP/1.1 {int(answer.get('status') or 502)} {answer.get('reason') or ''}"]
        head += [f"{n}: {v}" for n, v in answer.get("headers") or [] if isinstance(n, str) and isinstance(v, str) and "\n" not in n + v and "\r" not in n + v]
        head += [f"Content-Length: {len(data)}", "Connection: close"]
        stream = io.BytesIO(("\r\n".join(head) + "\r\n\r\n").encode("latin-1", "replace") + data)
        resp = http.client.HTTPResponse(_StreamSock(stream), method=method)
        resp.begin()
        return resp, stream.close
    if sandboxed(ws):
        proc = subprocess.Popen(["docker", "exec", "-i", ensure_sandbox(ws), "python3", "-c", LIVE_RELAY, str(port)],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            proc.stdin.write(raw)
            proc.stdin.close()
        except OSError:
            pass
        stream, close = proc.stdout, proc.kill
    else:
        try:
            sock = socket.create_connection(("127.0.0.1", port), timeout=10)
        except OSError:
            raise LiveError(f"nothing is listening on port {port}")
        sock.settimeout(300)
        sock.sendall(raw)
        stream, close = sock.makefile("rb"), sock.close
    resp = http.client.HTTPResponse(_StreamSock(stream), method=method)
    try:
        resp.begin()
    except (http.client.HTTPException, OSError):
        close()
        raise LiveError(f"nothing answered on port {port}")
    return resp, close


def live_location(value, prefix, port):
    """A redirect inside the app stays inside its live address."""
    for origin in (f"http://127.0.0.1:{port}", f"http://localhost:{port}", f"http://0.0.0.0:{port}"):
        if value.startswith(origin):
            value = value[len(origin):] or "/"
    return prefix + value if value.startswith("/") and not value.startswith("//") else value


def live_rewrite(data, ctype, prefix):
    """Root paths in a page or stylesheet point into its live address, and pages get the shim for paths built by scripts."""
    p = prefix.encode()
    if "text/css" in ctype:
        return LIVE_CSS_RE.sub(lambda m: m[1] + p + b"/", data)
    if "javascript" in ctype or "text/html" in ctype:
        # Full reloads such as location.assign('/') otherwise escape /live/<token> and try
        # to frame Studio itself.  The helper also handles computed destinations.
        data = LIVE_JS_ASSIGN_RE.sub(b"window.__studioAssign(", data)
        data = LIVE_JS_REPLACE_RE.sub(b"window.__studioReplace(", data)
    if "text/html" not in ctype:
        return data
    data = LIVE_ATTR_RE.sub(lambda m: m[1] + p + b"/", data)
    shim = (LIVE_SHIM % json.dumps(prefix)).encode()
    head = LIVE_HEAD_RE.search(data)
    return data[:head.end()] + shim + data[head.end():] if head else shim + data


@route("GET", "/api/live/ports")
def live_ports_route(req):
    ws = req.workspace()
    try:
        ports = live_ports(ws)
    except (OSError, subprocess.SubprocessError, ApiError) as exc:
        raise ApiError(502, f"could not list the servers: {exc}")
    return {"ports": ports, "sandboxed": sandboxed(ws)}


@route("POST", "/api/live/open")
def live_open_route(req):
    ws, port = req.workspace(), to_int(req.field("port"), 0, lo=0)
    if not 1 <= port <= 65535:
        raise ApiError(400, "port must be a number from 1 to 65535")
    if not sandboxed(ws) and port not in live_ports(ws):
        raise ApiError(403, f"no program started in this folder listens on port {port}")
    token = make_live_token(ws, req.current_user["username"], port)
    return {"url": f"/live/{token}/", "port": port, "expires_in": LIVE_TOKEN_S}


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 64

    def handle_error(self, request, client_address):
        """A browser that drops a spare connection (headless Chromium closing after view_page) is not an error worth a traceback."""
        if isinstance(sys.exc_info()[1], (ConnectionResetError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------

def set_session_cookie(req, token, max_age=None):
    """With no max_age the cookie lasts until the browser closes."""
    secure = "; Secure" if req.behind_proxy() and req.headers.get("X-Forwarded-Proto") == "https" else ""
    lifetime = f"; Max-Age={max_age}" if max_age is not None else ""
    req.extra_headers.append(("Set-Cookie", f"{APP.cookie_name}={token}; HttpOnly; SameSite=Strict; Path=/{lifetime}{secure}"))


def device_name(agent):
    """A short name for a browser, such as "Chrome on Windows", from its User-Agent."""
    agent = agent or ""
    browser = next((name for key, name in (("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"), ("Chrome/", "Chrome"), ("Safari/", "Safari")) if key in agent), "A browser")
    system = next((name for key, name in (("Android", "Android"), ("iPhone", "iPhone"), ("iPad", "iPad"), ("Windows", "Windows"), ("Mac OS X", "macOS"),
                                          ("CrOS", "ChromeOS"), ("Linux", "Linux")) if key in agent), "")
    return f"{browser} on {system}" if system else browser


def start_session(req, username, remember=False):
    set_session_cookie(req, APP.auth.new_session(username, remember, device_name(req.headers.get("User-Agent"))), REMEMBER_TTL if remember else None)


def clean_password(value):
    if not isinstance(value, str) or len(value) < 8:
        raise ApiError(400, "password must be at least 8 characters")
    if len(value) > 1024:
        raise ApiError(400, "password too long")
    return value


def clean_username(value):
    name = value.strip().lower() if isinstance(value, str) else ""
    if not USERNAME_RE.fullmatch(name):
        raise ApiError(400, "usernames are 2 to 32 characters: letters, numbers, - and _")
    return name


def clean_display_name(value):
    return value.strip()[:80] if isinstance(value, str) else ""


def check_rate_limit(req):
    if APP.auth.locked(req.client_ip()):
        raise ApiError(429, "too many failed attempts, try again in a minute")


def stop_user_work(username):
    """End a user's terminals and agent runs, after their account is disabled or deleted."""
    with APP.lock:
        terminals = [tid for tid, term in APP.terminals.items() if getattr(term, "owner", None) == username]
        closing = [APP.terminals.pop(tid) for tid in terminals]
        runs = [run for run in APP.active.values() if run.owner == username]
    for term in closing:
        term.kill()
    for run in runs:
        run.request_stop()
    APP.room.remove_user(username)


@route("GET", "/api/auth/state", public=True)
def auth_state(req):
    user = req.current_user
    return {"setup_needed": APP.auth.setup_needed(), "logged_in": bool(user),
            "user": APP.auth.public_user(user) if user else None}


@route("POST", "/api/auth/setup", public=True)
def auth_setup(req):
    password = clean_password(req.field("password"))
    username = clean_username(req.field("username") or "admin")
    APP.auth.set_password(password, username, clean_display_name(req.field("display_name")))
    start_session(req, username)
    return {"ok": True}


@route("POST", "/api/auth/login", public=True)
def auth_login(req):
    ip = req.client_ip()
    if APP.auth.locked(ip):
        raise ApiError(429, "too many failed logins, try again in a minute")
    password = req.field("password")
    username = req.field("username") or "admin"
    username = username.strip().lower() if isinstance(username, str) else ""
    user = APP.auth.verify(username, password) if isinstance(password, str) else None
    if not user:
        APP.auth.record_failure(ip)
        raise ApiError(401, "wrong username or password")
    if user.get("disabled"):
        raise ApiError(403, "this account is disabled, ask an admin")
    start_session(req, username, req.field("remember") is True)
    return {"ok": True}


@route("POST", "/api/auth/logout", public=True)
def auth_logout(req):
    APP.auth.drop(req.token())
    set_session_cookie(req, "", 0)
    return {"ok": True}


@route("GET", "/api/auth/invite", public=True)
def auth_invite_info(req):
    check_rate_limit(req)
    record = APP.auth.peek_token(req.arg("token"), "invite")
    if not record:
        APP.auth.record_failure(req.client_ip())
        raise ApiError(404, "this invite link is not valid or has expired")
    return {"role": record.get("role"), "expires": record["expires"]}


@route("POST", "/api/auth/signup", public=True)
def auth_signup(req):
    check_rate_limit(req)
    token = req.field("token")
    record = APP.auth.peek_token(token, "invite")
    if not record:
        APP.auth.record_failure(req.client_ip())
        raise ApiError(404, "this invite link is not valid or has expired")
    username = clean_username(req.field("username"))
    password = clean_password(req.field("password"))
    # A deleted account's folder is kept, so its name must not be handed to someone new.
    if username in APP.auth.users or os.path.exists(os.path.join(APP.user_base(), username)):
        raise ApiError(409, "that username is taken")
    if not APP.auth.take_token(token, "invite"):
        raise ApiError(404, "this invite link is not valid or has expired")
    APP.auth.create_user(username, password, record.get("role") or "user", clean_display_name(req.field("display_name")))
    start_session(req, username)
    return {"ok": True}


@route("GET", "/api/auth/reset", public=True)
def auth_reset_info(req):
    check_rate_limit(req)
    record = APP.auth.peek_token(req.arg("token"), "reset")
    if not record or record.get("username") not in APP.auth.users:
        APP.auth.record_failure(req.client_ip())
        raise ApiError(404, "this reset link is not valid or has expired")
    return {"username": record["username"]}


@route("POST", "/api/auth/reset", public=True)
def auth_reset(req):
    check_rate_limit(req)
    password = clean_password(req.field("password"))
    record = APP.auth.take_token(req.field("token"), "reset")
    if not record or record.get("username") not in APP.auth.users:
        APP.auth.record_failure(req.client_ip())
        raise ApiError(404, "this reset link is not valid or has expired")
    APP.auth.change_password(record["username"], password)
    APP.auth.revoke_sessions(record["username"])
    return {"ok": True}


@route("POST", "/api/account/password")
def account_password(req):
    check_rate_limit(req)
    user = req.current_user
    current = req.field("current")
    if not isinstance(current, str) or not APP.auth.verify(user["username"], current):
        APP.auth.record_failure(req.client_ip())
        raise ApiError(403, "your current password is wrong")
    APP.auth.change_password(user["username"], clean_password(req.field("password")))
    APP.auth.revoke_sessions(user["username"], keep_token=req.token())
    return {"ok": True}


@route("GET", "/api/account")
def account_info(req):
    me = req.current_user
    return {"user": APP.auth.public_user(me), "sessions": APP.auth.sessions_of(me["username"], req.token()),
            "pc_allowed": may_pair(me), "card_allowed": may_card(me)}


@route("POST", "/api/account/profile")
def account_profile(req):
    name = req.field("display_name")
    if not isinstance(name, str) or len(name.strip()) > 60 or any(ord(c) < 32 for c in name):
        raise ApiError(400, "a display name can have up to 60 characters")
    APP.auth.set_display_name(req.current_user["username"], name.strip())
    return {"user": APP.auth.public_user(APP.auth.users[req.current_user["username"]])}


@route("POST", "/api/account/sessions/revoke")
def account_session_revoke(req):
    sid = req.field("id")
    if not isinstance(sid, str) or not re.fullmatch(r"[0-9a-f]{16}", sid):
        raise ApiError(400, "unknown session")
    current = APP.auth._key(req.token())
    if current.startswith(sid):
        raise ApiError(400, "to end this browser's session, sign out")
    if not APP.auth.revoke_session(req.current_user["username"], sid):
        raise ApiError(404, "that session has already ended")
    return {"ok": True}


@route("POST", "/api/account/logout-others")
def account_logout_others(req):
    APP.auth.revoke_sessions(req.current_user["username"], keep_token=req.token())
    return {"ok": True}


@route("GET", "/api/admin/users", admin=True)
def admin_users(req):
    users = sorted(APP.auth.users.values(), key=lambda u: u["username"])
    return {"users": [APP.auth.public_user(u) for u in users], "invites": APP.auth.list_invites(),
            "user_root": APP.user_base()}


@route("POST", "/api/admin/invites", admin=True)
def admin_invite(req):
    role = req.field("role") or "user"
    if role not in ("user", "admin"):
        raise ApiError(400, "role must be user or admin")
    return {"token": APP.auth.issue_token("invite", INVITE_TTL, role=role, by=req.current_user["username"]), "expires_in": INVITE_TTL}


@route("POST", "/api/admin/invites/revoke", admin=True)
def admin_invite_revoke(req):
    if not APP.auth.revoke_invite(req.field("id")):
        raise ApiError(404, "unknown invite")
    return {"ok": True}


@route("POST", "/api/admin/users/update", admin=True)
def admin_user_update(req):
    username, role, disabled, pc_folders = req.field("username"), req.field("role"), req.field("disabled"), req.field("pc_folders")
    card = req.field("card")
    if pc_folders is not None and not isinstance(pc_folders, bool):
        raise ApiError(400, "pc_folders must be true or false")
    if card is not None and not isinstance(card, bool):
        raise ApiError(400, "card must be true or false")
    if role is not None and role not in ("user", "admin"):
        raise ApiError(400, "role must be user or admin")
    if disabled is not None and not isinstance(disabled, bool):
        raise ApiError(400, "disabled must be true or false")
    if username == req.current_user["username"] and disabled is True:
        raise ApiError(409, "you cannot disable your own account")
    user = APP.auth.update_user(username, role, disabled, pc_folders, card)
    if pc_folders is False:
        for hid in [h["id"] for h in HELPERS.all() if h["owner"] == username]:
            HELPERS.remove(hid)
    if disabled is True:
        APP.auth.revoke_sessions(username)
        stop_user_work(username)
    return {"user": APP.auth.public_user(user)}


@route("POST", "/api/admin/users/reset-link", admin=True)
def admin_reset_link(req):
    username = req.field("username")
    if username not in APP.auth.users:
        raise ApiError(404, "unknown user")
    return {"token": APP.auth.issue_token("reset", RESET_TTL, username=username, by=req.current_user["username"]), "expires_in": RESET_TTL}


@route("POST", "/api/admin/users/delete", admin=True)
def admin_user_delete(req):
    username = req.field("username")
    if username == req.current_user["username"]:
        raise ApiError(409, "you cannot delete your own account")
    APP.auth.delete_user(username)
    stop_user_work(username)
    if isinstance(username, str) and os.path.exists(prefs_path(username)):
        os.remove(prefs_path(username))
    return {"ok": True}


# ---------------------------------------------------------------------------
# Config, models, workspaces
# ---------------------------------------------------------------------------

@route("GET", "/api/config")
def api_config(req):
    cfg, user = APP.cfg, req.current_user
    out = {"workspace_roots": APP.roots_for(user), "default_model": cfg["default_model"],
           "max_turns": cfg["max_turns"], "command_timeout_s": cfg["command_timeout_s"],
           "sandbox_required": bool(cfg.get("sandbox_required")), "gateway_ok": get_models()[1],
           "public_url": cfg.get("public_url") or "", "user": APP.auth.public_user(user)}
    if user["role"] == "admin":
        out.update(host=cfg["host"], port=cfg["port"], gateway=cfg["gateway"], codex=bool(cfg.get("codex")), updates=bool(cfg.get("updates")))
    return out


@route("GET", "/api/models")
def api_models(req):
    models, ok = get_models()
    if APP.cfg.get("codex") and req.current_user["role"] == "admin":
        models = models + codex_models()
    return {"models": models, "gateway_ok": ok}


def folder_place(ws):
    """Where a folder lives, for its icon: on a computer through its helper, a copy kept from a disconnected computer, a
    copy passed by someone, or Studio's own server."""
    name = os.path.basename(ws.get("path") or "")
    if ws.get("helper"):
        h = HELPERS.public(ws["helper"])
        return {"kind": "computer", "machine": h.get("machine"), "online": h.get("online")}
    if ws.get("passed_from") or re.search(r"-from-[\w.-]+$", name):
        return {"kind": "passed", "from": ws.get("passed_from") or name.rsplit("-from-", 1)[1]}
    if ws.get("kept_from") is not None or re.search(r"@[A-Za-z0-9-]{2,}$", name):
        return {"kind": "kept", "machine": ws.get("kept_from") or name.rsplit("@", 1)[1]}
    return {"kind": "server"}


def folder_kinds(user):
    """What the person's folders are, by path: on a connected computer, a copy kept from a disconnected one, or passed to
    them by someone. Folders from before these were recorded are recognised by their names (<folder>@<machine>,
    <folder>-from-<person>)."""
    out = {}
    for ws in list(APP.workspaces.values()):
        if ws.get("owner", user["username"]) != user["username"] and user["role"] != "admin":
            continue
        info = folder_place(ws)
        if info["kind"] != "server":
            out[os.path.realpath(ws["path"])] = info
    return out


def root_label(user, root):
    if user["role"] != "admin" and os.path.realpath(root) == os.path.realpath(os.path.join(APP.user_base(), user["username"])):
        return "Your space"
    return os.path.basename(root.rstrip("/")) or root


@route("GET", "/api/workspaces/browse")
def api_browse(req):
    user = req.current_user
    roots = APP.roots_for(user)
    path = req.arg("path")
    labels = [{"path": r, "label": root_label(user, r)} for r in roots]
    if not path:
        return {"path": "", "parent": None, "roots": roots, "root_labels": labels,
                "dirs": [{"name": root_label(user, r), "path": r} for r in roots]}
    real = os.path.realpath(os.path.expanduser(path))
    if not APP.in_roots_for(real, user):
        raise ApiError(403, "folder is outside the allowed workspace roots")
    if not os.path.isdir(real):
        raise ApiError(400, "not a directory")
    dirs, kinds = [], folder_kinds(user)
    try:
        for e in sorted(os.scandir(real), key=lambda e: e.name.lower()):
            if e.name.startswith(".") or not e.is_dir():
                continue
            target = os.path.realpath(e.path)
            if APP.in_roots_for(target, user):
                dirs.append({"name": e.name, "path": target, **kinds.get(target, {})})
    except OSError as exc:
        raise ApiError(403, f"cannot list folder: {exc.strerror}")
    return {"path": real, "parent": "" if real in roots else os.path.dirname(real),
            "roots": roots, "root_labels": labels, "dirs": dirs}


@route("POST", "/api/workspaces/mkdir")
def api_mkdir(req):
    parent, name = req.field("parent"), req.field("name")
    if not isinstance(parent, str) or not parent:
        raise ApiError(400, "parent required")
    name = name.strip() if isinstance(name, str) else ""
    if not name or name.startswith(".") or "/" in name or "\\" in name or "\0" in name or len(name.encode()) > 255:
        raise ApiError(400, "invalid folder name")
    real = os.path.realpath(os.path.expanduser(parent))
    if not APP.in_roots_for(real, req.current_user):
        raise ApiError(403, "folder is outside the allowed workspace roots")
    if not os.path.isdir(real):
        raise ApiError(400, "not a directory")
    path = os.path.join(real, name)
    try:
        os.mkdir(path)
    except FileExistsError:
        raise ApiError(409, "a file or folder with that name already exists")
    except OSError as exc:
        raise ApiError(403, f"cannot create folder: {exc.strerror}")
    return {"name": name, "path": path}


@route("POST", "/api/workspace/open")
def api_workspace_open(req):
    path = req.field("path")
    if not isinstance(path, str) or not path:
        raise ApiError(400, "path required")
    return {"workspace": APP.open_workspace(path, req.current_user)}


@route("POST", "/api/workspace/sandbox")
def api_workspace_sandbox(req):
    ws = req.workspace()
    enabled, image, network, ports = (req.field(key) for key in ("enabled", "image", "network", "ports"))
    if enabled is not None and not isinstance(enabled, bool):
        raise ApiError(400, "enabled must be true or false")
    if image is not None and image != "" and not (isinstance(image, str) and SANDBOX_IMAGE_RE.fullmatch(image)):
        raise ApiError(400, "invalid image name")
    if network is not None and network not in SANDBOX_NETWORKS:
        raise ApiError(400, "network must be off, install or on")
    if ports is not None and not (isinstance(ports, list) and len(ports) <= 20 and all(
            isinstance(p, int) and not isinstance(p, bool) and 1 <= p <= 65535 for p in ports)):
        raise ApiError(400, "ports must be a list of port numbers")
    if APP.cfg.get("sandbox_required"):
        raise ApiError(409, "this server always uses the sandbox with fixed settings")
    if ws.get("owner") and enabled is False:
        raise ApiError(403, "user folders always use the sandbox")
    if req.current_user["role"] != "admin" and ports:
        raise ApiError(403, "only admins can publish ports")
    with APP.lock:
        if ws["id"] in APP.active:
            raise ApiError(409, "stop the agent before changing the sandbox setting")
    previous = {key: ws.get(key) for key in ("sandbox", "sandbox_image", "sandbox_network", "sandbox_ports")}
    with APP.lock:
        if enabled is not None:
            ws["sandbox"] = enabled
        if image is not None:
            ws["sandbox_image"] = image
        if network is not None:
            ws["sandbox_network"] = network
        if ports is not None:
            ws["sandbox_ports"] = sorted(set(ports))
    try:
        # The image, network and ports are fixed when a container is created, so always start fresh.
        remove_sandbox(ws)
        if sandboxed(ws):
            ensure_sandbox(ws)
    except ApiError:
        with APP.lock:
            ws.update(previous)
        raise
    with APP.lock:
        write_json_atomic(APP.workspaces_path, APP.workspaces)
    return {"workspace": APP.public_ws(ws)}


# ---------------------------------------------------------------------------
# Model state: the loading bar, Strata's own server (strata_dir), the dashboard's figures
# ---------------------------------------------------------------------------

# Lines the Strata engine writes while it loads, in order, with how far along each one is.
LOAD_STEPS = [("memory guard", 3, "Checking memory"), ("of weights loaded", 8, "Reading weights"), ("native projection matrices", 11, "Reading weights"),
              ("PLE on", 14, "Opening the lookup table"), ("draft layer loaded", 17, "Loading the draft head"), ("hipBLASLt tuning", 19, "Warming up the GPU"),
              ("warmed the BF16", 45, "Loading the expert arena"), ("expert arena read", 47, "Loading the expert arena"),
              ("GiB at", 78, "Starting the CPU workers"), ("expert cache auto", 82, "Filling the expert cache"), ("pre-filled", 90, "Starting the session"),
              ("session is up", 94, "Preparing the first reply"), ("with everything loaded", 97, "Almost ready"),
              # Strata 0.2.x (engine 0.1.61 on) prints plainer lines through its launcher
              ("loading the vision encoder", 2, "Starting"), ("reading the model's weights", 8, "Reading weights"),
              ("loading the experts into RAM", 20, "Loading the expert arena"), ("experts loaded:", 78, "Starting the CPU workers"),
              ("filling the GPU's expert cache", 82, "Filling the expert cache"), ("almost ready ...", 94, "Almost ready")]
DEFAULT_LOAD_S = 75


def load_progress(model, strata, now=None):
    """{percent, step, elapsed} while a Strata model loads, else None. Elapsed time against the model's last load moves
    the bar; engine milestones push it ahead when the engine is faster. It stays short of 100 until the engine is ready."""
    now = now or time.time()
    job = strata.get("job") or {}
    if job.get("kind") != "start" or job.get("state") not in ("starting", "running") or (strata.get("running") and (strata.get("health") or {}).get("loaded")):
        return None
    output, started = str(job.get("output") or ""), job.get("started_at")
    reached = [(percent, step) for key, percent, step in LOAD_STEPS if key in output]
    floor, step = max(reached, default=(1, "Starting the engine"))
    elapsed = max(0.0, now - started) if isinstance(started, (int, float)) else 0.0
    expected = APP.load_times.get(model) or DEFAULT_LOAD_S
    # Time leads: the engine can be quiet for many seconds before its first milestone line, and a bar held there looks stuck.
    percent = max(floor, min(elapsed / expected * 100, 98))
    if isinstance(started, (int, float)):
        APP.load_seen[model] = started
    return {"percent": round(percent), "step": step, "elapsed": round(elapsed)}


def note_load_finished(model, strata):
    """Once a watched load is over, remember how long it took, so the next bar is paced to it."""
    started = APP.load_seen.pop(model, None)
    if started is None or not (strata.get("running") and (strata.get("health") or {}).get("loaded")):
        return
    took = time.time() - started
    if 5 <= took <= 1800:
        APP.load_times[model] = round(took)
        write_json_atomic(os.path.join(APP.data_dir, "load_times.json"), APP.load_times)


def watch_model_load(run, done):
    """While a run waits for a Strata model's first reply, shows its loading progress in the chat."""
    if not is_strata_model(run.model):
        return
    strata_loading(run.model)
    shown, failures = False, 0
    while not done.wait(0.3 if not shown and failures == 0 else 1.5):
        try:
            strata = strata_status(timeout=5)
        except (OSError, ValueError, http.client.HTTPException):
            failures += 1
            if failures >= 3:         # no Manager status to follow
                return
            continue
        progress = load_progress(run.model, strata)
        if progress is None:
            if not (strata.get("running") and (strata.get("health") or {}).get("loaded")):
                continue              # the request may not have started the load yet
            note_load_finished(run.model, strata)
            if shown and not done.is_set() and time.time() - APP.stream_at > 2:
                run.emit("status", text="Model loaded. Reading the prompt…")
            return
        shown = True
        run.emit("status", text="Loading model…", load=progress)
    if shown:                         # the reply came before the loop saw the load end
        try:
            note_load_finished(run.model, strata_status(timeout=5))
        except (OSError, ValueError, http.client.HTTPException):
            pass


def gateway_state():
    """(the Manager's Strata status or {}, the loaded models from /api/ps, or None for a server with only the OpenAI
    API). Raises when the gateway does not answer at all."""
    try:
        strata = strata_status(timeout=5)
    except (GatewayStatus, ValueError):
        strata = {}
    if strata.get("direct"):
        return strata, []
    try:
        return strata, gateway_json("GET", "/api/ps", timeout=5).get("models", [])
    except (GatewayStatus, ValueError):
        openai_models()
        return strata, None


# ---- Strata without the Manager (2026-11-24): when the gateway is Strata's own server (serve/server.py, found by its
# /v1/status answering "service": "strata"), Studio reads that status and shapes it like the Manager's
# /api/strata/status, so the dashboard, the loading bar, the session cache and the waiting room work the same. Strata
# loads the model by itself on a request (auto_load); Studio unloads it after strata_idle_minutes without requests
# (/v1/unload, strata_idle_loop). One server serves one profile; starting servers and switching profiles is the
# Manager's job, or the person's.
STRATA_DIRECT = {"loaded": None, "epoch": None, "loading_since": None, "cpu": None, "log_offset": None}


def strata_direct_status(timeout=5):
    """Strata's own /v1/status, or None when the gateway is not Strata's server."""
    try:
        st = gateway_json("GET", "/v1/status", timeout=timeout)
    except (OSError, ValueError, http.client.HTTPException):
        return None
    return st if isinstance(st, dict) and st.get("service") == "strata" else None


def is_strata_model(model):
    """A Strata model, named by the Manager (…:strata) or served by Strata's own server."""
    return "strata" in (model or "") or (APP is not None and APP.gateway_kind == "strata" and model in APP.strata_models)


def gateway_base():
    parts = urllib.parse.urlsplit(APP.cfg["gateway"])
    path = parts.path.rstrip("/")
    return f"{parts.scheme}://{parts.netloc}{path[:-3] if path.endswith('/v1') else path}"


def strata_manager_shape(st):
    """Strata's own status in the shape of the Manager's /api/strata/status, plus the original under "direct"."""
    loaded, now = bool(st.get("loaded")), time.time()
    with APP.lock:
        d = STRATA_DIRECT
        if loaded and not d["loaded"]:
            d["epoch"] = now                        # a new engine instance: what it held before is gone
        d["loaded"] = loaded
        if loaded:
            d["loading_since"] = None
        loading = d["loading_since"]
    act, t = st.get("activity") or {}, st.get("last_timings") or {}
    minutes = float(APP.cfg.get("strata_idle_minutes") or 0)
    last = act.get("last_request_at")
    ctx = (st.get("context") or {}).get("native") or st.get("cache_max_tokens")
    used = sum(int(t.get(k) or 0) for k in ("cache_n", "prompt_n", "predicted_n"))
    return {"running": loaded, "health": {"loaded": loaded}, "base_url": gateway_base(),
            "server_processes": [{"pid": f"strata-{st.get('started')}-{d['epoch']}"}] if loaded else [],
            "job": {"kind": "start", "state": "running", "started_at": loading, "output": strata_log_tail()} if loading and not loaded else {},
            "lifecycle": {"active_requests": act.get("in_flight") or 0, "idle_timeout_minutes": minutes or None, "auto_start": True,
                          "next_unload_at": last + minutes * 60 if loaded and last and minutes and not act.get("in_flight") else None},
            "last_generation": {"context_tokens": ctx, "context_percent": round(used / ctx * 100, 1) if used and ctx else None,
                                "tokens_per_second": t.get("predicted_per_second")},
            "direct": st}


def strata_status(timeout=5):
    """The Strata engine's state as the Manager reports it, from the Manager or from Strata's own server. Raises like
    gateway_json; GatewayStatus(404) when the gateway is neither."""
    if APP.gateway_kind != "strata":
        try:
            return gateway_json("GET", "/api/strata/status", timeout=timeout)
        except GatewayStatus as exc:
            if exc.status != 404:
                raise
    st = strata_direct_status(timeout)
    if st is None and strata_dir():        # Studio starts Strata: a server that is down is a model that is not loaded
        st = {"service": "strata", "model": STRATA_PROC.get("model"), "loaded": False, "activity": {}}
    if st is None:
        if APP.gateway_kind == "strata":
            gateway_json("GET", "/v1/models", timeout=timeout)     # raises the real reason (nothing answers, or refused)
        raise GatewayStatus(404)
    return strata_manager_shape(st)


def strata_loading(model):
    """A run is about to wait for Strata's own server, which loads the model by itself: from now on that is a load."""
    if APP.gateway_kind == "strata" and model in APP.strata_models:
        with APP.lock:
            if not STRATA_DIRECT["loaded"] and not STRATA_DIRECT["loading_since"]:
                STRATA_DIRECT["loading_since"] = time.time()
                profile = strata_profiles().get(model) if strata_dir() else None
                try:
                    STRATA_DIRECT["log_offset"] = os.path.getsize(profile["config"]["log"]) if profile and profile["config"].get("log") else None
                except OSError:
                    STRATA_DIRECT["log_offset"] = 0


def cpu_percent():
    """The machine's CPU use since the last call, from /proc/stat (Strata's own status does not give it)."""
    try:
        with open("/proc/stat") as fh:
            parts = [int(x) for x in fh.readline().split()[1:]]
    except (OSError, ValueError):
        return None
    idle, total = parts[3] + (parts[4] if len(parts) > 4 else 0), sum(parts)
    prev, STRATA_DIRECT["cpu"] = STRATA_DIRECT["cpu"], (idle, total)
    if not prev or total <= prev[1]:
        return None
    return round(100 * (1 - (idle - prev[0]) / (total - prev[1])), 1)


def strata_direct_metrics(st):
    """The dashboard's metrics from Strata's own status: the card and RAM, in the Manager's field names."""
    machine = st.get("machine") or {}
    gpu, ram = machine.get("gpu") or {}, machine.get("ram") or {}
    total, used = (ram.get("total_gib") or 0) * 2 ** 30, (ram.get("used_gib") or 0) * 2 ** 30
    return {"system": {"used": used, "total": total, "available": max(0, total - used), "cpu_percent": cpu_percent()} if total else {},
            "gpu": {"gpu_busy": gpu.get("util_pct"), "vram_used": (gpu.get("used_mib") or 0) * MIB, "vram_total": (gpu.get("total_mib") or 0) * MIB,
                    "gpu_temps": {"junction": gpu.get("temp_c")} if gpu.get("temp_c") is not None else {}, "gpu_power_w": gpu.get("power_w"),
                    "name": gpu.get("name")} if gpu else {},
            "process": {}, "placement": {}, "strain": None, "runtime": {"engine": st.get("engine"), "direct": True}}


# ---- Strata started by Studio (2026-11-24, part 2): with config strata_dir, Studio runs Strata's own server itself
# (serve/server.py --lazy, so it binds at once and loads the model on the first request) on the gateway's port, one
# profile (strata-<name>.json) at a time. Every profile is a model in the picker; a chat that picks another profile
# stops the server and starts it with that one, once no other run uses Strata. Studio never edits the profiles: it
# starts from its own copy in data/strata/ (the Graphics card choice changes --vram-reserve-mib there). The engine's
# log (the profile's "log") gives the loading bar its steps. Without strata_dir all of this is off.
STRATA_PROC = {"proc": None, "model": None, "lock": threading.RLock(), "profiles": None, "seen": None}


def strata_dir():
    path = os.path.expanduser(APP.cfg.get("strata_dir") or "") if APP is not None else ""
    return path if path and os.path.isfile(os.path.join(path, "serve", "server.py")) else ""


def strata_profiles():
    """{model_name: {"path", "stem", "config"}} of the Strata checkout's profiles, read again when they change."""
    folder = strata_dir()
    if not folder:
        return {}
    files = sorted(glob.glob(os.path.join(folder, "strata-*.json")))
    seen = [(f, os.path.getmtime(f)) for f in files]
    if STRATA_PROC["seen"] == seen and STRATA_PROC["profiles"] is not None:
        return STRATA_PROC["profiles"]
    out = {}
    for f in files:
        config = read_json(f, None)
        if isinstance(config, dict) and isinstance(config.get("model_name"), str) and isinstance(config.get("args"), list):
            out.setdefault(config["model_name"], {"path": f, "stem": os.path.basename(f)[len("strata-"):-len(".json")], "config": config})
    STRATA_PROC.update(profiles=out, seen=seen)
    return out


def strata_port():
    return urllib.parse.urlsplit(APP.cfg["gateway"]).port or 80


def strata_reserve_args(args, reserve):
    args = [str(a) for a in args]
    if reserve is None:
        return args
    if "--vram-reserve-mib" in args:
        i = args.index("--vram-reserve-mib")
        return args[:i + 1] + [str(int(reserve))] + args[i + 2:]
    return args + ["--vram-reserve-mib", str(int(reserve))]


def strata_card_reserve():
    """The VRAM reserve for the Graphics card choice: Tools ready leaves room for the tools; Strata first, the profile's own."""
    return int(APP.cfg.get("strata_tools_reserve_mib") or 9216) if read_json(card_path(), {}).get("mode") == "tools" else None


def strata_start(model, run=None):
    """Starts Strata's server with the model's profile (the caller holds STRATA_PROC["lock"]). Raises ModelError."""
    profile = strata_profiles().get(model)
    if not profile:
        raise ModelError(f"no Strata profile serves {model}")
    folder = strata_dir()
    python = os.path.join(folder, ".venv", "bin", "python")
    if not os.path.isfile(python):
        raise ModelError(f"Strata's Python is missing ({python}): run Strata's setup first")
    config = dict(profile["config"])
    reserve = strata_card_reserve()
    config["args"] = strata_reserve_args(config["args"], reserve)
    if isinstance(config.get("user_args"), list):
        config["user_args"] = strata_reserve_args(config["user_args"], reserve)
    own = os.path.join(APP.data_dir, "strata", f"strata-{profile['stem']}.json")
    write_json_atomic(own, config, private=True)
    log = open(os.path.join(APP.data_dir, "strata", "server.log"), "ab")
    if run:
        run.emit("status", text=f"Starting Strata with {model_label(model, strata_direct_label(model))}…")
    proc = subprocess.Popen([python, os.path.join(folder, "serve", "server.py"), "--engine", "strata", "--config", own,
                             "--host", "127.0.0.1", "--port", str(strata_port()), "--lazy"],
                            cwd=folder, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    log.close()
    STRATA_PROC.update(proc=proc, pid=proc.pid, model=model, reserve_started=reserve)
    deadline = time.time() + 120
    while time.time() < deadline:
        if proc.poll() is not None:
            raise ModelError(f"Strata's server stopped as it started (exit {proc.returncode}); see data/strata/server.log")
        if strata_direct_status(timeout=2):
            print(f"Strata started with {model} on port {strata_port()}", flush=True)
            return
        time.sleep(0.5)
    raise ModelError("Strata's server did not answer within 2 minutes; see data/strata/server.log")


def strata_adopt():
    """The pid of a Strata server started from Studio's own copy of a profile (data/strata/), say by a Studio that was
    stopped without systemd ending its processes; None when the server on the port is someone else's."""
    mine = os.path.join(APP.data_dir, "strata") + os.sep
    for pid in os.listdir("/proc"):
        if pid.isdigit():
            cmd = process_command(pid)
            if "serve/server.py" in cmd and mine in cmd and f"--port {strata_port()}" in cmd:
                return int(pid)
    return None


def pid_alive(pid):
    """A process that still runs: one that has ended but was not reaped (a zombie, say in a container with no init) does not."""
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return False


def strata_running_pid():
    proc = STRATA_PROC.get("proc")
    if proc is not None:
        return proc.pid if proc.poll() is None else None
    pid = STRATA_PROC.get("pid")
    return pid if pid and pid_alive(pid) else None


def strata_stop():
    """Stops the Strata server Studio started (the caller holds the lock). A server someone else started is left alone."""
    pid = strata_running_pid()
    if pid:
        for sig, wait in ((signal.SIGTERM, 60), (signal.SIGKILL, 10)):
            try:
                os.killpg(pid, sig)
            except OSError:
                try:
                    os.kill(pid, sig)
                except OSError:
                    break
            deadline = time.time() + wait
            while time.time() < deadline and (pid_alive(pid) and (STRATA_PROC.get("proc") is None or STRATA_PROC["proc"].poll() is None)):
                time.sleep(0.2)
            if not pid_alive(pid) or (STRATA_PROC.get("proc") is not None and STRATA_PROC["proc"].poll() is not None):
                break
        print(f"Strata stopped ({STRATA_PROC.get('model')})", flush=True)
    STRATA_PROC.update(proc=None, pid=None, model=None)
    with APP.lock:
        STRATA_DIRECT.update(loaded=None, loading_since=None)


def strata_ensure(run):
    """Before a request to a Strata model when Studio starts Strata: the server runs with that model's profile."""
    if not strata_dir() or run.model not in strata_profiles():
        return
    with STRATA_PROC["lock"]:
        st = strata_direct_status(timeout=3)
        if st and st.get("model") == run.model:
            return
        mine = bool(strata_running_pid())
        if st and not mine:
            pid = strata_adopt()
            if pid:                        # started by an earlier Studio from its own copy: Studio's again
                STRATA_PROC.update(proc=None, pid=pid, model=st.get("model"))
                mine = True
        if st and not mine:
            raise ModelError(f"another program serves Strata ({st.get('model')}) on port {strata_port()}; stop it, or use another port")
        if st:
            others = [r for r in APP.active.values() if r is not run and is_strata_model(r.model) and r.model != run.model]
            if others or (st.get("activity") or {}).get("in_flight"):
                raise ModelError(f"Strata is working with {st.get('model')} for another run; try again when it finishes")
            if run:
                run.emit("status", text=f"Switching Strata from {st.get('model')} to {run.model}…")
            strata_stop()
        strata_start(run.model, run)


def strata_log_tail():
    """What the engine has written to its log since the load began (the loading bar's steps)."""
    profile = strata_profiles().get(STRATA_PROC.get("model") or "") if strata_dir() else None
    path, offset = (profile or {}).get("config", {}).get("log"), STRATA_DIRECT.get("log_offset")
    if not path or offset is None:
        return ""
    try:
        with open(path, "rb") as fh:
            fh.seek(offset)
            return fh.read(256 * 1024).decode("utf-8", "replace")
    except OSError:
        return ""


def strata_idle_loop():
    """Strata's own server keeps the model loaded; Studio unloads it after strata_idle_minutes without requests, when no
    run of Studio works, as the Manager does for its Strata."""
    while True:
        time.sleep(float(APP.cfg.get("strata_idle_check_s") or 30))
        minutes = float(APP.cfg.get("strata_idle_minutes") or 0)
        if APP.gateway_kind != "strata" or minutes <= 0 or APP.active:
            continue
        st = strata_direct_status()
        act = (st or {}).get("activity") or {}
        last = act.get("last_request_at")
        if not st or not st.get("loaded") or act.get("in_flight") or not last or time.time() - last < minutes * 60:
            continue
        try:
            gateway_json("POST", "/v1/unload", {}, timeout=60)
            print(f"Strata unloaded after {minutes:g} min without requests", flush=True)
        except (OSError, ValueError, http.client.HTTPException) as exc:
            print(f"Strata did not unload: {exc}", flush=True)


def model_state(model, strata, loaded, streaming, run_active):
    """Summarize what the model is doing as (state, label, detail). States drive the dashboard's colours."""
    match = next((m for m in loaded if m.get("name") == model or m.get("model") == model), None)
    if not is_strata_model(model):
        if not match:
            return "off", "Not loaded", "Loads on the next request"
        return ("work", "Generating", "") if streaming else ("work", "Working", "") if run_active else ("idle", "Loaded", "")
    job = strata.get("job") or {}
    lifecycle = strata.get("lifecycle") or {}
    message = str(job.get("message") or "")
    health = strata.get("health") or {}
    if job.get("kind") == "stop" and job.get("state") in ("starting", "stopping", "running"):
        return "load", "Unloading", ""
    if job.get("kind") == "start" and job.get("state") in ("starting", "running") and not (strata.get("running") and health.get("loaded")):
        return "load", "Loading", message.removeprefix("[strata] ")
    if not strata.get("running"):
        return "off", "Not loaded", "Loads on the next request"
    if streaming:
        return "work", "Generating", ""
    if lifecycle.get("active_requests"):
        if message.startswith("[strata] reading the prompt"):
            return "work", "Reading prompt", message.removeprefix("[strata] ")
        return "work", "Working", ""
    unload = lifecycle.get("next_unload_at")
    minutes = max(1, round((unload - time.time()) / 60)) if isinstance(unload, (int, float)) else 0
    return "idle", "Idle", f"Unloads in about {minutes} min" if minutes else "Loaded and ready"


# ---- What holds the graphics card and RAM now (2026-11-14), for the model dashboard's "Loaded now" list: the
# Manager's loaded models (/api/ps) plus every program of this account using the model's graphics card, read from the
# kernel's per-client counters (/proc/<pid>/fdinfo: drm-pdev, drm-client-id, drm-memory-vram in KiB), with its RSS.
RESIDENT_KINDS = [   # (part of the command line, label, what it is)
    ("Strata/engine/strata-vision", "Strata vision", "reads pictures for the model"),
    ("Strata/engine/strata ", None, "language model (Strata)"),
    ("comfyui", "ComfyUI", "pictures, video and music"),
    ("voice_engine.py", "Voices · VoxCPM2", "reading aloud"),
    ("sound_engine.py", "Sound effects · MOSS-SoundEffect", "sound effects"),
    ("music_engine.py", "Music · HeartMuLa", "music"),
    ("ollama runner", None, "language model (Ollama)"),
    ("blender", "Blender", "3D"),
    ("studio-browser", "Agent's browser", "Chromium"),
]
RESIDENT_CACHE = {"at": 0.0, "value": None}
MIB = 2 ** 20


def gpu_clients():
    """{pid: {pdev: vram bytes}} for the processes whose fdinfo this account can read."""
    out = {}
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            fds = os.listdir(f"/proc/{pid}/fdinfo")
        except OSError:
            continue
        seen = {}
        for fd in fds:
            try:
                with open(f"/proc/{pid}/fdinfo/{fd}") as fh:
                    text = fh.read()
            except OSError:
                continue
            if "drm-memory-vram" not in text:
                continue
            info = dict(line.split(":", 1) for line in text.splitlines() if ":" in line)
            try:
                kib = int(info["drm-memory-vram"].split()[0])
            except (KeyError, ValueError, IndexError):
                continue
            key = (info.get("drm-pdev", "").strip(), info.get("drm-client-id", "").strip())
            seen[key] = max(seen.get(key, 0), kib * 1024)       # one client is often open through several fds
        for (pdev, _client), size in seen.items():
            if size:
                per = out.setdefault(int(pid), {})
                per[pdev] = per.get(pdev, 0) + size
    return out


def process_rss(pid):
    try:
        with open(f"/proc/{pid}/status") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    return 0


def process_command(pid):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            return fh.read().replace(b"\0", b" ").decode("utf-8", "replace")
    except OSError:
        return ""


def memory_residents(loaded, gpu):
    """{card: {name, total, used}, items: [{name, what, vram, ram}]}, biggest first: what holds the model's graphics card and the RAM."""
    if time.time() - RESIDENT_CACHE["at"] < 3 and RESIDENT_CACHE["value"]:
        return RESIDENT_CACHE["value"]
    cards = {g.get("pci_address"): g for g in (gpu or {}).get("gpus") or [] if g.get("pci_address")}
    main = next((a for a, g in cards.items() if g.get("compute_capable") or g.get("model_dedicated")), None)
    clients = gpu_clients()
    strata_models = [m for m in loaded if "strata" in (m.get("name") or "") or (m.get("runtime") or "") == "strata"]
    other_models = [m for m in loaded if m not in strata_models]
    pids = set(clients)
    for pid in os.listdir("/proc"):          # also the known programs that hold RAM but no graphics memory right now
        if pid.isdigit() and int(pid) not in pids:
            cmd = process_command(pid)
            if any(part in cmd for part, _l, _w in RESIDENT_KINDS) and process_rss(pid) > 512 * MIB:
                pids.add(int(pid))
    items = []
    for pid in sorted(pids):
        cmd = process_command(pid)
        kind = next(((part, label, what) for part, label, what in RESIDENT_KINDS if part in cmd), None)
        vram = sum(v for d, v in clients.get(pid, {}).items() if not main or d == main)
        ram = process_rss(pid)
        if not kind and vram < 256 * MIB:
            continue                       # the desktop, browsers and other small users of the card
        if kind and kind[2].startswith("language model"):
            models = strata_models if "Strata" in kind[2] else other_models
            if not models:
                items.append({"name": "Strata engine" if "Strata" in kind[2] else "Ollama", "what": kind[2], "vram": vram, "ram": ram})
            elif len(models) == 1:
                items.append({"name": models[0].get("name"), "what": kind[2], "vram": max(vram, int(models[0].get("size_vram") or 0)), "ram": ram})
            else:
                for m in models:
                    size, in_vram = int(m.get("size") or 0), int(m.get("size_vram") or 0)
                    items.append({"name": m.get("name"), "what": kind[2], "vram": in_vram, "ram": max(0, size - in_vram)})
            models.clear()
            continue
        if vram < 64 * MIB and ram < 512 * MIB:
            continue
        name = kind[1] if kind else os.path.basename(cmd.split(" ")[0]) or f"process {pid}"
        prev = next((i for i in items if i["name"] == name), None)
        if prev:                           # several processes of one program: one row
            prev["vram"] += vram
            prev["ram"] += ram
        else:
            items.append({"name": name, "what": kind[2] if kind else "program", "vram": vram, "ram": ram})
    for m in strata_models + other_models:    # loaded models whose process this account cannot see
        size, in_vram = int(m.get("size") or 0), int(m.get("size_vram") or 0)
        items.append({"name": m.get("name"), "what": "language model", "vram": in_vram, "ram": max(0, size - in_vram)})
    items.sort(key=lambda i: -(i["vram"] * 4 + i["ram"]))
    card = cards.get(main) or {}
    value = {"items": items, "card": {"name": card.get("name") or (gpu or {}).get("gpu_name"), "total": card.get("vram_total") or (gpu or {}).get("vram_total"),
                                      "used": card.get("vram_used") or (gpu or {}).get("vram_used")}}
    RESIDENT_CACHE.update(at=time.time(), value=value)
    return value


# ---------------------------------------------------------------------------
# Connecting a model server (packaging, 2026-11-24): Account & settings → Models finds the servers running on this
# machine and saves the one an admin picks into studio.config.json, without a restart.
# ---------------------------------------------------------------------------

MODEL_SERVER_GUESSES = (("Ollama", 11434), ("Ollama Manager", 8787), ("llama.cpp or Strata", 8080), ("Strata", 18080), ("vLLM", 8000),
                        ("LM Studio", 1234))
MODEL_SERVER_KINDS = {"manager": "Ollama Manager", "ollama": "Ollama", "strata": "Strata",
                      "openai": "OpenAI API (llama.cpp, vLLM, LM Studio…)"}


def gateway_same(a, b):
    """Two gateway addresses that reach the same server ("…:8000" and "…:8000/v1/")."""
    def norm(url):
        parts = urllib.parse.urlsplit((url or "").strip())
        path = parts.path.rstrip("/")
        path = path[:-3] if path.endswith("/v1") else path
        port = parts.port or (443 if parts.scheme == "https" else 80)
        host = "127.0.0.1" if parts.hostname in ("localhost", "::1") else parts.hostname
        return parts.scheme, host, port, path
    return norm(a) == norm(b)


def probe_model_server(url, key="", timeout=3):
    """What answers at `url`, read the way Studio reads a gateway: {url, ok, kind, label, models, count, error}.
    The key is only sent there, never returned."""
    out = {"url": url, "ok": False, "kind": "", "label": "", "models": [], "count": 0, "error": ""}
    parts = urllib.parse.urlsplit((url or "").strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        out["error"] = "Give an address that starts with http:// or https://"
        return out
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        out["error"] = "The port is not a number"
        return out
    prefix = parts.path.rstrip("/")
    prefix = prefix[:-3] if prefix.endswith("/v1") else prefix
    headers = {**GATEWAY_HEADERS, **({"Authorization": f"Bearer {key}"} if key else {})}

    def get(path):
        cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
        conn = cls(parts.hostname, port, timeout=timeout)
        try:
            conn.request("GET", prefix + path, headers=headers)
            resp = conn.getresponse()
            return resp.status, resp.read(4 * MIB)
        finally:
            conn.close()

    def listed(data, field, name):
        items = json.loads(data).get(field)
        if not isinstance(items, list):
            raise ValueError("no model list")
        return [m[name] for m in items if isinstance(m, dict) and isinstance(m.get(name), str) and m[name]]

    try:
        status, data = get("/api/tags")
        names = None
        if status == 200:
            try:
                names, kind = listed(data, "models", "name"), "ollama"
            except ValueError:
                names = None
        if names is not None:
            try:
                if get("/api/strata/status")[0] == 200:
                    kind = "manager"
            except (OSError, http.client.HTTPException):
                pass
        else:
            status, data = get("/v1/models")
            if status in (401, 403):
                out["error"] = "The server asks for a key: put it in the Key box"
                return out
            if status != 200:
                out["error"] = f"It answered, but not as a model server (HTTP {status} for /v1/models)"
                return out
            names, kind = listed(data, "data", "id"), "openai"
            try:                  # Strata's own server says so in /v1/status
                status, data = get("/v1/status")
                if status == 200 and json.loads(data).get("service") == "strata":
                    kind = "strata"
            except (OSError, ValueError, AttributeError, http.client.HTTPException):
                pass
    except ConnectionRefusedError:
        out["error"] = "Nothing answers at that address"
        return out
    except (OSError, http.client.HTTPException) as exc:
        out["error"] = f"Could not reach it: {exc}"[:200]
        return out
    except ValueError:
        out["error"] = "It answered, but not as a model server"
        return out
    out.update(ok=True, kind=kind, label=MODEL_SERVER_KINDS[kind], models=names[:40], count=len(names))
    if not names:
        out["error"] = "It answers, but has no models yet"
    return out


@route("GET", "/api/admin/model_server", admin=True)
def api_model_server(req):
    """The connected model server, and the others found on this machine's usual ports."""
    gateway, key = APP.cfg["gateway"], APP.cfg.get("gateway_key") or ""
    guesses = [(name, f"http://127.0.0.1:{port}") for name, port in MODEL_SERVER_GUESSES]
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(guesses) + 1) as pool:
        current = pool.submit(probe_model_server, gateway, key)
        found = list(pool.map(lambda g: probe_model_server(g[1], timeout=1.5), guesses))
    others = [{**f, "guess": name} for (name, url), f in zip(guesses, found) if f["ok"] and not gateway_same(url, gateway)]
    return {"current": {**current.result(), "has_key": bool(key)}, "found": others}


def model_server_key(req):
    """The key from the form: a string replaces the saved one ("" removes it); absent keeps the saved one."""
    key = req.field("key")
    if key is None:
        return APP.cfg.get("gateway_key") or ""
    if not isinstance(key, str) or len(key) > 4096 or any(c in key for c in "\r\n"):
        raise ApiError(400, "the key must be one line of text")
    return key.strip()


@route("POST", "/api/admin/model_names", admin=True)
def api_model_names(req):
    """Names people see for models (Keystone for swift-1.5-iq3_xxs), kept in studio.config.json; an empty name removes one."""
    name, label = req.field("name"), req.field("label")
    if not isinstance(name, str) or not name.strip() or len(name) > 200:
        raise ApiError(400, "name is required")
    if not isinstance(label, str) or len(label) > 60 or any(c in label for c in "\r\n"):
        raise ApiError(400, "the new name must be one line of at most 60 characters")
    name, label = name.strip(), label.strip()
    with APP.mcp_edit:
        stored = read_json(APP.config_path, {}) or {}
        names = dict(stored.get("model_names") or {})
        if label:
            names[name] = label
        else:
            names.pop(name, None)
        stored["model_names"] = names
        write_json_atomic(APP.config_path, stored, private=bool(stored.get("gateway_key")))
        APP.cfg["model_names"] = names
    with APP.lock:
        APP.models_cache = {"t": 0.0, "models": [], "ok": False}
    return {"model_names": names, "models": get_models()[0]}


@route("POST", "/api/admin/model_server/test", admin=True)
def api_model_server_test(req):
    gateway = req.field("gateway")
    if not isinstance(gateway, str) or not gateway.strip():
        raise ApiError(400, "gateway is required")
    return probe_model_server(gateway.strip(), model_server_key(req))


@route("POST", "/api/admin/model_server", admin=True)
def api_model_server_save(req):
    """Saves gateway and gateway_key into studio.config.json and uses them at once."""
    gateway = req.field("gateway")
    if not isinstance(gateway, str) or not gateway.strip():
        raise ApiError(400, "gateway is required")
    gateway, key = gateway.strip().rstrip("/"), model_server_key(req)
    probe = probe_model_server(gateway, key)
    if not probe["ok"]:
        raise ApiError(400, probe["error"] or "that address does not answer as a model server")
    with APP.mcp_edit:
        stored = read_json(APP.config_path, {}) or {}
        stored["gateway"] = gateway
        if key:
            stored["gateway_key"] = key
        else:
            stored.pop("gateway_key", None)
        write_json_atomic(APP.config_path, stored, private=bool(key))
        APP.cfg["gateway"], APP.cfg["gateway_key"] = gateway, key
    with APP.lock:
        APP.models_cache = {"t": 0.0, "models": [], "ok": False}
    models, ok = get_models()
    return {"server": probe, "models": models, "gateway_ok": ok}

@route("GET", "/api/model/status")
def api_model_status(req):
    """The model's state, and with full=1 the Manager's live metrics for the dashboard's gauges."""
    model = req.query.get("model") or APP.cfg["default_model"]
    streaming = time.time() - APP.stream_at < 3
    out = {"model": model, "stream_chars": APP.stream_chars, "time": time.time()}
    with APP.lock:
        run_active = any(run.model == model for run in APP.active.values())
    try:
        strata, loaded = gateway_state()
    except (OSError, ValueError, http.client.HTTPException) as exc:
        return {**out, "state": "err", "label": "Gateway offline", "detail": str(exc)[:200], "metrics": None}
    if loaded is None:          # a server with only the OpenAI API keeps its models loaded and cannot say more
        state, label, detail = ("work", "Generating", "") if streaming else ("work", "Working", "") if run_active else ("idle", "Ready", "")
        return {**out, "state": state, "label": label, "detail": detail, "active_requests": 0, "last_generation": {},
                "loaded": [], "cleanup": CLEANUP["last"], "strata_idle": {}, "metrics": None, "server": "openai"}
    state, label, detail = model_state(model, strata, loaded, streaming, run_active)
    progress = load_progress(model, strata) if state == "load" and label == "Loading" else None
    if progress:
        out["load"] = progress
    elif is_strata_model(model):
        note_load_finished(model, strata)
    lifecycle = strata.get("lifecycle") or {}
    out.update(state=state, label=label, detail=detail, active_requests=lifecycle.get("active_requests") or 0,
               last_generation=strata.get("last_generation") or {},
               loaded=[{k: m.get(k) for k in ("name", "size", "size_vram", "processor")} for m in loaded])
    out["cleanup"] = CLEANUP["last"]
    out["strata_idle"] = {"minutes": lifecycle.get("idle_timeout_minutes"), "unload_at": lifecycle.get("next_unload_at")}
    out["metrics"] = None
    if req.query.get("full") == "1" and strata.get("direct"):
        out["metrics"] = strata_direct_metrics(strata["direct"])
        try:
            out["metrics"]["residents"] = memory_residents([], {})
        except OSError:
            pass
    elif req.query.get("full") == "1":
        try:
            metrics = gateway_json("GET", "/api/metrics?model=" + urllib.parse.quote(model), timeout=5)
            out["metrics"] = {k: metrics.get(k) for k in ("system", "gpu", "process", "placement", "strain", "runtime")}
            try:
                out["metrics"]["residents"] = memory_residents(loaded, metrics.get("gpu"))
            except OSError:
                pass
            if req.current_user["role"] != "admin" and isinstance(out["metrics"].get("process"), dict):
                out["metrics"]["process"].pop("items", None)      # command lines are for admins
        except (OSError, ValueError, http.client.HTTPException):
            pass
    return out


# ---------------------------------------------------------------------------
# ComfyUI dashboard: what the local ComfyUI is making for the comfyui MCP server
# ---------------------------------------------------------------------------

COMFYUI_CLIENT = "strata-studio"      # tools/comfyui_mcp.py queues under this id, and ComfyUI sends step progress only to it
COMFYUI_RECENT = 6
COMFYUI_IMAGE = re.compile(r"[\w.() -]{1,200}\.(png|jpe?g|webp)", re.I)


COMFYUI_MODELS = ("z-image-turbo", "z-image-base", "sdxl", "qwen-image", "auto")   # "auto": SDXL for painted styles, Z-Image Turbo otherwise


def comfy_settings_path():
    """Studio's ComfyUI settings; tools/comfyui_mcp.py reads them on every picture (COMFYUI_SETTINGS)."""
    return os.path.join(APP.data_dir, "comfyui.json")


def comfy_default_model():
    model = read_json(comfy_settings_path(), {}).get("default_model")
    return model if model in COMFYUI_MODELS else "z-image-turbo"


MUSIC_MODELS = ("ace-step", "heartmula")   # ACE-Step 1.5 in ComfyUI, HeartMuLa in tools/music_engine.py


def comfy_music_model():
    model = read_json(comfy_settings_path(), {}).get("music_model")
    return model if model in MUSIC_MODELS else "ace-step"


@route("POST", "/api/comfyui/settings", admin=True)
def comfyui_settings(req):
    """The models the comfyui tool uses when the agent does not name one: pictures (default_model) and songs (music_model)."""
    settings, changed = read_json(comfy_settings_path(), {}), {}
    for field, allowed in (("default_model", COMFYUI_MODELS), ("music_model", MUSIC_MODELS)):
        value = req.field(field)
        if value is None:
            continue
        if value not in allowed:
            raise ApiError(400, f"{field} must be one of {', '.join(allowed)}")
        settings[field] = changed[field] = value
    if not changed:
        raise ApiError(400, "default_model or music_model is required")
    write_json_atomic(comfy_settings_path(), settings)
    return {"default_model": comfy_default_model(), "music_model": comfy_music_model()}


def comfy_swap():
    """Whether the comfyui tool has unloaded Strata to give a big model the whole card, and since when."""
    path = APP.cfg.get("comfyui_swap_file")
    data = read_json(os.path.expanduser(path), {}) if path else {}
    active = data.get("full") is True and float(data.get("until") or 0) > time.time()
    return {"active": active, "since": data.get("since") if active else None}


def comfy_request(path, timeout=5):
    """(status, body) of a GET to the configured ComfyUI."""
    parts = urllib.parse.urlsplit(APP.cfg["comfyui_url"])
    conn = http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=timeout)
    try:
        conn.request("GET", parts.path.rstrip("/") + path)
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


def comfy_json(path):
    status, body = comfy_request(path)
    if status != 200:
        raise OSError(f"ComfyUI returned {status}")
    return json.loads(body)


class ComfyWatch:
    """Listens on ComfyUI's WebSocket as COMFYUI_CLIENT for the step progress of the pictures it makes."""

    def __init__(self):
        self.lock = threading.Lock()
        self.progress = {}                    # job -> {"value", "max"}
        self.started = {}                     # job -> when ComfyUI began it
        self.nodes = {}                       # job -> {"node": running node, "done": [...], "cached": [...], "error": {node, message}}
        self.url = None
        self.connected = False

    def ensure(self, url):
        with self.lock:
            if self.url == url:
                return
            self.url = url
        threading.Thread(target=self.run, args=(url,), daemon=True).start()

    def run(self, url):
        delay = 1
        while self.url == url:
            try:
                self.listen(url)
                delay = 1
            except (OSError, ValueError):
                pass
            self.connected = False
            time.sleep(delay)
            delay = min(delay * 2, 30)

    def listen(self, url):
        parts = urllib.parse.urlsplit(url)
        sock = socket.create_connection((parts.hostname, parts.port or 80), timeout=300)
        try:
            key = base64.b64encode(os.urandom(16)).decode()
            sock.sendall((f"GET {parts.path.rstrip('/')}/ws?clientId={COMFYUI_CLIENT} HTTP/1.1\r\nHost: {parts.netloc}\r\n"
                          f"Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                          "Sec-WebSocket-Version: 13\r\n\r\n").encode())
            reader = sock.makefile("rb")
            if b" 101 " not in reader.readline():
                raise OSError("ComfyUI refused the WebSocket")
            while reader.readline() not in (b"\r\n", b""):
                pass
            self.connected = True
            while self.url == url:
                head = reader.read(2)
                if len(head) < 2:
                    return
                opcode, length = head[0] & 0x0F, head[1] & 0x7F
                if length == 126:
                    length = struct.unpack(">H", reader.read(2))[0]
                elif length == 127:
                    length = struct.unpack(">Q", reader.read(8))[0]
                mask = reader.read(4) if head[1] & 0x80 else b""
                payload = reader.read(length)
                if mask:
                    payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
                if opcode == 8:
                    return
                if opcode == 9:               # ping: a client's frames are masked
                    pong_mask = os.urandom(4)
                    sock.sendall(bytes([0x8A, 0x80 | len(payload[:125])]) + pong_mask
                                 + bytes(b ^ pong_mask[i % 4] for i, b in enumerate(payload[:125])))
                elif opcode == 1:
                    self.note(json.loads(payload))
        finally:
            sock.close()

    def note(self, message):
        kind, data = message.get("type"), message.get("data") or {}
        job = data.get("prompt_id")
        if not isinstance(job, str):
            return
        with self.lock:
            nodes = self.nodes.setdefault(job, {"node": None, "done": [], "cached": [], "error": None})
            if kind == "execution_start":
                self.started[job] = time.time()
                self.progress.pop(job, None)
            elif kind == "progress":
                self.progress[job] = {"value": data.get("value"), "max": data.get("max")}
            elif kind == "execution_cached":
                nodes["cached"] = [str(n) for n in data.get("nodes") or []]
            elif kind == "executing":
                if nodes["node"] is not None and nodes["node"] not in nodes["done"]:
                    nodes["done"].append(nodes["node"])      # ComfyUI moved on, so the last node finished
                nodes["node"] = str(data["node"]) if data.get("node") is not None else None
            elif kind == "executed" and str(data.get("node")) not in nodes["done"]:
                nodes["done"].append(str(data.get("node")))
            elif kind in ("execution_success", "execution_error", "execution_interrupted"):
                self.progress.pop(job, None)
                if kind == "execution_error":
                    nodes["error"] = {"node": str(data.get("node_id")), "message": str(data.get("exception_message") or "")[:2000]}
                nodes["node"] = None
            for table in (self.started, self.progress, self.nodes):
                while len(table) > 50:
                    table.pop(next(iter(table)))


COMFY_WATCH = ComfyWatch()


def comfy_summary(graph):
    """Prompt, size, steps and seed of an API-format workflow, read from its sampler and the nodes feeding it."""
    graph = graph if isinstance(graph, dict) else {}
    nodes = [n for n in graph.values() if isinstance(n, dict) and isinstance(n.get("inputs"), dict)]
    sampler = next((n["inputs"] for n in nodes if "steps" in n["inputs"] and "positive" in n["inputs"]), {})
    texts = [n["inputs"]["text"] for n in nodes if isinstance(n["inputs"].get("text"), str)]
    ref = sampler.get("positive")
    linked = graph.get(str(ref[0]), {}).get("inputs", {}).get("text") if isinstance(ref, list) and ref else None
    latent = next((n["inputs"] for n in nodes if n.get("class_type") in ("EmptyLatentImage", "EmptySD3LatentImage")), {})
    out = {"prompt": (linked if isinstance(linked, str) else texts[0] if texts else "")[:600]}
    for key in ("steps", "seed", "cfg"):
        if isinstance(sampler.get(key), (int, float)):
            out[key] = sampler[key]
    if isinstance(latent.get("width"), int) and isinstance(latent.get("height"), int):
        out["size"] = f"{latent['width']}x{latent['height']}"
    return out


@route("GET", "/api/comfyui/status", admin=True)
def comfyui_status(req):
    """What ComfyUI is making now (with step progress for the comfyui MCP server's pictures), its queue, memory and latest pictures."""
    url = APP.cfg["comfyui_url"]
    if not url:
        return {"available": False}
    COMFY_WATCH.ensure(url)
    try:
        queue, stats = comfy_json("/queue"), comfy_json("/system_stats")
        history = comfy_json(f"/history?max_items={COMFYUI_RECENT}")
    except (OSError, ValueError, http.client.HTTPException):
        return {"available": True, "state": "err", "error": f"ComfyUI at {url} does not answer", "default_model": comfy_default_model(),
                "music_model": comfy_music_model(), "swap": comfy_swap()}
    running, pending = queue.get("queue_running") or [], queue.get("queue_pending") or []
    out = {"available": True, "state": "work" if running else "idle", "pending": len(pending), "live": COMFY_WATCH.connected,
           "default_model": comfy_default_model(), "music_model": comfy_music_model(), "swap": comfy_swap(),
           "version": (stats.get("system") or {}).get("comfyui_version"), "time": time.time()}
    system, device = stats.get("system") or {}, (stats.get("devices") or [{}])[0]
    out["memory"] = {"ram_total": system.get("ram_total"), "ram_free": system.get("ram_free"),
                     "vram_total": device.get("vram_total"), "vram_free": device.get("vram_free"), "device": device.get("name")}
    if running:
        entry = running[0]
        job = entry[1] if len(entry) > 1 else ""
        current = {"job": job, "studio": (entry[3] if len(entry) > 3 and isinstance(entry[3], dict) else {}).get("client_id") == COMFYUI_CLIENT,
                   **comfy_summary(entry[2] if len(entry) > 2 else {})}
        with COMFY_WATCH.lock:
            step, started = COMFY_WATCH.progress.get(job), COMFY_WATCH.started.get(job)
        if step:
            current["step"], current["of"] = step["value"], step["max"]
        if started:
            current["elapsed"] = round(time.time() - started, 1)
        out["current"] = current
    recent = []
    for job, item in (history or {}).items():
        if not isinstance(item, dict):
            continue
        status = item.get("status") or {}
        times = {name: data.get("timestamp") for name, data in status.get("messages") or [] if isinstance(data, dict)}
        files = [{"filename": img.get("filename"), "subfolder": img.get("subfolder", ""), "type": img.get("type", "output")}
                 for output in (item.get("outputs") or {}).values() for img in output.get("images", [])
                 if img.get("type", "output") == "output" and isinstance(img.get("filename"), str)]
        images = [f for f in files if COMFYUI_IMAGE.fullmatch(f["filename"])]
        videos = [f["filename"] for f in files if f["filename"].lower().endswith((".mp4", ".webm", ".mov"))]
        done = times.get("execution_success") or times.get("execution_error") or times.get("execution_interrupted")
        prompt = item.get("prompt") or []
        recent.append({"job": job, "ok": status.get("status_str") == "success", "images": images, "videos": videos, "finished": done / 1000 if done else None,
                       "seconds": round((done - times["execution_start"]) / 1000, 1) if done and times.get("execution_start") else None,
                       **comfy_summary(prompt[2] if len(prompt) > 2 else {})})
    recent.sort(key=lambda r: r["finished"] or 0, reverse=True)
    out["recent"] = recent[:COMFYUI_RECENT]
    return out


def comfy_nodes(graph):
    """The nodes of an API-format workflow: id, type, title, plain settings, and links to the nodes they read from."""
    out = []
    for node_id, node in (graph if isinstance(graph, dict) else {}).items():
        if not isinstance(node, dict):
            continue
        values, links = {}, []
        for name, value in (node.get("inputs") or {}).items():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[1], int):
                links.append({"input": name, "node": str(value[0]), "slot": value[1]})
            elif isinstance(value, (str, int, float, bool)) or value is None:
                values[name] = value[:4000] if isinstance(value, str) else value
        out.append({"id": str(node_id), "type": str(node.get("class_type") or "?"), "title": str((node.get("_meta") or {}).get("title") or ""),
                    "values": values, "links": links})
    return out


@route("GET", "/api/comfyui/graph", admin=True)
def comfyui_graph(req):
    """A job's workflow as nodes and links, with which node runs now and which are done, cached or failed.
    Without a job: the picture being made, or else the latest one."""
    url = APP.cfg["comfyui_url"]
    if not url:
        raise ApiError(404, "ComfyUI is not set up")
    COMFY_WATCH.ensure(url)
    job = req.arg("job")
    try:
        queue = comfy_json("/queue")
        entries = [("running", e) for e in queue.get("queue_running") or []] + [("waiting", e) for e in queue.get("queue_pending") or []]
        found = next(((state, e) for state, e in entries if len(e) > 2 and (e[1] == job or not job and state == "running")), None)
        history = None if found else comfy_json(f"/history/{urllib.parse.quote(job)}" if job else "/history?max_items=1")
    except (OSError, ValueError, http.client.HTTPException):
        raise ApiError(502, f"ComfyUI at {url} does not answer") from None
    out = {"images": {}, "done": [], "cached": [], "node": None, "error": None}
    if found:
        state, entry = found
        job, graph = entry[1], entry[2]
        with COMFY_WATCH.lock:
            seen, step, started = COMFY_WATCH.nodes.get(job) or {}, COMFY_WATCH.progress.get(job), COMFY_WATCH.started.get(job)
            out.update({k: (list(v) if isinstance(v, list) else v) for k, v in seen.items()})
        if step:
            out["step"], out["of"] = step["value"], step["max"]
        if started:
            out["elapsed"] = round(time.time() - started, 1)
        out["studio"] = (entry[3] if len(entry) > 3 and isinstance(entry[3], dict) else {}).get("client_id") == COMFYUI_CLIENT
    else:
        if not history:
            raise ApiError(404, "ComfyUI has made no pictures yet" if not job else "no such job")
        job, item = next(iter(history.items())) if not job else (job, history.get(job))
        if not isinstance(item, dict):
            raise ApiError(404, "no such job")
        prompt, status = item.get("prompt") or [], item.get("status") or {}
        graph = prompt[2] if len(prompt) > 2 else {}
        messages = [(name, data) for name, data in status.get("messages") or [] if isinstance(data, dict)]
        cached = next((data.get("nodes") or [] for name, data in messages if name == "execution_cached"), [])
        failed = next((data for name, data in messages if name == "execution_error"), None)
        times = {name: data.get("timestamp") for name, data in messages}
        state = "done" if status.get("status_str") == "success" else "error" if failed else "stopped"
        out["cached"] = [str(n) for n in cached]
        out["done"] = [str(n) for n in graph if str(n) not in out["cached"]] if state == "done" else []
        if failed:
            out["error"] = {"node": str(failed.get("node_id")), "message": str(failed.get("exception_message") or "")[:2000]}
        if times.get("execution_start") and (times.get("execution_success") or times.get("execution_error")):
            out["seconds"] = round(((times.get("execution_success") or times.get("execution_error")) - times["execution_start"]) / 1000, 1)
        for node_id, output in (item.get("outputs") or {}).items():
            images = [{"filename": i.get("filename"), "subfolder": i.get("subfolder", "")} for i in output.get("images", [])
                      if i.get("type", "output") == "output" and isinstance(i.get("filename"), str) and COMFYUI_IMAGE.fullmatch(i["filename"])]
            if images:
                out["images"][str(node_id)] = images
    out.update({"job": job, "state": state, "nodes": comfy_nodes(graph), "summary": comfy_summary(graph)})
    return out


@route("GET", "/api/comfyui/image", admin=True)
def comfyui_image(req):
    """A picture from ComfyUI's output folder, for the dashboard's thumbnails."""
    name, folder = req.arg("filename"), req.arg("subfolder")
    if not APP.cfg["comfyui_url"] or not COMFYUI_IMAGE.fullmatch(name) or ".." in folder or folder.startswith(("/", "\\")):
        raise ApiError(404, "not found")
    try:
        status, data = comfy_request("/view?" + urllib.parse.urlencode({"filename": name, "subfolder": folder, "type": "output"}), timeout=20)
    except (OSError, http.client.HTTPException):
        raise ApiError(502, "ComfyUI does not answer") from None
    if status != 200:
        raise ApiError(404, "not found")
    ext = name.rsplit(".", 1)[1].lower()
    req.send_response(200)
    req.send_header("Content-Type", {"png": "image/png", "webp": "image/webp"}.get(ext, "image/jpeg"))
    req.send_header("Content-Length", str(len(data)))
    req.send_header("Cache-Control", "private, max-age=3600")
    req.end_headers()
    req.wfile.write(data)


# ---------------------------------------------------------------------------
# Video timelines: the same timelines the video MCP tool renders, edited and rendered from Studio's timeline tab
# ---------------------------------------------------------------------------

VIDEO_MEDIA = {".mp4": "video", ".webm": "video", ".mov": "video", ".mkv": "video", ".m4v": "video",
               ".png": "picture", ".jpg": "picture", ".jpeg": "picture", ".webp": "picture",
               ".mp3": "sound", ".wav": "sound", ".m4a": "sound", ".ogg": "sound", ".flac": "sound", ".aac": "sound"}
# The Media view also lists 3D models (not media a timeline can use): the browser shows these with three.js, and a
# Blender file is turned into a GLB by Blender first (blend_glb).
MODEL_MEDIA = {".glb": "model", ".gltf": "model", ".obj": "model", ".stl": "model", ".fbx": "model", ".ply": "model", ".blend": "model"}
MEDIA_KIND = {**VIDEO_MEDIA, **MODEL_MEDIA}
VIDEO_JOBS = {}                 # job id -> {ws, path, preview, state, percent, error, output, started}
VIDEO_LOCK = threading.Lock()
_VIDEO_TOOL = []


def video_tool():
    """tools/video_mcp.py, loaded once, so the timeline tab and the agent's tool share one way of building a video."""
    if not _VIDEO_TOOL:
        spec = importlib.util.spec_from_file_location("studio_video_tool", os.path.join(SCRIPT_DIR, "tools", "video_mcp.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _VIDEO_TOOL.append(module)
    return _VIDEO_TOOL[0]


def video_roots(req, ws):
    """Where a timeline's media may come from: the project, and for admins ComfyUI's output folder too."""
    roots = [os.path.realpath(ws["path"])]
    if req.current_user["role"] == "admin":
        roots.append(os.path.realpath(os.path.expanduser("~/.local/opt/comfyui/output")))
    return roots


def timeline_path(ws, rel):
    if not isinstance(rel, str) or not rel.endswith(".timeline.json"):
        raise ApiError(400, "a timeline is a .timeline.json file")
    path = resolve(ws, rel)
    refuse_git_folder(rel_posix(path, ws["path"]))
    return path


def read_timeline(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        raise ApiError(404, "no such timeline") from None
    except ValueError as exc:
        raise ApiError(400, f"the timeline is not valid JSON: {exc}") from None
    if not isinstance(data, dict):
        raise ApiError(400, "the timeline must be a JSON object")
    return data


def layout_of(req, ws, timeline):
    """Where each clip, title and sound falls, worked out the way a render would; an error names what is wrong."""
    tool = video_tool()
    if not timeline.get("clips"):
        return {"length": 0, "clips": [], "titles": [], "audio": []}
    with tempfile.TemporaryDirectory() as work:
        try:
            return tool.build({k: v for k, v in timeline.items() if k != "_layout"}, os.path.join(work, "x.mp4"), False, work,
                              roots=video_roots(req, ws))[2]
        except tool.Failure as exc:
            raise ApiError(400, str(exc)) from None


def write_timeline(path, timeline, layout, video):
    data = {k: v for k, v in timeline.items() if k != "_layout"}
    data["_layout"] = {**layout, "video": video}
    os.makedirs(os.path.dirname(path), exist_ok=True)      # whole or not at all: the open tab reads it every 2 s
    tmp = f"{path}.{secrets.token_hex(4)}.tmp"
    with open(tmp, "wb") as fh:
        fh.write((json.dumps(data, ensure_ascii=False, indent=1) + "\n").encode())
    os.replace(tmp, path)


@route("GET", "/api/video/list")
def video_list(req):
    """The project's timelines and the media a timeline can use."""
    ws = req.workspace()
    root = os.path.realpath(ws["path"])
    timelines, media = [], []
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in IGNORE_DIRS and not d.startswith("."))
        for name in sorted(files):
            full = os.path.join(folder, name)
            rel = rel_posix(full, root)
            if name.endswith(".timeline.json"):
                timelines.append({"path": rel, "mtime": os.path.getmtime(full)})
            elif os.path.splitext(name)[1].lower() in VIDEO_MEDIA and len(media) < 500:
                media.append({"path": rel, "full": full, "kind": VIDEO_MEDIA[os.path.splitext(name)[1].lower()], "size": os.path.getsize(full)})
        if len(timelines) > 200:
            break
    timelines.sort(key=lambda t: -t["mtime"])
    return {"timelines": timelines, "media": media, "folder": root}


# ---------------------------------------------------------------------------
# Media view and 3D models (a .blend is exported by Blender)
# ---------------------------------------------------------------------------

MEDIA_LENGTHS = {}             # (full path, mtime, size) -> seconds, from ffprobe; kept while Studio runs
MEDIA_MAX = 1000


def media_length(full, mtime, size):
    key = (full, mtime, size)
    if key not in MEDIA_LENGTHS:
        try:
            out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", full],
                                 capture_output=True, text=True, timeout=10)
            MEDIA_LENGTHS[key] = round(float(out.stdout.strip()), 1)
        except (ValueError, OSError, subprocess.SubprocessError):
            MEDIA_LENGTHS[key] = None
        if len(MEDIA_LENGTHS) > 20000:
            MEDIA_LENGTHS.clear()
    return MEDIA_LENGTHS[key]


BLEND_LOCK = threading.Lock()
BLEND_KEEP = 40
BLEND_EXPORT = ("import bpy, os; bpy.ops.export_scene.gltf(filepath=os.environ['STUDIO_GLB_OUT'], export_format='GLB', "
                "export_apply=True, export_cameras=False, export_lights=False)")


def blender_command():
    found = shutil.which("blender") or os.path.expanduser("~/.local/bin/blender")
    return found if os.path.isfile(found) else None


def blend_glb(full):
    """A Blender file as a GLB for the 3D preview: Blender exports it in the background (factory settings, its scripts
    never run), and the result is kept in data/model_cache by path, time and size, the newest BLEND_KEEP."""
    st = os.stat(full)
    cache = os.path.join(APP.data_dir, "model_cache")
    out = os.path.join(cache, hashlib.sha1(f"{full}|{st.st_mtime_ns}|{st.st_size}".encode()).hexdigest() + ".glb")
    with BLEND_LOCK:
        if os.path.isfile(out):
            os.utime(out)
            return out
        blender = blender_command()
        if not blender:
            raise ApiError(400, "Blender is not installed on this machine, so .blend files cannot be shown. Export it as .glb.")
        os.makedirs(cache, exist_ok=True)
        part = out + ".part.glb"
        try:
            done = subprocess.run([blender, "-b", "--factory-startup", "--disable-autoexec", full, "--python-expr", BLEND_EXPORT],
                                  env={**os.environ, "STUDIO_GLB_OUT": part}, capture_output=True, text=True, timeout=180,
                                  stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            raise ApiError(400, "Blender took more than 3 minutes to export this file.")
        if not os.path.isfile(part):
            why = [ln for ln in (done.stdout + done.stderr).splitlines() if re.search(r"error|cannot|failed", ln, re.I)]
            raise ApiError(400, "Blender could not export this file" + (": " + why[-1].strip()[:300] if why else "."))
        os.replace(part, out)
        kept = sorted((os.path.join(cache, n) for n in os.listdir(cache) if n.endswith(".glb")), key=os.path.getmtime, reverse=True)
        for old in kept[BLEND_KEEP:]:
            try:
                os.remove(old)
            except OSError:
                pass
        return out


@route("GET", "/api/media/blend")
def media_blend(req):
    """The GLB of a Blender file in the folder, for the 3D preview. Admins only: Blender reads the file on this machine,
    outside any sandbox."""
    if req.current_user["role"] != "admin":
        raise ApiError(403, "Only admins can preview Blender files. Export it as .glb to see it here.")
    ws = req.workspace()
    full = resolve(ws, req.arg("path") or "")
    if not full.lower().endswith(".blend") or not os.path.isfile(full):
        raise ApiError(404, "not found")
    with open(blend_glb(full), "rb") as fh:
        data = fh.read()
    req.send_response(200)
    req.send_header("Content-Type", "model/gltf-binary")
    req.send_header("Content-Length", str(len(data)))
    req.send_header("Cache-Control", "no-store")
    req.end_headers()
    req.wfile.write(data)


@route("GET", "/api/media/list")
def media_list(req):
    """Every sound, picture and video in the project, newest first, with the length of sounds and videos: the Media view."""
    ws = req.workspace()
    root = os.path.realpath(ws["path"])
    items, more = [], False
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in IGNORE_DIRS and not d.startswith("."))
        for name in files:
            kind = MEDIA_KIND.get(os.path.splitext(name)[1].lower())
            if not kind or name.startswith("."):
                continue
            if len(items) >= MEDIA_MAX:
                more = True
                break
            full = os.path.join(folder, name)
            try:
                st = os.stat(full)
            except OSError:
                continue
            items.append({"path": rel_posix(full, root), "kind": kind, "size": st.st_size, "mtime": st.st_mtime, "full": full})
    items.sort(key=lambda m: -m["mtime"])
    # A video made from a timeline: render_video saves <name>.timeline.json beside <name>.mp4 (and a preview is
    # <name>.preview.mp4), and a timeline's _layout names its video.
    for m in items:
        if m["kind"] != "video":
            continue
        folder, name = os.path.split(m["full"])
        stem = re.sub(r"(\.preview)?\.[^.]+$", "", name)
        beside = os.path.join(folder, stem + ".timeline.json")
        if os.path.isfile(beside):
            m["timeline"] = rel_posix(beside, root)
    probed = 0
    for m in items:
        full = m.pop("full")
        if m["kind"] in ("sound", "video") and probed < 300:
            probed += 1
            m["seconds"] = media_length(full, m["mtime"], m["size"])
    return {"media": items, "more": more}


@route("GET", "/api/video/timeline")
def video_timeline(req):
    ws = req.workspace()
    path = timeline_path(ws, req.arg("path"))
    data = read_timeline(path)
    video = (data.get("_layout") or {}).get("video")
    full = os.path.join(os.path.dirname(path), video) if isinstance(video, str) and "/" not in video else None
    with VIDEO_LOCK:
        job = next(({**{x: y for x, y in j.items() if x != "proc"}, "id": k} for k, j in VIDEO_JOBS.items()          # proc is the running ffmpeg
                    if j["ws"] == ws["id"] and j["path"] == path and j["state"] == "running"), None)
    try:                             # a render may be replacing the video right now
        video_mtime = os.path.getmtime(full) if full else None
    except OSError:
        video_mtime = None
    return {"timeline": data, "mtime": os.path.getmtime(path),
            "video": rel_posix(full, ws["path"]) if video_mtime is not None else None, "video_mtime": video_mtime, "job": job}


@route("POST", "/api/video/new")
def video_new(req):
    ws = req.workspace()
    name = req.field("name")
    if not isinstance(name, str) or not re.fullmatch(r"[\w .()-]{1,60}", name, re.UNICODE):
        raise ApiError(400, "give the video a short name")
    src = req.field("src")
    timeline = {"width": 1920, "height": 1080, "fps": 30, "clips": [], "titles": [], "audio": []}
    if src is not None:              # a timeline that starts with this video (the player's Edit button)
        full = resolve(ws, src)
        if not os.path.isfile(full) or VIDEO_MEDIA.get(os.path.splitext(full)[1].lower()) != "video":
            raise ApiError(400, "src must be a video in the folder")
        timeline["clips"] = [{"src": full}]
        base, n = name.strip(), 1
        while os.path.exists(timeline_path(ws, f"video/{name.strip()}.timeline.json")):
            n += 1
            name = f"{base} {n}"
    rel = f"video/{name.strip()}.timeline.json"
    path = timeline_path(ws, rel)
    if os.path.exists(path):
        raise ApiError(409, f"{rel} already exists")
    layout = layout_of(req, ws, timeline) if timeline["clips"] else {"length": 0, "clips": [], "titles": [], "audio": []}
    write_timeline(path, timeline, layout, f"{name.strip()}.mp4")
    return {"path": rel}


@route("POST", "/api/video/save")
def video_save(req):
    """Saves a timeline the user changed, with its layout worked out again; a timeline that cannot render is refused."""
    ws = req.workspace()
    path = timeline_path(ws, req.field("path"))
    timeline = req.field("timeline")
    if not isinstance(timeline, dict):
        raise ApiError(400, "timeline must be an object")
    old = read_timeline(path) if os.path.exists(path) else {}
    layout = layout_of(req, ws, timeline)
    video = (old.get("_layout") or {}).get("video") or os.path.basename(path).replace(".timeline.json", ".mp4")
    write_timeline(path, timeline, layout, video)
    return {"layout": {**layout, "video": video}, "mtime": os.path.getmtime(path)}


def run_video_job(job_id, argv, length, path, timeline, layout, output):
    """Renders with ffmpeg, following its progress, then saves the timeline beside the video."""
    with VIDEO_LOCK:
        job = VIDEO_JOBS[job_id]
    partial = output[:-4] + ".rendering.mp4"
    argv = argv[:-1] + ["-progress", "pipe:1", "-nostats", partial]
    try:
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        job["proc"] = proc
        for line in proc.stdout:
            if line.startswith("out_time_us=") and line.strip()[12:].isdigit() and length:
                job["percent"] = min(99, round(int(line.strip()[12:]) / 1e6 / length * 100))
        err = proc.stderr.read()
        if proc.wait() != 0:
            raise RuntimeError("stopped" if job.get("cancelled") else (err.strip()[-800:] or f"ffmpeg exited with {proc.returncode}"))
        os.replace(partial, output)
        write_timeline(path, timeline, layout, os.path.basename(output))
        job.update(state="done", percent=100)
    except (OSError, RuntimeError) as exc:
        job.update(state="failed", error=str(exc))
    finally:
        job.pop("proc", None)
        job["ended"] = time.time()
        if os.path.exists(partial):
            os.remove(partial)


@route("POST", "/api/video/render")
def video_render(req):
    """Renders a timeline to <name>.mp4 (or <name>.preview.mp4, small and quick) in the background; poll /api/video/job."""
    ws = req.workspace()
    path = timeline_path(ws, req.field("path"))
    preview = req.field("preview") is True
    timeline = {k: v for k, v in read_timeline(path).items() if k != "_layout"}
    if not timeline.get("clips"):
        raise ApiError(400, "add a clip first")
    tool = video_tool()
    output = path[:-len(".timeline.json")] + (".preview.mp4" if preview else ".mp4")
    work = tempfile.mkdtemp(prefix="studio-video-")
    try:
        argv, length, layout = tool.build(timeline, output, preview, work, roots=video_roots(req, ws))
    except tool.Failure as exc:
        shutil.rmtree(work, ignore_errors=True)
        raise ApiError(400, str(exc)) from None
    with VIDEO_LOCK:
        if any(j["ws"] == ws["id"] and j["state"] == "running" for j in VIDEO_JOBS.values()):
            shutil.rmtree(work, ignore_errors=True)
            raise ApiError(409, "a video is already rendering in this folder")
        job_id = secrets.token_hex(6)
        VIDEO_JOBS[job_id] = {"ws": ws["id"], "path": path, "preview": preview, "state": "running", "percent": 0, "error": None,
                              "output": rel_posix(output, ws["path"]), "started": time.time(), "owner": req.current_user["username"]}
        for old in [k for k, j in VIDEO_JOBS.items() if j["state"] != "running" and time.time() - j.get("ended", 0) > 3600]:
            del VIDEO_JOBS[old]

    def run():
        try:
            run_video_job(job_id, argv, length, path, timeline, layout, output)
        finally:
            shutil.rmtree(work, ignore_errors=True)
    threading.Thread(target=run, daemon=True).start()
    return {"job": job_id, "length": length}


@route("GET", "/api/video/job")
def video_job(req):
    with VIDEO_LOCK:
        job = VIDEO_JOBS.get(req.arg("id"))
        if not job or job["owner"] != req.current_user["username"]:
            raise ApiError(404, "no such render")
        return {k: v for k, v in job.items() if k not in ("proc", "path", "owner")}


# ---------------------------------------------------------------------------
# Audio editor (2026-11-13): each edit is one ffmpeg run from a version to the next, in tools/audio_edit.py
# ---------------------------------------------------------------------------

AUDIO_EDIT_EXTS = (".mp3", ".wav", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".aac")
AUDIO_MAX_BYTES = 500 * 2 ** 20
AUDIO_HISTORY = 40               # versions kept for undo; older ones are deleted
AUDIO_KEEP_DAYS = 3
AUDIO_PER_USER = 30
AUDIO_LOCKS = collections.defaultdict(threading.Lock)
_AUDIO_TOOL = []


def audio_tool():
    if not _AUDIO_TOOL:
        spec = importlib.util.spec_from_file_location("studio_audio_edit", os.path.join(SCRIPT_DIR, "tools", "audio_edit.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _AUDIO_TOOL.append(module)
    return _AUDIO_TOOL[0]


def audio_root():
    return os.path.join(APP.data_dir, "audio_edits")


def audio_clip_path(username):
    return os.path.join(audio_root(), "clipboard", hashlib.sha256(username.encode()).hexdigest()[:16] + ".flac")


def audio_tidy(username):
    """Edits untouched for AUDIO_KEEP_DAYS go, and each person keeps their newest AUDIO_PER_USER."""
    root, now, mine = audio_root(), time.time(), []
    for name in os.listdir(root) if os.path.isdir(root) else []:
        folder = os.path.join(root, name)
        meta = read_json(os.path.join(folder, "meta.json"), None) if re.fullmatch(r"[0-9a-f]{16}", name) else None
        if not meta:
            continue
        if now - meta.get("touched", 0) > AUDIO_KEEP_DAYS * 86400:
            shutil.rmtree(folder, ignore_errors=True)
        elif meta.get("user") == username:
            mine.append((meta.get("touched", 0), folder))
    for _, folder in sorted(mine, reverse=True)[AUDIO_PER_USER:]:
        shutil.rmtree(folder, ignore_errors=True)


def audio_session(req, ws, eid):
    if not isinstance(eid, str) or not re.fullmatch(r"[0-9a-f]{16}", eid):
        raise ApiError(400, "no such audio edit")
    folder = os.path.join(audio_root(), eid)
    meta = read_json(os.path.join(folder, "meta.json"), None)
    if not meta or meta.get("user") != req.current_user["username"] or meta.get("ws") != ws["id"]:
        raise ApiError(404, "this edit is gone; open the file again")
    return folder, meta


def audio_save_meta(folder, meta):
    meta["touched"] = time.time()
    write_json_atomic(os.path.join(folder, "meta.json"), meta)


def audio_state(folder, meta, username):
    cur = meta["versions"][meta["v"]]
    clip = audio_clip_path(username)
    return {"id": os.path.basename(folder), "path": meta["path"], "v": meta["v"], "seconds": cur["seconds"], "rate": meta["rate"],
            "channels": meta["channels"], "history": [x["label"] for x in meta["versions"]], "saved_v": meta["saved_v"],
            "can_undo": meta["v"] > 0, "can_redo": meta["v"] < len(meta["versions"]) - 1, "version": cur["file"],
            "clip": os.path.isfile(clip), "src_mtime": meta["src_mtime"]}


def audio_file(ws, rel):
    path = resolve(ws, rel)
    rel = rel_posix(path, ws["path"])
    refuse_git_folder(rel)
    if os.path.splitext(path)[1].lower() not in AUDIO_EDIT_EXTS:
        raise ApiError(400, "the editor opens " + ", ".join(AUDIO_EDIT_EXTS) + " files")
    return path, rel


def audio_push(folder, meta, file, label, seconds):
    """A new version after the current one; redo steps after it and the oldest beyond AUDIO_HISTORY are deleted."""
    for old in meta["versions"][meta["v"] + 1:]:
        try:
            os.remove(os.path.join(folder, old["file"]))
        except OSError:
            pass
    meta["versions"] = meta["versions"][:meta["v"] + 1] + [{"file": file, "label": label, "seconds": seconds}]
    if meta["saved_v"] > meta["v"]:
        meta["saved_v"] = -1                  # the saved version was a redo step that is now gone
    meta["v"] = len(meta["versions"]) - 1
    drop = len(meta["versions"]) - AUDIO_HISTORY
    if drop > 0:
        for old in meta["versions"][:drop]:
            try:
                os.remove(os.path.join(folder, old["file"]))
            except OSError:
                pass
        meta["versions"] = meta["versions"][drop:]
        meta["v"] -= drop
        meta["saved_v"] = meta["saved_v"] - drop if meta["saved_v"] >= drop else -1


@route("POST", "/api/audio/open")
def audio_open(req):
    """Starts editing a sound (or picks up the tab's earlier edit of it, when it is still there)."""
    ws = req.workspace()
    path, rel = audio_file(ws, req.field("path"))
    me = req.current_user["username"]
    old = req.field("id")
    if old:
        try:
            folder, meta = audio_session(req, ws, old)
            if meta["path"] == rel:
                audio_save_meta(folder, meta)
                return audio_state(folder, meta, me)
        except ApiError:
            pass
    if not os.path.isfile(path):
        raise ApiError(404, "no such file")
    if os.path.getsize(path) > AUDIO_MAX_BYTES:
        raise ApiError(400, f"the file is over {AUDIO_MAX_BYTES // 2 ** 20} MB")
    tool = audio_tool()
    audio_tidy(me)
    eid = secrets.token_hex(8)
    folder = os.path.join(audio_root(), eid)
    os.makedirs(folder)
    try:
        info = tool.probe(path)
        tool.decode(path, info["format"], os.path.join(folder, "v0.flac"), info["channels"])
        seconds = tool.length(os.path.join(folder, "v0.flac"))
    except tool.EditError as exc:
        shutil.rmtree(folder, ignore_errors=True)
        raise ApiError(400, str(exc)) from None
    meta = {"user": me, "ws": ws["id"], "path": rel, "rate": info["rate"], "channels": info["channels"], "src_mtime": os.path.getmtime(path),
            "versions": [{"file": "v0.flac", "label": "Opened", "seconds": seconds}], "v": 0, "saved_v": 0, "n": 0}
    audio_save_meta(folder, meta)
    return audio_state(folder, meta, me)


@route("GET", "/api/audio/mtime")
def audio_mtime(req):
    """When a sound last changed, so an open player or editor notices the agent's edits."""
    ws = req.workspace()
    path, _ = audio_file(ws, req.arg("path"))
    return {"mtime": os.path.getmtime(path) if os.path.isfile(path) else None}


@route("GET", "/api/audio/sound")
def audio_sound(req):
    """One version as FLAC, for the browser to decode, draw and play."""
    ws = req.workspace()
    folder, meta = audio_session(req, ws, req.arg("id"))
    name = req.arg("file") or ""
    if name not in {v["file"] for v in meta["versions"]}:
        raise ApiError(404, "no such version")
    with open(os.path.join(folder, name), "rb") as fh:
        data = fh.read()
    req.send_response(200)
    req.send_header("Content-Type", "audio/flac")
    req.send_header("Content-Length", str(len(data)))
    req.send_header("Cache-Control", "private, max-age=86400")        # a version never changes
    req.end_headers()
    req.wfile.write(data)


@route("POST", "/api/audio/edit")
def audio_edit_route(req):
    ws = req.workspace()
    me = req.current_user["username"]
    folder, meta = audio_session(req, ws, req.field("id"))
    op, params = req.field("op"), req.field("params") or {}
    if not isinstance(op, str) or not isinstance(params, dict):
        raise ApiError(400, "op must be a name and params an object")
    tool = audio_tool()
    with AUDIO_LOCKS[req.field("id")]:
        folder, meta = audio_session(req, ws, req.field("id"))
        cur = meta["versions"][meta["v"]]
        if op in ("copy", "cut"):
            a, b = params.get("a"), params.get("b")
            if not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in (a, b)) or abs(b - a) < 0.001:
                raise ApiError(400, "select some sound first")
            a, b = max(0.0, min(a, b)), min(max(a, b), cur["seconds"])
            clip = audio_clip_path(me)
            os.makedirs(os.path.dirname(clip), exist_ok=True)
            try:
                tool.extract(os.path.join(folder, cur["file"]), a, b, clip + ".tmp.flac")
            except tool.EditError as exc:
                raise ApiError(400, str(exc)) from None
            os.replace(clip + ".tmp.flac", clip)
            if op == "copy":
                audio_save_meta(folder, meta)
                return audio_state(folder, meta, me)
            op, params = "delete", {"a": a, "b": b}
        meta["n"] = meta.get("n", 0) + 1
        file = f"v{meta['n']}.flac"
        out = os.path.join(folder, file)
        try:
            label = tool.apply(op, params, os.path.join(folder, cur["file"]), out, cur["seconds"], meta["rate"], meta["channels"],
                               clip=audio_clip_path(me), work=folder)
            seconds = tool.length(out)
        except tool.EditError as exc:
            if os.path.exists(out):
                os.remove(out)
            raise ApiError(400, str(exc)) from None
        if req.field("op") == "cut":
            label = "Cut " + label.split(" ", 1)[1]
        audio_push(folder, meta, file, label, seconds)
        audio_save_meta(folder, meta)
        return audio_state(folder, meta, me)


@route("POST", "/api/audio/step")
def audio_step(req):
    """Undo (-1), redo (+1), or a jump to a version in the history (to)."""
    ws = req.workspace()
    with AUDIO_LOCKS[req.field("id")]:
        folder, meta = audio_session(req, ws, req.field("id"))
        to = req.field("to")
        if to is None:
            to = meta["v"] + (1 if req.field("redo") else -1)
        if not isinstance(to, int) or isinstance(to, bool) or not 0 <= to < len(meta["versions"]):
            raise ApiError(400, "nothing to undo or redo there")
        meta["v"] = to
        audio_save_meta(folder, meta)
        return audio_state(folder, meta, req.current_user["username"])


@route("POST", "/api/audio/save")
def audio_save(req):
    """Writes the current version into the project: over the file (the old one goes to the trash) or as a new file."""
    ws = req.workspace()
    with AUDIO_LOCKS[req.field("id")]:
        folder, meta = audio_session(req, ws, req.field("id"))
        target = req.field("path") or meta["path"]
        path, rel = audio_file(ws, target)
        same = rel == meta["path"]
        if same and os.path.exists(path) and abs(os.path.getmtime(path) - meta["src_mtime"]) > 1e-3 and not req.field("overwrite"):
            raise ApiError(409, f"{rel} changed on disk since it was opened")
        if not same and os.path.lexists(path) and not req.field("overwrite"):
            raise ApiError(409, f"{rel} already exists")
        if os.path.isdir(path) or os.path.islink(path):
            raise ApiError(400, f"{rel} is a folder or a link")
        tool = audio_tool()
        ext = os.path.splitext(path)[1].lower()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = os.path.join(os.path.dirname(path), f".{os.path.basename(path)}.studio-{secrets.token_hex(4)}{ext}")
        try:
            tool.export(os.path.join(folder, meta["versions"][meta["v"]]["file"]), tmp, ext)
        except tool.EditError as exc:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise ApiError(400, str(exc)) from None
        trash = move_to_trash(ws, path) if os.path.exists(path) else None
        os.replace(tmp, path)
        if same:
            meta["saved_v"], meta["src_mtime"] = meta["v"], os.path.getmtime(path)
        audio_save_meta(folder, meta)
        return {**audio_state(folder, meta, req.current_user["username"]), "saved": rel, "trash": trash, "mtime": os.path.getmtime(path)}


@route("POST", "/api/audio/slices")
def audio_slices(req):
    """Saves the parts between the cut marks as separate files beside the sound: name-01.ext, name-02.ext..."""
    ws = req.workspace()
    folder, meta = audio_session(req, ws, req.field("id"))
    cur = meta["versions"][meta["v"]]
    cuts = req.field("cuts")
    if not isinstance(cuts, list) or not cuts or len(cuts) > 200 or not all(isinstance(c, (int, float)) and not isinstance(c, bool) for c in cuts):
        raise ApiError(400, "put at least one cut mark first")
    edges = [0.0] + sorted({round(c, 4) for c in cuts if 0.01 < c < cur["seconds"] - 0.01}) + [cur["seconds"]]
    if len(edges) < 3:
        raise ApiError(400, "put at least one cut mark inside the sound")
    stem, ext = os.path.splitext(meta["path"])
    ext = req.field("ext") or ext.lower()
    tool = audio_tool()
    if ext not in tool.ENCODE:
        raise ApiError(400, "unknown file type")
    saved = []
    for i, (a, b) in enumerate(zip(edges, edges[1:]), 1):
        rel = free_rel(ws, f"{stem}-{i:02d}{ext}")
        path, rel = audio_file(ws, rel)
        try:
            tool.export(os.path.join(folder, cur["file"]), path, ext, a, b)
        except tool.EditError as exc:
            raise ApiError(400, str(exc)) from None
        saved.append(rel)
    return {"saved": saved}


@route("POST", "/api/video/cancel")
def video_cancel(req):
    with VIDEO_LOCK:
        job = VIDEO_JOBS.get(req.field("id"))
        if not job or job["owner"] != req.current_user["username"]:
            raise ApiError(404, "no such render")
        job["cancelled"] = True
        proc = job.get("proc")
    if proc and proc.poll() is None:
        proc.terminate()
    return {"ok": True}


@route("GET", "/api/workspace/recent")
def api_workspace_recent(req):
    return {"workspaces": APP.recent_workspaces(req.current_user)}


# ---------------------------------------------------------------------------
# Files API
# ---------------------------------------------------------------------------

@route("GET", "/api/files/tree")
def files_tree(req):
    ws = req.workspace()
    directory = resolve(ws, req.arg("path"))
    show_all = req.arg("all") == "1"
    if not os.path.isdir(directory):
        raise ApiError(404, "not a directory")
    entries = []
    for e in os.scandir(directory):
        is_dir = e.is_dir()
        if is_dir and not show_all and e.name in IGNORE_DIRS:
            continue
        try:
            size = 0 if is_dir else e.stat().st_size
        except OSError:
            size = 0
        entries.append({"name": e.name, "path": rel_posix(e.path, ws["path"]),
                        "type": "dir" if is_dir else "file", "size": size})
    entries.sort(key=lambda x: (x["type"] != "dir", x["name"].lower()))
    return {"entries": entries}


@route("GET", "/api/files/list")
def files_list(req):
    ws = req.workspace()
    files = []
    for _, rel in iter_files(ws["path"], ws["path"]):
        files.append(rel)
        if len(files) >= 20000:
            break
    return {"files": files}


@route("GET", "/api/files/read")
def files_read(req):
    ws = req.workspace()
    path = resolve(ws, req.arg("path"))
    if not os.path.isfile(path):
        raise ApiError(404, "file not found")
    text, mtime = read_text_file(path)
    if text is None:
        return {"path": req.arg("path"), "content": "", "mtime": mtime, "binary": True,
                "size": os.path.getsize(path)}
    return {"path": req.arg("path"), "content": text, "mtime": mtime, "binary": False}


@route("POST", "/api/files/write")
def files_write(req):
    ws = req.workspace()
    path = resolve(ws, req.field("path"))
    content = req.field("content")
    if not isinstance(content, str):
        raise ApiError(400, "content must be a string")
    if os.path.isdir(path):
        raise ApiError(400, "path is a directory")
    expected = req.field("expected_mtime")
    if expected is not None and os.path.exists(path):
        current = os.stat(path).st_mtime
        if not isinstance(expected, (int, float)) or abs(current - expected) > 1e-3:
            raise ApiError(409, "file changed on disk", mtime=current)
    write_bytes(path, content.encode("utf-8"))
    return {"mtime": os.stat(path).st_mtime}


@route("POST", "/api/files/create")
def files_create(req):
    ws = req.workspace()
    path = resolve(ws, req.field("path"))
    kind = req.field("type")
    if kind not in ("file", "dir"):
        raise ApiError(400, "type must be file or dir")
    if os.path.lexists(path):
        raise ApiError(409, "already exists")
    if kind == "dir":
        os.makedirs(path)
    else:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "x").close()
    return {"path": rel_posix(path, ws["path"])}


# ---- Uploads (2026-11-14): files and folders dropped on the explorer, or added to the chat, come in as raw bytes in
# parts of at most UPLOAD_PART, so large sounds and videos pass through tunnels that limit one request. The parts
# gather outside the folder (data/uploads/<user>/<id>) and move in once complete, so a half-sent file never shows up.
RAW_BODY_ROUTES = {"/api/files/upload"}
UPLOAD_PART = 16 * 1024 * 1024
UPLOAD_MAX = 4 * 1024 ** 3
UPLOAD_KEEP_S = 24 * 3600


def uploads_dir(user):
    return os.path.join(APP.data_dir, "uploads", re.sub(r"[^\w.-]", "_", user))


@route("POST", "/api/files/upload_check")
def files_upload_check(req):
    """Which of these paths are taken already, so the page can ask once: replace, or keep both."""
    ws = req.workspace()
    paths = req.field("paths")
    if not isinstance(paths, list) or len(paths) > 5000:
        raise ApiError(400, "paths must be a list")
    taken = []
    for rel in paths:
        full = resolve(ws, rel)
        if os.path.isdir(full):
            raise ApiError(409, f"{rel} is a folder")
        if os.path.lexists(full):
            taken.append(rel)
    return {"taken": taken}


@route("POST", "/api/files/upload")
def files_upload(req):
    """One part of a file: ?ws&path&id&offset&total, the part's bytes as the body; on the last part (offset + part =
    total) the file moves into the folder. conflict: replace (the old file goes to the trash) or keep (a free name)."""
    ws = APP.get_workspace(req.arg("ws"), req.current_user)
    rel = req.arg("path")
    dest = resolve(ws, rel)
    uid = req.arg("id")
    if not re.fullmatch(r"[a-f0-9]{8,32}", uid or ""):
        raise ApiError(400, "invalid upload id")
    offset, total = to_int(req.arg("offset"), -1), to_int(req.arg("total"), -1)
    length = to_int(req.headers.get("Content-Length"), -1)
    if total < 0 or total > UPLOAD_MAX:
        raise ApiError(413, f"files can be at most {UPLOAD_MAX // 1024 ** 3} GB")
    if length < 0 or length > UPLOAD_PART or offset < 0 or offset + length > total:
        req.close_connection = True
        raise ApiError(400, "invalid part")
    folder = uploads_dir(req.current_user["username"])
    os.makedirs(folder, exist_ok=True)
    if offset == 0:
        for name in os.listdir(folder):            # parts of uploads that never finished
            old = os.path.join(folder, name)
            if time.time() - os.path.getmtime(old) > UPLOAD_KEEP_S:
                os.remove(old)
    part = os.path.join(folder, uid)
    have = os.path.getsize(part) if os.path.exists(part) else 0
    if have != offset:
        req.close_connection = True
        raise ApiError(409, "parts out of order", have=have)
    with open(part, "ab") as fh:
        left = length
        while left:
            chunk = req.rfile.read(min(left, 1024 * 1024))
            if not chunk:
                raise ApiError(400, "the upload was cut off")
            fh.write(chunk)
            left -= len(chunk)
    if offset + length < total:
        return {"have": offset + length}
    if os.path.isdir(dest):
        os.remove(part)
        raise ApiError(409, f"{rel} is a folder")
    trashed = None
    if os.path.lexists(dest):
        if req.arg("conflict") == "replace":
            trashed = move_to_trash(ws, dest)
        else:
            dest = resolve(ws, free_rel(ws, rel))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.move(part, dest)
    sync_publish(req.current_user["username"], req.sync_origin(), "files", ws=ws["id"])
    return {"path": rel_posix(dest, ws["path"]), "size": total, "trashed": trashed}


@route("POST", "/api/files/rename")
def files_rename(req):
    ws = req.workspace()
    src = resolve(ws, req.field("from"), nofollow=True)
    dst = resolve(ws, req.field("to"), nofollow=True)
    if src == ws["path"] or dst == ws["path"]:
        raise ApiError(400, "cannot rename the workspace root")
    if not os.path.lexists(src):
        raise ApiError(404, "source not found")
    if os.path.lexists(dst):
        raise ApiError(409, "destination already exists")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    os.rename(src, dst)
    return {"path": rel_posix(dst, ws["path"])}


@route("POST", "/api/files/move")
def files_move(req):
    """Moves files and folders into another folder of the project (the explorer's drag and drop). A name already taken
    there is left alone and reported in `taken`, unless conflict is replace (the old one goes to the trash) or keep
    (a free name, " (2)")."""
    ws = req.workspace()
    paths, conflict = req.field("paths"), req.field("conflict") or ""
    if not isinstance(paths, list) or not paths or len(paths) > 5000 or not all(isinstance(x, str) for x in paths):
        raise ApiError(400, "paths must be a list")
    folder = resolve(ws, req.field("to") or "")
    if not os.path.isdir(folder):
        raise ApiError(404, "the target folder is not there")
    moved, taken, skipped = [], [], []
    for rel in paths:
        src = resolve(ws, rel, nofollow=True)
        if src == ws["path"] or not os.path.lexists(src):
            skipped.append({"path": rel, "why": "not found"})
            continue
        if os.path.dirname(src) == folder:
            continue                          # already there
        if os.path.isdir(src) and not os.path.islink(src) and within(folder, src):
            skipped.append({"path": rel, "why": "a folder cannot go inside itself"})
            continue
        dest = os.path.join(folder, os.path.basename(src))
        if os.path.lexists(dest):
            if conflict == "replace" and not (os.path.isdir(dest) and not os.path.islink(dest)):
                move_to_trash(ws, dest)
            elif conflict in ("keep", "replace"):
                dest = resolve(ws, free_rel(ws, rel_posix(dest, ws["path"])), nofollow=True)
            else:
                taken.append(rel)
                continue
        os.rename(src, dest)
        moved.append({"from": rel_posix(src, ws["path"]), "to": rel_posix(dest, ws["path"])})
    return {"moved": moved, "taken": taken, "skipped": skipped}


# ---- downloads to the user's own computer: one file as it is, a folder or several files as one zip, streamed.
ZIP_STORED_EXTS = {".zip", ".gz", ".tgz", ".xz", ".bz2", ".7z", ".rar", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif",
                   ".mp3", ".m4a", ".ogg", ".oga", ".opus", ".flac", ".aac", ".mp4", ".webm", ".mov", ".m4v", ".mkv",
                   ".glb", ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp", ".pdf", ".woff", ".woff2", ".jar"}
ZIP_TICKETS = {}                 # token -> {user, ws, paths, name, until}: a POST makes one, the browser's GET uses it
ZIP_TICKET_S = 600


def disposition(name):
    """An attachment header that keeps Greek and other non-ASCII names (RFC 6266), with an ASCII fallback."""
    plain = re.sub(r'[^A-Za-z0-9._ ()-]', "_", name).strip() or "download"
    return f"attachment; filename=\"{plain}\"; filename*=UTF-8''{urllib.parse.quote(name, safe='')}"


def zip_entries(ws, fulls):
    """(absolute file, name in the zip) for the chosen files and folders, named from their common parent. Folders
    are walked without the ignored ones inside (node_modules, .git, .venv...) and links leading out of the project."""
    base = os.path.commonpath([os.path.dirname(f) for f in fulls]) if fulls else ws["path"]
    seen = set()
    for full in fulls:
        if os.path.isfile(full):
            walk = [(os.path.dirname(full), [], [os.path.basename(full)])]
        else:
            walk = os.walk(full)
        for dirpath, dirnames, filenames in walk:
            dirnames[:] = sorted(d for d in dirnames if d not in IGNORE_DIRS and not os.path.islink(os.path.join(dirpath, d)))
            for name in sorted(filenames):
                f = os.path.join(dirpath, name)
                real = os.path.realpath(f)
                if f in seen or not within(real, ws["path"]) or not os.path.isfile(real):
                    continue
                seen.add(f)
                yield real, rel_posix(f, base)


def send_zip(req, ws, fulls, name):
    req.send_response(200)
    req.send_header("Content-Type", "application/zip")
    req.send_header("Content-Disposition", disposition(name))
    req.send_header("Cache-Control", "no-store")
    req.send_header("Connection", "close")       # no length is known ahead: the zip ends when the connection does
    req.close_connection = True
    req.end_headers()
    with zipfile.ZipFile(req.wfile, "w", allowZip64=True) as zf:
        for real, arc in zip_entries(ws, fulls):
            stored = os.path.splitext(arc)[1].lower() in ZIP_STORED_EXTS
            info = zipfile.ZipInfo.from_file(real, arc)
            info.compress_type = zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED
            try:
                with open(real, "rb") as src, zf.open(info, "w", force_zip64=info.file_size > 2 ** 31) as dst:
                    shutil.copyfileobj(src, dst, 1 << 20)
            except OSError:
                continue


def project_name(ws):
    return os.path.basename(ws["path"].rstrip("/")) or "project"


@route("GET", "/api/files/download")
def files_download(req):
    """?ws&path: a file as it is, a folder (or the whole project, path empty) as a zip."""
    ws = req.workspace()
    full = resolve(ws, req.arg("path"))
    if os.path.isdir(full):
        return send_zip(req, ws, [full], (project_name(ws) if full == ws["path"] else os.path.basename(full)) + ".zip")
    if not os.path.isfile(full):
        raise ApiError(404, "file not found")
    size = os.path.getsize(full)
    req.send_response(200)
    req.send_header("Content-Type", "application/octet-stream")
    req.send_header("Content-Disposition", disposition(os.path.basename(full)))
    req.send_header("Content-Length", str(size))
    req.send_header("Cache-Control", "no-store")
    req.end_headers()
    with open(full, "rb") as fh:
        shutil.copyfileobj(fh, req.wfile, 1 << 20)


# ---- Office documents in the editor (2026-11-17): Word, PowerPoint and the rest as PDF pages, made by LibreOffice inside
# bubblewrap (no network, no home folder: only the system's programs and fonts and one scratch folder), so a document from
# anyone cannot reach the LAN or read Studio's data while it is opened; spreadsheets also as sheets of cells.
OFFICE_VIEW = {".docx", ".docm", ".doc", ".odt", ".rtf", ".pptx", ".ppt", ".odp", ".xlsx", ".xlsm", ".xls", ".ods"}
SHEET_VIEW = {".xlsx", ".xlsm", ".xls", ".ods"}
DOC_PDF_KEEP = 40
SHEET_VIEW_ROWS = 5000
OFFICE_SLOTS = threading.Semaphore(2)


def office_jail(workdir):
    """The command prefix that runs a program in workdir (seen as /work) with no network and no home folder, or None."""
    bwrap = shutil.which("bwrap")
    if not bwrap:
        return None
    jail = [bwrap, "--ro-bind", "/usr", "/usr", "--ro-bind", "/etc", "/etc", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
            "--tmpfs", "/home", "--bind", workdir, "/work", "--chdir", "/work", "--setenv", "HOME", "/work",
            "--unshare-all", "--die-with-parent", "--new-session"]
    for name in ("lib", "lib64", "bin", "sbin"):
        if os.path.islink("/" + name):
            jail[1:1] = ["--symlink", os.readlink("/" + name), "/" + name]
        elif os.path.isdir("/" + name):
            jail[1:1] = ["--ro-bind", "/" + name, "/" + name]
    return jail


def office_convert(full, target):
    """The bytes of the file at full converted by LibreOffice to target ("pdf" or "xlsx")."""
    ext = os.path.splitext(full)[1].lower()
    if os.path.getsize(full) > MAX_DOC_BYTES:
        raise ApiError(413, f"documents larger than {MAX_DOC_BYTES // 1048576} MB are not shown; download it instead")
    office = shutil.which("soffice") or shutil.which("libreoffice")
    if not office:
        raise ApiError(400, "LibreOffice is not installed here (sudo apt install libreoffice), so this document cannot be shown")
    with tempfile.TemporaryDirectory(prefix="studio-office-") as tmp:
        jail = office_jail(tmp)
        if jail is None:
            raise ApiError(400, "bubblewrap is not installed here (sudo apt install bubblewrap), so documents are not opened")
        shutil.copyfile(full, os.path.join(tmp, "in" + ext))
        with OFFICE_SLOTS:
            try:
                subprocess.run(jail + [office, "-env:UserInstallation=file:///work/profile", "--headless", "--norestore", "--nolockcheck",
                                       "--convert-to", target, "--outdir", "/work", "/work/in" + ext],
                               capture_output=True, timeout=120, stdin=subprocess.DEVNULL)
            except subprocess.TimeoutExpired:
                raise ApiError(504, "LibreOffice took more than two minutes to open this document") from None
        out = os.path.join(tmp, "in." + target)
        if not os.path.isfile(out):
            raise ApiError(400, "LibreOffice could not open this document")
        with open(out, "rb") as fh:
            return fh.read()


def doc_pdf_cached(full):
    """The PDF of an Office document, from data/doc_pdf/ when the file has not changed since (newest DOC_PDF_KEEP kept)."""
    st = os.stat(full)
    key = hashlib.sha256(f"{os.path.realpath(full)}\0{st.st_size}\0{st.st_mtime_ns}".encode()).hexdigest()[:32]
    folder = os.path.join(APP.data_dir, "doc_pdf")
    path = os.path.join(folder, key + ".pdf")
    if os.path.isfile(path):
        os.utime(path)
        with open(path, "rb") as fh:
            return fh.read()
    data = office_convert(full, "pdf")
    os.makedirs(folder, exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)
    old = sorted((os.path.join(folder, n) for n in os.listdir(folder) if n.endswith(".pdf")), key=os.path.getmtime, reverse=True)
    for stale in old[DOC_PDF_KEEP:]:
        try:
            os.remove(stale)
        except OSError:
            pass
    return data


@route("GET", "/api/files/doc_pdf")
def files_doc_pdf(req):
    """?ws&path: a Word, PowerPoint, Excel or OpenDocument file as PDF pages, for the editor's document viewer."""
    ws = req.workspace()
    full = resolve(ws, req.arg("path"))
    if not os.path.isfile(full):
        raise ApiError(404, "file not found")
    if os.path.splitext(full)[1].lower() not in OFFICE_VIEW:
        raise ApiError(400, "Studio shows Word, PowerPoint, Excel and OpenDocument files this way")
    data = doc_pdf_cached(full)
    req.send_response(200)
    req.send_header("Content-Type", "application/pdf")
    req.send_header("Content-Length", str(len(data)))
    req.send_header("Cache-Control", "no-store")
    req.send_header("X-Content-Type-Options", "nosniff")
    req.end_headers()
    req.wfile.write(data)


@route("GET", "/api/files/sheets")
def files_sheets(req):
    """?ws&path: a workbook's sheets as cells: [{name, first_row, rows, total_rows, columns, formulas}], each with at most
    SHEET_VIEW_ROWS rows; .xls and .ods are turned into .xlsx by LibreOffice first."""
    import zipfile
    ws = req.workspace()
    full = resolve(ws, req.arg("path"))
    if not os.path.isfile(full):
        raise ApiError(404, "file not found")
    ext = os.path.splitext(full)[1].lower()
    if ext not in SHEET_VIEW:
        raise ApiError(400, "only workbooks have sheets")
    if os.path.getsize(full) > MAX_DOC_BYTES:
        raise ApiError(413, f"workbooks larger than {MAX_DOC_BYTES // 1048576} MB are not shown; download it instead")
    if ext in (".xls", ".ods"):
        data = office_convert(full, "xlsx")
    else:
        with open(full, "rb") as fh:
            data = fh.read()
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            grids = xlsx_grids(z)
    except zipfile.BadZipFile:
        raise ApiError(400, "this workbook cannot be read") from None
    sheets = []
    for name, grid, formulas in grids:
        rows = grid_rows(grid)
        sheets.append({"name": name, "first_row": min(grid) if grid else 1, "rows": rows[:SHEET_VIEW_ROWS], "total_rows": len(rows),
                       "columns": len(rows[0]) if rows else 0, "formulas": formulas})
    return {"sheets": sheets}


@route("POST", "/api/files/zip")
def files_zip(req):
    """Several files and folders as one zip: answers a ticket for GET /api/files/zip?token=, which the browser downloads."""
    ws = req.workspace()
    paths = req.field("paths")
    if not isinstance(paths, list) or not paths or len(paths) > 5000 or not all(isinstance(x, str) for x in paths):
        raise ApiError(400, "paths must be a list")
    fulls = []
    for rel in paths:
        full = resolve(ws, rel)
        if not os.path.exists(full):
            raise ApiError(404, f"{rel} is not there")
        if full not in fulls:
            fulls.append(full)
    now = time.time()
    for k in [k for k, v in ZIP_TICKETS.items() if v["until"] < now]:
        ZIP_TICKETS.pop(k, None)
    name = (os.path.basename(fulls[0]) if len(fulls) == 1 and fulls[0] != ws["path"] else project_name(ws) + (" files" if len(fulls) > 1 else "")) + ".zip"
    token = secrets.token_urlsafe(18)
    ZIP_TICKETS[token] = {"user": req.current_user["username"], "ws": ws["id"], "paths": paths, "name": name, "until": now + ZIP_TICKET_S}
    return {"token": token, "name": name, "url": "/api/files/zip?token=" + token}


@route("GET", "/api/files/zip")
def files_zip_get(req):
    t = ZIP_TICKETS.get(req.arg("token"))
    if not t or t["until"] < time.time() or t["user"] != req.current_user["username"]:
        raise ApiError(404, "this download has expired; choose the files again")
    ZIP_TICKETS.pop(req.arg("token"), None)
    ws = APP.get_workspace(t["ws"], req.current_user)
    send_zip(req, ws, [resolve(ws, rel) for rel in t["paths"]], t["name"])


@route("POST", "/api/files/delete")
def files_delete(req):
    ws = req.workspace()
    path = resolve(ws, req.field("path"), nofollow=True)
    if path == ws["path"]:
        raise ApiError(400, "cannot delete the workspace root")
    if not os.path.lexists(path):
        raise ApiError(404, "not found")
    return {"trash": move_to_trash(ws, path)}


@route("GET", "/api/files/names")
def files_names(req):
    """Folders and files whose name holds q (or whose path does, when q has a slash), for the Explorer's find bar.
    Skips the ignored folders (node_modules, .git...) as the tree's search does; at most 300, within 3 seconds."""
    ws = req.workspace()
    query = req.arg("q").strip()
    if not query:
        raise ApiError(400, "q required")
    case = req.arg("case") == "1"
    needle = query if case else query.lower()
    whole = "/" in query
    found, deadline = [], time.monotonic() + 3.0
    for dirpath, dirnames, filenames in os.walk(ws["path"]):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORE_DIRS)
        rel_dir = rel_posix(dirpath, ws["path"])
        for kind, names in (("dir", dirnames), ("file", sorted(filenames))):
            for name in names:
                rel = name if rel_dir == "." else rel_dir + "/" + name
                hay = rel if whole else name
                if needle in (hay if case else hay.lower()):
                    found.append({"path": rel, "type": kind})
                    if len(found) >= 300:
                        return {"names": found, "truncated": True}
        if time.monotonic() > deadline:
            return {"names": found, "truncated": True}
    return {"names": found, "truncated": False}


@route("GET", "/api/files/search")
def files_search(req):
    ws = req.workspace()
    query = req.arg("q")
    if not query:
        raise ApiError(400, "q required")
    matches, truncated = search_tree(ws["path"], ws["path"], query, req.arg("regex") == "1",
                                     req.arg("case") == "1", 500, 5.0)
    return {"matches": matches, "truncated": truncated}


# ---------------------------------------------------------------------------
# Terminals (pty shells streamed over SSE)
# ---------------------------------------------------------------------------

def sandbox_flags(memory="6g"):
    """Container restrictions, the same as the Ollama Manager arena's."""
    return ["--user", f"{os.getuid()}:{os.getgid()}", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--memory", memory, "--memory-swap", memory, "--cpus", str(max(1, min(6, (os.cpu_count() or 2) - 1))),
            "--pids-limit", "1024", "-e", "HOME=/tmp", "-e", "PYTHONDONTWRITEBYTECODE=1"]


def sandboxed(ws):
    """The folder's preference: agent commands and terminals run in a container, or on the host. A folder on a person's
    own computer runs its commands there instead (`helper`)."""
    if ws.get("helper"):
        return False
    return bool(APP.cfg.get("sandbox_required") or ws.get("owner") or ws.get("sandbox", APP.cfg.get("sandbox_default")))


def sandbox_settings(ws):
    """The folder's own image, network mode and published ports, or the server's defaults."""
    network = ws.get("sandbox_network") or APP.cfg.get("sandbox_network")
    return {"image": ws.get("sandbox_image") or APP.cfg.get("sandbox_image") or DEFAULT_CONFIG["sandbox_image"],
            "network": network if network in SANDBOX_NETWORKS else "off", "ports": ws.get("sandbox_ports") or []}


def sandbox_name(ws):
    return f"strata-sandbox-{ws['id']}"


def ensure_sandbox_network():
    if docker("network", "inspect", SANDBOX_BRIDGE_NETWORK).returncode == 0:
        return
    result = docker("network", "create", "--driver", "bridge",
                    "-o", f"com.docker.network.bridge.name={SANDBOX_BRIDGE_INTERFACE}",
                    "-o", "com.docker.network.bridge.enable_icc=false", SANDBOX_BRIDGE_NETWORK)
    if result.returncode:
        raise ApiError(503, f"could not create the sandbox network: {result.stderr.strip()[:300]}")


def build_universal_image():
    if docker("image", "inspect", DEFAULT_CONFIG["sandbox_image"]).returncode == 0:
        return
    folder = os.path.join(SCRIPT_DIR, "sandbox", "images")
    result = docker("build", "-t", DEFAULT_CONFIG["sandbox_image"], "-f", os.path.join(folder, "universal.Dockerfile"),
                    "--build-arg", f"SANDBOX_UID={os.getuid()}", "--build-arg", f"SANDBOX_GID={os.getgid()}", folder, timeout=1800)
    if result.returncode:
        raise ApiError(503, f"could not build the sandbox image: {result.stderr.strip()[-300:]}")


def docker(*args, timeout=120):
    try:
        return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ApiError(503, f"the sandbox needs Docker, which did not respond: {exc}") from None


SANDBOX_VERIFIED = {}           # container name -> the folder identities it was checked against
STALE_MOUNT = "outside of container mount namespace root"


def sandbox_mounts_current(name, ws, home):
    """False when the container still sees a folder that was since moved or replaced at the same path.
    Docker fixes a bind mount when the container is created, so such a container runs commands in the old
    folder while Studio's file tools change the new one. Checked again whenever the folders on disk change."""
    try:
        want = f"{os.stat(ws['path']).st_ino} {os.stat(home).st_ino}"
    except OSError:
        return True
    if SANDBOX_VERIFIED.get(name) == want:
        return True
    # From /, since a folder deleted and made again leaves /work unusable as a working directory: Docker then
    # refuses every exec there ("outside of container mount namespace root"), which hid this from the check.
    seen = docker("exec", "-w", "/", name, "stat", "-c", "%i", "/work", "/home/sandbox")
    if seen.returncode:
        return STALE_MOUNT not in seen.stderr + seen.stdout     # otherwise cannot tell; never throw a container away on a guess
    if " ".join(seen.stdout.split()) != want:
        return False
    SANDBOX_VERIFIED[name] = want
    return True


def ensure_sandbox(ws):
    """Start the folder's container, creating it on first use. Only the folder is mounted, at /work, plus
    a private home that keeps downloaded packages and tool caches between containers."""
    name = sandbox_name(ws)
    settings = sandbox_settings(ws)
    home = os.path.join(APP.data_dir, "sandbox_homes", ws["id"])
    os.makedirs(home, exist_ok=True)
    with SANDBOX_LOCK:
        state = docker("inspect", "-f", "{{.State.Running}}", name)
        if state.returncode == 0:
            if state.stdout.strip() != "true":
                result = docker("start", name)
                if result.returncode:
                    raise ApiError(503, f"could not start the sandbox: {result.stderr.strip()[:300]}")
            if sandbox_mounts_current(name, ws, home):
                return name
            print(f"sandbox {name}: its folder was replaced since it was created, so it is created again", flush=True)
            docker("rm", "-f", name)
            SANDBOX_VERIFIED.pop(name, None)
        if settings["image"] == DEFAULT_CONFIG["sandbox_image"]:
            build_universal_image()
        ports = settings["ports"] if settings["network"] == "on" else []
        if settings["network"] != "off":
            ensure_sandbox_network()
        result = docker("run", "-d", "--name", name, "--hostname", "strata-sandbox",
                        *sandbox_flags(APP.cfg.get("sandbox_memory") or DEFAULT_CONFIG["sandbox_memory"]),
                        "--network", "none" if settings["network"] == "off" else SANDBOX_BRIDGE_NETWORK,
                        *[arg for port in ports for arg in ("-p", f"127.0.0.1:{port}:{port}")],
                        "--tmpfs", "/tmp:exec,size=4g", "-v", f"{ws['path']}:/work", "-v", f"{home}:/home/sandbox",
                        "-e", "HOME=/home/sandbox", "-w", "/work", "--entrypoint", "sleep",
                        settings["image"], "infinity", timeout=600)
        if result.returncode == 0 and settings["network"] == "install":
            docker("network", "disconnect", SANDBOX_BRIDGE_NETWORK, name)
    if result.returncode:
        raise ApiError(503, f"could not start the sandbox: {result.stderr.strip()[:300]}")
    return name


def remove_sandbox(ws):
    with SANDBOX_LOCK:
        docker("rm", "-f", sandbox_name(ws))
        SANDBOX_VERIFIED.pop(sandbox_name(ws), None)


def sandbox_exec(ws, argv, tty=False, env=()):
    """Wrap argv to run in the folder's sandbox container, or return it unchanged to run on the host.
    env names variables of Studio's own environment to pass into the container."""
    if not sandboxed(ws):
        return argv
    flags = ["-it", "-e", "TERM=xterm-256color"] if tty else []
    for name in env:
        flags += ["-e", name]
    return ["docker", "exec", *flags, "-w", "/work", ensure_sandbox(ws), *argv]


class Terminal:
    def __init__(self, ws, cols, rows):
        master, slave = pty.openpty()
        self.set_size_fd(master, cols, rows)
        shell = os.environ.get("SHELL")
        if not shell or not os.path.exists(shell) or sandboxed(ws):
            shell = "/bin/bash"
        env = dict(os.environ, TERM="xterm-256color", COLORTERM="truecolor")
        try:
            argv = sandbox_exec(ws, [shell, "-l"], tty=True)
            self.proc = subprocess.Popen(
                argv, stdin=slave, stdout=slave, stderr=slave, cwd=ws["path"], env=env,
                start_new_session=True,
                preexec_fn=lambda: fcntl.ioctl(0, termios.TIOCSCTTY, 1))
        finally:
            os.close(slave)
        self.fd = master
        self.cond = threading.Condition()
        self.buf = bytearray()
        self.total = 0
        self.exit_code = None
        self.clients = 0
        self.last_seen = time.time()
        threading.Thread(target=self._pump, daemon=True).start()

    @staticmethod
    def set_size_fd(fd, cols, rows):
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    def _pump(self):
        while True:
            try:
                data = os.read(self.fd, 65536)
            except OSError:
                break
            if not data:
                break
            with self.cond:
                self.buf += data
                self.total += len(data)
                if len(self.buf) > TERM_BUFFER:
                    del self.buf[:len(self.buf) - TERM_BUFFER]
                self.cond.notify_all()
        code = self.proc.wait()
        os.close(self.fd)
        with self.cond:
            self.exit_code = code
            self.cond.notify_all()

    def write(self, data):
        self.last_seen = time.time()
        if self.exit_code is None:
            try:
                os.write(self.fd, data)
            except OSError:
                pass

    def resize(self, cols, rows):
        self.last_seen = time.time()
        if self.exit_code is None:
            try:
                self.set_size_fd(self.fd, cols, rows)
            except OSError:
                pass

    def kill(self):
        try:
            os.killpg(self.proc.pid, signal.SIGHUP)
        except OSError:
            return
        threading.Timer(2.0, self._force_kill).start()

    def _force_kill(self):
        if self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except OSError:
                pass

    def stream(self, req):
        """Replay the scrollback, then follow new output until the shell exits."""
        with self.cond:
            sent = self.total - len(self.buf)
            self.clients += 1
        try:
            while True:
                with self.cond:
                    if self.total == sent and self.exit_code is None:
                        self.cond.wait(SSE_PING_S)
                    new = self.total - sent
                    data = bytes(self.buf[-min(new, len(self.buf)):]) if new else b""
                    sent = self.total
                    code = self.exit_code
                if data:
                    req.sse_event("data", {"b64": base64.b64encode(data).decode()})
                elif code is not None:
                    req.sse_event("exit", {"code": code})
                    return
                else:
                    req.sse_ping()
        finally:
            with self.cond:
                self.clients -= 1
            self.last_seen = time.time()

    def poll(self, sent):
        """Like stream, for one request: what came after `sent`, or the scrollback on the first request."""
        with self.cond:
            if sent is None:
                sent = self.total - len(self.buf)
            if self.total <= sent and self.exit_code is None:
                self.cond.wait(POLL_WAIT_S)
            new = max(0, self.total - sent)
            data = bytes(self.buf[-min(new, len(self.buf)):]) if new else b""
            total, code = self.total, self.exit_code
            self.last_seen = time.time()
        cursor = {"sent": total}
        if data:
            return {"events": [{"type": "data", "data": {"b64": base64.b64encode(data).decode()}}], "cursor": cursor}
        if code is not None:
            return {"events": [{"type": "exit", "data": {"code": code}}], "cursor": cursor, "end": True}
        return {"events": [], "cursor": cursor}


def get_terminal(req, tid):
    term = APP.terminals.get(tid) if isinstance(tid, str) else None
    if not term or term.owner != req.current_user["username"]:
        raise ApiError(404, "unknown terminal")
    return term


def terminal_janitor():
    while True:
        time.sleep(60)
        now = time.time()
        with APP.lock:
            stale = [tid for tid, t in APP.terminals.items()
                     if t.clients == 0 and now - t.last_seen > TERM_IDLE_S]
            victims = [APP.terminals.pop(tid) for tid in stale]
        for term in victims:
            term.kill()


@route("POST", "/api/term/open")
def term_open(req):
    ws = req.workspace()
    size = to_int(req.field("cols"), 80, 1, 1000), to_int(req.field("rows"), 24, 1, 1000)
    term = HelperTerminal(ws, *size) if ws.get("helper") else Terminal(ws, *size)
    term.owner = req.current_user["username"]
    term.ws_id = ws["id"]
    tid = secrets.token_hex(8)
    with APP.lock:
        APP.term_seq += 1
        term.number = APP.term_seq
        APP.terminals[tid] = term
    sync_publish(term.owner, req.sync_origin(), "terminals", ws=ws["id"])
    return {"id": tid, "n": term.number}


@route("GET", "/api/term/list")
def term_list(req):
    """This user's live terminals in a folder, so that every window can show them."""
    ws, username = req.workspace(), req.current_user["username"]
    with APP.lock:
        items = [{"id": tid, "n": t.number} for tid, t in APP.terminals.items() if t.owner == username and t.ws_id == ws["id"] and t.exit_code is None]
    return {"terminals": sorted(items, key=lambda item: item["n"])}


@route("GET", "/api/term/stream")
def term_stream(req):
    term = get_terminal(req, req.arg("id"))
    req.sse_start()
    term.stream(req)


@route("GET", "/api/term/poll")
def term_poll(req):
    sent = req.arg("sent")
    return get_terminal(req, req.arg("id")).poll(to_int(sent, 0, lo=0) if sent else None)


@route("POST", "/api/term/input")
def term_input(req):
    data = req.field("data")
    if not isinstance(data, str):
        raise ApiError(400, "data must be a string")
    get_terminal(req, req.field("id")).write(data.encode("utf-8"))
    return {"ok": True}


@route("POST", "/api/term/resize")
def term_resize(req):
    get_terminal(req, req.field("id")).resize(to_int(req.field("cols"), 80, 1, 1000), to_int(req.field("rows"), 24, 1, 1000))
    return {"ok": True}


@route("POST", "/api/term/close")
def term_close(req):
    tid = req.field("id")
    get_terminal(req, tid)
    with APP.lock:
        term = APP.terminals.pop(tid)
    term.kill()
    sync_publish(term.owner, req.sync_origin(), "terminals", ws=term.ws_id)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Agent: sessions and checkpoints
# ---------------------------------------------------------------------------

def session_path(sid):
    if not isinstance(sid, str) or not ID_RE.match(sid):
        raise ApiError(404, "unknown session")
    return os.path.join(APP.data_dir, "sessions", sid + ".json")


def load_session(req, sid):
    session = read_json(session_path(sid), None)
    # Sessions from before accounts existed belong to the migrated admin.
    if session is None:
        raise ApiError(404, "unknown session")
    if session.get("owner", "admin") != req.current_user["username"]:
        # A chat its owner shared with the project: everyone with the folder may read it and carry it on.
        ws = APP.workspaces.get(session.get("ws") or "")
        if not (session.get("shared") and ws and req.current_user["username"] in folder_people(ws)):
            raise ApiError(404, "unknown session")
    return session


def save_session(session):
    session["updated"] = time.time()
    write_json_atomic(session_path(session["id"]), session)


def checkpoint_path(ws, cid):
    if not isinstance(cid, str) or not ID_RE.match(cid):
        raise ApiError(404, "unknown checkpoint")
    return os.path.join(APP.data_dir, "checkpoints", ws["id"], cid + ".json")


def make_checkpoint(ws, previous):
    """previous maps relative path -> old text (None if the file did not exist)."""
    cid = secrets.token_hex(6)
    files = {p: {"existed": old is not None, "content": old} for p, old in previous.items()}
    write_json_atomic(checkpoint_path(ws, cid), {"id": cid, "created": time.time(), "files": files})
    return cid


# ---------------------------------------------------------------------------
# Shared projects (2026-11-17, the user's request): a folder its owner shares with other people, who work on the same files
# (no copy), in the same sandbox, with rights per person. Rights are switches: files (change files), agent (run the agent),
# terminal, delete (delete files), share (manage people: with every switch on, a co-owner). Reading is always allowed. A PC
# folder can be shared too: then the others' commands run on its owner's computer. Chats stay private unless their owner
# shares one with the folder.
# ---------------------------------------------------------------------------

SHARE_RIGHTS = ("files", "agent", "terminal", "delete", "share")
SHARE_WORDS = {"files": "change files", "agent": "run the agent", "terminal": "use the terminal", "delete": "delete files",
               "share": "manage who has this folder"}


def folder_people(ws):
    """Everyone with this folder: its owner (or the admin who shares an admin folder) and its members."""
    people = set(ws.get("members") or {})
    for who in (ws.get("owner"), ws.get("manager")):
        if who:
            people.add(who)
    return people


def folder_rights(ws, user):
    """The rights this person has in the folder: everything for its owner and admins, the switches for members."""
    if user["role"] == "admin" or ws.get("owner") == user["username"] or (not ws.get("owner") and ws.get("manager") == user["username"]):
        return {r: True for r in SHARE_RIGHTS}
    member = (ws.get("members") or {}).get(user["username"])
    return {r: bool(member and member.get(r)) for r in SHARE_RIGHTS}


def share_need(method, path):
    """The right a request needs in a shared folder, or None. Reading never needs one; anything else that changes the
    folder needs files, apart from these."""
    if method != "POST" or not path.startswith("/api/"):
        return None
    if path.startswith("/api/term/"):
        return "terminal"
    if path in ("/api/agent/send", "/api/agent/compact", "/api/timeskip/start", "/api/command/run", "/api/agent/goal") or path.startswith("/api/tasks"):
        return "agent"
    if path in ("/api/files/delete", "/api/folders/delete_copy"):
        return "delete"
    if path in ("/api/share/set", "/api/workspace/sandbox"):
        return "share"
    if path.startswith("/api/agent/") and path != "/api/agent/undo":
        return None                   # chats, decisions, stopping one's own run
    if path in ("/api/workspace/open", "/api/preview/token", "/api/files/zip", "/api/live/token", "/api/share/remove"):
        return None
    return "files"


def check_right(ws, user, need):
    rights = folder_rights(ws, user)
    if not rights.get(need):
        owner = ws.get("owner") or ws.get("manager") or "its owner"
        raise ApiError(403, f"{owner} has not let you {SHARE_WORDS[need]} in this folder")
    if need in ("agent", "terminal") and user["role"] != "admin" and not ws.get("helper") and not sandboxed(ws):
        raise ApiError(403, "this folder runs commands on the server itself (its Sandbox is off), so only admins may "
                            "run the agent or a terminal in it; ask the owner to turn the Sandbox on")


def share_public(ws, viewer):
    members = []
    for name, m in sorted((ws.get("members") or {}).items()):
        u = APP.auth.users.get(name) or {}
        members.append({"username": name, "display_name": u.get("display_name") or "", "rights": {r: bool(m.get(r)) for r in SHARE_RIGHTS},
                        "added": m.get("added"), "gone": not u or bool(u.get("disabled"))})
    owner = ws.get("owner") or ws.get("manager")
    return {"owner": owner, "owner_name": (APP.auth.users.get(owner or "") or {}).get("display_name") or owner, "members": members,
            "rights": folder_rights(ws, viewer), "place": folder_place(ws), "sandbox": sandboxed(ws), "me": viewer["username"]}


@route("GET", "/api/share")
def share_get(req):
    return share_public(req.workspace(), req.current_user)


@route("GET", "/api/share/people")
def share_people(req):
    """The people a folder can be shared with: everyone with an account here but you."""
    me = req.current_user["username"]
    return {"people": [{"username": u["username"], "display_name": u.get("display_name") or ""}
                       for u in APP.auth.users.values() if u["username"] != me and not u.get("disabled")]}


@route("POST", "/api/share/set")
def share_set(req):
    """Adds a person to the folder, or changes their rights (rights: {files, agent, terminal, delete, share})."""
    ws = req.workspace()
    name, rights = req.field("username"), req.field("rights")
    user = APP.auth.users.get(name) if isinstance(name, str) else None
    if not user or user.get("disabled"):
        raise ApiError(404, "no such person here")
    if name == (ws.get("owner") or ws.get("manager")) or (not ws.get("owner") and not ws.get("manager") and name == req.current_user["username"]):
        raise ApiError(400, "that person owns this folder")
    if not isinstance(rights, dict) or not all(k in SHARE_RIGHTS and isinstance(v, bool) for k, v in rights.items()):
        raise ApiError(400, "rights are switches: " + ", ".join(SHARE_RIGHTS))
    with APP.lock:
        record = APP.workspaces[ws["id"]]
        if not record.get("owner") and not record.get("manager"):
            record["manager"] = req.current_user["username"]        # an admin's folder: shared in this admin's name
        members = record.setdefault("members", {})
        old = members.get(name) or {"added": time.time(), "by": req.current_user["username"]}
        members[name] = {**old, **{r: bool(rights.get(r, old.get(r, False))) for r in SHARE_RIGHTS}}
        write_json_atomic(APP.workspaces_path, APP.workspaces)
    for who in folder_people(record):
        sync_publish(who, req.sync_origin() if who == req.current_user["username"] else "", "workspace", ws=ws["id"])
    return share_public(record, req.current_user)


@route("POST", "/api/share/remove")
def share_remove(req):
    """Takes a person out of the folder; anyone may leave a folder shared with them."""
    ws = req.workspace()
    name = req.field("username")
    if name != req.current_user["username"]:
        check_right(ws, req.current_user, "share")
    with APP.lock:
        record = APP.workspaces[ws["id"]]
        if name not in (record.get("members") or {}):
            raise ApiError(404, "that person does not have this folder")
        del record["members"][name]
        if not record["members"]:
            record.pop("members")
        write_json_atomic(APP.workspaces_path, APP.workspaces)
    with APP.lock:                     # their terminals in it close
        gone = [tid for tid, t in APP.terminals.items() if getattr(t, "ws_id", None) == ws["id"] and getattr(t, "owner", None) == name]
        closing = [APP.terminals.pop(tid) for tid in gone]
    for term in closing:
        term.kill()
    for who in folder_people(record) | {name}:
        sync_publish(who, "", "workspace", ws=ws["id"])
    return share_public(record, req.current_user) if name != req.current_user["username"] else {"left": True}


@route("GET", "/api/workspace/shared")
def workspace_shared(req):
    """Folders other people shared with you, and those you share with others."""
    me = req.current_user["username"]
    mine, theirs = [], []
    for w in list(APP.workspaces.values()):
        if not os.path.isdir(w.get("path") or ""):
            continue
        if me in (w.get("members") or {}):
            theirs.append({**APP.public_ws(w), "rights": folder_rights(w, req.current_user)})
        elif w.get("members") and (w.get("owner") == me or (not w.get("owner") and w.get("manager") == me)):
            mine.append({**APP.public_ws(w), "with": sorted(w["members"])})
    return {"with_me": sorted(theirs, key=lambda w: w["name"].lower()), "by_me": sorted(mine, key=lambda w: w["name"].lower())}


@route("POST", "/api/agent/session/share")
def agent_session_share(req):
    """Shares a chat with everyone who has its folder, or makes it private again. Only its owner can."""
    ws = req.workspace()
    session = read_json(session_path(req.field("id")), None)
    if not session or session.get("owner", "admin") != req.current_user["username"] or session.get("ws") != ws["id"]:
        raise ApiError(404, "unknown session")
    if session_busy(session["id"]):
        raise ApiError(409, "the agent is working in this chat; share it when the run ends")
    session["shared"] = req.field("shared") is True
    save_session(session)
    for who in folder_people(ws) | {req.current_user["username"]}:
        sync_publish(who, "", "session_list", ws=ws["id"])
    return {"shared": session["shared"]}


@route("POST", "/api/agent/sessions")
def agent_session_create(req):
    ws = req.workspace()
    sid = secrets.token_hex(6)
    save_session({"id": sid, "ws": ws["id"], "owner": req.current_user["username"], "title": "",
                  "model": APP.cfg["default_model"], "events": [], "messages": []})
    return {"id": sid}


@route("GET", "/api/agent/sessions")
def agent_session_list(req):
    ws = req.workspace()
    out = []
    folder = os.path.join(APP.data_dir, "sessions")
    for name in os.listdir(folder):
        if not name.endswith(".json"):
            continue
        s = read_json(os.path.join(folder, name), None)
        mine = s and s.get("owner", "admin") == req.current_user["username"]
        if s and s.get("ws") == ws["id"] and (mine or s.get("shared")):
            out.append({"id": s["id"], "title": s.get("title") or "New chat", "updated": s.get("updated", 0), "model": s.get("model"),
                        "shared": bool(s.get("shared")), **({} if mine else {"by": s.get("owner", "admin")})})
    out.sort(key=lambda s: s["updated"], reverse=True)
    return {"sessions": out}


@route("GET", "/api/agent/session")
def agent_session_get(req):
    s = load_session(req, req.arg("id"))
    return {"id": s["id"], "events": s["events"], "model": s.get("model"), "ws": s.get("ws"), "goal": goal_public(s.get("goal")), "effort": s.get("effort", "")}


@route("POST", "/api/agent/session/delete")
def agent_session_delete(req):
    sid = req.field("id")
    load_session(req, sid)
    with APP.lock:
        if any(r.session["id"] == sid for r in APP.active.values()):
            raise ApiError(409, "a run is active in this session")
    os.remove(session_path(sid))
    shutil.rmtree(attachments_dir(sid), ignore_errors=True)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Attachments and Office documents: reading .docx .xlsx .pptx and OpenDocument, writing .docx and .xlsx
# ---------------------------------------------------------------------------

# Pictures and text files the user adds to a message. Pictures are kept beside the chat, not inside it, and go to
# the model as image parts; text files go in as text, like @file.
MAX_ATTACHMENTS = 8
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_ATTACHED_TEXT = 200_000
IMAGE_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}
DATA_URL_RE = re.compile(r"data:(image/[a-z]+);base64,([A-Za-z0-9+/=\s]+)")


def attachments_dir(sid):
    return os.path.join(APP.data_dir, "attachments", sid)


# ---- Office documents as text, for the model: Word, Excel, PowerPoint and OpenDocument files are zipped XML, read here
# with the standard library; old binary formats (.doc, .xls, .ppt, .rtf) are first turned into the new ones by LibreOffice
# (admins only, since it runs on this machine), and PDFs go through pdftotext. Used for attachments, @mentions and read_file.
DOC_EXTS = {".docx": "Word", ".docm": "Word", ".xlsx": "Excel", ".xlsm": "Excel", ".pptx": "PowerPoint", ".odt": "Writer",
            ".ods": "Calc", ".odp": "Impress", ".doc": "Word 97", ".xls": "Excel 97", ".ppt": "PowerPoint 97", ".rtf": "RTF", ".pdf": "PDF"}
DOC_LEGACY = {".doc": "docx", ".xls": "xlsx", ".ppt": "pptx", ".rtf": "docx"}
MAX_DOC_BYTES = 25 * 1024 * 1024
MAX_DOC_PART = 80 * 1024 * 1024        # one unzipped part; more is a zip bomb, not a document
MAX_DOC_TEXT = 400_000
MAX_SHEET_ROWS = 3000
W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
S_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
A_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def is_document(name):
    return os.path.splitext(name or "")[1].lower() in DOC_EXTS


def doc_xml(z, member):
    """One XML part of a document, refusing doctype tricks and parts that unzip to something huge."""
    import xml.etree.ElementTree as ET
    try:
        info = z.getinfo(member)
    except KeyError:
        return None
    if info.file_size > MAX_DOC_PART:
        raise ApiError(413, f"a part of this document is too large to read ({info.file_size // 1048576} MB)")
    data = z.read(member)
    if b"<!DOCTYPE" in data[:4096] or b"<!ENTITY" in data:
        raise ApiError(400, "this document contains XML that Studio does not read")
    try:
        return ET.fromstring(data)
    except ET.ParseError as e:
        raise ApiError(400, f"this document is damaged ({member}: {e})")


def cell_text(value):
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def md_table(rows):
    width = max((len(r) for r in rows), default=0)
    if not width:
        return ""
    rows = [list(r) + [""] * (width - len(r)) for r in rows]
    out = ["| " + " | ".join(cell_text(c) for c in rows[0]) + " |", "|" + " --- |" * width]
    out += ["| " + " | ".join(cell_text(c) for c in r) + " |" for r in rows[1:]]
    return "\n".join(out)


def docx_text(z):
    root = doc_xml(z, "word/document.xml")
    if root is None:
        raise ApiError(400, "this is not a Word document (no word/document.xml)")
    out = []

    def para(p):
        bits = []
        for node in p.iter():
            if node.tag == W_NS + "t":
                bits.append(node.text or "")
            elif node.tag == W_NS + "tab":
                bits.append("\t")
            elif node.tag in (W_NS + "br", W_NS + "cr"):
                bits.append("\n")
        text = "".join(bits).strip()
        style = p.find(f"{W_NS}pPr/{W_NS}pStyle")
        level = style.get(W_NS + "val", "") if style is not None else ""
        m = re.match(r"(?i)(heading|title)\s*(\d*)", level)
        if text and m:
            return "#" * (int(m.group(2) or 1) if m.group(1).lower() == "heading" else 1) + " " + text
        if text and p.find(f"{W_NS}pPr/{W_NS}numPr") is not None:
            return "- " + text
        return text

    body = root.find(W_NS + "body")
    for block in (body if body is not None else []):
        if block.tag == W_NS + "p":
            out.append(para(block))
        elif block.tag == W_NS + "tbl":
            rows = [[" ".join(filter(None, (para(p) for p in tc.iter(W_NS + "p")))) for tc in tr.findall(W_NS + "tc")] for tr in block.findall(W_NS + "tr")]
            out.append(md_table(rows))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def col_index(ref):
    letters = re.match(r"[A-Z]+", ref or "A").group(0)
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n - 1


def xlsx_grids(z):
    """Each sheet of a workbook as (name, {row: {column: value}}, formula count)."""
    book = doc_xml(z, "xl/workbook.xml")
    if book is None:
        raise ApiError(400, "this is not an Excel workbook (no xl/workbook.xml)")
    rels = doc_xml(z, "xl/_rels/workbook.xml.rels")
    targets = {r.get("Id"): r.get("Target") for r in (rels if rels is not None else [])}
    shared_root = doc_xml(z, "xl/sharedStrings.xml")
    shared = ["".join(t.text or "" for t in si.iter(S_NS + "t")) for si in (shared_root if shared_root is not None else [])]
    out = []
    sheets = book.find(S_NS + "sheets")
    for sheet in (sheets if sheets is not None else []):
        name, rid = sheet.get("name"), sheet.get(R_NS + "id")
        target = (targets.get(rid) or "").lstrip("/")
        member = target if target.startswith("xl/") else "xl/" + target
        root = doc_xml(z, member)
        if root is None:
            continue
        grid, formulas = {}, 0
        data = root.find(S_NS + "sheetData")
        for row in (data if data is not None else []):
            r = int(row.get("r") or len(grid) + 1)
            for c in row.findall(S_NS + "c"):
                kind, v = c.get("t"), c.find(S_NS + "v")
                if c.find(S_NS + "f") is not None:
                    formulas += 1
                if kind == "s" and v is not None and (v.text or "").isdigit() and int(v.text) < len(shared):
                    value = shared[int(v.text)]
                elif kind == "inlineStr":
                    value = "".join(t.text or "" for t in c.iter(S_NS + "t"))
                elif kind == "b" and v is not None:
                    value = "TRUE" if v.text == "1" else "FALSE"
                else:
                    value = v.text if v is not None and v.text is not None else ""
                    f = c.find(S_NS + "f")
                    if value == "" and f is not None and f.text:
                        value = "=" + f.text          # a formula never calculated, as from write_document
                    if re.fullmatch(r"-?\d+\.\d{10,}", value):
                        value = repr(round(float(value), 10))
                if value != "":
                    grid.setdefault(r, {})[col_index(c.get("r"))] = value
        out.append((name, grid, formulas))
    return out


def grid_rows(grid):
    """A sheet's grid as rows of cells, from its first row with data to its last."""
    if not grid:
        return []
    cols_n = max(max(r) for r in grid.values()) + 1
    return [[grid.get(r, {}).get(c, "") for c in range(cols_n)] for r in range(min(grid), max(grid) + 1)]


def xlsx_text(z):
    out = []
    for name, grid, formulas in xlsx_grids(z):
        if not grid:
            out.append(f"## Sheet: {name} (empty)")
            continue
        rows_n, cols_n = max(grid), max(max(r) for r in grid.values()) + 1
        first = min(grid)
        shown = [r for r in range(first, rows_n + 1)][:MAX_SHEET_ROWS]
        table = md_table([[grid.get(r, {}).get(c, "") for c in range(cols_n)] for r in shown])
        note = f"{len(grid)} rows with data, {cols_n} columns" + (f", {formulas} formulas (values shown)" if formulas else "")
        more = f"\n[{rows_n - shown[-1]} more rows not shown; read the file with a script for all of them]" if shown[-1] < rows_n else ""
        out.append(f"## Sheet: {name} ({note})\n{table}{more}")
    return "\n\n".join(out)


def pptx_text(z):
    slides = sorted((n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)), key=lambda n: int(re.search(r"\d+", n.rsplit("/", 1)[1]).group()))
    out = []
    for i, member in enumerate(slides, 1):
        root = doc_xml(z, member)
        paras = ["".join(t.text or "" for t in p.iter(A_NS + "t")).strip() for p in root.iter(A_NS + "p")]
        out.append(f"## Slide {i}\n" + "\n".join(p for p in paras if p))
    return "\n\n".join(out)


def odf_text(z, ext):
    root = doc_xml(z, "content.xml")
    if root is None:
        raise ApiError(400, "this is not an OpenDocument file (no content.xml)")
    T = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
    TB = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"

    def text_of(node):
        bits = [node.text or ""]
        for child in node:
            if child.tag == T + "s":
                bits.append(" " * int(child.get(T + "c") or 1))
            elif child.tag == T + "tab":
                bits.append("\t")
            elif child.tag == T + "line-break":
                bits.append("\n")
            else:
                bits.append(text_of(child))
            bits.append(child.tail or "")
        return "".join(bits)

    if ext == ".ods":
        out = []
        for table in root.iter(TB + "table"):
            rows = []
            for tr in table.iter(TB + "table-row"):
                cells = []
                for tc in tr:
                    if tc.tag not in (TB + "table-cell", TB + "covered-table-cell"):
                        continue
                    value = "\n".join(text_of(p) for p in tc.iter(T + "p"))
                    cells += [value] * min(int(tc.get(TB + "number-columns-repeated") or 1), 200)
                while cells and not cells[-1]:
                    cells.pop()
                rows += [cells] * (min(int(tr.get(TB + "number-rows-repeated") or 1), 50) if cells else 1)
            while rows and not rows[-1]:
                rows.pop()
            out.append(f"## Sheet: {table.get(TB + 'name')}\n" + (md_table(rows[:MAX_SHEET_ROWS]) or "(empty)"))
        return "\n\n".join(out)
    out = []

    def walk(node, bullet=""):
        for child in node:
            if child.tag == T + "h":
                out.append("#" * int(child.get(T + "outline-level") or 1) + " " + text_of(child).strip())
            elif child.tag == T + "p":
                text = text_of(child).strip()
                if text or not bullet:
                    out.append(bullet + text)
            elif child.tag == T + "list":
                for item in child:
                    walk(item, "- ")
            elif child.tag == TB + "table":
                rows = [[" ".join(text_of(p).strip() for p in tc.iter(T + "p")) for tc in tr if tc.tag == TB + "table-cell"] for tr in child.iter(TB + "table-row")]
                out.append(md_table(rows))
            elif child.tag in (T + "section", T + "list-item", "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}body",
                               "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}text", "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}presentation") \
                    or child.tag.endswith("}page") or child.tag.endswith("}frame") or child.tag.endswith("}text-box"):
                walk(child, bullet)

    walk(root)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def legacy_to_new(data, ext, allow_tools):
    """An old binary document turned into its new format by LibreOffice, or the text of a PDF by pdftotext."""
    if not allow_tools:
        raise ApiError(400, f"Studio reads {ext} files only for admins; save it as .docx, .xlsx or .pptx and add that")
    with tempfile.TemporaryDirectory(prefix="studio-doc-") as tmp:
        src = os.path.join(tmp, "in" + ext)
        with open(src, "wb") as fh:
            fh.write(data)
        if ext == ".pdf":
            if not shutil.which("pdftotext"):
                raise ApiError(400, "pdftotext is not installed (sudo apt install poppler-utils)")
            done = subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", src, "-"], capture_output=True, timeout=60)
            if done.returncode != 0:
                raise ApiError(400, "pdftotext could not read this PDF")
            return None, done.stdout.decode("utf-8", "replace")
        office = shutil.which("soffice") or shutil.which("libreoffice")
        if not office:
            raise ApiError(400, f"LibreOffice is not installed, so {ext} files cannot be read; save it as .{DOC_LEGACY[ext]}")
        profile = "file://" + os.path.join(tmp, "profile")      # its own profile, so an open LibreOffice is not disturbed
        try:
            subprocess.run([office, f"-env:UserInstallation={profile}", "--headless", "--norestore", "--convert-to", DOC_LEGACY[ext],
                            "--outdir", tmp, src], capture_output=True, timeout=120, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            raise ApiError(400, f"LibreOffice took too long to open this {ext} file")
        out = os.path.join(tmp, "in." + DOC_LEGACY[ext])
        if not os.path.isfile(out):
            raise ApiError(400, f"LibreOffice could not open this {ext} file")
        with open(out, "rb") as fh:
            return "." + DOC_LEGACY[ext], fh.read()


def document_text(data, name, allow_tools=False):
    """The text of a document, with a header saying what it is; tables as Markdown, each sheet or slide in its section."""
    ext = os.path.splitext(name)[1].lower()
    if ext not in DOC_EXTS:
        raise ApiError(400, f"{name} is not a document Studio reads")
    if len(data) > MAX_DOC_BYTES:
        raise ApiError(413, f"{name} is larger than {MAX_DOC_BYTES // 1048576} MB")
    kind = DOC_EXTS[ext]
    if ext in DOC_LEGACY or ext == ".pdf":
        new_ext, converted = legacy_to_new(data, ext, allow_tools)
        if new_ext is None:
            text = converted.replace("\f", "\n\n")
            return f"[{kind} document {name}, as text]\n" + text[:MAX_DOC_TEXT] + ("\n[the rest was cut]" if len(text) > MAX_DOC_TEXT else "")
        data, ext = converted, new_ext
    import zipfile
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ApiError(400, f"{name} is not a valid {kind} file")
    with z:
        if ext in (".docx", ".docm"):
            text = docx_text(z)
        elif ext in (".xlsx", ".xlsm"):
            text = xlsx_text(z)
        elif ext == ".pptx":
            text = pptx_text(z)
        else:
            text = odf_text(z, ext)
    text = text or "(no text in this document)"
    return f"[{kind} document {name}, as text; tables in Markdown]\n" + text[:MAX_DOC_TEXT] + ("\n[the rest was cut]" if len(text) > MAX_DOC_TEXT else "")


def read_document_file(path, allow_tools=False):
    if os.path.getsize(path) > MAX_DOC_BYTES:
        raise ApiError(413, f"{os.path.basename(path)} is larger than {MAX_DOC_BYTES // 1048576} MB")
    with open(path, "rb") as fh:
        return document_text(fh.read(), os.path.basename(path), allow_tools)


# ---- Writing Word and Excel files (the agent's write_document tool), with the standard library: a Word file from
# Markdown (headings, paragraphs, bold, italic, code, links, lists, tables, quotes, code blocks, page breaks) and an
# Excel workbook from sheets of rows (numbers stay numbers, "=..." is a formula, recalculated when the file opens).
WRITE_DOC_EXTS = {".docx": "Word", ".xlsx": "Excel"}
MAX_WRITE_CELLS = 200_000
XML_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
DOC_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
W_MAIN = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
S_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
XML_HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'


def xesc(text, attr=False):
    text = XML_BAD.sub("", str(text)).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return text.replace('"', "&quot;") if attr else text


def zip_parts(parts):
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in parts.items():
            info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, text.encode("utf-8"))
    return buf.getvalue()


def core_props(title):
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return (XML_HEAD + '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            + (f"<dc:title>{xesc(title)}</dc:title>" if title else "") + "<dc:creator>Eidoloom Studio</dc:creator>"
            f'<dcterms:created xsi:type="dcterms:W3CDTF">{stamp}</dcterms:created>'
            f'<dcterms:modified xsi:type="dcterms:W3CDTF">{stamp}</dcterms:modified></cp:coreProperties>')


DOCX_STYLES = (XML_HEAD + f'<w:styles xmlns:w="{W_MAIN}">'
    '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Calibri" w:cs="Calibri"/>'
    '<w:sz w:val="22"/><w:szCs w:val="22"/></w:rPr></w:rPrDefault>'
    '<w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="264" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>'
    '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>'
    '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>'
    '<w:pPr><w:spacing w:after="240"/></w:pPr><w:rPr><w:b/><w:color w:val="1F3864"/><w:sz w:val="48"/><w:szCs w:val="48"/></w:rPr></w:style>'
    + "".join(f'<w:style w:type="paragraph" w:styleId="Heading{n}"><w:name w:val="heading {n}"/><w:basedOn w:val="Normal"/>'
              f'<w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:spacing w:before="{240 if n < 3 else 160}" w:after="80"/>'
              f'<w:outlineLvl w:val="{n - 1}"/></w:pPr><w:rPr><w:b/><w:color w:val="{"1F3864" if n < 3 else "2F5496"}"/>'
              f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/></w:rPr></w:style>'
              for n, size in zip(range(1, 7), (32, 28, 25, 23, 22, 22)))
    + '<w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/><w:basedOn w:val="Normal"/>'
    '<w:pPr><w:spacing w:after="40"/><w:contextualSpacing/></w:pPr></w:style>'
    '<w:style w:type="paragraph" w:styleId="Quote"><w:name w:val="Quote"/><w:basedOn w:val="Normal"/>'
    '<w:pPr><w:ind w:left="567"/><w:pBdr><w:left w:val="single" w:sz="18" w:space="8" w:color="A5A5A5"/></w:pBdr></w:pPr>'
    '<w:rPr><w:i/><w:color w:val="404040"/></w:rPr></w:style>'
    '<w:style w:type="paragraph" w:styleId="Code"><w:name w:val="Code"/><w:basedOn w:val="Normal"/>'
    '<w:pPr><w:spacing w:after="0" w:line="240" w:lineRule="auto"/><w:shd w:val="clear" w:color="auto" w:fill="F2F2F2"/></w:pPr>'
    '<w:rPr><w:rFonts w:ascii="Consolas" w:hAnsi="Consolas" w:cs="Consolas"/><w:sz w:val="20"/></w:rPr></w:style>'
    '<w:style w:type="character" w:styleId="Hyperlink"><w:name w:val="Hyperlink"/><w:rPr><w:color w:val="0563C1"/><w:u w:val="single"/></w:rPr></w:style>'
    '<w:style w:type="table" w:styleId="TableGrid"><w:name w:val="Table Grid"/><w:tblPr><w:tblBorders>'
    + "".join(f'<w:{side} w:val="single" w:sz="4" w:space="0" w:color="A6A6A6"/>' for side in ("top", "left", "bottom", "right", "insideH", "insideV"))
    + '</w:tblBorders><w:tblCellMar><w:left w:w="100" w:type="dxa"/><w:right w:w="100" w:type="dxa"/></w:tblCellMar></w:tblPr></w:style>'
    '</w:styles>')


def docx_numbering(numbered_lists):
    bullets = "•◦▪"
    levels = lambda fmt: "".join(
        f'<w:lvl w:ilvl="{i}"><w:start w:val="1"/><w:numFmt w:val="{fmt}"/>'
        f'<w:lvlText w:val="{xesc(bullets[i % 3], True) if fmt == "bullet" else f"%{i + 1}."}"/><w:lvlJc w:val="left"/>'
        f'<w:pPr><w:ind w:left="{360 * (i + 1) + 360}" w:hanging="360"/></w:pPr></w:lvl>' for i in range(9))
    nums = '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>' + "".join(
        f'<w:num w:numId="{n}"><w:abstractNumId w:val="1"/><w:lvlOverride w:ilvl="0"><w:startOverride w:val="1"/></w:lvlOverride></w:num>'
        for n in numbered_lists)
    return (XML_HEAD + f'<w:numbering xmlns:w="{W_MAIN}">'
            f'<w:abstractNum w:abstractNumId="0"><w:multiLevelType w:val="hybridMultilevel"/>{levels("bullet")}</w:abstractNum>'
            f'<w:abstractNum w:abstractNumId="1"><w:multiLevelType w:val="hybridMultilevel"/>{levels("decimal")}</w:abstractNum>'
            f'{nums}</w:numbering>')


SHADE = '<w:shd w:val="clear" w:color="auto" w:fill="D9E2F3"/>'
INLINE_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__|(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])|`([^`]+)`|\[([^\]]+)\]\((https?://[^)\s]+|mailto:[^)\s]+)\)")


def docx_runs(text, links, bold=False):
    """Word runs for one line of Markdown text: **bold**, *italic*, `code` and [links](https://...)."""
    def run(t, b=False, i=False, code=False):
        props = ("<w:b/>" if b or bold else "") + ("<w:i/>" if i else "") + ('<w:rFonts w:ascii="Consolas" w:hAnsi="Consolas"/>' if code else "")
        return (f"<w:r>{f'<w:rPr>{props}</w:rPr>' if props else ''}"
                f'<w:t xml:space="preserve">{xesc(t)}</w:t></w:r>') if t else ""
    out, at = [], 0
    for m in INLINE_RE.finditer(text):
        out.append(run(text[at:m.start()]))
        if m.group(1) or m.group(2):
            out.append(run(m.group(1) or m.group(2), b=True))
        elif m.group(3):
            out.append(run(m.group(3), i=True))
        elif m.group(4):
            out.append(run(m.group(4), code=True))
        else:
            links.append(m.group(6))
            out.append(f'<w:hyperlink r:id="rIdL{len(links)}"><w:r><w:rPr><w:rStyle w:val="Hyperlink"/>{"<w:b/>" if bold else ""}</w:rPr>'
                       f'<w:t xml:space="preserve">{xesc(m.group(5))}</w:t></w:r></w:hyperlink>')
        at = m.end()
    out.append(run(text[at:]))
    return "".join(out)


TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def md_cells(line):
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", line)]


def markdown_docx(markdown, title=""):
    """A Word file (bytes) from Markdown."""
    body, links, numbered = [], [], []
    def para(inner, style=None, extra=""):
        props = ('<w:pStyle w:val="%s"/>' % style if style else "") + extra
        return f"<w:p><w:pPr>{props}</w:pPr>{inner}</w:p>" if props else f"<w:p>{inner}</w:p>"
    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    i, text_buf, list_num = 0, [], None

    def flush():
        if text_buf:
            body.append(para(docx_runs(" ".join(s.strip() for s in text_buf), links)))
            text_buf.clear()
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("```"):
            flush()
            i += 1
            code = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            for c in code or [""]:
                body.append(para(f'<w:r><w:t xml:space="preserve">{xesc(c.expandtabs(4))}</w:t></w:r>', "Code"))
            body.append(para(""))
            i += 1
            continue
        if not stripped:
            flush()
            list_num = None
            i += 1
            continue
        if stripped in ("\\pagebreak", "\\newpage", "<pagebreak>"):
            flush()
            body.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
            i += 1
            continue
        heading = re.match(r"^(#{1,6})\s+(.*?)\s*#*$", stripped)
        if heading:
            flush()
            level = len(heading.group(1))
            body.append(para(docx_runs(heading.group(2), links), f"Heading{level}"))
            list_num = None
            i += 1
            continue
        if re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", stripped):
            flush()
            body.append(para("", None, '<w:pBdr><w:bottom w:val="single" w:sz="6" w:space="1" w:color="A6A6A6"/></w:pBdr>'))
            i += 1
            continue
        if stripped.startswith("|") and i + 1 < len(lines) and TABLE_SEP.match(lines[i + 1]):
            flush()
            rows = [md_cells(line)]
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(md_cells(lines[i]))
                i += 1
            width = max(len(r) for r in rows)
            grid = "".join('<w:gridCol w:w="%d"/>' % (9000 // width) for _ in range(width))
            trs = []
            for n, row in enumerate(rows):
                row = row + [""] * (width - len(row))
                head = n == 0
                cells = "".join(f'<w:tc><w:tcPr><w:tcW w:w="{9000 // width}" w:type="dxa"/>'
                                f'{SHADE if head else ""}</w:tcPr>'
                                f'<w:p><w:pPr><w:spacing w:before="40" w:after="40"/></w:pPr>{docx_runs(c, links, bold=head)}</w:p></w:tc>' for c in row)
                trs.append(f'<w:tr>{"<w:trPr><w:tblHeader/></w:trPr>" if head else ""}{cells}</w:tr>')
            body.append(f'<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/><w:tblW w:w="0" w:type="auto"/></w:tblPr>'
                        f'<w:tblGrid>{grid}</w:tblGrid>{"".join(trs)}</w:tbl>')
            body.append(para(""))
            continue
        item = re.match(r"^(\s*)([-*+]|\d{1,3}[.)])\s+(.*)$", line)
        if item:
            flush()
            level = min(8, len(item.group(1).expandtabs(4)) // 2)
            if item.group(2)[0].isdigit():
                if list_num is None:
                    list_num = 2 + len(numbered)
                    numbered.append(list_num)
                num = list_num
            else:
                num = 1
            body.append(para(docx_runs(item.group(3), links), "ListParagraph",
                             f'<w:numPr><w:ilvl w:val="{level}"/><w:numId w:val="{num}"/></w:numPr>'))
            i += 1
            continue
        if stripped.startswith(">"):
            flush()
            quote = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote.append(lines[i].strip()[1:].strip())
                i += 1
            body.append(para(docx_runs(" ".join(quote), links), "Quote"))
            continue
        list_num = None
        text_buf.append(line)
        i += 1
    flush()
    if title:
        body.insert(0, para(docx_runs(title, links), "Title"))
    sect = ('<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:right="1304" w:bottom="1440" w:left="1304" '
            'w:header="709" w:footer="709" w:gutter="0"/></w:sectPr>')
    document = (XML_HEAD + f'<w:document xmlns:w="{W_MAIN}" xmlns:r="{DOC_REL}"><w:body>{"".join(body)}{sect}</w:body></w:document>')
    rels = (XML_HEAD + f'<Relationships xmlns="{PKG_REL}">'
            f'<Relationship Id="rIdS" Type="{DOC_REL}/styles" Target="styles.xml"/>'
            f'<Relationship Id="rIdN" Type="{DOC_REL}/numbering" Target="numbering.xml"/>'
            + "".join(f'<Relationship Id="rIdL{n}" Type="{DOC_REL}/hyperlink" Target="{xesc(url, True)}" TargetMode="External"/>'
                      for n, url in enumerate(links, 1)) + "</Relationships>")
    return zip_parts({
        "[Content_Types].xml": XML_HEAD + f'<Types xmlns="{CT_NS}"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
            '<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>'
            '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/></Types>',
        "_rels/.rels": XML_HEAD + f'<Relationships xmlns="{PKG_REL}">'
            f'<Relationship Id="rId1" Type="{DOC_REL}/officeDocument" Target="word/document.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/></Relationships>',
        "word/document.xml": document,
        "word/_rels/document.xml.rels": rels,
        "word/styles.xml": DOCX_STYLES,
        "word/numbering.xml": docx_numbering(numbered),
        "docProps/core.xml": core_props(title or ""),
    })


def col_letters(n):
    out = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


PLAIN_NUMBER = re.compile(r"-?(0|[1-9]\d{0,14})(\.\d+)?")


def xlsx_cell(ref, value, style):
    s = f' s="{style}"' if style else ""
    if value is None or value == "":
        return f'<c r="{ref}"{s}/>' if style else ""
    if isinstance(value, bool):
        return f'<c r="{ref}"{s} t="b"><v>{int(value)}</v></c>'
    if isinstance(value, (int, float)) and math.isfinite(value):
        return f'<c r="{ref}"{s}><v>{value!r}</v></c>'
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    if text.startswith("=") and len(text) > 1:
        return f'<c r="{ref}"{s}><f>{xesc(text[1:])}</f></c>'
    if PLAIN_NUMBER.fullmatch(text.strip()):
        return f'<c r="{ref}"{s}><v>{text.strip()}</v></c>'
    return f'<c r="{ref}"{s} t="inlineStr"><is><t xml:space="preserve">{xesc(text[:32767])}</t></is></c>'


def sheet_name(name, n, used):
    name = re.sub(r"[\[\]:*?/\\]", " ", str(name or "")).strip().strip("'")[:31] or f"Sheet{n}"
    base, k = name, 2
    while name.lower() in used:
        name = f"{base[:27]} ({k})"
        k += 1
    used.add(name.lower())
    return name


def sheets_xlsx(sheets, title=""):
    """An Excel file (bytes) from [{name, rows, header, widths}]."""
    used, names, sheet_xml, cells = set(), [], [], 0
    for n, sheet in enumerate(sheets, 1):
        rows = sheet.get("rows") or []
        if not isinstance(rows, list) or not all(isinstance(r, list) for r in rows):
            raise ApiError(400, f"sheet {n}: rows must be a list of lists of cells")
        cells += sum(len(r) for r in rows)
        if cells > MAX_WRITE_CELLS:
            raise ApiError(400, f"at most {MAX_WRITE_CELLS} cells in one workbook")
        header = sheet.get("header", True) is not False and len(rows) > 1
        names.append(sheet_name(sheet.get("name"), n, used))
        width = max((len(r) for r in rows), default=0)
        given = sheet.get("widths") if isinstance(sheet.get("widths"), list) else []
        cols = []
        for c in range(width):
            w = given[c] if c < len(given) and isinstance(given[c], (int, float)) and 1 <= given[c] <= 255 else None
            if w is None:
                longest = max((len(str(r[c])) for r in rows[:500] if c < len(r) and r[c] is not None and not str(r[c]).startswith("=")), default=6)
                w = min(60, max(8, longest + 2))
            cols.append(f'<col min="{c + 1}" max="{c + 1}" width="{w}" customWidth="1"/>')
        data = []
        for r, row in enumerate(rows, 1):
            style = 1 if header and r == 1 else 0
            data.append(f'<row r="{r}">' + "".join(xlsx_cell(f"{col_letters(c)}{r}", v, style) for c, v in enumerate(row)) + "</row>")
        view = ('<sheetViews><sheetView workbookViewId="0"' + (' tabSelected="1"' if n == 1 else "") + ">"
                + ('<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>' if header else "") + "</sheetView></sheetViews>")
        sheet_xml.append(XML_HEAD + f'<worksheet xmlns="{S_MAIN}" xmlns:r="{DOC_REL}">{view}'
                         + (f"<cols>{''.join(cols)}</cols>" if cols else "") + f'<sheetData>{"".join(data)}</sheetData></worksheet>')
    if not sheet_xml:
        raise ApiError(400, "give at least one sheet")
    parts = {
        "[Content_Types].xml": XML_HEAD + f'<Types xmlns="{CT_NS}"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            + "".join(f'<Override PartName="/xl/worksheets/sheet{n}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                      for n in range(1, len(sheet_xml) + 1))
            + '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/></Types>',
        "_rels/.rels": XML_HEAD + f'<Relationships xmlns="{PKG_REL}">'
            f'<Relationship Id="rId1" Type="{DOC_REL}/officeDocument" Target="xl/workbook.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/></Relationships>',
        "xl/workbook.xml": XML_HEAD + f'<workbook xmlns="{S_MAIN}" xmlns:r="{DOC_REL}"><sheets>'
            + "".join(f'<sheet name="{xesc(name, True)}" sheetId="{n}" r:id="rId{n}"/>' for n, name in enumerate(names, 1))
            + '</sheets><calcPr calcId="191029" fullCalcOnLoad="1"/></workbook>',
        "xl/_rels/workbook.xml.rels": XML_HEAD + f'<Relationships xmlns="{PKG_REL}">'
            + "".join(f'<Relationship Id="rId{n}" Type="{DOC_REL}/worksheet" Target="worksheets/sheet{n}.xml"/>' for n in range(1, len(sheet_xml) + 1))
            + f'<Relationship Id="rIdS" Type="{DOC_REL}/styles" Target="styles.xml"/></Relationships>',
        "xl/styles.xml": XML_HEAD + f'<styleSheet xmlns="{S_MAIN}">'
            '<fonts count="2"><font><sz val="11"/><name val="Calibri"/><family val="2"/></font>'
            '<font><b/><sz val="11"/><name val="Calibri"/><family val="2"/></font></fonts>'
            '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
            '<fill><patternFill patternType="solid"><fgColor rgb="FFD9E2F3"/><bgColor indexed="64"/></patternFill></fill></fills>'
            '<borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border>'
            '<border><left/><right/><top/><bottom style="thin"><color rgb="FF8EA9DB"/></bottom><diagonal/></border></borders>'
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
            '<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1"/></cellXfs>'
            '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>',
        "docProps/core.xml": core_props(title or ""),
    }
    for n, text in enumerate(sheet_xml, 1):
        parts[f"xl/worksheets/sheet{n}.xml"] = text
    return zip_parts(parts)


def markdown_sheets(markdown):
    """Sheets from Markdown tables (each table a sheet, named by the heading above it), or from CSV text."""
    import csv
    lines, sheets, name, i = markdown.replace("\r\n", "\n").split("\n"), [], "", 0
    while i < len(lines):
        heading = re.match(r"^#{1,6}\s+(.*)$", lines[i].strip())
        if heading:
            name = heading.group(1).strip()
        if lines[i].strip().startswith("|") and i + 1 < len(lines) and TABLE_SEP.match(lines[i + 1]):
            rows = [md_cells(lines[i])]
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(md_cells(lines[i]))
                i += 1
            sheets.append({"name": name, "rows": rows})
            name = ""
            continue
        i += 1
    if sheets:
        return sheets
    text = markdown.strip()
    if not text:
        return []
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return [{"name": "", "rows": [row for row in csv.reader(io.StringIO(text), dialect) if row]}]


def build_document(rel, args):
    """(bytes, kind) of the document write_document makes at rel."""
    ext = os.path.splitext(rel)[1].lower()
    if ext not in WRITE_DOC_EXTS:
        raise ApiError(400, "write_document makes .docx (Word) and .xlsx (Excel) files; use write_file for other files, such as .csv or .md")
    title = args.get("title") if isinstance(args.get("title"), str) else ""
    content = args.get("content") if isinstance(args.get("content"), str) else ""
    if ext == ".docx":
        if not content.strip():
            raise ApiError(400, "content is required for a Word file: the document in Markdown")
        return markdown_docx(content, title.strip()), "Word"
    sheets = args.get("sheets")
    if isinstance(sheets, dict):
        sheets = [sheets]
    if sheets is None and content.strip():
        sheets = markdown_sheets(content)
    if not isinstance(sheets, list) or not sheets or not all(isinstance(s, dict) for s in sheets):
        raise ApiError(400, "sheets is required for an Excel file: a list of {name, rows}, where rows is a list of lists of cells")
    return sheets_xlsx(sheets, title.strip()), "Excel"


def check_attachments(raw, admin=False):
    """The message's attachments, checked: [{kind: "image", name, mime, data: bytes} | {kind: "text", name, text}]."""
    if raw in (None, []):
        return []
    if not isinstance(raw, list) or len(raw) > MAX_ATTACHMENTS:
        raise ApiError(400, f"at most {MAX_ATTACHMENTS} attachments per message")
    out, text_total = [], 0
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise ApiError(400, "each attachment needs a name")
        name = os.path.basename(item["name"].replace("\\", "/")).strip()[:120] or "attachment"
        if item.get("kind") == "image":
            match = DATA_URL_RE.fullmatch(item.get("data") or "")
            if not match or match[1] not in IMAGE_TYPES:
                raise ApiError(400, f"{name}: pictures must be PNG, JPEG, WebP or GIF")
            try:
                data = base64.b64decode(match[2], validate=False)
            except ValueError:
                raise ApiError(400, f"{name}: the picture could not be read")
            if len(data) > MAX_IMAGE_BYTES:
                raise ApiError(413, f"{name}: pictures can be at most {MAX_IMAGE_BYTES // 1048576} MB")
            out.append({"kind": "image", "name": name, "mime": match[1], "data": data})
        elif item.get("kind") == "text":
            text = item.get("text")
            if not isinstance(text, str) or "\x00" in text:
                raise ApiError(400, f"{name}: only text files and pictures can be attached")
            if text_total + len(text) > MAX_ATTACHED_TEXT:      # too much for the chat: saved in the folder, the model gets its start
                out.append({"kind": "big", "name": name, "text": text, "data": text.encode("utf-8")})
                text_total += BIG_PREVIEW
                continue
            text_total += len(text)
            out.append({"kind": "text", "name": name, "text": text})
        elif item.get("kind") == "document":
            # Word, Excel and other documents: sent as data, read into text here, then treated like a text file.
            match = re.fullmatch(r"data:[\w.+/-]*;base64,([A-Za-z0-9+/=\s]+)", item.get("data") or "")
            if not match or not is_document(name):
                raise ApiError(400, f"{name}: documents can be Word, Excel, PowerPoint, OpenDocument or PDF files")
            try:
                data = base64.b64decode(match[1], validate=False)
            except ValueError:
                raise ApiError(400, f"{name}: the file could not be read")
            text = document_text(data, name, allow_tools=admin)
            if text_total + len(text) > MAX_ATTACHED_TEXT or "more rows not shown" in text:
                out.append({"kind": "big", "name": name, "text": text, "data": data, "sheets": document_sheets(data, name, admin)})
                text_total += BIG_PREVIEW
                continue
            text_total += len(text)
            out.append({"kind": "text", "name": name, "text": text})
        elif item.get("kind") == "file":
            # any other file: already in the folder (uploaded to attachments/, or picked from the project); the model
            # gets its path and what kind of file it is (project_files_text), and reads or uses it with its tools
            if not isinstance(item.get("path"), str) or not item["path"].strip():
                raise ApiError(400, f"{name}: the file's path is missing")
            out.append({"kind": "file", "name": name, "path": item["path"]})
        else:
            raise ApiError(400, f"{name}: only pictures, text files and documents can be attached")
    return out


def media_facts(full):
    """A few words about a sound or video for the model: length, rate, channels, size."""
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_entries",
                              "format=duration,format_name:stream=codec_type,codec_name,sample_rate,channels,width,height", full],
                             capture_output=True, text=True, timeout=15)
        info = json.loads(out.stdout or "{}")
    except (OSError, ValueError, subprocess.SubprocessError):
        return ""
    words, streams = [], info.get("streams") or []
    try:
        seconds = float((info.get("format") or {}).get("duration") or 0)
    except ValueError:
        seconds = 0
    video = next((st for st in streams if st.get("codec_type") == "video" and st.get("width")), None)
    audio = next((st for st in streams if st.get("codec_type") == "audio"), None)
    if video and seconds > 0.2:
        words.append(f"video {video['width']}x{video['height']}, {seconds:.1f} s")
    elif video:
        words.append(f"picture {video['width']}x{video['height']}")
    elif audio:
        words.append(f"sound, {seconds:.2f} s")
    if audio:
        words.append(f"{audio.get('codec_name')} {audio.get('sample_rate')} Hz, {audio.get('channels')} channel{'s' if audio.get('channels') != 1 else ''}")
    return ", ".join(w for w in words if w)


def project_files_text(ws, attachments, admin=False):
    """Files added to the message that are in the folder: text and documents are included like @file, other files
    are named with their path in the folder, the full path on Studio's server (for tool servers) and a few facts."""
    out = []
    for a in attachments:
        if a["kind"] != "file":
            continue
        full = resolve(ws, a["path"])
        if not os.path.isfile(full):
            raise ApiError(400, f"{a['path']} is not in the folder")
        a["path"] = rel_posix(full, ws["path"])
        size = os.path.getsize(full)
        text = None
        try:
            if is_document(full):
                text = read_document_file(full, allow_tools=bool(admin))
            elif size <= MAX_TEXT_BYTES:
                text = read_text_file(full)[0]
        except (ApiError, OSError):
            text = None
        if text is not None and len(text) <= MAX_MENTION_CHARS:
            out.append(f"Attached file {a['path']}:\n```\n{text}\n```\n\n")
            continue
        facts = media_facts(full) if os.path.splitext(full)[1].lower() in MEDIA_FACT_EXTS else ""
        kb = f"{size / 1048576:.1f} MB" if size >= 1048576 else f"{max(1, size // 1024)} KB"
        out.append(f"Attached file {a['path']} ({facts + ', ' if facts else ''}{kb}), saved in the project; its full path on Studio's server, "
                   f"for tool servers, is {full}." + (" It is a large text file: read it in parts with read_file." if text is not None else "") + "\n\n")
    return "".join(out)


MEDIA_FACT_EXTS = {".wav", ".mp3", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".aac", ".aif", ".aiff", ".mp4", ".webm", ".mov", ".mkv", ".avi", ".m4v"}


BIG_PREVIEW = 20_000          # characters of a large attachment's text the model gets in the message


def document_sheets(data, name, admin=False):
    """A workbook's sheets as [(name, rows)] with every row, for CSV files; [] for other documents."""
    ext = os.path.splitext(name)[1].lower()
    if ext not in (".xlsx", ".xlsm", ".xls"):
        return []
    import zipfile
    try:
        if ext == ".xls":
            ext, data = legacy_to_new(data, ext, admin)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            return [(sheet, grid_rows(grid)) for sheet, grid, _ in xlsx_grids(z) if grid]
    except (ApiError, zipfile.BadZipFile, OSError):
        return []


def free_rel(ws, rel):
    """rel, or rel with " (2)", " (3)"... before its extension when a file is already there."""
    stem, ext = os.path.splitext(rel)
    n = 1
    while os.path.lexists(resolve(ws, rel)):
        n += 1
        rel = f"{stem} ({n}){ext}"
    return rel


def save_big_attachments(ws, attachments):
    """Attachments too large for the chat go into the folder's attachments/ folder, spreadsheets also as one CSV per
    sheet with every row (in a folder named after the file), so the agent can read them in parts, run scripts on them or hand them to a tool."""
    import csv
    for a in attachments:
        if a["kind"] != "big":
            continue
        rel = free_rel(ws, "attachments/" + a["name"])
        write_bytes(resolve(ws, rel), a["data"])
        a["saved"], a["csv"] = rel, []
        stem = os.path.splitext(rel)[0]
        for sheet, rows in a.get("sheets") or []:
            out = io.StringIO()
            csv.writer(out).writerows(rows)
            safe = re.sub(r'[\\/:*?"<>|]', " ", sheet).strip() or "sheet"
            csv_rel = free_rel(ws, f"{stem}/{safe}.csv")        # one folder per file, named after it
            write_bytes(resolve(ws, csv_rel), out.getvalue().encode("utf-8"))
            a["csv"].append((csv_rel, sheet, len(rows), len(rows[0]) if rows else 0))


def attached_files_text(attachments):
    out = []
    for a in attachments:
        if a["kind"] == "text":
            out.append(f"Attached file {a['name']}:\n```\n{a['text']}\n```\n\n")
        elif a["kind"] == "big":
            text = a["text"]
            head = text[:BIG_PREVIEW].rsplit("\n", 1)[0] if len(text) > BIG_PREVIEW else text
            csvs = "".join(f"\n- {rel}: sheet {sheet!r}, {rows} rows (with its header row), {cols} columns, every row" for rel, sheet, rows, cols in a.get("csv") or [])
            out.append(f"Attached file {a['name']} is too large to include here ({len(text) // 1000}K characters as text), so Studio saved it in the project as "
                       f"{a['saved']}." + (f" Each sheet is also saved as a CSV file:{csvs}\nUse the CSV files for scripts and imports." if csvs else "")
                       + f" Read the rest with read_file in parts (start_line, end_line). Its first {len(head) // 1000 or 1}K characters:\n```\n{head}\n```\n\n")
    return "".join(out)


def store_images(sid, attachments):
    """Saves the pictures beside the chat; returns [{id, name}] for the chat and the model."""
    stored = []
    for a in attachments:
        if a["kind"] != "image":
            continue
        os.makedirs(attachments_dir(sid), exist_ok=True)
        image_id = secrets.token_hex(8) + "." + IMAGE_TYPES[a["mime"]]
        with open(os.path.join(attachments_dir(sid), image_id), "wb") as fh:
            fh.write(a["data"])
        stored.append({"id": image_id, "name": a["name"]})
    return stored


def wire_messages(sid, messages):
    """Messages as the gateway takes them: a user message with pictures becomes text and image parts."""
    out = []
    for m in messages:
        if "images" not in m:
            out.append(m)
            continue
        parts = [{"type": "text", "text": m.get("content") or ""}]
        for image_id in m["images"]:
            path = os.path.join(attachments_dir(sid), os.path.basename(image_id))
            ext = os.path.splitext(path)[1].lstrip(".")
            try:
                with open(path, "rb") as fh:
                    data = base64.b64encode(fh.read()).decode()
            except OSError:
                parts.append({"type": "text", "text": "[a picture that is no longer available]"})
                continue
            mime = next((mt for mt, e in IMAGE_TYPES.items() if e == ext), "image/png")
            parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}})
        out.append({**{k: v for k, v in m.items() if k != "images"}, "content": parts})
    return out


@route("GET", "/api/agent/attachment")
def agent_attachment(req):
    session = load_session(req, req.arg("session"))
    image_id = req.arg("id") or ""
    path = os.path.join(attachments_dir(session["id"]), image_id)
    if not re.fullmatch(r"[0-9a-f]{16}\.(png|jpg|webp|gif)", image_id) or not os.path.isfile(path):
        raise ApiError(404, "not found")
    with open(path, "rb") as fh:
        data = fh.read()
    req.send_response(200)
    req.send_header("Content-Type", next(mt for mt, e in IMAGE_TYPES.items() if e == image_id.rsplit(".", 1)[1]))
    req.send_header("Content-Length", str(len(data)))
    req.send_header("Cache-Control", "private, max-age=86400")
    req.end_headers()
    req.wfile.write(data)


# ---------------------------------------------------------------------------
# Agent: undo, rewind and branch
# ---------------------------------------------------------------------------

def restore_checkpoint(ws, cid):
    """Puts the files of an undo point back as they were. Returns their paths, or None for an unknown (or used) point."""
    path = checkpoint_path(ws, cid)
    cp = read_json(path, None)
    if cp is None:
        return None
    restored = []
    for rel, info in cp["files"].items():
        target = resolve(ws, rel)
        if info["existed"]:
            write_bytes(target, info["content"].encode("utf-8"))
        elif os.path.lexists(target):
            move_to_trash(ws, target)
        restored.append(rel)
    os.remove(path)
    return restored


@route("POST", "/api/agent/undo")
def agent_undo(req):
    restored = restore_checkpoint(req.workspace(), req.field("checkpoint"))
    if restored is None:
        raise ApiError(404, "unknown checkpoint")
    return {"restored": restored}


# Rewind and branch. A chat keeps two lists: the events the page shows, and the messages the model reads. They are cut at
# the same user message: the n-th one the user sent (steers do not count), found in both lists.
def real_user_message(m):
    content = m.get("content") if m.get("role") == "user" else None
    text = content if isinstance(content, str) else ""
    return content is not None and not text.startswith(LOOP_NOTES) and GUIDE_NOTE not in text[:30000]


def user_points(session):
    """[(event index, message index)] of each message the user sent, in order. None when the two lists do not match up."""
    events = [i for i, e in enumerate(session.get("events") or []) if e.get("type") == "user" and not e.get("guided")]
    recorded = [session["events"][i].get("mi") for i in events]
    if all(isinstance(mi, int) for mi in recorded):
        return list(zip(events, recorded))
    messages = [i for i, m in enumerate(session.get("messages") or []) if real_user_message(m)]
    return list(zip(events, messages)) if len(messages) == len(events) else None


def cut_point(session, n, end_ok=False):
    """(event index, message index) just before the user's n-th message (0-based); with end_ok, n may be their count (the end)."""
    points = user_points(session)
    if points is None:
        raise ApiError(409, "This chat is from an older Studio and cannot be cut at a message.")
    if end_ok and n == len(points):
        return len(session["events"]), len(session["messages"])
    if type(n) is not int or not 0 <= n < len(points):
        raise ApiError(400, "unknown message")
    ei, mi = points[n]
    if any(e.get("type") == "compacted" for e in session["events"][ei:]):
        raise ApiError(409, "The chat was summarized after this message, so it cannot go back to it.")
    return ei, mi


def session_busy(sid):
    with APP.lock:
        return any(r.session["id"] == sid for r in APP.active.values())


def forget_prompt(session):
    """After a cut, the chat's saved prompt may describe messages that are gone; the next run builds it again."""
    for key in ("system_prompt", "prompt_fixed", "prompt_sections"):
        session.pop(key, None)


EFFORTS = ("none", "low", "medium", "high", "xhigh")
CODEX_EFFORTS = ("minimal", "low", "medium", "high", "xhigh", "max", "ultra")     # Codex models offer some of these


@route("POST", "/api/agent/session/settings")
def agent_session_settings(req):
    """A chat's own settings: reasoning effort (none to xhigh, or "" for the Manager's default)."""
    ws = req.workspace()
    session = load_session(req, req.field("id"))
    if session.get("ws") != ws["id"]:
        raise ApiError(400, "session belongs to another workspace")
    effort = req.field("effort")
    if effort not in EFFORTS + CODEX_EFFORTS + ("",):
        raise ApiError(400, "effort must be none, minimal, low, medium, high, xhigh, max, ultra or empty")
    if session_busy(session["id"]):           # the run holds the chat; it takes the effort with its next request
        with APP.lock:
            run = next(r for r in APP.active.values() if r.session["id"] == session["id"])
        session = run.session
    if effort:
        session["effort"] = effort
    else:
        session.pop("effort", None)
    if not session_busy(session["id"]):
        save_session(session)
    return {"effort": session.get("effort", "")}


@route("POST", "/api/agent/session/rewind")
def agent_session_rewind(req):
    """Removes the user's n-th message and everything after it, and gives the message back for the box. With undo_files,
    the agent's file changes after it are undone too, newest first."""
    ws = req.workspace()
    session = load_session(req, req.field("id"))
    if session.get("ws") != ws["id"]:
        raise ApiError(400, "session belongs to another workspace")
    if session_busy(session["id"]):
        raise ApiError(409, "Stop the run first.")
    ei, mi = cut_point(session, req.field("user"))
    message = session["events"][ei]
    restored = []
    if req.field("undo_files") is True:
        for ev in reversed(session["events"][ei:]):
            if ev.get("type") == "file_changed" and isinstance(ev.get("checkpoint"), str):
                restored += restore_checkpoint(ws, ev["checkpoint"]) or []
    del session["events"][ei:], session["messages"][mi:]
    forget_prompt(session)
    save_session(session)
    return {"text": message.get("text") or "", "attachments": bool(message.get("images") or message.get("files")),
            "restored": sorted(set(restored))}


@route("POST", "/api/agent/session/branch")
def agent_session_branch(req):
    """A new chat holding this one up to just before the user's n-th message (or all of it, with n = their count)."""
    ws = req.workspace()
    session = load_session(req, req.field("id"))
    if session.get("ws") != ws["id"]:
        raise ApiError(400, "session belongs to another workspace")
    if session_busy(session["id"]):
        raise ApiError(409, "Wait until the run ends.")
    ei, mi = cut_point(session, req.field("user"), end_ok=True)
    sid = secrets.token_hex(6)
    title = (session.get("title") or "Chat")[:50]
    copy = {**json.loads(json.dumps({k: v for k, v in session.items() if k not in ("events", "messages")})),
            "id": sid, "title": "Branch: " + title, "events": json.loads(json.dumps(session["events"][:ei])),
            "messages": json.loads(json.dumps(session["messages"][:mi])), "branched_from": session["id"]}
    if os.path.isdir(attachments_dir(session["id"])):
        shutil.copytree(attachments_dir(session["id"]), attachments_dir(sid))
    save_session(copy)
    return {"id": sid, "title": copy["title"]}


# ---------------------------------------------------------------------------
# Agent: run object (event log, approvals, stop)
# ---------------------------------------------------------------------------

class Run:
    def __init__(self, ws, session, model, tools_enabled, auto_apply, auto_commands, context):
        self.id = secrets.token_hex(6)
        self.ws = ws
        self.session = session
        self.model = model
        self.tools_enabled = tools_enabled
        self.auto_apply = auto_apply
        self.auto_commands = auto_commands
        self.context = context
        self.project_tree = None
        self.skills_prompt = None
        self.mcp_tools = {}
        self.user_tools = {}                  # the user's own tools, fixed for the run
        self.mcp_prompt = None                   # tool name -> MCP server and tool, fixed for the run so the prompt stays the same
        self.cond = threading.Condition()
        self.events = []
        self.seq = 0
        self.finished = False
        self.stop = False
        self.turns = 0
        self.conn = None                      # open gateway connection, closed on stop
        self.owner = None
        self.started = time.time()
        self.progress = None                  # (current step, total steps, when that step began)
        self.plan = None                      # step names from the plan tool
        self.frozen_prompt = None             # the system prompt of this run's requests, fixed once it is built
        self.script_noted = False             # asked once to rewrite a reply that mixed Greek with other scripts
        self.script_asides = 0                # notes after text that came with tool calls and mixed scripts
        self.plan_noted = False               # told the model once that its progress total disagrees with the plan
        self.vision = False                   # the model can see pictures, so pictures from tools are passed on
        self.pending_pictures = []            # (tool, [picture ids]) to attach after this turn's tool results
        self.inbox = []                       # messages the user sent to guide this run, taken in at its next step
        self.closing = False
        self.steered = False
        self.worked = False                   # made a tool call in this run
        self.announce_nudges = 0
        self.longest_text = 0                 # the longest reply text this run, to tell whether an answer was written
        self.answer_noted = False             # sent back once a finish that left the answer out
        self.only_read = True                 # every tool call so far only read the folder (list, read, search)
        self.nudged_last = False              # the last reply was sent back; a second text reply in a row ends the run
        self.approvals = {}
        self.last_parts = None                # the parts of an edit the user chose in the latest decision
        self.last_answers = None              # the user's answers to the latest ask_user questions
        self.plan_mode = False                # look, but change nothing: edits are refused, and other commands always ask
        self.quiet = False                    # the model's words are not shown as they come (a summary for /compact)
        self.fail_streak = 0                  # tool calls in a row that failed (the turn guard)
        self.plan_calls = 0
        self.repeats = 0                      # the same call straight after itself
        self.guard_noted = {}
        self.stuck = None                     # why the turn guard stopped the run
        self.silent = False                   # it ended without a word to the user
        self.timeskip = None                  # the Time-skip job when this run works on a copy of the folder
        self.unattended = False               # a scheduled task or loop: asks are answered no at once
        self.refused = []                     # what such a run could not do without a yes
        self.changed = False                  # an edit or a command that looks like it writes files
        self.use_branch = False
        self.branch = None

    def _persist(self, ev):
        """Store the event in the session; consecutive token/thinking events are merged."""
        if ev["type"] in ("status", "tool_progress", "codex_usage"):       # moment-to-moment, not kept in the chat
            return
        stored = self.session["events"]
        if ev["type"] in ("token", "thinking") and stored and stored[-1]["type"] == ev["type"]:
            stored[-1]["text"] += ev["text"]
            stored[-1]["seq"] = ev["seq"]
        else:
            stored.append(dict(ev))

    def note_progress(self, text):
        """Takes the model's "3/8" progress note. Anything malformed is ignored, because it is only a hint."""
        parsed = parse_progress(text)
        if not parsed:
            return
        step, total = parsed
        if self.plan:                         # the plan's own length wins over a total the model guessed
            step, total = min(step, len(self.plan)), len(self.plan)
        previous = self.progress
        self.progress = (step, total, previous[2] if previous and previous[0] == step else time.time())

    def emit(self, type_, **data):
        APP.room.activity(self.owner)
        if type_ in ("token", "thinking"):
            APP.stream_chars += len(data.get("text") or "")
            APP.stream_at = time.time()
        if self.timeskip and type_ == "file_changed":
            data["copy"] = True               # made in the copy: its undo points belong to the copy
        with self.cond:
            self.seq += 1
            ev = {"type": type_, "seq": self.seq, **data}
            self._persist(ev)
            self.events.append(ev)
            self.cond.notify_all()

    def close(self, reason, summary=None):
        """Emit `done`. The session is saved and the workspace freed before the event becomes visible."""
        with self.cond:
            self.seq += 1
            ev = {"type": "done", "seq": self.seq, "reason": reason, "turns": self.turns}
            if summary:
                ev["summary"] = summary
            if self.stuck:
                ev["stuck"] = self.stuck
            if isinstance(self.session.get("goal"), dict):
                ev["goal"] = goal_public(self.session["goal"])
            if self.silent and reason == "answered":
                ev["silent"] = True
            elif reason == "answered" and self.worked and self.turns >= 2 and not self.plan_mode:
                # It worked with tools and ended on a written reply instead of finish: that reply is the run's summary,
                # and the page shows it as one, so the end of a long run does not look like one more message (2026-11-11).
                ev["summary_reply"] = True
            if self.plan_mode:          # the page offers to carry the plan out, or to edit it first
                last = next((m.get("content") for m in reversed(self.session["messages"]) if m.get("role") == "assistant" and m.get("content")), "")
                ev.update(plan_mode=True, plan_steps=plan_steps(self, summary or (last if isinstance(last, str) else "")))
            self._persist(ev)
            save_session(self.session)
            with APP.lock:
                for key in [k for k, v in APP.active.items() if v is self]:
                    del APP.active[key]
            note_active()
            self.events.append(ev)
            self.finished = True
            self.cond.notify_all()
        schedule_cleanup()
        APP.room.activity(self.owner)
        APP.room.changed()
        if self.owner:
            sync_publish(self.owner, "", "sessions", ws=self.ws["id"])

    def ask(self, **fields):
        """Emit an approval request and block until it is decided (or the run is stopped)."""
        aid = secrets.token_hex(4)
        ap = {"event": threading.Event(), "decision": None, "note": "", "kind": fields.get("kind"), "questions": fields.get("questions"),
              "plan": fields.get("plan") is True}
        with self.cond:
            self.approvals[aid] = ap
        self.emit("approval", approval_id=aid, **fields)
        if self.stop:
            self.decide(aid, "reject", "The run was stopped.")
        elif self.unattended and not (self.auto_allows(ap["kind"]) and not ap["plan"]):
            # A scheduled run: nobody is at the screen, so whatever needs a yes is not done, and the result says so.
            self.refused.append(describe_ask(fields))
            self.decide(aid, "reject", "Nobody was at the screen to approve this, so it was not done. Carry on without it, "
                                       "and say in your final answer what you could not do.")
        elif self.auto_allows(ap["kind"]) and not ap["plan"]:      # a toggle was switched on after the tool checked it
            self.decide(aid, "accept")
        ap["event"].wait()
        decision = ap["decision"] or "reject"
        self.last_parts = ap.get("parts")
        self.last_answers = ap.get("answers") if decision == "accept" else None
        self.emit("approval_resolved", approval_id=aid, decision=decision, **({"parts": ap["parts"]} if ap.get("parts") is not None else {}),
                  **({"answers": self.last_answers} if self.last_answers else {}))
        with self.cond:
            self.approvals.pop(aid, None)
        return decision, ap["note"]

    def decide(self, aid, decision, note="", parts=None, answers=None):
        with self.cond:
            ap = self.approvals.get(aid)
            if ap is None:
                return False
            if ap["decision"] is None:
                ap["decision"], ap["note"], ap["parts"], ap["answers"] = decision, note, parts, answers
            ap["event"].set()
            return True

    def auto_allows(self, kind):
        return (kind in ("edit", "skill") and self.auto_apply) or (kind in ("command", "mcp") and self.auto_commands)

    def set_auto(self, auto_apply, auto_commands):
        """Change the auto modes mid-run. Waiting approvals of a kind just switched on are accepted."""
        if isinstance(auto_apply, bool):
            self.auto_apply = auto_apply
        if isinstance(auto_commands, bool):
            self.auto_commands = auto_commands
        with self.cond:
            ids = [aid for aid, ap in self.approvals.items() if self.auto_allows(ap["kind"]) and not ap["plan"]]
        for aid in ids:
            self.decide(aid, "accept")

    def request_stop(self):
        self.stop = True
        with self.cond:
            ids = list(self.approvals)
        for aid in ids:
            self.decide(aid, "reject", "The run was stopped.")
        conn = self.conn
        if conn is not None and conn.sock is not None:
            try:
                conn.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


class Stopped(Exception):
    pass


class ModelError(Exception):
    pass


class NothingUsable(Exception):
    """The streaming response produced no content and no tool calls."""


# ---------------------------------------------------------------------------
# Agent: tools
# ---------------------------------------------------------------------------

TOOLS = [
    {"name": "list_files", "description": "List files and folders in the project as an indented tree.",
     "properties": {"path": {"type": "string", "description": "Folder, default '.'"},
                    "depth": {"type": "integer", "description": "Levels to show (1-5), default 2"}}, "required": []},
    {"name": "read_file", "description": "Read a text file with line numbers (at most 400 lines per call). Word, Excel, PowerPoint, OpenDocument and PDF files (.docx, .xlsx, .pptx, .odt, .ods, .pdf…) come back as text: headings, paragraphs, and tables and each sheet as Markdown tables.",
     "properties": {"path": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}},
     "required": ["path"]},
    {"name": "search", "description": "Search file contents (case-insensitive). Returns path:line: text, at most 100 matches.",
     "properties": {"query": {"type": "string"}, "regex": {"type": "boolean"},
                    "path": {"type": "string", "description": "Folder or file to search, default '.'"}},
     "required": ["query"]},
    {"name": "write_file", "description": "Create a file or fully replace its content.",
     "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]},
    {"name": "replace_in_file", "description": "Replace one exact, unique occurrence of old_text with new_text.",
     "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}},
     "required": ["path", "old_text", "new_text"]},
    {"name": "run", "description": "Run a shell command (bash -lc) in the project root and return its exit code and output.",
     "properties": {"command": {"type": "string"}}, "required": ["command"]},
    {"name": "finish", "description": "Finish the task and summarize what was done.",
     "properties": {"summary": {"type": "string"}}, "required": ["summary"]},
]

PROGRESS_PROPERTY = {"type": "string", "description": 'Required: where you are in your plan, as "current/total", such as "3/8".'}
TOOLS.append({"name": "delete_file", "description": "Delete one text file. Only when the user's request needs it. The user can restore it.",
              "properties": {"path": {"type": "string", "description": "File path relative to the project root"}}, "required": ["path"]})
TOOLS.append({"name": "save_picture", "description": "Save a picture that a tool returned into the project as a file, by the picture id "
              "the tool's result names. It works with the sandbox on, where the tool's own files cannot be reached.",
              "properties": {"picture": {"type": "string", "description": 'The id from the tool result, such as "3fa21c0b9d4e5f67.png"'},
                             "path": {"type": "string", "description": "File path relative to the project root, ending in the picture's type, such as assets/header.png"}},
              "required": ["picture", "path"]})
SKILL_KINDS = ("workflow", "debugging", "testing", "refactoring", "setup", "tooling", "language", "other")
TOOLS.append({"name": "save_skill", "description": "Save a way of working you will want again in other tasks and projects. The same name replaces the earlier version.",
              "properties": {"name": {"type": "string", "description": "Short and specific, such as 'Fix a flaky Go test'"},
                             "kind": {"type": "string", "enum": list(SKILL_KINDS), "description": "What kind of skill this is"},
                             "description": {"type": "string", "description": "One line: when to use it"},
                             "instructions": {"type": "string", "description": "The steps to follow, general enough for another project"}},
              "required": ["name", "kind", "description", "instructions"]})
TOOLS.append({"name": "use_skill", "description": "Load the full instructions of a saved skill by its name.",
              "properties": {"name": {"type": "string"}}, "required": ["name"]})
TOOLS.append({"name": "view_page", "description": "Open a page of a server this project runs (started with run, in the background) in a real browser, and see what a user would: "
                                             "title, visible text, JavaScript errors and failed requests. Optionally click, type or press keys first. Only that server can be reached.",
              "properties": {"port": {"type": "integer", "description": "The port the server listens on"},
                             "path": {"type": "string", "description": "Page path, default '/'"},
                             "actions": {"type": "array", "description": "Steps to run after the page loads, in order",
                                         "items": {"type": "object", "properties": {"click": {"type": "string", "description": "CSS selector to click"},
                                                                                    "type": {"type": "string", "description": "CSS selector of a field to fill"},
                                                                                    "text": {"type": "string", "description": "Text for type"},
                                                                                    "press": {"type": "string", "description": "Key to press, such as Enter or KeyW"},
                                                                                    "hold_ms": {"type": "integer", "description": "With press: hold the key down this long, at most 10000, as games need"},
                                                                                    "wait_ms": {"type": "integer", "description": "Wait afterwards, at most 10000"}}}},
                             "width": {"type": "integer", "description": "Window width in pixels, default 1280; 390 to see it as on a phone"},
                             "full_page": {"type": "boolean", "description": "true for a picture of the whole page, not only the first screen"}},
              "required": ["port"]})
TOOLS.append({"name": "open_preview", "description": "Show the user a page of a server this project runs: it opens in a Live tab in their editor. "
                                                "Use it when the user asks to see, open or preview the app. view_page is for your own checks; the user does not see it.",
              "properties": {"port": {"type": "integer", "description": "The port the server listens on"},
                             "path": {"type": "string", "description": "Page path, default '/'"}},
              "required": ["port"]})
TOOLS.append({"name": "write_document", "description": "Create a Word (.docx) or Excel (.xlsx) file, or replace one in full. "
              "Word: write content in Markdown: # headings, paragraphs, **bold**, *italic*, `code`, [links](https://...), - and 1. lists "
              "(indent two spaces per level), | tables |, > quotes, ``` code blocks, --- for a line and \\pagebreak alone on a line for a new page. "
              "Excel: give sheets, each with a name and rows (a list of rows, each a list of cells); numbers stay numbers, text starting with = "
              "is a formula such as =SUM(B2:B9), the first row is a bold, frozen header unless header is false. "
              "Replacing a file rewrites it from what you give, so formatting it had beyond these is lost: read it with read_file first.",
              "properties": {"path": {"type": "string", "description": "File path relative to the project root, ending in .docx or .xlsx"},
                             "content": {"type": "string", "description": "Word: the whole document in Markdown"},
                             "sheets": {"type": "array", "description": "Excel: the sheets in order",
                                        "items": {"type": "object", "properties": {
                                            "name": {"type": "string", "description": "Sheet name, at most 31 characters"},
                                            "rows": {"type": "array", "items": {"type": "array", "items": {}}, "description": "Rows of cells: text, numbers, true/false, null for empty, or =FORMULA"},
                                            "header": {"type": "boolean", "description": "First row is a header (default true)"},
                                            "widths": {"type": "array", "items": {"type": "number"}, "description": "Column widths in characters (optional)"}},
                                            "required": ["name", "rows"]}},
                             "title": {"type": "string", "description": "Optional: the document's title (Word shows it at the top)"}},
              "required": ["path"]})
MAX_PLAN_STEPS, MAX_PLAN_STEP = 20, 120
TOOLS.append({"name": "plan", "description": "Show the user your plan as a checklist, before you start a task with more than two steps. Call it again if the plan changes.",
              "properties": {"steps": {"type": "array", "items": {"type": "string"}, "description": "Short step names, in order, in the user's language"}},
              "required": ["steps"]})
MAX_QUESTIONS, MAX_OPTIONS = 4, 6
TOOLS.append({"name": "ask_user", "description": "Ask the user one to four questions, each with two to six short choices, when you need a decision only they "
                                             "can make: which of several ways to go, what they prefer, or details missing from their request. They can also type their own "
                                             "answer. Do not ask what you can find out yourself by looking at the project.",
              "properties": {"questions": {"type": "array", "description": "1 to 4 questions",
                                           "items": {"type": "object", "properties": {
                                               "question": {"type": "string", "description": "The question, in the user's language"},
                                               "header": {"type": "string", "description": "Optional label of one to three words"},
                                               "options": {"type": "array", "description": "2 to 6 choices",
                                                           "items": {"type": "object", "properties": {"label": {"type": "string", "description": "A few words"},
                                                                                                      "description": {"type": "string", "description": "Optional: what this choice means"}},
                                                                     "required": ["label"]}},
                                               "multi": {"type": "boolean", "description": "true when several choices can be picked"}},
                                               "required": ["question", "options"]}}},
              "required": ["questions"]})
TOOL_SCHEMAS = [{"type": "function", "function": {
    "name": t["name"], "description": t["description"],
    "parameters": {"type": "object", "properties": {**t["properties"], "progress": PROGRESS_PROPERTY},
                   # Listed as required so models fill it in. A missing or malformed value is simply ignored.
                   "required": t["required"] + ([] if t["name"] == "finish" else ["progress"])}}} for t in TOOLS]


def parse_progress(text):
    """(step, total) from a "3/8" progress note, or None."""
    match = re.fullmatch(r"\s*(\d{1,4})\s*/\s*(\d{1,4})\s*", text) if isinstance(text, str) else None
    return (int(match[1]), int(match[2])) if match and 1 <= int(match[1]) <= int(match[2]) else None


def need_str(args, key):
    value = args.get(key)
    if not isinstance(value, str):
        raise ApiError(400, f"argument '{key}' (string) is required")
    return value


def tool_list_files(run, args):
    start = resolve(run.ws, args.get("path") or ".")
    if not os.path.isdir(start):
        raise ApiError(404, "not a directory")
    lines, truncated = tree_lines(start, to_int(args.get("depth"), 2, 1, 5), 500)
    out = "\n".join(lines) or "(empty folder)"
    return True, f"Listed {len(lines)} entries", out + ("\n[listing truncated]" if truncated else "")


MAX_READ_LARGE = 200 * 1024 * 1024     # text files past MAX_TEXT_BYTES are read by line range, without loading them whole


def read_large_lines(rel, path, args):
    """read_file for a large text file (a CSV export, a log): the asked lines, read as a stream."""
    with open(path, "rb") as fh:
        if b"\0" in fh.read(8192):
            raise ApiError(400, "file is binary")
        fh.seek(0)
        start = to_int(args.get("start_line"), 1, lo=1)
        end = min(to_int(args.get("end_line"), start + 399, lo=start), start + 399)
        shown, total = [], 0
        for total, raw in enumerate(fh, 1):
            if start <= total <= end:
                shown.append(f"{total:>6}\t{raw.decode('utf-8', 'replace').rstrip(chr(13) + chr(10)).lstrip(chr(65279))}")
    if start > max(total, 1):
        raise ApiError(400, f"start_line is beyond the end of the file ({total} lines)")
    end = min(end, total)
    remaining = total - end
    note = f"\n[{remaining} more lines remain; continue with start_line={end + 1}, or use a script for the whole file]" if remaining > 0 else ""
    return True, f"Read {rel} (lines {start}-{end} of {total})", "\n".join(shown) + note


def tool_read_file(run, args):
    rel = need_str(args, "path")
    path = resolve(run.ws, rel)
    if not os.path.isfile(path):
        raise ApiError(404, f"file not found: {rel}")
    if is_document(path):
        auth = getattr(APP, "auth", None)
        owner = auth.users.get(run.owner) if auth else None
        text = read_document_file(path, allow_tools=(owner or {}).get("role") == "admin")
    else:
        text, _ = read_text_file(path)
        if text is None and os.path.getsize(path) <= MAX_READ_LARGE:
            return read_large_lines(rel, path, args)
    if text is None:
        raise ApiError(400, f"file is binary or larger than {MAX_READ_LARGE // 1048576} MB")
    lines = text.splitlines()
    start = to_int(args.get("start_line"), 1, lo=1)
    if start > max(len(lines), 1):
        raise ApiError(400, f"start_line is beyond the end of the file ({len(lines)} lines)")
    end = min(to_int(args.get("end_line"), start + 399, lo=start), start + 399, len(lines))
    body = "\n".join(f"{n:>6}\t{lines[n - 1]}" for n in range(start, end + 1))
    remaining = len(lines) - end
    note = f"\n[{remaining} more lines remain; continue with start_line={end + 1}]" if remaining > 0 else ""
    return True, f"Read {rel} (lines {start}-{end} of {len(lines)})", body + note


def tool_search(run, args):
    query = need_str(args, "query")
    start = resolve(run.ws, args.get("path") or ".")
    if not os.path.exists(start):
        raise ApiError(404, "path not found")
    matches, truncated = search_tree(run.ws["path"], start, query, bool(args.get("regex")), False, 100, 5.0)
    out = "\n".join(f"{m['path']}:{m['line']}: {m['text']}" for m in matches) or "No matches."
    return True, f"{len(matches)} matches", out + ("\n[results truncated]" if truncated else "")


MAX_HUNK_LINES = 1500        # the comparison gets slow beyond this, and the run waits for it


def edit_hunks(old, new):
    """The separate changes between two texts, as (old start, old end, new start, new end) line ranges."""
    a, b = old.splitlines(keepends=True), new.splitlines(keepends=True)
    if max(len(a), len(b)) > MAX_HUNK_LINES:
        return a, b, []
    return a, b, [op[1:] for op in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if op[0] != "equal"]


def hunks_for_approval(old, new):
    """What the approval card shows, so the user can pick parts. Only worth offering for two or more changes."""
    a, b, hunks = edit_hunks(old or "", new)
    if not old or len(hunks) < 2:
        return None
    return [{"line": i1 + 1, "old": "".join(a[i1:i2]), "new": "".join(b[j1:j2])} for i1, i2, j1, j2 in hunks]


MAX_DIFF_ROWS = 1500
MAX_DIFF_LINE = 2000


def describe_change(old, new):
    """What an edit did, for the action card: line counts and rows of [kind, old line, new line, text]."""
    a, b, hunks = edit_hunks(old or "", new or "")
    if max(len(a), len(b)) > MAX_HUNK_LINES:
        return {"added": len(b), "removed": len(a), "rows": [], "too_large": True}
    added = sum(j2 - j1 for _, _, j1, j2 in hunks)
    removed = sum(i2 - i1 for i1, i2, _, _ in hunks)
    rows = []
    clean = lambda line: line.rstrip("\r\n")[:MAX_DIFF_LINE]
    for group in difflib.SequenceMatcher(None, a, b, autojunk=False).get_grouped_opcodes(3):
        if rows:
            rows.append(["sep", None, None, ""])
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                rows += [["ctx", i + 1, j + 1, clean(a[i])] for i, j in zip(range(i1, i2), range(j1, j2))]
                continue
            rows += [["del", i + 1, None, clean(a[i])] for i in range(i1, i2)]
            rows += [["add", None, j + 1, clean(b[j])] for j in range(j1, j2)]
    change = {"added": added, "removed": removed, "rows": rows[:MAX_DIFF_ROWS]}
    if len(rows) > MAX_DIFF_ROWS:
        change["truncated"] = True
    return change


def merge_hunks(old, new, chosen):
    """The old text with only the chosen hunks applied."""
    a, b, hunks = edit_hunks(old, new)
    out, at = [], 0
    for index, (i1, i2, j1, j2) in enumerate(hunks):
        out += a[at:i1]
        out += b[j1:j2] if index in chosen else a[i1:i2]
        at = i2
    return "".join(out + a[at:])


def apply_edit(run, rel, path, old, new):
    """Ask for approval unless auto-apply is on, then write with a checkpoint."""
    if old == new:
        return True, "No changes", "No changes: the new content is identical to the current content."
    summary, result = f"Applied {rel}", "applied"
    if not run.auto_apply:
        extra = {"hunks": hunks} if (hunks := hunks_for_approval(old, new)) else {}
        run.last_parts = None
        decision, note = run.ask(kind="edit", path=rel, old=old or "", new=new, change=describe_change(old, new), **extra)
        if decision != "accept":
            return False, f"Rejected {rel}", "The user rejected this change." + (f" Note: {note}" if note else "")
        if run.last_parts is not None and extra:
            chosen = {i for i in run.last_parts if 0 <= i < len(extra["hunks"])}
            if not chosen:
                return False, f"Rejected {rel}", "The user rejected this change." + (f" Note: {note}" if note else "")
            if len(chosen) < len(extra["hunks"]):
                new = merge_hunks(old, new, chosen)
                summary = f"Partly applied {rel}"
                result = (f"Only part of this change was applied: the user accepted {len(chosen)} of {len(extra['hunks'])} parts and rejected the rest. "
                          "Read the file before editing it again." + (f" Note: {note}" if note else ""))
    cid = make_checkpoint(run.ws, {rel: old})
    write_bytes(path, new.encode("utf-8"))
    run.changed = True
    run.emit("file_changed", path=rel, checkpoint=cid, action="create" if old is None else "edit", change=describe_change(old, new))
    return True, summary, result


def tool_delete_file(run, args):
    rel = need_str(args, "path")
    path = resolve(run.ws, rel, nofollow=True)
    rel = rel_posix(path, run.ws["path"])
    refuse_git_folder(rel)
    if os.path.islink(path):
        raise ApiError(400, "that path is a link, which this tool does not delete")
    old = current_text(path)       # refuses folders, and files too big or binary to restore from an undo point
    if old is None:
        raise ApiError(404, f"file not found: {rel}")
    change = describe_change(old, "")
    if not run.auto_apply:
        decision, note = run.ask(kind="edit", action="delete", path=rel, old=old, new="", change=change)
        if decision != "accept":
            return False, f"Kept {rel}", "The user rejected deleting this file." + (f" Note: {note}" if note else "")
    cid = make_checkpoint(run.ws, {rel: old})
    move_to_trash(run.ws, path)
    run.changed = True
    run.emit("file_changed", path=rel, checkpoint=cid, action="delete", change=change)
    return True, f"Deleted {rel}", "deleted"


PICTURE_ID = re.compile(r"[0-9a-f]{16}\.(png|jpg|webp|gif)")


def tool_save_picture(run, args):
    picture, rel = need_str(args, "picture").strip(), need_str(args, "path")
    source = os.path.join(attachments_dir(run.session["id"]), picture)
    if not PICTURE_ID.fullmatch(picture) or not os.path.isfile(source):
        raise ApiError(404, f"no picture {picture!r} in this chat; use the id a tool result gave")
    ext = picture.rsplit(".", 1)[1]
    if os.path.splitext(rel)[1].lower().lstrip(".") not in ({ext, "jpeg"} if ext == "jpg" else {ext}):
        raise ApiError(400, f"the picture is a .{ext} file, so the path must end in .{ext}")
    path = resolve(run.ws, rel, nofollow=True)
    rel = rel_posix(path, run.ws["path"])
    refuse_git_folder(rel)
    if os.path.isdir(path) or os.path.islink(path):
        raise ApiError(400, f"{rel} is a folder or a link")
    size = os.path.getsize(source)
    replacing = os.path.exists(path)
    note = f"[picture {picture}, {max(1, size // 1024)} KB" + (", replacing the file there]" if replacing else "]")
    change = describe_change("", note)
    if not run.auto_apply:
        decision, reason = run.ask(kind="edit", action="picture", picture=picture, path=rel, old="", new=note, change=change)
        if decision != "accept":
            return False, f"Did not save {rel}", "The user rejected saving this picture." + (f" Note: {reason}" if reason else "")
    if replacing:
        move_to_trash(run.ws, path)               # the earlier file stays restorable from the trash
    with open(source, "rb") as fh:
        write_bytes(path, fh.read())
    cid = make_checkpoint(run.ws, {rel: None})    # undo removes the picture again
    run.changed = True
    run.emit("file_changed", path=rel, checkpoint=cid, action="picture", change=change)
    return True, f"Saved {rel}", f"Saved the picture as {rel} ({max(1, size // 1024)} KB)." + (" The earlier file is in the trash." if replacing else "")


def tool_write_document(run, args):
    rel = need_str(args, "path")
    path = resolve(run.ws, rel, nofollow=True)
    rel = rel_posix(path, run.ws["path"])
    refuse_git_folder(rel)
    if os.path.isdir(path) or os.path.islink(path):
        raise ApiError(400, f"{rel} is a folder or a link")
    data, kind = build_document(rel, args)
    new = document_text(data, os.path.basename(rel))           # what the file reads as: the approval's view, and a check that it opens
    new = new.split("\n", 1)[1] if new.startswith("[") else new
    old, replacing = "", os.path.exists(path)
    if replacing:
        try:
            old = read_document_file(path).split("\n", 1)[1]
        except (ApiError, OSError, IndexError):
            old = f"[the {kind} file there now cannot be read]"
    change = describe_change(old, new)
    if not run.auto_apply:
        decision, reason = run.ask(kind="edit", action="document", path=rel, old=old, new=new, change=change)
        if decision != "accept":
            return False, f"Did not save {rel}", "The user rejected this document." + (f" Note: {reason}" if reason else "")
    if replacing:
        move_to_trash(run.ws, path)               # the earlier file stays restorable from the trash
    write_bytes(path, data)
    cid = make_checkpoint(run.ws, {rel: None})    # undo removes the new file
    run.changed = True
    run.emit("file_changed", path=rel, checkpoint=cid, action="document", change=change)
    size = max(1, len(data) // 1024)
    return with_mixed_note((True, f"Saved {rel}", f"Saved the {kind} file {rel} ({size} KB)." + (" The earlier file is in the trash." if replacing else "")
                            + " It reads back as:\n" + new[:1500] + ("\n[…]" if len(new) > 1500 else "")), rel, new, old)


def skills_path(username):
    return os.path.join(APP.data_dir, "skills", hashlib.sha1(username.encode()).hexdigest() + ".json")


def skills_for(username):
    """A user's saved skills, newest change first. Skills belong to the user, so they are there in every folder."""
    with APP.skills_lock:
        skills = read_json(skills_path(username), {})
    return sorted(skills.values(), key=lambda skill: skill["updated"], reverse=True)


def change_skills(username, change):
    with APP.skills_lock:
        skills = read_json(skills_path(username), {})
        result = change(skills)
        os.makedirs(os.path.dirname(skills_path(username)), exist_ok=True)
        write_json_atomic(skills_path(username), skills, private=True)
    return result


def skill_fields(args):
    """A skill's checked fields, the same rules whether the model or the user writes it."""
    name = need_str(args, "name").strip()
    kind, description, instructions = args.get("kind"), need_str(args, "description").strip(), need_str(args, "instructions").strip()
    if kind not in SKILL_KINDS:
        raise ApiError(400, f"kind must be one of: {', '.join(SKILL_KINDS)}")
    if not 2 <= len(name) <= MAX_SKILL_NAME or "\n" in name:
        raise ApiError(400, f"name must be one line of 2 to {MAX_SKILL_NAME} characters")
    if not description or len(description) > MAX_SKILL_DESCRIPTION:
        raise ApiError(400, f"description must be 1 to {MAX_SKILL_DESCRIPTION} characters")
    if not instructions or len(instructions) > MAX_SKILL_TEXT:
        raise ApiError(400, f"instructions must be 1 to {MAX_SKILL_TEXT} characters")
    return {"name": name, "skill_kind": kind, "description": description, "instructions": instructions}


def tool_save_skill(run, args):
    fields = skill_fields(args)
    name, kind = fields["name"], fields["skill_kind"]
    # A skill reaches later runs in every folder, so it needs the same permission as an edit.
    if not run.auto_apply:
        decision, note = run.ask(kind="skill", **fields)
        if decision != "accept":
            return False, "Skill not saved", "The user rejected saving this skill." + (f" Note: {note}" if note else "")

    def store(skills):
        key = name.casefold()
        old = skills.get(key)
        if not old and len(skills) >= MAX_SKILLS:
            raise ApiError(400, f"there are already {MAX_SKILLS} skills; replace one by saving under its name")
        now = time.time()
        skills[key] = {**fields, "created": old["created"] if old else now, "updated": now, "uses": old["uses"] if old else 0,
                       "folder": os.path.basename(run.ws["path"])}
        return bool(old)
    updated = change_skills(run.owner, store)
    run.emit("skill_saved", updated=updated, **fields)
    sync_publish(run.owner, "", "skills")
    return True, f"Saved {kind} skill", f"Saved the {kind} skill '{name}'. It is available in later runs and in other projects."


def tool_use_skill(run, args):
    name = need_str(args, "name").strip()
    # The prompt lists skills as "Name (kind): description", and models copy the kind too.
    name = re.sub(r"\s*\((?:%s)\)\s*:?$" % "|".join(SKILL_KINDS), "", name, flags=re.I).strip() or name

    def load(skills):
        skill = skills.get(name.casefold())
        if skill:
            skill["uses"] += 1
            skill["last_used"] = time.time()          # for "Recently used" in the Skills view
        return skill, sorted(s["name"] for s in skills.values())
    skill, names = change_skills(run.owner, load)
    if not skill:
        raise ApiError(404, f"no skill named '{name}'. Saved skills: {', '.join(names) or 'none'}")
    return True, f"Loaded {skill['skill_kind']} skill", f"Skill '{skill['name']}' ({skill['skill_kind']}): {skill['description']}\n\n{skill['instructions']}"


def tool_plan(run, args):
    steps = args.get("steps")
    if isinstance(steps, str):          # some models send one string with a step per line
        steps = [line for line in steps.splitlines()]
    if not isinstance(steps, list) or not all(isinstance(s, str) for s in steps):
        raise ApiError(400, "steps must be a list of short strings")
    steps = [re.sub(r"^\s*(\d+[.)]|[-*•])\s*", "", s).strip()[:MAX_PLAN_STEP] for s in steps]
    steps = [s for s in steps if s]
    if not 1 <= len(steps) <= MAX_PLAN_STEPS:
        raise ApiError(400, f"a plan needs 1 to {MAX_PLAN_STEPS} steps")
    # The card shows steps as written, so a step with a stray script is sent back before anyone sees it: Chinese, Japanese,
    # Korean or Cyrillic characters («loweringduring朗读»), or a word mixing Greek and Latin letters («γreek-check», also
    # joined by an underscore, «έλεγχος_CLEAR»).
    if APP.cfg.get("script_check", True):
        steps = [repair_mixed(step) for step in steps]
    bad = plan_script_problems(steps)
    if bad:
        return False, "Plan sent back", ("Studio note: the plan was not shown, because " + "; ".join(f"step {i} has «{w}»" for i, w in bad[:5])
                                         + ". Call plan again with each word in one language and one alphabet, and no Chinese, "
                                           "Japanese, Korean or Cyrillic characters.")
    revised = run.plan is not None
    run.plan, run.plan_noted = steps, False
    run.emit("plan", steps=steps, revised=revised)
    reply = (f"The user now sees your {'revised ' if revised else ''}plan of {len(steps)} steps. "
             f'Set progress to the step you are working on, such as "1/{len(steps)}".')
    return True, f"Plan: {len(steps)} steps", reply


def plan_script_problems(steps):
    """(step number, word) for each word in a plan step with foreign characters or mixed Greek and Latin letters."""
    found = []
    for i, step in enumerate(steps, 1):
        prose = NOT_PROSE_RE.sub(" ", step)
        for word in re.findall(r"[^\W\d]+", prose):           # underscores kept: «έλεγχος_CLEAR» is one word here
            if (FOREIGN_RE.search(word) or (len(word) >= 4 and LATIN_RE.search(word) and GREEK_RE.search(word))) and (i, word) not in found:
                found.append((i, word))
    return found


def clean_questions(raw):
    """The questions of an ask_user call, checked and shortened: [{question, header, options: [{label, description}], multi}]."""
    if isinstance(raw, dict):           # one question sent on its own
        raw = [raw]
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_QUESTIONS:
        raise ApiError(400, f"questions must be a list of 1 to {MAX_QUESTIONS} questions")
    out = []
    for q in raw:
        if not isinstance(q, dict) or not isinstance(q.get("question"), str) or not q["question"].strip():
            raise ApiError(400, "each question needs its text in 'question'")
        options = q.get("options")
        if isinstance(options, str):    # one string with a choice per line
            options = [line for line in options.splitlines() if line.strip()]
        options = [{"label": o} if isinstance(o, str) else o for o in options] if isinstance(options, list) else None
        if not options or not all(isinstance(o, dict) and isinstance(o.get("label"), str) and o["label"].strip() for o in options):
            raise ApiError(400, "each question needs 'options': a list of choices, each with a 'label'")
        labels = []
        for o in options:
            label = re.sub(r"^\s*(\d+[.)]|[-*•])\s*", "", o["label"]).strip()[:80]
            if label and label not in [x["label"] for x in labels]:
                labels.append({"label": label, "description": o["description"].strip()[:200] if isinstance(o.get("description"), str) else ""})
        if not 2 <= len(labels) <= MAX_OPTIONS:
            raise ApiError(400, f"each question needs 2 to {MAX_OPTIONS} different choices")
        out.append({"question": q["question"].strip()[:400], "header": q["header"].strip()[:40] if isinstance(q.get("header"), str) else "",
                    "options": labels, "multi": q.get("multi") is True})
    return out


def clean_answers(questions, raw):
    """The user's answers, checked against the questions: [{choices: [labels], text}], one per question."""
    if not isinstance(raw, list) or len(raw) != len(questions):
        raise ApiError(400, "answers must hold one answer per question")
    out = []
    for q, a in zip(questions, raw):
        if not isinstance(a, dict):
            raise ApiError(400, "each answer must be an object")
        choices, text = a.get("choices") or [], a.get("text") or ""
        if not isinstance(choices, list) or not all(isinstance(c, str) for c in choices) or not isinstance(text, str):
            raise ApiError(400, "an answer has 'choices' (a list of labels) and 'text'")
        offered = [o["label"] for o in q["options"]]
        if any(c not in offered for c in choices) or len(set(choices)) != len(choices) or (len(choices) > 1 and not q["multi"]):
            raise ApiError(400, "answers may only pick the choices offered, and one unless the question allows several")
        if not choices and not text.strip():
            raise ApiError(400, "every question needs an answer: a choice or some text")
        out.append({"choices": choices, "text": text.strip()[:2000]})
    return out


def tool_ask_user(run, args):
    questions = clean_questions(args.get("questions", args if "question" in args else None))
    if APP.cfg.get("script_check", True):
        for q in questions:
            q["question"], q["header"] = repair_mixed(q["question"]), repair_mixed(q.get("header") or "") or q.get("header")
            for o in q["options"]:
                o["label"], o["description"] = repair_mixed(o["label"]), repair_mixed(o.get("description") or "") or o.get("description")
        shown = "\n".join(part for q in questions
                          for part in [q["question"], q.get("header") or ""] + [o["label"] + " " + (o.get("description") or "") for o in q["options"]])
        bad = mixed_words(shown)
        if bad:        # the card is shown as written, so a typo like «_potάμι» is fixed before the user sees it
            return False, "Questions not shown", "The questions were not shown to the user. " + mixed_note(bad, "they have") + " Then call ask_user again."
    decision, note = run.ask(kind="question", questions=questions)
    answers = run.last_answers
    if decision != "accept" or not answers:
        why = "The run was stopped." if run.stop else "The user skipped the questions."
        return True, "Not answered", why + " Carry on with your best judgment and say what you assumed, or ask in your reply."
    lines = []
    for i, (q, a) in enumerate(zip(questions, answers), 1):
        given = ", ".join(a["choices"]) + ("; " if a["choices"] and a["text"] else "") + (f"in their own words: {a['text']}" if a["text"] else "")
        lines.append(f"{i}. {q['question']}\n   Answer: {given}")
    return True, "Answered", "The user answered:\n" + "\n".join(lines)


TOOLS_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), "tools")
VIEW_PAGE_SCRIPT = os.path.join(TOOLS_DIR, "view_page.mjs")
MAX_PAGE_ACTIONS = 20
MAX_LIVE_SHOTS = 200


def tool_view_page(run, args):
    port = to_int(args.get("port"), 0)
    path = args.get("path") if isinstance(args.get("path"), str) and args.get("path").strip() else "/"
    path = path.strip() if path.strip().startswith("/") else "/" + path.strip()
    actions = args.get("actions") or []
    width = to_int(args.get("width"), 1280)
    if not 320 <= width <= 2560:
        raise ApiError(400, "width must be 320 to 2560 pixels (390 is a phone)")
    full_page = args.get("full_page") is True
    if not 1 <= port <= 65535:
        raise ApiError(400, "port must be a number from 1 to 65535")
    if not isinstance(actions, list) or len(actions) > MAX_PAGE_ACTIONS or not all(isinstance(a, dict) and set(a) <= {"click", "type", "text", "press", "hold_ms", "wait_ms"} for a in actions):
        raise ApiError(400, f"actions must be a list of at most {MAX_PAGE_ACTIONS} steps, each with click, type (and text), press (and hold_ms) or wait_ms")
    steps = [f"{k} {a[k]}" + (f" for {a['hold_ms']} ms" if k == "press" and a.get("hold_ms") else "") for a in actions for k in ("click", "type", "press") if a.get(k)]
    if not run.auto_commands:
        decision, note = run.ask(kind="command", command=f"view_page http://localhost:{port}{path}" + "".join(f"\n  then {s}" for s in steps))
        if decision != "accept":
            return False, "Page not opened", "The user rejected opening this page." + (f" Note: {note}" if note else "")
    if not sandboxed(run.ws) and port not in live_ports(run.ws):
        raise ApiError(400, f"no program started in this project listens on port {port}; start the server in the background first")
    token = make_live_token(run.ws, run.owner, port)
    url = f"http://127.0.0.1:{APP.cfg['port']}/live/{token}{path}"
    shots = os.path.join(APP.data_dir, "live_shots", run.owner or "_")
    os.makedirs(shots, exist_ok=True)
    shot_id = secrets.token_hex(8)
    cdp = agent_browser_cdp(run)
    view = getattr(APP, "browser", None) if cdp else None
    if view:
        view.checking(True)
    try:
        seen = render_page(url, actions, os.path.join(shots, shot_id + ".png"), width, full_page, cdp=cdp)
    finally:
        if view:
            view.checking(False)
    if seen is None:                  # no Chromium here: the page's HTML as text, without its scripts
        seen = plain_page(token, path)
    if seen.get("shared") and not getattr(run, "browser_shown", False):
        run.browser_shown = True
        run.emit("open_browser")          # the person watches the check live in the Browser tab
    for old in sorted(os.listdir(shots), key=lambda n: os.path.getmtime(os.path.join(shots, n)))[:-MAX_LIVE_SHOTS]:
        os.remove(os.path.join(shots, old))
    has_shot = os.path.isfile(os.path.join(shots, shot_id + ".png"))
    run.emit("page_view", port=port, path=seen.get("url") or path, title=seen.get("title") or "", status=seen.get("status") or 0, width=width,
             errors=page_errors(seen), failed=len(seen.get("failed") or []), shot=shot_id if has_shot else None, rendered=seen.get("rendered", True))
    lines = [f"Page http://localhost:{port}{seen.get('url') or path} at {width} pixels wide: HTTP {seen.get('status') or 'no answer'}, title {seen.get('title')!r}"]
    if not seen.get("rendered", True):
        lines.append("Chromium is not available, so this is the page's HTML as text; its scripts did not run.")
    for note in seen.get("notes") or []:
        lines.append("Note: " + note)
    for label, key in (("Steps", "actions"), ("JavaScript errors and warnings", "console"), ("Failed requests", "failed"), ("Blocked (outside this server)", "blocked")):
        items = seen.get(key) or []
        if items or key == "console":
            lines.append(f"{label}: " + ("none" if not items else "\n  " + "\n  ".join(items)))
    lines.append("Visible text:\n" + (seen.get("text") or "(empty)"))
    if has_shot and getattr(run, "vision", False) and getattr(run, "session", None):
        # A model that can see gets the screenshot too, after the tool results, so it can judge the layout, not only the text.
        try:
            data = open(os.path.join(shots, shot_id + ".png"), "rb").read()
        except OSError:
            data = b""
        if 0 < len(data) <= MAX_IMAGE_BYTES:
            stored = store_images(run.session["id"], [{"kind": "image", "name": "view_page.png", "mime": "image/png", "data": data}])
            run.pending_pictures.append(("view_page", [p["id"] for p in stored]))
            lines.insert(1, "The screenshot is attached for you to look at" + (" (the whole page)." if full_page else " (the first screen)."))
    status = seen.get("status") or 0
    errors, warnings = page_errors(seen), len(seen.get("console") or []) - page_errors(seen)
    counts = ", ".join(f"{n} {word}{'' if n == 1 else 's'}" for n, word in ((errors, "error"), (warnings, "warning")) if n)
    return 200 <= status < 400, f"HTTP {status or 'no answer'}" + (f", {counts}" if counts else ""), "\n".join(lines)


def page_errors(seen):
    """Console lines that are errors; warnings (deprecations, the GPU's notes) are reported but not counted as errors."""
    return sum(1 for line in seen.get("console") or [] if not line.startswith("warning:"))


def tool_open_preview(run, args):
    port = to_int(args.get("port"), 0)
    path = args.get("path") if isinstance(args.get("path"), str) and args.get("path").strip() else "/"
    path = path.strip() if path.strip().startswith("/") else "/" + path.strip()
    if not 1 <= port <= 65535:
        raise ApiError(400, "port must be a number from 1 to 65535")
    if port not in live_ports(run.ws):
        raise ApiError(400, f"nothing in this project listens on port {port}; start the server in the background first")
    run.emit("open_live", port=port, path=path)
    return True, f"Opened :{port}{path}", (f"The page http://localhost:{port}{path} is open in a Live tab in the user's editor, if their Studio window is open. "
                                          "It shows the server as it runs, so keep the server running while they look at it.")


def agent_browser_cdp(run):
    """The DevTools address of the agent's own Chromium (the browser tool server), when it runs and this run has the
    browser tools, so view_page opens its page there and the person watches it in the Browser tab; else None."""
    if not any(entry.get("server") == "browser" for entry in (getattr(run, "mcp_tools", None) or {}).values()):
        return None
    session = read_json(browser_view().session_file(), None) if getattr(APP, "mcp", None) else None
    cdp = (session or {}).get("cdp") or ""
    if not cdp.startswith("http://127.0.0.1:"):
        return None
    try:
        urllib.request.urlopen(cdp + "/json/version", timeout=2).read()
    except (OSError, ValueError):
        return None
    return cdp


def render_page(url, actions, shot, width=1280, full_page=False, cdp=None):
    """What Chromium shows at url, or None when Node or Playwright is missing. With cdp, in the agent's own Chromium."""
    if not shutil.which("node") or not os.path.isfile(VIEW_PAGE_SCRIPT):
        return None
    try:
        result = subprocess.run(["node", VIEW_PAGE_SCRIPT, json.dumps({"url": url, "actions": actions, "shot": shot, "width": width, "full_page": full_page,
                                                                       **({"cdp": cdp} if cdp else {})})],
                                capture_output=True, text=True, timeout=90)
    except subprocess.TimeoutExpired:
        return {"status": 0, "console": ["the browser took longer than 90 s"], "text": ""}
    if result.returncode != 0 and "Cannot find module" in result.stderr:
        return None
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"status": 0, "console": ["the browser failed: " + (result.stderr.strip().splitlines() or ["no output"])[-1][:300]], "text": ""}


def plain_page(token, path):
    entry = live_entry(token)
    try:
        resp, close = live_fetch(entry, "GET", path, [("Accept", "text/html")], b"")
    except LiveError as exc:
        return {"status": 0, "console": [str(exc)], "text": "", "rendered": False}
    try:
        html_text = resp.read(2 * 1024 * 1024).decode("utf-8", "replace")
    finally:
        close()
    title = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.S | re.I)
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", html_text)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    return {"status": resp.status, "title": html.unescape(title[1].strip()) if title else "", "text": re.sub(r"[ \t]*\n\s*", "\n", re.sub(r"[ \t]+", " ", text)).strip()[:8000], "rendered": False}


@route("GET", "/api/live/shot")
def live_shot(req):
    shot = req.query.get("id") or ""
    path = os.path.join(APP.data_dir, "live_shots", req.current_user["username"], shot + ".png")
    if not re.fullmatch(r"[0-9a-f]{16}", shot) or not os.path.isfile(path):
        raise ApiError(404, "not found")
    with open(path, "rb") as fh:
        data = fh.read()
    req.send_response(200)
    req.send_header("Content-Type", "image/png")
    req.send_header("Content-Length", str(len(data)))
    req.send_header("Cache-Control", "private, max-age=86400")
    req.end_headers()
    req.wfile.write(data)


def build_skills_prompt(run):
    shown = [f"- {s['name']} ({s['skill_kind']}): {s['description']}" for s in skills_for(run.owner)[:50]]
    return SKILLS_PROMPT + "\n" + ("Saved skills:\n" + "\n".join(shown) if shown else "No skills are saved yet.")


def refuse_git_folder(rel):
    """Git settings inside .git can run programs, so the agent's file tools stay out of it."""
    if ".git" in rel.split("/"):
        raise ApiError(403, "the agent's file tools cannot change the .git folder; use git commands instead")


def current_text(path):
    """Existing text of a file (None if absent); raises if it is not editable text."""
    if not os.path.lexists(path):
        return None
    if os.path.isdir(path):
        raise ApiError(400, "path is a directory")
    text, _ = read_text_file(path)
    if text is None:
        raise ApiError(400, "file is binary or larger than 2 MB")
    return text


def tool_write_file(run, args):
    rel, content = need_str(args, "path"), need_str(args, "content")
    path = resolve(run.ws, rel)
    refuse_git_folder(rel_posix(path, run.ws["path"]))
    old = current_text(path)
    return with_check_note(with_mixed_note(apply_edit(run, rel_posix(path, run.ws["path"]), path, old, content), rel, content, old), rel, path, old)


# Quick checks after an edit (config `edit_checks`): only parsers, never the project's own code, so a typo shows in the
# edit's result instead of at the next run. Each one gives (line, message) or None.
CHECK_SKIP_JSON = re.compile(r"(^|/)(\.vscode/|tsconfig[^/]*\.json$|jsconfig[^/]*\.json$|devcontainer\.json$|\.eslintrc\.json$|[^/]*\.jsonc$)")
CHECK_MAX_BYTES = 1_000_000
CHECK_TIMEOUT_S = 5


def check_python(text, rel):
    try:
        compile(text, rel, "exec", dont_inherit=True)
    except SyntaxError as e:          # IndentationError and TabError too
        return e.lineno or 0, f"{type(e).__name__}: {e.msg}"
    except (ValueError, RecursionError, MemoryError) as e:
        return 0, f"Python cannot read it: {e}"
    return None


def check_json(text, rel):
    try:
        json.loads(text)
    except ValueError as e:
        return getattr(e, "lineno", 0), f"invalid JSON: {getattr(e, 'msg', str(e))}"
    return None


def check_toml(text, rel):
    try:
        import tomllib
    except ImportError:
        return None
    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        m = re.search(r"line (\d+)", str(e))
        return int(m.group(1)) if m else 0, f"invalid TOML: {e}"
    return None


def check_program(argv, text, suffix, pattern):
    """Runs a parser (node --check, bash -n) on a copy in a private temporary folder, so nothing in the project is read."""
    exe = shutil.which(argv[0])
    if not exe:
        return None
    with tempfile.TemporaryDirectory(prefix="studio-check-") as tmp:
        copy = os.path.join(tmp, "file" + suffix)
        with open(copy, "w", encoding="utf-8") as fh:
            fh.write(text)
        try:
            proc = subprocess.run([exe, *argv[1:], copy], capture_output=True, text=True, timeout=CHECK_TIMEOUT_S, cwd=tmp,
                                  env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": tmp, "LANG": "C.UTF-8"})
        except (OSError, subprocess.TimeoutExpired):
            return None
    if proc.returncode == 0:
        return None
    out = (proc.stderr or proc.stdout).replace(copy, "file" + suffix)
    m = re.search(pattern, out)
    line = int(m.group(1)) if m else 0
    msg = next((l.strip() for l in out.splitlines() if re.search(r"(SyntaxError|syntax error|unexpected|Unexpected)", l)), "")
    if not msg:
        msg = out.strip().splitlines()[-1][:300] if out.strip() else "the parser refused it"
    return line, msg


MODULE_HINTS = ("Cannot use import statement outside a module", "Unexpected token 'export'",
                "await is only valid", "import.meta")


def check_js(text, rel):
    suffix = os.path.splitext(rel)[1]
    found = check_program(["node", "--check"], text, suffix, r"file\.\w+:(\d+)")
    # Node before 22 reads .js as CommonJS, so a module's import or export looks like an error: check it as a module too.
    if found and suffix == ".js" and any(h in found[1] for h in MODULE_HINTS):
        return check_program(["node", "--check"], text, ".mjs", r"file\.\w+:(\d+)")
    return found


def check_shell(text, rel):
    return check_program(["bash", "-n"], text, ".sh", r"line (\d+)")


EDIT_CHECKS = {".py": check_python, ".pyw": check_python, ".json": check_json, ".toml": check_toml,
               ".js": check_js, ".mjs": check_js, ".cjs": check_js, ".sh": check_shell, ".bash": check_shell}


def file_problem(rel, text):
    """(line, message) for a file that its language's parser refuses, else None."""
    check = EDIT_CHECKS.get(os.path.splitext(rel)[1].lower())
    if not check or text is None or len(text) > CHECK_MAX_BYTES or (check is check_json and CHECK_SKIP_JSON.search(rel)):
        return None
    try:
        return check(text, rel)
    except Exception:                 # a checker's own failure never blocks an edit
        return None


def with_check_note(result, rel, path, old):
    """An applied edit's result, saying when the file no longer parses. Files that did not parse before are left alone."""
    ok, summary, text = result
    if not ok or not APP.cfg.get("edit_checks", True) or summary == "No changes":
        return result
    problem = file_problem(rel, current_text(path))
    if not problem or (old is not None and file_problem(rel, old)):
        return result
    line, msg = problem
    msg = re.sub(r"^file\.\w+: (line \d+: )?", "", msg)
    lines = (current_text(path) or "").splitlines()
    shown = f" (`{lines[line - 1].strip()[:160]}`)" if 0 < line <= len(lines) and lines[line - 1].strip() else ""
    where = f" on line {line}{shown}" if line else ""
    return ok, summary, text + f"\nStudio checked {rel}: it no longer parses{where}: {msg}. Fix this before going on."


def with_mixed_note(result, rel, new, old):
    """An applied edit's result, naming words it brought in that mix Greek and Latin letters (the edit stays as it is)."""
    ok, summary, text = result
    if not ok or not APP.cfg.get("script_check", True):
        return result
    before = set(mixed_words(old or ""))
    added = [w for w in mixed_words(new) if w not in before]
    return (ok, summary, text + "\n" + mixed_note(added, f"{rel} now has") + " Fix them unless they are meant to be so.") if added else result


def tool_replace_in_file(run, args):
    rel, old_text, new_text = need_str(args, "path"), need_str(args, "old_text"), need_str(args, "new_text")
    path = resolve(run.ws, rel)
    refuse_git_folder(rel_posix(path, run.ws["path"]))
    text = current_text(path)
    if text is None:
        raise ApiError(404, f"file not found: {rel}")
    if not old_text:
        raise ApiError(400, "old_text must not be empty")
    count = text.count(old_text)
    if count == 0:
        raise ApiError(400, "old_text was not found (0 matches); read the file and copy the text exactly")
    if count > 1:
        raise ApiError(400, f"old_text matches {count} times; include more surrounding context to make it unique")
    return with_check_note(with_mixed_note(apply_edit(run, rel_posix(path, run.ws["path"]), path, text, text.replace(old_text, new_text, 1)),
                                           rel, new_text, old_text), rel, path, text)


WRITING_COMMAND_RE = re.compile(r">|\b(sed\s+-i|tee|mv|cp|rm|mkdir|touch|patch|git\s+apply|npm\s+(install|i)\b|pip\s+install)")


# Plan mode: commands that only look at things run as usual; anything else asks the user, even with auto-run on.
LOOK_COMMANDS = {"cd", "ls", "cat", "head", "tail", "wc", "grep", "egrep", "fgrep", "rg", "find", "tree", "pwd", "file", "stat", "du", "df",
                 "sort", "uniq", "cut", "nl", "echo", "which", "diff", "cmp", "md5sum", "sha1sum", "sha256sum", "basename", "dirname",
                 "realpath", "readlink", "date", "whoami", "uname", "printenv", "true", "jq", "tr", "column", "hexdump", "xxd", "od", "strings"}
LOOK_GIT = {"status", "log", "diff", "show", "ls-files", "blame", "grep", "rev-parse", "describe", "shortlog", "branch", "tag", "remote"}
WRITING_FLAGS = {"find": re.compile(r"^-(exec|execdir|delete|ok|okdir|fprint\w*|fls)$"), "sort": re.compile(r"^(-o.*|--output.*)$"),
                 "tree": re.compile(r"^-o$"), "rg": re.compile(r"^--pre.*$"), "git": re.compile(r"^(-c|--output.*|-o)$")}


def looks_only(command, test_command=None):
    """True for a command that only reads, such as `ls src | wc -l` or `git log -5`. No redirections, substitutions or
    command lists, and each part of a pipe is a known reading program without options that write. `2>&1` only merges the
    error output, and the project's own test command (test_command) counts as looking, so a plan can check what fails."""
    if not isinstance(command, str):
        return False
    command = re.sub(r"\s2>&1(?=\s|\||$)", " ", command)
    command = re.sub(r"\s(?:[12]?>>?|&>)\s*/dev/null(?=\s|\||;|&|$)", " ", command)     # discarding output changes nothing
    command = command.replace("$?", "")                                                   # the last exit code; $(…) stays refused
    steps = [step for step in re.split(r"\s*(?:&&|\|\||;)\s*", command.strip()) if step]
    if len(steps) > 1:                       # `pwd; ls -la` or `cd src && ls`: every step must only look
        return all(looks_only(step, test_command) for step in steps)
    if re.search(r"[;&<>`$(){}\\\n]", command):
        return False
    for index, part in enumerate(command.split("|")):
        if index == 0 and test_command and part.strip() == test_command.strip():
            continue
        try:
            words = shlex.split(part)
        except ValueError:
            return False
        if not words:
            return False
        program, rest = os.path.basename(words[0]), words[1:]
        if program == "git":
            sub = next((w for w in rest if not w.startswith("-")), "")
            if sub not in LOOK_GIT or (sub in ("branch", "tag", "remote") and len([w for w in rest if w != sub]) > (1 if sub == "remote" else 0)
                                       and not all(w in ("-v", "-a", "-l", "--list", "-r", "--all") for w in rest if w != sub)):
                return False
        elif program not in LOOK_COMMANDS:
            return False
        if program in WRITING_FLAGS and any(WRITING_FLAGS[program].match(w) for w in rest):
            return False
        if program == "uniq" and len([w for w in rest if not w.startswith("-")]) > 1:      # uniq IN OUT writes OUT
            return False
    return True


BASH_ONLY = [(re.compile(r"<<-?\s*['\"]?[A-Za-z_]\w*['\"]?"), "a heredoc (<<)"),
             (re.compile(r"(^|[\s;(])(export\s+\w+=|source\s|\[\[\s)"), "bash syntax (export, source or [[ ]])")]


def powershell_problem(ws, command):
    """Why a bash-only command cannot run in a Windows PowerShell helper folder, or None. Sent back before it runs,
    since PowerShell reads `python - <<'PY'` as a redirect and fails with MissingFileSpecification (Keystone, 2026-11-13)."""
    if not ws.get("helper") or (HELPERS.get(ws["helper"]) or {}).get("shell") != "powershell":
        return None
    for pattern, what in BASH_ONLY:
        if pattern.search(command):
            return (f"Not run: this folder's commands run in Windows PowerShell, which has no {what}. For a script, write it to a file with "
                    "write_file (for example check.py, saved as UTF-8) and run it: `python check.py` (or `py check.py`). Set an environment "
                    "variable with `$env:NAME = 'value'`, join steps with `;`, and test with `if ($?) { ... }` instead of `&&`.")
    return None


def tool_run(run, args):
    command = need_str(args, "command")
    problem = powershell_problem(run.ws, command)
    if problem:
        return False, "Not run: bash syntax in PowerShell", problem
    if run.plan_mode and not looks_only(command, detect_test_command(run.ws["path"])):
        decision, note = run.ask(kind="command", command=command, plan=True)
        if decision != "accept":
            return False, "Command skipped", ("Plan mode is on, and the user did not allow this command, which could change things. "
                                              "Look with commands that only read, and put what you would run in your plan." + (f" Note: {note}" if note else ""))
    elif not run.auto_commands:
        decision, note = run.ask(kind="command", command=command)
        if decision != "accept":
            return False, "Command skipped", "The user rejected this command." + (f" Note: {note}" if note else "")
    run.changed = run.changed or bool(WRITING_COMMAND_RE.search(command))
    result = run_online_if_allowed(run, command)
    if WAIT_COMMAND.fullmatch(command.strip()) and APP.cfg.get("turn_guard", True):
        run.waits = getattr(run, "waits", 0) + 1
        if run.waits >= WAIT_NOTE_AFTER:
            ok, summary, text = result
            result = ok, summary, text + "\n" + WAIT_NOTE.format(n=run.waits)
    return result


# A command that only waits (Start-Sleep 60; Write-Output ok). Keystone slept 45, 60, 90 and 120 s on 2026-11-13 for a
# listmonk import that was stuck behind a database lock and would never finish.
WAIT_COMMAND = re.compile(r"(?i)(start-sleep(\s+-(seconds|s|milliseconds|m))?\s+\d+(\.\d+)?|sleep\s+\d+(\.\d+)?[smh]?|timeout\s+(/t\s+)?\d+(\s+/nobreak)?)"
                          r"(\s*(;|&&|\|\|)\s*(write-output|write-host|echo)\b[^;&|]*)?\s*;?")
WAIT_NOTE_AFTER = 3
WAIT_NOTE = ("Studio note: this is wait number {n} in this run. Waiting more does not make anything finish: check the thing you are waiting "
             "for with its own status tool or command, and if it has not moved since the last check, it is stuck, not slow. Then stop or "
             "restart it if a tool can, or ask the user, instead of sleeping again.")


def run_online_if_allowed(run, command):
    online = sandboxed(run.ws) and sandbox_settings(run.ws)["network"] == "install" and INSTALL_RE.search(command)
    if not online:
        return run_command(run, command)
    name = ensure_sandbox(run.ws)
    docker("network", "connect", SANDBOX_BRIDGE_NETWORK, name)
    try:
        return run_command(run, command)
    finally:
        docker("network", "disconnect", SANDBOX_BRIDGE_NETWORK, name)


# A pipe's exit code is its last part's, so `tests | tail -20` reads "exit 0" when the tests failed. After the command, in the
# same eval, the shell records every part of the last pipe and prints them on a marker line, which Studio takes out again.
PIPE_SCRIPT = r'''eval "$STUDIO_CMD"$'\n__studio_p=("${PIPESTATUS[@]}")'; printf '\n%s %s\n' "$STUDIO_PIPE_MARK" "${__studio_p[*]}"; exit "${__studio_p[-1]}"'''
# Statuses that are not failures: 141 is a part stopped because the next one read enough (`| head`), and 1 from these
# programs means "found nothing" or "they differ".
PIPE_NOT_FAILED = {"grep", "egrep", "fgrep", "zgrep", "rg", "ag", "diff", "cmp"}


def pipe_parts(command):
    """The program of each part of the command's last pipe, or None when it cannot be told. Quotes are respected."""
    lines = [line for line in command.strip().splitlines() if line.strip()]
    try:
        lexer = shlex.shlex(lines[-1] if lines else "", posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return None
    pipes = [[[]]]                          # commands, each a pipe of parts, each part a list of words
    for tok in tokens:
        if tok in (";", "&&", "||", "&"):
            pipes.append([[]])
        elif tok == "|":
            pipes[-1].append([])
        else:
            pipes[-1][-1].append(tok)
    last = next((pipe for pipe in reversed(pipes) if any(pipe)), None)
    names = []
    for words in last or []:
        words = [w for w in words if not re.match(r"^\w+=", w)]           # VAR=value before the program
        if not words:
            return None
        names.append(os.path.basename(words[0]))
    return names or None


def hidden_failure(command, statuses):
    """(position, program, status) of the first earlier part of the last pipe that failed, which its exit code hides."""
    names = pipe_parts(command)
    names = names if names and len(names) == len(statuses) else None
    for i, status in enumerate(statuses[:-1]):
        name = names[i] if names else None
        if status in (0, 141) or (status == 1 and name in PIPE_NOT_FAILED):
            continue
        return i + 1, name, status
    return None


def run_command(run, command, extra_env=None):
    if run.ws.get("helper"):
        return helper_run(run, command, extra_env or {})
    timeout = APP.cfg["command_timeout_s"]
    extra_env = extra_env or {}
    mark = "__studio_pipe_" + secrets.token_hex(6)
    # The command travels in a variable, not on bash's command line, so `pkill -f <part of the command>` cannot kill
    # the shell running it, which every model tried at least once.
    proc = subprocess.Popen(sandbox_exec(run.ws, ["bash", "-lc", PIPE_SCRIPT], env=("STUDIO_CMD", "STUDIO_PIPE_MARK", *extra_env)), cwd=run.ws["path"],
                            env={**os.environ, "STUDIO_CMD": command, "STUDIO_PIPE_MARK": mark, **extra_env}, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    deadline = time.monotonic() + timeout
    output, note = b"", ""
    while True:
        try:
            output, _ = proc.communicate(timeout=0.5)
            break
        except subprocess.TimeoutExpired:
            if run.stop or time.monotonic() > deadline:
                note = "\n[stopped by the user]" if run.stop else f"\n[timed out after {timeout}s and killed]"
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except OSError:
                    pass
                if sandboxed(run.ws):
                    # Killing `docker exec` leaves the command running inside, so restart the container.
                    docker("restart", "-t", "0", sandbox_name(run.ws))
                output, _ = proc.communicate()
                break
    return command_result(command, proc.returncode, output.decode("utf-8", errors="replace"), mark, note)


def command_result(command, code, text, mark, note=""):
    """The tool's answer for a finished command: the pipe statuses taken off the output, and a failure hidden in a pipe named."""
    statuses, at = [], text.rfind("\n" + mark + " ") if mark else -1
    if at >= 0:
        line = text[at + len(mark) + 2:].split("\n", 1)[0]
        statuses = [int(x) for x in line.split() if x.isdigit()]
        text = text[:at] + text[at + len(mark) + 2 + len(line) + 1:]
    if len(text) > 6000:
        text = "[earlier output cut]\n" + text[-6000:]
    hidden = hidden_failure(command, statuses) if code == 0 and len(statuses) > 1 else None
    if hidden:
        place, name, status = hidden
        what = f"`{name}`" if name else f"part {place}"
        return (False, f"exit 0, but {what} exited {status}",
                f"exit code 0, but in the pipe {what} (part {place}) exited {status}: the pipe's exit code is only its last part's, "
                f"so look at the output for the failure.\n{text}{note}")
    return code == 0, f"exit {code}", f"exit code {code}\n{text}{note}"


TOOL_FUNCS = {"list_files": tool_list_files, "read_file": tool_read_file, "search": tool_search,
              "write_file": tool_write_file, "replace_in_file": tool_replace_in_file, "run": tool_run,
              "delete_file": tool_delete_file, "save_skill": tool_save_skill, "use_skill": tool_use_skill, "plan": tool_plan,
              "view_page": tool_view_page, "open_preview": tool_open_preview, "save_picture": tool_save_picture,
              "ask_user": tool_ask_user, "write_document": tool_write_document}


def tool_enabled(name):
    if name == "delete_file":
        return APP.cfg["delete_tool"]
    if name == "plan":
        return APP.cfg["plan_tool"]
    if name == "ask_user":
        return APP.cfg.get("ask_tool", DEFAULT_CONFIG["ask_tool"])
    if name in ("view_page", "open_preview"):
        return APP.cfg["live_view"]
    if name == "write_document":
        return APP.cfg.get("document_tool", DEFAULT_CONFIG["document_tool"])
    if name == "save_picture":
        return APP.cfg["mcp"]                      # its pictures come from MCP tools
    return APP.cfg["skills"] if name in ("save_skill", "use_skill") else True


def full_tool_name(run, name):
    """The MCP tool a shortened name means: "mcp__greek-check" for a server with one tool, or a bare tool name such as
    "check_text" that only one server has. Any other name stays as it is."""
    mcp_tools = getattr(run, "mcp_tools", None) or {}
    if not isinstance(name, str) or name in mcp_tools or name in TOOL_FUNCS or name == "finish":
        return name
    server = name[len("mcp__"):].rstrip("_") if name.startswith("mcp__") else None
    matches = [full for full, entry in mcp_tools.items() if (entry["server"] == server if server else entry["tool"] == name)]
    return matches[0] if len(matches) == 1 else name


PLAN_REFUSED = {"write_file", "replace_in_file", "delete_file", "save_picture", "write_document"}
PLAN_REFUSAL = ("Plan mode is on, so files cannot be changed now. Nothing was changed. Finish looking, then present your plan; "
                "the user approves it before any change is made.")
PLAN_NOTE = ("Plan first is on for this message. Look around with the tools, but change nothing: file edits are refused, "
             "and commands that could change anything need the user's approval. When you know what to do, {how}: what you would "
             "change in each step and why. Do not make the changes; the user approves or edits the plan first.")


def plan_note():
    how = "call plan with the steps, then explain the plan in your reply" if APP.cfg["plan_tool"] else "reply with a numbered plan"
    return PLAN_NOTE.format(how=how) + "\n"


def plan_steps(run, text):
    """The steps of the plan a plan-mode run ends with: its plan tool's steps, or the numbered lines of its last reply."""
    if run.plan:
        return list(run.plan)
    steps = [m.strip()[:MAX_PLAN_STEP * 2] for m in re.findall(r"^\s*\d{1,2}[.)]\s+(.+)$", text or "", re.M)]
    return steps[:MAX_PLAN_STEPS]


# ---- the user's own tools: written in the Skills view, offered to the agent as user__<name>
USER_TOOL_KINDS = ("instructions", "command")
USER_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]{1,39}$")
MAX_USER_TOOLS, MAX_USER_TOOL_TEXT, MAX_USER_TOOL_INPUT = 30, 8000, 8000


def user_tools_path(username):
    return os.path.join(APP.data_dir, "user_tools", hashlib.sha1(username.encode()).hexdigest() + ".json")


def user_tools_of(username):
    return read_json(user_tools_path(username), {}).get("tools", [])


def clean_user_tool(raw):
    if not isinstance(raw, dict):
        raise ApiError(400, "tool must be an object")
    name = raw.get("name").strip().lower() if isinstance(raw.get("name"), str) else ""
    if not USER_TOOL_NAME.match(name):
        raise ApiError(400, "the name must be 2 to 40 lowercase letters, digits or _, starting with a letter, such as release_notes")
    kind = raw.get("kind")
    if kind not in USER_TOOL_KINDS:
        raise ApiError(400, "kind must be instructions or command")
    description = raw.get("description").strip() if isinstance(raw.get("description"), str) else ""
    text = raw.get("text").strip() if isinstance(raw.get("text"), str) else ""
    if not 5 <= len(description) <= 300:
        raise ApiError(400, "say in one line when the agent should use it (5 to 300 characters)")
    if not text or len(text) > MAX_USER_TOOL_TEXT or "\x00" in text:
        raise ApiError(400, "the instructions or command are missing or too long")
    return {"name": name, "kind": kind, "description": description, "text": text, "on": raw.get("on") is not False}


def user_tools_for(username):
    """{tool name: entry} of the user's own tools that are on, fixed for a run like the MCP tools."""
    out = {}
    for tool in user_tools_of(username):
        if not tool.get("on", True):
            continue
        full = "user__" + tool["name"]
        props = {"input": {"type": "string", "description": "What the command should work on; it receives it in $STUDIO_INPUT"}} if tool["kind"] == "command" else {}
        desc = tool["description"] + (" (a command the user wrote; it runs in the project)" if tool["kind"] == "command" else " (instructions the user wrote)")
        out[full] = {**tool, "schema": {"type": "function", "function": {"name": full, "description": desc[:400],
                                                                         "parameters": {"type": "object", "properties": props, "required": []}}}}
    return out


def tool_user(run, name, args):
    tool = run.user_tools[name]
    if tool["kind"] == "instructions":
        return True, "Instructions", tool["text"]
    given = args.get("input") if isinstance(args.get("input"), str) else ""
    if len(given) > MAX_USER_TOOL_INPUT:
        return False, "Input too long", f"Error: input can have at most {MAX_USER_TOOL_INPUT} characters."
    if not run.auto_commands or (run.plan_mode and not looks_only(tool["text"])):
        decision, note = run.ask(kind="command", command=tool["text"] + (f"\n# STUDIO_INPUT: {given[:500]}" if given else ""), purpose="user_tool",
                                 tool=tool["name"], **({"plan": True} if run.plan_mode else {}))
        if decision != "accept":
            return False, "Command skipped", "The user did not allow this tool to run now." + (f" Note: {note}" if note else "")
    run.changed = run.changed or bool(WRITING_COMMAND_RE.search(tool["text"]))
    return run_command(run, tool["text"], {"STUDIO_INPUT": given})


@route("GET", "/api/tools/mine")
def user_tools_get(req):
    return {"tools": user_tools_of(req.current_user["username"]), "enabled": APP.cfg.get("user_tools", True)}


@route("POST", "/api/tools/save")
def user_tools_save(req):
    """Adds a tool, or changes the one named old_name."""
    me = req.current_user["username"]
    tool = clean_user_tool(req.field("tool"))
    old = req.field("old_name") if isinstance(req.field("old_name"), str) else None
    tools = [t for t in user_tools_of(me) if t["name"] != old]
    if any(t["name"] == tool["name"] for t in tools):
        raise ApiError(409, f"you already have a tool called {tool['name']}")
    if len(tools) >= MAX_USER_TOOLS:
        raise ApiError(400, f"at most {MAX_USER_TOOLS} tools")
    tools.append(tool)
    write_json_atomic(user_tools_path(me), {"tools": sorted(tools, key=lambda t: t["name"])})
    return {"tool": tool}


@route("POST", "/api/tools/delete")
def user_tools_delete(req):
    me = req.current_user["username"]
    tools = user_tools_of(me)
    left = [t for t in tools if t["name"] != req.field("name")]
    if len(left) == len(tools):
        raise ApiError(404, "unknown tool")
    write_json_atomic(user_tools_path(me), {"tools": left})
    return {"ok": True}


def execute_tool(run, name, args):
    """Run one tool call; returns (ok, summary, output). Errors become failed results for the model."""
    if isinstance(args, dict):
        run.note_progress(args.get("progress"))
    if name == "finish":
        summary = args.get("summary") if isinstance(args.get("summary"), str) else ""
        return True, "Finished", summary or "Done."
    fn = TOOL_FUNCS.get(name) if tool_enabled(name) else None
    if fn and name in PLAN_REFUSED and getattr(run, "plan_mode", False):
        return False, "Plan mode: not changed", PLAN_REFUSAL
    mcp_tools = getattr(run, "mcp_tools", None) or {}      # tests drive this with stand-in runs
    if name in mcp_tools:
        fn = lambda run, args: tool_mcp(run, name, args)
    if name in (getattr(run, "user_tools", None) or {}):
        fn = lambda run, args: tool_user(run, name, args)
    if fn is None:
        available = [tool for tool in TOOL_FUNCS if tool_enabled(tool)] + list(mcp_tools) + list(getattr(run, "user_tools", None) or {})
        return False, "Unknown tool", f"Unknown tool '{name}'. Available tools: {', '.join(available)}, finish."
    try:
        ok, summary, output = fn(run, args)
    except ApiError as exc:
        return False, exc.message, f"Error: {exc.message}"
    except OSError as exc:
        return False, str(exc.strerror or exc), f"Error: {exc}"
    return ok, summary, output + plan_progress_note(run, name, args)


def plan_progress_note(run, name, args):
    """Once per plan, tells a model whose progress total disagrees with its plan how to count."""
    plan, parsed = getattr(run, "plan", None), parse_progress(args.get("progress")) if isinstance(args, dict) else None
    if not plan or not parsed or name == "plan" or parsed[1] == len(plan) or getattr(run, "plan_noted", False):
        return ""
    run.plan_noted = True
    return f'\n\nStudio note: your plan has {len(plan)} steps, so set progress as "n/{len(plan)}".'


# ---------------------------------------------------------------------------
# MCP servers: tools from other programs, offered to the agent
# ---------------------------------------------------------------------------

MCP_PROTOCOL = "2025-06-18"
MCP_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
MCP_APPROVALS = ("always", "with_commands")   # ask for every call, or follow the auto-run toggle like commands
MCP_START_TIMEOUT_S = 60                      # npx and uvx may download the server first
MCP_MAX_OUTPUT = 12000
MCP_MAX_TOOLS = 100                           # per server; long tool lists crowd the prompt
SECRET_MASK = "••••••"


class McpError(Exception):
    pass


def mcp_argv(spec):
    """(argv, container name) for a stdio server. A `docker run` server gets a fixed name, studio-mcp-<server>, unless it has
    one, so Studio can remove the container: stopping `docker run` does not stop a server that ignores the end of its input."""
    if spec.get("transport") != "stdio":
        return [], None
    args = list(spec.get("args") or [])
    if os.path.basename(spec.get("command") or "") != "docker" or not args or args[0] != "run":
        return [spec["command"], *args], None
    for i, arg in enumerate(args):
        if arg == "--name" and i + 1 < len(args):
            return [spec["command"], *args], args[i + 1]
        if arg.startswith("--name="):
            return [spec["command"], *args], arg.split("=", 1)[1]
    name = "studio-mcp-" + re.sub(r"[^a-zA-Z0-9_.-]", "-", spec["name"])
    return [spec["command"], "run", "--name", name, *args[1:]], name


class McpClient:
    """One connection to one MCP server, over stdio (a program Studio starts) or HTTP. Requests may come from several runs at once."""

    def __init__(self, spec):
        self.spec = spec
        self.name = spec["name"]
        self.state, self.error = "starting", ""
        self.tools, self.instructions, self.server_info = [], "", {}
        self.log = collections.deque(maxlen=200)          # stderr and connection notes, shown in the panel
        self.lock = threading.Lock()
        self.pending = {}                                 # request id -> [event, response]
        self.progress = {}                                # progress token -> callback for notifications/progress
        self.next_id = 0
        self.proc = None
        self.session_id = None                            # HTTP servers may hand one out at initialize
        self.ready = threading.Event()
        self.closed = False
        self.argv, self.container = mcp_argv(spec)

    def note(self, text):
        self.log.append(f"{time.strftime('%H:%M:%S')} {text}")

    # -- transport ------------------------------------------------------------
    def start(self):
        try:
            if self.spec["transport"] == "stdio":
                env = {**os.environ, **greek_check_env(self.argv), **self.spec.get("env", {})}
                if self.container:      # one left by an earlier start, which a server that ignores the end of its input keeps alive
                    subprocess.run(["docker", "rm", "-f", self.container], capture_output=True, timeout=30)
                self.proc = subprocess.Popen(self.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                             stderr=subprocess.PIPE, cwd=os.path.expanduser("~"), env=env, start_new_session=True)
                threading.Thread(target=self._read_stdout, daemon=True).start()
                threading.Thread(target=self._read_stderr, daemon=True).start()
            init = self.request("initialize", {"protocolVersion": MCP_PROTOCOL, "capabilities": {},
                                               "clientInfo": {"name": "Eidoloom Studio", "version": "1.0"}}, MCP_START_TIMEOUT_S)
            self.server_info = init.get("serverInfo") or {}
            self.instructions = str(init.get("instructions") or "")[:2000]
            self.notify("notifications/initialized")
            self.load_tools()
            self.state = "ready"
            self.note(f"ready with {len(self.tools)} tools")
        except (OSError, McpError, ValueError) as exc:
            self.state, self.error = "error", str(exc) or exc.__class__.__name__
            self.note("failed: " + self.error)
            self.close()
        finally:
            self.ready.set()

    def load_tools(self):
        tools, cursor = [], None
        for _ in range(20):
            page = self.request("tools/list", {"cursor": cursor} if cursor else {}, 30)
            tools += [t for t in page.get("tools") or [] if isinstance(t, dict) and isinstance(t.get("name"), str)]
            cursor = page.get("nextCursor")
            if not cursor:
                break
        self.tools = tools[:MCP_MAX_TOOLS]

    def _send(self, message):
        if self.spec["transport"] == "stdio":
            data = (json.dumps(message) + "\n").encode()
            with self.lock:
                if not self.proc or self.proc.poll() is not None:
                    raise McpError("the server program is not running")
                try:
                    self.proc.stdin.write(data)
                    self.proc.stdin.flush()
                except OSError as exc:
                    raise McpError(f"could not write to the server: {exc}")
            return None
        return self._post(message)

    def _post(self, message):
        """Streamable HTTP: the answer is plain JSON or an event stream that carries it."""
        parts = urllib.parse.urlsplit(self.spec["url"])
        conn_class = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
        conn = conn_class(parts.hostname, parts.port, timeout=self.call_timeout() + 10)
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", **self.spec.get("headers", {})}
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        try:
            conn.request("POST", (parts.path or "/") + (f"?{parts.query}" if parts.query else ""), json.dumps(message).encode(), headers)
            resp = conn.getresponse()
            if resp.getheader("Mcp-Session-Id"):
                self.session_id = resp.getheader("Mcp-Session-Id")
            if resp.status == 202 or "id" not in message:
                resp.read()
                return None
            if resp.status >= 400:
                raise McpError(f"HTTP {resp.status}: {resp.read(300).decode('utf-8', 'replace').strip()}")
            if "text/event-stream" in (resp.getheader("Content-Type") or ""):
                data = []
                for raw in resp:
                    line = raw.decode("utf-8", "replace").rstrip("\r\n")
                    if line.startswith("data:"):
                        data.append(line[5:].lstrip())
                    elif not line and data:
                        reply = json.loads("\n".join(data))
                        data = []
                        if isinstance(reply, dict) and reply.get("id") == message["id"]:
                            return reply
                raise McpError("the server closed the stream without an answer")
            return json.loads(resp.read())
        except (OSError, http.client.HTTPException) as exc:
            raise McpError(f"could not reach {self.spec['url']}: {exc}")
        finally:
            conn.close()

    def _read_stdout(self):
        for raw in self.proc.stdout:
            try:
                message = json.loads(raw)
            except ValueError:
                self.note("not JSON: " + raw.decode("utf-8", "replace").strip()[:200])
                continue
            if isinstance(message, dict):
                self._handle(message)
        self.note(f"the server program exited ({self.proc.wait()})")
        if self.state == "ready":
            self.state, self.error = "error", "the server program exited"
        with self.lock:
            waiting, self.pending = list(self.pending.values()), {}
        for slot in waiting:
            slot[1] = {"error": {"message": "the server program exited"}}
            slot[0].set()

    def _read_stderr(self):
        for raw in self.proc.stderr:
            self.note(raw.decode("utf-8", "replace").rstrip()[:500])

    def _handle(self, message):
        if "method" in message:
            if "id" in message:     # a request from the server: answer pings, refuse the rest (sampling, roots, elicitation)
                reply = {"result": {}} if message["method"] == "ping" else {"error": {"code": -32601, "message": "not supported by Eidoloom Studio"}}
                try:
                    self._send({"jsonrpc": "2.0", "id": message["id"], **reply})
                except McpError:
                    pass
            elif message["method"] == "notifications/tools/list_changed":
                threading.Thread(target=self._reload_tools, daemon=True).start()
            elif message["method"] == "notifications/progress":
                params = message.get("params") or {}
                callback = self.progress.get(params.get("progressToken"))
                if callback:
                    try:
                        callback(params)
                    except Exception:                  # noqa: BLE001 - a progress note never breaks the call
                        pass
            return
        with self.lock:
            slot = self.pending.pop(message.get("id"), None)
        if slot:
            slot[1] = message
            slot[0].set()

    def _reload_tools(self):
        try:
            self.load_tools()
            self.note(f"tool list changed: {len(self.tools)} tools; new runs will see it")
        except McpError as exc:
            self.note(f"could not reload the tool list: {exc}")

    # -- requests -------------------------------------------------------------
    def notify(self, method, params=None):
        self._send({"jsonrpc": "2.0", "method": method, **({"params": params} if params else {})})

    def request(self, method, params, timeout, stop=None):
        """Send a request and wait for its answer. `stop` is checked while waiting, so a stopped run cancels it."""
        with self.lock:
            self.next_id += 1
            rid = self.next_id
            slot = self.pending[rid] = [threading.Event(), None]
        message = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
        try:
            reply = self._send(message)
        except McpError:
            with self.lock:
                self.pending.pop(rid, None)
            raise
        if reply is None:
            deadline = time.monotonic() + timeout
            while not slot[0].wait(0.5):
                if (stop and stop()) or time.monotonic() > deadline:
                    with self.lock:
                        self.pending.pop(rid, None)
                    try:
                        self.notify("notifications/cancelled", {"requestId": rid, "reason": "stopped" if stop and stop() else "timed out"})
                    except McpError:
                        pass
                    raise McpError("stopped by the user" if stop and stop() else f"no answer after {timeout}s")
            reply = slot[1]
        else:
            with self.lock:
                self.pending.pop(rid, None)
        if not isinstance(reply, dict):
            raise McpError("the server sent an invalid answer")
        if reply.get("error"):
            error = reply["error"]
            raise McpError(str(error.get("message") if isinstance(error, dict) else error))
        return reply.get("result") or {}

    def call(self, tool, args, stop, on_progress=None):
        """Calls a tool. With on_progress, the server is offered a progress token and its notifications/progress reach it."""
        if self.state != "ready":
            raise McpError(f"the server is not running ({self.error or self.state})")
        params = {"name": tool, "arguments": args}
        token = None
        if on_progress:
            token = "studio-" + secrets.token_hex(6)
            params["_meta"] = {"progressToken": token}
            self.progress[token] = on_progress
        try:
            return self.request("tools/call", params, self.call_timeout(), stop)
        finally:
            if token:
                self.progress.pop(token, None)

    def call_timeout(self):
        return self.spec.get("timeout_s") or APP.cfg["mcp_call_timeout_s"]

    def close(self):
        self.closed = True
        if self.state == "ready":
            self.state = "stopped"
        if self.container:
            container = self.container
            threading.Thread(target=lambda: subprocess.run(["docker", "rm", "-f", container], capture_output=True, timeout=30), daemon=True).start()
        proc = self.proc
        if proc and proc.poll() is None:
            try:
                proc.stdin.close()
                proc.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except OSError:
                    pass
        if self.spec["transport"] == "http" and self.session_id:
            try:
                parts = urllib.parse.urlsplit(self.spec["url"])
                conn = (http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection)(parts.hostname, parts.port, timeout=3)
                conn.request("DELETE", parts.path or "/", headers={"Mcp-Session-Id": self.session_id, **self.spec.get("headers", {})})
                conn.getresponse().read()
                conn.close()
            except (OSError, http.client.HTTPException):
                pass


def clean_mcp_spec(raw, old=None):
    """Checks one server from the panel. Secrets shown masked keep their stored value."""
    if not isinstance(raw, dict):
        raise ApiError(400, "each server must be an object")
    name = raw.get("name")
    if not isinstance(name, str) or not MCP_NAME_RE.match(name):
        raise ApiError(400, "a server name is 1 to 32 letters, digits, - or _, starting with a letter or digit")
    transport = raw.get("transport") or ("http" if raw.get("url") else "stdio")
    if transport not in ("stdio", "http"):
        raise ApiError(400, f"{name}: transport must be stdio or http")
    approval = raw.get("approval") or "always"
    if approval not in MCP_APPROVALS:
        raise ApiError(400, f"{name}: approval must be one of {', '.join(MCP_APPROVALS)}")

    def secrets_of(key):
        values = raw.get(key) or {}
        if not isinstance(values, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in values.items()):
            raise ApiError(400, f"{name}: {key} must map names to text")
        kept = (old or {}).get(key, {})
        return {k: kept.get(k, "") if v == SECRET_MASK else v for k, v in values.items()}
    spec = {"name": name, "transport": transport, "enabled": raw.get("enabled", True) is not False, "approval": approval,
            "users": raw.get("users") is True, "description": str(raw.get("description") or "")[:300]}
    if raw.get("timeout_s") not in (None, ""):
        timeout = raw.get("timeout_s")
        if not isinstance(timeout, int) or isinstance(timeout, bool) or not 10 <= timeout <= 3600:
            raise ApiError(400, f"{name}: timeout_s must be a whole number of seconds from 10 to 3600")
        spec["timeout_s"] = timeout         # for servers whose calls take long, such as ComfyUI's model swaps
    # A program the server talks to that must be running, such as Blender for the blender server: Studio starts it on
    # this machine's screen when a call needs it and nothing answers on its port.
    app_command, app_port = raw.get("app_command"), raw.get("app_port")
    if app_command not in (None, ""):
        if not isinstance(app_command, str) or len(app_command) > 500 or not shlex.split(app_command):
            raise ApiError(400, f"{name}: the program to start must be a command line of up to 500 characters")
        if not isinstance(app_port, int) or isinstance(app_port, bool) or not 1 <= app_port <= 65535:
            raise ApiError(400, f"{name}: give the port the program listens on (1 to 65535)")
        window = raw.get("app_window") or "screen"
        if window not in ("screen", "hidden"):
            raise ApiError(400, f"{name}: the program's window is shown on the screen or hidden")
        spec.update(app_command=app_command.strip(), app_port=app_port, app_window=window)
    if transport == "stdio":
        command, args = raw.get("command"), raw.get("args") or []
        if not isinstance(command, str) or not command.strip():
            raise ApiError(400, f"{name}: a command is required")
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            raise ApiError(400, f"{name}: args must be a list of text")
        spec.update(command=command.strip(), args=args, env=secrets_of("env"))
    else:
        url = raw.get("url")
        if not isinstance(url, str) or urllib.parse.urlsplit(url).scheme not in ("http", "https") or not urllib.parse.urlsplit(url).hostname:
            raise ApiError(400, f"{name}: a http or https URL is required")
        spec.update(url=url, headers=secrets_of("headers"))
    return spec


SECRET_NAME = re.compile(r"(TOKEN|SECRET|PASS|PASSWORD|PWD|KEY|AUTH|CREDENTIAL|COOKIE|SESSION|PRIVATE)", re.I)


def secret_setting(name, value):
    """Whether a server setting must stay hidden: by its name (…_TOKEN, API_KEY, Authorization…), or a value that
    carries a password, such as postgres://user:password@host."""
    return bool(SECRET_NAME.search(name) or re.search(r"://[^/@\s]*:[^/@\s]*@", value or ""))


def setting_shown(name, value):
    """How the panel shows a stored setting: plain when harmless (an address, a user name), else dots and its last 4."""
    if not value:
        return ""
    if not secret_setting(name, value):
        return value
    return "••••" + value[-4:] if len(value) >= 12 else "••••"


def masked_mcp_spec(spec):
    """Values hidden; "filled" says which are set and "shown" what the panel may display of each, so admins can see that
    a server has its credentials (user name in full, token as ••••1a2b) without the token ever leaving the server."""
    return {**spec, **{key: {k: SECRET_MASK for k in spec[key]} for key in ("env", "headers") if key in spec},
            "filled": {key: sorted(k for k, v in spec[key].items() if v) for key in ("env", "headers") if key in spec},
            "shown": {key: {k: {"value": setting_shown(k, v), "secret": secret_setting(k, v)} for k, v in spec[key].items()}
                      for key in ("env", "headers") if key in spec}}


def mcp_launch(spec):
    """The settings that need the server started again when they change."""
    return None if not spec else {key: spec.get(key) for key in ("transport", "command", "args", "env", "url", "headers")}


class McpManager:
    """The servers from data/mcp.json, each started in the background when enabled."""

    def __init__(self, data_dir):
        self.path = os.path.join(data_dir, "mcp.json")
        self.lock = threading.Lock()
        self.servers = read_json(self.path, {}).get("servers", [])
        self.clients = {}
        self.apps = {}          # programs Studio started for servers (app_command)
        self.screens = {}       # the invisible screens (Xvfb) they draw on, when their window is hidden
        self.app_errors = {}

    def start_all(self):
        for spec in self.servers:
            if spec.get("enabled"):
                self._start(spec)

    def _start(self, spec):
        client = McpClient(spec)
        with self.lock:
            old = self.clients.get(spec["name"])
            self.clients[spec["name"]] = client
        if old:
            old.close()
        threading.Thread(target=client.start, daemon=True).start()

    def _stop(self, name):
        with self.lock:
            client = self.clients.pop(name, None)
        if client:
            client.close()

    def replace(self, servers):
        """Saves the panel's list. Servers whose settings changed are restarted; removed or turned off ones are stopped."""
        old = {s["name"]: s for s in self.servers}
        specs = [clean_mcp_spec(raw, old.get(raw.get("name") if isinstance(raw, dict) else None)) for raw in servers]
        if len({s["name"] for s in specs}) != len(specs):
            raise ApiError(400, "two servers have the same name")
        write_json_atomic(self.path, {"servers": specs}, private=True)
        self.servers = specs
        for name in set(old) - {s["name"] for s in specs}:
            self._stop(name)
        for spec in specs:
            if not spec["enabled"]:
                self._stop(spec["name"])
            elif mcp_launch(old.get(spec["name"])) != mcp_launch(spec) or spec["name"] not in self.clients:
                self._start(spec)
            else:
                self.clients[spec["name"]].spec = spec      # approval, sharing and description take effect without a restart

    def current(self, name, fallback):
        """The server's live connection, so a run started before a restart still reaches it; waits a little while it starts."""
        client = self.clients.get(name) or fallback
        if client.state == "starting":
            client.ready.wait(20)
        return client

    def restart(self, name):
        spec = next((s for s in self.servers if s["name"] == name), None)
        if not spec or not spec["enabled"]:
            raise ApiError(404, "no enabled server with that name")
        self._start(spec)

    def spec(self, name):
        return next((s for s in self.servers if s["name"] == name), None)

    def app_running(self, spec):
        port = spec.get("app_port")
        if not port:
            return None
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            return False

    def app_log(self, name):
        return os.path.join(os.path.dirname(self.path), "mcp-apps", re.sub(r"[^\w-]", "_", name) + ".log")

    def invisible_screen(self, name, log):
        """An X screen nobody sees (Xvfb), for a program whose window is hidden. Its display name, such as ":5"."""
        xvfb = shutil.which("Xvfb")
        if not xvfb:
            raise RuntimeError("an invisible window needs Xvfb, which is not installed: sudo apt install xvfb")
        old = self.screens.get(name)
        if old and old[0].poll() is None:
            return old[1]
        read, write = os.pipe()
        proc = subprocess.Popen([xvfb, "-displayfd", str(write), "-screen", "0", "1920x1080x24", "-nolisten", "tcp", "-noreset"],
                                pass_fds=(write,), stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        os.close(write)
        number = b""
        with os.fdopen(read, "rb") as pipe:
            number = pipe.readline().strip()
        if not number.isdigit():
            proc.kill()
            raise RuntimeError("Xvfb did not start; see the log")
        self.screens[name] = (proc, ":" + number.decode())
        return ":" + number.decode()

    @staticmethod
    def monitor_connected():
        """True when a screen is plugged in and seen by a graphics card. Without one, a desktop window cannot be made: Blender
        crashed creating its window on 2026-11-11 while the server's monitor was off."""
        for status in glob.glob("/sys/class/drm/card*-*/status"):
            if "Writeback" in status:
                continue
            try:
                with open(status) as f:
                    if f.read().strip() == "connected":
                        return True
            except OSError:
                continue
        return False

    def start_app(self, spec, wait=90, stop=lambda: False):
        """Starts the program, falling back to an invisible screen when its window cannot be shown."""
        if spec.get("app_window") != "hidden" and shutil.which("Xvfb") and spec.get("app_command") and not self.app_running(spec):
            if not self.monitor_connected():
                self.app_note(spec["name"], "no monitor is connected, so it starts on an invisible screen")
                return self.start_app_as(dict(spec, app_window="hidden"), wait, stop)
            if not self.start_app_as(spec, wait, stop) and not self.app_running(spec) and "closed straight away" in self.app_errors.get(spec["name"], ""):
                self.app_note(spec["name"], "its window could not open, so it starts again on an invisible screen")
                return self.start_app_as(dict(spec, app_window="hidden"), wait, stop)
            return self.app_running(spec)
        return self.start_app_as(spec, wait, stop)

    def app_note(self, name, text):
        path = self.app_log(name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as log:
            log.write(f"== {time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")

    def start_app_as(self, spec, wait=90, stop=lambda: False):
        """Starts the program a server needs, on this machine's screen or on an invisible one, and waits until its port
        answers. True when it runs; otherwise app_errors says why."""
        self.app_errors.pop(spec.get("name"), None)
        if not spec.get("app_command") or self.app_running(spec):
            return True
        with self.lock:
            proc = self.apps.get(spec["name"])
            if not proc or proc.poll() is not None:
                env = dict(os.environ)
                runtime = env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
                env.setdefault("XDG_RUNTIME_DIR", runtime)
                log_path = self.app_log(spec["name"])
                os.makedirs(os.path.dirname(log_path), exist_ok=True)
                with open(log_path, "a") as log:
                    log.write(f"\n== {time.strftime('%Y-%m-%d %H:%M:%S')} Studio starts ({spec.get('app_window', 'screen')}): {spec['app_command']}\n")
                    log.flush()
                    if spec.get("app_window") == "hidden":
                        try:
                            env["DISPLAY"] = self.invisible_screen(spec["name"], log)
                        except RuntimeError as e:
                            self.app_errors[spec["name"]] = str(e)
                            log.write(str(e) + "\n")
                            return False
                        # X11 on the invisible screen, never the desktop's Wayland: without WAYLAND_DISPLAY a program still finds the
                        # default socket (wayland-0) and opens there, which crashed Blender while no monitor was connected.
                        env["WAYLAND_DISPLAY"] = "studio-no-wayland"
                        env["XDG_SESSION_TYPE"] = "x11"
                    else:
                        env.setdefault("DISPLAY", ":0")
                        if "WAYLAND_DISPLAY" not in env and os.path.exists(os.path.join(runtime, "wayland-0")):
                            env["WAYLAND_DISPLAY"] = "wayland-0"
                    proc = subprocess.Popen(shlex.split(os.path.expanduser(spec["app_command"])), env=env, stdin=subprocess.DEVNULL,
                                            stdout=log, stderr=subprocess.STDOUT, start_new_session=True, cwd=os.path.expanduser("~"))
                self.apps[spec["name"]] = proc
        deadline = time.time() + wait
        while time.time() < deadline and not stop():
            if self.app_running(spec):
                return True
            if proc.poll() is not None:
                self.app_errors[spec["name"]] = f"it closed straight away; see {self.app_log(spec['name'])}"
                return False
            time.sleep(0.5)
        if not self.app_running(spec):
            self.app_errors[spec["name"]] = f"nothing answered on port {spec['app_port']} in time; see {self.app_log(spec['name'])}"
            return False
        return True

    def stop_app(self, name):
        """Ends the program only if Studio started it; one someone opened by hand is left alone. Its invisible screen goes too."""
        with self.lock:
            proc = self.apps.pop(name, None)
            screen = self.screens.pop(name, None)
        if screen and screen[0].poll() is None:
            screen[0].terminate()
        if not proc or proc.poll() is not None:
            return False
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(10)
        except (OSError, subprocess.TimeoutExpired):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
        return True

    def status(self):
        out = []
        for spec in self.servers:
            client = self.clients.get(spec["name"])
            proc = self.apps.get(spec["name"])
            app = {"command": spec["app_command"], "port": spec["app_port"], "window": spec.get("app_window", "screen"), "running": self.app_running(spec),
                   "started_by_studio": bool(proc and proc.poll() is None), "hidden_available": bool(shutil.which("Xvfb")),
                   "error": self.app_errors.get(spec["name"], "")} if spec.get("app_command") else None
            out.append({**masked_mcp_spec(spec), "app": app, "state": client.state if client else "off", "error": client.error if client else "",
                        "server_info": client.server_info if client else {}, "log": list(client.log)[-40:] if client else [],
                        "tools": [{"name": t["name"], "description": str(t.get("description") or "")[:300]} for t in client.tools] if client else []})
        return out

    def tools_for(self, username):
        """The tools a run may use: from enabled servers that started, and for users only from servers shared with them.
        Waits a little for servers still starting, so the first run after a restart sees them."""
        user = APP.auth.users.get(username) or {}
        allowed = [s for s in self.servers if s["enabled"] and (user.get("role") == "admin" or s["users"])]
        deadline = time.monotonic() + 20
        tools = {}
        for spec in allowed:
            client = self.clients.get(spec["name"])
            if not client:
                continue
            client.ready.wait(max(0.0, deadline - time.monotonic()))
            if client.state != "ready":
                continue
            for tool in client.tools:
                name = mcp_tool_name(spec["name"], tool["name"])
                schema = tool.get("inputSchema") if isinstance(tool.get("inputSchema"), dict) else {}
                parameters = {**{k: v for k, v in schema.items() if k != "$schema"}, "type": "object", "properties": dict(schema.get("properties") or {})}
                parameters["properties"]["progress"] = PROGRESS_PROPERTY
                description = (str(tool.get("description") or tool.get("title") or tool["name"]).strip()[:1000] +
                               f" (MCP server {spec['name']})")
                tools[name] = {"server": spec["name"], "tool": tool["name"], "approval": spec["approval"], "client": client,
                               "schema": {"type": "function", "function": {"name": name, "description": description, "parameters": parameters}}}
        return tools


def mcp_tool_name(server, tool):
    """mcp__server__tool, in the characters and length the model APIs accept."""
    name = "mcp__" + server + "__" + re.sub(r"[^A-Za-z0-9_-]", "_", tool)
    return name if len(name) <= 64 else name[:55] + "_" + hashlib.sha1(name.encode()).hexdigest()[:8]


def mcp_result_text(result, shown=False, kept=()):
    """The model sees text here. Pictures are described; with shown, they follow in the next message for models with vision.
    kept: the ids Studio saved them under, in order, for save_picture."""
    parts, kept = [], list(kept)
    for item in result.get("content") or []:
        kind = item.get("type") if isinstance(item, dict) else None
        if kind == "text":
            parts.append(str(item.get("text", "")))
        elif kind in ("image", "audio"):
            size = len(item.get("data") or "") * 3 // 4
            seen = shown and kind == "image" and item.get("mimeType") in IMAGE_TYPES
            keeps = kind == "image" and item.get("mimeType") in IMAGE_TYPES and kept
            picture = kept.pop(0) if keeps else None
            parts.append(f"[{kind} returned: {item.get('mimeType') or 'unknown type'}, about {max(1, size // 1024)} KB, "
                         + ("attached to the next message for you to look at" if seen else "not shown to you")
                         + (f'; Studio kept it as picture "{picture}", which save_picture can put in the project]' if picture else "]"))
        elif kind == "resource":
            resource = item.get("resource") or {}
            parts.append(str(resource.get("text")) if "text" in resource else f"[resource {resource.get('uri', '')}: {resource.get('mimeType') or 'binary'}]")
        elif kind == "resource_link":
            parts.append(f"[link: {item.get('name') or ''} {item.get('uri', '')}]".replace("  ", " "))
    if not parts and result.get("structuredContent") is not None:
        parts.append(json.dumps(result["structuredContent"], ensure_ascii=False, indent=1))
    text = "\n".join(parts) or "(the tool returned nothing)"
    return text if len(text) <= MCP_MAX_OUTPUT else text[:MCP_MAX_OUTPUT] + f"\n[output cut: {len(text) - MCP_MAX_OUTPUT} more characters]"


def build_mcp_prompt(run):
    servers = []
    for server in dict.fromkeys(e["server"] for e in run.mcp_tools.values()):
        spec = next((s for s in APP.mcp.servers if s["name"] == server), {})
        client = next(e["client"] for e in run.mcp_tools.values() if e["server"] == server)
        about = spec.get("description") or client.instructions
        servers.append(f"- {server}" + (f": {about[:600]}" if about else ""))
    folder = run.ws["path"]
    return ("Tools named mcp__<server>__<tool> come from these connected MCP servers. They act outside the project folder, "
            "and outside the sandbox, so use them only when the task needs them:\n" + "\n".join(servers)
            + f"\nThey run on Studio's server \"{socket.gethostname()}\" ({platform.system()}), not in a sandbox or on the user's own computer; "
            f"there the project folder is {folder}: an MCP tool that reads or writes a file needs "
            f"its full path, such as {folder}/assets/clip.mp4. Your own file tools and commands keep using paths relative to "
            "the project.\nIf a service's tools cannot do what the user asks, or refuse it, do not reach the service another way: no "
            "searching for its passwords, tokens or settings files, no direct requests to it. Say what the tools cannot do and ask "
            "the user how to go on (ask_user if you have it).")


COMFY_PROGRESS_TOOLS = {"generate_image", "generate_images", "generate_video", "generate_music"}


def comfy_progress(tool, progress, stop):
    """ComfyUI tells only the client that queued a job about its sampler steps, and the comfyui MCP server queues as Studio,
    so Studio's ComfyWatch hears them: report them as the call's progress until the call returns (stop is set)."""
    COMFY_WATCH.ensure(APP.cfg["comfyui_url"])
    began, what = time.time() - 1, {"generate_video": "Video", "generate_music": "Music"}.get(tool, "Picture")
    while not stop.wait(1.0):
        with COMFY_WATCH.lock:
            jobs = sorted((t, job) for job, t in COMFY_WATCH.started.items() if t >= began)
            step = COMFY_WATCH.progress.get(jobs[-1][1]) if jobs else None
        if not jobs:
            progress({"message": "Waiting for ComfyUI (queue, or making room on the graphics card)"})
            continue
        label = what + (f" {len(jobs)}" if tool == "generate_images" else "")
        if step and isinstance(step.get("value"), (int, float)) and step.get("max"):
            progress({"progress": step["value"], "total": step["max"], "message": f"{label} · step {step['value']}/{step['max']}"})
        else:
            progress({"message": f"{label} · preparing"})


# ---------------------------------------------------------------------------
# The agent's web browser, shown live in the Browser tab
# ---------------------------------------------------------------------------

class BrowserView:
    """Studio's Browser tab: tools/browser_view.mjs watches the agent's Chromium (session.json from tools/browser_mcp.py),
    streams its screen as JPEG frames and passes the user's input on. While the user holds the browser ("Take over"),
    the agent's browser calls wait (tool_mcp). The viewer stops after a minute nobody looks; a hold ends with it."""
    IDLE_S = 60

    def __init__(self):
        self.lock = threading.Condition()
        self.proc, self.cdp, self.frame, self.seq, self.meta, self.error = None, None, None, 0, {}, ""
        self.seen, self.held, self.agent_at, self.sized = 0.0, False, 0.0, False
        self.shape, self.checks = None, 0   # what the tab asked for ({phone} or {height}); view_page checks under way
        threading.Thread(target=self.janitor, daemon=True).start()

    def session_file(self):
        spec = APP.mcp.spec("browser") or {}
        folder = (spec.get("env") or {}).get("BROWSER_STATE") or os.path.join(APP.data_dir, "browser")
        return os.path.join(os.path.expanduser(folder), "session.json")

    def ensure(self):
        self.seen = time.time()
        session = read_json(self.session_file(), None)
        if not session or not session.get("cdp"):
            raise ApiError(409, "the agent's browser is not running: it starts with the browser tool server (Account & settings → Tool servers)")
        with self.lock:
            if self.proc and self.proc.poll() is None and self.cdp == session["cdp"]:
                return
            self.stop_viewer()
            if not shutil.which("node"):
                raise ApiError(500, "Node is not installed, so Studio cannot show the browser")
            self.cdp, self.error, self.shape = session["cdp"], "", None
            self.proc = subprocess.Popen(["node", os.path.join(os.path.dirname(VIEW_PAGE_SCRIPT), "browser_view.mjs"), session["cdp"]], stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
            threading.Thread(target=self.read, args=(self.proc,), daemon=True).start()

    def read(self, proc):
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            with self.lock:
                if msg.get("type") == "frame":
                    self.seq += 1
                    self.frame = msg
                    self.meta = {k: msg.get(k) for k in ("url", "title", "pages")}
                elif msg.get("type") == "page":
                    self.meta = {k: msg.get(k) for k in ("url", "title", "pages")}
                    self.seq += 1
                elif msg.get("type") == "error":
                    self.error = str(msg.get("message") or "")[:300]
                self.lock.notify_all()
        err = proc.stderr.read()[-400:] if proc.stderr else ""
        with self.lock:
            if proc is self.proc:
                self.error = self.error or (err.strip().splitlines() or ["the viewer stopped"])[-1]
                self.proc = None
            self.lock.notify_all()

    def stop_viewer(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
        self.proc = None

    def janitor(self):
        while True:
            time.sleep(10)
            with self.lock:
                if self.seen and time.time() - self.seen > self.IDLE_S:
                    self.fit(None)
                    self.stop_viewer()
                    self.held, self.seen = False, 0.0
                    self.lock.notify_all()

    def fit(self, size):
        """A phone's Take over shows the page at the phone's width ({width, height}); None gives the agent its 1280x800
        back. Called with the lock held."""
        if not size and not self.sized:
            return
        self.sized = bool(size)
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.write(json.dumps({"type": "size", **(size or {"width": 1280, "height": 800})}) + "\n")
                self.proc.stdin.flush()
            except OSError:
                pass

    def tell(self, msg):
        """One line to the viewer, if it runs. Called with the lock held."""
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.write(json.dumps(msg) + "\n")
                self.proc.stdin.flush()
            except OSError:
                pass

    def want_shape(self, phone, height):
        """The Browser tab's view: a phone, or the height that fills it at 1280 wide (None: the window's own size).
        Sent on when it changes, by more than a few pixels for a height."""
        shape = {"phone": True} if phone else {"height": max(600, min(1600, height))} if height else None
        old = self.shape or {}
        if shape == self.shape or (shape and "height" in shape and "height" in old and abs(shape["height"] - old["height"]) < 24):
            return
        with self.lock:
            self.shape = shape
            self.tell({"type": "shape", **(shape or {})})

    def checking(self, on):
        """view_page sets the size of the tab it checks; the viewer leaves sizes alone meanwhile."""
        with self.lock:
            self.checks = max(0, self.checks + (1 if on else -1))
            if on and self.checks == 1 or not on and self.checks == 0:
                self.tell({"type": "pause", "on": bool(self.checks)})

    def state(self, frame=None):
        out = {"seq": self.seq, "phone": bool(self.shape and self.shape.get("phone")), "held": self.held, "agent": time.time() - self.agent_at < 4, "error": self.error, **self.meta}
        if frame:
            out.update(data=frame.get("data"), width=frame.get("width"), height=frame.get("height"), scrollY=frame.get("scrollY"))
        return out

    def wait_frame(self, after, timeout=10.0):
        self.ensure()
        end = time.time() + timeout
        with self.lock:
            while self.seq <= after and time.time() < end and self.proc is not None:
                self.lock.wait(min(1.0, end - time.time()))
            return self.state(self.frame if self.seq > after else None)

    def send(self, events):
        self.ensure()
        with self.lock:
            if not self.proc or self.proc.poll() is not None:
                raise ApiError(409, "the viewer is not running")
            for e in events:
                self.proc.stdin.write(json.dumps(e) + "\n")
            self.proc.stdin.flush()


BROWSER_INPUT = {"mouse", "key", "text", "go", "back", "forward", "reload", "tab"}


def browser_view():
    if getattr(APP, "browser", None) is None:
        APP.browser = BrowserView()
    return APP.browser


@route("GET", "/api/browser/frame", admin=True)
def browser_frame(req):
    view = browser_view()
    view.ensure()
    view.want_shape(req.arg("view") == "phone", to_int(req.arg("fill"), 0, lo=0, hi=4000))
    return view.wait_frame(to_int(req.arg("after"), 0, lo=0), timeout=0.2 if req.arg("now") else 10.0)


@route("POST", "/api/browser/input", admin=True)
def browser_input(req):
    view, events = browser_view(), req.field("events")
    if not isinstance(events, list) or not events or len(events) > 50 or not all(isinstance(e, dict) and e.get("type") in BROWSER_INPUT for e in events):
        raise ApiError(400, "events is a list of browser input events")
    if not view.held and any(e["type"] not in ("tab",) for e in events):
        raise ApiError(409, "take over the browser first")
    for e in events:
        if e["type"] == "go" and not re.match(r"(?i)^(https?://)?[^\s/]+\.[^\s]+$|^https?://\S+$", str(e.get("url") or "")):
            raise ApiError(400, "type a web address, such as example.com")
    view.send(events)
    return {"ok": True}


@route("POST", "/api/browser/hold", admin=True)
def browser_hold(req):
    view = browser_view()
    view.ensure()
    with view.lock:
        view.held = req.field("hold") is True
        size = req.field("size")
        if view.held and isinstance(size, dict):
            view.fit({"width": to_int(size.get("width"), 390, lo=320, hi=1920), "height": to_int(size.get("height"), 700, lo=240, hi=1400)})
        elif not view.held:
            view.fit(None)
        view.lock.notify_all()
    return view.state()


def browser_page():
    """(title, url) of the page the agent's browser shows, or None: the Browser tab's page if it is watching, else
    DevTools' list of pages, newest first."""
    view = getattr(APP, "browser", None)
    if view and view.proc and view.proc.poll() is None and view.meta.get("url"):
        return view.meta.get("title") or "", view.meta["url"]
    session = read_json(browser_view().session_file(), None) if getattr(APP, "mcp", None) else None
    if not session or not session.get("cdp"):
        return None
    try:
        with urllib.request.urlopen(session["cdp"] + "/json/list", timeout=2) as resp:
            pages = [p for p in json.load(resp) if p.get("type") == "page"]
    except (OSError, ValueError):
        return None
    return (pages[0].get("title") or "", pages[0].get("url") or "") if pages else None


# Words that point at the browser's page. Only then is the open page named: on 2026-11-18 Keystone, asked to write a
# page about dyslexia in an empty folder, read the quiz another project had left in the browser, copied its style and
# linked to a quiz.html that was not there.
BROWSER_WORDS_RE = re.compile(
    r"(?i)\b(?:this|that|the open|the current|the same)\s+(?:web\s*)?(?:page|site|website|tab|article)\b|\bbrowser\b|\bon screen\b"
    r"|(?:αυτή|αυτήν|αυτό|εκείνη|εκείνο|την ανοιχτή|την ίδια)\s+(?:(?:τη|την|το|η|ο)\s+)?(?:ιστο)?(?:σελίδα|ιστότοπο|site|καρτέλα|άρθρο)"
    r"|φυλλομετρητ|περιηγητ|πρόγραμμα περιήγησης|στην οθόνη")


def browser_context(username, message=""):
    """A line for the model when the browser has a page open and the message points at it ("this page", "the
    browser"), so those words mean something without the user naming the page. Only for people who may use the browser
    server, and never "Studio note:" (LOOP_NOTES)."""
    if not BROWSER_WORDS_RE.search(message or ""):
        return ""
    spec = APP.mcp.spec("browser") if getattr(APP, "mcp", None) else None
    user = APP.auth.users.get(username) or {}
    if not spec or not spec.get("enabled") or not (user.get("role") == "admin" or spec.get("users")):
        return ""
    page = browser_page()
    if not page or not page[1] or page[1] in ("about:blank", "chrome://newtab/"):
        return ""
    title, url = str(page[0]).replace("\n", " ")[:200], urllib.parse.unquote(str(page[1]))   # Greek addresses read as Greek
    return (f"The web browser (the mcp__browser__ tools; the user sees it in Studio's Browser tab) has this page open: "
            f"«{title}» at {url[:500].replace(chr(10), ' ')}. When the user says this page, the page or the browser, they mean it: read it with "
            "browser_snapshot before answering; do not open it again. It is not part of this project unless they say so.\n\n")


def wait_for_browser(run):
    """The agent's browser calls wait while the user has taken over the browser."""
    view = getattr(APP, "browser", None)
    if not view or not view.held:
        return
    run.emit("status", text="Waiting: you have the browser. Press Hand back in the Browser tab and the agent carries on.")
    with view.lock:
        while view.held and not run.stop:
            view.lock.wait(0.25)


def tool_mcp(run, name, args):
    entry = run.mcp_tools[name]
    args = {k: v for k, v in args.items() if k != "progress"}
    if not (entry["approval"] == "with_commands" and run.auto_commands) or run.plan_mode:
        shown = json.dumps(args, ensure_ascii=False, indent=1)
        decision, note = run.ask(kind="mcp" if entry["approval"] == "with_commands" else "mcp_ask", server=entry["server"], tool=entry["tool"],
                                 arguments=shown if len(shown) <= 8000 else shown[:8000] + "\n…", **({"plan": True} if run.plan_mode else {}))
        if decision != "accept":
            return False, "Tool call skipped", "The user rejected this tool call." + (f" Note: {note}" if note else "")
    spec = APP.mcp.spec(entry["server"]) or {}
    if spec.get("app_command") and not APP.mcp.app_running(spec):
        program = os.path.basename(shlex.split(spec["app_command"])[0])
        run.emit("status", text=f"Starting {program} for the {entry['server']} tools…")
        if not APP.mcp.start_app(spec, stop=lambda: run.stop):
            why = APP.mcp.app_errors.get(entry["server"]) or f"nothing answered on port {spec['app_port']}"
            return (False, f"{program} did not start", f"Studio could not start {program} for the {entry['server']} server: {why}. "
                    "Tell the user; an admin can fix it under Tool servers.")
    if entry["server"] == "browser":
        wait_for_browser(run)
        if run.stop:
            return False, "Stopped", "The run was stopped."
        if getattr(APP, "browser", None):
            APP.browser.agent_at = time.time()
        if entry["tool"] in ("browser_navigate", "browser_tabs") and not getattr(run, "browser_shown", False):
            run.browser_shown = True
            run.emit("open_browser")             # the user's window opens the Browser tab the first time a run uses it
    if entry["tool"] in CACHE_SWAP_TOOLS:
        cache_save(run, "before " + entry["tool"])       # the tool may unload Strata to make room on the card
    if entry["server"] in ENGINE_SERVERS:
        run.engines_used = True                          # released before the model's next turn (release_engines)
    client = APP.mcp.current(entry["server"], entry["client"])
    row, last = getattr(run, "current_tool", None), [0.0]

    def progress(note):
        """A tool's own progress, shown on its row: a bar when it gives a total, and its message."""
        now = time.monotonic()
        if now - last[0] < 0.5 and not (note.get("total") and note.get("progress") == note.get("total")):
            return
        last[0] = now
        done, total = note.get("progress"), note.get("total")
        percent = round(min(100.0, 100.0 * done / total)) if isinstance(done, (int, float)) and isinstance(total, (int, float)) and total > 0 else None
        message = str(note.get("message") or "")[:200]
        # A tool that is waiting (for the graphics card, a queue, an engine) says so in words starting "Waiting"; the page shows it amber.
        run.emit("tool_progress", id=row, percent=percent, message=message, waiting=message.lower().startswith("waiting"))
    watching = threading.Event()
    if APP.cfg["comfyui_url"] and entry["tool"] in COMFY_PROGRESS_TOOLS:
        threading.Thread(target=comfy_progress, args=(entry["tool"], progress, watching), daemon=True).start()
    try:
        result = client.call(entry["tool"], args, lambda: run.stop, on_progress=progress)
    except McpError as exc:
        return False, f"{entry['server']}: {exc}"[:120], f"Error from MCP server {entry['server']}: {exc}"
    finally:
        watching.set()
        if entry["server"] == "browser" and getattr(APP, "browser", None):
            APP.browser.agent_at = time.time()
    shown, kept = keep_tool_pictures(run, entry, result)
    text = mcp_result_text(result, shown=shown, kept=kept if tool_enabled("save_picture") else ())
    failed = result.get("isError") is True
    return not failed, f"{entry['server']} · {entry['tool']}" + (" failed" if failed else ""), text


MAX_TOOL_PICTURES = 8          # a comfyui generate_images batch; fewer left pictures 4-8 without an id for save_picture


def keep_tool_pictures(run, entry, result):
    """The pictures a tool returned are saved beside the chat and shown there, so save_picture can put them in the project.
    For a model that can see, they are also attached to a note after the tool results, since tool results carry only text.
    Returns (whether the model sees them, their ids in order)."""
    if not getattr(run, "session", None):
        return False, []
    pictures = []
    for item in result.get("content") or []:
        if not (isinstance(item, dict) and item.get("type") == "image" and item.get("mimeType") in IMAGE_TYPES):
            continue
        try:
            data = base64.b64decode(item.get("data") or "", validate=False)
        except ValueError:
            continue
        if 0 < len(data) <= MAX_IMAGE_BYTES and len(pictures) < MAX_TOOL_PICTURES:
            pictures.append({"kind": "image", "name": f"{entry['server']}-{entry['tool']}.{IMAGE_TYPES[item['mimeType']]}", "mime": item["mimeType"], "data": data})
    if not pictures:
        return False, []
    stored = store_images(run.session["id"], pictures)
    seen = bool(getattr(run, "vision", False))
    if seen:
        run.pending_pictures.append((f"{entry['server']} · {entry['tool']}", [p["id"] for p in stored]))
    run.emit("tool_picture", server=entry["server"], tool=entry["tool"], images=stored)
    return seen, [p["id"] for p in stored]


@route("GET", "/api/admin/mcp", admin=True)
def admin_mcp(req):
    return {"servers": APP.mcp.status(), "enabled": APP.cfg["mcp"]}


@route("POST", "/api/admin/mcp", admin=True)
def admin_mcp_save(req):
    servers = req.field("servers")
    if not isinstance(servers, list) or len(servers) > 50:
        raise ApiError(400, "servers must be a list of at most 50")
    with APP.mcp_edit:
        APP.mcp.replace(servers)
    return {"servers": APP.mcp.status()}


@route("POST", "/api/admin/mcp/app", admin=True)
def admin_mcp_app(req):
    """Start or stop the program a tool server needs (Blender for the blender server)."""
    spec = APP.mcp.spec(req.field("name"))
    if not spec or not spec.get("app_command"):
        raise ApiError(404, "that server needs no program")
    if req.field("action") == "stop":
        if not APP.mcp.stop_app(spec["name"]):
            raise ApiError(409, "it was not started by Studio, so Studio leaves it alone; close it on the server")
        return {"running": APP.mcp.app_running(spec)}
    if req.field("action") != "start":
        raise ApiError(400, "action must be start or stop")
    if not APP.mcp.start_app(spec):
        raise ApiError(502, "it did not start: " + (APP.mcp.app_errors.get(spec["name"]) or f"see {APP.mcp.app_log(spec['name'])}"))
    return {"running": True}


@route("POST", "/api/admin/mcp/restart", admin=True)
def admin_mcp_restart(req):
    APP.mcp.restart(req.field("name"))
    return {"ok": True}


# ---------------------------------------------------------------------------
# Agent: prompt, model calls, loop
# ---------------------------------------------------------------------------

PLAN_OLD_RULE = "(b) For non-trivial tasks, write a short numbered plan before you start."
PLAN_RULE = ("(b) For a task with more than two steps, call plan first with short step names, then work through them in order. "
             "Call plan again if the plan changes.")
SYSTEM_PROMPT = """\
You are an expert software engineer working in the user's project. All file paths are relative to the project root.

Method:
(a) Explore first: list, read and search the relevant files before editing anything.
(b) For non-trivial tasks, write a short numbered plan before you start.
(c) Prefer replace_in_file with an exact, unique old_text; use write_file only for new files or small full rewrites.
(d) After editing, run the project's tests, build or linter when they exist.
(e) If something fails, read the error, fix it and re-run. Make at most 3 attempts per problem, then report the blocker.
(f) End with finish(summary) for tasks that changed files; for plain questions just answer.
(g) Every tool call has a "progress" argument. Always set it to "current step/total steps" of your plan, such as "3/8", and update the total if the plan changes. People waiting for you rely on it to know how long you will take. For a small task with no plan, use "1/1".

Reply in the language of the user's message. Never mix in words or characters from other languages or scripts.
Use only the provided tools, with valid JSON arguments, and never guess file contents: read them."""


MAX_SKILLS = 200
MAX_SKILL_NAME = 80
MAX_SKILL_DESCRIPTION = 300
MAX_SKILL_TEXT = 20000
SKILLS_PROMPT = """\
Skills are your own notes, kept between tasks and projects. The user's requests and project rules always come first.
At the start of every task, look at the saved skills listed below. Once you know what the task involves, if one could apply, call use_skill and follow it before you edit anything.
Before you call finish, ask yourself whether you worked out something you would do the same way in another project. If so, save it with save_skill first, unless a saved skill already covers it. Write general steps, without this project's paths. Never put passwords, keys or personal data in a skill. Do not save routine things such as a plain typo fix."""


ENGLISH_WORDS = {"the", "and", "of", "to", "is", "are", "this", "that", "with", "for", "it", "you", "my", "please", "what", "why", "how", "from", "should",
                 "when", "not", "have", "there", "where", "can", "does", "which", "add", "fix", "make", "create"}


USER_EXTRAS_END = "\n[End of attached context]\n\n"


def typed_part(content):
    """The words the user typed, without the commands and files attached in front of them."""
    return content.split(USER_EXTRAS_END)[-1]


def detect_language(text):
    """'Greek' or 'English' when the text is clearly one of them, otherwise None."""
    text = re.sub(r"```.*?(```|\Z)|`[^`\n]*`", " ", text, flags=re.S)
    letters = len(re.findall(r"[^\W\d_]", text))
    if letters and len(re.findall(r"[Ͱ-Ͽἀ-῿]", text)) / letters >= 0.5:
        return "Greek"
    words = re.findall(r"[^\W\d_]+", text.lower())
    if len(words) >= 3 and sum(word in ENGLISH_WORDS for word in words) / len(words) >= 0.1:
        return "English"
    return None


def user_language(messages):
    """The language of the user's latest message. A short reply such as "ok" says nothing, so look further back;
    a longer message in another language leaves it unknown, so it is never mislabelled."""
    for message in reversed(messages):
        content = message.get("content")
        if message.get("role") != "user" or not isinstance(content, str) or content.startswith(LOOP_NOTES):
            continue
        content = typed_part(content)
        language = detect_language(content)
        if language or len(content.split()) >= 4:
            return language
    return None


def earlier_instructions(run):
    """What the user asked for in this chat. Long sessions forget rules, so it is repeated in the system prompt."""
    if not APP.cfg["rule_reminders"]:
        return None
    asked = [typed_part(m["content"]) for m in run.session["messages"] if m.get("role") == "user" and isinstance(m.get("content"), str)
             and not m["content"].startswith(LOOP_NOTES)]
    if not asked or (len(asked) < 2 and run.turns < APP.cfg["rule_reminder_turns"]):
        return None
    shown = ["- " + text[:1500] + ("…" if len(text) > 1500 else "") for text in (asked if len(asked) <= 8 else asked[:2] + asked[-6:])]
    return "The user's requests in this chat, in order. Rules in them still apply, unless a later message changes them:\n" + "\n".join(shown)[:8000]


def prompt_sections(run):
    """The system prompt in two kinds of part: (fixed, changing). Fixed parts hold the rules; changing parts follow the
    chat and the folder (language, reminders, tree, skills, servers, the open file) and are taken once per run."""
    root = run.ws["path"]
    # Runs with the plan tool off keep the old wording, so the overnight results stay comparable.
    fixed = [SYSTEM_PROMPT.replace(PLAN_OLD_RULE, PLAN_RULE) if APP.cfg["plan_tool"] and run.tools_enabled else SYSTEM_PROMPT]
    if sandboxed(run.ws):
        settings = sandbox_settings(run.ws)
        internet = {"off": "There is no internet access.",
                    "install": "Commands that install dependencies (such as npm install, pip install, cargo build) "
                               "get internet access for that command only; all other commands have none.",
                    "on": "Internet access is available."}[settings["network"]]
        ports = settings["ports"] if settings["network"] == "on" else []
        published = f" Servers must listen on 0.0.0.0; these ports reach the user's machine: {ports}." if ports else ""
        fixed.append("The `run` tool executes in an isolated container: the project is the current directory (/work) "
                     f"and nothing else on the machine is visible. {internet}{published}")
    elif run.ws.get("helper"):
        fixed.append(helper_prompt(run.ws))
    changing = {}
    language = user_language(run.session["messages"])
    if language:
        changing["language"] = (f"The user writes in {language}. Write everything you say to the user in {language}, unless they ask for another language."
                                + (" Write Greek only in the Greek alphabet: never Greek words in Latin letters (Greeklish), and no characters from other scripts. "
                                   "Code, commands and file names stay as they are." if language == "Greek" else ""))
    reminder = earlier_instructions(run)
    if reminder:
        changing["reminder"] = reminder
    for name in ("STUDIO.md", "CLAUDE.md", "AGENTS.md"):
        try:
            with open(os.path.join(root, name), encoding="utf-8", errors="replace") as fh:
                fixed.append(f"Project rules ({name}):\n{fh.read(12000)}")
            break
        except OSError:
            continue
    # Taken once per run: a system prompt that changes between turns makes the engine read the whole conversation again.
    if run.project_tree is None:
        lines, truncated = tree_lines(root, 2, 200)
        run.project_tree = "Project tree (2 levels):\n" + ("\n".join(lines) or "(empty)") + ("\n[truncated]" if truncated else "")
    changing["tree"] = run.project_tree
    if APP.cfg["skills"] and run.tools_enabled:
        if run.skills_prompt is None:
            run.skills_prompt = build_skills_prompt(run)
        changing["skills"] = run.skills_prompt
    if run.mcp_tools:
        if run.mcp_prompt is None:
            run.mcp_prompt = build_mcp_prompt(run)
        changing["mcp"] = run.mcp_prompt
    ctx = run.context
    if isinstance(ctx, dict) and isinstance(ctx.get("path"), str):
        text = f"The user currently has this file open: {ctx['path']}"
        sel = ctx.get("selection")
        if isinstance(sel, dict) and isinstance(sel.get("text"), str) and sel["text"]:
            text += (f"\nSelected lines {sel.get('start_line')}-{sel.get('end_line')}:\n"
                     f"```\n{sel['text'][:8000]}\n```")
        changing["open_file"] = text
    return fixed, changing


STUDIO_CONTEXT = "Studio context, newer than the system prompt (it replaces what the system prompt says on the same subject):"


def build_system_prompt(run):
    """The system prompt for this run's requests. With stable_prompt a chat keeps the prompt of its first run, so the
    engine can reuse everything already read; what changed since goes into a note before the new message instead."""
    if run.frozen_prompt is not None:
        return run.frozen_prompt
    fixed, changing = prompt_sections(run)
    whole = "\n\n".join(fixed + list(changing.values()))
    if not APP.cfg["stable_prompt"]:
        if APP.cfg["rule_reminders"]:
            return whole          # reminders may start part-way through a run, so the prompt is built each turn
        run.frozen_prompt = whole
        return whole
    session = run.session
    fixed_text = "\n\n".join(fixed)
    if session.get("prompt_fixed") != fixed_text or not isinstance(session.get("system_prompt"), str):
        session.update(system_prompt=whole, prompt_fixed=fixed_text, prompt_sections=changing)
    else:
        seen = session.get("prompt_sections") or {}
        changed = [value for key, value in changing.items() if seen.get(key) != value]
        if changed:
            add_context_note(session["messages"], STUDIO_CONTEXT + "\n\n" + "\n\n".join(changed) + "\n\n")
            session["prompt_sections"] = {**seen, **changing}
    run.frozen_prompt = session["system_prompt"]
    return run.frozen_prompt


def add_context_note(messages, note):
    """Puts the note at the start of the message this run answers, among the extras the language check skips."""
    for message in reversed(messages):
        content = message.get("content")
        if message.get("role") != "user" or not isinstance(content, str) or content.startswith(LOOP_NOTES):
            continue
        message["content"] = note + content if USER_EXTRAS_END in content else note + USER_EXTRAS_END + content
        return


def trimmed_summary(name, args, output):
    """A short stand-in for an old tool output, keeping what the model is most likely to still need."""
    lines = output.splitlines()
    head, keep = f"[older output trimmed: {name or 'tool'}", []
    if name == "run":
        head += f" `{str(args.get('command'))[:200]}`"
        keep = lines[:1] + lines[-5:]
    elif name == "read_file":
        head += f" {args.get('path')}, {len(lines)} lines; read it again if you need it"
        keep = lines[:3]
    elif name == "search":
        head += f" for {str(args.get('query'))[:100]!r}, {len(lines)} lines"
        keep = lines[:5]
    elif name == "list_files":
        head += f" {args.get('path', '.')}, {len(lines)} entries"
        keep = lines[:10]
    else:
        keep = lines[:2]
    body = "\n".join(line[:200] for line in keep)
    return head + "]" + ("\n" + body if body else "")


def trim_context(messages, smart=False, limit=90000):
    """Shorten older tool outputs once the total grows beyond the limit (keep the last 6)."""
    idx = [i for i, m in enumerate(messages) if m["role"] == "tool"]
    if sum(len(messages[i]["content"]) for i in idx) <= limit:
        return
    calls = {}
    for m in messages:
        for call in m.get("tool_calls") or []:
            calls[call.get("id")] = call.get("function") or {}
    for i in idx[:-6]:
        content = messages[i]["content"]
        if content.startswith("[older output trimmed"):
            continue
        if not smart:
            messages[i]["content"] = "[older output trimmed]"
            continue
        function = calls.get(messages[i].get("tool_call_id"), {})
        try:
            args = json.loads(function.get("arguments") or "{}")
        except ValueError:
            args = {}
        messages[i]["content"] = trimmed_summary(function.get("name"), args if isinstance(args, dict) else {}, content)


def estimate_tokens(obj):
    return len(json.dumps(obj)) // 4


def parse_tool_calls(raw_calls):
    calls = []
    for tc in raw_calls:
        fn = tc.get("function") or {}
        calls.append({"id": tc.get("id") or f"call_{secrets.token_hex(4)}", "type": "function",
                      "function": {"name": fn.get("name") or "", "arguments": fn.get("arguments") or ""}})
    return calls


def studio_gateway_headers():
    """Ask the Manager to keep the full thinking budget, and to leave its planning-loop guard off. That guard takes the
    tools away and allows 1536 tokens, so a long tool call arrives cut off as text, and Studio has its own checks."""
    return {**gateway_headers(), **({"X-Studio-Full-Thinking": "1"} if APP.cfg["full_thinking"] else {}),
            **({"X-Studio-No-Planning-Guard": "1"} if APP.cfg["no_planning_guard"] else {})}


def open_chat(run, payload):
    conn, prefix = gateway_connect(1200)
    run.conn = conn
    try:
        conn.connect()
        conn.request("POST", prefix + "/v1/chat/completions", json.dumps(payload).encode(), studio_gateway_headers())
        resp = conn.getresponse()
    except (OSError, http.client.HTTPException) as exc:
        conn.close()
        run.conn = None
        raise ModelError(f"gateway unreachable: {exc}")
    return conn, resp


def stream_completion(run, payload):
    """Stream one completion, emitting token/thinking events. Raises NothingUsable if nothing arrived."""
    body = dict(payload, stream=True, stream_options={"include_usage": True})
    try:
        conn, resp = open_chat(run, body)
    except ModelError as exc:
        raise NothingUsable(str(exc))
    content, calls, usage, finish_reason = [], {}, None, None
    try:
        if resp.status != 200:
            raise NothingUsable(f"gateway returned {resp.status}: {resp.read(300).decode('utf-8', 'replace')}")
        while True:
            line = resp.readline()
            if not line:
                break
            line = line.strip()
            if not line.startswith(b"data:"):
                continue
            chunk = line[5:].strip()
            if chunk == b"[DONE]":
                break
            try:
                obj = json.loads(chunk)
            except ValueError:
                continue
            if obj.get("error"):
                raise ModelError(str(obj["error"]))
            usage = obj.get("usage") or usage
            for choice in obj.get("choices") or []:
                finish_reason = choice.get("finish_reason") or finish_reason
                delta = choice.get("delta") or {}
                thinking = delta.get("reasoning_content") or delta.get("reasoning")
                if thinking and not run.quiet:
                    run.emit("thinking", text=thinking)
                if delta.get("content"):
                    content.append(delta["content"])
                    if not run.quiet:
                        run.emit("token", text=delta["content"])
                for frag in delta.get("tool_calls") or []:
                    slot = calls.setdefault(frag.get("index", 0), {"id": "", "function": {"name": "", "arguments": ""}})
                    slot["id"] = frag.get("id") or slot["id"]
                    fn = frag.get("function") or {}
                    name = fn.get("name")
                    if name and name != slot["function"]["name"]:
                        slot["function"]["name"] += name
                    slot["function"]["arguments"] += fn.get("arguments") or ""
    except (OSError, http.client.HTTPException) as exc:
        if run.stop:
            raise Stopped()
        if content or calls:
            raise ModelError(f"stream interrupted: {exc}")
        raise NothingUsable(f"stream failed: {exc}")
    finally:
        conn.close()
        run.conn = None
    if run.stop:
        raise Stopped()
    if not content and not calls:
        raise NothingUsable("the stream contained no content")
    return {"content": "".join(content), "finish_reason": finish_reason, "tool_calls": parse_tool_calls([calls[k] for k in sorted(calls)]),
            "usage": usage}


def plain_completion(run, payload):
    """Non-streaming fallback: read choices[0].message."""
    conn, resp = open_chat(run, dict(payload, stream=False))
    try:
        data = resp.read()
        if resp.status != 200:
            raise ModelError(f"gateway returned {resp.status}: {data[:300].decode('utf-8', 'replace')}")
        obj = json.loads(data)
    except (OSError, http.client.HTTPException, ValueError) as exc:
        if run.stop:
            raise Stopped()
        raise ModelError(f"bad response from gateway: {exc}")
    finally:
        conn.close()
        run.conn = None
    if run.stop:
        raise Stopped()
    if obj.get("error"):
        raise ModelError(str(obj["error"]))
    try:
        msg = obj["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        raise ModelError("gateway response has no message")
    thinking = msg.get("reasoning_content") or msg.get("reasoning")
    if thinking and not run.quiet:
        run.emit("thinking", text=thinking)
    if msg.get("content") and not run.quiet:
        run.emit("token", text=msg["content"])
    calls = parse_tool_calls(msg.get("tool_calls") or [])
    if not msg.get("content") and not calls:
        raise ModelError("the model returned an empty reply")
    return {"content": msg.get("content") or "", "tool_calls": calls, "usage": obj.get("usage"),
            "finish_reason": (obj.get("choices") or [{}])[0].get("finish_reason")}


def engine_context_tokens():
    """How much the loaded engine can hold, from the Manager. None for gateways that do not say."""
    try:
        status = strata_status(timeout=2)
    except (OSError, ValueError, http.client.HTTPException):
        return None
    return (status.get("last_generation") or {}).get("context_tokens") or None


# ---------------------------------------------------------------------------
# Session cache: a chat's engine state, saved with Strata's session files
# ---------------------------------------------------------------------------

# ---- the session cache (2026-11-12): a reloaded Strata engine re-read a 120K-token chat for about two minutes. Strata
# saves the conversation it holds to a file and restores it in about a second (POST /slots/0?action=save|restore, on
# when the profile has "slot_save_path"). Studio saves the chat when a run ends and before a picture or voice tool may
# unload Strata, and restores it before the next request when the engine is new or holds another chat. A stale or
# mismatched file costs nothing: the engine reuses the prefix that matches and reads the rest.
CACHE_LOCK = threading.Lock()
CACHE_HOLDS = {"engine": None, "session": None}            # what the engine holds, as far as Studio knows
CACHE_SWAP_TOOLS = {"generate_image", "generate_images", "generate_video", "generate_music", "speak", "make_sound"}


def cache_index_path():
    return os.path.join(APP.data_dir, "session_cache.json")


def engine_identity():
    """{"loaded", "key" (changes with every engine start), "base"} of the Strata engine, or None without a Manager."""
    try:
        st = strata_status(timeout=5)
    except (OSError, ValueError, http.client.HTTPException):
        return None
    procs = st.get("server_processes") or []
    loaded = bool(st.get("running") and (st.get("health") or {}).get("loaded") is not False and procs)
    key = f"{procs[0].get('pid')}:{(st.get('job') or {}).get('started_at')}" if procs else None
    return {"loaded": loaded, "key": key, "base": st.get("base_url") or "http://127.0.0.1:18080"}


def engine_slot(base, action, name, timeout=120):
    """(HTTP status, body) of the engine's slot save or restore."""
    parts = urllib.parse.urlsplit(base)
    conn = http.client.HTTPConnection(parts.hostname or "127.0.0.1", parts.port or 80, timeout=timeout)
    try:
        conn.request("POST", f"/slots/0?action={action}", json.dumps({"filename": name}), {"Content-Type": "application/json"})
        resp = conn.getresponse()
        data = resp.read()
        try:
            return resp.status, json.loads(data or b"{}")
        except ValueError:
            return resp.status, {}
    finally:
        conn.close()


def cache_name(sid):
    return f"studio-{sid}.bin"


def cache_on(run):
    return bool(APP.cfg["session_cache"]) and is_strata_model(run.model)


def cache_prune(index):
    """Keeps the newest session_cache_keep chats' files; Studio deletes only its own studio-*.bin files."""
    folder = os.path.expanduser(APP.cfg["session_cache_dir"])
    newest = sorted(index, key=lambda sid: index[sid].get("saved_at", 0), reverse=True)
    for sid in newest[max(1, int(APP.cfg["session_cache_keep"])):]:
        index.pop(sid, None)
        try:
            os.remove(os.path.join(folder, cache_name(sid)))
        except OSError:
            pass


def cache_save(run, why=""):
    """Saves the chat the engine holds, if it is this run's, so a later reload can restore it."""
    if not cache_on(run):
        return
    sid = run.session["id"]
    with CACHE_LOCK:
        info = engine_identity()
        if not info or not info["loaded"] or CACHE_HOLDS != {"engine": info["key"], "session": sid}:
            return
        try:
            status, body = engine_slot(info["base"], "save", cache_name(sid))
        except (OSError, http.client.HTTPException):
            return
        if status != 200:
            return
        index = read_json(cache_index_path(), {})
        index[sid] = {"saved_at": time.time(), "tokens": body.get("n_saved"), "bytes": body.get("n_written"), "why": why}
        cache_prune(index)
        write_json_atomic(cache_index_path(), index)


def cache_restore(run):
    """Before a request: when the engine is new or holds another chat and this chat has a saved file, load the model
    (a one-token request) and restore the file, so the chat is not read again."""
    if not cache_on(run):
        return
    sid = run.session["id"]
    with CACHE_LOCK:
        info = engine_identity()
        if not info or (info["loaded"] and CACHE_HOLDS == {"engine": info["key"], "session": sid}):
            return
        entry = read_json(cache_index_path(), {}).get(sid)
        if not entry:
            return
        if not info["loaded"]:
            run.emit("status", text="Loading the model before restoring this chat…")
            done = threading.Event()
            threading.Thread(target=watch_model_load, args=(run, done), daemon=True).start()
            try:
                stoppable(run, lambda: gateway_json("POST", "/v1/chat/completions", {"model": run.model, "messages": [{"role": "user", "content": "ok"}],
                                                                                  "max_tokens": 1}, timeout=900))
            except (OSError, ValueError, http.client.HTTPException):
                return
            finally:
                done.set()
            info = engine_identity()
            if not info or not info["loaded"]:
                return
        started = time.time()
        try:
            status, body = stoppable(run, lambda: engine_slot(info["base"], "restore", cache_name(sid)))
        except (OSError, http.client.HTTPException):
            return
        if status == 200:
            CACHE_HOLDS.update(engine=info["key"], session=sid)
            run.emit("status", text=f"Restored this chat from the cache: {fmt_tokens(body.get('n_restored'))} tokens in {time.time() - started:.1f} s")
        elif status == 400:
            # Saved by another engine build or settings (an update changes the fingerprint): it will never restore.
            index = read_json(cache_index_path(), {})
            if index.pop(sid, None) is not None:
                write_json_atomic(cache_index_path(), index)


def stoppable(run, work):
    """Runs work() in a thread and returns its result, or raises Stopped as soon as the run is stopped (the work is left
    to finish on its own). Loading the model before a cache restore can take minutes, and Stop has to answer at once."""
    box = {}

    def go():
        try:
            box["value"] = work()
        except BaseException as exc:          # noqa: BLE001 - passed back to the caller
            box["error"] = exc
    worker = threading.Thread(target=go, daemon=True)
    worker.start()
    while worker.is_alive():
        if run.stop:
            raise Stopped()
        worker.join(0.25)
    if run.stop:
        raise Stopped()
    if "error" in box:
        raise box["error"]
    return box.get("value")


def cache_holds(run):
    """After a request the engine holds this chat."""
    if not cache_on(run):
        return
    info = engine_identity()
    if info and info["loaded"]:
        with CACHE_LOCK:
            CACHE_HOLDS.update(engine=info["key"], session=run.session["id"])


def fmt_tokens(n):
    return f"{n:,}" if isinstance(n, int) else "the"


ENGINE_SERVERS = {"voices", "sounds", "comfyui"}     # tool servers whose engines load a model into memory (voices, sounds, music)


def release_engines(run):
    """After a reply whose tool calls used the voice, sound or music engine, those engines unload before the model is asked
    again, so a Strata that has to load does not meet them in memory. Several calls in one reply keep the engine loaded
    (a batch); it is the next turn of the model that releases it. On 2026-11-18 Keystone's narration made the voice engine
    load beside Strata on every reading and the kernel killed Strata 13 times. Config tool_engines lists the engines."""
    if not getattr(run, "engines_used", False):
        return
    run.engines_used = False
    if is_codex(run.model):              # Codex thinks elsewhere: nothing here needs the memory back
        return
    urls = [u for u in (APP.cfg.get("tool_engines") or []) if isinstance(u, str) and u.startswith("http://127.0.0.1:")]
    if APP.cfg.get("voice_url") and APP.cfg["voice_url"] not in urls:
        urls.append(APP.cfg["voice_url"])
    loaded = []
    for url in urls:
        try:
            with urllib.request.urlopen(url.rstrip("/") + "/health", timeout=3) as resp:
                health = json.load(resp)
        except (OSError, ValueError):
            continue
        if health.get("loaded") and not health.get("busy"):
            loaded.append(url.rstrip("/"))
    if not loaded:
        return
    run.emit("status", text="Freeing memory from the tool engines before the model's turn…")
    for url in loaded:
        try:
            urllib.request.urlopen(urllib.request.Request(url + "/unload", data=b"{}", headers={"Content-Type": "application/json"},
                                                          method="POST"), timeout=10).read()
        except (OSError, ValueError, http.client.HTTPException):
            pass                         # an engine that exits to give memory back closes the connection
    deadline = time.time() + 20          # the engine's process ends and systemd starts an empty one
    while loaded and time.time() < deadline and not run.stop:
        still = []
        for url in loaded:
            try:
                with urllib.request.urlopen(url + "/health", timeout=2) as resp:
                    if json.load(resp).get("loaded"):
                        still.append(url)
            except (OSError, ValueError):
                pass
        loaded = still
        if loaded:
            time.sleep(0.5)


def call_model(run, messages):
    payload = {"model": run.model, "messages": wire_messages(run.session["id"], messages), "max_tokens": 8192}
    if run.session.get("effort") in EFFORTS:      # the chat's own choice; otherwise the Manager's profile decides
        payload["reasoning_effort"] = run.session["effort"]
    if run.tools_enabled:
        payload["tools"] = ([t for t in TOOL_SCHEMAS if tool_enabled(t["function"]["name"])] + [e["schema"] for e in run.mcp_tools.values()]
                            + [e["schema"] for e in run.user_tools.values()])
    if wants_script_ban(run, messages):
        bias = script_ban_bias()
        if bias:
            payload["logit_bias"] = bias
    if run.stop:
        raise Stopped()
    release_engines(run)
    strata_ensure(run)
    cache_restore(run)
    run.emit("status", text="Waiting for the model (it may be loading)…")
    loading_done = threading.Event()
    threading.Thread(target=watch_model_load, args=(run, loading_done), daemon=True).start()
    try:
        try:
            result = stream_completion(run, payload)
        except NothingUsable as exc:
            if run.stop:
                raise Stopped()
            if "logit_bias" not in payload or "logit_bias" not in str(exc):
                raise
            # An engine without per-request biases (the split engine, older builds) answers 400: ask again without.
            SCRIPT_BAN["refused"].add(run.model)
            print(f"script ban: {run.model} refused logit_bias, so it is sent without: {str(exc)[:200]}", flush=True)
            payload.pop("logit_bias")
            result = stream_completion(run, payload)
    except NothingUsable:
        if run.stop:
            raise Stopped()
        result = plain_completion(run, payload)
    finally:
        loading_done.set()
    cache_holds(run)
    usage = result.get("usage") or {}
    prompt = usage.get("prompt_tokens") or estimate_tokens(messages)
    completion = usage.get("completion_tokens") or estimate_tokens([result["content"], result["tool_calls"]])
    run.emit("usage", prompt_tokens=prompt, completion_tokens=completion, turn=run.turns,
             max_turns=APP.cfg["max_turns"], context_tokens=engine_context_tokens())
    return result


def clip_args(args):
    """Shorten long string arguments for display in the tool_call event."""
    return {k: (v[:2000] + "…" if isinstance(v, str) and len(v) > 2000 else v) for k, v in args.items()}


LEAKED_CALL_RE = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.S)
LEAKED_FUNCTION_RE = re.compile(r"<function=([\w-]+)>\s*(.*?)\s*</function>", re.S)
LEAKED_PARAM_RE = re.compile(r"<parameter=([\w-]+)>\n?(.*?)\n?</parameter>", re.S)


def recover_text_tool_calls(content):
    """Local models occasionally send tool calls as plain text, which would look like a final answer and end
    the run. Returns (calls, remaining text). Recovers only text that ends in well-formed calls, so a reply that
    merely discusses this format is left alone. An unknown tool name is kept, so the model is told and can retry;
    it does nothing else. Recovered calls still go through approvals."""
    if not isinstance(content, str) or not content.rstrip().endswith("</tool_call>"):
        return [], content
    calls = []
    for body in LEAKED_CALL_RE.findall(content):
        function = LEAKED_FUNCTION_RE.fullmatch(body)
        if function:
            name, args = function[1], dict(LEAKED_PARAM_RE.findall(function[2]))
        else:
            try:
                parsed = json.loads(body)
                name, args = parsed["name"], parsed.get("arguments", {})
            except (ValueError, KeyError, TypeError):
                return [], content
        if not isinstance(name, str) or not isinstance(args, dict):
            return [], content
        calls.append({"id": "text_" + secrets.token_hex(6), "type": "function", "function": {"name": name, "arguments": json.dumps(args)}})
    return calls, content[:content.index("<tool_call>")].rstrip() if calls else content


BARE_CALL_RE = re.compile(r'(?:```[\w-]*\s*)?\{\s*"name"\s*:')
BARE_SKIP_RE = re.compile(r"(?:\s|```[\w-]*)*")
UNQUOTED_NAME_RE = re.compile(r'(\{\s*"name"\s*:\s*)([A-Za-z_]\w*)(\s*,)')


def recover_bare_tool_calls(content, extra=()):
    """Some models, such as qwen2.5-coder, write calls as bare JSON with no tags, sometimes in a code fence and with
    the tool name unquoted. Returns (calls, remaining text). Recovers only when everything from the first such object
    to the end is calls to tools that exist, so an answer that merely shows some JSON is left alone. Recovered calls
    still go through approvals."""
    start = BARE_CALL_RE.search(content) if isinstance(content, str) else None
    if not start:
        return [], content
    decoder, rest, calls = json.JSONDecoder(strict=False), content[start.start():], []
    while True:
        rest = rest[BARE_SKIP_RE.match(rest).end():]
        if not rest:
            break
        fixed = UNQUOTED_NAME_RE.match(rest)
        if fixed:
            rest = f'{fixed[1]}"{fixed[2]}"{fixed[3]}' + rest[fixed.end():]
        try:
            parsed, end = decoder.raw_decode(rest)
            name, args = parsed["name"], parsed.get("arguments", {})
        except (ValueError, KeyError, TypeError, AttributeError):
            return [], content
        if not (name == "finish" or name in extra or (name in TOOL_FUNCS and tool_enabled(name))) or not isinstance(args, dict):
            return [], content
        calls.append({"id": "text_" + secrets.token_hex(6), "type": "function",
                      "function": {"name": name, "arguments": json.dumps({str(k).strip(): v for k, v in args.items()})}})
        rest = rest[end:]
    return calls, content[:start.start()].rstrip()


# ---------------------------------------------------------------------------
# Reply checks: mixed scripts, cut-off or garbled replies, announcements, the turn guard
# ---------------------------------------------------------------------------

GARBLED_CALL_RE = re.compile(r'<tool_call>\s*(?:<function=|\{\s*"name")')
LOOP_NOTES = ("You repeated", "Studio note:")
RETRY_NOTE = "Studio note: your last reply was cut off or held an incomplete tool call, so nothing ran. Send it again in full. If a file is too long for one reply, write it in smaller parts."
MAX_CUT_OFF_RETRIES = 2
SCRIPT_NOTE = ("Studio note: your last {what} mixed scripts or languages ({why}). Send it again, with Greek written "
               "only in the Greek alphabet: no Greek words in Latin letters (Greeklish), no Chinese or other characters. Keep code, commands and file names as they are.")
NOT_PROSE_RE = re.compile(r"```.*?```|`[^`\n]*`|https?://\S+|\S*[/\\]\S*|\S+\.[A-Za-z0-9]{1,5}\b|\b[A-Za-z_]+\([^)]*\)", re.S)
GREEK_RE, LATIN_RE = re.compile(r"[\u0370-\u03ff\u1f00-\u1fff]"), re.compile(r"[A-Za-z]")
# Common Greek words as they look in Latin letters; English hardly uses them, so they tell Greeklish from English.
GREEKLISH_WORDS = {"kai", "einai", "den", "gia", "apo", "tin", "tis", "tou", "tous", "ton", "twn", "stin", "stis", "sto", "sta", "stous",
                   "otan", "alla", "oti", "pou", "mia", "enas", "ena", "afto", "auto", "ola", "mono", "meta", "tha", "na", "mas", "sas",
                   "pros", "xoris", "xwris", "epeidi", "kathe", "poli", "ligo", "edw", "edo", "ekei", "ayto", "einai", "exei", "exoun",
                   "kanei", "prepei", "mporei", "boreite", "mporeite", "douleuei", "douleui", "lexi", "lexis", "selida", "arxeio"}
FOREIGN_RE = re.compile(r"[\u0400-\u04ff\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")


def mixed_words(text):
    """Words that mix Greek and Latin letters («_potάμι», «Σcaffolding», «αρχείa»), outside code, paths, file names and
    links. Words under four letters do not count, so symbols such as Δx or Δt pass."""
    found = []
    for word in re.findall(r"[^\W\d_]+", NOT_PROSE_RE.sub(" ", text or "")):
        if len(word) >= 4 and LATIN_RE.search(word) and GREEK_RE.search(word) and word not in found:
            found.append(word)
    return found


# Repairs of words mixing Greek and Latin letters, made only when the result is a word of the Greek lexicon
# (tools/greek_grammar.py's LEX, about 530,000 forms), so a repair never invents a word (2026-11-26, the user's request):
#   «αρχείa» -> «αρχεία»: Latin letters that look like Greek ones;
#   «_potάμι» -> «ποτάμι»: Greek written in Latin letters (Greeklish) inside the word;
#   «υπάρχουνnoch» -> «υπάρχουν»: a Greek word with Latin letters stuck to it; an English word stuck to it gets a space.
# Anything else («Σcaffolding», «γreek») is left for the send-back notes as before.
LOOKALIKES = str.maketrans("aeiknoptuvxyABEHIKMNOPTXYZ", "αεικηορτυνχυΑΒΕΗΙΚΜΝΟΡΤΧΥΖ")
GREEKLISH_PARTS = [("th", ["θ"]), ("ch", ["χ"]), ("ps", ["ψ"]), ("ks", ["ξ"]), ("ou", ["ου"]), ("mp", ["μπ"]), ("nt", ["ντ"]),
                   ("a", ["α"]), ("b", ["β", "μπ"]), ("c", ["κ"]), ("d", ["δ", "ντ"]), ("e", ["ε"]), ("f", ["φ"]), ("g", ["γ"]),
                   ("h", ["η", "χ"]), ("i", ["ι", "η", "υ"]), ("j", ["τζ"]), ("k", ["κ"]), ("l", ["λ"]), ("m", ["μ"]), ("n", ["ν"]),
                   ("o", ["ο", "ω"]), ("p", ["π"]), ("q", ["κ"]), ("r", ["ρ"]), ("s", ["σ"]), ("t", ["τ"]), ("u", ["υ", "ου"]),
                   ("v", ["β"]), ("w", ["ω"]), ("x", ["ξ", "χ"]), ("y", ["υ"]), ("z", ["ζ"])]
MAX_GREEKLISH = 256


def greek_word_known(word):
    try:
        lex = tools_import("greek_grammar").LEX
        return len(word) >= 2 and lex.ready() and bool(lex.look(word))
    except Exception:
        return False


def greeklish_spellings(latin):
    """Greek spellings of a run of Latin letters, at most MAX_GREEKLISH."""
    out = [""]
    rest = latin.lower()
    while rest:
        for part, options in GREEKLISH_PARTS:
            if rest.startswith(part):
                out = [o + g for o in out for g in options][:MAX_GREEKLISH]
                rest = rest[len(part):]
                break
        else:
            return []
    return out


ACCENTED = str.maketrans("αεηιουω", "άέήίόύώ")


def with_accents(word):
    """The word itself and, when it has no accent, the word with an accent on each vowel in turn (Greeklish has none)."""
    yield word
    if re.search(r"[άέήίόύώΆΈΉΊΌΎΏΐΰ]", word):
        return
    for i, ch in enumerate(word):
        if ch in "αεηιουω":
            yield word[:i] + ch.translate(ACCENTED) + word[i + 1:]


def final_sigma(word):
    return word[:-1] + "ς" if word.endswith("σ") else word


def repaired_word(word):
    """The Greek word a mixed word meant, or None when the lexicon cannot say."""
    tried = final_sigma(word.translate(LOOKALIKES))
    if not LATIN_RE.search(tried) and greek_word_known(tried):
        return tried
    segments = re.findall(r"[A-Za-z]+|[^A-Za-z]+", word)
    spellings = [""]
    for seg in segments:
        options = greeklish_spellings(seg) if LATIN_RE.match(seg) else [seg]
        spellings = [s + o for s in spellings for o in options][:MAX_GREEKLISH]
    for spelled in spellings:
        spelled = final_sigma(spelled)
        for accented in with_accents(spelled):
            if greek_word_known(accented):
                return accented[0].upper() + accented[1:] if word[0].isupper() else accented
    if len(segments) == 2:
        greek, latin = (segments[0], segments[1]) if GREEK_RE.match(segments[0]) else (segments[1], segments[0])
        english = english_word(latin)
        if english is not None and greek_word_known(greek):
            if english:                # «τρέχειserver», «ναAssert»: two words without the space
                return f"{greek} {latin}" if segments[0] == greek else f"{latin} {greek}"
            if len(greek) >= 3:        # «υπάρχουνnoch»: a stray word of another language («λαths» is left alone)
                return greek
    return None


TECH_WORDS = {"api", "json", "yaml", "toml", "html", "css", "http", "https", "url", "npm", "git", "cli", "csv", "sql", "ui", "ux", "js",
              "ts", "py", "app", "repo", "commit", "readme", "docker", "node", "python", "regex", "pdf", "svg", "png", "ok"}
ENGLISH_DICT = {"words": None}


def english_word(word):
    """True for an English word (the system's word list, Studio's own short list, technical words, or an acronym), False
    for another word, None when there is no word list to tell."""
    if word.lower() in ENGLISH_WORDS or word.lower() in TECH_WORDS or (len(word) > 1 and word.isupper()):
        return True
    if ENGLISH_DICT["words"] is None:
        words = set()
        for path in ("/usr/share/dict/words", "/usr/share/dict/american-english", "/usr/share/dict/british-english"):
            try:
                with open(path, encoding="utf-8", errors="ignore") as fh:
                    words |= {line.strip().lower() for line in fh}
            except OSError:
                pass
        ENGLISH_DICT["words"] = words
    if not ENGLISH_DICT["words"]:
        return None
    return word.lower() in ENGLISH_DICT["words"]


def repair_mixed(text):
    """The text with every mixed word the lexicon can repair repaired, outside code spans. Words it cannot repair stay."""
    if not isinstance(text, str) or not (APP is None or APP.cfg.get("script_check", True)):
        return text
    fixes = {w: r for w in mixed_words(text) for r in [repaired_word(w)] if r}
    if not fixes:
        return text
    pattern = re.compile(r"(?<![^\W\d_])(" + "|".join(re.escape(w) for w in sorted(fixes, key=len, reverse=True)) + r")(?![^\W\d_])")
    out, at = [], 0
    for m in CODE_SPAN_RE.finditer(text):
        out.append(pattern.sub(lambda x: fixes[x.group(1)], text[at:m.start()]))
        out.append(m.group(0))
        at = m.end()
    out.append(pattern.sub(lambda x: fixes[x.group(1)], text[at:]))
    return "".join(out)


def mixed_note(words, where):
    shown = ", ".join(f"«{w}»" for w in words[:6]) + (f" and {len(words) - 6} more" if len(words) > 6 else "")
    return f"Studio note: {where} {shown}, mixing Greek and Latin letters in one word. Write each word in one alphabet."


def mixed_scripts(text, greek_chat=True):
    """Why a Greek reply should be rewritten, or "" when it reads as Greek. Code, paths, file names and links do not count;
    English terms such as "server" inside Greek are normal, so Latin words only count when they are a large share."""
    prose = NOT_PROSE_RE.sub(" ", text or "")
    words = re.findall(r"[^\W\d_]+", prose)
    greek = sum(1 for w in words if GREEK_RE.search(w) and not LATIN_RE.search(w))
    latin = sum(1 for w in words if LATIN_RE.search(w) and not GREEK_RE.search(w))
    mixed = len(mixed_words(prose))
    greeklish = sum(1 for w in words if w.lower() in GREEKLISH_WORDS)
    foreign = len(FOREIGN_RE.findall(prose))
    reasons = []
    if mixed:                    # one is already wrong: «_potάμι» in a reply is a typo the reader sees
        reasons.append(f"{mixed} word{'s' if mixed > 1 else ''} mix{'' if mixed > 1 else 'es'} Greek and Latin letters ({', '.join(mixed_words(prose)[:4])})")
    if foreign:                  # one is already wrong: «ορθογραφίας需要» (Keystone, 2026-11-11)
        shown = "".join(dict.fromkeys(FOREIGN_RE.findall(prose)))[:8]
        reasons.append(f"{foreign} Chinese, Japanese, Korean or Cyrillic character{'s' if foreign > 1 else ''} ({shown})")
    if greeklish >= 4:
        reasons.append(f"{greeklish} Greek words written in Latin letters (Greeklish)")
    elif greek_chat and greek >= 10 and latin >= 25 and latin / (greek + latin) >= 0.35:      # Greek drifting into Latin letters
        reasons.append(f"{latin} of {greek + latin} words are in Latin letters")
    return "; ".join(reasons)


ASKED_LANGUAGE_RE = re.compile(r"\b(?:in|into)\s+(greek|english)\b|(?:στα|σε)\s+(ελληνικά|ελληνικα|αγγλικά|αγγλικα)", re.I)


# Messages that carry on the same task: Continue, the goal's "Carry on with the goal.", and steers sent during a run.
CARRY_ON_RE = re.compile(r"^\s*(continue|carry on|keep going|go on|resume|start now|go ahead|proceed|συνέχισε|συνεχίστε|συνέχεια|προχώρα|ξεκίνα)(?!\w)", re.I)


def asked_language(messages, goal=None):
    """A language the user asked for in the message this run answers, such as "in Greek" or «στα ελληνικά».
    An earlier task's request does not carry over, but a steer or a "Continue…" is the same task, so without a request
    of its own it looks further back (the Island prompt's "summary in Greek", steered in English, 2026-11-11)."""
    for message in reversed(messages):
        content = message.get("content")
        if message.get("role") != "user" or not isinstance(content, str) or content.startswith(LOOP_NOTES):
            continue
        typed = typed_part(content)
        found = ASKED_LANGUAGE_RE.findall(typed)
        if not found and (content.startswith(GUIDE_NOTE[:40]) or CARRY_ON_RE.match(typed) or (goal and typed.strip().startswith(goal.strip()[:60]))):
            continue
        if not found:
            return None
        word = (found[-1][0] or found[-1][1]).lower()
        return "Greek" if word.startswith(("greek", "ελλ")) else "English"
    return None


def script_problem(run, text):
    """Why a final reply or summary should be written again, once per run: Greek mixed with other scripts (in any chat),
    or a reply mostly in Greek when the user writes English and did not ask for Greek."""
    if not APP.cfg["script_check"] or run.script_noted:
        return ""
    why = script_reasons(run, text)
    if why:
        run.script_noted = True
    return why


MAX_SCRIPT_ASIDES = 3
SCRIPT_ASIDE = ("Studio note: the message you wrote with those tool calls mixed scripts or languages ({why}). Do not write it again; "
                "from now on write Greek only in the Greek alphabet, with no Chinese or other characters and no Latin letters inside Greek words.")


def script_aside(run, text):
    """A note for text that came with tool calls («ορθογραφίας需要», «αόριστος形态», «εφeureka» between calls): the calls run,
    and the note follows their results so the model writes clean Greek from then on, at most MAX_SCRIPT_ASIDES a run."""
    if not APP.cfg["script_check"] or run.script_asides >= MAX_SCRIPT_ASIDES or not (text or "").strip():
        return ""
    why = script_reasons(run, text)
    if not why:
        return ""
    run.script_asides += 1
    return SCRIPT_ASIDE.format(why=why)


def script_reasons(run, text):
    language = asked_language(run.session["messages"], (run.session.get("goal") or {}).get("text")) or user_language(run.session["messages"])
    prose = re.findall(r"[^\W\d_]+", NOT_PROSE_RE.sub(" ", text or ""))
    greek = sum(1 for w in prose if GREEK_RE.search(w) and not LATIN_RE.search(w))
    if language != "Greek" and greek < 10:
        return ""
    # In an English chat Latin words are the language itself: a reply quoting Greek examples is not drifting.
    reasons = [mixed_scripts(text, greek_chat=language != "English")] if (language == "Greek" or greek >= 10) else []
    if language == "English" and greek >= 15 and greek > len(prose) / 2:
        reasons.append("it is in Greek, but the user writes English")
    why = "; ".join(r for r in reasons if r)
    if why:
        why += f". Write it in {language or 'the language the user asked for'}"
    return why


CODE_SPAN_RE = re.compile(r"```.*?```|`[^`\n]*`", re.S)

# Scripts Strata models leak into Greek and English text: banned at the source with logit_bias, so they cannot be written
# at all (2026-11-26, after the guards benchmark still found Chinese in a Greek reply). A token is banned when its bytes hold
# a UTF-8 lead byte of Cyrillic (D0-D3) or of CJK, kana, Yi and Hangul (E3-ED): those bytes start nothing else, so no
# sequence of tokens can spell such a character. Fullwidth forms, CJK compatibility and the 4-byte CJK planes are banned
# where a token holds the whole character. Greek (CE, CF, E1 for polytonic), Latin, punctuation and emoji stay.
SCRIPT_BAN_LEADS = frozenset(range(0xD0, 0xD4)) | frozenset(range(0xE3, 0xEE))
SCRIPT_BAN_CHARS = re.compile(r"[\uf900-\ufaff\uff00-\uffef\U00020000-\U0003ffff]")
SCRIPT_BAN = {"key": None, "bias": None, "found": 0.0, "files": [], "refused": set()}
# The user asks for one of those languages, or writes about them: no ban for that chat.
SCRIPT_LANGUAGE_RE = re.compile(r"chinese|mandarin|cantonese|japanese|korean|russian|ukrainian|bulgarian|serbian|belarus|macedonian|kazakh|"
                                r"mongolian|cyrillic|kanji|hanzi|hiragana|katakana|hangul|κινε[ζσ]|κινέζ|ιαπων|γιαπων|κορε[αά]τικ|ρωσ[ιί]κ|ρώσικ|"
                                r"ουκραν|βουλγ[αά]ρ|σερβικ|σέρβικ|κυριλλ", re.I)


def gpt2_byte_decoder():
    """GPT-2 byte-level BPE's printable character -> byte, the inverse of its byte encoder."""
    bs = list(range(0x21, 0x7F)) + list(range(0xA1, 0xAD)) + list(range(0xAE, 0x100))
    cs, n = bs[:], 0
    for b in range(256):
        if b not in bs:
            bs.append(b)
            cs.append(256 + n)
            n += 1
    return {chr(c): b for b, c in zip(bs, cs)}


def vocab_script_bans(path):
    """The ids of a byte-level BPE vocab.json ({token: id}) that would write a banned script, sorted."""
    decoder, out = gpt2_byte_decoder(), []
    for token, tid in read_json(path, {}).items():
        try:
            raw = bytes(decoder[c] for c in token)
        except KeyError:
            continue                                # not a byte-level token (a special one such as <|im_end|>)
        if any(b in SCRIPT_BAN_LEADS for b in raw) or SCRIPT_BAN_CHARS.search(raw.decode("utf-8", "ignore")):
            out.append(int(tid))
    return sorted(out)


def strata_vocab_files():
    """The vocab.json of every Strata profile this machine runs (config strata_vocab first), looked up every 10 minutes."""
    now = time.time()
    if now - SCRIPT_BAN["found"] < 600:
        return SCRIPT_BAN["files"]
    dirs = []
    if APP.cfg.get("strata_vocab"):
        dirs.append(os.path.dirname(os.path.expanduser(APP.cfg["strata_vocab"])))
    elif strata_dir():
        dirs += [p["config"].get("tokenizer") for p in strata_profiles().values()]
    else:
        try:
            dirs += [(m.get("config") or {}).get("tokenizer") for m in (strata_status() or {}).get("models") or [] if isinstance(m, dict)]
        except Exception:
            return SCRIPT_BAN["files"]              # the Manager does not answer now: keep what was found before
    files = sorted({os.path.join(d, "vocab.json") for d in dirs if isinstance(d, str) and d and os.path.isfile(os.path.join(d, "vocab.json"))})
    SCRIPT_BAN.update(found=now, files=files)
    return files


def script_ban_bias():
    """{token id: -100} for the Strata models' vocabulary, or None when there is none, or when the profiles use different
    vocabularies (Studio cannot tell which one a model name loads). Built once per vocabulary file (about a second)."""
    files = strata_vocab_files()
    if not files:
        return None
    try:
        digests = {hashlib.sha256(open(f, "rb").read()).hexdigest() for f in files}
    except OSError:
        return None
    if len(digests) != 1:
        return None
    key = digests.pop()
    if SCRIPT_BAN["key"] != key:
        ids = vocab_script_bans(files[0])
        SCRIPT_BAN.update(key=key, bias={str(i): -100 for i in ids} if ids else None)
        print(f"script ban: {len(ids)} tokens of Chinese, Japanese, Korean and Cyrillic text from {files[0]}", flush=True)
    return SCRIPT_BAN["bias"]


def message_texts(message):
    content = message.get("content")
    if isinstance(content, str):
        yield content
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                yield part["text"]
    for call in message.get("tool_calls") or []:
        yield str((call.get("function") or {}).get("arguments") or "")


def wants_script_ban(run, messages):
    """Ban those scripts for this request, unless the conversation already holds them (a file the model may have to quote
    exactly to edit it) or the user names one of those languages."""
    if not APP.cfg.get("script_ban", True) or not is_strata_model(run.model) or run.model in SCRIPT_BAN["refused"]:
        return False
    for message in messages:
        for text in message_texts(message):
            if FOREIGN_RE.search(text) or SCRIPT_BAN_CHARS.search(text):
                return False
            if message.get("role") == "user" and SCRIPT_LANGUAGE_RE.search(typed_part(text)):
                return False
    return True



def drop_foreign(run, text):
    """Text that came with tool calls, without Chinese, Japanese, Korean or Cyrillic characters, in a Greek or English
    chat («Ο用户使用: θα ξανακάνω…», Keystone, 2026-11-15). Code spans keep theirs. The model is told by script_aside
    (at most three times a run); this keeps the chat readable every time."""
    if not APP.cfg["script_check"] or not isinstance(text, str) or not FOREIGN_RE.search(text):
        return text
    language = asked_language(run.session["messages"], (run.session.get("goal") or {}).get("text")) or user_language(run.session["messages"])
    if language not in ("Greek", "English") and len(GREEK_RE.findall(text)) < 20:
        return text
    out, at = [], 0
    for m in CODE_SPAN_RE.finditer(text):
        out.append(FOREIGN_RE.sub("", text[at:m.start()]))
        out.append(m.group(0))
        at = m.end()
    out.append(FOREIGN_RE.sub("", text[at:]))
    clean = "".join(out)
    clean = re.sub(r"[ \t]{2,}", " ", clean)
    clean = re.sub(r" ([,.;:!?·])", r"\1", clean)
    return clean


def has_garbled_call(content):
    """True when a reply ends in a tool call that could not be read. A reply that goes on after the call is left alone."""
    start = content.rfind("<tool_call>") if isinstance(content, str) else -1
    if start < 0 or not GARBLED_CALL_RE.match(content, start):
        return False
    closed = content.find("</tool_call>", start)
    return closed < 0 or content.rstrip().endswith("</tool_call>")


def was_cut_off(result):
    """True when a reply ended early, rather than being a real final answer."""
    content = result.get("content") or ""
    unfinished_call = content.rfind("<tool_call>") > content.rfind("</tool_call>") and "<function=" in content[content.rfind("<tool_call>"):]
    return result.get("finish_reason") == "length" or unfinished_call


GUIDE_NOTE = ("Message from the user while you were working. Take it into account and carry on with your tools; "
              "it does not end the task:")
STEER_NOTE = ("Studio note: you answered the user's message without a tool call, so nothing was done and the run would end here. "
              "If there is work left, do it now with your tools; when everything is done, call finish with a summary.")


ANNOUNCE_NOTE = ("Studio note: your reply says what you will do next, but it has no tool call, so nothing was done and the run "
                 "would end here. Do it now with your tools. If everything is already done, call finish with a summary; "
                 "if you need the user's answer first, ask your question.")
MAX_ANNOUNCE_NUDGES = 3
ANNOUNCE_WORDS = re.compile(r"(?:^|[.!:\n]\s*)(?:\*\*)?(?:I'll|I will|I'm going to|I am going to|I need to|Let me|Let's|Next,? I|Now I|"
                            r"Θα |Τώρα θα|Ας |Πάμε )", re.I)
INTENT_WORDS = re.compile(r"\b(?:I'll|I will|I'm going to|I am going to)\b", re.I)      # anywhere in a sentence
OFFER_WORDS = re.compile(r"\bif you\b|\bwould you\b|\bwant me\b|\bshall I\b|αν θέλετε|αν θέλεις", re.I)
DASH_GERUND = re.compile(r"[—–]\s*([a-z]+ing)\b")
GERUND = re.compile(r"(?:\*\*)?([A-Z][a-z]+ing)\b")
NOT_GERUNDS = {"Nothing", "Everything", "Something", "Anything", "During", "String", "Thing", "Morning", "Evening", "King", "Spring",
               "Ceiling", "Building", "Ring", "Setting", "Heading", "Padding", "Spacing", "Rendering", "Lighting", "Painting", "Bring"}


ANSWER_NOTE = ("Studio note: you only read files, and your finish summary only says what you did (\"{summary}\"), so the user "
               "never sees the answer itself. Call finish again with the full answer in the summary: everything the user asked "
               "for, as you would write it to them. If that one line is the whole answer, send it again as it is.")
READING_TOOLS = {"list_files", "read_file", "search", "finish", "plan", "use_skill"}
ANSWER_SHORT = 300          # a finish summary up to this long, with no reply text this long in the run, may have left the answer out


def leaves_out_answer(run, summary):
    """True for a finish after a run that only read the folder, whose summary is a line or two and was the only thing written:
    a model in a benchmark read a project well, then finished with "Catalogued all HTTP routes…" and never listed them."""
    s = (summary or "").strip()
    return (APP.cfg["answer_check"] and run.worked and run.only_read and not run.changed and not run.answer_noted and not run.plan_mode
            and len(s) <= ANSWER_SHORT and run.longest_text <= ANSWER_SHORT and not s.endswith("?"))


def announces_work(text):
    """True for a short reply that only says what comes next ("Adding the back covers now."), which ends a run unfinished.
    A question to the user, a long summary or a report of what was done does not count."""
    t = (text or "").strip()
    if not t or len(t) > 800 or t.rstrip("*_ )").endswith("?"):
        return False
    if t.endswith(":") or t.endswith("…") and not t.endswith("……"):
        return True
    sentences = [s for s in re.split(r"(?<=[.!])\s+|\n+", t) if s.strip()]
    for s in (sentences[0], sentences[-1]):
        m = GERUND.match(s.strip())
        if m and m.group(1) not in NOT_GERUNDS:
            return True
    last = sentences[-1]
    m = DASH_GERUND.search(last)
    if m and m.group(1).capitalize() not in NOT_GERUNDS:
        return True
    if INTENT_WORDS.search(last) and not OFFER_WORDS.search(last):
        return True
    return bool(ANNOUNCE_WORDS.search(t[-400:])) and not OFFER_WORDS.search(last)


# The turn guard: a run going in circles gets one note, and stops if it carries on, with a plain sentence saying why.
FAIL_NOTE_AT, FAIL_STOP_AT = 4, 8           # tool calls in a row that failed
PLAN_NOTE_AT, PLAN_STOP_AT = 4, 7           # plan calls in one run
REPEAT_STOP_AT = 3                          # the same call straight after itself
STUCK_NOTE = ("Studio note: {what}. Stop and think before the next call: read the errors, try a different approach, or, "
              "if you are blocked, say so to the user and ask.")
NOT_THE_MODELS_FAULT = ("Command skipped", "Tool call skipped", "Skipped", "Not answered")


def turn_guard(run, results, history):
    """After a turn's tool results: (reason the run should stop, or None). Adds a note to the conversation at the first sign."""
    if not APP.cfg.get("turn_guard", True):
        return None
    for ok, summary, name in results:
        if name == "plan":
            run.plan_calls += 1
        if summary in NOT_THE_MODELS_FAULT:        # the user said no, or the run was ending: not the model going wrong
            continue
        run.fail_streak = 0 if ok else run.fail_streak + 1
    if run.fail_streak >= FAIL_STOP_AT:
        return f"{run.fail_streak} tool calls in a row failed"
    if run.plan_calls >= PLAN_STOP_AT:
        return f"the plan was rewritten {run.plan_calls} times"
    if run.repeats >= REPEAT_STOP_AT:
        return f"the same call was made {run.repeats + 1} times in a row"
    if run.fail_streak >= FAIL_NOTE_AT and not run.guard_noted.get("fail"):
        run.guard_noted["fail"] = True
        history.append({"role": "user", "content": STUCK_NOTE.format(what=f"your last {run.fail_streak} tool calls all failed")})
    elif run.plan_calls >= PLAN_NOTE_AT and not run.guard_noted.get("plan"):
        run.guard_noted["plan"] = True
        history.append({"role": "user", "content": STUCK_NOTE.format(what=f"you have written the plan {run.plan_calls} times in this task; keep to it and do the work")})
    return None


def take_guidance(run, history):
    """Moves the user's guiding messages into the conversation. Returns how many there were."""
    with run.cond:
        messages, run.inbox = run.inbox, []
    for text in messages:
        history.append({"role": "user", "content": GUIDE_NOTE + "\n" + USER_EXTRAS_END + text})
    if messages:
        run.steered = True                # its next reply should act on it, not just say what it will do
    return len(messages)


def agent_loop(run):
    """Drive the model/tool conversation. Returns (reason, summary)."""
    history = run.session["messages"]
    max_turns = APP.cfg["max_turns"]
    errors, retries, last_call, summary = 0, 0, None, None
    while run.turns < max_turns:
        if run.stop:
            return "stopped", None
        if take_guidance(run, history):
            run.emit("status", text="Your message reached the model.")
        run.turns += 1
        if APP.cfg["stable_prompt"] and run.turns == APP.cfg["rule_reminder_turns"] and run.frozen_prompt is not None:
            # A long run: the reminder goes into the conversation, since changing the prompt would mean reading it all again.
            reminder = earlier_instructions(run)
            if reminder and reminder != (run.session.get("prompt_sections") or {}).get("reminder"):
                history.append({"role": "user", "content": "Studio note: " + reminder})
        trim_context(history, APP.cfg["smart_trim"], APP.cfg["trim_chars"])
        try:
            result = call_model(run, [{"role": "system", "content": build_system_prompt(run)}] + history)
        except Stopped:
            return "stopped", None
        except ModelError as exc:
            errors += 1
            run.emit("error", text=str(exc))
            if errors >= 3:
                return "error", None
            time.sleep(1)
            continue
        errors = 0
        calls = result["tool_calls"]
        if calls:
            # A reply can hold real calls and also more written out as text (Keystone, 2026-11-15: list_files for real
            # and list_voices as <tool_call> text, twice). The text ones run too, unless they repeat a real one.
            more, rest = recover_text_tool_calls(result["content"])
            seen = {(c["function"]["name"], c["function"]["arguments"]) for c in calls}
            more = [c for c in more if (c["function"]["name"], c["function"]["arguments"]) not in seen]
            if rest != result["content"]:
                calls, result["content"] = calls + more, rest
                run.emit("text_fixed", text=rest)
        else:
            calls, result["content"] = recover_text_tool_calls(result["content"])
        if not calls and APP.cfg["text_tool_calls"]:
            calls, result["content"] = recover_bare_tool_calls(result["content"], {**run.mcp_tools, **run.user_tools})
        if calls and not result["tool_calls"]:        # the chat showed the calls as text while they streamed
            run.emit("text_fixed", text=result["content"])
        if not calls and (was_cut_off(result) or (APP.cfg["retry_cut_off"] and has_garbled_call(result["content"]))):
            if APP.cfg["retry_cut_off"] and retries < MAX_CUT_OFF_RETRIES:
                retries += 1
                run.emit("status", text="The model's reply was incomplete. Asking it to send it again…")
                history.append({"role": "user", "content": RETRY_NOTE})
                save_session(run.session)
                continue
            history.append({"role": "assistant", "content": result["content"]})
            save_session(run.session)
            run.emit("error", text="The model's reply was cut off before it finished, so this run stopped. The chat and files are kept, "
                                   "and you can send a message to continue.")
            return "cut_off", None
        retries = 0
        history.append({"role": "assistant", "content": result["content"], **({"tool_calls": calls} if calls else {})})
        run.longest_text = max(run.longest_text, len((result["content"] or "").strip()))
        if not calls:
            fixed = repair_mixed(result["content"])
            if fixed != result["content"]:      # words the lexicon can repair are repaired; only the rest is sent back
                result["content"] = history[-1]["content"] = fixed
                run.emit("text_fixed", text=fixed)
            why = script_problem(run, result["content"])
            if why:
                run.emit("status", text="The reply mixed scripts or languages. Asking the model to write it again…")
                run.emit("sent_back", why=why.split(". Write it in")[0][:300])       # the page marks the reply, so it does not look accepted
                history.append({"role": "user", "content": SCRIPT_NOTE.format(what="reply", why=why)})
                save_session(run.session)
                continue
            if run.inbox:             # the user wrote while this answer was coming: answer that too
                continue
            if run.steered and run.tools_enabled:
                # Steered mid-task, models often reply "Adding X now." without a call, which would end the run unfinished.
                run.steered = False
                run.nudged_last = True
                run.emit("status", text="The model replied without acting on your message. Asking it to carry on…")
                history.append({"role": "user", "content": STEER_NOTE})
                save_session(run.session)
                continue
            if (APP.cfg["announce_check"] and run.tools_enabled and run.worked and not run.nudged_last
                    and run.announce_nudges < MAX_ANNOUNCE_NUDGES and announces_work(result["content"])):
                run.announce_nudges += 1
                run.nudged_last = True
                run.emit("status", text="The model said what it would do next but stopped. Asking it to carry on…")
                history.append({"role": "user", "content": ANNOUNCE_NOTE})
                save_session(run.session)
                continue
            if not (result["content"] or "").strip():
                run.silent = True
            clean = drop_foreign(run, result["content"])
            if clean != result["content"]:      # sent back once already this run: the stray characters still leave the chat
                result["content"] = history[-1]["content"] = clean
                run.emit("text_fixed", text=clean)
            save_session(run.session)
            return "answered", None
        clean = repair_mixed(result["content"])
        aside = script_aside(run, clean)
        clean = drop_foreign(run, clean)
        if clean != result["content"]:      # the calls run anyway; the chat shows the words without the stray characters
            result["content"] = history[-1]["content"] = clean
            run.emit("text_fixed", text=clean)
        finished, repeated, outcomes = False, False, []
        for tc in calls:
            # The history keeps the full name too, so the model sees the right one for next time.
            name = tc["function"]["name"] = full_tool_name(run, tc["function"]["name"])
            raw = tc["function"]["arguments"]
            try:
                args = json.loads(raw) if raw.strip() else {}
                if not isinstance(args, dict):
                    raise ValueError("arguments must be a JSON object")
                parse_error = None
            except ValueError as exc:
                args, parse_error = {}, str(exc)
            tc["function"]["arguments"] = json.dumps(args)
            run.emit("tool_call", id=tc["id"], name=name, args=clip_args(args))
            run.only_read = run.only_read and name in READING_TOOLS
            run.current_tool = tc["id"]          # a tool's progress (MCP notifications/progress) goes to this row
            if run.stop or finished:
                ok, summ, out = False, "Skipped", "Skipped: the run ended before this call."
            elif parse_error:
                ok, summ, out = False, "Invalid arguments", f"Error: invalid JSON arguments ({parse_error})."
            else:
                ok, summ, out = execute_tool(run, name, args)
                key = (name, json.dumps(args, sort_keys=True))
                repeated = repeated or key == last_call
                last_call = key
                if name == "finish":
                    out = repair_mixed(out)
                    if run.script_noted:       # its one send-back is used: the stray characters still leave the summary
                        out = drop_foreign(run, out)
                    finished, summary = True, out
            outcomes.append((ok, summ, name))
            run.emit("tool_result", id=tc["id"], name=name, ok=ok, summary=summ, output=out[-12000:] if name == "run" else out[:4000])
            history.append({"role": "tool", "tool_call_id": tc["id"], "content": out})
        if run.pending_pictures:      # after every tool result, so the model's calls and their answers stay together
            tools = ", ".join(dict.fromkeys(name for name, _ in run.pending_pictures))
            history.append({"role": "user", "content": f"Studio note: the pictures returned by {tools} are attached.",
                            "images": [pid for _, ids in run.pending_pictures for pid in ids]})
            run.pending_pictures = []
        if aside:
            last = history[-1]
            if last.get("role") == "user" and isinstance(last.get("content"), str) and last["content"].startswith("Studio note:"):
                last["content"] += "\n\n" + aside          # one user message after the results, beside the pictures' note
            else:
                history.append({"role": "user", "content": aside})
        run.repeats = run.repeats + 1 if repeated else 0
        if repeated and not finished:
            history.append({"role": "user", "content": "You repeated the same call; change approach."})
        stuck = None if finished else turn_guard(run, outcomes, history)
        if stuck:
            run.stuck = stuck
            save_session(run.session)
            return "stuck", None
        if finished:
            why = script_problem(run, summary)
            if why:
                run.emit("status", text="The summary mixed scripts or languages. Asking the model to write it again…")
                history.append({"role": "user", "content": SCRIPT_NOTE.format(what="finish summary", why=why) + " Call finish again with the rewritten summary."})
                finished = False
            elif leaves_out_answer(run, summary):
                run.answer_noted = True
                run.emit("status", text="The model finished without writing its answer. Asking it for the answer…")
                history.append({"role": "user", "content": ANSWER_NOTE.format(summary=" ".join(summary.split())[:200])})
                finished = False

        if calls:
            run.steered = run.nudged_last = False
            run.worked = True
        if finished and run.inbox:
            finished = False
        save_session(run.session)
        if finished:
            return "finished", summary
    return "max_turns", None


# ---------------------------------------------------------------------------
# /compact and goals
# ---------------------------------------------------------------------------

COMPACT_REQUEST = ("Studio note: the conversation is getting too long for your memory, so the earlier part will be replaced by a "
                   "summary you write now. Write it for yourself, to carry on the work from it alone. Include: what the user asked for "
                   "and any rules they gave, in their words; decisions made and why; files created or changed and what is in them now; "
                   "what was tried and failed; the current state; and what is left to do. Keep file paths, names, commands and numbers "
                   "exact. Write in the language the user writes in. Do not call any tools.")
COMPACT_INTRO = "Studio note: the earlier part of this chat was replaced by this summary, written by you, to save room:\n\n"
MIN_COMPACT_MESSAGES = 4


def last_prompt_tokens(session):
    """The prompt size of the chat's latest request, as the engine counted it, or None."""
    return next((e.get("prompt_tokens") for e in reversed(session.get("events") or []) if e.get("type") == "usage"), None)


def compact_main(run):
    """Replaces what the model reads of this chat with a summary it writes. The chat the user sees stays as it was."""
    reason = "error"
    try:
        history = run.session["messages"]
        if len(history) < MIN_COMPACT_MESSAGES:
            run.emit("error", text="This chat is still short, so there is nothing to summarize yet.")
            reason = "answered"
            return
        before = last_prompt_tokens(run.session) or estimate_tokens(history)
        run.emit("status", text="Summarizing the earlier messages…")
        trim_context(history, APP.cfg["smart_trim"], APP.cfg["trim_chars"])
        result = call_model(run, [{"role": "system", "content": build_system_prompt(run)}] + history + [{"role": "user", "content": COMPACT_REQUEST}])
        summary = (result.get("content") or "").strip()
        if len(summary) < 40:
            run.emit("error", text="The model did not write a usable summary, so the chat was left as it was.")
            return
        run.session["messages"] = [{"role": "user", "content": COMPACT_INTRO + summary},
                                   {"role": "assistant", "content": "Understood. I will carry on from this summary."}]
        after = estimate_tokens(run.session["messages"]) + estimate_tokens(run.frozen_prompt or "")
        run.emit("compacted", before=before, after=after, summary=summary)
        reason = "compacted"
    except Stopped:
        reason = "stopped"
    except ModelError as exc:
        run.emit("error", text=f"Could not summarize: {exc}")
    except Exception as exc:
        traceback.print_exc()
        run.emit("error", text=f"Internal error: {exc}")
    finally:
        run.close(reason)


# /goal: a goal kept with the chat. When a run ends with the goal still open, it is sent back to it, round after round,
# until the model says it is reached (GOAL COMPLETE) or blocked (BLOCKED: why), the user pauses it, or the rounds run out.
GOAL_DONE_RE = re.compile(r"^\W*goal complete\b", re.I)
GOAL_BLOCKED_RE = re.compile(r"^\W*blocked\b\W*(.*)", re.I | re.S)
MAX_GOAL_CHARS = 16000          # the page's GOAL_MAX; a long goal is repeated in short each round (goal_reminder)
GOAL_STATES = ("active", "paused", "done", "blocked")


def goal_end_words(codex=False):
    """How the model says the goal is reached: Codex has no finish tool, so its final reply starts with the words instead."""
    return ("give a final reply that starts with GOAL COMPLETE and sums up the work" if codex
            else "call finish with a summary that starts with GOAL COMPLETE")


def goal_note(text, codex=False):
    return (f"Goal for this chat: {text}\nWork until the goal is fully reached, checking your work as you go. When it is, "
            f"{goal_end_words(codex)}. If you cannot go on without the user, reply with BLOCKED: and what you "
            "need. Otherwise keep working: after each answer you will be sent back to the goal.\n")


GOAL_REPEAT_CHARS = 2500


def goal_reminder(goal):
    """The goal as repeated each round. A long one (a whole brief) is not sent again in full every round: its opening and
    what done means are, and the model is pointed at the message that set it."""
    text = goal.get("text") or ""
    if len(text) <= GOAL_REPEAT_CHARS:
        return f"Goal: {text}"
    parts = goal.get("parts") or {}
    head = (parts.get("goal") or text)[:600].rstrip()
    out = f"Goal (in full in the message that set it; read it again there for every step): {head}…"
    if parts.get("done"):
        out += f"\nDone when:\n{parts['done']}"
    return out


def goal_public(goal):
    return ({k: goal.get(k) for k in ("text", "state", "rounds", "why", "parts", "max_rounds", "codex_tokens", "codex_pct", "codex_used")}
            if isinstance(goal, dict) else None)


def goal_extras(req):
    """The Goal window's parts (the goal, when it is done, what to keep in mind) and its round limit, checked."""
    parts, limit = req.field("goal_parts"), req.field("goal_rounds")
    out = {}
    if isinstance(parts, dict):
        clean = {k: parts[k].strip()[:MAX_GOAL_CHARS] for k in ("goal", "done", "limits") if isinstance(parts.get(k), str) and parts[k].strip()}
        if clean:
            out["parts"] = clean
    if limit is not None:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ApiError(400, "the goal's round limit is a whole number from 1 to 100")
        out["max_rounds"] = limit
    # A Codex allowance (2026-11-18, the user's request): new tokens (output plus input not served from Codex's cache, in
    # thousands) and a ceiling on the plan's 5-hour usage. The goal pauses at the end of the round that passes either.
    tokens, pct = req.field("goal_codex_tokens"), req.field("goal_codex_pct")
    for value, name, low, high in ((tokens, "token allowance (thousands)", 1, 100000), (pct, "5-hour usage ceiling (%)", 1, 100)):
        if value is not None and (not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high):
            raise ApiError(400, f"the goal's Codex {name} is a whole number from {low} to {high}")
    if tokens is not None:
        out["codex_tokens"] = tokens * 1000
    if pct is not None:
        out["codex_pct"] = pct
    return out


def goal_codex_spent(goal):
    """Why a Codex goal must pause for its allowance, or "". Nothing set means no limit but the rounds."""
    if goal.get("codex_tokens") and goal.get("codex_used", 0) >= goal["codex_tokens"]:
        return f"It used its Codex allowance: {goal['codex_used'] // 1000:,}K of {goal['codex_tokens'] // 1000:,}K new tokens."
    window = ((CODEX_USAGE.get("data") or {}).get("windows") or [None])[0]
    if goal.get("codex_pct") and isinstance(window, dict) and isinstance(window.get("used"), (int, float)) and window["used"] >= goal["codex_pct"]:
        return f"Codex's 5-hour usage reached {window['used']:g}% (the goal stops at {goal['codex_pct']}%)."
    return ""


def goal_next(run, reason, summary):
    """After a run's answer: True when it goes on with its goal, with a note added. Updates the goal's state."""
    goal = run.session.get("goal")
    if not isinstance(goal, dict) or goal.get("state") != "active" or run.stop or reason not in ("answered", "finished"):
        return False
    last = summary if reason == "finished" else next((m.get("content") for m in reversed(run.session["messages"])
                                                      if m.get("role") == "assistant" and isinstance(m.get("content"), str)), "")
    blocked = GOAL_BLOCKED_RE.match(last or "")
    codex = is_codex(run.model)
    if GOAL_DONE_RE.match(last or "") and (reason == "finished" or codex):
        goal.update(state="done", why="")
    elif blocked:
        goal.update(state="blocked", why=blocked.group(1).strip()[:500] or "The agent needs you.")
    elif goal.get("rounds", 0) >= (goal.get("max_rounds") or APP.cfg.get("goal_max_rounds", 20)):
        goal.update(state="paused", why=f"It worked for {goal['rounds']} rounds without reaching the goal.")
    elif codex and goal_codex_spent(goal):
        goal.update(state="paused", why=goal_codex_spent(goal))
    else:
        goal["rounds"] = goal.get("rounds", 0) + 1
        goal["why"] = ""
        run.session["messages"].append({"role": "user", "content": f"Studio note: the goal is not reached yet (round {goal['rounds']}). "
                                        f"{goal_reminder(goal)}\nCarry on. When it is fully reached, {goal_end_words(codex)}; "
                                        "if you need the user, reply with BLOCKED: and what you need."})
        run.turns = 0
        run.emit("goal", **goal_public(goal))
        save_session(run.session)
        return True
    run.emit("goal", **goal_public(goal))
    save_session(run.session)
    return False


@route("POST", "/api/agent/goal")
def agent_goal(req):
    """Pause, resume, edit or clear a chat's goal. A goal is set by sending a message with goal: true."""
    ws = req.workspace()
    sid, action = req.field("id"), req.field("action")
    if action not in ("pause", "resume", "edit", "clear"):
        raise ApiError(400, "action must be pause, resume, edit or clear")
    with APP.lock:
        run = next((r for r in APP.active.values() if r.session["id"] == sid and r.owner == req.current_user["username"]), None)
    session = run.session if run else load_session(req, sid)
    if session.get("ws") != ws["id"]:
        raise ApiError(400, "session belongs to another workspace")
    goal = session.get("goal")
    if action == "clear":
        session.pop("goal", None)
    elif not isinstance(goal, dict):
        raise ApiError(404, "this chat has no goal")
    elif action == "pause":
        goal.update(state="paused", why="Paused by you.")
    elif action == "resume":
        goal.update(state="active", why="", rounds=0, codex_used=0)      # a resume grants the rounds and the allowance again
    else:
        text = req.field("text")
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_GOAL_CHARS:
            raise ApiError(400, f"the goal needs 1 to {MAX_GOAL_CHARS} characters")
        goal["text"] = text.strip()
        extra = goal_extras(req)
        for key in ("parts", "codex_tokens", "codex_pct"):     # an emptied box removes it
            goal.pop(key, None)
        goal.update(extra)
        if run:                          # the running model hears it at its next step, like a steer
            with run.cond:
                if not run.closing:
                    run.inbox.append(f"I changed the goal. Goal from now on: {goal['text']}")
        else:
            session["messages"].append({"role": "user", "content": f"Studio note: the user changed the goal. Goal from now on: {goal['text']}"})
    if run:
        run.emit("goal", **(goal_public(session.get("goal")) or {"state": "cleared"}))
    else:
        save_session(session)
    return {"goal": goal_public(session.get("goal"))}


# ---------------------------------------------------------------------------
# Scheduled tasks and /loop: agent runs the server starts on a timetable
# ---------------------------------------------------------------------------

TASK_KINDS = ("hourly", "daily", "weekdays", "weekly", "every")
TASK_MODES = ("look", "ask", "auto")          # look only (plan first), stop at what needs a yes, or do everything
MAX_TASKS, MAX_TASK_PROMPT, MAX_LOOP_RUNS = 50, 8000, 100
TASK_GIVE_UP_S = 1800                         # a run that cannot start (the seat or folder is busy) is skipped after this
TASK_FAILS_TO_PAUSE = 3


def describe_ask(fields):
    kind = fields.get("kind")
    if kind == "command":
        return f"run the command `{str(fields.get('command'))[:200]}`"
    if kind == "edit":
        return {"delete": "delete", "picture": "save a picture as", "create": "create"}.get(fields.get("action"), "change") + f" {fields.get('path')}"
    if kind == "question":
        return "ask you: " + "; ".join(q["question"] for q in fields.get("questions") or [])[:300]
    if kind == "skill":
        return f"save the skill {fields.get('name')}"
    return f"use the tool {fields.get('server')} · {fields.get('tool')}"


def next_run(schedule, after):
    """The next time a schedule is due after `after` (local time). Missed times are not made up for."""
    kind = schedule["kind"]
    if kind == "every":
        return after + schedule["minutes"] * 60
    hour, minute = (int(x) for x in schedule["time"].split(":"))
    if kind == "hourly":
        t = time.localtime(after)
        candidate = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour, minute, 0, 0, 0, -1))
        return candidate if candidate > after else candidate + 3600
    base = time.localtime(after)
    for days in range(0, 9):
        candidate = time.mktime((base.tm_year, base.tm_mon, base.tm_mday + days, hour, minute, 0, 0, 0, -1))
        weekday = time.localtime(candidate).tm_wday
        if candidate <= after or (kind == "weekdays" and weekday > 4) or (kind == "weekly" and weekday != schedule["weekday"]):
            continue
        return candidate
    return after + 86400


def clean_schedule(raw):
    if not isinstance(raw, dict) or raw.get("kind") not in TASK_KINDS:
        raise ApiError(400, f"schedule.kind must be one of {', '.join(TASK_KINDS)}")
    kind = raw["kind"]
    if kind == "every":
        minutes = to_int(raw.get("minutes"), 0)
        if not 1 <= minutes <= 24 * 60:
            raise ApiError(400, "every: minutes must be 1 to 1440")
        return {"kind": kind, "minutes": minutes}
    when = raw.get("time") if isinstance(raw.get("time"), str) else ""
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", when):
        raise ApiError(400, "time must be HH:MM")
    out = {"kind": kind, "time": when}
    if kind == "weekly":
        weekday = to_int(raw.get("weekday"), -1)
        if not 0 <= weekday <= 6:
            raise ApiError(400, "weekly: weekday must be 0 (Monday) to 6 (Sunday)")
        out["weekday"] = weekday
    return out


def parse_loop(text):
    """/loop's words: (minutes, prompt, limits) from "10m check the build, stop after 3 answers"."""
    match = re.match(r"^\s*(\d+)\s*(s|sec|secs|seconds?|m|min|mins|minutes?|h|hr|hrs|hours?)\b\s*(.*)$", text or "", re.I | re.S)
    if not match:
        raise ApiError(400, "start with how often, such as /loop 10m check whether the build passes")
    amount, unit, rest = int(match[1]), match[2].lower(), match[3].strip()
    minutes = max(1, round(amount / 60)) if unit.startswith("s") else amount * 60 if unit.startswith("h") else amount
    limits = {}
    words = {"once": 1, "twice": 2, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
    count = re.search(r"[,;.]?\s*\bstop after (\d+|\w+)(?: (?:answers?|runs?|times?))?\b", rest, re.I)
    if count and (count[1].isdigit() or count[1].lower() in words):
        limits["max_runs"] = int(count[1]) if count[1].isdigit() else words[count[1].lower()]
        rest = (rest[:count.start()] + rest[count.end():]).strip(" ,;.")
    span = re.search(r"[,;.]?\s*\bfor (?:the next )?(\d+) ?(minutes?|mins?|hours?|h)\b", rest, re.I)
    if span:
        limits["until"] = time.time() + int(span[1]) * (3600 if span[2].lower().startswith("h") else 60)
        rest = (rest[:span.start()] + rest[span.end():]).strip(" ,;.")
    until = re.search(r"[,;.]?\s*\bstop when (?:it )?(?:says |shows |prints )?[\"“']?(.+?)[\"”']?\s*$", rest, re.I)
    if until:
        limits["stop_when"] = until[1].strip()[:200]
        rest = rest[:until.start()].strip(" ,;.")
    if not rest:
        raise ApiError(400, "say what to do after how often, such as /loop 10m check whether the build passes")
    return minutes, rest[:MAX_TASK_PROMPT], limits


class TaskBook:
    """The users' tasks and loops, kept in data/tasks.json, and the timer that starts them."""

    def __init__(self, data_dir):
        self.path = os.path.join(data_dir, "tasks.json")
        self.lock = threading.Lock()
        self.tasks = read_json(self.path, {}).get("tasks", []) if os.path.exists(self.path) else []
        now = time.time()
        for task in self.tasks:
            task.pop("running", None)
            task.pop("waiting_since", None)
            if task.get("enabled") and task.get("next_run", 0) < now:      # missed while Studio was off: wait for the next time
                task["next_run"] = next_run(task["schedule"], now)

    def save(self):
        write_json_atomic(self.path, {"tasks": self.tasks})

    def find(self, tid, owner):
        task = next((t for t in self.tasks if t["id"] == tid), None)
        if not task or task["owner"] != owner:
            raise ApiError(404, "unknown task")
        return task

    def public(self, task):
        return {k: v for k, v in task.items() if k != "owner"}

    def due(self, now):
        with self.lock:
            return [t for t in self.tasks if t.get("enabled") and not t.get("running") and t.get("next_run", 0) <= now]

    def tick(self):
        if not APP.cfg.get("tasks", True):
            return
        now = time.time()
        for task in self.due(now):
            why = start_task(task)
            with self.lock:
                if why is None:
                    task.pop("waiting_since", None)
                    continue
                task.setdefault("waiting_since", now)
                task["waiting"] = why
                if now - task["waiting_since"] > TASK_GIVE_UP_S or task["schedule"]["kind"] == "every":
                    task.pop("waiting_since", None)
                    task["next_run"] = next_run(task["schedule"], now)
                    task["last"] = {"started": now, "ended": now, "reason": "skipped", "summary": f"Skipped: {why}."}
                    self.save()
                    sync_publish(task["owner"], "", "tasks")


def task_loop():
    while True:
        time.sleep(5)
        try:
            APP.tasks.tick()
        except Exception:
            traceback.print_exc()


def start_task(task):
    """Starts a due task. Returns None when it started, or why it could not."""
    user = APP.auth.users.get(task["owner"])
    ws = APP.workspaces.get(task["ws"])
    if not user or not ws or not os.path.isdir(ws["path"]):
        with APP.tasks.lock:
            task["enabled"] = False
            task["last"] = {"started": time.time(), "ended": time.time(), "reason": "error", "summary": "Paused: its folder or account is gone."}
            APP.tasks.save()
        return None
    room = APP.room
    with room.lock:
        holder = room.holder
        if holder and holder["username"] != user["username"]:
            return f"{holder['name']} had the agent"
        if not holder:
            if room.queue:
                return "people were waiting for the agent"
            room.grant({**user, "username": user["username"]})
    loop = task.get("loop")
    if loop:
        try:
            session = read_json(session_path(loop["session"]), None)
        except ApiError:
            session = None
        if not session:
            with APP.tasks.lock:
                task["enabled"] = False
                APP.tasks.save()
            return None
    else:
        session = {"id": secrets.token_hex(6), "ws": ws["id"], "owner": task["owner"], "title": f"Task: {task['title']}"[:60],
                   "model": task["model"], "events": [], "messages": [], "task": task["id"]}
    known = {m["name"]: m for m in get_models()[0]}
    mode = task["mode"]
    # Look only runs read-only commands freely: plan mode still refuses edits, and anything that could change things asks, so it is skipped.
    allowed = mode in ("auto", "look")
    run = Run(ws, session, task["model"], known.get(task["model"], {}).get("tools") is not False, allowed, allowed, None)
    run.owner, run.unattended, run.plan_mode = task["owner"], True, mode == "look"
    run.task_id = task["id"]
    with APP.lock:
        if ws["id"] in APP.active:
            return "another run was working in its folder"
        APP.active[ws["id"]] = run
        note_active()
        APP.runs[run.id] = run
    message = task["prompt"]
    extras = plan_note() if run.plan_mode else ""
    extras += ("This message comes from a scheduled task, and nobody is watching. Whatever needs the user's yes "
               "is answered no at once, so do what you can without it, and end with a short report.\n")
    session["events"].append({"type": "user", "text": message, "task": task["id"], "mi": len(session["messages"]), **({"plan_mode": True} if run.plan_mode else {})})
    session["messages"].append({"role": "user", "content": extras + USER_EXTRAS_END + message})
    save_session(session)
    with APP.tasks.lock:
        task["running"] = run.id
        task["waiting"] = ""
        task["next_run"] = next_run(task["schedule"], time.time())
        APP.tasks.save()
    threading.Thread(target=task_main, args=(run, task["id"]), daemon=True).start()
    room.changed()
    sync_publish(task["owner"], "", "run_started", ws=ws["id"], session=session["id"], run=run.id, task=task["id"])
    sync_publish(task["owner"], "", "tasks")
    return None


def task_main(run, tid):
    started = time.time()
    try:
        agent_main(run)
    finally:
        done = next((e for e in reversed(run.events) if e["type"] == "done"), {})
        files = next((e for e in reversed(run.events) if e["type"] == "files"), {})
        answer = done.get("summary") or next((m.get("content") for m in reversed(run.session["messages"])
                                              if m.get("role") == "assistant" and isinstance(m.get("content"), str) and m.get("content").strip()), "")
        reason = done.get("reason", "error")
        last = {"started": started, "ended": time.time(), "reason": reason, "summary": (answer or "")[:4000], "session": run.session["id"],
                "files": [f["path"] for f in files.get("files", [])][:20], "file_count": files.get("total", 0), "refused": run.refused[:10]}
        with APP.tasks.lock:
            task = next((t for t in APP.tasks.tasks if t["id"] == tid), None)
            if task:
                task.pop("running", None)
                task["last"] = last
                task["runs"] = task.get("runs", 0) + 1
                task.setdefault("history", []).insert(0, {k: last[k] for k in ("started", "ended", "reason", "session")})
                del task["history"][10:]
                failed = reason in ("error", "stuck", "cut_off", "max_turns")
                task["fails_in_row"] = task.get("fails_in_row", 0) + 1 if failed else 0
                if task["fails_in_row"] >= TASK_FAILS_TO_PAUSE:
                    task["enabled"] = False
                    task["paused_why"] = f"Paused: {TASK_FAILS_TO_PAUSE} runs in a row did not work."
                loop = task.get("loop")
                if loop:
                    loop["runs"] = loop.get("runs", 0) + 1
                    stop = (loop.get("max_runs") and loop["runs"] >= loop["max_runs"]) or loop["runs"] >= MAX_LOOP_RUNS \
                        or (loop.get("until") and time.time() >= loop["until"]) \
                        or (loop.get("stop_when") and loop["stop_when"].lower() in (answer or "").lower())
                    if stop or not task["enabled"]:
                        task["enabled"] = False
                        task["paused_why"] = task.get("paused_why") or "The loop ended by its own rule."
                APP.tasks.save()
                sync_publish(task["owner"], "", "tasks", event="ended", task=tid, title=task["title"], reason=reason, refused=len(run.refused))


def clean_task(req, raw, old=None):
    if not isinstance(raw, dict):
        raise ApiError(400, "task must be an object")
    title, prompt = raw.get("title"), raw.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > MAX_TASK_PROMPT:
        raise ApiError(400, f"say what the task should do (at most {MAX_TASK_PROMPT} characters)")
    title = (title.strip() if isinstance(title, str) and title.strip() else prompt.strip().split("\n")[0])[:80]
    mode = raw.get("mode", "look")
    if mode not in TASK_MODES:
        raise ApiError(400, f"mode must be one of {', '.join(TASK_MODES)}")
    model = raw.get("model") or (old or {}).get("model") or APP.cfg["default_model"]
    if not isinstance(model, str):
        raise ApiError(400, "model must be a string")
    if is_codex(model):
        raise ApiError(400, "scheduled tasks use Studio's own models, not Codex")
    ws = req.workspace()
    return {"title": title, "prompt": prompt.strip(), "mode": mode, "model": model, "ws": ws["id"], "schedule": clean_schedule(raw.get("schedule"))}


@route("GET", "/api/tasks")
def tasks_list(req):
    me = req.current_user["username"]
    with APP.tasks.lock:
        mine = [APP.tasks.public(t) for t in APP.tasks.tasks if t["owner"] == me]
    names = {w["id"]: os.path.basename(w["path"]) for w in APP.workspaces.values()}
    for t in mine:
        t["folder"] = names.get(t["ws"], "")
    return {"tasks": mine, "enabled": APP.cfg.get("tasks", True), "now": time.time()}


@route("POST", "/api/tasks/save")
def tasks_save(req):
    me = req.current_user["username"]
    raw = req.field("task")
    tid = raw.get("id") if isinstance(raw, dict) else None
    with APP.tasks.lock:
        old = APP.tasks.find(tid, me) if tid else None
    fields = clean_task(req, raw, old)
    with APP.tasks.lock:
        if old:
            old.update(fields)
            old["next_run"] = next_run(old["schedule"], time.time())
            task = old
        else:
            if sum(t["owner"] == me for t in APP.tasks.tasks) >= MAX_TASKS:
                raise ApiError(400, f"at most {MAX_TASKS} tasks each")
            task = {"id": secrets.token_hex(6), "owner": me, "created": time.time(), "enabled": True, "runs": 0, **fields}
            task["next_run"] = next_run(task["schedule"], time.time())
            APP.tasks.tasks.append(task)
        task.pop("paused_why", None)
        APP.tasks.save()
        return {"task": APP.tasks.public(task)}


@route("POST", "/api/tasks/loop")
def tasks_loop(req):
    """/loop: the same message sent again in this chat every so often, until its own rule ends it."""
    ws = req.workspace()
    session = load_session(req, req.field("session"))
    if session.get("ws") != ws["id"]:
        raise ApiError(400, "session belongs to another workspace")
    minutes, prompt, limits = parse_loop(req.field("text"))
    me = req.current_user["username"]
    model = req.field("model") if isinstance(req.field("model"), str) else session.get("model") or APP.cfg["default_model"]
    if is_codex(model):
        raise ApiError(400, "/loop uses Studio's own models, not Codex")
    mode = req.field("mode") if req.field("mode") in TASK_MODES else "ask"
    with APP.tasks.lock:
        for t in APP.tasks.tasks:          # one loop per chat: a new one replaces it
            if t["owner"] == me and (t.get("loop") or {}).get("session") == session["id"]:
                t["enabled"] = False
                t["paused_why"] = "Replaced by a newer loop."
        task = {"id": secrets.token_hex(6), "owner": me, "created": time.time(), "enabled": True, "runs": 0, "title": f"Loop: {prompt[:60]}",
                "prompt": prompt, "mode": mode, "model": model, "ws": ws["id"], "schedule": {"kind": "every", "minutes": minutes},
                "loop": {"session": session["id"], "runs": 0, **limits}}
        task["next_run"] = time.time() + 2 if not APP.active.get(ws["id"]) else next_run(task["schedule"], time.time())
        APP.tasks.tasks.append(task)
        APP.tasks.save()
    return {"task": APP.tasks.public(task)}


@route("POST", "/api/tasks/action")
def tasks_action(req):
    me = req.current_user["username"]
    action = req.field("action")
    if action not in ("pause", "resume", "delete", "run", "stop"):
        raise ApiError(400, "action must be pause, resume, delete, run or stop")
    with APP.tasks.lock:
        task = APP.tasks.find(req.field("id"), me)
        if action == "pause":
            task["enabled"] = False
            task["paused_why"] = "Paused by you."
        elif action == "resume":
            task.update(enabled=True, fails_in_row=0)
            task.pop("paused_why", None)
            task["next_run"] = next_run(task["schedule"], time.time())
        elif action == "delete":
            APP.tasks.tasks.remove(task)
        elif action == "run" and task.get("running"):
            raise ApiError(409, "it is running now")
        APP.tasks.save()
        running = APP.runs.get(task.get("running") or "")
    if action in ("stop", "delete") and running:
        running.request_stop()
    if action == "run":
        why = start_task(task)
        if why:
            raise ApiError(409, f"It cannot start now: {why}.")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Time-skip: the agent works on a copy of the folder; Studio runs the tests on both, shows the difference, and the user
# keeps the update (with an undo point) or throws it away. Their own folder is untouched until then.
# ---------------------------------------------------------------------------

TIMESKIP_NOTE = ("Time-skip: you are working on a copy of the project, so make the change completely, without "
                 "asking for approval of each step: run the project's tests and fix what fails, then call finish with a summary. "
                 "Studio then runs the tests on the copy and on the original, and the user compares them before keeping your version.\n")
TIMESKIP_SKIP = IGNORE_DIRS | {".git"}
MAX_TIMESKIP_FILES, MAX_TIMESKIP_ROWS = 100, 400


def timeskip_dir(jid=None):
    return os.path.join(APP.data_dir, "timeskip", *( [jid] if jid else []))


def timeskip_job(jid):
    if not isinstance(jid, str) or not ID_RE.match(jid):
        raise ApiError(404, "unknown Time-skip")
    job = read_json(os.path.join(timeskip_dir(jid), "job.json"), None)
    if job is None:
        raise ApiError(404, "unknown Time-skip")
    return job


def save_timeskip(job):
    write_json_atomic(os.path.join(timeskip_dir(job["id"]), "job.json"), job)


def folder_bytes(root, limit):
    """The folder's size in bytes, or None once it is past the limit."""
    total = 0
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != ".strata-studio"]
        for name in files:
            try:
                total += os.lstat(os.path.join(folder, name)).st_size
            except OSError:
                pass
            if total > limit:
                return None
    return total


def compared_files(original, copy):
    """[(path, status)] of files that differ between the two folders, leaving out .git and folders such as node_modules."""
    def listing(root):
        found = {}
        for folder, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in TIMESKIP_SKIP]
            for name in files:
                full = os.path.join(folder, name)
                if not os.path.islink(full):
                    found[os.path.relpath(full, root).replace(os.sep, "/")] = full
        return found
    a, b = listing(original), listing(copy)
    out = []
    for path in sorted(a.keys() | b.keys()):
        if path not in a:
            out.append((path, "new"))
        elif path not in b:
            out.append((path, "deleted"))
        else:
            try:
                same = os.path.getsize(a[path]) == os.path.getsize(b[path]) and open(a[path], "rb").read() == open(b[path], "rb").read()
            except OSError:
                same = True
            if not same:
                out.append((path, "changed"))
    return out


def text_or_none(path):
    try:
        text, _ = read_text_file(path)
        return text
    except OSError:
        return None


def timeskip_compare(run, reason):
    """At the end of the copy's run: the differences, and the tests on both versions, for the user to decide."""
    job, original = run.timeskip, APP.workspaces.get(run.timeskip["ws"])
    try:
        run.emit("status", text="Comparing the copy with your folder…")
        changes = compared_files(original["path"], run.ws["path"])
        files = []
        for path, status in changes[:MAX_TIMESKIP_FILES]:
            old = None if status == "new" else text_or_none(os.path.join(original["path"], path))
            new = None if status == "deleted" else text_or_none(os.path.join(run.ws["path"], path))
            entry = {"path": path, "status": status}
            if (status == "new" and new is None) or (status == "deleted" and old is None) or (status == "changed" and (old is None or new is None)):
                entry["binary"] = True
            else:
                change = describe_change(old or "", new or "")
                change["rows"] = change.get("rows", [])[:MAX_TIMESKIP_ROWS]
                entry["change"] = change
            files.append(entry)
        checks = None
        command = detect_test_command(run.ws["path"]) if files else None
        if command and not run.stop:
            decision = "accept"
            if not run.auto_commands:
                decision, _ = run.ask(kind="command", command=command, purpose="timeskip")
            if decision == "accept":
                run.emit("status", text=f"Running the tests on the copy: {command}")
                ok_b, sum_b, out_b = run_online_if_allowed(run, command)
                run.emit("status", text=f"Running the tests on your folder: {command}")
                ok_a, sum_a, out_a = run_command(types.SimpleNamespace(ws=original, stop=False), command)
                checks = {"command": command, "original": {"ok": ok_a, "summary": sum_a, "output": out_a[-3000:]},
                          "copy": {"ok": ok_b, "summary": sum_b, "output": out_b[-3000:]}}
        job.update(state="ready" if files else "nothing", ended=time.time(), reason=reason, files=len(changes))
        save_timeskip(job)
        run.emit("timeskip", id=job["id"], state=job["state"], files=files, total=len(changes), checks=checks, reason=reason)
        if not files:
            timeskip_cleanup(job)
    except Exception as exc:
        traceback.print_exc()
        job.update(state="failed")
        save_timeskip(job)
        run.emit("timeskip", id=job["id"], state="failed", error=str(exc))


def timeskip_cleanup(job):
    copy_ws = {"id": job["copy_id"], "path": job["copy"], **{k: v for k, v in job.get("sandbox", {}).items()}}
    if job.get("sandboxed"):
        try:
            remove_sandbox(copy_ws)
        except Exception:
            pass
    shutil.rmtree(os.path.join(timeskip_dir(job["id"]), "work"), ignore_errors=True)
    shutil.rmtree(os.path.join(APP.data_dir, "checkpoints", job["copy_id"]), ignore_errors=True)
    shutil.rmtree(os.path.join(APP.data_dir, "sandbox_homes", job["copy_id"]), ignore_errors=True)


def timeskip_main(run, job):
    """Copies the folder, then runs the agent on the copy (agent_main ends by comparing)."""
    original = APP.workspaces.get(job["ws"])
    try:
        run.emit("status", text="Copying your folder for Time-skip…")
        os.makedirs(os.path.dirname(job["copy"]), exist_ok=True)
        result = subprocess.run(["cp", "-a", "--reflink=auto", original["path"] + "/.", job["copy"]], capture_output=True, text=True, timeout=900) \
            if os.path.isdir(job["copy"]) else subprocess.run(["cp", "-a", "--reflink=auto", original["path"], job["copy"]], capture_output=True, text=True, timeout=900)
        if result.returncode:
            raise RuntimeError(result.stderr.strip()[-500:] or "cp failed")
        shutil.rmtree(os.path.join(job["copy"], ".strata-studio"), ignore_errors=True)
        job["snapshot"] = folder_snapshot(original["path"]) or {}
        job["state"] = "working"
        save_timeskip(job)
    except Exception as exc:
        job["state"] = "failed"
        save_timeskip(job)
        run.emit("error", text=f"Time-skip could not copy the folder: {exc}")
        timeskip_cleanup(job)
        run.close("error")
        return
    agent_main(run)


@route("POST", "/api/timeskip/start")
def timeskip_start(req):
    ws = req.workspace()
    message = req.field("message")
    if not isinstance(message, str) or not message.strip():
        raise ApiError(400, "message required")
    session = load_session(req, req.field("session"))
    if session.get("ws") != ws["id"]:
        raise ApiError(400, "session belongs to another workspace")
    if session_busy(session["id"]):
        raise ApiError(409, "a run is already active in this chat")
    limit = APP.cfg.get("timeskip_max_mb", 2048) * 1024 * 1024
    if folder_bytes(ws["path"], limit) is None:
        raise ApiError(413, f"Time-skip copies the folder, and this one is larger than {limit // 1048576} MB.")
    model = req.field("model") or APP.cfg["default_model"]
    if not isinstance(model, str):
        raise ApiError(400, "model must be a string")
    if is_codex(model):
        raise ApiError(400, "Time-skip works with Studio's own models, not with Codex")
    known = {m["name"]: m for m in get_models()[0]}
    if known.get(model, {}).get("tools") is False:
        raise ApiError(400, f"{model} cannot use tools, so it cannot work on a copy")
    APP.room.claim(req.current_user)
    jid = secrets.token_hex(6)
    copy_id = "ts" + jid
    sandbox = {k: ws[k] for k in ("owner", "sandbox", "sandbox_image", "sandbox_network", "sandbox_ports") if k in ws}
    copy_ws = {"id": copy_id, "path": os.path.join(timeskip_dir(jid), "work"), **sandbox}
    job = {"id": jid, "owner": req.current_user["username"], "ws": ws["id"], "session": session["id"], "copy": copy_ws["path"],
           "copy_id": copy_id, "sandbox": sandbox, "sandboxed": sandboxed(copy_ws), "state": "copying", "started": time.time(), "message": message.strip()[:2000]}
    os.makedirs(timeskip_dir(jid), exist_ok=True)
    save_timeskip(job)
    run = Run(copy_ws, session, model, True, True, bool(req.field("auto_commands")), None)
    run.owner, run.timeskip = req.current_user["username"], job
    with APP.lock:
        if copy_id in APP.active:
            raise ApiError(409, "a run is already active")
        APP.active[copy_id] = run
        note_active()
        APP.runs[run.id] = run
    session["model"] = model
    session["title"] = session.get("title") or message.strip()[:60]
    session["events"].append({"type": "user", "text": message, "timeskip": jid, "mi": len(session["messages"])})
    session["messages"].append({"role": "user", "content": TIMESKIP_NOTE + USER_EXTRAS_END + message})
    save_session(session)
    threading.Thread(target=timeskip_main, args=(run, job), daemon=True).start()
    APP.room.changed()
    sync_publish(run.owner, req.sync_origin(), "run_started", ws=ws["id"], session=session["id"], run=run.id)
    return {"run": run.id, "id": jid}


@route("POST", "/api/timeskip/decide")
def timeskip_decide(req):
    """Keep the copy's version (its differences are written into the folder, with an undo point) or throw it away."""
    job = timeskip_job(req.field("id"))
    if job["owner"] != req.current_user["username"]:
        raise ApiError(404, "unknown Time-skip")
    action = req.field("action")
    if action not in ("keep", "discard"):
        raise ApiError(400, "action must be keep or discard")
    if job["state"] != "ready" and not (action == "discard" and job["state"] in ("failed", "interrupted", "ready")):
        raise ApiError(409, f"This Time-skip is {job['state']}.")
    ws = APP.get_workspace(job["ws"], req.current_user)
    session = read_json(session_path(job["session"]), None)
    if session_busy(job["session"]):
        raise ApiError(409, "Wait until the run in this chat ends.")
    result = {"action": action}
    if action == "keep":
        changes = compared_files(ws["path"], job["copy"])
        snapshot, now = job.get("snapshot") or {}, folder_snapshot(ws["path"]) or {}
        conflicts = [path for path, _ in changes if tuple(snapshot.get(path) or ()) != tuple(now.get(path) or ())]
        if conflicts and req.field("force") is not True:
            raise ApiError(409, "These files changed in your folder after the copy was made: " + ", ".join(conflicts[:10])
                           + ". Keeping the update would overwrite those changes.", conflicts=conflicts)
        previous = {}
        for path, status in changes:
            target = resolve(ws, path)
            old = text_or_none(target) if status != "new" else None
            if status == "new" or old is not None:
                previous[path] = old
            if status == "deleted":
                move_to_trash(ws, target)
            else:
                with open(os.path.join(job["copy"], path), "rb") as fh:
                    write_bytes(target, fh.read())
        result.update(checkpoint=make_checkpoint(ws, previous) if previous else None, files=[path for path, _ in changes], conflicts=conflicts)
    job["state"] = "kept" if action == "keep" else "discarded"
    save_timeskip(job)
    timeskip_cleanup(job)
    if session:
        session["events"].append({"type": "timeskip_decided", "id": job["id"], **result})
        save_session(session)
    return result


def timeskip_recover():
    """After a restart, a Time-skip that was copying or working cannot go on."""
    folder = timeskip_dir()
    for jid in os.listdir(folder) if os.path.isdir(folder) else []:
        job = read_json(os.path.join(folder, jid, "job.json"), None)
        if job and job.get("state") in ("copying", "working"):
            job["state"] = "interrupted"
            write_json_atomic(os.path.join(folder, jid, "job.json"), job)


# ---------------------------------------------------------------------------
# After a run: test checks and the files card; agent_main starts a run
# ---------------------------------------------------------------------------

def detect_test_command(root):
    """The project's test command: a `Test command: ...` line in STUDIO.md, or the usual file for each language."""
    def read(name):
        try:
            with open(os.path.join(root, name), encoding="utf-8", errors="replace") as fh:
                return fh.read(200000)
        except OSError:
            return ""

    match = re.search(r"^test command:\s*`?([^`\n]+?)`?\s*$", read("STUDIO.md"), re.I | re.M)
    if match:
        return match[1]
    if re.search(r"^test\s*:", read("Makefile"), re.M):
        return "make test"
    try:
        script = (json.loads(read("package.json") or "{}").get("scripts") or {}).get("test") or ""
    except (ValueError, AttributeError, RecursionError):
        script = ""
    if script and "no test specified" not in script:
        return "npm test"
    if os.path.exists(os.path.join(root, "go.mod")):
        return "go test ./..."
    if os.path.exists(os.path.join(root, "Cargo.toml")):
        return "cargo test"
    if os.path.exists(os.path.join(root, "pytest.ini")) or os.path.exists(os.path.join(root, "conftest.py")) or "[tool.pytest" in read("pyproject.toml"):
        return "python3 -m pytest -q"
    for folder, pattern in (("tests", "test*.py"), (".", "test*.py")):
        path = os.path.join(root, folder)
        if os.path.isdir(path) and any(re.fullmatch(r"test.*\.py", name) for name in os.listdir(path)):
            return f"python3 -m unittest discover -s {folder} -p '{pattern}'"
    return None


# The files a run made or changed, including those its commands wrote: the folder is noted before and compared after.
SWEEP_MAX_FILES, SWEEP_MAX_S, SWEEP_SHOWN = 5000, 1.5, 60


def folder_snapshot(root):
    """{relative path: (modified time, size)} of the folder's files, or None when it is too big to look at quickly."""
    found, started, stack = {}, time.monotonic(), [root]
    while stack:
        folder = stack.pop()
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir(follow_symlinks=False):
                if entry.name not in IGNORE_DIRS and entry.name != ".git":
                    stack.append(entry.path)
                continue
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            found[os.path.relpath(entry.path, root).replace(os.sep, "/")] = (st.st_mtime_ns, st.st_size)
            if len(found) > SWEEP_MAX_FILES or time.monotonic() - started > SWEEP_MAX_S:
                return None
    return found


def run_files(run, before):
    """[{path, status, added, removed}] for the files this run created, changed or deleted."""
    edits = {}
    for ev in run.events:
        if ev["type"] == "file_changed" and isinstance(ev.get("path"), str):
            change = ev.get("change") or {}
            seen = edits.setdefault(ev["path"], {"added": 0, "removed": 0, "action": ev.get("action"), "first": ev.get("checkpoint")})
            seen["added"] += change.get("added") or 0
            seen["removed"] += change.get("removed") or 0
            seen["action"] = ev.get("action") or seen["action"]
    # The counts are the file's net change in this run: its first undo point holds how it was before. Several edits to one
    # file (written, then partly taken back) would otherwise add up to far more than what changed.
    for path, edit in edits.items():
        try:
            was = (read_json(checkpoint_path(run.ws, edit["first"]), None) or {}).get("files", {}).get(path)
        except ApiError:
            was = None
        if was is None:
            continue
        try:
            now = text_or_none(resolve(run.ws, path))
        except ApiError:
            now = None
        old = was.get("content") if was.get("existed") else ""
        if old is not None and (now is not None or edit["action"] == "delete"):
            net = describe_change(old, now or "")
            edit["added"], edit["removed"] = net.get("added", 0), net.get("removed", 0)
    after = folder_snapshot(run.ws["path"]) if before is not None else None
    files = {}
    if after is not None:
        for path in after.keys() | before.keys():
            if path not in before:
                files[path] = "new"
            elif path not in after:
                files[path] = "deleted"
            elif after[path] != before[path]:
                files[path] = "changed"
    for path, edit in edits.items():
        files.setdefault(path, {"create": "new", "delete": "deleted"}.get(edit["action"], "changed"))
    out = [{"path": path, "status": status, **({"added": edits[path]["added"], "removed": edits[path]["removed"]} if path in edits else {})}
           for path, status in sorted(files.items(), key=lambda item: ({"new": 0, "changed": 1, "deleted": 2}[item[1]], item[0]))]
    return out


def check_finished_run(run, reason):
    """Run the project's tests itself and show the result beside the model's claim. It is never sent to the model."""
    if not APP.cfg["verify_on_finish"] or not run.changed or run.stop or reason not in ("finished", "answered"):
        return
    command = detect_test_command(run.ws["path"])
    if not command:
        run.emit("check", status="none")
        return
    if not run.auto_commands:
        decision, _ = run.ask(kind="command", command=command, purpose="check")
        if decision != "accept":
            run.emit("check", status="skipped", command=command)
            return
    run.emit("status", text=f"Running the tests: {command}")
    ok, summary, output = run_online_if_allowed(run, command)
    run.emit("check", status="passed" if ok else "failed", command=command, summary=summary, output=output[-4000:], claimed=reason == "finished")


def agent_main(run):
    if is_codex(run.model):
        return codex_main(run)
    reason, summary = "error", None
    try:
        start_run_branch(run)
        if APP.cfg["mcp"] and run.tools_enabled:
            run.emit("status", text="Connecting to tool servers…")
            run.mcp_tools = APP.mcp.tools_for(run.owner)
        if APP.cfg.get("user_tools", True) and run.tools_enabled:
            run.user_tools = user_tools_for(run.owner)
        before = folder_snapshot(run.ws["path"]) if run.tools_enabled else None
        while True:
            reason, summary = agent_loop(run)
            if not run.inbox and goal_next(run, reason, summary):
                run.emit("status", text="The goal is not reached yet, so the agent carries on…")
                continue
            with run.cond:
                if not (reason in ("answered", "finished") and run.inbox and not run.stop):
                    run.closing = True              # no more guiding messages; one that came as it ended is answered first
                    break
        if run.timeskip:
            timeskip_compare(run, reason)
        else:
            files = run_files(run, before) if run.tools_enabled else []
            if files:
                run.emit("files", files=files[:SWEEP_SHOWN], total=len(files))
            check_finished_run(run, reason)
    except Exception as exc:
        traceback.print_exc()
        run.emit("error", text=f"Internal error: {exc}")
    finally:
        with run.cond:
            run.closing = True                              # no more guiding messages from here on
        if take_guidance(run, run.session["messages"]):     # stopped first: kept for the next message or Continue
            save_session(run.session)
        try:
            finish_run_branch(run)
        except Exception as exc:
            run.emit("error", text=f"Could not save the run's branch: {exc}")
        run.close(reason, summary)
        threading.Thread(target=cache_save, args=(run, "run ended"), daemon=True).start()


# ---------------------------------------------------------------------------
# Codex as the chat's agent (2026-11-17, the user's request): an admin picks one of OpenAI's Codex models in the model
# picker, and Studio drives the Codex CLI's own app server (`codex app-server`, JSON-RPC over stdio, the channel Codex's
# editor extensions use) with the login saved on this machine. Studio never reads that login. Codex keeps the conversation
# (its thread id is kept in the chat); its words, commands and edits become the chat's usual events, its requests to run
# commands or change files become Studio's approval cards (read-only commands pass, like Keystone's), and every edit gets
# an undo point. It runs on this machine in Codex's own sandbox, so only in folders with the Sandbox off.
# ---------------------------------------------------------------------------

CODEX_PREFIX = "codex:"
CODEX_MODELS_S = 600
CODEX_NOTE = ("You are working inside Eidoloom Studio, a web IDE, in the project folder you were started in. The person sees your "
              "messages, commands and file changes in Studio's chat and approves them there. Reply in the language they write in. "
              "Name files by their path inside the project folder (such as src/app.py), not as links or full paths, since "
              "Studio's chat cannot open those. For a large task with independent parts you may use sub-agents.")
CODEX_EARLIER = 30_000           # characters of an earlier conversation (with another model) given to a new Codex thread


def is_codex(model):
    return isinstance(model, str) and model.startswith(CODEX_PREFIX)


class CodexError(Exception):
    pass


class CodexClient:
    """One `codex app-server` process: requests and their answers, and a queue of what Codex sends on its own
    (notifications, and requests such as approvals, which carry an id to answer)."""

    def __init__(self, cwd):
        argv = shlex.split(APP.cfg.get("codex_command") or "codex") + ["app-server"]
        if not shutil.which(argv[0]):
            raise CodexError(f"the Codex CLI ({argv[0]}) is not installed on this machine")
        log = open(os.path.join(APP.data_dir, "codex.log"), "a")
        self.proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True,
                                     bufsize=1, start_new_session=True)
        log.close()
        self.lock = threading.Lock()
        self.next_id = 0
        self.pending = {}
        self.inbox = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if "method" not in msg and msg.get("id") in self.pending:
                box = self.pending[msg["id"]]
                box["msg"] = msg
                box["event"].set()
            else:
                self.inbox.put(msg)
        self.inbox.put({"method": "studio/exited"})
        for box in list(self.pending.values()):
            box["event"].set()

    def _send(self, msg):
        try:
            with self.lock:
                self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", **msg}) + "\n")
                self.proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise CodexError(f"Codex stopped answering ({exc})") from None

    def call(self, method, params, timeout=60):
        with self.lock:
            self.next_id += 1
            rid = self.next_id
        box = {"event": threading.Event(), "msg": None}
        self.pending[rid] = box
        try:
            self._send({"id": rid, "method": method, "params": params})
            if not box["event"].wait(timeout) or box["msg"] is None:
                raise CodexError(f"Codex did not answer {method}")
        finally:
            self.pending.pop(rid, None)
        if "error" in box["msg"]:
            raise CodexError((box["msg"]["error"] or {}).get("message") or f"Codex refused {method}")
        return box["msg"].get("result") or {}

    def notify(self, method, params=None):
        self._send({"method": method, **({"params": params} if params is not None else {})})

    def reply(self, rid, result):
        self._send({"id": rid, "result": result})

    def refuse(self, rid, message):
        self._send({"id": rid, "error": {"code": -32601, "message": message}})

    def start(self):
        self.call("initialize", {"clientInfo": {"name": "strata_studio", "title": "Eidoloom Studio", "version": "1"},
                                 "capabilities": {"experimentalApi": False, "requestAttestation": False}})
        self.notify("initialized")

    def close(self):
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
                self.proc.wait(3)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except OSError:
                    pass


CODEX_CACHE = {"t": 0, "models": [], "error": ""}


def codex_models():
    """Codex's models for the picker, asked of `codex app-server` and kept for ten minutes (a minute after a failure)."""
    if not APP.cfg.get("codex"):
        return []
    with APP.lock:
        if time.time() - CODEX_CACHE["t"] < (CODEX_MODELS_S if CODEX_CACHE["models"] else 60):
            return CODEX_CACHE["models"]
    models, error = [], ""
    client = None
    try:
        client = CodexClient(APP.data_dir)
        client.start()
        result = client.call("model/list", {}, timeout=30)
        for m in result.get("data") or []:
            if m.get("hidden") or not m.get("id"):
                continue
            efforts = [{"value": e.get("reasoningEffort"), "description": (e.get("description") or "")[:200]}
                       for e in m.get("supportedReasoningEfforts") or [] if e.get("reasoningEffort") in CODEX_EFFORTS]
            models.append({"name": CODEX_PREFIX + m["id"], "label": f"{m.get('displayName') or m['id']} · Codex", "kind": "codex",
                           "tools": True, "vision": "image" in (m.get("inputModalities") or ["text", "image"]),
                           "description": (m.get("description") or "")[:200], "efforts": efforts,
                           "default_effort": m.get("defaultReasoningEffort")})
    except (CodexError, OSError) as exc:
        error = str(exc)
    finally:
        if client:
            client.close()
    with APP.lock:
        CODEX_CACHE.update(t=time.time(), models=models, error=error)
    return models


CODEX_USAGE = {"t": 0, "data": None, "error": ""}
CODEX_USAGE_S = 60


def codex_window(w, label):
    if not isinstance(w, dict) or w.get("usedPercent") is None:
        return None
    minutes = w.get("windowDurationMins")
    if minutes == 10080:
        label = "This week"
    elif minutes:
        label = f"{minutes // 60}-hour window" if minutes % 60 == 0 else f"{minutes}-minute window"
    return {"label": label, "used": max(0, min(100, round(float(w["usedPercent"])))), "minutes": minutes, "resets_at": w.get("resetsAt")}


def codex_usage_shape(result, old=None):
    """Codex's rate limits (account/rateLimits/read, or a rolling account/rateLimits/updated merged into the last read)
    as the status bar shows them: the short window (5 hours on ChatGPT plans) and the week, with their resets."""
    snap = result.get("rateLimits") or {}
    old = old or {}
    windows = [codex_window(snap.get("primary"), "Short window"), codex_window(snap.get("secondary"), "Long window")]
    previous = old.get("windows") or [None, None]
    windows = [w or (previous[i] if i < len(previous) else None) for i, w in enumerate(windows)]
    credits = result.get("rateLimitResetCredits")
    return {"plan": snap.get("planType") or old.get("plan"), "windows": windows,
            "reached": snap.get("rateLimitReachedType"), "allowed": result.get("ordinaryUsageAllowed", old.get("allowed")),
            "reset_credits": credits.get("availableCount") if isinstance(credits, dict) else old.get("reset_credits"), "at": time.time()}


def codex_usage(fresh=False):
    """The ChatGPT plan's Codex limits, read through `codex app-server` and kept for a minute."""
    with APP.lock:
        if not fresh and CODEX_USAGE["data"] and time.time() - CODEX_USAGE["t"] < CODEX_USAGE_S:
            return CODEX_USAGE["data"]
    client, error, data = None, "", None
    try:
        client = CodexClient(APP.data_dir)
        client.start()
        data = codex_usage_shape(client.call("account/rateLimits/read", {}, timeout=30))
    except (CodexError, OSError) as exc:
        error = str(exc)
    finally:
        if client:
            client.close()
    with APP.lock:
        if data:
            CODEX_USAGE.update(t=time.time(), data=data, error="")
        else:
            CODEX_USAGE["error"] = error
        return CODEX_USAGE["data"] or {"error": error, "windows": []}


@route("GET", "/api/codex/usage", admin=True)
def codex_usage_route(req):
    if not APP.cfg.get("codex"):
        raise ApiError(404, "Codex is not turned on in this Studio")
    return codex_usage(req.arg("fresh") == "1")


# ---- Signing Codex in and out (Account & settings → Codex). Codex keeps its login in ~/.codex; Studio only asks
# `codex app-server` to start a login and passes on what it gives: a link and a one-time code (device code, which works from
# any computer), or a sign-in link whose last page, on this machine's localhost, the person pastes back so Studio can open
# it here, or an API key the person types, handed to Codex and not kept by Studio.
CODEX_LOGIN = {"state": "idle"}
CODEX_LOGIN_LOCK = threading.Lock()


def codex_forget():
    """After a sign-in or sign-out the models and usage belong to another account (or none)."""
    with APP.lock:
        CODEX_CACHE.update(t=0, models=[], error="")
        CODEX_USAGE.update(t=0, data=None, error="")


def codex_login_public():
    keep = ("state", "method", "url", "code", "error", "started", "email")
    return {k: v for k, v in CODEX_LOGIN.items() if k in keep}


def codex_login_wait(client, login_id, deadline_s=900):
    """Waits for Codex to say the login finished, then closes its app server."""
    end = time.time() + deadline_s
    try:
        while time.time() < end and CODEX_LOGIN.get("login_id") == login_id:
            try:
                msg = client.inbox.get(timeout=0.5)
            except queue.Empty:
                continue
            if msg.get("method") == "studio/exited":
                CODEX_LOGIN.update(state="failed", error="Codex stopped before the sign-in finished")
                return
            if msg.get("method") == "account/login/completed":
                params = msg.get("params") or {}
                if params.get("loginId") not in (None, login_id):
                    continue
                if params.get("success"):
                    account = (client.call("account/read", {}, timeout=30).get("account") or {})
                    CODEX_LOGIN.update(state="done", email=account.get("email"), url=None, code=None)
                else:
                    CODEX_LOGIN.update(state="failed", error=params.get("error") or "the sign-in did not finish")
                codex_forget()
                return
        if CODEX_LOGIN.get("login_id") == login_id:
            CODEX_LOGIN.update(state="failed", error="the sign-in was not finished in 15 minutes")
    except CodexError as exc:
        CODEX_LOGIN.update(state="failed", error=str(exc))
    finally:
        client.close()
        with CODEX_LOGIN_LOCK:
            if CODEX_LOGIN.get("client") is client:
                CODEX_LOGIN.pop("client", None)


def codex_busy():
    return [r for r in list(APP.runs.values()) if not r.finished and is_codex(r.model)]


@route("GET", "/api/codex/account", admin=True)
def codex_account(req):
    if not APP.cfg.get("codex"):
        raise ApiError(404, "Codex is not turned on in this Studio")
    client = None
    try:
        client = CodexClient(APP.data_dir)
        client.start()
        result = client.call("account/read", {}, timeout=30)
        version = ""
    except (CodexError, OSError) as exc:
        return {"error": str(exc), "login": codex_login_public()}
    finally:
        if client:
            client.close()
    account = result.get("account") or None
    if account:
        account = {"type": account.get("type"), "email": account.get("email"), "plan": account.get("planType"), "workspace": account.get("workspaceName")}
    return {"account": account, "requires_auth": result.get("requiresOpenaiAuth"), "login": codex_login_public(), "version": version}


@route("POST", "/api/codex/logout", admin=True)
def codex_logout(req):
    busy = codex_busy()
    if busy:
        raise ApiError(409, "a Codex chat is working; sign out when it has finished")
    client = None
    try:
        client = CodexClient(APP.data_dir)
        client.start()
        client.call("account/logout", {}, timeout=30)
    except (CodexError, OSError) as exc:
        raise ApiError(502, f"Codex could not sign out: {exc}") from None
    finally:
        if client:
            client.close()
    codex_forget()
    return {"ok": True}


@route("POST", "/api/codex/login", admin=True)
def codex_login(req):
    """method: device (a link and a one-time code), link (a sign-in link; its last page is pasted back) or apikey."""
    method = req.field("method")
    if method not in ("device", "link", "apikey"):
        raise ApiError(400, "method must be device, link or apikey")
    if codex_busy():
        raise ApiError(409, "a Codex chat is working; sign in again when it has finished")
    params = {"type": {"device": "chatgptDeviceCode", "link": "chatgpt", "apikey": "apiKey"}[method]}
    if method == "apikey":
        key = req.field("api_key")
        if not isinstance(key, str) or not re.fullmatch(r"\S{20,300}", key.strip()):
            raise ApiError(400, "paste the whole API key")
        params["apiKey"] = key.strip()
    with CODEX_LOGIN_LOCK:
        old = CODEX_LOGIN.get("client")
        if old:
            old.close()
        client = CodexClient(APP.data_dir)
        try:
            client.start()
            result = client.call("account/login/start", params, timeout=60)
        except (CodexError, OSError) as exc:
            client.close()
            raise ApiError(502, f"Codex could not start the sign-in: {exc}") from None
        CODEX_LOGIN.clear()
        CODEX_LOGIN.update(state="waiting", method=method, started=time.time(), login_id=result.get("loginId"), client=client,
                           url=result.get("verificationUrl") or result.get("authUrl"), code=result.get("userCode"))
        if method == "apikey" or result.get("type") == "apiKey":
            client.close()
            CODEX_LOGIN.pop("client", None)
            CODEX_LOGIN.update(state="done", url=None)
            codex_forget()
        else:
            threading.Thread(target=codex_login_wait, args=(client, CODEX_LOGIN["login_id"]), daemon=True).start()
    return codex_login_public()


@route("POST", "/api/codex/login/finish", admin=True)
def codex_login_finish(req):
    """The link sign-in ends on a page of this machine's localhost (Codex's own small server), which another computer's
    browser cannot reach: the person pastes that page's address, and Studio opens it here."""
    address = req.field("url")
    if CODEX_LOGIN.get("state") != "waiting" or CODEX_LOGIN.get("method") != "link":
        raise ApiError(409, "no link sign-in is waiting")
    if not isinstance(address, str):
        raise ApiError(400, "paste the address of the page the sign-in ended on")
    parts = urllib.parse.urlsplit(address.strip())
    redirect = urllib.parse.parse_qs(urllib.parse.urlsplit(CODEX_LOGIN.get("url") or "").query).get("redirect_uri", [""])[0]
    want = urllib.parse.urlsplit(redirect)
    if parts.scheme != "http" or parts.hostname not in ("localhost", "127.0.0.1") or not want.port or parts.port != want.port or parts.path != want.path:
        raise ApiError(400, "that is not the page the sign-in ended on: it starts with " + (redirect or "http://localhost"))
    target = urllib.parse.urlunsplit(("http", f"127.0.0.1:{want.port}", parts.path, parts.query, ""))
    try:
        with urllib.request.urlopen(target, timeout=30) as resp:
            resp.read(65536)
    except urllib.error.HTTPError as exc:
        if exc.code >= 400:
            raise ApiError(400, f"Codex refused that address ({exc.code}); start the sign-in again") from None
    except OSError as exc:
        raise ApiError(502, f"Codex's sign-in page did not answer ({exc})") from None
    deadline = time.time() + 20
    while time.time() < deadline and CODEX_LOGIN.get("state") == "waiting":
        time.sleep(0.25)
    return codex_login_public()


@route("POST", "/api/codex/login/cancel", admin=True)
def codex_login_cancel(req):
    with CODEX_LOGIN_LOCK:
        client = CODEX_LOGIN.pop("client", None)
        if client:
            try:
                client.call("account/login/cancel", {"loginId": CODEX_LOGIN.get("login_id")}, timeout=10)
            except CodexError:
                pass
            client.close()
        CODEX_LOGIN.clear()
        CODEX_LOGIN["state"] = "idle"
    return codex_login_public()


@route("GET", "/api/codex/login", admin=True)
def codex_login_state(req):
    return codex_login_public()


def codex_allowed(user, ws):
    """Why this person or folder cannot use Codex, or None."""
    if not APP.cfg.get("codex"):
        return "Codex is not turned on in this Studio"
    if user["role"] != "admin":
        return "Codex runs with the ChatGPT login of this machine's owner, so only admins can use it"
    if ws.get("helper"):
        return ("Codex runs on Studio's server, so it cannot work in a folder on your computer; "
                "open a folder in your space on the server (Sandbox off) for Codex")
    if ws.get("owner"):
        return ("folders in a person's own space are always sandboxed, and Codex works only with the Sandbox off; "
                "make a folder under Projects instead (Open folder, then Projects, then New folder)")
    if sandboxed(ws):
        return "Codex runs on this machine in its own sandbox, so it works only in folders with the Sandbox off"
    return None


def codex_shell(command):
    """The command Codex runs, without its `bash -lc "..."` wrapper."""
    try:
        argv = shlex.split(command or "")
    except ValueError:
        return command or ""
    if len(argv) == 3 and os.path.basename(argv[0]) in ("bash", "sh", "zsh") and argv[1] in ("-lc", "-c"):
        return argv[2]
    return command or ""


def apply_unified(old, diff):
    """old with a unified diff's hunks applied (Codex's patches, for the approval card), or None when it does not fit."""
    lines, out, at = (old or "").splitlines(keepends=True), [], 0
    hunk = None
    for raw in diff.splitlines(keepends=True):
        head = re.match(r"@@ -(\d+)(?:,\d+)? \+\d+(?:,\d+)? @@", raw)
        if head:
            start = max(int(head.group(1)) - 1, 0) if int(head.group(1)) else 0
            if start < at:
                return None
            out += lines[at:start]
            at, hunk = start, True
            continue
        if not hunk or raw.startswith("\\"):
            continue
        tag, text = raw[:1], raw[1:]
        if tag == " " or tag == "-":
            if at >= len(lines) or lines[at].rstrip("\r\n") != text.rstrip("\r\n"):
                return None
            if tag == " ":
                out.append(lines[at])
            at += 1
        elif tag == "+":
            out.append(text if text.endswith("\n") else text + "\n")
    return "".join(out + lines[at:])


def codex_old_text(path):
    """A file's text before Codex changes it, or None when it is not there (or not text, or too large to keep)."""
    try:
        if os.path.getsize(path) > MAX_TEXT_BYTES:
            return None
        with open(path, "rb") as fh:
            return fh.read().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def codex_changes(run, item):
    """[(rel, abs, kind, old, new)] for a fileChange item; new is what the file will hold, as far as the diff says."""
    out = []
    for change in item.get("changes") or []:
        full = os.path.realpath(change.get("path") or "")
        if not within(full, run.ws["path"]):
            continue
        rel = os.path.relpath(full, run.ws["path"])
        kind = (change.get("kind") or {}).get("type")
        diff = change.get("diff") or ""
        old = codex_old_text(full) if os.path.exists(full) else None
        if kind == "add":
            new = diff
        elif kind == "delete":
            new = None
        else:
            new = apply_unified(old, diff)
            if new is None:
                new = old or ""
        out.append((rel, full, kind, old, new))
    return out


def codex_transcript(messages):
    """What was said in this chat before Codex joined it, for a new Codex conversation."""
    parts = []
    for m in messages:
        if m.get("role") not in ("user", "assistant") or not isinstance(m.get("content"), str) or not m["content"].strip():
            continue
        text = m["content"].split(USER_EXTRAS_END, 1)[-1] if m["role"] == "user" else m["content"]
        if text.startswith("Studio note:"):
            continue
        parts.append(("User: " if m["role"] == "user" else "Assistant: ") + text.strip())
    text = "\n\n".join(parts)
    return text[-CODEX_EARLIER:]


def codex_input(run, entries):
    items = []
    for entry in entries:
        items.append({"type": "text", "text": entry["content"] if isinstance(entry.get("content"), str) else "", "text_elements": []})
        for image in entry.get("images") or []:
            path = os.path.join(attachments_dir(run.session["id"]), os.path.basename(image))
            if os.path.isfile(path):
                items.append({"type": "localImage", "path": path})
    return items


def codex_images_dir():
    return os.path.realpath(os.path.join(os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex"), "generated_images"))


def codex_picture(run, item):
    """A picture Codex made with its own image tool (imagegen): Codex saves it in its own folder and sends only the path,
    so Studio keeps a copy beside the chat and shows it as a picture card, like a tool server's picture. Only files in
    Codex's generated_images folder are read. Returns the kept ids."""
    path = item.get("savedPath")
    if not isinstance(path, str) or not path:
        return []
    real, root = os.path.realpath(path), codex_images_dir()
    mime = next((m for m, ext in IMAGE_TYPES.items() if real.lower().endswith("." + ext) or (ext == "jpg" and real.lower().endswith(".jpeg"))), None)
    if not (real.startswith(root + os.sep) and mime and os.path.isfile(real)) or os.path.getsize(real) > MAX_IMAGE_BYTES:
        return []
    try:
        data = open(real, "rb").read()
    except OSError:
        return []
    stored = store_images(run.session["id"], [{"kind": "image", "name": "codex-" + os.path.basename(real), "mime": mime, "data": data}])
    run.emit("tool_picture", server="codex", tool="imagegen", images=stored, title="Picture made by Codex",
             prompt=(item.get("revisedPrompt") or "")[:600])
    return [p["id"] for p in stored]


def codex_unsent(run):
    """The user's messages Codex has not seen yet, and marks them sent."""
    history = run.session["messages"]
    start = run.session.get("codex_sent", 0)
    entries = [m for m in history[start:] if m.get("role") == "user"]
    run.session["codex_sent"] = len(history)
    return entries


CODEX_BRIDGE = os.path.join(os.path.dirname(os.path.realpath(__file__)), "tools", "studio_mcp_bridge.py")
CODEX_BRIDGES = {}               # one-time token -> the Codex run whose `studio` tool server it is
CODEX_TOOLS_NOTE = ("\n\nYou also have Eidoloom Studio's tools, in your `studio` tool server: use them instead of improvising with "
                    "shell commands. Pictures, video clips and music: comfyui (leave `model` out unless the person names one; "
                    "save results into the project, never placeholders). Voices: voices (only voices from the cast; never imitate "
                    "a real person). Sound effects: sounds. Sound files: audio. Video from a timeline: video. 3D: blender (check "
                    "with a viewport screenshot). Web: web-search to search, fetch to read a page, browser for pages that need "
                    "clicking, context7 for library documentation. A server this project runs here (start it as a command that keeps running, "
                    "such as `python3 -m http.server 8765`: your commands end their background `&` processes): view_page to check it yourself (width 390 "
                    "for a phone), open_preview to show it to the person; the browser server refuses local addresses. Greek text: greek-check every Greek text you write for people. "
                    "Email: listmonk prepares drafts; listmonk-send sends real email, so use it only when the person asked for "
                    "exactly that, after saying how many people it reaches. Pictures a tool returns come back to you to look at; "
                    "check your results before you finish. Each call shows in Studio's chat, and some ask the person first; a "
                    "skipped call means they said no.")


def codex_bridge_name(full):
    """The bridge's name for a Studio tool: mcp__comfyui__generate_image -> comfyui__generate_image (Codex adds its own prefix)."""
    return full[len("mcp__"):] if full.startswith("mcp__") else full


def codex_bridge_tools(run):
    """Studio's tools as the bridge lists them: name -> (Studio's name, MCP tool description)."""
    out = {}
    for full, entry in (run.mcp_tools or {}).items():
        params = dict(entry["schema"]["function"]["parameters"])
        params["properties"] = {k: v for k, v in (params.get("properties") or {}).items() if k != "progress"}
        if "required" in params:
            params["required"] = [r for r in params["required"] if r != "progress"]
        out[codex_bridge_name(full)] = (full, {"name": codex_bridge_name(full), "description": entry["schema"]["function"]["description"],
                                               "inputSchema": params})
    # Studio's own tools Codex lacks: save_picture for tool pictures, and view_page / open_preview for a server the
    # project runs here, which the browser server refuses as a local address (Codex's showcase run, 2026-11-18).
    for name in ("save_picture", "view_page", "open_preview"):
        if (name != "save_picture" or run.mcp_tools) and tool_enabled(name):
            tool = next((t for t in TOOLS if t["name"] == name), None)
            if tool:
                params = {"type": "object", "properties": {k: v for k, v in tool["properties"].items() if k != "progress"},
                          "required": [r for r in tool.get("required", []) if r != "progress"]}
                out[name] = (name, {"name": name, "description": tool["description"], "inputSchema": params})
    return out


CODEX_CARD_SERVERS = {"comfyui", "voices", "sounds", "blender"}     # tool servers that use the graphics card


def codex_free_card(run):
    """Codex thinks on OpenAI's servers, so while it works Strata only holds the card. Before its first call to a tool
    that uses the card, an idle Strata is unloaded (it loads again on its next request, about half a minute), unless
    another chat is using a Studio model. Config codex_free_card."""
    if getattr(run, "card_freed", False) or not APP.cfg.get("codex_free_card", True):
        return
    run.card_freed = True
    if any(not r.finished and r is not run and not is_codex(r.model) for r in list(APP.runs.values())):
        return
    pool = card_pool()
    if not pool or not pool["running"] or pool["busy"]:
        return
    run.emit("status", text="Unloading Strata, so the tools have the whole graphics card while Codex works…")
    try:
        gateway_json("POST", "/api/strata/stop", {}, timeout=120)
        run.emit("status", text="Strata is unloaded; the tools have the whole graphics card.")
    except (OSError, ValueError, http.client.HTTPException) as exc:
        run.emit("status", text=f"Strata could not be unloaded ({exc}); the tools make room themselves.")


def codex_bridge_run(req):
    """The Codex run a bridge request belongs to. Only from this machine, never through the proxy, and with its token."""
    run = CODEX_BRIDGES.get(req.headers.get("X-Studio-Bridge") or "")
    if run is None or req.client_address[0] not in ("127.0.0.1", "::1") or req.headers.get("X-Forwarded-For") or run.finished:
        raise ApiError(403, "not a running Codex chat")
    return run


@route("GET", "/api/codex/bridge/tools", public=True)
def codex_bridge_list(req):
    return {"tools": [spec for _, spec in codex_bridge_tools(codex_bridge_run(req)).values()]}


@route("POST", "/api/codex/bridge/call", public=True)
def codex_bridge_call(req):
    """One tool call from Codex, run as for Studio's own models: its row and approval card in the chat, then the result,
    with the pictures it returned so Codex can look at them."""
    run = codex_bridge_run(req)
    name, args = req.field("name"), req.field("arguments")
    tools = codex_bridge_tools(run)
    if not isinstance(name, str) or name not in tools:
        return {"isError": True, "content": [{"type": "text", "text": f"Unknown tool {name}. Studio's tools: {', '.join(tools)}"}]}
    if not isinstance(args, dict):
        args = {}
    full = tools[name][0]
    iid = "studio-" + secrets.token_hex(5)
    run.worked = True
    run.emit("tool_call", id=iid, name=full, args=clip_args(args))
    run.current_tool = iid
    if run.stop:
        ok, summary, out = False, "Skipped", "The run was stopped."
    else:
        if (run.mcp_tools.get(full) or {}).get("server") in CODEX_CARD_SERVERS:
            codex_free_card(run)
        ok, summary, out = execute_tool(run, full, args)
    run.emit("tool_result", id=iid, name=full, ok=ok, summary=summary, output=out[:4000])
    if ok and full == "save_picture":
        run.changed = True
    content = [{"type": "text", "text": out}]
    with run.cond:
        pictures, run.pending_pictures = run.pending_pictures, []
    budget = 12 * 1024 * 1024
    for _, ids in pictures:
        for pid in ids:
            path = os.path.join(attachments_dir(run.session["id"]), os.path.basename(pid))
            try:
                data = open(path, "rb").read()
            except OSError:
                continue
            if len(data) > budget:
                break
            budget -= len(data)
            kind = mimetypes.guess_type(path)[0] or "image/png"
            content.append({"type": "image", "data": base64.b64encode(data).decode(), "mimeType": kind})
    return {"isError": not ok, "content": content}


def codex_main(run):
    """The run for a Codex model: the same start and end as agent_main (files card, test check), with codex_loop between."""
    reason, summary = "error", None
    try:
        start_run_branch(run)
        if APP.cfg["mcp"]:
            run.emit("status", text="Connecting to tool servers…")
            run.mcp_tools = APP.mcp.tools_for(run.owner)
            run.vision = True                  # Codex's models see pictures: a tool's pictures go back to it with the result
        before = folder_snapshot(run.ws["path"])
        reason, summary = codex_loop(run)
        files = run_files(run, before)
        if files:
            run.emit("files", files=files[:SWEEP_SHOWN], total=len(files))
        check_finished_run(run, reason)
    except Exception as exc:
        traceback.print_exc()
        run.emit("error", text=f"Internal error: {exc}")
    finally:
        with run.cond:
            run.closing = True
        if take_guidance(run, run.session["messages"]):
            save_session(run.session)
        try:
            finish_run_branch(run)
        except Exception as exc:
            run.emit("error", text=f"Could not save the run's branch: {exc}")
        run.close(reason, summary)


def codex_loop(run):
    history = run.session["messages"]
    model = run.model[len(CODEX_PREFIX):]
    run.emit("status", text="Starting Codex…")
    try:
        client = CodexClient(run.ws["path"])
    except (CodexError, OSError) as exc:
        run.emit("error", text=f"Could not start Codex: {exc}")
        return "error", None
    run.codex = client
    try:
        client.start()
        params = {"cwd": run.ws["path"], "model": model, "approvalPolicy": "untrusted",
                  "sandbox": "read-only" if run.plan_mode else "workspace-write"}
        if run.mcp_tools:
            # Studio's tools reach Codex through one tool server of its own, the bridge, which hands each call back to this run.
            run.bridge_token = secrets.token_urlsafe(24)
            CODEX_BRIDGES[run.bridge_token] = run
            longest = max([(APP.mcp.spec(e["server"]) or {}).get("timeout_s") or APP.cfg["mcp_call_timeout_s"] for e in run.mcp_tools.values()]
                          + [APP.cfg["mcp_call_timeout_s"]])
            params["config"] = {"mcp_servers": {"studio": {
                "command": sys.executable, "args": [CODEX_BRIDGE],
                "env": {"STUDIO_BRIDGE_URL": f"http://127.0.0.1:{APP.cfg['port']}", "STUDIO_BRIDGE_TOKEN": run.bridge_token,
                        "STUDIO_BRIDGE_TIMEOUT": str(longest + 120)},
                "startup_timeout_sec": 30, "tool_timeout_sec": longest + 120}}}
        thread = None
        tid = run.session.get("codex_thread")
        if tid:
            try:
                thread = client.call("thread/resume", {"threadId": tid, **params}, timeout=60)
            except CodexError:
                run.emit("status", text="Codex could not reopen its earlier conversation, so it starts a new one with this chat so far.")
                run.session.pop("codex_sent", None)
        if thread is None:
            # A new Codex conversation: the messages since the chat's last reply are what to do now; anything before is
            # the conversation so far (with another model, or one Codex can no longer reopen), given as background.
            first = len(history)
            while first > 0 and history[first - 1].get("role") == "user":
                first -= 1
            pending = list(range(first, len(history)))
            earlier = codex_transcript(history[:first])
            note = CODEX_NOTE + (CODEX_TOOLS_NOTE + "\n\n" + (build_mcp_prompt(run) or "") if run.mcp_tools else "")
            note += ("\n\nThe conversation so far in this chat, before you joined it:\n\n" + earlier) if earlier else ""
            thread = client.call("thread/start", {**params, "developerInstructions": note, "serviceName": "strata_studio"}, timeout=60)
            run.session["codex_thread"] = thread["thread"]["id"]
            if pending:
                run.session["codex_sent"] = pending[0]
        tid = run.session["codex_thread"]
        run.emit("status", text="Codex is working…")
        reason = "answered"
        while True:
            take_guidance(run, history)
            entries = codex_unsent(run)
            if not entries:
                break
            save_session(run.session)
            try:
                start = {"threadId": tid, "input": codex_input(run, entries), "model": model}
                offered = next((m.get("efforts") or [] for m in CODEX_CACHE["models"] if m["name"] == run.model), [])
                if run.session.get("effort") in [e["value"] for e in offered]:      # the chat's own choice, when this model has it
                    start["effort"] = run.session["effort"]
                turn = client.call("turn/start", start, timeout=60)
            except CodexError as exc:
                run.emit("error", text=f"Codex did not start: {exc}")
                return "error", None
            run.turns += 1
            status, final, error = codex_turn(run, client, tid, (turn.get("turn") or {}).get("id"))
            if final:
                history.append({"role": "assistant", "content": final})
            save_session(run.session)
            why = getattr(run, "allowance_stop", "")
            if why and not run.stop:
                goal = run.session.get("goal")
                if isinstance(goal, dict) and goal.get("state") == "active":
                    goal.update(state="paused", why=why)
                    run.emit("goal", **goal_public(goal))
                save_session(run.session)
                return "answered", None
            if status == "interrupted" or run.stop:
                return "stopped", None
            if status == "failed":
                run.emit("error", text="Codex stopped with an error: " + (error or "no reason given"))
                return "error", None
            if status == "exited":
                run.emit("error", text="Codex stopped unexpectedly. The chat and files are kept; send a message to continue." + (f" ({error})" if error else ""))
                return "error", None
            if not final.strip():
                run.silent = True
            if not run.inbox:
                if goal_next(run, "answered", None):      # a goal: back to work with a reminder, as for Studio's models
                    run.emit("status", text="The goal is not reached yet, so the agent carries on…")
                    continue
                break
        return reason, None
    except CodexError as exc:
        run.emit("error", text=f"Codex: {exc}")
        return "error", None
    finally:
        run.codex = None
        CODEX_BRIDGES.pop(getattr(run, "bridge_token", None), None)
        client.close()


CODEX_ALLOWANCE_NOTE = ("Studio note: this goal's Codex allowance is used up ({why}). Stop at the next good point: "
                        "finish or undo the change you are making so nothing is left half done, then end your turn with a short "
                        "summary of what is done and what is left. Do not start anything new.")
CODEX_ALLOWANCE_CALLS = 1      # calls Codex may still start after the note; the one after that stops the turn
CODEX_ALLOWANCE_S = 120        # or this long after the note


def codex_turn(run, client, tid, turn_id):
    """Follows one Codex turn until it ends: (status, final reply, error text). A goal whose Codex allowance runs out
    during the turn (one turn may hold a whole goal) gets CODEX_ALLOWANCE_NOTE by turn/steer, and the turn is stopped
    at the second call after it or after CODEX_ALLOWANCE_S; `run.allowance_stop` then says why."""
    items, olds, final, last = {}, {}, "", None
    asked_stop = 0.0
    spent = {"at": 0.0, "calls": 0, "why": ""}

    def allowance_check():
        goal = run.session.get("goal")
        if spent["at"] or not turn_id or run.stop or not isinstance(goal, dict) or goal.get("state") != "active":
            return
        why = goal_codex_spent(goal)
        if not why:
            return
        spent.update(at=time.time(), why=why)
        try:
            client.call("turn/steer", {"threadId": tid, "expectedTurnId": turn_id,
                                       "input": [{"type": "text", "text": CODEX_ALLOWANCE_NOTE.format(why=why), "text_elements": []}]}, timeout=20)
            run.emit("status", text=why + " Codex is asked to stop at a good point.")
        except CodexError:
            pass                           # the turn was ending anyway: the goal pauses after it

    def allowance_stop(reason):
        run.allowance_stop = spent["why"]
        run.emit("status", text=f"Stopping Codex: {reason}.")
        try:
            client.call("turn/interrupt", {"threadId": tid, "turnId": turn_id}, timeout=10)
        except CodexError:
            pass
    error = ""
    plan, step = None, None

    def call(iid, name, args):
        """A tool row; while Codex has a plan, its current step moves the plan card (`progress`, as Studio's models send)."""
        if step and plan:
            args = {**args, "progress": f"{step}/{len(plan)}"}
            run.note_progress(args["progress"])
        run.emit("tool_call", id=iid, name=name, args=args)

    while True:
        if run.stop and not asked_stop:
            asked_stop = time.time()
            run.emit("status", text="Stopping Codex…")
            try:
                client.call("turn/interrupt", {"threadId": tid, "turnId": turn_id}, timeout=10)
            except CodexError:
                pass
        if asked_stop and time.time() - asked_stop > 15:
            return "interrupted", final, ""
        if spent["at"] and not asked_stop and time.time() - spent["at"] > CODEX_ALLOWANCE_S:
            asked_stop = time.time()
            allowance_stop(f"it kept working {CODEX_ALLOWANCE_S} s after its allowance ran out")
        if run.inbox and turn_id and not run.stop:
            with run.cond:
                texts, run.inbox = run.inbox, []
            for text in texts:
                run.session["messages"].append({"role": "user", "content": GUIDE_NOTE + "\n" + USER_EXTRAS_END + text})
            run.session["codex_sent"] = len(run.session["messages"])
            try:
                client.call("turn/steer", {"threadId": tid, "expectedTurnId": turn_id,
                                           "input": [{"type": "text", "text": t, "text_elements": []} for t in texts]}, timeout=20)
                run.emit("status", text="Your message reached Codex.")
            except CodexError:             # the turn was ending: the messages start the next one
                run.session["codex_sent"] -= len(texts)
            save_session(run.session)
        try:
            msg = client.inbox.get(timeout=0.25)
        except queue.Empty:
            continue
        method, params = msg.get("method"), msg.get("params") or {}
        if method == "studio/exited":
            return "exited", final, error
        if "id" in msg:                    # Codex asks something: answered in a thread, so the turn's events keep flowing
            threading.Thread(target=codex_answer, args=(run, client, msg, items), daemon=True).start()
            continue
        item = params.get("item") or {}
        kind = item.get("type")
        # Codex's sub-agents work in threads of their own: their commands, edits and calls show like the main agent's
        # (with their undo points); their words, plans and token use stay out of the chat, which follows the main agent.
        main = params.get("threadId") in (None, tid) and params.get("turnId") in (None, turn_id)
        tool_item = method in ("item/started", "item/completed") and kind in ("commandExecution", "fileChange", "mcpToolCall",
                                                                              "webSearch", "dynamicToolCall", "imageGeneration",
                                                                              "collabAgentToolCall")
        if not main and not tool_item and method != "turn/completed":
            continue
        if method == "item/agentMessage/delta":
            text = params.get("delta") or ""
            if last == "message" and params.get("itemId") != items.get("_message"):
                text = "\n\n" + text
            items["_message"] = params.get("itemId")
            last = "message"
            run.emit("token", text=text)
        elif method in ("item/reasoning/summaryTextDelta", "item/reasoning/textDelta"):
            run.emit("thinking", text=params.get("delta") or "")
        elif method == "item/started":
            items[item.get("id")] = item
            if spent["at"] and not asked_stop and main and kind in ("commandExecution", "fileChange", "mcpToolCall", "webSearch", "imageGeneration"):
                spent["calls"] += 1
                if spent["calls"] > CODEX_ALLOWANCE_CALLS:
                    asked_stop = time.time()
                    allowance_stop("its allowance ran out and it started another call")
            if kind == "commandExecution":
                last = "tool"
                run.worked = True
                call(item["id"], "run", {"command": codex_shell(item.get("command"))})
            elif kind == "fileChange":
                last = "tool"
                run.worked = True
                olds[item["id"]] = codex_changes(run, item)
                call(item["id"], "apply_patch", {"files": [c[0] for c in olds[item["id"]]]})
            elif kind == "mcpToolCall" and item.get("server") == "studio":
                last = "tool"                 # Studio shows the call itself, from the bridge
            elif kind == "collabAgentToolCall":
                last = "tool"
                run.worked = True
                call(item["id"], "codex_agent", {"action": item.get("tool"), **({"task": item["prompt"][:400]} if item.get("prompt") else {}),
                                                 **({"model": item["model"]} if item.get("model") else {})})
            elif kind in ("mcpToolCall", "webSearch", "dynamicToolCall", "imageGeneration"):
                last = "tool"
                run.worked = True
                name = f"codex: {item.get('server')} · {item.get('tool')}" if kind == "mcpToolCall" else f"codex: {kind}"
                call(item["id"], name, {k: v for k, v in item.items() if k in ("query", "arguments", "prompt")})
        elif method == "item/completed":
            if kind == "agentMessage" and main:
                final = item.get("text") or final
            elif kind == "commandExecution":
                code = item.get("exitCode")
                ok = item.get("status") == "completed" and code in (0, None)
                summary = "Skipped" if item.get("status") == "declined" else f"exit {code}" if code is not None else item.get("status") or "done"
                run.emit("tool_result", id=item["id"], name="run", ok=ok, summary=summary, output=(item.get("aggregatedOutput") or "")[-12000:])
                if not looks_only(codex_shell(item.get("command"))):
                    run.changed = True
            elif kind == "fileChange":
                done = item.get("status") == "completed"
                changes = olds.pop(item["id"], None) or codex_changes(run, item)
                for rel, full, ckind, old, _ in changes if done else []:
                    new = codex_old_text(full) if os.path.exists(full) else None
                    cid = make_checkpoint(run.ws, {rel: old})
                    run.changed = True
                    action = "delete" if ckind == "delete" else "create" if old is None else "edit"
                    run.emit("file_changed", path=rel, checkpoint=cid, action=action, change=describe_change(old or "", new or ""))
                run.emit("tool_result", id=item["id"], name="apply_patch", ok=done, summary=f"{len(changes)} file{'s' if len(changes) != 1 else ''}"
                         if done else "Not applied", output="\n".join(c[0] for c in changes))
            elif kind == "mcpToolCall" and item.get("server") == "studio":
                pass
            elif kind == "collabAgentToolCall":
                states = item.get("agentsStates") or {}
                said = "\n".join(f"{(st or {}).get('status')}: {(st or {}).get('message') or ''}".strip() for st in states.values())
                run.emit("tool_result", id=item["id"], name="codex_agent", ok=item.get("status") == "completed",
                         summary=f"{item.get('tool')} · {item.get('status')}", output=said[:4000])
            elif kind == "imageGeneration":
                kept = codex_picture(run, item)
                failure = item.get("failure") or {}
                run.emit("tool_result", id=item["id"], name="codex: imageGeneration", ok=item.get("status") not in ("failed", "declined") and not failure,
                         summary=("picture made" + (" and shown in the chat" if kept else "")) if not failure else "failed",
                         output=((item.get("revisedPrompt") or "") + (f"\nSaved by Codex as {item['savedPath']}" if item.get("savedPath") else "")
                                 + (f"\n{json.dumps(failure)}" if failure else ""))[:4000])
            elif kind in ("mcpToolCall", "webSearch", "dynamicToolCall"):
                name = f"codex: {item.get('server')} · {item.get('tool')}" if kind == "mcpToolCall" else f"codex: {kind}"
                run.emit("tool_result", id=item["id"], name=name, ok=item.get("status") not in ("failed", "declined"),
                         summary=item.get("status") or "done", output=json.dumps(item.get("result") or item.get("query") or "")[:4000])
        elif method == "turn/plan/updated":
            entries = [s for s in params.get("plan") or [] if s.get("step")][:MAX_PLAN_STEPS]
            steps = [s["step"][:MAX_PLAN_STEP] for s in entries]
            if steps and steps != plan:
                run.emit("plan", steps=steps, revised=plan is not None)
                run.plan, plan = steps, steps
            if steps:
                states = [s.get("status") for s in entries]
                step = next((i + 1 for i, st in enumerate(states) if st == "inProgress"),
                            min(sum(st == "completed" for st in states) + 1, len(steps)))
        elif method == "thread/tokenUsage/updated":
            usage = params.get("tokenUsage") or {}
            last_use = usage.get("last") or {}
            run.emit("usage", prompt_tokens=last_use.get("inputTokens"), completion_tokens=last_use.get("outputTokens"), turn=run.turns,
                     max_turns=APP.cfg["max_turns"], context_tokens=usage.get("modelContextWindow"))
            goal = run.session.get("goal")
            if isinstance(goal, dict) and goal.get("state") == "active" and goal.get("codex_tokens"):
                new_tokens = max(0, int(last_use.get("inputTokens") or 0) - int(last_use.get("cachedInputTokens") or 0)) + int(last_use.get("outputTokens") or 0)
                goal["codex_used"] = goal.get("codex_used", 0) + new_tokens
            allowance_check()
        elif method == "mcpServer/startupStatus/updated" and params.get("name") == "studio" and params.get("status") not in ("starting", "ready"):
            why = params.get("error") or params.get("failureReason") or params.get("status")
            run.emit("error", text=f"Studio's tools did not start inside Codex ({str(why)[:300]}), so Codex works without them this time.")
        elif method == "account/rateLimits/updated":
            with APP.lock:
                CODEX_USAGE.update(t=time.time(), data=codex_usage_shape(params, CODEX_USAGE["data"]))
                usage = CODEX_USAGE["data"]
            run.emit("codex_usage", usage=usage)
            allowance_check()
        elif method == "error" and not params.get("willRetry"):
            error = ((params.get("error") or {}).get("message") or "")[:1000]
        elif method == "turn/completed" and (params.get("turn") or {}).get("id") in (turn_id, None) and params.get("threadId") in (None, tid):
            turn = params.get("turn") or {}
            for it in turn.get("items") or []:
                if it.get("type") == "agentMessage" and it.get("text"):
                    final = it["text"]
            err = (turn.get("error") or {}).get("message") or error
            return turn.get("status") or "completed", final, err


def codex_answer(run, client, msg, items):
    """Answers a request from Codex: approvals through Studio's cards and auto modes, questions through the question card."""
    rid, method, params = msg["id"], msg.get("method"), msg.get("params") or {}
    try:
        with open(os.path.join(APP.data_dir, "codex.log"), "a") as fh:      # what Codex asked, for when an answer goes wrong
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} request {method} {json.dumps(params)[:1500]}\n")
    except OSError:
        pass
    try:
        if method == "item/commandExecution/requestApproval":
            command = codex_shell(params.get("command") or (items.get(params.get("itemId")) or {}).get("command") or "")
            # "decline" lets Codex carry on without the command (checked with Codex 0.156 on 2026-11-17), although its
            # availableDecisions lists only accept and cancel, and cancel ends the whole turn.
            refuse = "decline"
            if run.stop:
                return client.reply(rid, {"decision": refuse})
            if (run.auto_commands and not run.plan_mode) or looks_only(command):
                return client.reply(rid, {"decision": "accept"})
            extra = {"purpose": "codex_network", "reason": params["reason"]} if params.get("reason") else {}
            decision, note = run.ask(kind="command", command=command, plan=run.plan_mode, **extra)
            client.reply(rid, {"decision": "accept" if decision == "accept" else refuse})
            if decision != "accept" and note:
                with run.cond:
                    run.inbox.append(f"(About the command you were not allowed to run: {note})")
        elif method == "item/fileChange/requestApproval":
            item = items.get(params.get("itemId")) or {}
            if run.plan_mode or run.stop:
                return client.reply(rid, {"decision": "decline"})
            if run.auto_apply:
                return client.reply(rid, {"decision": "accept"})
            changes = codex_changes(run, item)
            if len(changes) == 1:
                rel, _, ckind, old, new = changes[0]
                fields = {"path": rel, "old": old or "", "new": new or "", "change": describe_change(old or "", new or "")}
                if ckind == "delete":
                    fields["action"] = "delete"
            else:
                old = "".join(f"===== {c[0]} =====\n{c[3] or ''}\n" for c in changes)
                new = "".join(f"===== {c[0]} =====\n{c[4] or ''}\n" for c in changes)
                fields = {"path": f"{len(changes)} files: " + ", ".join(c[0] for c in changes)[:200], "old": old, "new": new,
                          "change": describe_change(old, new)}
            decision, note = run.ask(kind="edit", **fields)
            client.reply(rid, {"decision": "accept" if decision == "accept" else "decline"})
            if decision != "accept" and note:
                with run.cond:
                    run.inbox.append(f"(About the change you were not allowed to make: {note})")
        elif method == "item/tool/requestUserInput":
            questions = [{"question": (q.get("question") or "")[:400], "header": (q.get("header") or "")[:40],
                          "options": [{"label": (o.get("label") or "")[:80], "description": (o.get("description") or "")[:200]}
                                      for o in q.get("options") or [] if o.get("label")], "multi": False}
                         for q in params.get("questions") or []]
            decision, _ = run.ask(kind="question", questions=questions)
            answers = run.last_answers or []
            client.reply(rid, {"answers": {q.get("id"): {"answers": (a.get("choices") or []) + ([a["text"]] if a.get("text") else [])}
                                           for q, a in zip(params.get("questions") or [], answers) if decision == "accept"}})
        elif method == "mcpServer/elicitation/request":
            # Codex asks before every tool call ("Allow the studio MCP server to run tool …?", _meta codex_approval_kind
            # mcp_tool_call). For Studio's own bridge the answer is yes: the call comes back to Studio, which asks the person
            # with its own card as each tool server is set to. Anything else is declined.
            ours = params.get("serverName") == "studio" and (params.get("_meta") or {}).get("codex_approval_kind") == "mcp_tool_call"
            client.reply(rid, {"action": "accept", "content": {}, "_meta": None} if ours and not run.stop
                         else {"action": "decline", "content": None, "_meta": None})
        else:
            client.refuse(rid, f"Eidoloom Studio does not handle {method}")
    except CodexError:
        pass



# ---------------------------------------------------------------------------
# Agent endpoints
# ---------------------------------------------------------------------------

def get_run(req, rid):
    run = APP.runs.get(rid) if isinstance(rid, str) else None
    if not run or run.owner != req.current_user["username"]:
        raise ApiError(404, "unknown run")
    return run


@route("POST", "/api/agent/send")
def agent_send(req):
    ws = req.workspace()
    message = req.field("message")
    if not isinstance(message, str) or not message.strip():
        raise ApiError(400, "message required")
    session = load_session(req, req.field("session"))
    if session.get("ws") != ws["id"]:
        raise ApiError(400, "session belongs to another workspace")
    model = req.field("model") or APP.cfg["default_model"]
    if not isinstance(model, str):
        raise ApiError(400, "model must be a string")
    known = {m["name"]: m for m in get_models()[0]}
    if is_codex(model):
        why = codex_allowed(req.current_user, ws)
        if why:
            raise ApiError(403 if "admins" in why else 400, why)
        known.update({m["name"]: m for m in codex_models()})
        if model not in known:
            raise ApiError(400, f"Codex does not offer {model[len(CODEX_PREFIX):]}" + (f" ({CODEX_CACHE['error']})" if CODEX_CACHE["error"] else ""))
    tools_enabled = known.get(model, {}).get("tools") is not False
    attachments = check_attachments(req.field("attachments"), admin=req.current_user["role"] == "admin")
    for a in attachments:          # files from the folder: checked before the run starts
        if a["kind"] == "file" and not os.path.isfile(resolve(ws, a["path"])):
            raise ApiError(400, f"{a['path']} is not in the folder")
    if any(a["kind"] == "image" for a in attachments) and known.get(model, {}).get("vision") is False:
        raise ApiError(400, f"{model} cannot see pictures; pick a model that can, or remove the picture")
    if session_busy(session["id"]):
        raise ApiError(409, "a run is already active in this chat")
    APP.room.claim(req.current_user)
    run = Run(ws, session, model, tools_enabled, bool(req.field("auto_apply")),
              bool(req.field("auto_commands")), req.field("context"))
    run.owner = req.current_user["username"]
    run.vision = known.get(model, {}).get("vision") is True
    run.plan_mode = req.field("plan_mode") is True
    run.use_branch = req.field("git_branch") is True
    with APP.lock:
        if ws["id"] in APP.active:
            busy = APP.active[ws["id"]].owner
            raise ApiError(409, "a run is already active in this workspace" if busy == run.owner
                           else f"{busy}'s agent is working in this folder; wait for it to finish, then send this")
        APP.active[ws["id"]] = run
        note_active()
        APP.runs[run.id] = run
        finished = [rid for rid, r in APP.runs.items() if r.finished]
        for rid in finished[:max(0, len(finished) - 30)]:
            del APP.runs[rid]
    session["model"] = model
    session["title"] = session.get("title") or message.strip()[:60]
    saved_as = None
    if req.field("as_file") is True:            # too long to send: the agent reads it from a file, in parts
        saved_as = save_long_message(ws, message)
        shown = message[:400].rstrip() + "…"
        message = (f"My message is long, so Studio saved it as the file {saved_as} ({message.count(chr(10)) + 1} lines, "
                   f"{len(message)} characters). Read it in parts with read_file, then do what it asks.")
    save_big_attachments(ws, attachments)
    browser_note = browser_context(req.current_user["username"], message)
    extras = (browser_note + shell_notes_text(session) + mentioned_files_text(ws, req.field("mentions"), admin=req.current_user["role"] == "admin")
              + attached_files_text(attachments) + project_files_text(ws, attachments, admin=req.current_user["role"] == "admin"))
    if run.plan_mode:
        extras = plan_note() + extras
    if req.field("goal") is True:
        if len(message) > MAX_GOAL_CHARS:
            raise ApiError(400, f"a goal can have at most {MAX_GOAL_CHARS} characters")
        session["goal"] = {"text": message.strip(), "state": "active", "rounds": 0, "why": "", **goal_extras(req)}
        extras = goal_note(message.strip(), codex=is_codex(model)) + extras
    images = store_images(session["id"], attachments)
    event = {"type": "user", "text": shown if saved_as else message}
    if saved_as:
        event["saved_as"] = saved_as
    if run.plan_mode:
        event["plan_mode"] = True
    if req.field("goal") is True:
        event["goal"] = True
    if images:
        event["images"] = images
    files = [a["name"] for a in attachments if a["kind"] in ("text", "big")]
    if files:
        event["files"] = files
    saved = [path for a in attachments if a["kind"] == "big" for path in [a["saved"]] + [c[0] for c in a["csv"]]]
    saved += [a["path"] for a in attachments if a["kind"] == "file"]
    if saved:
        event["saved_files"] = saved
    event["mi"] = len(session["messages"])           # where this message starts in what the model reads, for rewind and branch
    session["events"].append(event)
    entry = {"role": "user", "content": extras + USER_EXTRAS_END + message if extras else message}
    if images:
        entry["images"] = [image["id"] for image in images]     # sent as pictures by call_model; the text stays a plain string
    session["messages"].append(entry)
    save_session(session)
    threading.Thread(target=agent_main, args=(run,), daemon=True).start()
    APP.room.changed()
    sync_publish(run.owner, req.sync_origin(), "run_started", ws=ws["id"], session=session["id"], run=run.id)
    return {"run": run.id, **({"saved_files": saved} if saved else {})}


@route("POST", "/api/agent/compact")
def agent_compact(req):
    """/compact: the model summarizes the chat for itself, and the summary replaces what it reads. Runs like a message."""
    ws = req.workspace()
    session = load_session(req, req.field("session"))
    if session.get("ws") != ws["id"]:
        raise ApiError(400, "session belongs to another workspace")
    model = req.field("model") or session.get("model") or APP.cfg["default_model"]
    if not isinstance(model, str):
        raise ApiError(400, "model must be a string")
    if is_codex(model):
        raise ApiError(400, "Codex keeps and compacts its own conversation, so /compact is not needed with it")
    APP.room.claim(req.current_user)
    run = Run(ws, session, model, False, False, False, None)
    run.owner, run.quiet = req.current_user["username"], True
    with APP.lock:
        if ws["id"] in APP.active:
            raise ApiError(409, "a run is already active in this workspace")
        APP.active[ws["id"]] = run
        note_active()
        APP.runs[run.id] = run
    threading.Thread(target=compact_main, args=(run,), daemon=True).start()
    APP.room.changed()
    sync_publish(run.owner, req.sync_origin(), "run_started", ws=ws["id"], session=session["id"], run=run.id)
    return {"run": run.id}


def save_long_message(ws, message):
    """A message too long for the model's memory, saved in the folder for the agent to read in parts. Returns its path."""
    rel = f".strata-studio/messages/message-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}.md"
    write_bytes(resolve(ws, rel), message.encode("utf-8"))
    return rel


@route("GET", "/api/agent/stream")
def agent_stream(req):
    run = get_run(req, req.arg("run"))
    sent = to_int(req.arg("after"), 0, lo=0)
    req.sse_start()
    while True:
        with run.cond:
            if sent >= len(run.events) and not run.finished:
                run.cond.wait(SSE_PING_S)
            batch = run.events[sent:]
            finished = run.finished
        if not batch:
            if finished:
                return
            req.sse_ping()
            continue
        for ev in batch:
            req.sse_event(ev["type"], ev)
            sent = ev["seq"]
        if batch[-1]["type"] == "done":
            return


@route("GET", "/api/agent/poll")
def agent_poll(req):
    run = get_run(req, req.arg("run"))
    sent = to_int(req.arg("after"), 0, lo=0)
    with run.cond:
        if sent >= len(run.events) and not run.finished:
            run.cond.wait(POLL_WAIT_S)
        batch = run.events[sent:]
        finished = run.finished
    return {"events": [{"type": ev["type"], "data": ev} for ev in batch], "cursor": {"after": batch[-1]["seq"] if batch else sent},
            "end": batch[-1]["type"] == "done" if batch else finished}


@route("GET", "/api/agent/active")
def agent_active(req):
    ws = req.workspace()
    run = APP.active.get(ws["id"])
    if run and run.owner != req.current_user["username"]:
        run = None
    return {"run": run.id if run else None, "session": run.session["id"] if run else None}


@route("POST", "/api/agent/decision")
def agent_decision(req):
    run = get_run(req, req.field("run"))
    decision = req.field("decision")
    if decision not in ("accept", "reject"):
        raise ApiError(400, "decision must be accept or reject")
    note = req.field("note")
    parts = req.field("parts")
    if parts is not None and not (isinstance(parts, list) and all(type(i) is int for i in parts)):
        raise ApiError(400, "parts must be a list of numbers")
    answers = None
    with run.cond:
        ap = run.approvals.get(req.field("approval_id"))
    if ap and ap.get("kind") == "question" and decision == "accept":
        answers = clean_answers(ap["questions"], req.field("answers"))
    if not run.decide(req.field("approval_id"), decision, note if isinstance(note, str) else "", parts, answers):
        raise ApiError(404, "unknown approval")
    APP.room.activity(run.owner)
    APP.room.changed()
    return {"ok": True}


MAX_GUIDE_CHARS = 20000


@route("POST", "/api/agent/guide")
def agent_guide(req):
    """A message for the running model, taken in at its next step; it also shows in the chat now."""
    run = get_run(req, req.field("run"))
    text = req.field("message")
    if not isinstance(text, str) or not text.strip():
        raise ApiError(400, "message required")
    if len(text) > MAX_GUIDE_CHARS:
        raise ApiError(413, f"a message can be at most {MAX_GUIDE_CHARS} characters")
    with run.cond:
        if run.closing or run.finished or run.stop:
            raise ApiError(409, "the run has ended; send it as a new message")
        run.inbox.append(text.strip())
    run.emit("user", text=text.strip(), guided=True)
    return {"ok": True}


@route("POST", "/api/agent/stop")
def agent_stop(req):
    get_run(req, req.field("run")).request_stop()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Passing a chat to another person (a copy of the chat and its folder)
# ---------------------------------------------------------------------------

HANDOFF_KEEP_S = 7 * 86400      # an offer nobody answers goes away after a week
HANDOFF_DONE_S = 86400          # answered ones stay a day, so the sender sees the answer


def handoffs_path():
    return os.path.join(APP.data_dir, "handoffs.json")


def handoffs_load():
    now = time.time()
    return [h for h in read_json(handoffs_path(), []) if now - h["at"] < (HANDOFF_KEEP_S if h["state"] == "offered" else HANDOFF_DONE_S)]


def handoffs_for(username):
    """Offers to this person, and this person's own offers with their answers."""
    try:
        items = handoffs_load()
    except Exception:
        return {"incoming": [], "outgoing": []}
    keep = ("id", "from", "from_name", "to", "to_name", "title", "folder", "note", "at", "state", "mb")
    return {"incoming": [{k: h.get(k) for k in keep} for h in items if h["to"] == username and h["state"] == "offered"],
            "outgoing": [{k: h.get(k) for k in keep} for h in items if h["from"] == username]}


def handoff_change(update):
    with APP.handoff_lock:
        items = handoffs_load()
        result = update(items)
        write_json_atomic(handoffs_path(), items)
    APP.room.changed()
    return result


def handoff_find(items, hid, **match):
    found = next((h for h in items if h["id"] == hid), None)
    if not found or any(found.get(k) != v for k, v in match.items()):
        raise ApiError(404, "that chat offer is gone")
    if found["state"] != "offered":
        raise ApiError(409, f"that offer was already {found['state']}")
    return found


@route("GET", "/api/people")
def people_list(req):
    """Every other account, for choosing who to message or pass a chat to."""
    me = req.current_user["username"]
    return {"people": sorted(({"username": name, "name": u.get("display_name") or name} for name, u in APP.auth.users.items() if name != me),
                             key=lambda p: p["name"].lower())}


@route("POST", "/api/handoff/offer")
def handoff_offer(req):
    ws = req.workspace()
    session = load_session(req, req.field("session"))
    if session.get("ws") != ws["id"]:
        raise ApiError(400, "session belongs to another workspace")
    if session_busy(session["id"]):
        raise ApiError(409, "Wait until the run ends.")
    me, to = req.current_user, req.field("to")
    target = APP.auth.users.get(to) if isinstance(to, str) else None
    if not target:
        raise ApiError(404, "unknown user")
    if to == me["username"]:
        raise ApiError(400, "you cannot pass a chat to yourself")
    note = req.field("note") or ""
    if not isinstance(note, str) or len(note) > 500:
        raise ApiError(400, "the note can have up to 500 characters")
    limit = APP.cfg.get("handoff_max_mb", DEFAULT_CONFIG["handoff_max_mb"]) * 1048576
    size = folder_bytes(ws["path"], limit)
    if size is None:
        raise ApiError(413, f"Passing a chat copies its folder, and this one is larger than {limit // 1048576} MB.")
    offer = {"id": secrets.token_hex(6), "from": me["username"], "from_name": me.get("display_name") or me["username"],
             "to": to, "to_name": target.get("display_name") or to, "session": session["id"], "ws": ws["id"],
             "title": session.get("title") or "Chat", "folder": ws["name"], "note": note.strip(), "at": time.time(),
             "state": "offered", "mb": round(size / 1048576, 1)}

    def add(items):
        if any(h["session"] == offer["session"] and h["to"] == to and h["state"] == "offered" for h in items):
            raise ApiError(409, f"this chat is already offered to {offer['to_name']}")
        items.append(offer)
    handoff_change(add)
    return {"ok": True, "offer": {k: v for k, v in offer.items() if k not in ("session", "ws")}}


@route("POST", "/api/handoff/cancel")
def handoff_cancel(req):
    def cancel(items):
        handoff_find(items, req.field("id"), **{"from": req.current_user["username"]})["state"] = "cancelled"
    handoff_change(cancel)
    return {"ok": True}


@route("POST", "/api/handoff/decline")
def handoff_decline(req):
    def decline(items):
        handoff_find(items, req.field("id"), to=req.current_user["username"])["state"] = "declined"
    handoff_change(decline)
    return {"ok": True}


def free_name(parent, name):
    candidate, n = os.path.join(parent, name), 2
    while os.path.exists(candidate):
        candidate, n = os.path.join(parent, f"{name}-{n}"), n + 1
    return candidate


@route("POST", "/api/handoff/accept")
def handoff_accept(req):
    """Copies the folder into the recipient's space and the chat into their account, with its pictures and undo points."""
    me = req.current_user
    with APP.handoff_lock:
        offer = handoff_find(handoffs_load(), req.field("id"), to=me["username"])
        source = read_json(session_path(offer["session"]), None)
        src_ws = APP.workspaces.get(offer["ws"])
        if not source or not src_ws or not os.path.isdir(src_ws["path"]):
            raise ApiError(410, "the chat or its folder no longer exists")
        if session_busy(source["id"]):
            raise ApiError(409, "the sender's agent is working in this chat; try again when it ends")
        limit = APP.cfg.get("handoff_max_mb", DEFAULT_CONFIG["handoff_max_mb"]) * 1048576
        if folder_bytes(src_ws["path"], limit) is None:
            raise ApiError(413, f"the folder has grown past {limit // 1048576} MB since it was offered")
        parent = os.path.join(APP.user_base(), me["username"])
        if me["role"] == "admin" and not APP.in_roots_for(parent, me):
            parent = APP.roots()[0]
        os.makedirs(parent, exist_ok=True)
        dest = free_name(parent, f"{src_ws['name']}-from-{offer['from']}")
        copied = subprocess.run(["cp", "-a", "--reflink=auto", src_ws["path"], dest], capture_output=True, text=True, timeout=900)
        if copied.returncode != 0:
            shutil.rmtree(dest, ignore_errors=True)
            raise ApiError(500, "could not copy the folder: " + (copied.stderr.strip()[:300] or "cp failed"))
        ws = APP.open_workspace(dest, me)
        with APP.lock:
            record = APP.workspaces[ws["id"]]
            record["passed_from"] = offer.get("from_name") or offer["from"]
            for key in ("sandbox", "sandbox_image", "sandbox_network", "sandbox_ports"):
                if key in src_ws:
                    record[key] = src_ws[key]
            write_json_atomic(APP.workspaces_path, APP.workspaces)
        old_points = os.path.join(APP.data_dir, "checkpoints", src_ws["id"])
        if os.path.isdir(old_points):
            shutil.copytree(old_points, os.path.join(APP.data_dir, "checkpoints", ws["id"]), dirs_exist_ok=True)
        sid = secrets.token_hex(6)
        copy = json.loads(json.dumps({k: v for k, v in source.items() if k not in ("system_prompt", "queued")}))
        copy.update(id=sid, ws=ws["id"], owner=me["username"], title=f"From {offer['from_name']}: {offer['title']}"[:60], handed_from=offer["from"])
        copy["events"].append({"type": "handoff", "from": offer["from_name"], "note": offer["note"], "folder": os.path.basename(dest), "at": time.time()})
        if os.path.isdir(attachments_dir(source["id"])):
            shutil.copytree(attachments_dir(source["id"]), attachments_dir(sid))
        save_session(copy)
        items = handoffs_load()
        for h in items:
            if h["id"] == offer["id"]:
                h["state"] = "accepted"
        write_json_atomic(handoffs_path(), items)
    APP.room.changed()
    sync_publish(me["username"], req.headers.get("X-Studio-Client"), "session_list", ws=ws["id"])
    return {"ok": True, "ws": APP.public_ws(APP.workspaces[ws["id"]]), "session": sid}


# ---------------------------------------------------------------------------
# Waiting room endpoints
# ---------------------------------------------------------------------------

@route("GET", "/api/queue/state")
def queue_state(req):
    APP.room.mark_seen(req.current_user["username"])
    return APP.room.state_for(req.current_user)


def queue_updates(user, last_message, last_sync, first=False):
    """What a window has not had yet from the waiting room, and where it has now got to. The first answer always carries
    the chat, even empty, so a window can tell the history it had from messages that arrive later."""
    events = [("state", APP.room.state_for(user))]
    messages = APP.room.messages_for(user["username"], last_message)
    if messages or first:
        events.append(("chat", {"messages": messages}))
        last_message = messages[-1]["id"] if messages else last_message
    changes, last_sync = sync_events(user["username"], last_sync)
    events += [("sync", change["event"]) for change in changes]
    return events, last_message, last_sync


@route("GET", "/api/queue/poll")
def queue_poll(req):
    """The stream below, one answer at a time. The first request has no `sync` and answers at once."""
    user, room = req.current_user, APP.room
    room.mark_polled(user["username"])
    last_sync = to_int(req.arg("sync"), 0, lo=0)
    if not req.arg("sync"):
        last_sync = sync_events(user["username"], 0)[1]
    else:
        with room.cond:
            if APP.sync_seq == last_sync:
                room.cond.wait(5)
    events, last_message, last_sync = queue_updates(user, to_int(req.arg("message"), 0, lo=0), last_sync, first=not req.arg("sync"))
    room.mark_polled(user["username"])
    return {"events": [{"type": name, "data": data} for name, data in events], "cursor": {"message": last_message, "sync": last_sync}}


@route("GET", "/api/queue/stream")
def queue_stream(req):
    """Live waiting room state. An open stream also counts as being present in line."""
    user, room = req.current_user, APP.room
    req.sse_start()
    room.connect(user["username"])
    last_message = 0
    last_sync = sync_events(user["username"], 0)[1]
    first = True
    try:
        while True:
            # Accounts change while a stream is open, so a signed-out or removed user must stop receiving.
            user = APP.auth.session_user(req.token())
            if not user:
                return
            events, last_message, last_sync = queue_updates(user, last_message, last_sync, first)
            first = False
            for name, data in events:
                req.sse_event(name, data)
            with room.cond:
                if APP.sync_seq == last_sync:
                    room.cond.wait(5)
    finally:
        room.disconnect(user["username"] if user else req.current_user["username"])


@route("POST", "/api/queue/join")
def queue_join(req):
    APP.room.join(req.current_user)
    return APP.room.state_for(req.current_user)


@route("POST", "/api/queue/leave")
def queue_leave(req):
    APP.room.leave(req.current_user["username"])
    return APP.room.state_for(req.current_user)


@route("POST", "/api/queue/accept")
def queue_accept(req):
    APP.room.accept(req.current_user)
    return APP.room.state_for(req.current_user)


@route("POST", "/api/queue/release")
def queue_release(req):
    APP.room.release(req.current_user["username"])
    return APP.room.state_for(req.current_user)


@route("POST", "/api/queue/keep")
def queue_keep(req):
    APP.room.keep(req.current_user["username"])
    return APP.room.state_for(req.current_user)


@route("GET", "/api/queue/chat")
def queue_chat(req):
    return {"messages": APP.room.messages_for(req.current_user["username"])}


@route("POST", "/api/queue/chat")
def queue_chat_post(req):
    return {"message": APP.room.post_message(req.current_user, req.field("text"), req.field("to"))}


@route("POST", "/api/admin/queue/chat/clear", admin=True)
def admin_chat_clear(req):
    APP.room.clear_chat()
    return {"ok": True}


@route("POST", "/api/admin/queue/end", admin=True)
def admin_queue_end(req):
    APP.room.admin_end()
    return APP.room.state_for(req.current_user)


@route("POST", "/api/admin/queue/remove", admin=True)
def admin_queue_remove(req):
    APP.room.admin_remove(req.field("username"))
    return APP.room.state_for(req.current_user)


@route("POST", "/api/admin/queue/move", admin=True)
def admin_queue_move(req):
    position = req.field("position")
    if not isinstance(position, int) or isinstance(position, bool):
        raise ApiError(400, "position must be a number")
    APP.room.admin_move(req.field("username"), position)
    return APP.room.state_for(req.current_user)


@route("POST", "/api/agent/settings")
def agent_settings(req):
    get_run(req, req.field("run")).set_auto(req.field("auto_apply"), req.field("auto_commands"))
    return {"ok": True}


# ---------------------------------------------------------------------------
# Commands typed in the chat box
# ---------------------------------------------------------------------------

MAX_NOTE_CHARS = 4000
MAX_MENTIONS = 5
MAX_MENTION_CHARS = 20000


def shell_notes_text(session):
    """Commands the user ran since the agent last heard from them. Sent once, with their next message."""
    notes = session.pop("shell_notes", None) or []
    if not notes:
        return ""
    return "Commands I ran myself just now:\n" + "\n".join(f"$ {n['command']}\n{n['output']}" for n in notes[-5:]) + "\n"


def mentioned_files_text(ws, mentions, admin=False):
    """Files the user named with @. Each is confined to the folder; one that cannot be read is simply left out."""
    if not isinstance(mentions, list):
        return ""
    parts = []
    for rel in [m for m in mentions if isinstance(m, str)][:MAX_MENTIONS]:
        try:
            full = resolve(ws, rel)
            text = read_document_file(full, allow_tools=bool(admin)) if is_document(full) else read_text_file(full)[0]
        except (ApiError, OSError):
            continue
        if text is None:
            continue
        clipped = text[:MAX_MENTION_CHARS]
        parts.append(f"--- {rel}\n{clipped}" + ("\n[the rest of this file was cut]" if len(clipped) < len(text) else ""))
    return "Files I attached:\n" + "\n".join(parts) + "\n" if parts else ""


# ---------------------------------------------------------------------------
# Voices: reading aloud, designed voices, voices from recordings (tools/voice_engine.py)
# ---------------------------------------------------------------------------

# ---- Voices: a shared cast read by VoxCPM2 through tools/voice_engine.py (config voice_url). A voice is the
# description it was made from plus the take someone kept (its reference recording), so it sounds the same every time.
# The Voices view and tools/voice_mcp.py (the agent's list_voices and speak) use the same data/voices/voices.json.
VOICE_SAMPLE_TEXT = "Καλημέρα! Αυτή είναι η φωνή μου. Μπορώ να διαβάσω ιστορίες, μαθήματα και ό,τι άλλο χρειαστείτε."
# The language a voice was made in (its kept take): the cast is grouped by it. Any voice can read any language.
VOICE_LANGS = {"el": "Greek", "en": "English"}
VOICE_SAMPLES = {"el": VOICE_SAMPLE_TEXT, "en": "Hello! This is my voice. I can read stories, lessons, and anything else you need."}
VOICE_KEEP_OUT = 200
VOICE_JOBS = {}


def voices_dir():
    return os.path.join(APP.data_dir, "voices")


def voices_load():
    return read_json(os.path.join(voices_dir(), "voices.json"), {"voices": []}).get("voices", [])


def voices_save(items):
    os.makedirs(os.path.join(voices_dir(), "samples"), exist_ok=True)
    write_json_atomic(os.path.join(voices_dir(), "voices.json"), {"voices": items})


def voice_engine(path, body=None, timeout=10):
    url = APP.cfg.get("voice_url") or ""
    if not url:
        raise ApiError(404, "voices are not set up here (config voice_url)")
    req = urllib.request.Request(url.rstrip("/") + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"}, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        try:
            message = json.load(e).get("error")
        except ValueError:
            message = None
        raise ApiError(400, message or f"the voice engine answered {e.code}")
    except (urllib.error.URLError, OSError, ValueError):
        raise ApiError(503, "the voice engine is not running (systemctl --user start strata-voice)")


def voice_text(raw, limit=20000):
    if not isinstance(raw, str) or not raw.strip():
        raise ApiError(400, "write the text to read")
    if len(raw) > limit:
        raise ApiError(400, f"at most {limit} characters at a time")
    return raw.strip()


def voice_job_start(req, kind, body, **meta):
    out_dir = os.path.join(voices_dir(), "out")
    os.makedirs(out_dir, exist_ok=True)
    for old in sorted(os.listdir(out_dir), key=lambda n: os.path.getmtime(os.path.join(out_dir, n)))[:-VOICE_KEEP_OUT]:
        os.remove(os.path.join(out_dir, old))
    jid = secrets.token_hex(6)
    fmt = body.get("format") or "wav"
    out = os.path.join(out_dir, f"{jid}.{fmt}")
    engine_job = voice_engine("/speak", {**body, "out": out, "format": fmt})["job"]
    VOICE_JOBS[jid] = {"owner": req.current_user["username"], "kind": kind, "engine": engine_job, "out": out, "created": time.time(), **meta}
    return jid


def voice_job(req, jid):
    job = VOICE_JOBS.get(jid) if isinstance(jid, str) else None
    if not job or job["owner"] != req.current_user["username"]:
        raise ApiError(404, "unknown voice job")
    return job


@route("GET", "/api/voices")
def voices_list(req):
    enabled = bool(APP.cfg.get("voice_url"))
    try:
        engine = voice_engine("/health", timeout=3) if enabled else None
    except ApiError as e:
        engine = {"ok": False, "error": e.message}
    me = req.current_user
    items = [{**v, "lang": v.get("lang") or "el", "mine": v.get("created_by") == me["username"],
              "can_edit": me["role"] == "admin" or v.get("created_by") == me["username"]} for v in voices_load()]
    return {"enabled": enabled, "engine": engine, "voices": items, "sample_text": VOICE_SAMPLE_TEXT, "sample_texts": VOICE_SAMPLES, "langs": VOICE_LANGS}


@route("POST", "/api/voices/design")
def voices_design(req):
    """Takes of a new voice made from a description, to choose one to keep."""
    description = req.field("description")
    if not isinstance(description, str) or not description.strip() or len(description) > 300:
        raise ApiError(400, "describe the voice in up to 300 characters")
    text = voice_text(req.field("text") or VOICE_SAMPLE_TEXT, 600)
    takes = to_int(req.field("takes"), 2, 1, 3)
    return {"jobs": [voice_job_start(req, "design", {"text": text, "description": description.strip()}, description=description.strip())
                     for _ in range(takes)]}


@route("POST", "/api/voices/speak")
def voices_speak(req):
    voice = next((v for v in voices_load() if v["id"] == req.field("voice")), None)
    if not voice:
        raise ApiError(404, "choose a voice")
    sample = os.path.join(voices_dir(), "samples", voice["id"] + ".wav")
    if not os.path.isfile(sample):
        raise ApiError(410, "this voice's recording is missing; make it again")
    style = req.field("style")
    # a voice with a transcript reads in its recording's own delivery unless this reading asks for another
    style = (style if isinstance(style, str) else ("" if voice.get("transcript") else voice.get("style") or "")).strip()[:200]
    fmt = req.field("format") if req.field("format") in ("wav", "mp3") else "mp3"
    return {"job": voice_job_start(req, "speak", {"text": voice_text(req.field("text")), "reference": sample, "style": style, "format": fmt,
                                                  "transcript": voice.get("transcript") or ""},
                                   voice=voice["id"], name=voice["name"])}


VOICE_CONSENTS = ("own", "permission", "made")    # my own voice; the speaker's written permission; a voice made by a machine


@route("POST", "/api/voices/from_recording")
def voices_from_recording(req):
    """A test reading in the voice of a recording in the open folder (or its part from start to end); /api/voices/save
    with that job keeps the recording as a voice, with its transcript and who agreed. Never a voice without consent."""
    ws = req.workspace()
    path = resolve(ws, req.field("path") or "")
    if not os.path.isfile(path) or not path.lower().endswith(AUDIO_EDIT_EXTS + (".mp4", ".webm", ".mov", ".mkv")):
        raise ApiError(400, "choose a sound file in the folder")
    consent, speaker = req.field("consent"), req.field("speaker")
    if consent not in VOICE_CONSENTS:
        raise ApiError(400, "say why copying this voice is allowed: your own voice, the speaker's written permission, or a voice made by a machine")
    if not isinstance(speaker, str) or not speaker.strip() or len(speaker) > 200:
        raise ApiError(400, "say whose voice it is")
    transcript = req.field("transcript") or ""
    if not isinstance(transcript, str) or len(transcript) > 2000:
        raise ApiError(400, "the transcript can have up to 2000 characters")
    nums = []
    for key in ("start", "end"):
        value = req.field(key)
        if value in (None, ""):
            nums.append(None)
            continue
        try:
            nums.append(float(value))
        except (TypeError, ValueError):
            raise ApiError(400, f"{key} is a time in seconds")
    out_dir = os.path.join(voices_dir(), "out")
    os.makedirs(out_dir, exist_ok=True)
    ref = os.path.join(out_dir, f"ref-{secrets.token_hex(6)}.wav")
    tool = audio_tool()
    try:
        seconds = tool.voice_reference(path, ref, nums[0], nums[1])
    except tool.EditError as e:
        raise ApiError(400, str(e))
    meta = {"origin": "recording", "transcript": transcript.strip(), "consent": consent, "speaker": speaker.strip(),
            "source": rel_posix(path, ws["path"]), "seconds": seconds}
    lang = req.field("lang") if req.field("lang") in VOICE_SAMPLES else "el"
    text = voice_text(req.field("text") or VOICE_SAMPLES[lang], 600)
    job = voice_job_start(req, "recording", {"text": text, "reference": ref, "transcript": meta["transcript"]}, reference=ref, **meta)
    return {"job": job, "seconds": seconds}


@route("GET", "/api/voices/job")
def voices_job_state(req):
    job = voice_job(req, req.arg("id"))
    state = voice_engine(f"/job?id={job['engine']}", timeout=5)
    out = {k: state.get(k) for k in ("state", "progress", "audio_s", "took_s", "error", "ahead")}
    if state.get("state") == "done":
        out["url"] = f"/api/voices/audio?job={req.arg('id')}"
    return out


@route("POST", "/api/voices/cancel")
def voices_cancel(req):
    job = voice_job(req, req.field("id"))
    voice_engine("/cancel", {"id": job["engine"]})
    return {"ok": True}


@route("GET", "/api/voices/audio")
def voices_audio(req):
    if req.arg("voice"):
        voice = next((v for v in voices_load() if v["id"] == req.arg("voice")), None)
        path = os.path.join(voices_dir(), "samples", voice["id"] + ".wav") if voice else ""
    else:
        path = voice_job(req, req.arg("job"))["out"]
    if not path or not os.path.isfile(path):
        raise ApiError(404, "no recording")
    with open(path, "rb") as f:
        data = f.read()
    req.send_response(200)
    req.send_header("Content-Type", "audio/mpeg" if path.endswith(".mp3") else "audio/wav")
    req.send_header("Content-Length", str(len(data)))
    req.send_header("Cache-Control", "private, max-age=3600")
    req.end_headers()
    req.wfile.write(data)


@route("POST", "/api/voices/save")
def voices_save_route(req):
    """A new voice from the take someone kept, or new words for an existing voice."""
    me, items = req.current_user, voices_load()
    name = req.field("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 60:
        raise ApiError(400, "give the voice a name of up to 60 characters")
    name = name.strip()
    fields = {"name": name}
    for key, limit in (("description", 300), ("style", 200)):
        value = req.field(key)
        if value is not None:
            if not isinstance(value, str) or len(value) > limit:
                raise ApiError(400, f"{key} can have up to {limit} characters")
            fields[key] = value.strip()
    transcript = req.field("transcript")
    if transcript is not None:
        if not isinstance(transcript, str) or len(transcript) > 2000:
            raise ApiError(400, "the transcript can have up to 2000 characters")
        fields["transcript"] = transcript.strip()
    lang = req.field("lang")
    if lang is not None:
        if lang not in VOICE_LANGS:
            raise ApiError(400, "lang must be one of " + ", ".join(VOICE_LANGS))
        fields["lang"] = lang
    vid = req.field("id")
    if any(v["name"].lower() == name.lower() and v["id"] != vid for v in items):
        raise ApiError(409, f"there is already a voice called {name}")
    if vid:
        voice = next((v for v in items if v["id"] == vid), None)
        if not voice:
            raise ApiError(404, "unknown voice")
        if me["role"] != "admin" and voice.get("created_by") != me["username"]:
            raise ApiError(403, "only its maker or an admin can change this voice")
        voice.update(fields)
    else:
        job = voice_job(req, req.field("job"))
        if job["kind"] not in ("design", "recording") or not os.path.isfile(job["out"]):
            raise ApiError(409, "keep a finished take of a new voice")
        voice = {"id": secrets.token_hex(6), "description": job.get("description", ""), "style": "", "lang": "el", **fields,
                 "created_by": me["username"], "created": time.time()}
        os.makedirs(os.path.join(voices_dir(), "samples"), exist_ok=True)
        sample = os.path.join(voices_dir(), "samples", voice["id"] + ".wav")
        if job["kind"] == "recording":
            # the voice keeps the recording itself (not the test reading), with where it came from and who agreed
            if not os.path.isfile(job.get("reference") or ""):
                raise ApiError(409, "the recording is gone; make the test reading again")
            shutil.copyfile(job["reference"], sample)
            voice.update({k: job[k] for k in ("origin", "consent", "speaker", "source", "seconds")})
            voice["transcript"] = fields.get("transcript", job.get("transcript", ""))
        else:
            shutil.copyfile(job["out"], sample)
        items.append(voice)
    voices_save(items)
    return {"voice": voice}


@route("POST", "/api/voices/delete")
def voices_delete(req):
    me, items = req.current_user, voices_load()
    voice = next((v for v in items if v["id"] == req.field("id")), None)
    if not voice:
        raise ApiError(404, "unknown voice")
    if me["role"] != "admin" and voice.get("created_by") != me["username"]:
        raise ApiError(403, "only its maker or an admin can delete this voice")
    voices_save([v for v in items if v["id"] != voice["id"]])
    try:
        os.remove(os.path.join(voices_dir(), "samples", voice["id"] + ".wav"))
    except OSError:
        pass
    return {"ok": True}


@route("POST", "/api/voices/to_project")
def voices_to_project(req):
    """A finished reading saved into the open folder, like any file there; one already at that path goes to the trash."""
    ws = req.workspace()
    job = voice_job(req, req.field("job"))
    if not os.path.isfile(job["out"]):
        raise ApiError(409, "the reading is not ready")
    rel = req.field("path")
    if not isinstance(rel, str) or not rel.lower().endswith((".mp3", ".wav")):
        raise ApiError(400, "the file name must end in .mp3 or .wav")
    path = resolve(ws, rel)
    if os.path.isdir(path):
        raise ApiError(400, "path is a folder")
    trashed = move_to_trash(ws, path) if os.path.lexists(path) else None
    os.makedirs(os.path.dirname(path), exist_ok=True)
    shutil.copyfile(job["out"], path)
    sync_publish(req.current_user["username"], req.sync_origin(), "files", ws=ws["id"])
    return {"path": rel_posix(path, ws["path"]), "trashed": trashed}


# ---------------------------------------------------------------------------
# A person's own computer (studio-helper): pairing, the folder's copy, commands and terminals
# ---------------------------------------------------------------------------

# ---- folders on a person's own computer: `studio-helper` (tools/helper) connects out to Studio, keeps a copy of the
# folder in sync with one here (the mirror), and runs commands and terminals there. Studio's file tools, search, undo and
# Git work on the mirror; each command syncs both ways before and after it.
HELPER_SKIP = {"node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".next", ".turbo",
               ".parcel-cache", "target", ".gradle", ".tox", ".DS_Store", "Thumbs.db", ".strata-studio"}   # the last: Studio's trash and notes
HELPER_MAX_FILE = 25 * 1024 * 1024        # larger files stay on the computer
HELPER_BATCH = 8 * 1024 * 1024            # bytes of file content per sync answer
HELPER_ONLINE_S = 40                      # a helper asks every 25 s, so this long without a word means it is gone
HELPER_CODE_S = 600
HELPER_CODE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
HELPER_DIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools", "helper", "dist")
HELPER_FILES = {"windows": "studio-helper.exe", "linux": "studio-helper"}


def helper_latest():
    """The helper version Studio offers for download (tools/helper/VERSION)."""
    try:
        with open(os.path.join(os.path.dirname(HELPER_DIST), "VERSION")) as f:
            return f.read().strip()
    except OSError:
        return ""


def helper_older(have, latest):
    """True when `have` is an older helper than `latest` (versions such as 0.04; a missing one is older)."""
    def parts(v):
        return tuple(int(x) if x.isdigit() else 0 for x in re.split(r"[.]", v or "0"))
    return bool(latest) and parts(have) < parts(latest)


def helper_rel(rel):
    """A path as both sides write it: forward slashes, inside the folder, never skipped. None for anything else."""
    if not isinstance(rel, str) or not rel or "\0" in rel or len(rel) > 1000:
        return None
    rel = rel.replace("\\", "/")
    parts = rel.split("/")
    if rel.startswith("/") or re.match(r"^[A-Za-z]:", rel) or any(p in ("", ".", "..") or ":" in p for p in parts):
        return None
    if any(p in HELPER_SKIP for p in parts) or rel.startswith(".strata-studio/conflicts/") or (parts[0] == ".git" and parts[-1].endswith(".lock")):
        return None
    return rel


def helper_prompt(ws):
    h = HELPERS.get(ws["helper"]) or {}
    shell = {"bash": "bash", "powershell": "PowerShell (Windows)"}.get(h.get("shell"), h.get("shell") or "a shell")
    return (f"This folder is on the user's own computer \"{h.get('machine', '?')}\" ({h.get('os', '?')}), at {h.get('remote_path', '?')}. "
            f"Studio itself, its tool servers (the mcp__ tools) and the services they use (such as listmonk, ComfyUI or the web "
            f"browser) run on a different computer: Studio's server \"{socket.gethostname()}\" ({platform.system()}). Addresses such as "
            f"127.0.0.1 or localhost in those tools' descriptions and results are on that server, which commands on "
            f"\"{h.get('machine', '?')}\" cannot reach, so use those services only through their tools; the tools see this project as "
            f"Studio's copy of it at {ws['path']}, kept in step with your folder. "
            f"The `run` tool executes on \"{h.get('machine', '?')}\", in {shell}, in the folder there, with that computer's own programs; write commands for that shell. "
            + ("It is not bash: no heredocs (<<), no `export`, no `&&` in Windows PowerShell 5.1. For anything longer than one line, write a "
               "script file with write_file and run it (`python script.py`); set variables with `$env:NAME = 'value'`. "
               if h.get("shell") == "powershell" else "")
            + 
            "Your file edits reach that folder before each command, and the files a command changes come back after it. "
            f"These folders are not copied either way, so look at them with commands: {', '.join(sorted(HELPER_SKIP - {'.DS_Store', 'Thumbs.db', '.strata-studio'}))}. "
            + ("Servers it starts run on that computer; view_page and open_preview reach them through Studio, as long as they were started by your commands "
               "(start them in the background so the command returns, e.g. with Start-Process on Windows)."
               if not helper_older(h.get("version"), HELPER_LIVE_VERSION) else
               "Servers it starts run on that computer, and its studio-helper is too old for view_page and open_preview to reach them."))


class HelperHub:
    """The paired helpers, their jobs, and the agreed state of each folder (`base`: path -> sha1 on both sides)."""

    def __init__(self):
        self.lock = threading.RLock()
        self.cond = threading.Condition(self.lock)
        self.items = None
        self.codes = {}               # pairing code -> {owner, expires}
        self.jobs = collections.defaultdict(list)
        self.results = {}
        self.seen = {}
        self.sync_locks = collections.defaultdict(threading.Lock)
        self.stat_cache = collections.defaultdict(dict)
        self.failed_codes = collections.defaultdict(list)
        self.terms = {}

    def path(self):
        return os.path.join(APP.data_dir, "helpers.json")

    def load(self):
        with self.lock:
            if self.items is None:
                self.items = read_json(self.path(), {})
            return self.items

    def save(self):
        write_json_atomic(self.path(), self.items)

    def all(self):
        return list(self.load().values())

    def get(self, hid):
        return self.load().get(hid) if isinstance(hid, str) else None

    def online(self, hid):
        return time.time() - self.seen.get(hid, 0) < HELPER_ONLINE_S

    def public(self, hid):
        h = self.get(hid)
        if not h:
            return {"id": hid, "gone": True, "online": False}
        latest = helper_latest()
        return {"id": hid, "machine": h["machine"], "os": h["os"], "shell": h["shell"], "remote_path": h["remote_path"],
                "online": self.online(hid), "last_seen": self.seen.get(hid) or h.get("last_seen"), "skipped": h.get("skipped", [])[:20],
                "version": h.get("version") or "", "outdated": helper_older(h.get("version"), latest)}

    def by_key(self, key):
        if not isinstance(key, str) or len(key) < 20:
            raise ApiError(401, "unknown helper key")
        digest = hashlib.sha256(key.encode()).hexdigest()
        found = next((h for h in self.all() if hmac.compare_digest(h["key_hash"], digest)), None)
        owner = APP.auth.users.get(found["owner"]) if found else None
        if not found or not owner or owner.get("disabled") or not may_pair(owner):
            raise ApiError(401, "this helper is no longer paired with Studio")
        return found

    def new_code(self, owner):
        with self.lock:
            now = time.time()
            self.codes = {c: v for c, v in self.codes.items() if v["expires"] > now and v["owner"] != owner}
            code = "".join(secrets.choice(HELPER_CODE_CHARS) for _ in range(8))
            self.codes[code] = {"owner": owner, "expires": now + HELPER_CODE_S}
            return code

    def take_code(self, code, ip):
        with self.lock:
            now = time.time()
            self.failed_codes[ip] = [t for t in self.failed_codes[ip] if now - t < 60]
            if len(self.failed_codes[ip]) >= 5:
                raise ApiError(429, "too many wrong codes; wait a minute")
            code = re.sub(r"[\s-]", "", code.upper()) if isinstance(code, str) else ""
            entry = self.codes.pop(code, None)
            if not entry or entry["expires"] < now:
                self.failed_codes[ip].append(now)
                raise ApiError(403, "that pairing code is wrong or has expired; make a new one in Studio")
            return entry["owner"]

    def add(self, record):
        with self.lock:
            self.load()[record["id"]] = record
            self.save()

    def remove(self, hid):
        with self.lock:
            h = self.load().pop(hid, None)
            self.save()
            self.jobs.pop(hid, None)
            self.cond.notify_all()           # its waiting poll answers now, and the next one is refused
        if not h:
            return
        with APP.lock:
            ws = APP.workspaces.get(h.get("ws"))
            if ws and ws.get("helper") == hid:
                ws.pop("helper", None)          # the copy stays, as an ordinary folder
                ws["kept_from"] = h.get("machine") or ""
                write_json_atomic(APP.workspaces_path, APP.workspaces)
            victims = [tid for tid, t in APP.terminals.items() if getattr(t, "helper", None) == hid]
            closing = [APP.terminals.pop(tid) for tid in victims]
        for term in closing:
            term.gone()
        shutil.rmtree(helper_dir(hid), ignore_errors=True)

    def send(self, hid, job):
        with self.cond:
            self.jobs[hid].append(job)
            self.cond.notify_all()

    def wait_jobs(self, hid, wait):
        deadline = time.time() + wait
        with self.cond:
            self.seen[hid] = time.time()
            while not self.jobs[hid] and time.time() < deadline and hid in self.load():
                self.cond.wait(min(5, max(0.1, deadline - time.time())))
                self.seen[hid] = time.time()
            jobs, self.jobs[hid] = self.jobs[hid], []
            return jobs

    def finish(self, job_id, result):
        with self.cond:
            self.results[job_id] = result
            self.cond.notify_all()

    def wait_result(self, job_id, until, stop=lambda: False):
        with self.cond:
            while job_id not in self.results:
                if time.time() > until or stop():
                    return None
                self.cond.wait(0.5)
            return self.results.pop(job_id)


HELPERS = HelperHub()


def helper_dir(hid):
    return os.path.join(APP.data_dir, "helpers", hid)


def may_pair(user):
    return user["role"] == "admin" or bool(user.get("pc_folders"))


def may_card(user):
    """Admins, and users an admin let choose how the graphics card is shared (the Users pane's Graphics card right)."""
    return user["role"] == "admin" or bool(user.get("card"))


def need_card_right(req):
    if not may_card(req.current_user):
        raise ApiError(403, "an admin has not let you change the graphics card setting")


def helper_auth(req):
    h = HELPERS.by_key(req.headers.get("X-Helper-Key"))
    HELPERS.seen[h["id"]] = time.time()
    agent = re.match(r"studio-helper/([\w.-]{1,20})", req.headers.get("User-Agent") or "")
    if agent and h.get("version") != agent.group(1):
        with HELPERS.lock:
            h["version"] = agent.group(1)
            HELPERS.save()
    ws = APP.workspaces.get(h["ws"])
    if not ws or ws.get("helper") != h["id"] or not os.path.isdir(ws["path"]):
        raise ApiError(410, "the folder for this helper is gone from Studio; pair it again")
    return h, ws


def mirror_scan(hid, root):
    """path -> sha1 for every file of the copy here that is shared with the computer. Unchanged files keep their sum."""
    cache, found = HELPERS.stat_cache[hid], {}
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in HELPER_SKIP and not os.path.islink(os.path.join(folder, d))]
        for name in files:
            full = os.path.join(folder, name)
            rel = helper_rel(os.path.relpath(full, root).replace(os.sep, "/"))
            if not rel:
                continue
            try:
                st = os.lstat(full)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode) or st.st_size > HELPER_MAX_FILE:
                continue
            key = (st.st_size, st.st_mtime_ns)
            hit = cache.get(rel)
            if hit and hit[0] == key:
                found[rel] = hit[1]
                continue
            try:
                with open(full, "rb") as f:
                    digest = hashlib.sha1(f.read()).hexdigest()
            except OSError:
                continue
            cache[rel] = (key, digest)
            found[rel] = digest
    return found


def mirror_write(ws, rel, data):
    full = resolve(ws, rel, nofollow=True)
    if os.path.islink(full) or os.path.isdir(full):
        shutil.rmtree(full, ignore_errors=True) if os.path.isdir(full) and not os.path.islink(full) else os.remove(full)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    if not within(os.path.realpath(os.path.dirname(full)), os.path.realpath(ws["path"])):
        raise ApiError(403, "path outside workspace")
    tmp = full + ".studio-sync"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, full)


def mirror_delete(ws, rel):
    full = resolve(ws, rel, nofollow=True)
    if os.path.isfile(full) or os.path.islink(full):
        os.remove(full)
        parent = os.path.dirname(full)
        while parent != ws["path"] and within(parent, ws["path"]):      # empty folders left behind go too
            try:
                os.rmdir(parent)
            except OSError:
                break
            parent = os.path.dirname(parent)


def helper_sync(h, ws, changes, skipped):
    """One round: the computer's changes since the agreed state come in, the copy's changes go back as `ops`. A file
    changed on both sides keeps the computer's version, and the copy's goes to .strata-studio/conflicts/."""
    hid = h["id"]
    with HELPERS.sync_locks[hid]:
        base_path = os.path.join(helper_dir(hid), "base.json")
        base = read_json(base_path, {})
        mirror = mirror_scan(hid, ws["path"])
        conflicts = []
        for raw, change in changes.items():
            rel = helper_rel(raw)
            if not rel:
                continue
            here = mirror.get(rel)
            if change is None:
                if here is not None and here != base.get(rel):
                    continue                         # edited here, deleted there: the edit wins and goes back below
                mirror_delete(ws, rel)
                base.pop(rel, None)
                mirror.pop(rel, None)
                continue
            try:
                data = base64.b64decode(change.get("b64") or "", validate=True)
            except (ValueError, AttributeError):
                raise ApiError(400, f"bad content for {rel}")
            digest = hashlib.sha1(data).hexdigest()
            if here is not None and here not in (base.get(rel), digest):
                stamp = time.strftime("%Y%m%d-%H%M%S")
                with open(resolve(ws, rel), "rb") as f:
                    mirror_write(ws, f".strata-studio/conflicts/{rel}.{stamp}", f.read())
                conflicts.append(rel)
            mirror_write(ws, rel, data)
            base[rel] = mirror[rel] = digest
        ops, size, more = {}, 0, False
        for rel, digest in sorted(mirror.items()):
            if rel in changes or base.get(rel) == digest:
                continue
            with open(resolve(ws, rel), "rb") as f:
                data = f.read()
            if ops and size + len(data) > HELPER_BATCH:
                more = True
                break
            ops[rel] = {"sha": digest, "b64": base64.b64encode(data).decode()}
            size += len(data)
            base[rel] = digest
        for rel in [r for r in base if r not in mirror and r not in changes]:
            ops[rel] = None
            base.pop(rel)
        os.makedirs(os.path.dirname(base_path), exist_ok=True)
        write_json_atomic(base_path, base)
        skipped = skipped if isinstance(skipped, list) else []     # Go sends an empty list as null
        with HELPERS.lock:
            h["skipped"] = [str(x)[:300] for x in skipped[:50]]
            h["last_sync"] = time.time()
            HELPERS.save()
    if changes or ops:
        sync_publish(ws.get("owner") or h["owner"], None, "files", ws=ws["id"])
    return {"ops": ops, "more": more, "conflicts": conflicts}


def helper_wait_online(hid, run=None, wait=15):
    deadline = time.time() + wait
    while not HELPERS.online(hid):
        if time.time() > deadline or (run is not None and run.stop):
            return False
        time.sleep(0.5)
    return True


def helper_run(run, command, extra_env):
    """A command on the person's computer: the helper syncs, runs it in the folder, syncs back, then answers."""
    h = HELPERS.get(run.ws["helper"])
    if not h:
        return False, "no computer", "This folder's computer is no longer paired with Studio, so commands cannot run."
    if not helper_wait_online(h["id"], run):
        return False, "computer offline", (f"The computer \"{h['machine']}\" is offline: start studio-helper there, then try again. "
                                           "Files can still be read and edited here; they reach the computer when it is back.")
    timeout = APP.cfg["command_timeout_s"]
    job = {"id": secrets.token_hex(8), "type": "run", "timeout": timeout, "env": {k: str(v) for k, v in extra_env.items()}}
    mark = None
    if h["shell"] == "bash":
        mark = "__studio_pipe_" + secrets.token_hex(6)
        job.update(script=PIPE_SCRIPT, env={**job["env"], "STUDIO_CMD": command, "STUDIO_PIPE_MARK": mark})
    else:
        job["command"] = command
    HELPERS.send(h["id"], job)
    result = HELPERS.wait_result(job["id"], time.time() + timeout + 120, stop=lambda: bool(getattr(run, "stop", False)))
    if result is None:
        HELPERS.send(h["id"], {"id": secrets.token_hex(8), "type": "kill", "job": job["id"]})
        stopped = getattr(run, "stop", False)
        return False, "stopped" if stopped else "no answer", ("exit code -1\n[stopped by the user]" if stopped else
                                                              f"exit code -1\n[the computer \"{h['machine']}\" did not answer in time]")
    note = "\n[timed out after %ss and killed]" % timeout if result.get("timed_out") else ""
    return command_result(command, to_int(result.get("code"), -1), str(result.get("output") or ""), mark, note)


class HelperTerminal:
    """A terminal on the person's computer. It keeps the same scrollback and streaming as a local one."""

    def __init__(self, ws, cols, rows):
        hid = ws["helper"]
        h = HELPERS.get(hid)
        if not h or not HELPERS.online(hid):
            raise ApiError(409, f"the computer \"{h['machine'] if h else '?'}\" is offline: start studio-helper there first")
        self.helper, self.key = hid, secrets.token_hex(8)
        self.cond = threading.Condition()
        self.buf, self.total, self.exit_code, self.clients, self.last_seen = bytearray(), 0, None, 0, time.time()
        HELPERS.terms[self.key] = self
        HELPERS.send(hid, {"id": secrets.token_hex(8), "type": "term_open", "term": self.key, "cols": cols, "rows": rows})

    def feed(self, data=b"", code=None):
        with self.cond:
            if data:
                self.buf += data
                self.total += len(data)
                if len(self.buf) > TERM_BUFFER:
                    del self.buf[:len(self.buf) - TERM_BUFFER]
            if code is not None and self.exit_code is None:
                self.exit_code = code
                HELPERS.terms.pop(self.key, None)
            self.cond.notify_all()

    def write(self, data):
        self.last_seen = time.time()
        if self.exit_code is None:
            HELPERS.send(self.helper, {"id": secrets.token_hex(8), "type": "term_in", "term": self.key, "b64": base64.b64encode(data).decode()})

    def resize(self, cols, rows):
        self.last_seen = time.time()
        if self.exit_code is None:
            HELPERS.send(self.helper, {"id": secrets.token_hex(8), "type": "term_resize", "term": self.key, "cols": cols, "rows": rows})

    def kill(self):
        HELPERS.send(self.helper, {"id": secrets.token_hex(8), "type": "term_close", "term": self.key})
        self.feed(b"", 0)

    def gone(self):
        self.feed(b"\r\n[the computer was removed from Studio]\r\n", -1)

    stream = Terminal.stream
    poll = Terminal.poll


@route("GET", "/api/helpers")
def helpers_list(req):
    me = req.current_user
    mine = [h for h in HELPERS.all() if h["owner"] == me["username"]]
    out = []
    for h in sorted(mine, key=lambda x: x["created"]):
        ws = APP.workspaces.get(h["ws"]) or {}
        out.append({**HELPERS.public(h["id"]), "folder": ws.get("name"), "path": ws.get("path"), "created": h["created"]})
    kept = [{"folder": os.path.basename(p), "path": p, "machine": info["machine"]} for p, info in folder_kinds(me).items()
            if info["kind"] == "kept" and os.path.isdir(p) and APP.in_roots_for(p, me)]
    return {"helpers": out, "kept": sorted(kept, key=lambda k: k["folder"].lower()), "allowed": may_pair(me), "latest": helper_latest(),
            "downloads": {os_name: os.path.isfile(os.path.join(HELPER_DIST, f)) for os_name, f in HELPER_FILES.items()}}


@route("POST", "/api/helpers/pair")
def helpers_pair(req):
    me = req.current_user
    if not may_pair(me):
        raise ApiError(403, "an admin has not allowed you to connect folders from your computer")
    return {"code": HELPERS.new_code(me["username"]), "expires_in": HELPER_CODE_S}


@route("POST", "/api/helpers/remove")
def helpers_remove(req):
    """Disconnects a computer's folder. Studio's copy of it stays as an ordinary folder, unless delete_copy is true."""
    h = HELPERS.get(req.field("id"))
    if not h or (h["owner"] != req.current_user["username"] and req.current_user["role"] != "admin"):
        raise ApiError(404, "unknown computer")
    HELPERS.remove(h["id"])
    deleted = None
    if req.field("delete_copy") is True:
        ws = APP.workspaces.get(h["ws"])
        if ws:
            deleted = delete_folder_copy(ws, h["owner"])
    sync_publish(h["owner"], req.sync_origin(), "workspace", ws=h["ws"])
    return {"ok": True, "deleted": bool(deleted)}


FOLDER_BIN_DAYS = 30


def folder_bin(username):
    return os.path.join(APP.data_dir, "deleted_folders", re.sub(r"[^\w.-]", "_", username))


def delete_folder_copy(ws, username):
    """Moves a kept or passed copy out of the person's space into data/deleted_folders/<user>/ (removed after
    FOLDER_BIN_DAYS), forgets it as a folder, and returns where it went. Never for a folder still on a computer."""
    if ws.get("helper"):
        raise ApiError(409, "that folder is still connected to a computer; disconnect it first")
    if any(not r.finished and r.ws.get("id") == ws["id"] for r in list(APP.runs.values())):
        raise ApiError(409, "an agent run is working in that folder")
    if not os.path.isdir(ws["path"]):
        raise ApiError(404, "that folder is gone already")
    binned = folder_bin(username)
    os.makedirs(binned, exist_ok=True)
    for old in os.listdir(binned):         # clear what has waited long enough
        full = os.path.join(binned, old)
        try:
            if time.time() - os.path.getmtime(full) > FOLDER_BIN_DAYS * 86400:
                shutil.rmtree(full, ignore_errors=True)
        except OSError:
            pass
    dest = os.path.join(binned, f"{os.path.basename(ws['path'])}--{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.move(ws["path"], dest)
    os.utime(dest)
    with APP.lock:
        APP.workspaces.pop(ws["id"], None)
        write_json_atomic(APP.workspaces_path, APP.workspaces)
    return dest


@route("POST", "/api/folders/delete_copy")
def folders_delete_copy(req):
    """Deletes a copy kept from a disconnected computer, or a copy passed by someone: moved out of the person's space for
    FOLDER_BIN_DAYS days (an admin can bring it back from data/deleted_folders/)."""
    me = req.current_user
    path = req.field("path")
    real = os.path.realpath(path) if isinstance(path, str) and path else ""
    if not real or not APP.in_roots_for(real, me):
        raise ApiError(403, "folder is outside the allowed workspace roots")
    ws = next((w for w in list(APP.workspaces.values()) if os.path.realpath(w["path"]) == real
               and (w.get("owner") == me["username"] or me["role"] == "admin")), None)
    if not ws or folder_place(ws)["kind"] not in ("kept", "passed"):
        raise ApiError(400, "only copies kept from a computer or passed to you can be deleted here")
    delete_folder_copy(ws, me["username"])
    sync_publish(me["username"], req.sync_origin(), "workspace", ws=ws["id"])
    return {"ok": True, "days": FOLDER_BIN_DAYS}


@route("GET", "/api/helpers/download")
def helpers_download(req):
    name = HELPER_FILES.get(req.arg("os") or "")
    full = os.path.join(HELPER_DIST, name) if name else None
    if not full or not os.path.isfile(full):
        raise ApiError(404, "that helper has not been built; run tools/helper/build.sh")
    with open(full, "rb") as f:
        data = f.read()
    req.send_response(200)
    req.send_header("Content-Type", "application/octet-stream")
    req.send_header("Content-Disposition", f'attachment; filename="{name}"')
    req.send_header("Content-Length", str(len(data)))
    req.end_headers()
    req.wfile.write(data)


@route("POST", "/api/helper/register", public=True)
def helper_register(req):
    """The helper's first call: a pairing code made in Studio, and the folder it serves. Answers its own key."""
    owner_name = HELPERS.take_code(req.field("code"), req.client_ip())
    owner = APP.auth.users.get(owner_name)
    if not owner or owner.get("disabled") or not may_pair(owner):
        raise ApiError(403, "that account may not connect folders from a computer")
    fields = {k: req.field(k) for k in ("machine", "os", "shell", "folder")}
    if not all(isinstance(v, str) and 0 < len(v) <= 300 for v in fields.values()) or fields["shell"] not in ("bash", "powershell"):
        raise ApiError(400, "machine, os, shell (bash or powershell) and folder are required")
    clean = lambda text: re.sub(r"[^\w.-]+", "-", text).strip("-.")[:40] or "folder"
    base_name = clean(re.split(r"[\\/]", fields["folder"].rstrip("\\/"))[-1]) + "@" + clean(fields["machine"])
    parent = os.path.join(APP.user_base(), owner_name)
    if owner["role"] == "admin" and not APP.in_roots_for(parent, owner):
        parent = os.path.join(APP.roots()[0], "computers")
    os.makedirs(parent, exist_ok=True)
    mirror = free_name(parent, base_name)
    os.makedirs(mirror)
    ws = APP.open_workspace(mirror, owner)
    hid, key = secrets.token_hex(8), secrets.token_urlsafe(32)
    with APP.lock:
        APP.workspaces[ws["id"]]["helper"] = hid
        write_json_atomic(APP.workspaces_path, APP.workspaces)
    agent = re.match(r"studio-helper/([\w.-]{1,20})", req.headers.get("User-Agent") or "")
    HELPERS.add({"id": hid, "owner": owner_name, "machine": fields["machine"], "os": fields["os"], "shell": fields["shell"], "version": agent.group(1) if agent else "",
                 "remote_path": fields["folder"], "ws": ws["id"], "key_hash": hashlib.sha256(key.encode()).hexdigest(), "created": time.time()})
    HELPERS.seen[hid] = time.time()
    sync_publish(owner_name, None, "helpers")
    return {"id": hid, "key": key, "folder": os.path.basename(mirror), "skip": sorted(HELPER_SKIP), "max_file": HELPER_MAX_FILE}


@route("GET", "/api/helper/poll", public=True)
def helper_poll(req):
    h, _ = helper_auth(req)
    jobs = HELPERS.wait_jobs(h["id"], min(to_int(req.arg("wait"), 25, 0, 25), 25))
    return {"jobs": jobs, "skip": sorted(HELPER_SKIP), "max_file": HELPER_MAX_FILE}


@route("POST", "/api/helper/sync", public=True)
def helper_sync_route(req):
    h, ws = helper_auth(req)
    changes = req.field("changes") or {}
    if not isinstance(changes, dict) or not all(v is None or isinstance(v, dict) for v in changes.values()):
        raise ApiError(400, "changes must map paths to {b64} or null")
    return helper_sync(h, ws, changes, req.field("skipped"))


@route("POST", "/api/helper/leave", public=True)
def helper_leave(req):
    """The person pressed Disconnect in the helper on their computer (0.07 on): Studio forgets the connection as if it
    were disconnected here, keeps the copy as an ordinary folder, and tells the person's open windows."""
    h, ws = helper_auth(req)
    HELPERS.remove(h["id"])
    sync_publish(h["owner"], "", "workspace", ws=ws["id"])
    return {"ok": True}


@route("POST", "/api/helper/result", public=True)
def helper_result(req):
    helper_auth(req)
    job = req.field("job")
    if not isinstance(job, str):
        raise ApiError(400, "job required")
    output = req.field("output")
    result = {"code": req.field("code"), "output": output if isinstance(output, str) else "", "timed_out": req.field("timed_out") is True}
    for key in ("ports", "status", "reason", "headers", "b64", "error"):      # the answers to "ports" and "fetch" (helper 0.06)
        if req.field(key) is not None:
            result[key] = req.field(key)
    HELPERS.finish(job, result)
    return {"ok": True}


@route("POST", "/api/helper/term", public=True)
def helper_term(req):
    h, _ = helper_auth(req)
    term = HELPERS.terms.get(req.field("term"))
    if not term or term.helper != h["id"]:
        return {"ok": False}
    data = req.field("b64")
    term.feed(base64.b64decode(data) if isinstance(data, str) and data else b"", to_int(req.field("exit"), 0) if req.field("exit") is not None else None)
    return {"ok": True}


# ---------------------------------------------------------------------------
# !command from the chat box, and the project's test command
# ---------------------------------------------------------------------------

@route("POST", "/api/command/run")
def command_run(req):
    """A command the user typed with !. It runs where their terminals run: in the folder's sandbox, or on the host."""
    ws = req.workspace()
    command = req.field("command")
    if not isinstance(command, str) or not command.strip():
        raise ApiError(400, "command required")
    session = load_session(req, req.field("session"))
    if session.get("ws") != ws["id"]:
        raise ApiError(400, "session belongs to another workspace")
    with APP.lock:
        if ws["id"] in APP.active:
            raise ApiError(409, "the agent is running in this folder; wait until it ends")
    context = types.SimpleNamespace(ws=ws, stop=False)
    ok, summary, output = run_online_if_allowed(context, command)
    session = load_session(req, req.field("session"))     # other windows may have changed it while this ran
    session["title"] = session.get("title") or command.strip()[:60]
    event = {"type": "shell", "command": command, "ok": ok, "summary": summary, "output": output[-12000:]}
    session["events"].append(event)
    session["shell_notes"] = (session.get("shell_notes") or []) + [{"command": command, "output": output[-MAX_NOTE_CHARS:]}]
    save_session(session)
    username = req.current_user["username"]
    sync_publish(username, req.sync_origin(), "shell", ws=ws["id"], session=session["id"])
    sync_publish(username, req.sync_origin(), "files", ws=ws["id"])
    return event


@route("GET", "/api/command/test")
def command_test(req):
    """The command /test would run."""
    return {"command": detect_test_command(req.workspace()["path"])}


# ---------------------------------------------------------------------------
# Chat report
# ---------------------------------------------------------------------------

def session_report(session):
    """A chat as Markdown, for reading or sharing. Leaves out the model's private reasoning."""
    def fence(text):
        ticks = "`" * max(3, max((len(m) for m in re.findall(r"`+", text)), default=0) + 1)
        return f"{ticks}\n{text}\n{ticks}"

    updated = session.get("updated")
    out = [f"# {session.get('title') or 'Untitled chat'}", "", f"- Model: {session.get('model') or 'unknown'}"]
    if updated:
        out.append(f"- Exported: {time.strftime('%Y-%m-%d %H:%M')}, last updated {time.strftime('%Y-%m-%d %H:%M', time.localtime(updated))}")
    calls = {}
    for ev in session.get("events") or []:
        kind = ev.get("type")
        if kind == "user":
            out += ["", "## User", "", str(ev.get("text") or "")]
        elif kind == "token":
            out += ["", "## Agent", "", str(ev.get("text") or "")]
        elif kind == "tool_call":
            calls[ev.get("id")] = ev
        elif kind == "tool_result":
            call = calls.get(ev.get("id")) or {}
            args = call.get("args") if isinstance(call.get("args"), dict) else {}
            what = args.get("command") or args.get("path") or args.get("query") or ""
            out += ["", f"**{ev.get('name')}** `{str(what)[:200]}`: {'ok' if ev.get('ok') else 'failed'}, {ev.get('summary') or ''}"]
            if ev.get("name") == "run" and ev.get("output"):
                out += ["", fence(str(ev["output"])[-2000:])]
        elif kind == "approval_resolved":
            out += ["", f"User decision: {ev.get('decision')}"]
        elif kind == "file_changed":
            out += ["", f"Changed `{ev.get('path')}`"]
        elif kind == "check":
            out += ["", f"**Tests run by Studio: {ev.get('status')}** `{ev.get('command') or ''}`"]
            if ev.get("output"):
                out += ["", fence(str(ev["output"])[-2000:])]
        elif kind == "shell":
            out += ["", f"**User ran** `{str(ev.get('command'))[:200]}`: {ev.get('summary')}", "", fence(str(ev.get("output") or "")[-2000:])]
        elif kind == "git_branch":
            out += ["", f"Run branch {ev.get('state')}: `{ev.get('branch') or ''}` {ev.get('reason') or ''}"]
        elif kind == "error":
            out += ["", f"**Error:** {ev.get('text')}"]
        elif kind == "done":
            out += ["", f"Run ended: {ev.get('reason')}, {ev.get('turns')} turns"]
            if ev.get("summary"):
                out += ["", str(ev["summary"])]
    return "\n".join(out) + "\n"


@route("GET", "/api/agent/session/export")
def agent_session_export(req):
    session = load_session(req, req.arg("id"))
    data = session_report(session).encode("utf-8")
    name = re.sub(r"[^\w-]+", "-", session.get("title") or "chat", flags=re.ASCII).strip("-")[:50] or "chat"
    req.send_response(200)
    req.send_header("Content-Type", "text/markdown; charset=utf-8")
    req.send_header("Content-Disposition", f'attachment; filename="{name}.md"')
    req.send_header("Content-Length", str(len(data)))
    req.end_headers()
    req.wfile.write(data)


# ---------------------------------------------------------------------------
# Git
# ---------------------------------------------------------------------------

# Settings in .git/config can run programs on any git command, and an agent or sandbox can write that file.
GIT_SAFE = ["--no-optional-locks", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "-c", "core.pager=cat", "-c", "commit.gpgSign=false",
            "-c", "core.editor=true", "-c", "diff.external=", "-c", "core.sshCommand=false", "-c", "color.ui=false", "-c", "core.quotePath=false"]
GIT_BRANCH_RE = re.compile(r"[A-Za-z0-9_][\w./+@#=-]{0,250}")
# Left out of a run's commit when no .gitignore does it: secrets, and folders that are large and can be rebuilt.
RUN_COMMIT_EXCLUDES = [f":(glob,exclude){p}" for p in ("**/node_modules/**", "**/.venv/**", "**/venv/**", "**/__pycache__/**", "**/.env", "**/.env.*")]
RUN_BRANCH_RE = re.compile(r"studio/run-[\w-]{1,80}")


def git(ws, *args, ok_codes=(0,), timeout=30):
    """Run git in the folder: inside its container when it is sandboxed, so nothing in the folder reaches the host."""
    extra = ["-c", "safe.directory=*"] if sandboxed(ws) else []
    run = lambda argv: subprocess.run(sandbox_exec(ws, ["git", *GIT_SAFE, *extra, *argv]), cwd=ws["path"], stdin=subprocess.DEVNULL,
                                      capture_output=True, timeout=timeout, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    try:
        if not sandboxed(ws):
            # Filter and merge drivers run programs, and no setting turns them all off. On the host, refuse a repository that has its own.
            drivers = run(["config", "--local", "--includes", "--name-only", "--get-regexp", r"^(filter|merge)\."])
            if drivers.returncode == 0 and drivers.stdout.strip():
                raise ApiError(403, "this repository's own git settings define filter or merge programs, so Studio will not run git in it; use a terminal, or turn the Sandbox on")
        proc = run(args)
    except FileNotFoundError:
        raise ApiError(503, "git is not installed")
    except subprocess.TimeoutExpired:
        raise ApiError(504, "git took too long")
    if proc.returncode not in ok_codes:
        raise ApiError(400, (proc.stderr or proc.stdout).decode("utf-8", "replace").strip()[:500] or f"git failed with code {proc.returncode}")
    return proc.stdout.decode("utf-8", "replace")


def git_state(ws):
    """'repo' when this folder is the top of a repository, 'parent' when it sits inside a larger one, else 'none'."""
    try:
        prefix = git(ws, "rev-parse", "--show-prefix").strip()
    except ApiError as exc:
        if exc.status != 400:
            raise
        return "none"
    return "parent" if prefix else "repo"


def git_current_branch(ws):
    return git(ws, "symbolic-ref", "--short", "-q", "HEAD", ok_codes=(0, 1)).strip()


def git_has_commits(ws):
    return bool(git(ws, "rev-parse", "-q", "--verify", "HEAD", ok_codes=(0, 1)).strip())


def git_identity(ws):
    """Commit under the configured name, or a stand-in on a machine where git has none."""
    name = git(ws, "config", "user.name", ok_codes=(0, 1)).strip()
    email = git(ws, "config", "user.email", ok_codes=(0, 1)).strip()
    return ["-c", f"user.name={name or 'Eidoloom Studio'}", "-c", f"user.email={email or 'studio@localhost'}"]


def git_paths(ws, paths):
    """Confine each path to the folder, and never let one read as an option."""
    if not isinstance(paths, list) or not paths or not all(isinstance(p, str) and p for p in paths):
        raise ApiError(400, "paths required")
    return [rel_posix(resolve(ws, p, nofollow=True), ws["path"]) for p in paths]


def git_ready(req):
    ws = req.workspace()
    with APP.lock:
        if ws["id"] in APP.active:
            raise ApiError(409, "the agent is running in this folder; wait until it ends")
    if git_state(ws) != "repo":
        raise ApiError(400, "this folder is not a git repository")
    return ws


@route("GET", "/api/git/status")
def git_status(req):
    ws = req.workspace()
    state = git_state(ws)
    if state != "repo":
        return {"state": state}
    raw = git(ws, "status", "--porcelain=v1", "-z", "--untracked-files=all").split("\0")
    files, i = [], 0
    while i < len(raw) and raw[i]:
        entry = raw[i]
        code, path = entry[:2], entry[3:]
        i += 1
        if code[0] in "RC":
            i += 1
        files.append({"path": path, "code": code.strip() or code, "untracked": code == "??"})
    log = []
    if git_has_commits(ws):
        for line in git(ws, "log", "-n", "30", "--format=%h%x09%s%x09%an%x09%ar").splitlines():
            parts = line.split("\t")
            if len(parts) == 4:
                log.append({"hash": parts[0], "subject": parts[1], "author": parts[2], "when": parts[3]})
    return {"state": "repo", "branch": git_current_branch(ws), "files": files, "log": log,
            "branches": git(ws, "branch", "--format=%(refname:short)", ok_codes=(0, 128)).split()}


@route("GET", "/api/git/diff")
def git_diff(req):
    ws = req.workspace()
    path = git_paths(ws, [req.arg("path")])[0]
    untracked = git(ws, "ls-files", "--error-unmatch", "--", path, ok_codes=(0, 1)) == ""
    if untracked:
        text = git(ws, "diff", "--no-index", "--no-ext-diff", "--no-textconv", "--", "/dev/null", path, ok_codes=(0, 1))
    elif git_has_commits(ws):
        text = git(ws, "diff", "--no-ext-diff", "--no-textconv", "HEAD", "--", path)
    else:
        text = git(ws, "diff", "--no-ext-diff", "--no-textconv", "--cached", "--", path)
    return {"diff": text[:400000]}


@route("GET", "/api/git/changes")
def git_changes(req):
    """Everything uncommitted, for /diff."""
    ws = req.workspace()
    if git_state(ws) != "repo":
        raise ApiError(400, "this folder is not a git repository")
    diff = git(ws, "diff", "--no-ext-diff", "--no-textconv", *(["HEAD"] if git_has_commits(ws) else ["--cached"]))
    new = git(ws, "ls-files", "--others", "--exclude-standard")
    return {"diff": diff[:400000], "new_files": [name for name in new.split("\n") if name][:200]}


@route("GET", "/api/git/show")
def git_show(req):
    ws = req.workspace()
    commit = req.arg("hash")
    if not re.fullmatch(r"[0-9a-f]{4,40}", commit):
        raise ApiError(400, "invalid commit")
    return {"diff": git(ws, "show", "--no-ext-diff", "--no-textconv", "--stat", "--patch", commit)[:400000]}


@route("POST", "/api/git/init")
def git_init(req):
    ws = req.workspace()
    state = git_state(ws)
    if state != "none":
        raise ApiError(400, "this folder is already inside a git repository")
    git(ws, "init")
    return {"ok": True}


@route("POST", "/api/git/commit")
def git_commit(req):
    ws = git_ready(req)
    message = req.field("message")
    if not isinstance(message, str) or not message.strip():
        raise ApiError(400, "a commit message is required")
    paths = git_paths(ws, req.field("paths"))
    git(ws, "add", "-A", "--", *paths)
    git(ws, *git_identity(ws), "commit", "-m", message.strip(), "--", *paths)
    return {"ok": True}


@route("POST", "/api/git/branch")
def git_branch(req):
    """Switch to a branch, creating it when asked."""
    ws = git_ready(req)
    name = req.field("name")
    if not isinstance(name, str) or not GIT_BRANCH_RE.fullmatch(name):
        raise ApiError(400, "invalid branch name")
    git(ws, "switch", *(["-c"] if req.field("create") is True else []), name)
    return {"ok": True}


@route("POST", "/api/git/run")
def git_run_branch(req):
    """Keep or throw away a branch that an agent run worked on."""
    ws = git_ready(req)
    action, branch, base = req.field("action"), req.field("branch"), req.field("base")
    if action not in ("merge", "discard"):
        raise ApiError(400, "action must be merge or discard")
    if not isinstance(branch, str) or not RUN_BRANCH_RE.fullmatch(branch) or not isinstance(base, str) or not GIT_BRANCH_RE.fullmatch(base):
        raise ApiError(400, "invalid branch name")
    git(ws, "switch", base)
    if action == "discard":
        git(ws, "branch", "-D", branch)
        return {"ok": True}
    try:
        git(ws, *git_identity(ws), "merge", "--no-ff", "--no-edit", branch)
    except ApiError:
        git(ws, "merge", "--abort", ok_codes=(0, 128))
        raise
    git(ws, "branch", "-d", branch)
    return {"ok": True}


def start_run_branch(run):
    """Put the run on its own branch, so that a bad run can be thrown away in one step."""
    if not run.use_branch:
        return
    ws = run.ws

    def skip(reason):
        run.emit("git_branch", state="skipped", reason=reason)

    try:
        if git_state(ws) != "repo":
            return skip("this folder is not its own git repository")
        current = git_current_branch(ws)
        if not current or not git_has_commits(ws):
            return skip("the repository needs a current branch with at least one commit")
        if not GIT_BRANCH_RE.fullmatch(current):
            return skip("Studio cannot handle the current branch's name")
        if RUN_BRANCH_RE.fullmatch(current) and run.session.get("git_base"):
            run.branch = current
        else:
            if git(ws, "status", "--porcelain"):
                return skip("the folder has uncommitted changes")
            run.branch = f"studio/run-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
            git(ws, "switch", "-c", run.branch)
            run.session["git_base"] = current
    except ApiError as exc:
        run.branch = None
        return skip(exc.message)
    run.emit("git_branch", state="started", branch=run.branch, base=run.session["git_base"])


def finish_run_branch(run):
    """Commit everything the run did to its branch. The user then keeps or discards the branch."""
    if not run.branch:
        return
    ws, committed, problem = run.ws, False, None
    try:
        git(ws, "add", "-A", "--", ".", *RUN_COMMIT_EXCLUDES, timeout=300)
        if git(ws, "diff", "--cached", "--name-only", timeout=300):
            title = (run.session.get("title") or "agent run").replace("\n", " ")[:60]
            git(ws, *git_identity(ws), "commit", "-m", f"Studio run: {title}", timeout=300)
            committed = True
    except ApiError as exc:
        problem = exc.message
    run.emit("git_branch", state="finished", branch=run.branch, base=run.session.get("git_base"), committed=committed, **({"error": problem} if problem else {}))


# ---------------------------------------------------------------------------
# Backups
# ---------------------------------------------------------------------------

BACKUP_SKIP = {"sandbox_homes"}     # package caches, which are large and can be rebuilt


def backups_dir(data_dir):
    return os.path.join(os.path.dirname(data_dir), "backups")


def make_backup(data_dir, keep):
    """Write data/ to a private archive, then drop the oldest. It holds password hashes, so only this user can read it."""
    folder = backups_dir(data_dir)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    os.chmod(folder, 0o700)
    name = f"studio-data-{time.strftime('%Y%m%d-%H%M%S')}.tar.gz"
    tmp = os.path.join(folder, "." + name + ".tmp")
    descriptor = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as raw, tarfile.open(fileobj=raw, mode="w:gz") as archive:
            archive.add(data_dir, arcname="data", filter=lambda m: None if set(m.name.split("/")) & BACKUP_SKIP else m)
        os.replace(tmp, os.path.join(folder, name))
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    for old in list_backups(data_dir)[keep:]:
        os.remove(os.path.join(folder, old["name"]))
    return name


def list_backups(data_dir):
    folder = backups_dir(data_dir)
    try:
        names = [n for n in os.listdir(folder) if re.fullmatch(r"studio-data-[\d-]+\.tar\.gz", n)]
    except OSError:
        return []
    found = []
    for name in sorted(names, reverse=True):
        info = os.stat(os.path.join(folder, name))
        found.append({"name": name, "size": info.st_size, "time": info.st_mtime})
    return found


# ---------------------------------------------------------------------------
# Graphics card, and the cleanup after the last run
# ---------------------------------------------------------------------------

# ---- the graphics card: how Strata and the tools (pictures, voices) share it (2026-11-11)
# "tools": the Manager's Subagent pool keeps 9 GB free beside Strata, so pictures and voices start at once while Strata
# runs with less of the card. "strata": no pool, Strata gets the whole card, and a picture or reading first unloads an
# idle Strata (tools/comfyui_mcp.py no_room_beside, tools/voice_engine.py make_room), which loads again on its next
# request. The engine's share is fixed when it starts (VRAM can be given back at runtime only on NVIDIA), so a choice
# waits as `pending` until Strata is not running, or until an admin restarts it.
CARD_MODES = {"tools": {"mode": "fixed", "pool_mib": 9216}, "strata": {"mode": "off", "pool_mib": 0}}


CLEANUP = {"pending": False, "last": None, "lock": threading.Lock()}
CLEANUP_WAIT_S = 10            # after the last run ends, so a queued message or a goal's next round is not cleaned under
COMFY_RESTART_S = 60           # then, if ComfyUI still holds this much, its service is restarted
COMFY_HELD_LIMIT = 512 * MIB


def studio_idle():
    """No run is working and no task is starting one."""
    with APP.lock:
        busy = any(not r.finished for r in list(APP.active.values()))
    return not busy


def card_vram_used():
    """Bytes in use on the largest graphics card, from the kernel (None when it cannot be read)."""
    best = None
    for dev in glob.glob("/sys/class/drm/card*/device"):
        try:
            total = int(open(os.path.join(dev, "mem_info_vram_total")).read())
            used = int(open(os.path.join(dev, "mem_info_vram_used")).read())
        except (OSError, ValueError):
            continue
        if best is None or total > best[0]:
            best = (total, used)
    return best[1] if best else None


def ram_available():
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        pass
    return None


def comfy_get(path, timeout=5):
    url = (APP.cfg.get("comfyui_url") or "").rstrip("/")
    if not url:
        return None
    try:
        with urllib.request.urlopen(url + path, timeout=timeout) as resp:
            return json.load(resp)
    except (OSError, ValueError):
        return None


def comfy_held():
    """What ComfyUI's PyTorch holds on the card, and whether its queue is empty: (bytes or None, idle)."""
    stats, queue = comfy_get("/system_stats"), comfy_get("/queue")
    held = None
    if isinstance(stats, dict):
        held = sum(int(d.get("torch_vram_total") or 0) for d in stats.get("devices") or [] if isinstance(d, dict))
    idle = isinstance(queue, dict) and not queue.get("queue_running") and not queue.get("queue_pending")
    return held, idle


def schedule_cleanup():
    """Called when a run ends: once Studio has been idle for CLEANUP_WAIT_S, cleanup_main gives memory back."""
    if not APP.cfg.get("run_cleanup", True):
        return
    with CLEANUP["lock"]:
        if CLEANUP["pending"]:
            return
        CLEANUP["pending"] = True
    threading.Thread(target=cleanup_main, daemon=True).start()


def cleanup_main():
    """The failsafe after the last run (2026-11-18, the user's request, after ComfyUI kept 1.15 GB on the card it would not
    free and the voice engine sat beside Strata): 1) the voice, sound and music engines unload and ComfyUI is asked to
    free its models; 2) a minute later, if ComfyUI still holds more than COMFY_HELD_LIMIT with an empty queue, its
    service (config comfyui_service) is restarted. Nothing happens while any run works. Strata itself is left to the
    Manager's own idle unload (its idle_timeout_minutes, shown under Graphics card)."""
    try:
        time.sleep(CLEANUP_WAIT_S)
        if not studio_idle():
            return
        vram0, ram0, steps = card_vram_used(), ram_available(), []
        urls = [u for u in (APP.cfg.get("tool_engines") or []) if isinstance(u, str) and u.startswith("http://127.0.0.1:")]
        if APP.cfg.get("voice_url") and APP.cfg["voice_url"] not in urls:
            urls.append(APP.cfg["voice_url"])
        for url in urls:
            url = url.rstrip("/")
            try:
                with urllib.request.urlopen(url + "/health", timeout=3) as resp:
                    health = json.load(resp)
            except (OSError, ValueError):
                continue
            if health.get("loaded") and not health.get("busy"):
                try:
                    urllib.request.urlopen(urllib.request.Request(url + "/unload", data=b"{}", headers={"Content-Type": "application/json"},
                                                                  method="POST"), timeout=10).read()
                except (OSError, ValueError, http.client.HTTPException):
                    pass
                steps.append(f"unloaded {health.get('engine') or url.rsplit(':', 1)[-1]}")
        held, idle = comfy_held()
        if held and idle:
            try:
                comfy = APP.cfg["comfyui_url"].rstrip("/")
                urllib.request.urlopen(urllib.request.Request(comfy + "/free", data=json.dumps({"unload_models": True, "free_memory": True}).encode(),
                                                              headers={"Content-Type": "application/json"}, method="POST"), timeout=10).read()
                steps.append("asked ComfyUI to free its models")
            except (OSError, ValueError, http.client.HTTPException):
                pass
        service = APP.cfg.get("comfyui_service") or ""
        if service and re.fullmatch(r"[\w.@-]+", service):
            time.sleep(COMFY_RESTART_S)
            held, idle = comfy_held()
            if held is not None and held > COMFY_HELD_LIMIT and idle and studio_idle():
                done = subprocess.run(["systemctl", "--user", "restart", service], capture_output=True, text=True, timeout=120)
                steps.append(f"restarted {service}, which still held {held / 2 ** 30:.1f} GB" if done.returncode == 0
                             else f"could not restart {service}: {(done.stderr or '').strip()[:200]}")
        if not steps:
            return
        time.sleep(3)
        vram1, ram1 = card_vram_used(), ram_available()
        freed_vram = max(0, vram0 - vram1) if vram0 is not None and vram1 is not None else 0
        freed_ram = max(0, ram1 - ram0) if ram0 is not None and ram1 is not None else 0
        CLEANUP["last"] = {"at": time.time(), "steps": steps, "freed_vram": freed_vram, "freed_ram": freed_ram}
        print(f"cleanup after the last run: {'; '.join(steps)} (freed {freed_vram / 2 ** 30:.1f} GB on the card, {freed_ram / 2 ** 30:.1f} GB of RAM)", flush=True)
    except Exception as exc:                 # a failsafe must never take Studio down
        print(f"cleanup after the last run failed: {exc}", flush=True)
    finally:
        with CLEANUP["lock"]:
            CLEANUP["pending"] = False


def note_active():
    """data/active_runs.json: the runs working now, for scripts and maintenance (restart Studio only when it is empty)."""
    try:
        runs = [{"run": r.id, "session": r.session["id"], "started": getattr(r, "started", None)} for r in list(APP.active.values()) if not r.finished]
        write_json_atomic(os.path.join(APP.data_dir, "active_runs.json"), {"runs": runs, "at": time.time()})
    except (OSError, AttributeError):
        pass


def card_path():
    return os.path.join(APP.data_dir, "card.json")


def card_pool():
    """The Manager's pool setting and the share the running engine started with, or None without a Manager. When Studio
    starts Strata itself, the reserve it starts Strata with (Tools ready) instead."""
    if strata_dir():
        st = strata_direct_status(timeout=5) or {}
        reserve = strata_card_reserve()
        return {"running": bool(st.get("loaded")), "busy": int((st.get("activity") or {}).get("in_flight") or 0),
                "mode": "fixed" if reserve else "off", "pool_mib": reserve or 0, "keep_ollama": False, "direct": True,
                "active_mib": (STRATA_PROC.get("reserve_started") or 0) if st and strata_running_pid() else None}
    try:
        status = gateway_json("GET", "/api/strata/status", timeout=5)
    except (OSError, ValueError, http.client.HTTPException):
        return None
    pool = status.get("subagent_pool") or {}
    running = bool(status.get("running") or status.get("server_processes"))
    return {"running": running, "busy": int((status.get("lifecycle") or {}).get("active_requests") or 0),
            "mode": pool.get("mode"), "pool_mib": int(pool.get("pool_mib") or 0), "keep_ollama": bool(pool.get("keep_ollama")),
            "active_mib": int((pool.get("active") or {}).get("pool_mib") or 0) if running else None}


STRATA_IDLE_CHOICES = (0, 5, 15, 30, 60, 120)     # the Manager's own choices for idle_timeout_minutes


def strata_idle_minutes():
    """The Manager's idle unload for Strata (minutes, 0 = never), or None without a Manager; Studio's own
    (strata_idle_minutes) when it talks to Strata's own server."""
    if APP.gateway_kind == "strata":
        return float(APP.cfg.get("strata_idle_minutes") or 0)
    try:
        lifecycle = gateway_json("GET", "/api/strata/status", timeout=5).get("lifecycle") or {}
    except (OSError, ValueError, http.client.HTTPException):
        return None
    return lifecycle.get("idle_timeout_minutes")


@route("POST", "/api/card/strata_idle")
def card_strata_idle(req):
    """Sets how long the Manager keeps an idle Strata loaded. The Manager's route replaces all three runtime settings, so
    auto_start and the startup script are sent back as they are."""
    need_card_right(req)
    minutes = req.field("minutes")
    if minutes not in STRATA_IDLE_CHOICES or isinstance(minutes, bool):
        raise ApiError(400, "minutes must be one of " + ", ".join(map(str, STRATA_IDLE_CHOICES)))
    if APP.gateway_kind == "strata":           # Studio's own idle unload for Strata's own server
        with APP.mcp_edit:
            stored = read_json(APP.config_path, {}) or {}
            stored["strata_idle_minutes"] = minutes
            write_json_atomic(APP.config_path, stored, private=bool(stored.get("gateway_key")))
            APP.cfg["strata_idle_minutes"] = minutes
        return card_state()
    try:
        lifecycle = gateway_json("GET", "/api/strata/status", timeout=5).get("lifecycle") or {}
        gateway_json("POST", "/api/strata/runtime_settings", {"auto_start": lifecycle.get("auto_start", True), "idle_timeout_minutes": minutes,
                                                             "startup_script": lifecycle.get("startup_script") or ""}, timeout=20)
    except (OSError, ValueError, http.client.HTTPException) as exc:
        raise ApiError(502, f"the Ollama Manager did not take it: {exc}")
    return card_state()


# ---------------------------------------------------------------------------
# Greek check: the glossary, the grammar rules and LanguageTool
# ---------------------------------------------------------------------------

# --- Greek glossary --------------------------------------------------------------------------------------------------
# The house glossary for Check Greek (tools/languagetool_mcp.py and tools/greek_grammar.py): terms to avoid with what
# to use and why, and words that are right (names, terms) so the speller leaves them. One for the whole Studio, read by
# the tool on every check, so a change counts at once; admins edit it in Account & settings → Greek.
GLOSSARY_SEED = {
    "terms": [
        {"use": "έχει δυσλεξία", "avoid": ["πάσχει από δυσλεξία", "υποφέρει από δυσλεξία"],
         "why": "Η δυσλεξία δεν είναι ασθένεια· είναι διαφορετικός τρόπος επεξεργασίας της γλώσσας."},
        {"use": "παιδί με δυσλεξία", "avoid": ["δυσλεξικό παιδί", "δυσλεξικός μαθητής"],
         "why": "Πρώτα το παιδί, μετά η δυσκολία."},
        {"use": "ειδικές μαθησιακές δυσκολίες", "avoid": ["μαθησιακά προβλήματα"],
         "why": "Ο όρος που χρησιμοποιεί η εκπαιδευτική νομοθεσία."},
    ],
    "accept": ["Ηλέκτρα", "Φρενέλ", "Strata", "Studio"],
}
GLOSSARY_LIMITS = {"terms": 500, "avoid": 12, "text": 160, "why": 400, "accept": 3000, "word": 60}


def glossary_path():
    return os.path.join(APP.data_dir, "greek", "glossary.json")


def greek_check_env(argv):
    """Studio's Greek checker learns where the glossary is; other servers get nothing."""
    return {"GREEK_GLOSSARY": glossary_path()} if any(str(a).endswith("languagetool_mcp.py") for a in argv or []) else {}


def glossary_read():
    path = glossary_path()
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        write_json_atomic(path, GLOSSARY_SEED)
    data = read_json(path, {})
    return {"terms": data.get("terms") or [], "accept": data.get("accept") or []}


def glossary_clean(terms, accept):
    """Checks a glossary from the page; raises ApiError with what is wrong."""
    L = GLOSSARY_LIMITS
    if not isinstance(terms, list) or not isinstance(accept, list):
        raise ApiError(400, "terms and accept are lists")
    if len(terms) > L["terms"] or len(accept) > L["accept"]:
        raise ApiError(400, f"at most {L['terms']} terms and {L['accept']} words that are right")
    out = []
    for i, t in enumerate(terms, 1):
        if not isinstance(t, dict):
            raise ApiError(400, f"term {i} is not an object")
        use = str(t.get("use") or "").strip()
        avoid = [str(a).strip() for a in (t.get("avoid") or []) if str(a).strip()] if isinstance(t.get("avoid"), list) else None
        why = str(t.get("why") or "").strip()
        if avoid is None or not avoid:
            raise ApiError(400, f"term {i} ({use or 'no name'}) needs at least one phrase to avoid")
        if len(avoid) > L["avoid"] or any(len(a) > L["text"] for a in avoid) or len(use) > L["text"] or len(why) > L["why"]:
            raise ApiError(400, f"term {i} is too long: phrases up to {L['text']} characters, the reason up to {L['why']}")
        out.append({"use": use, "avoid": avoid, "why": why})
    words = []
    for w in accept:
        w = str(w).strip()
        if not w:
            continue
        if len(w) > L["word"] or re.search(r"\s", w):
            raise ApiError(400, f"«{w[:30]}» is not one word of up to {L['word']} letters")
        if w not in words:
            words.append(w)
    return {"terms": out, "accept": words}


@route("GET", "/api/greek/glossary")
def greek_glossary(req):
    return {**glossary_read(), "editable": (req.current_user or {}).get("role") == "admin", "lexicon": greek_lexicon_ready()}


@route("POST", "/api/greek/glossary", admin=True)
def greek_glossary_save(req):
    data = glossary_clean(req.field("terms"), req.field("accept"))
    write_json_atomic(glossary_path(), data)
    return {**data, "editable": True, "lexicon": greek_lexicon_ready()}


def tools_import(name):
    """A module from tools/ (Studio's own tool servers), imported into Studio."""
    if TOOLS_DIR not in sys.path:
        sys.path.insert(0, TOOLS_DIR)
    return importlib.import_module(name)


def greek_lexicon_ready():
    greek_grammar = tools_import("greek_grammar")
    return greek_grammar.LEX.ready()


@route("POST", "/api/greek/check")
def greek_check(req):
    """The same check the agent's tool makes, for the Greek section's Try it box."""
    text = req.field("text")
    if not isinstance(text, str) or not text.strip() or len(text) > 20000:
        raise ApiError(400, "text is up to 20000 characters")
    languagetool_mcp = tools_import("languagetool_mcp")
    spec = next((s for s in APP.mcp.servers if any(str(a).endswith("languagetool_mcp.py") for a in s.get("args") or [])), None)
    languagetool_mcp.LANGUAGETOOL_URL = ((spec or {}).get("env") or {}).get("LANGUAGETOOL_URL", "http://127.0.0.1:8010").rstrip("/")
    languagetool_mcp.GLOSSARY = glossary_path()
    glossary_read()
    result, failed = languagetool_mcp.check_text({"text": text})
    return {"result": result, "failed": failed}


# ---------------------------------------------------------------------------
# Graphics card endpoints
# ---------------------------------------------------------------------------

def card_state():
    saved = read_json(card_path(), {})
    pool = card_pool()
    mode = saved.get("mode")
    if mode not in CARD_MODES:                  # never chosen in Studio: read it from the Manager
        mode = "tools" if pool and pool["pool_mib"] >= 8192 else "strata"
    runs = [r for r in APP.runs.values() if not r.finished]
    return {"mode": mode, "pending": bool(saved.get("pending")), "pool": pool, "runs": len(runs), "applied_at": saved.get("applied_at"),
            "strata_idle": strata_idle_minutes(),
            "strata_server": "studio" if strata_dir() else "own" if APP.gateway_kind == "strata" else "manager"}


def card_apply(restart):
    """Sends the chosen pool to the Manager. The Manager restarts a running Strata to apply it, so without `restart`
    this happens only while Strata is not running."""
    saved = read_json(card_path(), {})
    if saved.get("mode") not in CARD_MODES:
        return False
    pool = card_pool()
    if pool is None or (pool["running"] and not restart):
        return False
    if pool.get("direct"):                     # Studio starts Strata: the next start takes the new reserve
        with STRATA_PROC["lock"]:
            if strata_running_pid() or strata_adopt():
                STRATA_PROC["pid"] = strata_running_pid() or strata_adopt()
                strata_stop()
        saved.update(pending=False, applied_at=time.time())
        write_json_atomic(card_path(), saved)
        return True
    gateway_json("POST", "/api/strata/pool", {**CARD_MODES[saved["mode"]], "keep_ollama": pool["keep_ollama"]}, timeout=60)
    saved.update(pending=False, applied_at=time.time())
    write_json_atomic(card_path(), saved)
    return True


def card_loop():
    """A pending choice reaches the Manager once Strata is not running (it unloads after 15 idle minutes)."""
    while True:
        time.sleep(60)
        try:
            if read_json(card_path(), {}).get("pending"):
                card_apply(restart=False)
        except (OSError, ValueError, http.client.HTTPException):
            continue


@route("GET", "/api/card")
def card_route(req):
    need_card_right(req)
    return card_state()


@route("POST", "/api/card")
def card_save(req):
    need_card_right(req)
    mode = req.field("mode")
    if mode not in CARD_MODES:
        raise ApiError(400, "mode must be tools or strata")
    saved = read_json(card_path(), {})
    saved.update(mode=mode, pending=True)
    write_json_atomic(card_path(), saved)
    try:
        card_apply(restart=False)              # applied now when Strata is not running
    except (OSError, ValueError, http.client.HTTPException):
        pass
    return card_state()


@route("POST", "/api/card/restart")
def card_restart(req):
    """Restarts Strata with the chosen share now. Not while an agent run is working: a restart would end it."""
    need_card_right(req)
    state = card_state()
    if state["runs"]:
        raise ApiError(409, f"{state['runs']} agent run{'s are' if state['runs'] > 1 else ' is'} working; restarting Strata now would end "
                            f"{'them' if state['runs'] > 1 else 'it'}. It applies by itself once Strata is idle and unloaded.")
    if state["pool"] and state["pool"]["busy"]:
        raise ApiError(409, "Strata is answering a request now; try again in a moment")
    try:
        if not card_apply(restart=True):
            raise ApiError(502, "the Ollama Manager did not answer")
    except (OSError, ValueError, http.client.HTTPException) as exc:
        raise ApiError(502, f"the Ollama Manager refused the change: {exc}") from None
    return card_state()


# ---------------------------------------------------------------------------
# Updates and Tools & setup (updates.py, installer.py)
# ---------------------------------------------------------------------------

# ---- Updates for third-party tools (updates.py): Studio gives it what is busy and how to restart a tool server.
def updates_busy():
    """Why an update must wait, or None: an agent run, or ComfyUI making something."""
    if APP.active:
        return "an agent run is working"
    if APP.cfg.get("comfyui_url"):
        try:
            with urllib.request.urlopen(APP.cfg["comfyui_url"].rstrip("/") + "/queue", timeout=5) as resp:
                queue = json.load(resp)
            if queue.get("queue_running") or queue.get("queue_pending"):
                return "ComfyUI is making something"
        except (OSError, ValueError):
            pass
    return None


def updates_server_ready(name, timeout):
    end = time.time() + timeout
    while time.time() < end:
        client = APP.mcp.clients.get(name)
        if client is not None and client.state == "ready" and client.tools:
            return True
        if client is not None and client.state == "error":
            return False
        time.sleep(1)
    return False


def make_updates():
    import updates
    test = APP.cfg.get("updates_catalog") == "test"
    hooks = {"busy": updates_busy, "restart_server": APP.mcp.restart, "server_ready": updates_server_ready, "test": test,
             "server_enabled": lambda name: bool((APP.mcp.spec(name) or {}).get("enabled"))}
    return updates.Updates(APP.data_dir, os.path.dirname(os.path.abspath(__file__)), hooks)


def need_updates():
    if not APP.updates:
        raise ApiError(404, "updates are off for this Studio (\"updates\": true in studio.config.json turns them on)")
    return APP.updates


def update_call(fn, *args, **kw):
    import updates
    try:
        return fn(*args, **kw)
    except updates.Fail as e:
        raise ApiError(409, str(e))


# ---- Installing the tools on a new machine (installer.py): Tools & setup in Account & settings.
def installer_add_server(spec):
    """Adds one tool server to data/mcp.json, keeping every other server exactly as it is."""
    with APP.mcp_edit:
        if APP.mcp.spec(spec["name"]):
            return
        APP.mcp.replace(APP.mcp.servers + [{**spec, "enabled": True}])


def installer_set_config(key, value):
    """Sets one key of studio.config.json, for an engine the installer started (comfyui_url, voice_url); never replaces a
    value an admin set by hand."""
    if APP.cfg.get(key):
        return
    with APP.mcp_edit:
        stored = read_json(APP.config_path, {}) or {}
        stored[key] = value
        write_json_atomic(APP.config_path, stored)
        APP.cfg[key] = value


def need_installer():
    if not APP.cfg.get("installer"):
        raise ApiError(404, "Tools & setup is off for this Studio (\"installer\": true in studio.config.json turns it on)")
    if APP.installer is None:
        import installer
        hooks = {"busy": updates_busy, "server_spec": APP.mcp.spec, "add_server": installer_add_server, "server_ready": updates_server_ready,
                 "config_get": APP.cfg.get, "set_config": installer_set_config, "test": APP.cfg.get("installer_catalog") == "test"}
        APP.installer = installer.Installer(APP.data_dir, os.path.dirname(os.path.abspath(__file__)), hooks)
    return APP.installer


def installer_call(fn, *args):
    import installer
    try:
        return fn(*args)
    except installer.Fail as e:
        raise ApiError(409, str(e))


@route("GET", "/api/admin/setup", admin=True)
def setup_get(req):
    return need_installer().public()


@route("POST", "/api/admin/setup/check", admin=True)
def setup_check(req):
    inst = need_installer()
    inst.look(again=True)
    return inst.public()


@route("POST", "/api/admin/setup/script", admin=True)
def setup_script(req):
    ids = req.field("ids")
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        raise ApiError(400, "ids must be a list")
    return {"script": need_installer().sudo_script(ids)}


@route("POST", "/api/admin/setup/install", admin=True)
def setup_install(req):
    if not APP.cfg["mcp"]:
        raise ApiError(409, "tool servers are off for this Studio (\"mcp\": true in studio.config.json)")
    inst = need_installer()
    return {"job": installer_call(inst.start, req.field("ids"))}


@route("POST", "/api/admin/setup/stop", admin=True)
def setup_stop(req):
    return {"stopping": need_installer().stop()}


@route("GET", "/api/admin/updates", admin=True)
def updates_get(req):
    return need_updates().public()


@route("POST", "/api/admin/updates/check", admin=True)
def updates_check(req):
    up = need_updates()
    iid = req.field("id")
    if iid is not None and iid not in up.items:
        raise ApiError(404, "unknown item")
    threading.Thread(target=(lambda: up.check(iid)) if iid else up.check_all, daemon=True).start()
    return {"ok": True}


@route("POST", "/api/admin/updates/apply", admin=True)
def updates_apply(req):
    up = need_updates()
    action = req.field("action") or "update"
    if action not in ("update", "rollback"):
        raise ApiError(400, "action is update or rollback")
    return {"job": update_call(up.start, need_str({"id": req.field("id")}, "id"), action)}


@route("POST", "/api/admin/updates/settings", admin=True)
def updates_settings(req):
    up = need_updates()
    with up.lock:
        settings = up.state["settings"]
        if req.field("auto") is not None:
            settings["auto"] = req.field("auto") is True
        window = req.field("window")
        if window is not None:
            if not (isinstance(window, list) and len(window) == 2 and all(isinstance(h, int) and 0 <= h <= 23 for h in window) and window[0] != window[1]):
                raise ApiError(400, "window is [start hour, end hour], 0-23, not equal")
            settings["window"] = window
        items = req.field("items") or {}
        if not isinstance(items, dict):
            raise ApiError(400, "items maps an id to true or false")
        for iid, on in items.items():
            if iid not in up.items or not up.items[iid]["auto_ok"] or not isinstance(on, bool):
                raise ApiError(400, f"{iid} cannot update by itself")
            up.info(iid)["auto"] = on
        up.save()
    return up.public()


# ---------------------------------------------------------------------------
# Backup endpoints
# ---------------------------------------------------------------------------

def backup_loop():
    """Back up when the newest backup is older than the interval."""
    while True:
        hours = APP.cfg["backup_interval_h"]
        if hours > 0:
            backups = list_backups(APP.data_dir)
            if not backups or time.time() - backups[0]["time"] > hours * 3600:
                try:
                    make_backup(APP.data_dir, APP.cfg["backup_keep"])
                except (OSError, tarfile.TarError) as exc:
                    print(f"backup failed: {exc}", flush=True)
        time.sleep(600)


@route("GET", "/api/admin/backups", admin=True)
def admin_backups(req):
    return {"backups": list_backups(APP.data_dir), "folder": backups_dir(APP.data_dir), "interval_h": APP.cfg["backup_interval_h"]}


@route("POST", "/api/admin/backups", admin=True)
def admin_backup_now(req):
    try:
        return {"name": make_backup(APP.data_dir, APP.cfg["backup_keep"])}
    except (OSError, tarfile.TarError) as exc:
        raise ApiError(500, f"backup failed: {exc}")


# ---------------------------------------------------------------------------
# Command line (--backup, --restore, --set-password) and the entry point
# ---------------------------------------------------------------------------

def restore_command(config_path, archive_path):
    """Replace data/ with a backup. The current data is moved aside, not deleted."""
    cfg = load_config(config_path)
    try:
        socket.create_connection((cfg["host"] if cfg["host"] not in ("0.0.0.0", "") else "127.0.0.1", cfg["port"]), timeout=1).close()
        print(f"Studio is still running on port {cfg['port']}. Stop it first.", file=sys.stderr)
        return 1
    except OSError:
        pass
    data_dir = os.path.join(os.path.dirname(os.path.realpath(config_path)), "data")
    with tarfile.open(archive_path) as archive:
        members = archive.getmembers()
        if not any(m.name == "data" and m.isdir() for m in members) or any(m.name != "data" and not m.name.startswith("data/") for m in members):
            print("That file is not a Eidoloom Studio backup.", file=sys.stderr)
            return 1
        staging = os.path.join(os.path.dirname(data_dir), f".restore-{secrets.token_hex(4)}")
        os.makedirs(staging, mode=0o700)
        try:
            archive.extractall(staging, filter="data")
            aside = None
            if os.path.exists(data_dir):
                aside = f"{data_dir}.before-restore-{time.strftime('%Y%m%d-%H%M%S')}"
                os.rename(data_dir, aside)
            os.rename(os.path.join(staging, "data"), data_dir)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    print(f"Restored {archive_path}." + (f" The previous data is in {aside}." if aside else ""))
    return 0


def set_password_command(config_path, username):
    """Recovery for a forgotten password, when no admin can sign in to create a reset link."""
    auth = Auth(os.path.join(os.path.dirname(os.path.realpath(config_path)), "data"))
    if username not in auth.users:
        sys.exit(f"unknown user {username!r}. Accounts: {', '.join(sorted(auth.users)) or 'none'}")
    password = getpass.getpass("New password: ")
    if len(password) < 8 or password != getpass.getpass("Confirm password: "):
        sys.exit("passwords must match and have at least 8 characters")
    auth.change_password(username, password)
    auth.revoke_sessions(username)
    print(f"Password changed for {username}, and all of their sessions were signed out.")


def main():
    global APP
    parser = argparse.ArgumentParser(description="Eidoloom Studio backend")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--config", default=os.path.join(SCRIPT_DIR, "studio.config.json"))
    parser.add_argument("--set-password", metavar="USERNAME",
                        help="set an account's password and exit; stop the server first")
    parser.add_argument("--backup", action="store_true", help="back up the data folder and exit")
    parser.add_argument("--restore", metavar="ARCHIVE", help="replace the data folder with a backup and exit; stop the server first")
    args = parser.parse_args()
    if args.set_password:
        return set_password_command(args.config, args.set_password)
    if args.restore:
        return restore_command(args.config, args.restore)
    if args.backup:
        cfg = load_config(args.config)
        print("Wrote", make_backup(os.path.join(os.path.dirname(os.path.realpath(args.config)), "data"), cfg["backup_keep"]))
        return 0
    cfg = load_config(args.config)
    if args.host:
        cfg["host"] = args.host
    if args.port is not None:
        cfg["port"] = args.port
    APP = App(cfg, args.config)
    server = Server((cfg["host"], cfg["port"]), Handler)
    cfg["port"] = server.server_address[1]
    threading.Thread(target=terminal_janitor, daemon=True).start()
    threading.Thread(target=waiting_room_loop, daemon=True).start()
    threading.Thread(target=external_activity_loop, daemon=True).start()
    threading.Thread(target=backup_loop, daemon=True).start()
    threading.Thread(target=card_loop, daemon=True).start()
    threading.Thread(target=strata_idle_loop, daemon=True).start()
    note_active()                         # no run survives a restart
    threading.Thread(target=task_loop, daemon=True).start()
    if cfg.get("updates"):
        APP.updates = make_updates()
        threading.Thread(target=APP.updates.loop, daemon=True).start()
    timeskip_recover()
    if cfg["mcp"]:
        APP.mcp.start_all()
    shown = "localhost" if cfg["host"] in ("0.0.0.0", "") else cfg["host"]
    state = "setup needed: open the page and create a password" if APP.auth.setup_needed() else "password set, login required"
    print(f"Eidoloom Studio on http://{shown}:{cfg['port']}/ (bound to {cfg['host']}) - {state}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    sys.exit(main())
