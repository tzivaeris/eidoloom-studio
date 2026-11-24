#!/usr/bin/env python3
"""An MCP server (stdio) that gives the agent Studio's wave editor: audio_info, audio_waveform, edit_audio, split_audio.

The edits are the editor's own (tools/audio_edit.py), so the agent and the person in the editor have the same tools:
delete, keep, silence, insert silence, insert a part of another sound, move, fades, volume, normalize, speed, pitch,
reverse, reverb, echo and the effects. A list of edits runs in order, each one on the result of the one before, and the
result is written once. A file that is replaced goes to its project's trash (.strata-studio/trash), as when someone
saves in the editor. Results come with a waveform picture, so a model that can see can check its work.

Files are read and written only inside AUDIO_ROOTS (default: ~/Projects, ComfyUI's output folder, ~/Music).
"""
import base64
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import audio_edit as ae          # noqa: E402

ROOTS = [os.path.realpath(os.path.expanduser(p)) for p in
         os.environ.get("AUDIO_ROOTS", "~/Projects:~/.local/opt/comfyui/output:~/Music").split(":") if p.strip()]
FONT = os.environ.get("AUDIO_FONT", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
PROTOCOL = "2025-06-18"
MAX_EDITS = 40
OPS = ("delete", "crop", "silence", "insert_silence", "insert", "move", "fade_in", "fade_out", "gain", "normalize", "speed",
       "pitch", "reverse", "reverb", "echo", "fx")
TIME_KEYS = ("a", "b", "at", "to")

EDIT_HELP = (
    "Each edit is an object with \"op\" and its settings. Times are seconds from the start of the sound as it is at that "
    "step (a number, or \"m:ss.sss\" as the editor shows them). \"a\" and \"b\" select a part; leave both out to change the "
    "whole sound. Ops: "
    "delete {a, b}: take the part out; "
    "crop {a, b}: keep only that part; "
    "silence {a, b}: make it silent; "
    "insert_silence {at, seconds}: add silence; "
    "insert {at, from: {path, a?, b?}}: put in (a part of) another sound file, or this one; with a and b instead of at it "
    "replaces that part; "
    "move {a, b, to}: move that part so it starts at \"to\", a time outside the part, counted on the sound before the move "
    "(to past the part means it lands so that it ends where \"to\" was); "
    "fade_in / fade_out {a?, b?, curve?: tri|qsin|esin|log|exp}: fade over the part (whole sound when left out); "
    "gain {db: -60..30}: louder or quieter; "
    "normalize {mode: peak|loudness, target: dB peak (-30..0, default -1) or LUFS (-36..-6, default -16)}; "
    "speed {factor: 0.25..4, keep_pitch: true (default) | false (tape: faster also sounds higher)}; "
    "pitch {semitones: -24..24}: same speed; "
    "reverse; "
    "reverb {room: " + "|".join(ae.REVERBS) + ", mix: 0..1}: rings on past the part; "
    "echo {kind: " + "|".join(ae.ECHOES) + "}; "
    "fx {name: " + "|".join(ae.EFFECTS) + ", amount: 0..1 (default 0.5)}.")

TOOLS = [
    {"name": "audio_info",
     "description": "Look at a sound file before editing it: length, sample rate, channels, peak and loudness, and its "
                    "silences (start and end of every quiet stretch), so you can find words, gaps and where to cut.",
     "inputSchema": {"type": "object", "properties": {
         "path": {"type": "string", "description": "Full path of the sound"},
         "silence_db": {"type": "number", "description": "What counts as quiet, in dB (default -40; try -30 for noisy recordings)"},
         "min_silence": {"type": "number", "description": "Shortest quiet stretch to list, seconds (default 0.3)"}},
         "required": ["path"]}},
    {"name": "audio_waveform",
     "description": "A picture of a sound's waveform with a time ruler, the whole sound or the part from a to b, and an "
                    "optional highlighted part. Use it to see where sounds and silences are before and after editing.",
     "inputSchema": {"type": "object", "properties": {
         "path": {"type": "string", "description": "Full path of the sound"},
         "a": {"description": "Show from this time (seconds or m:ss.sss)"}, "b": {"description": "Show up to this time"},
         "highlight": {"type": "array", "description": "[start, end] of a part to mark, e.g. what you plan to cut"}},
         "required": ["path"]}},
    {"name": "edit_audio",
     "description": "Edit a sound file with the wave editor's tools, a list of edits applied in order, then save it. " + EDIT_HELP +
                    " The result is written over the file (the old one goes to the project's trash) unless you give "
                    "output: a new path, whose extension picks the format (.mp3 .wav .flac .ogg .opus .m4a .aac). With no "
                    "edits and an output, it converts. Returns each step and a waveform picture of the result.",
     "inputSchema": {"type": "object", "properties": {
         "path": {"type": "string", "description": "Full path of the sound to edit"},
         "edits": {"type": "array", "description": "The edits in order, as described above", "items": {"type": "object"}},
         "output": {"type": "string", "description": "Full path to save to instead of replacing the file"},
         "overwrite": {"type": "boolean", "description": "Allow output to replace a file that is there (it goes to the trash)"}},
         "required": ["path", "edits"]}},
    {"name": "split_audio",
     "description": "Cut a sound into separate files at the given times (the editor's Save slices): <name>-01.<ext>, "
                    "<name>-02.<ext>… beside it, or in folder. The sound itself stays as it is.",
     "inputSchema": {"type": "object", "properties": {
         "path": {"type": "string", "description": "Full path of the sound"},
         "cuts": {"type": "array", "description": "Times to cut at (seconds or m:ss.sss)"},
         "folder": {"type": "string", "description": "Full path of a folder for the parts (default: beside the sound)"},
         "ext": {"type": "string", "description": "Format of the parts, e.g. .wav (default: the sound's own)"}},
         "required": ["path", "cuts"]}},
]


class Failure(Exception):
    pass


def allowed(path, must_exist=True):
    if not isinstance(path, str) or not os.path.isabs(os.path.expanduser(path)):
        raise Failure(f"{path!r} is not a full path; use the project folder's full path")
    real = os.path.realpath(os.path.expanduser(path))
    if not any(real == root or real.startswith(root + os.sep) for root in ROOTS):
        raise Failure(f"{path} is outside the folders this tool may use: {', '.join(ROOTS)}")
    if os.sep + ".git" + os.sep in real + os.sep:
        raise Failure("not inside .git")
    if must_exist and not os.path.isfile(real):
        raise Failure(f"no file {path}")
    return real


def seconds(value, what):
    """A time as seconds: a number, or "m:ss.sss" / "h:mm:ss" as the editor writes them."""
    if isinstance(value, bool):
        raise Failure(f"{what} must be a time")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and re.fullmatch(r"\s*(\d+:){0,2}\d+(\.\d+)?\s*", value):
        total = 0.0
        for part in value.strip().split(":"):
            total = total * 60 + float(part)
        return total
    raise Failure(f"{what} must be seconds or m:ss.sss, not {value!r}")


def load(path, work, name):
    """The sound decoded to FLAC in work, with its probe."""
    try:
        info = ae.probe(path)
        out = os.path.join(work, name)
        ae.decode(path, info["format"], out, info["channels"])
    except ae.EditError as exc:
        raise Failure(f"{os.path.basename(path)}: {exc}") from None
    return out, info


def project_root(path):
    """The nearest folder above path with a .strata-studio folder (a Studio project), else the file's own folder."""
    folder = os.path.dirname(path)
    while True:
        if os.path.isdir(os.path.join(folder, ".strata-studio")):
            return folder
        parent = os.path.dirname(folder)
        if parent == folder or not any(parent == r or parent.startswith(r + os.sep) for r in ROOTS):
            return os.path.dirname(path)
        folder = parent


def to_trash(path):
    root = project_root(path)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(root, ".strata-studio", "trash", stamp, os.path.relpath(path, root))
    n = 1
    while os.path.lexists(dest):
        n += 1
        dest = os.path.join(root, ".strata-studio", "trash", f"{stamp}-{n}", os.path.relpath(path, root))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.move(path, dest)
    return dest


def measure(path, silence_db=-40.0, min_silence=0.3):
    """Peak, mean, loudness and silences in one ffmpeg pass."""
    out = subprocess.run([ae.FFMPEG, "-v", "info", "-nostdin", "-i", path, "-af",
                          f"silencedetect=n={silence_db}dB:d={min_silence},volumedetect,ebur128=framelog=quiet", "-f", "null", "-"],
                         capture_output=True, text=True, timeout=ae.TIMEOUT)
    err = out.stderr
    peak = re.search(r"max_volume:\s*(-?[\d.]+|-inf) dB", err)
    mean = re.search(r"mean_volume:\s*(-?[\d.]+|-inf) dB", err)
    lufs = re.findall(r"I:\s*(-?[\d.]+|-inf) LUFS", err)
    starts = [float(x) for x in re.findall(r"silence_start:\s*(-?[\d.]+)", err)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*(-?[\d.]+)", err)]
    silences = []
    for i, s in enumerate(starts):
        silences.append([round(max(0.0, s), 3), round(ends[i], 3) if i < len(ends) else None])
    num = lambda m: None if not m or m.group(1) == "-inf" else float(m.group(1))
    return {"peak_db": num(peak), "mean_db": num(mean), "loudness_lufs": None if not lufs or lufs[-1] == "-inf" else float(lufs[-1]),
            "silences": silences}


def clock(s):
    m = int(s // 60)
    return f"{m}:{s - m * 60:06.3f}"


def waveform(path, a=None, b=None, highlight=None, width=1200, height=300, name=None):
    """PNG of the waveform (one band per channel) with a time ruler, as MCP image content."""
    total = ae.length(path)
    a = 0.0 if a is None else max(0.0, min(a, total))
    b = total if b is None else max(a + 0.01, min(b, total))
    span = b - a
    step = next((s for s in (0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600) if span / s <= 12), 600)
    ruler, top = 28, 28
    marks = []
    t0 = (int(a / step) + (0 if a % step == 0 else 1)) * step
    tick = t0
    while tick <= b + 1e-9 and len(marks) < 32:
        x = int((tick - a) / span * (width - 1))
        label = (f"{tick:.2f}s" if step < 1 else clock(tick)[:-4]).replace(":", "\\:")
        marks.append(f"drawbox=x={x}:y=0:w=1:h={height + top}:color=0x3c3c3c@1:t=fill")
        if os.path.exists(FONT):
            marks.append(f"drawtext=fontfile={FONT}:text='{label}':x={x + 4}:y=8:fontsize=13:fontcolor=0xbbbbbb")
        tick = round(tick + step, 6)
    if highlight and len(highlight) == 2:
        h1, h2 = sorted(highlight)
        x1, x2 = int((max(h1, a) - a) / span * width), int((min(h2, b) - a) / span * width)
        if x2 > x1:
            marks.append(f"drawbox=x={x1}:y={top}:w={x2 - x1}:h={height}:color=0xf2cc60@0.28:t=fill")
            marks.append(f"drawbox=x={x1}:y=0:w={x2 - x1}:h=6:color=0xf2cc60@1:t=fill")
    graph = (f"[0:a]atrim=start={a:.6f}:end={b:.6f},asetpts=N/SR/TB,showwavespic=s={width}x{height}:split_channels=1:colors=0x4fc1c9|0x4fc1c9:scale=lin[w];"
             f"color=c=0x1e1e1e:s={width}x{height + top}[bg];[bg][w]overlay=0:{top},format=yuv444p" + ("," + ",".join(marks) if marks else "") + "[out]")
    with tempfile.TemporaryDirectory() as work:
        png = os.path.join(work, "wave.png")
        ae.run([ae.FFMPEG, "-v", "error", "-nostdin", "-i", path, "-filter_complex", graph, "-map", "[out]", "-frames:v", "1", "-y", png])
        with open(png, "rb") as fh:
            data = fh.read()
    return [{"type": "image", "data": base64.b64encode(data).decode(), "mimeType": "image/png"},
            {"type": "text", "text": f"Waveform of {name or os.path.basename(path)} from {clock(a)} to {clock(b)}"
                                     + (f", marked {clock(min(highlight))}–{clock(max(highlight))}" if highlight else "") + "."}]


def tell_progress(token, done, total, message):
    if token is None:
        return
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/progress",
                                 "params": {"progressToken": token, "progress": done, "total": total, "message": message}}) + "\n")
    sys.stdout.flush()


def edit(args, token):
    src = allowed(args.get("path"))
    edits = args.get("edits")
    if not isinstance(edits, list) or len(edits) > MAX_EDITS or not all(isinstance(e, dict) for e in edits):
        raise Failure(f"edits must be a list of at most {MAX_EDITS} objects")
    output = args.get("output")
    out = allowed(output, must_exist=False) if output else src
    ext = os.path.splitext(out)[1].lower()
    if ext not in ae.ENCODE:
        raise Failure(f"the file type is picked by the extension: {', '.join(ae.SAVE_EXTS)}")
    if not edits and out == src:
        raise Failure("no edits: give edits, or an output to convert to")
    if out != src and os.path.exists(out) and args.get("overwrite") is not True:
        raise Failure(f"{out} already exists; pass overwrite: true to replace it (the old one goes to the trash)")
    started, lines = time.time(), []
    with tempfile.TemporaryDirectory() as work:
        cur, info = load(src, work, "v0.flac")
        total = ae.length(cur)
        before = total
        for i, e in enumerate(edits, 1):
            op = e.get("op")
            if op not in OPS:
                raise Failure(f"edit {i}: op must be one of {', '.join(OPS)}")
            params = {k: v for k, v in e.items() if k not in ("op", "from")}
            for key in TIME_KEYS:
                if key in params and params[key] is not None:
                    params[key] = seconds(params[key], f"edit {i}: {key}")
            clip = None
            if op == "insert":
                source = e.get("from")
                if not isinstance(source, dict):
                    raise Failure(f"edit {i}: insert needs from: {{path, a?, b?}}")
                spath = allowed(source.get("path"))
                whole = cur if spath == src else load(spath, work, f"from{i}.flac")[0]     # this sound as it is at this step
                fa = seconds(source["a"], f"edit {i}: from.a") if source.get("a") is not None else None
                fb = seconds(source["b"], f"edit {i}: from.b") if source.get("b") is not None else None
                clip = os.path.join(work, f"clip{i}.flac")
                if fa is not None or fb is not None:
                    ae.extract(whole, fa or 0.0, fb if fb is not None else ae.length(whole), clip)
                else:
                    shutil.copyfile(whole, clip)
                op = "paste"
                if "a" not in params and "at" not in params:
                    raise Failure(f"edit {i}: insert needs at (or a and b to replace a part)")
            nxt = os.path.join(work, f"v{i}.flac")
            tell_progress(token, i - 1, len(edits), f"Edit {i} of {len(edits)}: {e.get('op')}")
            try:
                label = ae.apply(op, params, cur, nxt, total, info["rate"], info["channels"], clip=clip, work=work)
            except ae.EditError as exc:
                raise Failure(f"edit {i} ({e.get('op')}): {exc}" + (f". Edits 1-{i - 1} worked, but nothing was saved." if i > 1 else "")) from None
            cur, total = nxt, ae.length(nxt)
            lines.append(f"{i}. {'Insert' if e.get('op') == 'insert' else label} → {total:.2f} s")
        tell_progress(token, len(edits), len(edits), "Saving")
        tmp = os.path.join(os.path.dirname(out), f".{os.path.basename(out)}.audio-{os.getpid()}{ext}")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        try:
            ae.export(cur, tmp, ext)
        except ae.EditError as exc:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise Failure(str(exc)) from None
        trash = to_trash(out) if os.path.exists(out) else None
        os.replace(tmp, out)
    summary = "\n".join(lines + [f"Saved {out}: {before:.2f} s → {ae.length(out):.2f} s, {os.path.getsize(out) // 1024} KB, in {time.time() - started:.1f} s."
                                 + (f" The earlier file is in {trash}." if trash else "")])
    return [{"type": "text", "text": summary}] + waveform(out)


def split(args):
    src = allowed(args.get("path"))
    cuts = args.get("cuts")
    if not isinstance(cuts, list) or not cuts or len(cuts) > 200:
        raise Failure("cuts must be a list of times")
    folder = allowed(args["folder"], must_exist=False) if args.get("folder") else os.path.dirname(src)
    stem, own = os.path.splitext(os.path.basename(src))
    ext = (args.get("ext") or own).lower()
    if not ext.startswith("."):
        ext = "." + ext
    if ext not in ae.ENCODE:
        raise Failure(f"ext must be one of {', '.join(ae.SAVE_EXTS)}")
    with tempfile.TemporaryDirectory() as work:
        cur, _ = load(src, work, "v0.flac")
        total = ae.length(cur)
        edges = [0.0] + sorted({round(seconds(c, "cut"), 4) for c in cuts if 0.01 < seconds(c, "cut") < total - 0.01}) + [total]
        if len(edges) < 3:
            raise Failure(f"no cut lies inside the sound (0 to {total:.2f} s)")
        os.makedirs(folder, exist_ok=True)
        saved = []
        for i, (a, b) in enumerate(zip(edges, edges[1:]), 1):
            name, n = f"{stem}-{i:02d}{ext}", 1
            while os.path.exists(os.path.join(folder, name)):
                n += 1
                name = f"{stem}-{i:02d} ({n}){ext}"
            ae.export(cur, os.path.join(folder, name), ext, a, b)
            saved.append(f"{os.path.join(folder, name)} ({clock(a)}–{clock(b)}, {b - a:.2f} s)")
    return [{"type": "text", "text": f"Saved {len(saved)} parts:\n" + "\n".join(saved)}]


def call(name, args, token=None):
    try:
        if name == "audio_info":
            path = allowed(args.get("path"))
            info = ae.probe(path)
            silence_db = float(args.get("silence_db", -40)) if isinstance(args.get("silence_db", -40), (int, float)) else -40.0
            min_silence = float(args.get("min_silence", 0.3)) if isinstance(args.get("min_silence", 0.3), (int, float)) else 0.3
            found = measure(path, max(-90.0, min(-5.0, silence_db)), max(0.05, min(10.0, min_silence)))
            data = {"path": path, "seconds": round(info["seconds"], 3), "length": clock(info["seconds"]), "sample_rate": info["rate"],
                    "channels": info["channels"], "format": info["format"], **found}
            if len(data["silences"]) > 60:
                data["silences"] = data["silences"][:60]
                data["note"] = "only the first 60 silences are listed; raise min_silence for fewer"
            return [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}]
        if name == "audio_waveform":
            path = allowed(args.get("path"))
            ae.probe(path)
            hl = args.get("highlight")
            if hl is not None:
                if not isinstance(hl, list) or len(hl) != 2:
                    raise Failure("highlight must be [start, end]")
                hl = [seconds(x, "highlight") for x in hl]
            with tempfile.TemporaryDirectory() as work:
                cur, _ = load(path, work, "v0.flac")
                return waveform(cur, seconds(args["a"], "a") if args.get("a") is not None else None,
                                seconds(args["b"], "b") if args.get("b") is not None else None, hl, name=os.path.basename(path))
        if name == "edit_audio":
            return edit(args, token)
        if name == "split_audio":
            return split(args)
    except ae.EditError as exc:
        raise Failure(str(exc)) from None
    raise Failure(f"unknown tool {name}")


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
            answer(msg_id, {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}}, "serverInfo": {"name": "audio-local", "version": "1.0"}})
        elif method == "ping":
            answer(msg_id, {})
        elif method == "tools/list":
            answer(msg_id, {"tools": TOOLS})
        elif method == "tools/call":
            params = msg.get("params") or {}
            if params.get("name") not in {tool["name"] for tool in TOOLS}:
                answer(msg_id, error={"code": -32602, "message": f"unknown tool {params.get('name')!r}"})
                continue
            try:
                token = (params.get("_meta") or {}).get("progressToken")
                content, failed = call(params.get("name"), params.get("arguments") or {}, token), False
            except Failure as exc:
                content, failed = [{"type": "text", "text": f"Error: {exc}"}], True
            except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as exc:
                content, failed = [{"type": "text", "text": f"Error: {type(exc).__name__}: {exc}"}], True
            answer(msg_id, {"content": content, "isError": failed})
        else:
            answer(msg_id, error={"code": -32601, "message": f"unknown method {method!r}"})


if __name__ == "__main__":
    main()
