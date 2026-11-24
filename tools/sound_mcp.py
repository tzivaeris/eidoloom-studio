#!/usr/bin/env python3
"""An MCP server (stdio) for sound effects: make_sound, through Studio's local sound engine (tools/sound_engine.py,
MOSS-SoundEffect v2.0). It writes an MP3 or WAV at a full path inside SOUND_ROOTS and reports progress to a caller that
asks with a progress token. Nothing leaves the machine.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

ENGINE = os.environ.get("SOUND_URL", "http://127.0.0.1:8190").rstrip("/")
ROOTS = [os.path.realpath(os.path.expanduser(p)) for p in os.environ.get("SOUND_ROOTS", "~/Projects").split(":") if p]
WAIT_S = int(os.environ.get("SOUND_WAIT_S", "1700"))
PROTOCOL = "2025-06-18"
TOOLS = [
    {"name": "make_sound",
     "description": "Make a sound effect or ambience with MOSS-SoundEffect v2.0 on this machine and save it as an MP3 (or WAV) at a "
                    "full path in the project: nature, city, animals, objects, people's actions, short percussive or musical "
                    "stings, up to 30 seconds. Describe the sound in English, concretely, e.g. \"small waves lapping on a pebble "
                    "beach, gulls far away\" or \"a soft wooden click, like a toy block\". For songs or background music use "
                    "generate_music instead. Each sound takes about 30 s, and a call may first unload the chat model to make room on "
                    "the graphics card, which then has to load again: so when you need several sounds, make them in ONE call "
                    "with `sounds` (a list), never one call per sound.",
     "inputSchema": {"type": "object", "properties": {
         "description": {"type": "string", "description": "The sound, in English, concretely"},
         "seconds": {"type": "number", "description": "0.5 to 30; default 5"},
         "path": {"type": "string", "description": "Full path of the file to write, ending in .mp3 or .wav, inside the project"},
         "steps": {"type": "integer", "description": "10-200 solver steps; default 100 (fewer is faster and rougher)"},
         "sounds": {"type": "array", "description": "Several sounds in one call, made one after another without unloading: each "
                    "{description, path, seconds?, steps?}. Use instead of description and path.",
                    "items": {"type": "object", "properties": {"description": {"type": "string"}, "path": {"type": "string"},
                                                                 "seconds": {"type": "number"}, "steps": {"type": "integer"}},
                              "required": ["description", "path"]}}}}},
]
STATE_WORDS = {"queued": "Waiting for the sound engine", "waiting_card": "Waiting for the graphics card",
               "making_room": "Waiting for the graphics card: unloading Strata to make room",
               "loading": "Loading the sound model", "working": "Making the sound"}


def engine(path, body=None, timeout=15):
    req = urllib.request.Request(ENGINE + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"}, method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def tell_progress(token, percent, message):
    if token is None:
        return
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/progress",
                                 "params": {"progressToken": token, "progress": percent, "total": 100, "message": message}}, ensure_ascii=False) + "\n")
    sys.stdout.flush()


MAX_BATCH = 12


def make_sounds(args, token=None):
    """One sound, or a list of them made in one go: all jobs are queued at once, so the engine loads (and makes room) once."""
    batch = args.get("sounds")
    if batch is None:
        return make_sound(args, token)
    if not isinstance(batch, list) or not batch or len(batch) > MAX_BATCH or not all(isinstance(b, dict) for b in batch):
        return f"Error: sounds must be a list of 1 to {MAX_BATCH} objects with description and path.", True
    jobs = []
    for n, item in enumerate(batch, 1):
        job, error = start_job(item)
        if error:
            return f"Error in sound {n} ({str(item.get('path'))[-60:]}): {error[7:] if error.startswith('Error: ') else error}" + (
                f" Sounds 1-{n - 1} are still being made." if n > 1 else ""), True
        jobs.append((job, item["path"]))
    lines, failed = [], False
    for n, (job, path) in enumerate(jobs, 1):
        def tell(percent, message, n=n):
            tell_progress(token, round(((n - 1) * 100 + percent) / len(jobs)), f"Sound {n}/{len(jobs)}: {message}")
        text, bad = follow(job, path, tell)
        failed = failed or bad
        lines.append(f"{n}. {text}")
    return "\n".join(lines), failed and all(line.split(". ", 1)[1].startswith("Error") for line in lines)


def make_sound(args, token=None):
    job, error = start_job(args)
    if error:
        return error, True
    return follow(job, args["path"], lambda percent, message: tell_progress(token, percent, message))


def start_job(args):
    """Checks one sound's arguments and queues it; (job id, None) or (None, "Error: ...")."""
    description, path = args.get("description"), args.get("path")
    if not isinstance(description, str) or not description.strip():
        return None, "Error: description is required."
    if not isinstance(path, str) or not os.path.isabs(path) or not path.lower().endswith((".mp3", ".wav")):
        return None, "Error: path must be a full path ending in .mp3 or .wav."
    real = os.path.realpath(os.path.dirname(path))
    if not any(real == r or real.startswith(r + os.sep) for r in ROOTS):
        return None, f"Error: the file must be inside {', '.join(ROOTS)}."
    try:
        seconds = float(args.get("seconds") or 5)
        steps = int(args.get("steps") or 100)
    except (TypeError, ValueError):
        return None, "Error: seconds and steps must be numbers."
    body = {"prompt": description.strip(), "out": path, "format": "mp3" if path.lower().endswith(".mp3") else "wav", "seconds": seconds, "steps": steps}
    try:
        return engine("/make", body)["job"], None
    except urllib.error.HTTPError as exc:
        try:
            detail = json.load(exc).get("error")
        except ValueError:
            detail = str(exc)
        return None, f"Error: {detail}"
    except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
        return None, f"Error: the sound engine at {ENGINE} did not take the job ({exc}). Is the strata-sound service running?"


def follow(job, path, tell):
    """Waits for one queued job, reporting its progress through tell(percent, message); (text, failed)."""
    deadline = time.time() + WAIT_S
    while time.time() < deadline:
        time.sleep(1)
        try:
            st = engine(f"/job?id={job}")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return "Error: the sound engine restarted and lost this job; call make_sound again.", True
            continue
        except (urllib.error.URLError, OSError, ValueError):
            continue
        if st["state"] == "queued":
            tell(0, "Waiting for the sounds before it…")
        else:
            tell(round(100 * (st.get("progress") or 0)), STATE_WORDS.get(st["state"], st["state"]) + ("…" if st["state"] != "done" else ""))
        if st["state"] == "done":
            return f"Saved {st.get('audio_s') or '?'} s of sound in {path} (took {st.get('took_s')} s).", False
        if st["state"] in ("error", "cancelled"):
            return f"Error: the sound engine failed: {st.get('error')}", True
    return "Error: the sound took too long.", True


def answer(msg_id, result=None, error=None):
    reply = {"jsonrpc": "2.0", "id": msg_id}
    reply.update({"error": error} if error else {"result": result})
    sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        method, msg_id = msg.get("method"), msg.get("id")
        if msg_id is None:
            continue
        if method == "initialize":
            answer(msg_id, {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}}, "serverInfo": {"name": "studio-sounds", "version": "1.0"}})
        elif method == "ping":
            answer(msg_id, {})
        elif method == "tools/list":
            answer(msg_id, {"tools": TOOLS})
        elif method == "tools/call":
            params = msg.get("params") or {}
            if params.get("name") != "make_sound":
                answer(msg_id, error={"code": -32602, "message": "unknown tool"})
                continue
            text, failed = make_sounds(params.get("arguments") or {}, (params.get("_meta") or {}).get("progressToken"))
            answer(msg_id, {"content": [{"type": "text", "text": text}], "isError": failed})
        else:
            answer(msg_id, error={"code": -32601, "message": "method not found"})


if __name__ == "__main__":
    main()
