"""Studio's audio editor: every edit is one ffmpeg run from one version of the sound to the next (2026-11-13).

The editor keeps an edit's versions as FLAC files in a folder of its own (lossless and about half the size of WAV), so
undo and redo are a step through that list and only Save writes into the project, in the file's own format. The browser
decodes a version itself to draw the waveform and play it. Used by studio.py's /api/audio/* routes; standard library
and ffmpeg only.

Times are seconds. A selection is [a, b]; an edit with no selection applies to the whole sound.
"""
import json
import math
import os
import random
import re
import struct
import subprocess
import wave

FFMPEG = os.environ.get("STUDIO_FFMPEG") or "ffmpeg"
FFPROBE = os.environ.get("STUDIO_FFPROBE") or "ffprobe"
MAX_SECONDS = 3600
TIMEOUT = 300

# Only these demuxers are opened (forced with -f), so a file named .mp3 that is really a playlist or a concat list
# cannot make ffmpeg read other files on this machine.
DEMUXERS = {"mp3", "wav", "flac", "ogg", "mov", "aac", "matroska", "webm", "aiff", "w64", "caf"}

ENCODE = {
    ".mp3": ["-c:a", "libmp3lame", "-q:a", "2"],
    ".wav": ["-c:a", "pcm_s16le"],
    ".flac": ["-c:a", "flac", "-sample_fmt", "s16"],
    ".ogg": ["-c:a", "libvorbis", "-q:a", "6"],
    ".oga": ["-c:a", "libvorbis", "-q:a", "6"],
    ".opus": ["-c:a", "libopus", "-b:a", "160k"],
    ".m4a": ["-c:a", "aac", "-b:a", "192k"],
    ".aac": ["-c:a", "aac", "-b:a", "192k", "-f", "adts"],
}
SAVE_EXTS = tuple(ENCODE)

REVERBS = {"room": (0.5, 0.35), "hall": (1.8, 0.45), "church": (3.5, 0.5), "plate": (1.2, 0.5)}   # decay seconds, wet
ECHOES = {"short": "0.8:0.75:120:0.45", "slap": "0.8:0.8:80|160:0.4|0.2", "long": "0.8:0.7:400|800:0.45|0.25",
          "canyon": "0.8:0.85:700|1400|2100:0.5|0.3|0.15"}
# In-place effects: (ffmpeg filter, extra seconds of tail to keep). {amount} is 0..1 from the user's slider.
EFFECTS = {
    "chorus": lambda k: ("chorus=0.6:0.9:50|60:0.4|0.32:0.25|0.4:2|1.3", 0.1),
    "flanger": lambda k: (f"flanger=delay=2:depth={2 + 6 * k:.2f}:regen={int(-20 + 60 * k)}:speed=0.5", 0),
    "phaser": lambda k: (f"aphaser=in_gain=0.5:out_gain=0.75:delay=3:decay={0.3 + 0.6 * k:.2f}:speed=0.5", 0),
    "tremolo": lambda k: (f"tremolo=f={2 + 10 * k:.2f}:d=0.6", 0),
    "vibrato": lambda k: (f"vibrato=f={3 + 6 * k:.2f}:d={0.2 + 0.5 * k:.2f}", 0),
    "lowpass": lambda k: (f"lowpass=f={int(400 * 20 ** (1 - k))}", 0),
    "highpass": lambda k: (f"highpass=f={int(60 * 20 ** k)}", 0),
    "bass": lambda k: (f"bass=g={2 + 13 * k:.1f}", 0),
    "treble": lambda k: (f"treble=g={2 + 13 * k:.1f}", 0),
    "compressor": lambda k: (f"acompressor=threshold={0.25 - 0.2 * k:.3f}:ratio={2 + 8 * k:.1f}:attack=10:release=120:makeup={1 + 3 * k:.2f}", 0),
    "denoise": lambda k: (f"afftdn=nr={6 + 30 * k:.0f}:nf=-35", 0),
    "telephone": lambda k: ("highpass=f=320,lowpass=f=3200,acompressor=threshold=0.1:ratio=6:makeup=2", 0),
    "crush": lambda k: (f"acrusher=bits={int(12 - 9 * k)}:mix=1:mode=log:samples={1 + int(9 * k)}", 0),
}


class EditError(Exception):
    pass


def run(argv, timeout=TIMEOUT):
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise EditError("ffmpeg took too long") from None
    except OSError as exc:
        raise EditError(f"could not run {argv[0]}: {exc}") from None
    if out.returncode != 0:
        tail = [line for line in out.stderr.strip().splitlines() if line.strip()][-3:]
        raise EditError("ffmpeg: " + (" | ".join(tail) or f"exited with {out.returncode}"))
    return out


def probe(path):
    """{format, seconds, rate, channels} of a sound or video's first audio stream; refuses what is not plain audio."""
    out = run([FFPROBE, "-v", "error", "-print_format", "json", "-show_entries",
               "format=format_name,duration:stream=codec_type,sample_rate,channels", path], timeout=30)
    try:
        info = json.loads(out.stdout)
    except ValueError:
        raise EditError("ffprobe gave no answer") from None
    names = (info.get("format") or {}).get("format_name", "").split(",")
    demuxer = next((n for n in names if n in DEMUXERS), None)
    if not demuxer:
        raise EditError(f"not a sound file Studio can edit (format {','.join(names) or 'unknown'})")
    stream = next((s for s in info.get("streams") or [] if s.get("codec_type") == "audio"), None)
    if not stream:
        raise EditError("the file has no sound")
    seconds = float((info.get("format") or {}).get("duration") or 0)
    if seconds > MAX_SECONDS:
        raise EditError(f"the sound is {seconds / 60:.0f} minutes long; the editor takes up to {MAX_SECONDS // 60}")
    return {"format": demuxer, "seconds": seconds, "rate": int(stream.get("sample_rate") or 48000),
            "channels": min(2, int(stream.get("channels") or 2))}


def flac_out(path):
    return ["-c:a", "flac", "-sample_fmt", "s16", "-y", path]


def decode(src, demuxer, dst, channels):
    run([FFMPEG, "-v", "error", "-nostdin", "-f", demuxer, "-i", src, "-map", "0:a:0", "-vn", "-ac", str(channels)] + flac_out(dst))


def length(path):
    out = run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path], timeout=30)
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def t(x):
    return f"{max(0.0, x):.6f}"


def layout(channels):
    return "mono" if channels == 1 else "stereo"


class Graph:
    """Builds one filter_complex: splits the input as often as needed, cuts parts out and joins them."""

    def __init__(self, rate, channels):
        self.rate, self.channels, self.lines, self.n, self.uses = rate, channels, [], 0, []

    def name(self):
        self.n += 1
        return f"x{self.n}"

    def source(self):
        """A fresh copy of the sound: each part reads its own input, so no part waits on another's buffering."""
        self.uses.append(len(self.uses))
        return f"{len(self.uses) - 1}:a"

    def norm(self):
        return f"aformat=sample_fmts=fltp:sample_rates={self.rate}:channel_layouts={layout(self.channels)}"

    def part(self, a=None, b=None, chain=""):
        """Input 0 from a to b (either may be None), with an optional filter chain after it."""
        src, out = self.source(), self.name()
        trim = []
        if a is not None or b is not None:
            trim.append("atrim=" + ":".join(([f"start={t(a)}"] if a is not None else []) + ([f"end={t(b)}"] if b is not None else [])))
            trim.append("asetpts=N/SR/TB")
        self.lines.append(f"[{src}]" + ",".join(trim + ([chain] if chain else []) + [self.norm()]) + f"[{out}]")
        return out

    def add(self, line_in, chain):
        out = self.name()
        self.lines.append(f"[{line_in}]{chain},{self.norm()}[{out}]" if line_in else f"{chain},{self.norm()}[{out}]")
        return out

    def silence(self, seconds):
        out = self.name()
        self.lines.append(f"anullsrc=r={self.rate}:cl={layout(self.channels)},atrim=end={t(seconds)},{self.norm()}[{out}]")
        return out

    def concat(self, parts):
        if len(parts) == 1:
            return parts[0]
        out = self.name()
        self.lines.append("".join(f"[{p}]" for p in parts) + f"concat=n={len(parts)}:v=0:a=1[{out}]")
        return out

    def text(self, last):
        """The graph, with extra inputs ({extra0}, {extra1}...) numbered after the copies of the sound."""
        text = ";".join(self.lines + [f"[{last}]anull[out]"])
        return re.sub(r"\{extra(\d)\}", lambda m: str(len(self.uses) + int(m.group(1))), text)


def keep(a, b, total):
    """The parts around a selection that are long enough to be worth a segment (an empty atrim can stall concat)."""
    return a > 0.0005, b < total - 0.0005


def speed_chain(factor, keep_pitch, rate, rubberband):
    if keep_pitch:
        if rubberband:
            return f"rubberband=tempo={factor:.5f}:transients=mixed"
        steps, f = [], factor
        while f > 2:
            steps.append("atempo=2")
            f /= 2
        while f < 0.5:
            steps.append("atempo=0.5")
            f /= 0.5
        return ",".join(steps + [f"atempo={f:.5f}"])
    return f"asetrate={int(rate * factor)},aresample={rate}"


def pitch_chain(semitones, rate, rubberband):
    ratio = 2 ** (semitones / 12)
    if rubberband:
        return f"rubberband=pitch={ratio:.6f}:pitchq=quality:formant=preserved"
    return f"asetrate={int(rate * ratio)},aresample={rate}," + speed_chain(1 / ratio, True, rate, False)


def make_ir(path, rate, channels, decay, seed=7):
    """A reverb's impulse response: decaying noise, a little different per channel so stereo sounds wide."""
    rng = random.Random(seed)
    n = max(1, int(rate * decay))
    rows = [[rng.gauss(0, 1) * math.exp(-6.9 * i / n) for _ in range(channels)] for i in range(n)]   # -60 dB at the end
    # Scaled to unit energy per channel, so the reverb is about as loud as the sound itself, and stored as 32-bit
    # samples, which keep such small values. afir then runs with gtype=none: ffmpeg 6.1's default (peak) turned the
    # reverb down to about -55 dB, so it could not be heard.
    energy = [math.sqrt(sum(r[c] ** 2 for r in rows)) or 1 for c in range(channels)]
    frames = bytearray()
    for r in rows:
        for c in range(channels):
            frames += struct.pack("<i", int(max(-1, min(1, r[c] / energy[c])) * 2147483647))
    with wave.open(path, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(4)
        w.setframerate(rate)
        w.writeframes(bytes(frames))


def peak_db(path, a, b):
    out = subprocess.run([FFMPEG, "-v", "info", "-nostdin", "-ss", t(a), "-to", t(b), "-i", path, "-af", "volumedetect", "-f", "null", "-"],
                         capture_output=True, text=True, timeout=TIMEOUT)
    found = re.search(r"max_volume:\s*(-?[\d.]+|-inf) dB", out.stderr)
    if not found or found.group(1) == "-inf":
        return None
    return float(found.group(1))


def has_rubberband():
    if not hasattr(has_rubberband, "known"):
        try:
            has_rubberband.known = " rubberband " in subprocess.run([FFMPEG, "-hide_banner", "-filters"], capture_output=True, text=True, timeout=20).stdout
        except (OSError, subprocess.SubprocessError):
            has_rubberband.known = False
    return has_rubberband.known


def num(params, key, lo, hi, default=None):
    value = params.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise EditError(f"{key} must be a number")
    if not lo <= value <= hi:
        raise EditError(f"{key} must be from {lo} to {hi}")
    return float(value)


def apply(op, params, cur, out, total, rate, channels, clip=None, work=None):
    """Writes the edit of cur as out. Returns a short label for the history. Raises EditError."""
    a = params.get("a")
    b = params.get("b")
    whole = a is None or b is None
    if whole:
        a, b = 0.0, total
    else:
        a, b = num(params, "a", 0, total + 1), num(params, "b", 0, total + 1)
        a, b = min(a, b, total), min(max(a, b), total)
    span = b - a
    needs_span = op not in ("insert_silence", "paste")
    if needs_span and span < 0.001:
        raise EditError("select some sound first")
    g, inputs = Graph(rate, channels), []
    pre, post = keep(a, b, total)

    def around(mid):
        return g.concat(([g.part(None, a)] if pre else []) + [mid] + ([g.part(b, None)] if post else []))

    def with_tail(chain, tail, dry=True):
        """Effects that ring on after the selection: the selection (with its tail) over the rest, which is muted under it."""
        wet = g.part(a, b, f"apad=pad_dur={t(tail)}")
        if dry:
            split_d, split_w = g.name(), g.name()
            g.lines.append(f"[{wet}]asplit=2[{split_d}][{split_w}]")
            fx = g.add(split_w, chain)
            wet = g.name()
            g.lines.append(f"[{split_d}][{fx}]amix=inputs=2:normalize=0:duration=longest[{wet}]")
        else:
            wet = g.add(wet, chain)
        if a > 0.0005:
            wet = g.add(wet, f"adelay=delays={int(round(a * 1000))}:all=1")
        rest = g.part(None, None, f"volume=0:enable='between(t,{t(a)},{t(b)})'")
        mixed = g.name()
        g.lines.append(f"[{wet}][{rest}]amix=inputs=2:normalize=0:duration=longest[{mixed}]")   # this order: ffmpeg 6.1's amix
        # ended at the second input's end when that one was padded (a chorus on 0.5-2.5 s of 4 s gave 2.7 s)
        return mixed

    label = op
    if op == "delete":
        parts = ([g.part(None, a)] if pre else []) + ([g.part(b, None)] if post else [])
        if not parts:
            raise EditError("that would delete the whole sound")
        last, label = g.concat(parts), f"Delete {span:.2f} s"
    elif op == "crop":
        last, label = g.part(a, b), f"Keep {span:.2f} s"
    elif op == "silence":
        last, label = g.part(None, None, f"volume=0:enable='between(t,{t(a)},{t(b)})'"), "Silence"
    elif op == "insert_silence":
        at = num(params, "at", 0, total + 1, a if not whole else 0)
        seconds = num(params, "seconds", 0.01, 600)
        at = min(at, total)
        parts = ([g.part(None, at)] if at > 0.0005 else []) + [g.silence(seconds)] + ([g.part(at, None)] if at < total - 0.0005 else [])
        last, label = g.concat(parts), f"Insert {seconds:g} s of silence"
    elif op == "paste":
        if not clip or not os.path.isfile(clip):
            raise EditError("nothing has been cut or copied yet")
        inputs.append(clip)
        pasted = g.name()
        g.lines.append(f"[{{extra0}}:a]{g.norm()}[{pasted}]")
        if whole:
            at = min(num(params, "at", 0, total + 1, 0), total)
            parts = ([g.part(None, at)] if at > 0.0005 else []) + [pasted] + ([g.part(at, None)] if at < total - 0.0005 else [])
            last = g.concat(parts)
        else:
            last = around(pasted)
        label = "Paste"
    elif op == "move":
        to = min(num(params, "to", 0, total + 1), total)
        if whole:
            raise EditError("select the part to move")
        if a - 0.0005 <= to <= b + 0.0005:
            raise EditError("move it outside the selected part")
        if to < a:
            order = [(None, to), (a, b), (to, a), (b, None)]
        else:
            order = [(None, a), (b, to), (a, b), (to, None)]
        parts = [g.part(x, y) for x, y in order if (y if y is not None else total) - (x or 0) > 0.0005]
        last, label = g.concat(parts), f"Move {span:.2f} s"
    elif op in ("fade_in", "fade_out"):
        curve = params.get("curve") or "tri"
        if curve not in ("tri", "qsin", "esin", "log", "exp"):
            raise EditError("unknown fade curve")
        kind = "in" if op == "fade_in" else "out"
        last, label = around(g.part(a, b, f"afade=t={kind}:st=0:d={t(span)}:curve={curve}")), f"Fade {kind}"
    elif op == "gain":
        db = num(params, "db", -60, 30)
        last, label = around(g.part(a, b, f"volume={db:.2f}dB")), f"Volume {db:+g} dB"
    elif op == "normalize":
        mode = params.get("mode") or "peak"
        if mode == "peak":
            target = num(params, "target", -30, 0, -1)
            peak = peak_db(cur, a, b)
            if peak is None:
                raise EditError("the selection is silent")
            last, label = around(g.part(a, b, f"volume={target - peak:.2f}dB")), f"Normalize to {target:g} dB peak"
        elif mode == "loudness":
            target = num(params, "target", -36, -6, -16)
            last, label = around(g.part(a, b, f"loudnorm=I={target:g}:TP=-1.5:LRA=11,aresample={rate}")), f"Loudness {target:g} LUFS"
        else:
            raise EditError("mode must be peak or loudness")
    elif op == "speed":
        factor = num(params, "factor", 0.25, 4)
        keep_pitch = params.get("keep_pitch", True) is not False
        last = around(g.part(a, b, speed_chain(factor, keep_pitch, rate, has_rubberband())))
        label = f"Speed ×{factor:g}" + ("" if keep_pitch else " (pitch too)")
    elif op == "pitch":
        semis = num(params, "semitones", -24, 24)
        last, label = around(g.part(a, b, pitch_chain(semis, rate, has_rubberband()))), f"Pitch {semis:+g} semitones"
    elif op == "reverse":
        last, label = around(g.part(a, b, "areverse")), "Reverse"
    elif op == "reverb":
        room = params.get("room") or "hall"
        if room not in REVERBS:
            raise EditError("unknown room")
        decay, wet = REVERBS[room]
        mix = num(params, "mix", 0, 1, wet)
        ir = os.path.join(work or os.path.dirname(out), f"ir2-{room}-{rate}-{channels}.wav")
        if not os.path.exists(ir):
            make_ir(ir, rate, channels, decay)
        inputs.append(ir)
        wet_in = g.part(a, b, f"apad=pad_dur={t(decay)}")
        split_d, split_w, ir_in, verb, mixed = g.name(), g.name(), g.name(), g.name(), g.name()
        g.lines.append(f"[{wet_in}]asplit=2[{split_d}][{split_w}]")
        g.lines.append(f"[{{extra0}}:a]aformat=sample_fmts=fltp:sample_rates={rate}:channel_layouts={layout(channels)}[{ir_in}]")
        g.lines.append(f"[{split_w}][{ir_in}]afir=dry=1:wet=1:gtype=none,volume={mix * 0.9:.3f},{g.norm()}[{verb}]")
        g.lines.append(f"[{split_d}]volume={1 - mix * 0.5:.3f}[{split_d}v];[{split_d}v][{verb}]amix=inputs=2:normalize=0:duration=longest[{mixed}]")
        wet = mixed
        if a > 0.0005:
            wet = g.add(wet, f"adelay=delays={int(round(a * 1000))}:all=1")
        rest, last = g.part(None, None, f"volume=0:enable='between(t,{t(a)},{t(b)})'"), g.name()
        g.lines.append(f"[{wet}][{rest}]amix=inputs=2:normalize=0:duration=longest[{last}]")
        label = f"Reverb ({room})"
    elif op == "echo":
        kind = params.get("kind") or "short"
        if kind not in ECHOES:
            raise EditError("unknown echo")
        delays = [int(x) for x in ECHOES[kind].split(":")[2].split("|")]
        last, label = with_tail(f"aecho={ECHOES[kind]}", max(delays) / 1000 * 4, dry=False), f"Echo ({kind})"
    elif op == "fx":
        name = params.get("name")
        if name not in EFFECTS:
            raise EditError("unknown effect")
        chain, tail = EFFECTS[name](num(params, "amount", 0, 1, 0.5))
        last = with_tail(chain, tail, dry=False) if tail else around(g.part(a, b, chain))
        label = name.capitalize()
    else:
        raise EditError(f"unknown edit {op!r}")
    graph = g.text(last)
    argv = [FFMPEG, "-v", "error", "-nostdin"]
    for path in [cur] * len(g.uses) + inputs:
        argv += ["-i", path]
    argv += ["-filter_complex", graph, "-map", "[out]"] + flac_out(out)
    run(argv)
    return label


def extract(cur, a, b, out):
    """The part from a to b as its own file (the clipboard, or one slice)."""
    run([FFMPEG, "-v", "error", "-nostdin", "-i", cur, "-af", f"atrim=start={t(a)}:end={t(b)},asetpts=N/SR/TB"] + flac_out(out))


def export(cur, out, ext, a=None, b=None):
    """Encodes a version (or a part of it) in the format its file name asks for."""
    if ext not in ENCODE:
        raise EditError(f"Studio saves {', '.join(SAVE_EXTS)} files")
    trim = ["-af", f"atrim=start={t(a)}:end={t(b)},asetpts=N/SR/TB"] if a is not None else []
    run([FFMPEG, "-v", "error", "-nostdin", "-i", cur] + trim + ENCODE[ext] + ["-y", out])


VOICE_REF_MAX_S = 30      # a voice's reference: VoxCPM2 works best with 3 to 15 seconds of clean speech


def voice_reference(src, dst, a=None, b=None):
    """A recording (or the part from a to b) as the mono 16-bit WAV a voice keeps as its reference; returns its seconds.
    Opened with a forced demuxer after ffprobe, like every file the editor reads."""
    info = probe(src)
    a = max(0.0, float(a)) if a is not None else 0.0
    b = min(info["seconds"], float(b)) if b is not None else info["seconds"]
    if b - a < 1:
        raise EditError("the reference must have at least 1 second of speech")
    if b - a > VOICE_REF_MAX_S:
        raise EditError(f"the reference is {b - a:.0f} s long; use at most {VOICE_REF_MAX_S} s (3 to 15 s of clean speech works best)")
    run([FFMPEG, "-v", "error", "-nostdin", "-f", info["format"], "-i", src, "-map", "0:a:0", "-vn",
         "-af", f"atrim=start={t(a)}:end={t(b)},asetpts=N/SR/TB", "-ac", "1", "-ar", str(max(16000, min(48000, info["rate"]))),
         "-c:a", "pcm_s16le", "-y", dst])
    return round(b - a, 2)
