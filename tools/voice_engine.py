#!/usr/bin/env python3
"""Eidoloom Studio's voice engine: VoxCPM2 behind a small local HTTP server (127.0.0.1:8189 by default).

Runs in its own environment (~/.local/opt/voice/.venv, which borrows ComfyUI's PyTorch for AMD). The model loads when a
job needs it and unloads after VOICE_IDLE_S idle seconds, so the graphics card is free again for ComfyUI and Strata.
It loads only when VOICE_NEED_GB is free on the card; otherwise the job waits (state "waiting_card").

  GET  /health                -> {ok, loaded, device, queue, busy, seconds_per_audio_second}
  POST /speak {text, description?, reference?, transcript?, style?, out, format?, cfg?, steps?} -> {job}
       description: design a voice from words; reference: a WAV whose voice to keep (a saved voice's sample);
       transcript: the exact words spoken in the reference, for the closest copy (VoxCPM2's prompt audio + prompt text;
                   not used when a style asks for another delivery);
       style: a short hint used with a reference ("slightly faster, cheerful"); out: the file to write, inside VOICE_OUT_ROOTS
  GET  /job?id=               -> {state: queued|waiting_card|loading|working|done|error|cancelled, progress, audio_s, out, error}
  POST /cancel {id}           (a job that has not started)
  POST /unload
Long texts are read in sentence groups; every group after the first copies the first group's voice, so one reading
keeps one voice even when it comes from a description.
"""
import json
import os
import queue
import re
import secrets
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("VOICE_PORT", "8189"))
MODEL = os.path.expanduser(os.environ.get("VOICE_MODEL", "~/.local/opt/voice/models/VoxCPM2"))
IDLE_S = int(os.environ.get("VOICE_IDLE_S", "180"))
NEED_GB = float(os.environ.get("VOICE_NEED_GB", "8"))
NEED_RAM_GB = float(os.environ.get("VOICE_NEED_RAM_GB", "8"))   # system memory the load takes; Strata can hold 43 GB of 62


def ram_free_gb():
    """MemAvailable from /proc/meminfo, in GB (0 when it cannot be read, so nothing is unloaded on a guess)."""
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / 1e6
    except OSError:
        pass
    return float("inf")

CARD_WAIT_S = int(os.environ.get("VOICE_CARD_WAIT_S", "900"))
# When Strata holds the card (Studio's "Strata first" setting), unload it through the Strata gateway while it is idle,
# read, and give the card back at once, so Strata loads again at full size on its next request. VOICE_MAKE_ROOM=0 waits instead.
MANAGER_URL = os.environ.get("MANAGER_URL", "http://127.0.0.1:8787").rstrip("/")
MAKE_ROOM = os.environ.get("VOICE_MAKE_ROOM", "1") == "1"
OUT_ROOTS = [os.path.realpath(os.path.expanduser(p)) for p in os.environ.get("VOICE_OUT_ROOTS", "~/Projects").split(":") if p]
MAX_TEXT = 20000
CHUNK_CHARS = 280
FAKE = os.environ.get("VOICE_FAKE") == "1"          # tests: a tone instead of the model, no PyTorch needed

jobs, work = {}, queue.Queue()
state = {"model": None, "device": None, "last_used": 0.0, "busy": None, "pace": 3.2, "made_room": False}   # pace: seconds of work per second of speech
lock = threading.Lock()


def within(path, roots):
    real = os.path.realpath(path)
    return any(real == r or real.startswith(r + os.sep) for r in roots)


def chunks(text):
    """Sentence groups of at most CHUNK_CHARS, so long texts stay stable."""
    parts = re.split(r"(?<=[.!;?·…»\n])\s+", text.strip())
    out, cur = [], ""
    for part in parts:
        if cur and len(cur) + len(part) + 1 > CHUNK_CHARS:
            out.append(cur)
            cur = part
        else:
            cur = (cur + " " + part).strip()
    if cur:
        out.append(cur)
    return out or [text]


def free_card_gb():
    """What the model could use: free memory on the card plus what this process already holds (PyTorch keeps it after an
    unload, and a reload reuses it). Counting only the free part made the engine wait for itself (2026-11-11)."""
    import torch
    free, _ = torch.cuda.mem_get_info()
    return (free + torch.cuda.memory_reserved()) / 1e9


def manager(path, body=None, timeout=20):
    import json as _json
    import urllib.request
    req = urllib.request.Request(MANAGER_URL + path, data=_json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"}, method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return _json.load(resp)


def strata_state():
    """(loaded, requests in progress) of the Strata engine, from the gateway."""
    status = manager("/api/strata/status")
    return bool(status.get("running") or status.get("server_processes")), int((status.get("lifecycle") or {}).get("active_requests") or 0)


def make_room(job):
    """Unloads Strata when it holds the card and is not answering anyone; True once it is gone. An agent waiting for this
    reading is not generating, so only the reload afterwards costs time."""
    try:
        loaded, busy = strata_state()
        if not loaded or busy:
            return False
        job["state"] = "making_room"
        print("unloading Strata to make room on the graphics card", flush=True)
        manager("/api/strata/stop", {}, timeout=120)
        deadline = time.time() + 120
        while strata_state()[0] and time.time() < deadline:
            time.sleep(1)
        state["made_room"] = True
        return True
    except Exception as exc:          # no gateway here: wait as before
        print(f"could not ask the gateway to unload Strata: {exc}", flush=True)
        return False


def load(job):
    if state["model"] is not None:
        return state["model"]
    if FAKE:
        state["model"], state["device"] = "fake", "fake"
        return "fake"
    import torch
    from voxcpm import VoxCPM
    if not torch.cuda.is_available():
        raise RuntimeError("no graphics card is available to PyTorch")
    deadline = time.time() + CARD_WAIT_S
    while free_card_gb() < NEED_GB:
        if job.get("cancel") or time.time() > deadline:
            raise RuntimeError(f"the graphics card did not have {NEED_GB:g} GB free in time (pictures or Strata are using it)")
        if MAKE_ROOM and make_room(job):
            continue
        job["state"] = "waiting_card"
        time.sleep(3)
    # The card is not the only limit: loading reads the weights into system memory, and beside a loaded Strata
    # (43 GB) that ran the machine out of memory, so the kernel killed Strata 13 times in one run (2026-11-18).
    if MAKE_ROOM and not FAKE and ram_free_gb() < NEED_RAM_GB:
        print(f"only {ram_free_gb():.1f} GB of memory is free and loading takes about {NEED_RAM_GB:g} GB", flush=True)
        make_room(job)
    job["state"] = "loading"
    state["model"] = VoxCPM(voxcpm_model_path=MODEL, zipenhancer_model_path=None, enable_denoiser=False, optimize=False, device="cuda")
    state["device"] = "cuda"
    return state["model"]


def unload():
    with lock:
        if state["model"] is None or state["busy"]:
            return
        state["model"], state["device"] = None, None
    if not FAKE:
        import gc
        import torch
        gc.collect()
        torch.cuda.empty_cache()
        held = torch.cuda.memory_reserved() / 1e9
        if held > 1 and work.empty() and not state["busy"]:
            # Memory still held after the unload goes back to the card only when the process ends: exit, and the
            # strata-voice service starts a fresh engine, so ComfyUI and Strata really get the card back.
            print(f"unloaded, but {held:.1f} GB is still held; restarting to give it back", flush=True)
            os._exit(3)


def synth(model, text, reference=None, prompt_wav=None, prompt_text=None, cfg=2.0, steps=10):
    if FAKE:
        import math
        import struct
        rate = 16000
        seconds = max(0.5, len(text) / 60)
        samples = [int(8000 * math.sin(2 * math.pi * 440 * i / rate)) for i in range(int(rate * seconds))]
        return struct.pack(f"<{len(samples)}h", *samples), rate
    import numpy as np
    wav = model.generate(text=text, reference_wav_path=reference, prompt_wav_path=prompt_wav, prompt_text=prompt_text,
                         cfg_value=cfg, inference_timesteps=steps)
    return np.asarray(wav, dtype=np.float32), model.tts_model.sample_rate


def write_audio(path, pieces, rate, fmt):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    wav_path = path if fmt == "wav" else path + ".tmp.wav"
    if FAKE:
        import wave
        with wave.open(wav_path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(b"".join(pieces))
    else:
        import numpy as np
        import soundfile as sf
        gap = np.zeros(int(rate * 0.25), dtype=np.float32)
        joined = []
        for i, piece in enumerate(pieces):
            if i:
                joined.append(gap)
            joined.append(piece)
        sf.write(wav_path, np.concatenate(joined), rate)
    if fmt != "wav":
        done = subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", wav_path, "-codec:a", "libmp3lame", "-qscale:a", "2", path],
                              capture_output=True, text=True)
        os.remove(wav_path)
        if done.returncode:
            raise RuntimeError("ffmpeg could not make the MP3: " + done.stderr[-300:])


def duration(path):
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path], capture_output=True, text=True, timeout=30)
        return round(float(out.stdout.strip()), 1)
    except (ValueError, OSError, subprocess.SubprocessError):
        return None


def run(job):
    with lock:
        state["busy"] = job["id"]
    try:
        model = load(job)
        job["state"], job["started"] = "working", time.time()
        groups = chunks(job["text"])
        job["estimate_s"] = max(5.0, sum(len(g) for g in groups) / 13 * state["pace"])
        pieces, rate, first_wav = [], 16000, None
        for i, group in enumerate(groups):
            if job.get("cancel"):
                raise RuntimeError("cancelled")
            if job.get("reference"):
                # With a transcript (and no style) VoxCPM2 continues the reference itself: the closest copy, delivery
                # included. A style asks for another delivery, so it uses the reference for the voice only.
                transcript = None if job.get("style") else job.get("transcript")
                text = f"({job['style']}){group}" if job.get("style") else group
                audio, rate = synth(model, text, reference=job["reference"], prompt_wav=job["reference"] if transcript else None,
                                    prompt_text=transcript, cfg=job["cfg"], steps=job["steps"])
            elif i == 0 or not first_wav:
                text = f"({job['description']}){group}" if job.get("description") else group
                audio, rate = synth(model, text, cfg=job["cfg"], steps=job["steps"])
                if len(groups) > 1 and not FAKE:      # the rest copy this voice, so one reading keeps one voice
                    import soundfile as sf
                    first_wav = os.path.join(os.path.dirname(job["out"]), f".voice-{job['id']}.wav")
                    sf.write(first_wav, audio, rate)
            else:
                audio, rate = synth(model, group, reference=first_wav, cfg=job["cfg"], steps=job["steps"])
            pieces.append(audio)
            job["done_groups"] = i + 1
        write_audio(job["out"], pieces, rate, job["format"])
        if first_wav and os.path.exists(first_wav):
            os.remove(first_wav)
        took = time.time() - job["started"]
        job["audio_s"] = duration(job["out"])
        if job["audio_s"] and not FAKE:
            state["pace"] = round(0.7 * state["pace"] + 0.3 * took / job["audio_s"], 2)
        job["took_s"] = round(took, 1)
        job["state"] = "done"
    except Exception as e:                     # noqa: BLE001 - the job reports it
        job["state"] = "cancelled" if str(e) == "cancelled" else "error"
        job["error"] = str(e)[:500]
    finally:
        job["finished"] = time.time()
        with lock:
            state["busy"] = None
            state["last_used"] = time.time()


def worker():
    while True:
        job = work.get()
        if job.get("cancel"):
            job["state"] = "cancelled"
            continue
        run(job)


def janitor():
    while True:
        time.sleep(10)
        # After making room, the card goes back to Strata as soon as the readings are done, not after the idle time.
        idle = 5 if state["made_room"] else IDLE_S
        if state["model"] is not None and not state["busy"] and work.empty() and time.time() - state["last_used"] > idle:
            state["made_room"] = False
            unload()
        cutoff = time.time() - 3600
        for jid in [j for j, v in jobs.items() if v.get("finished", time.time()) < cutoff]:
            jobs.pop(jid, None)


def progress(job):
    if job["state"] == "done":
        return 1.0
    if job["state"] != "working":
        return 0.0
    groups = len(chunks(job["text"]))
    by_time = (time.time() - job["started"]) / job.get("estimate_s", 60)
    by_groups = job.get("done_groups", 0) / groups
    return round(min(0.97, max(by_time, by_groups)), 3)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, code, obj):
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return {}

    def do_GET(self):
        if self.path == "/health":
            return self.reply(200, {"ok": True, "loaded": state["model"] is not None, "device": state["device"], "queue": work.qsize(),
                                    "busy": state["busy"], "seconds_per_audio_second": state["pace"], "fake": FAKE})
        if self.path.startswith("/job?id="):
            job = jobs.get(self.path.split("=", 1)[1])
            if not job:
                return self.reply(404, {"error": "unknown job"})
            ahead = sum(1 for j in list(work.queue) if j["created"] < job["created"]) if job["state"] == "queued" else 0
            return self.reply(200, {k: job.get(k) for k in ("id", "state", "audio_s", "took_s", "error", "out")} | {"progress": progress(job), "ahead": ahead})
        self.reply(404, {"error": "not found"})

    def do_POST(self):
        b = self.body()
        if self.path == "/speak":
            text = b.get("text") if isinstance(b.get("text"), str) else ""
            out = b.get("out") if isinstance(b.get("out"), str) else ""
            fmt = b.get("format") if b.get("format") in ("wav", "mp3") else "wav"
            reference = b.get("reference") if isinstance(b.get("reference"), str) else None
            if not text.strip() or len(text) > MAX_TEXT:
                return self.reply(400, {"error": f"text is required, up to {MAX_TEXT} characters"})
            if not out or not os.path.isabs(out) or not within(os.path.dirname(out) or "/", OUT_ROOTS):
                return self.reply(400, {"error": "out must be a full path inside " + ", ".join(OUT_ROOTS)})
            if reference and not os.path.isfile(reference):
                return self.reply(400, {"error": "the reference recording is missing"})
            job = {"id": secrets.token_hex(6), "state": "queued", "created": time.time(), "text": text.strip(), "out": out, "format": fmt,
                   "description": (b.get("description") or "").strip()[:300] or None, "reference": reference,
                   "style": (b.get("style") or "").strip()[:200] or None,
                   "transcript": ((b.get("transcript") or "").strip()[:2000] or None) if reference else None,
                   "cfg": float(b.get("cfg") or 2.0), "steps": int(b.get("steps") or 10)}
            jobs[job["id"]] = job
            work.put(job)
            return self.reply(200, {"job": job["id"]})
        if self.path == "/cancel":
            job = jobs.get(b.get("id"))
            if not job:
                return self.reply(404, {"error": "unknown job"})
            job["cancel"] = True
            return self.reply(200, {"ok": True})
        if self.path == "/unload":
            unload()
            return self.reply(200, {"ok": True, "loaded": state["model"] is not None})
        self.reply(404, {"error": "not found"})


if __name__ == "__main__":
    threading.Thread(target=worker, daemon=True).start()
    threading.Thread(target=janitor, daemon=True).start()
    print(f"voice engine on 127.0.0.1:{PORT} ({'fake' if FAKE else MODEL})", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
    sys.exit(0)
