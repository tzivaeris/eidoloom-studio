#!/usr/bin/env python3
"""An MCP server (stdio) for Studio's voices: list_voices and speak, through the local voice engine (tools/voice_engine.py).

Voices are Studio's shared cast (VOICES_FILE, data/voices/voices.json): each has a name, the description it was made
from (or the recording it was made from), and its own reference recording, so a voice sounds the same every time.
speak writes a WAV or MP3 file at a full path inside VOICE_ROOTS, in a saved voice, a described one, or the voice of a
reference recording; save_voice keeps a recording as a named voice. Recordings are copied only with the speaker's
consent, which save_voice records. Nothing leaves the machine.
"""
import importlib.util
import secrets
import shutil
import tempfile
import json
import os
import sys
import time
import urllib.error
import urllib.request

_here = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("voice_audio_edit", os.path.join(_here, "audio_edit.py"))
audio_edit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audio_edit)

ENGINE = os.environ.get("VOICE_URL", "http://127.0.0.1:8189").rstrip("/")
VOICES_FILE = os.environ.get("VOICES_FILE", "")
ROOTS = [os.path.realpath(os.path.expanduser(p)) for p in os.environ.get("VOICE_ROOTS", "~/Projects").split(":") if p]
WAIT_S = int(os.environ.get("VOICE_WAIT_S", "1700"))
PROTOCOL = "2025-06-18"
CONSENT_RULE = ("Copy a voice only from a recording of the user's own voice, of a voice made by a machine (designed here or "
                "elsewhere with the right to reuse it), or of a person who gave written permission. Never copy a voice from a "
                "hosted voice library of another service, a public figure, or anyone who has not agreed; ask the user when unsure.")
LANGS = {"el": "Greek", "en": "English"}
TOOLS = [
    {"name": "list_voices",
     "description": "List Studio's voices: each one's name, what it sounds like, its usual style and where it came from (designed "
                    "from a description, or copied from a recording). Use one of these names with speak.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "speak",
     "description": "Read text aloud and save it as an audio file (MP3 by default) at a full path in the project. Greek and 29 "
                    "other languages work; write the text exactly as it should be read, with numbers in words where it matters. "
                    "It takes about three times the length of the speech (a minute of text, about three minutes), so read long "
                    "texts in parts of a few paragraphs. Give one of: voice (a saved voice: it sounds the same in every call, "
                    "because each copies the recording kept with it), description (a new voice from words: it changes between "
                    "calls, so use it only for one-offs), or reference (the voice of a recording in the project, 1 to 30 s of "
                    "clean speech, 3 to 15 s is best; add reference_transcript, the exact words spoken in it, for the closest "
                    "copy). " + CONSENT_RULE + " For a voice used again, keep it with save_voice.",
     "inputSchema": {"type": "object", "properties": {
         "text": {"type": "string", "description": "What to read, up to 20000 characters"},
         "voice": {"type": "string", "description": "A voice name from list_voices"},
         "description": {"type": "string", "description": "Instead of voice: what the voice should sound like, e.g. 'a calm older woman, slow'"},
         "reference": {"type": "string", "description": "Instead of voice: full path of a recording (WAV, MP3, FLAC, OGG...) whose voice to copy"},
         "reference_start": {"type": "number", "description": "Optional: where the speech starts in the reference, in seconds"},
         "reference_end": {"type": "number", "description": "Optional: where the speech ends in the reference, in seconds"},
         "reference_transcript": {"type": "string", "description": "Optional: the exact words spoken in the reference (in that part), for the closest copy"},
         "style": {"type": "string", "description": "Optional delivery for this reading, e.g. 'slightly faster, cheerful' or 'whispering'. "
                                                     "With a transcript the delivery otherwise follows the recording; a style overrides that."},
         "path": {"type": "string", "description": "Full path of the file to write, ending in .mp3 or .wav, inside the project"}},
         "required": ["text", "path"]}},
    {"name": "save_voice",
     "description": "Keep a recording as a named voice in Studio's cast, so speak can use it by name and it sounds the same in "
                    "every call and release. The recording (or the part from start to end) should be 3 to 15 s of one person's "
                    "clean speech, no music; at most 30 s. " + CONSENT_RULE + " Say in consent whose voice it is and why copying "
                    "it is allowed; Studio keeps that with the voice. A name already in the cast is refused: people change or "
                    "delete voices in Studio's Voices view.",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string", "description": "The voice's name, e.g. 'Mello'"},
         "recording": {"type": "string", "description": "Full path of the recording in the project"},
         "start": {"type": "number", "description": "Optional: where the speech starts, in seconds"},
         "end": {"type": "number", "description": "Optional: where the speech ends, in seconds"},
         "transcript": {"type": "string", "description": "The exact words spoken in that part; strongly recommended (closest copy)"},
         "lang": {"type": "string", "enum": ["el", "en"], "description": "The language spoken in the recording"},
         "description": {"type": "string", "description": "What it sounds like, for people and agents choosing a voice"},
         "style": {"type": "string", "description": "Optional usual delivery for every reading"},
         "consent": {"type": "string", "description": "Whose voice it is and why copying it is allowed, e.g. 'the owner's approved "
                                                      "Mello v4 reference, a designed voice they own' or 'my own voice'"}},
         "required": ["name", "recording", "lang", "consent"]}},
]


def engine(path, body=None, timeout=15):
    req = urllib.request.Request(ENGINE + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"}, method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def voices():
    try:
        with open(VOICES_FILE, encoding="utf-8") as f:
            return json.load(f).get("voices", [])
    except (OSError, ValueError):
        return []


def sample_path(voice):
    return os.path.join(os.path.dirname(VOICES_FILE), "samples", voice["id"] + ".wav")


def within_roots(path):
    real = os.path.realpath(path)
    return any(real == r or real.startswith(r + os.sep) for r in ROOTS)


def origin_words(v):
    return "copied from a recording" if v.get("origin") == "recording" else "designed from a description"


def list_voices(_args):
    found = voices()
    if not found:
        return "No voices yet. Make some in Studio's Voices view, keep a recording with save_voice, or use speak with a description.", False
    lines = [f"- {v['name']} [{LANGS.get(v.get('lang') or 'el', v.get('lang'))}, {origin_words(v)}]: {v.get('description') or 'no description'}"
             + (f" (usual style: {v['style']})" if v.get("style") else "") for v in found]
    return (f"{len(found)} voices. The language in brackets is the one each voice was made in and sounds most natural in; "
            "pick a voice of the text's language:\n" + "\n".join(lines)), False


def cut_reference(path, start, end, work):
    """The recording (or its part) as a mono WAV the engine reads; returns (wav, seconds) or raises ValueError."""
    if not isinstance(path, str) or not os.path.isabs(path):
        raise ValueError("the recording must be a full path")
    if not within_roots(path) or not os.path.isfile(path):
        raise ValueError(f"there is no recording at {path} inside {', '.join(ROOTS)}")
    for x in (start, end):
        if x is not None and not isinstance(x, (int, float)):
            raise ValueError("start and end are seconds, as numbers")
    out = os.path.join(work, "reference.wav")
    try:
        return out, audio_edit.voice_reference(path, out, start, end)
    except audio_edit.EditError as exc:
        raise ValueError(str(exc)) from None


def save_voice(args):
    name = args.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 60:
        return "Error: give the voice a name of up to 60 characters.", True
    name = name.strip()
    consent = args.get("consent")
    if not isinstance(consent, str) or len(consent.strip()) < 8:
        return "Error: say in consent whose voice this is and why copying it is allowed. " + CONSENT_RULE, True
    lang = args.get("lang") if args.get("lang") in LANGS else None
    if not lang:
        return "Error: lang must be el or en.", True
    if not VOICES_FILE:
        return "Error: this tool server has no VOICES_FILE.", True
    found = voices()
    if any(v["name"].lower() == name.lower() for v in found):
        return f"Error: there is already a voice called {name}; choose another name, or change it in Studio's Voices view.", True
    work = tempfile.mkdtemp(prefix="studio-voice-")
    try:
        try:
            wav, seconds = cut_reference(args.get("recording"), args.get("start"), args.get("end"), work)
        except ValueError as exc:
            return f"Error: {exc}", True
        vid = secrets.token_hex(6)
        folder = os.path.dirname(VOICES_FILE)
        os.makedirs(os.path.join(folder, "samples"), exist_ok=True)
        shutil.copyfile(wav, os.path.join(folder, "samples", vid + ".wav"))
        text = lambda k, n: (args.get(k) or "").strip()[:n] if isinstance(args.get(k), str) else ""
        voice = {"id": vid, "name": name, "description": text("description", 300), "style": text("style", 200), "lang": lang,
                 "origin": "recording", "transcript": text("transcript", 2000), "consent": consent.strip()[:300],
                 "source": args["recording"], "seconds": seconds, "created_by": "agent", "created": time.time()}
        tmp = VOICES_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"voices": voices() + [voice]}, f, ensure_ascii=False, indent=1)
        os.replace(tmp, VOICES_FILE)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return (f"Saved {name!r} ({seconds} s of {os.path.basename(args['recording'])}"
            + (", with its transcript" if voice["transcript"] else ", without a transcript: add one in Studio's Voices view for a closer copy")
            + f"). Use speak with voice {name!r}; it now appears in list_voices and in Studio's Voices view."), False


def tell_progress(token, percent, message):
    """An MCP progress notification, when the caller asked for them with a progress token."""
    if token is None:
        return
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/progress",
                                 "params": {"progressToken": token, "progress": percent, "total": 100, "message": message}}, ensure_ascii=False) + "\n")
    sys.stdout.flush()


STATE_WORDS = {"queued": "Waiting for the voice engine", "waiting_card": "Waiting for the graphics card",
               "making_room": "Waiting for the graphics card: unloading Strata to make room", "loading": "Loading the voice model", "working": "Reading"}


def speak(args, token=None):
    text, path = args.get("text"), args.get("path")
    if not isinstance(text, str) or not text.strip():
        return "Error: text is required.", True
    if not isinstance(path, str) or not os.path.isabs(path) or not path.lower().endswith((".mp3", ".wav")):
        return "Error: path must be a full path ending in .mp3 or .wav.", True
    if not within_roots(os.path.dirname(path)):
        return f"Error: the file must be inside {', '.join(ROOTS)}.", True
    body = {"text": text, "out": path, "format": "mp3" if path.lower().endswith(".mp3") else "wav"}
    name = (args.get("voice") or "").strip()
    used = "a voice made from the description"
    work = None
    if name:
        voice = next((v for v in voices() if v["name"].lower() == name.lower()), None)
        if not voice:
            return f"Error: there is no voice called {name!r}. " + list_voices({})[0], True
        if not os.path.isfile(sample_path(voice)):
            return f"Error: the recording of {voice['name']!r} is missing; make the voice again in Studio.", True
        style = args.get("style") or ("" if voice.get("transcript") else voice.get("style") or "")
        body.update(reference=sample_path(voice), style=style, transcript=voice.get("transcript") or "")
        used = f"the voice {voice['name']!r}"
    elif args.get("reference"):
        work = tempfile.mkdtemp(prefix="studio-voice-")
        try:
            wav, _seconds = cut_reference(args["reference"], args.get("reference_start"), args.get("reference_end"), work)
        except ValueError as exc:
            shutil.rmtree(work, ignore_errors=True)
            return f"Error: {exc}", True
        transcript = args.get("reference_transcript") if isinstance(args.get("reference_transcript"), str) else ""
        body.update(reference=wav, style=args.get("style") or "", transcript=transcript)
        used = f"the voice of {os.path.basename(args['reference'])}" + (" with its transcript" if transcript.strip() else "")
    elif args.get("description"):
        body["description"] = args["description"]
    else:
        return "Error: give voice (see list_voices), reference (a recording) or description.", True
    try:
        return wait_reading(body, path, used, token)
    finally:
        if work:
            shutil.rmtree(work, ignore_errors=True)


def wait_reading(body, path, used, token):
    try:
        job = engine("/speak", body)["job"]
    except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
        return f"Error: the voice engine at {ENGINE} did not take the job ({exc}).", True
    deadline = time.time() + WAIT_S
    while time.time() < deadline:
        time.sleep(2)
        try:
            state = engine(f"/job?id={job}")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:          # the engine restarted and lost the job: say so now, not after WAIT_S
                return "Error: the voice engine restarted and lost this reading; call speak again.", True
            continue
        except (urllib.error.URLError, OSError, ValueError):
            continue
        tell_progress(token, round(100 * (state.get("progress") or 0)), STATE_WORDS.get(state["state"], state["state"]) + ("…" if state["state"] != "done" else ""))
        if state["state"] == "done":
            return f"Saved {state.get('audio_s') or '?'} s of speech in {path}, read in {used} (took {state.get('took_s')} s).", False
        if state["state"] in ("error", "cancelled"):
            return f"Error: the voice engine failed: {state.get('error')}", True
    return "Error: the reading took too long; try a shorter text.", True


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
            answer(msg_id, {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}}, "serverInfo": {"name": "studio-voices", "version": "1.0"}})
        elif method == "ping":
            answer(msg_id, {})
        elif method == "tools/list":
            answer(msg_id, {"tools": TOOLS})
        elif method == "tools/call":
            params = msg.get("params") or {}
            fn = {"list_voices": list_voices, "speak": speak, "save_voice": save_voice}.get(params.get("name"))
            if not fn:
                answer(msg_id, error={"code": -32602, "message": "unknown tool"})
                continue
            token = (params.get("_meta") or {}).get("progressToken")
            text, failed = fn(params.get("arguments") or {}, token) if fn is speak else fn(params.get("arguments") or {})
            answer(msg_id, {"content": [{"type": "text", "text": text}], "isError": failed})
        else:
            answer(msg_id, error={"code": -32601, "message": "method not found"})


if __name__ == "__main__":
    main()
