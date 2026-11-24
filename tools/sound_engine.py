#!/usr/bin/env python3
"""Studio's sound-effects engine: MOSS-SoundEffect v2.0 (OpenMOSS, Apache 2.0; 1.3B DiT, 48 kHz, up to 30 s) behind a small
local HTTP API on 127.0.0.1, like tools/voice_engine.py. It loads the model when a job needs it and unloads after
SOUND_IDLE_S idle seconds. When Strata holds the graphics card (Studio's "Strata first" setting) it unloads an idle Strata
through the Ollama Manager, makes the sounds, and gives the card back straight after.

  GET  /health
  POST /make   {prompt, seconds, out, format: mp3|wav, steps}  -> {job}
  GET  /job?id= -> {state: queued|waiting_card|making_room|loading|working|done|error|cancelled, progress, audio_s, out, error}
  POST /cancel {job}
  POST /unload

SOUND_FAKE=1 writes a tone instead (tests, no PyTorch). Runs in ~/.local/opt/sound/.venv (ComfyUI's AMD PyTorch through a
.pth file, MOSS's own package from github.com/OpenMOSS/MOSS-TTS); see sandbox/mcp-sound/README.md.
"""
import json
import os
import queue
import secrets
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("SOUND_PORT", "8190"))
MODEL = os.path.expanduser(os.environ.get("SOUND_MODEL", "~/.local/opt/sound/models/MOSS-SoundEffect-v2.0"))
IDLE_S = int(os.environ.get("SOUND_IDLE_S", "180"))
NEED_GB = float(os.environ.get("SOUND_NEED_GB", "11"))   # measured: about 9 GB allocated while making a sound
NEED_RAM_GB = float(os.environ.get("SOUND_NEED_RAM_GB", "16"))   # system memory the load takes; Strata can hold 43 GB of 62


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

CARD_WAIT_S = int(os.environ.get("SOUND_CARD_WAIT_S", "900"))
OUT_ROOTS = [os.path.realpath(os.path.expanduser(p)) for p in os.environ.get("SOUND_OUT_ROOTS", "~/Projects").split(":") if p]
MANAGER_URL = os.environ.get("MANAGER_URL", "http://127.0.0.1:8787").rstrip("/")
MAKE_ROOM = os.environ.get("SOUND_MAKE_ROOM", "1") == "1"
OFFLOAD = os.environ.get("SOUND_OFFLOAD", "1") == "1"
FAKE = os.environ.get("SOUND_FAKE") == "1"
MAX_SECONDS = 30

jobs, work = {}, queue.Queue()
state = {"model": None, "last_used": 0.0, "busy": None, "pace": 1.5, "made_room": False}   # pace: seconds of work per second of sound
lock = threading.Lock()


def within(path, roots):
    real = os.path.realpath(path)
    return any(real == r or real.startswith(r + os.sep) for r in roots)


def free_card_gb():
    import torch
    free, _ = torch.cuda.mem_get_info()
    return (free + torch.cuda.memory_reserved()) / 1e9


def manager(path, body=None, timeout=20):
    req = urllib.request.Request(MANAGER_URL + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"}, method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def make_room(job):
    """Unloads an idle Strata (never one answering a request); True once it is gone."""
    try:
        status = manager("/api/strata/status")
        loaded = bool(status.get("running") or status.get("server_processes"))
        if not loaded or int((status.get("lifecycle") or {}).get("active_requests") or 0):
            return False
        job["state"] = "making_room"
        print("unloading Strata to make room on the graphics card", flush=True)
        manager("/api/strata/stop", {}, timeout=120)
        deadline = time.time() + 120
        while time.time() < deadline:
            st = manager("/api/strata/status")
            if not (st.get("running") or st.get("server_processes")):
                break
            time.sleep(1)
        state["made_room"] = True
        return True
    except Exception as exc:                  # noqa: BLE001 - no Manager: wait as before
        print(f"could not ask the Manager to unload Strata: {exc}", flush=True)
        return False


def load(job):
    if state["model"] is not None:
        return state["model"]
    if FAKE:
        state["model"] = "fake"
        return "fake"
    os.environ.setdefault("TORCHDYNAMO_DISABLE", "1")       # torch.compile + CUDA graphs are NVIDIA-only here
    import torch
    from moss_soundeffect_v2 import MossSoundEffectPipeline
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
    pipe = MossSoundEffectPipeline.from_pretrained(MODEL, torch_dtype=torch.bfloat16, device="cuda")
    if OFFLOAD and hasattr(pipe, "engine"):
        # DiffSynth moves each part (text encoder, DiT, VAE) to the card only for its own stage, so the peak is the
        # largest part plus its work instead of all of them (19.6 GB reserved with everything on the card).
        pipe.engine.vram_management_enabled = True
        pipe.engine.load_models_to_device([])
    state["model"] = pipe
    return pipe


def unload():
    with lock:
        if state["model"] is None or state["busy"]:
            return
        state["model"] = None
    if not FAKE and work.empty() and not state["busy"]:
        # The process always ends: with per-stage offloading the model's parts sit in RAM (11.6 GB) and PyTorch keeps its
        # card context (1.1 GB) even after the model is dropped, and on 2026-11-12 that left Strata too little of both to
        # start ("Process exited with code 1"). systemd starts an empty engine again (Restart=on-failure).
        print("unloaded; ending the process so its RAM and card memory go back", flush=True)
        os._exit(3)


def write_wav(path, job):
    if FAKE:
        import math
        import struct
        import wave
        rate = 16000
        samples = [int(6000 * math.sin(2 * math.pi * 330 * i / rate)) for i in range(int(rate * job["seconds"]))]
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(struct.pack(f"<{len(samples)}h", *samples))
        return
    pipe = state["model"]
    audio = pipe(prompt=job["prompt"], seconds=job["seconds"], num_inference_steps=job["steps"], cfg_scale=4.0)
    # MOSS's save_audio goes through torchaudio, which now needs torchcodec (not built for this AMD PyTorch), so the
    # samples are written with soundfile: channels x samples -> samples x channels.
    import soundfile
    import torch
    wav = audio.detach().cpu()
    if wav.ndim == 3:
        wav = wav[0]
    elif wav.ndim == 1:
        wav = wav.unsqueeze(0)
    wav = wav.to(torch.float32)
    peak = float(wav.abs().max())
    if peak > 1e-4:
        wav = wav * (0.89 / peak)                 # peaks at -1 dB: MOSS's sounds come out quiet (-6 to -9 dB)
    soundfile.write(path, wav.clamp(-1, 1).numpy().T, int(getattr(pipe, "sample_rate", 48000)), subtype="PCM_16")


def duration(path):
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path], capture_output=True, text=True, timeout=30)
        return round(float(out.stdout.strip()), 1)
    except (ValueError, OSError, subprocess.SubprocessError):
        return None


def out_of_memory(exc):
    return "out of memory" in str(exc).lower()


def drop_model():
    """Frees the card after a failed job, so a retry starts clean."""
    state["model"] = None
    if not FAKE:
        import gc
        import torch
        gc.collect()
        torch.cuda.empty_cache()


def run(job):
    with lock:
        state["busy"] = job["id"]
    try:
        try:
            make(job)
        except Exception as exc:               # noqa: BLE001 - a card filled by someone else: make room once and retry
            if not out_of_memory(exc) or job.get("cancel"):
                raise
            print(f"out of card memory ({exc}); making room and trying again", flush=True)
            drop_model()
            if not (MAKE_ROOM and make_room(job)):
                raise RuntimeError("the graphics card ran out of memory and Strata could not be unloaded (it is answering a request); "
                                   "try again in a moment") from exc
            make(job)
    except Exception as e:                     # noqa: BLE001 - the job reports it
        job["state"] = "cancelled" if str(e) == "cancelled" else "error"
        job["error"] = str(e)[:500]
    finally:
        job["finished"] = time.time()
        with lock:
            state["busy"] = None
            state["last_used"] = time.time()


def make(job):
    """Loads the model if needed, makes the sound and writes the file."""
    load(job)
    job["state"], job["started"] = "working", time.time()
    job["estimate_s"] = max(3.0, job["seconds"] * state["pace"] * job["steps"] / 100)
    os.makedirs(os.path.dirname(job["out"]), exist_ok=True)
    wav = job["out"] if job["format"] == "wav" else job["out"] + ".tmp.wav"
    write_wav(wav, job)
    if job["format"] != "wav":
        done = subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", wav, "-codec:a", "libmp3lame", "-qscale:a", "2", job["out"]],
                              capture_output=True, text=True)
        os.remove(wav)
        if done.returncode:
            raise RuntimeError("ffmpeg could not make the MP3: " + done.stderr[-300:])
    took = time.time() - job["started"]
    job["audio_s"] = duration(job["out"])
    if job["audio_s"] and not FAKE:
        state["pace"] = round(0.7 * state["pace"] + 0.3 * took / job["audio_s"] * 100 / job["steps"], 2)
    job["took_s"] = round(took, 1)
    if not FAKE:
        import torch
        job["peak_gb"] = round(torch.cuda.max_memory_reserved() / 1e9, 1)
        print(f"made {job['audio_s']} s in {job['took_s']} s, peak {job['peak_gb']} GB on the card", flush=True)
    job["state"] = "done"


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
        idle = 5 if state["made_room"] else IDLE_S         # after making room, the card goes back to Strata at once
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
    return round(min(0.97, (time.time() - job["started"]) / job.get("estimate_s", 30)), 3)


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
            return self.reply(200, {"ok": True, "loaded": state["model"] is not None, "queue": work.qsize(), "busy": state["busy"],
                                    "seconds_per_sound_second": state["pace"], "fake": FAKE})
        if self.path.startswith("/job?id="):
            job = jobs.get(self.path.split("=", 1)[1])
            if not job:
                return self.reply(404, {"error": "unknown job"})
            return self.reply(200, {k: job.get(k) for k in ("id", "state", "audio_s", "took_s", "error", "out")} | {"progress": progress(job)})
        self.reply(404, {"error": "not found"})

    def do_POST(self):
        b = self.body()
        if self.path == "/make":
            prompt = b.get("prompt") if isinstance(b.get("prompt"), str) else ""
            out = b.get("out") if isinstance(b.get("out"), str) else ""
            fmt = b.get("format") if b.get("format") in ("wav", "mp3") else "mp3"
            try:
                seconds = float(b.get("seconds") or 5)
                steps = int(b.get("steps") or 100)
            except (TypeError, ValueError):
                return self.reply(400, {"error": "seconds and steps must be numbers"})
            if not prompt.strip() or len(prompt) > 2000:
                return self.reply(400, {"error": "prompt is required, up to 2000 characters"})
            if not 0.5 <= seconds <= MAX_SECONDS or not 10 <= steps <= 200:
                return self.reply(400, {"error": f"seconds must be 0.5 to {MAX_SECONDS}, steps 10 to 200"})
            if not out or not os.path.isabs(out) or not within(os.path.dirname(out) or "/", OUT_ROOTS):
                return self.reply(400, {"error": "out must be a full path inside " + ", ".join(OUT_ROOTS)})
            job = {"id": secrets.token_hex(6), "state": "queued", "created": time.time(), "prompt": prompt.strip(), "out": out,
                   "format": fmt, "seconds": seconds, "steps": steps}
            jobs[job["id"]] = job
            work.put(job)
            return self.reply(200, {"job": job["id"]})
        if self.path == "/cancel":
            job = jobs.get(b.get("job"))
            if job:
                job["cancel"] = True
            return self.reply(200, {"ok": bool(job)})
        if self.path == "/unload":
            self.reply(200, {"ok": True})
            threading.Timer(0.3, unload).start()        # after the answer: unloading ends the process
            return
        self.reply(404, {"error": "not found"})


if __name__ == "__main__":
    threading.Thread(target=worker, daemon=True).start()
    threading.Thread(target=janitor, daemon=True).start()
    print(f"sound engine on 127.0.0.1:{PORT} ({'fake' if FAKE else MODEL})", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
    sys.exit(0)
