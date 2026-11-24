#!/usr/bin/env python3
"""Studio's second music engine: HeartMuLa-oss-3B "happy new year" with HeartCodec-oss-20260123 (HeartMuLa team, Apache 2.0;
github.com/HeartMuLa/heartlib), behind a small local HTTP API on 127.0.0.1 like tools/sound_engine.py. ACE-Step 1.5 stays
in ComfyUI; tools/comfyui_mcp.py's generate_music sends a song here when the user (or the call) picks HeartMuLa.

HeartMuLa is a language model over 12.5 Hz codec frames: it writes the song frame by frame (from the style tags and the
lyrics) until it decides the song has ended or reaches the length asked for, then HeartCodec turns the frames into 48 kHz
stereo. The two run one after the other (heartlib's lazy load), so the card holds only one of them at a time.

  GET  /health
  POST /make   {style, lyrics, seconds, out, format: mp3|wav, cfg, temperature, topk, seed}  -> {job}
  GET  /job?id= -> {state: queued|waiting_card|making_room|loading|writing|decoding|done|error|cancelled, progress, audio_s, out, error}
  POST /cancel {job}
  POST /unload

MUSIC_FAKE=1 writes a tone instead (tests, no PyTorch). Runs in ~/.local/opt/music/.venv (ComfyUI's AMD PyTorch through a
.pth file, heartlib installed with --no-deps); see sandbox/mcp-sound/README.md.
"""
import gc
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

PORT = int(os.environ.get("MUSIC_PORT", "8191"))
CKPT = os.path.expanduser(os.environ.get("MUSIC_CKPT", "~/.local/opt/music/ckpt"))
IDLE_S = int(os.environ.get("MUSIC_IDLE_S", "180"))
NEED_GB = float(os.environ.get("MUSIC_NEED_GB", "16"))   # measured: up to 15.6 GB reserved (2026-11-16, without expandable segments)
NEED_RAM_GB = float(os.environ.get("MUSIC_NEED_RAM_GB", "20"))   # system memory the load takes; Strata can hold 43 GB of 62


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

CARD_WAIT_S = int(os.environ.get("MUSIC_CARD_WAIT_S", "900"))
OUT_ROOTS = [os.path.realpath(os.path.expanduser(p)) for p in os.environ.get("MUSIC_OUT_ROOTS", "~/Projects").split(":") if p]
MANAGER_URL = os.environ.get("MANAGER_URL", "http://127.0.0.1:8787").rstrip("/")
MAKE_ROOM = os.environ.get("MUSIC_MAKE_ROOM", "1") == "1"
FAKE = os.environ.get("MUSIC_FAKE") == "1"
MAX_SECONDS = 240                 # heartlib's own limit (max_audio_length_ms 240000)
MIN_SHARE = 0.9                   # a song may end by itself only after this share of the length asked for
EARLY_TRIES = 12                  # endings drawn again before that, before one is accepted
FRAME_MS = 80                     # 12.5 frames a second

jobs, work = {}, queue.Queue()
state = {"pipe": None, "last_used": 0.0, "busy": None, "made_room": False,
         "pace": 0.8, "decode_pace": 0.3}     # seconds of work per second of song, for the estimates
lock = threading.Lock()


class Cancelled(Exception):
    pass


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
    if state["pipe"] is not None:
        return state["pipe"]
    if FAKE:
        state["pipe"] = "fake"
        return "fake"
    os.environ.setdefault("TORCHDYNAMO_DISABLE", "1")      # heartlib calls torch.compile; it is NVIDIA-tuned and slow to start here
    import torch
    from heartlib import HeartMuLaGenPipeline
    if not torch.cuda.is_available():
        raise RuntimeError("no graphics card is available to PyTorch")
    deadline = time.time() + CARD_WAIT_S
    while free_card_gb() < NEED_GB:
        if job.get("cancel"):
            raise Cancelled()
        if time.time() > deadline:
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
    state["pipe"] = HeartMuLaGenPipeline.from_pretrained(
        CKPT, device=torch.device("cuda"), dtype={"mula": torch.bfloat16, "codec": torch.float32}, version="3B", lazy_load=True)
    return state["pipe"]


def unload():
    with lock:
        if state["pipe"] is None or state["busy"]:
            return
        state["pipe"] = None
    if not FAKE and work.empty() and not state["busy"]:
        # As the sound engine: PyTorch keeps its card context and the loaded weights' RAM after a model is dropped,
        # so the process ends and systemd starts an empty one (Restart=on-failure).
        print("unloaded; ending the process so its RAM and card memory go back", flush=True)
        os._exit(3)


def math_attention(module):
    """Runs module's attention with PyTorch's plain (math) kernel. HeartMuLa's decoder has 384-wide
    heads, and on this AMD card (ROCm 7.2, PyTorch 2.14) the efficient-attention kernel returns NaN
    for them, so codebooks 1-7 of every frame were random and every song came out garbled
    (2026-11-16). The 3B backbone (128-wide heads) is right with the fast kernel and keeps it."""
    if getattr(module, "_studio_math", False):
        return
    from torch.nn.attention import SDPBackend, sdpa_kernel
    forward = module.forward

    def math_forward(*args, **kwargs):
        with sdpa_kernel([SDPBackend.MATH]):
            return forward(*args, **kwargs)

    module.forward = math_forward
    module._studio_math = True


def write_song(path, job):
    """Writes the song as a WAV at path; the frames loop is heartlib's _forward with progress and Stop."""
    if FAKE:
        import math
        import struct
        import wave
        rate = 16000
        job["state"] = "writing"
        samples = [int(6000 * math.sin(2 * math.pi * 262 * i / rate)) for i in range(int(rate * job["seconds"]))]
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(struct.pack(f"<{len(samples)}h", *samples))
        return
    import torch
    pipe = state["pipe"]
    torch.manual_seed(job["seed"])
    inputs = pipe.preprocess({"tags": job["style"], "lyrics": job["lyrics"]}, cfg_scale=job["cfg"])
    job["state"], job["phase_started"] = "writing", time.time()
    mula, dev, dtype = pipe.mula, pipe.mula_device, pipe.mula_dtype
    math_attention(mula.decoder)
    tokens, mask = inputs["tokens"].to(dev), inputs["tokens_mask"].to(dev)
    pos = inputs["pos"].to(dev)
    frames, empty, width = [], pipe.config.empty_id, pipe._parallel_number
    max_frames = int(job["seconds"] * 1000) // FRAME_MS
    job["max_frames"] = max_frames
    with torch.no_grad():
        mula.setup_caches(2 if job["cfg"] != 1.0 else 1)
        with torch.autocast(device_type=dev.type, dtype=dtype):
            token = mula.generate_frame(tokens=tokens, tokens_mask=mask, input_pos=pos, temperature=job["temperature"],
                                        topk=job["topk"], cfg_scale=job["cfg"], continuous_segments=inputs["muq_embed"].to(dev),
                                        starts=inputs["muq_idx"])
        frames.append(token[0:1, ])
        for i in range(max_frames):
            if job.get("cancel"):
                raise Cancelled()
            padded = torch.ones((token.shape[0], width), device=dev, dtype=torch.long) * empty
            padded[:, :-1] = token
            padded = padded.unsqueeze(1)
            padded_mask = torch.ones_like(padded, dtype=torch.bool)
            padded_mask[..., -1] = False
            # HeartMuLa decides itself when a song ends, and with tags alone it often ends after a few seconds (3 s and
            # 12.7 s of a 90 s theme on 2026-11-17). Before most of the asked length, an ending is drawn again (the same
            # position overwrites its cache entry), a little warmer each time; only after EARLY_TRIES does it end there.
            for attempt in range(EARLY_TRIES + 1):
                with torch.autocast(device_type=dev.type, dtype=dtype):
                    token = mula.generate_frame(tokens=padded, tokens_mask=padded_mask, input_pos=pos[..., -1:] + i + 1,
                                                temperature=min(2.0, job["temperature"] + 0.1 * attempt), topk=job["topk"],
                                                cfg_scale=job["cfg"], continuous_segments=None, starts=None)
                ended = bool(torch.any(token[0:1, :] >= pipe.config.audio_eos_id))
                if not ended or len(frames) >= max_frames * MIN_SHARE:
                    break
                job["redrawn"] = job.get("redrawn", 0) + 1
            if ended:
                if len(frames) < max_frames * MIN_SHARE:
                    job["ended_early"] = True
                break
            frames.append(token[0:1, ])
            job["frames"] = len(frames)
        codes = torch.stack(frames).permute(1, 2, 0).squeeze(0).cpu()
        written, count = time.time() - job["phase_started"], len(frames)
        if count > 25:
            state["pace"] = round(0.6 * state["pace"] + 0.4 * written / (count * FRAME_MS / 1000), 2)
        # HeartMuLa off the card before the codec comes on: heartlib's _unload drops only its own reference, so this
        # function's must go too, with the KV caches (12.7 GB stayed on the card and the codec ran out of memory).
        try:
            mula.reset_caches()
        except RuntimeError:
            pass
        del mula, tokens, mask, pos, token, frames, inputs
        padded = padded_mask = None
        pipe._unload()
        job["state"], job["phase_started"] = "decoding", time.time()
        job["decode_estimate_s"] = max(5.0, count * FRAME_MS / 1000 * state["decode_pace"])
        wav = pipe.codec.detokenize(codes.to(pipe.codec_device)).to(torch.float32).cpu()
        decoded = time.time() - job["phase_started"]
        state["decode_pace"] = round(0.6 * state["decode_pace"] + 0.4 * decoded / max(1.0, count * FRAME_MS / 1000), 3)
        pipe._unload()
    # torchaudio.save needs torchcodec, which has no ROCm build here, so soundfile writes it: channels x samples -> samples x channels.
    import soundfile
    if wav.ndim == 3:
        wav = wav[0]
    elif wav.ndim == 1:
        wav = wav.unsqueeze(0)
    peak = float(wav.abs().max())
    if peak > 0.89:
        wav = wav * (0.89 / peak)                        # never clip; leave quieter songs as they are
    soundfile.write(path, wav.clamp(-1, 1).numpy().T, 48000, subtype="PCM_16")


def duration(path):
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                             capture_output=True, text=True, timeout=30)
        return round(float(out.stdout.strip()), 1)
    except (ValueError, OSError, subprocess.SubprocessError):
        return None


def drop_pipe():
    """Frees the card after a failed job, so a retry starts clean."""
    pipe, state["pipe"] = state["pipe"], None
    if not FAKE:
        import torch
        if pipe is not None and pipe != "fake":
            pipe._mula = pipe._codec = None
        gc.collect()
        torch.cuda.empty_cache()


def run(job):
    with lock:
        state["busy"] = job["id"]
    try:
        try:
            make(job)
        except Cancelled:
            raise
        except Exception as exc:                  # noqa: BLE001 - a card filled by someone else: make room once and retry
            if "out of memory" not in str(exc).lower() or job.get("cancel"):
                raise
            print(f"out of card memory ({exc}); making room and trying again", flush=True)
            drop_pipe()
            if not (MAKE_ROOM and make_room(job)):
                raise RuntimeError("the graphics card ran out of memory and there was nothing idle to unload (Strata is answering "
                                   "a request, or other programs hold the card); try again in a moment") from exc
            make(job)
    except Cancelled:
        job["state"], job["error"] = "cancelled", "stopped"
        drop_pipe()
    except Exception as e:                        # noqa: BLE001 - the job reports it
        job["state"], job["error"] = "error", str(e)[:500]
        print(f"job {job['id']} failed: {e}", flush=True)
        drop_pipe()
    finally:
        job["finished"] = time.time()
        with lock:
            state["busy"] = None
            state["last_used"] = time.time()


def make(job):
    load(job)
    job["started"] = time.time()
    os.makedirs(os.path.dirname(job["out"]), exist_ok=True)
    wav = job["out"] if job["format"] == "wav" else job["out"] + ".tmp.wav"
    try:
        write_song(wav, job)
        if job["format"] != "wav":
            done = subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", wav, "-codec:a", "libmp3lame", "-qscale:a", "0", job["out"]],
                                  capture_output=True, text=True)
            if done.returncode:
                raise RuntimeError("ffmpeg could not make the MP3: " + done.stderr[-300:])
    finally:
        if wav != job["out"] and os.path.exists(wav):
            os.remove(wav)
    job["took_s"] = round(time.time() - job["started"], 1)
    job["audio_s"] = duration(job["out"])
    if not FAKE:
        import torch
        job["peak_gb"] = round(torch.cuda.max_memory_reserved() / 1e9, 1)
        print(f"made {job['audio_s']} s of song in {job['took_s']} s, peak {job['peak_gb']} GB on the card", flush=True)
    job["state"] = "done"


def progress(job):
    """Writing is 0-85 %, by frames against the length asked for (a song may end sooner); decoding is the rest, by time."""
    if job["state"] == "done":
        return 1.0
    if job["state"] == "writing":
        if FAKE:
            return 0.5
        return round(0.85 * min(1.0, job.get("frames", 0) / max(1, job.get("max_frames", 1))), 3)
    if job["state"] == "decoding":
        return round(0.85 + 0.14 * min(1.0, (time.time() - job["phase_started"]) / job.get("decode_estimate_s", 30)), 3)
    return 0.0


def worker():
    while True:
        job = work.get()
        if job.get("cancel"):
            job["state"], job["finished"] = "cancelled", time.time()
            continue
        run(job)


def janitor():
    while True:
        time.sleep(10)
        idle = 5 if state["made_room"] else IDLE_S         # after making room, the card goes back to Strata at once
        if state["pipe"] is not None and not state["busy"] and work.empty() and time.time() - state["last_used"] > idle:
            state["made_room"] = False
            unload()
        cutoff = time.time() - 3600
        for jid in [j for j, v in jobs.items() if v.get("finished", time.time()) < cutoff]:
            jobs.pop(jid, None)


def number(b, key, default, lo, hi, kind):
    value = kind(b.get(key) if b.get(key) is not None else default)
    if not lo <= value <= hi:
        raise ValueError(f"{key} must be {lo} to {hi}")
    return value


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
            return self.reply(200, {"ok": True, "engine": "heartmula", "loaded": state["pipe"] is not None, "queue": work.qsize(),
                                    "busy": state["busy"], "seconds_per_song_second": state["pace"], "fake": FAKE,
                                    "installed": FAKE or os.path.isdir(os.path.join(CKPT, "HeartMuLa-oss-3B"))})
        if self.path.startswith("/job?id="):
            job = jobs.get(self.path.split("=", 1)[1])
            if not job:
                return self.reply(404, {"error": "unknown job"})
            out = {k: job.get(k) for k in ("id", "state", "audio_s", "took_s", "error", "out", "seed", "redrawn", "ended_early")}
            return self.reply(200, out | {"progress": progress(job)})
        self.reply(404, {"error": "not found"})

    def do_POST(self):
        b = self.body()
        if self.path == "/make":
            style = b.get("style") if isinstance(b.get("style"), str) else ""
            lyrics = b.get("lyrics") if isinstance(b.get("lyrics"), str) else ""
            out = b.get("out") if isinstance(b.get("out"), str) else ""
            fmt = b.get("format") if b.get("format") in ("wav", "mp3") else "mp3"
            try:
                seconds = number(b, "seconds", 60, 5, MAX_SECONDS, float)
                cfg = number(b, "cfg", 1.5, 1.0, 5.0, float)
                temperature = number(b, "temperature", 1.0, 0.1, 2.0, float)
                topk = number(b, "topk", 50, 1, 500, int)
                seed = number(b, "seed", secrets.randbelow(2 ** 32), 0, 2 ** 63 - 1, int)
            except (TypeError, ValueError) as exc:
                return self.reply(400, {"error": str(exc) if "must be" in str(exc) else "seconds, cfg, temperature, topk and seed must be numbers"})
            if not style.strip() or len(style) > 1000:
                return self.reply(400, {"error": "style is required, up to 1000 characters"})
            if len(lyrics) > 6000:
                return self.reply(400, {"error": "lyrics may be up to 6000 characters"})
            if not out or not os.path.isabs(out) or not within(os.path.dirname(out) or "/", OUT_ROOTS):
                return self.reply(400, {"error": "out must be a full path inside " + ", ".join(OUT_ROOTS)})
            job = {"id": secrets.token_hex(6), "state": "queued", "created": time.time(), "style": style.strip(),
                   "lyrics": lyrics.strip() or "[instrumental]", "out": out, "format": fmt, "seconds": seconds, "cfg": cfg,
                   "temperature": temperature, "topk": topk, "seed": seed}
            jobs[job["id"]] = job
            work.put(job)
            return self.reply(200, {"job": job["id"], "seed": seed})
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
    print(f"music engine on 127.0.0.1:{PORT} ({'fake' if FAKE else CKPT})", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
    sys.exit(0)
