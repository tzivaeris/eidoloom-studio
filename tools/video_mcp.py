#!/usr/bin/env python3
"""An MCP server (stdio) for simple video editing with ffmpeg: render_video, probe_media and video_frames.

The agent describes a whole edit as a timeline (clips in order with in and out points, transitions, effects, titles, music),
and render_video turns it into an MP4 with ffmpeg on this machine. The timeline is saved beside the video as
<name>.timeline.json, so a later call can change one thing and render again, and Studio's timeline tab can show it. Still
frames of the result come back as pictures, so a model that can see can check its work.

Files are read and written only inside VIDEO_ROOTS (default: ~/Projects, ComfyUI's output folder and ~/Videos).
"""
import base64
import json
import os
import subprocess
import sys
import tempfile
import time

ROOTS = [os.path.realpath(os.path.expanduser(p)) for p in
         os.environ.get("VIDEO_ROOTS", "~/Projects:~/.local/opt/comfyui/output:~/Videos").split(":") if p.strip()]
FFMPEG = os.environ.get("FFMPEG", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE", "ffprobe")
FONT = os.environ.get("VIDEO_FONT", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")     # has Greek letters
RENDER_TIMEOUT_S = int(os.environ.get("VIDEO_RENDER_TIMEOUT_S", "1700"))
PROTOCOL = "2025-06-18"
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
# Our names for ffmpeg's xfade transitions (6.1); the first thirteen are the original set, kept for older timelines.
TRANSITIONS = {"dissolve": "fade", "fade_black": "fadeblack", "fade_white": "fadewhite", "wipe_left": "wipeleft",
               "wipe_right": "wiperight", "wipe_up": "wipeup", "wipe_down": "wipedown", "slide_left": "slideleft",
               "slide_right": "slideright", "circle": "circleopen", "pixelize": "pixelize", "radial": "radial", "zoom": "zoomin",
               "fade_fast": "fadefast", "fade_slow": "fadeslow", "fade_gray": "fadegrays", "grain": "dissolve", "distance": "distance",
               "blur": "hblur", "slide_up": "slideup", "slide_down": "slidedown",
               "smooth_left": "smoothleft", "smooth_right": "smoothright", "smooth_up": "smoothup", "smooth_down": "smoothdown",
               "cover_left": "coverleft", "cover_right": "coverright", "cover_up": "coverup", "cover_down": "coverdown",
               "reveal_left": "revealleft", "reveal_right": "revealright", "reveal_up": "revealup", "reveal_down": "revealdown",
               "circle_close": "circleclose", "circle_crop": "circlecrop", "rect_crop": "rectcrop",
               "open_horizontal": "horzopen", "close_horizontal": "horzclose", "open_vertical": "vertopen", "close_vertical": "vertclose",
               "diagonal_tl": "diagtl", "diagonal_tr": "diagtr", "diagonal_bl": "diagbl", "diagonal_br": "diagbr",
               "wipe_tl": "wipetl", "wipe_tr": "wipetr", "wipe_bl": "wipebl", "wipe_br": "wipebr",
               "slices_left": "hlslice", "slices_right": "hrslice", "slices_up": "vuslice", "slices_down": "vdslice",
               "wind_left": "hlwind", "wind_right": "hrwind", "wind_up": "vuwind", "wind_down": "vdwind",
               "squeeze_h": "squeezeh", "squeeze_v": "squeezev"}
EFFECTS = ("fade_in", "fade_out", "grayscale", "sepia", "blur", "brightness", "contrast", "saturation", "speed", "zoom", "mirror",
           "flip", "rotate", "vignette", "sharpen", "grain", "vintage", "warm", "cool", "hue", "invert", "pixelate", "sketch",
           "glitch", "trails", "letterbox")
MAX_CLIPS, MAX_SECONDS = 60, 600

TIMELINE_HELP = (
    "The timeline is JSON: {\"width\": 1920, \"height\": 1080, \"fps\": 30, \"clips\": [...], \"titles\": [...], \"audio\": [...], "
    "\"clip_audio\": true}. clips play in order on one track; each is {\"src\": full path of a video or picture} or "
    "{\"color\": \"#000000\"}, with \"in\" and \"out\" (seconds into a video) or \"duration\" (pictures, colours; default 4), "
    "\"fit\": \"contain\" (bars, default) or \"cover\" (crop to fill), \"transition\": {\"type\": one of "
    + ", ".join(TRANSITIONS) + ", \"duration\": seconds} for the change from the previous clip (a cut when left out), and "
    "\"effects\": a list of {\"type\": ...}: fade_in/fade_out {duration}, grayscale, sepia, blur {amount 1-20}, brightness "
    "{value -1..1}, contrast {value 0..3}, saturation {value 0..3}, speed {factor 0.25-4}, zoom {from, to: e.g. 1 to 1.2, a slow "
    "Ken Burns move, best on pictures}, mirror, flip (upside down), rotate {angle 90, 180 or 270}, vignette {amount 0-1}, "
    "sharpen {amount 0-2}, grain {amount 0-50}, vintage (faded film colours), warm {amount 0-1}, cool {amount 0-1}, hue "
    "{degrees -180..180}, invert, pixelate {size 2-64}, sketch (pencil drawing), glitch {amount 1-30, colour channels "
    "shifted}, trails {frames 2-10, motion echo}, letterbox {ratio, default 2.39: cinema bars}. titles are text over the video: {\"text\", \"start\", \"duration\", \"position\": "
    "top/center/bottom, \"size\" (pixels, default 6% of the height), \"color\" (default white), \"box\": true for a dark band "
    "behind it}; Greek and other scripts work. audio is music or voice: {\"src\", \"start\" (on the timeline), \"in\", "
    "\"duration\", \"volume\" (0-2, default 1), \"fade_in\", \"fade_out\"}. clip_audio keeps the clips' own sound (default true).")

TOOLS = [
    {"name": "render_video",
     "description": "Edit and render a video with ffmpeg from a timeline, and show still frames of the result. " + TIMELINE_HELP +
                    " Use preview: true for a quick small render while you work, then render the final one. Paths are full "
                    "paths on this machine (the project folder's full path is given in your instructions). The timeline is "
                    "saved as <output without .mp4>.timeline.json; read it back to make a change and render again.",
     "inputSchema": {"type": "object", "properties": {
         "timeline": {"type": "object", "description": "The edit, as described above"},
         "output": {"type": "string", "description": "Full path of the MP4 to write, inside the project folder"},
         "preview": {"type": "boolean", "description": "Quick 640-pixel-wide render; default false"}},
         "required": ["timeline", "output"]}},
    {"name": "probe_media",
     "description": "Length, size, frame rate and sound of a video, picture or audio file, before putting it on a timeline.",
     "inputSchema": {"type": "object", "properties": {"path": {"type": "string", "description": "Full path of the file"}},
                     "required": ["path"]}},
    {"name": "video_frames",
     "description": "Still frames from a video at the given times, as pictures to look at.",
     "inputSchema": {"type": "object", "properties": {
         "path": {"type": "string", "description": "Full path of the video"},
         "times": {"type": "array", "items": {"type": "number"}, "maxItems": 6, "description": "Seconds; default start, middle and end"}},
         "required": ["path"]}},
]


class Failure(Exception):
    pass


def allowed(path, must_exist=True, roots=None, bases=()):
    """The real path, if it lies inside the allowed folders (ROOTS, or roots when Studio renders for one project).
    A relative path counts from the first of bases (folders, nearest first) where that file exists."""
    roots = roots or ROOTS
    if isinstance(path, str) and path.strip() and not os.path.isabs(os.path.expanduser(path)) and bases:
        found = [os.path.join(b, path) for b in bases if os.path.isfile(os.path.join(b, path))]
        path = found[0] if found else os.path.join(bases[-1], path)
    if not isinstance(path, str) or not os.path.isabs(os.path.expanduser(path)):
        raise Failure(f"{path!r} is not a full path; use the project folder's full path")
    real = os.path.realpath(os.path.expanduser(path))
    if not any(real == root or real.startswith(root + os.sep) for root in roots):
        raise Failure(f"{path} is outside the folders this tool may use: {', '.join(roots)}")
    if must_exist and not os.path.isfile(real):
        raise Failure(f"no file {path}")
    return real


def run(argv, timeout=120):
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise Failure(f"{argv[0]} is not installed (sudo apt install ffmpeg)") from None
    except subprocess.TimeoutExpired:
        raise Failure(f"{os.path.basename(argv[0])} took longer than {timeout} s") from None
    if proc.returncode:
        raise Failure(f"{os.path.basename(argv[0])} failed: " + proc.stderr.strip()[-1500:])
    return proc.stdout


def probe(path):
    data = json.loads(run([FFPROBE, "-v", "error", "-show_format", "-show_streams", "-of", "json", path]) or "{}")
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    fps = None
    if video and video.get("avg_frame_rate", "0/0") not in ("0/0", ""):
        num, den = video["avg_frame_rate"].split("/")
        fps = round(int(num) / int(den), 3) if int(den) else None
    duration = float(data.get("format", {}).get("duration") or 0)
    still = bool(video) and (path.lower().endswith(IMAGE_EXT) or (video.get("codec_name") in ("png", "mjpeg", "webp", "bmp") and duration < 0.1))
    return {"duration": round(duration, 3), "width": video and video.get("width"), "height": video and video.get("height"),
            "fps": fps, "audio": bool(audio), "picture": still, "video_codec": video and video.get("codec_name"),
            "audio_codec": audio and audio.get("codec_name")}


def num(obj, key, default, lo, hi):
    value = obj.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Failure(f"{key} must be a number")
    if not lo <= value <= hi:
        raise Failure(f"{key} must be between {lo:g} and {round(hi, 2):g}")
    return float(value)


def start_within(item, length, what, transitions):
    """An item's start, which must fall inside the video; the error says by how much and why the video is shorter."""
    start = num(item, "start", 0, 0, MAX_SECONDS)
    if start > length:
        why = f" ({transitions} transition{'s' if transitions > 1 else ''} overlap{'' if transitions > 1 else 's'} neighbouring clips, so the video is shorter than its clips added up)" if transitions else ""
        raise Failure(f"{what} starts at {start:g} s, after the video ends at {round(length, 2):g} s{why}; start it earlier or lengthen the clips")
    return start


def color(value, default):
    value = value or default
    if not isinstance(value, str) or not value.startswith("#") or len(value) not in (7, 9) or any(c not in "0123456789abcdefABCDEF" for c in value[1:]):
        raise Failure(f"colours are written like #1a2b3c, not {value!r}")
    return "0x" + value[1:7] + ("@" + str(round(int(value[7:9], 16) / 255, 3)) if len(value) == 9 else "")


def clip_video_chain(clip, idx, w, h, fps, dur, info):
    """The ffmpeg filters that turn input idx into a W x H clip of dur seconds, with the clip's effects."""
    fit = clip.get("fit", "contain")
    if fit not in ("contain", "cover"):
        raise Failure("fit must be contain or cover")
    effects = clip.get("effects") or []
    if not isinstance(effects, list):
        raise Failure("effects must be a list")
    zoom = next((e for e in effects if isinstance(e, dict) and e.get("type") == "zoom"), None)
    f = []
    for e in effects:
        if isinstance(e, dict) and e.get("type") == "rotate":
            angle = int(num(e, "angle", 90, 0, 360)) // 90 * 90 % 360
            f += {90: ["transpose=1"], 180: ["hflip", "vflip"], 270: ["transpose=2"]}.get(angle, [])
    if zoom:
        # Scaled up first so the slow zoom does not shake, then zoompan makes one output frame per input frame.
        z0, z1 = num(zoom, "from", 1.0, 1.0, 3.0), num(zoom, "to", 1.2, 1.0, 3.0)
        frames = max(1, round(dur * fps))
        f += [f"scale={w * 2}:{h * 2}:force_original_aspect_ratio=increase", f"crop={w * 2}:{h * 2}",
              f"zoompan=z='{z0}+({z1}-{z0})*on/{frames}':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={w}x{h}:fps={fps}"]
    elif fit == "cover":
        f += [f"scale={w}:{h}:force_original_aspect_ratio=increase", f"crop={w}:{h}"]
    else:
        f += [f"scale={w}:{h}:force_original_aspect_ratio=decrease", f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black"]
    for e in effects:
        if not isinstance(e, dict) or e.get("type") not in EFFECTS:
            raise Failure(f"unknown effect {e!r}; the effects are {', '.join(EFFECTS)}")
        kind = e["type"]
        if kind == "speed":
            f.append(f"setpts=PTS/{num(e, 'factor', 1, 0.25, 4)}")
        elif kind == "grayscale":
            f.append("hue=s=0")
        elif kind == "sepia":
            f.append("colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131")
        elif kind == "blur":
            f.append(f"boxblur={int(num(e, 'amount', 5, 1, 20))}")
        elif kind == "brightness":
            f.append(f"eq=brightness={num(e, 'value', 0.1, -1, 1)}")
        elif kind == "contrast":
            f.append(f"eq=contrast={num(e, 'value', 1.2, 0, 3)}")
        elif kind == "saturation":
            f.append(f"eq=saturation={num(e, 'value', 1.3, 0, 3)}")
        elif kind == "mirror":
            f.append("hflip")
        elif kind == "flip":
            f.append("vflip")
        elif kind == "vignette":
            f.append(f"vignette=angle={0.2 + 0.6 * num(e, 'amount', 0.5, 0, 1):.3f}")
        elif kind == "sharpen":
            f.append(f"unsharp=5:5:{num(e, 'amount', 1, 0, 2):.2f}")
        elif kind == "grain":
            f.append(f"noise=alls={int(num(e, 'amount', 15, 0, 50))}:allf=t+u")
        elif kind == "vintage":
            f.append("curves=preset=vintage")
        elif kind == "warm":
            f.append(f"colortemperature=temperature={6500 - 3500 * num(e, 'amount', 0.5, 0, 1):.0f}")
        elif kind == "cool":
            f.append(f"colortemperature=temperature={6500 + 5500 * num(e, 'amount', 0.5, 0, 1):.0f}")
        elif kind == "hue":
            f.append(f"hue=h={num(e, 'degrees', 30, -180, 180)}")
        elif kind == "invert":
            f.append("negate")
        elif kind == "pixelate":
            n = int(num(e, "size", 12, 2, 64))
            f.append(f"pixelize=w={n}:h={n}")
        elif kind == "sketch":
            f += ["edgedetect=mode=colormix:high=0.2:low=0.05", "hue=s=0", "negate"]
        elif kind == "glitch":
            n = int(num(e, "amount", 8, 1, 30))
            f.append(f"rgbashift=rh={n}:bh={-n}:gv={n // 2}")
        elif kind == "trails":
            n = int(num(e, "frames", 4, 2, 10))
            f.append(f"tmix=frames={n}")
    for e in effects:
        if e.get("type") == "letterbox":
            bar = round((h - w / num(e, "ratio", 2.39, 1, 4)) / 2)
            if bar > 0:
                f.append(f"drawbox=x=0:y=0:w=iw:h={bar}:color=black:t=fill,drawbox=x=0:y=ih-{bar}:w=iw:h={bar}:color=black:t=fill")
    f += [f"fps={fps}", "setsar=1", "format=yuv420p", f"trim=duration={dur:.3f}", "setpts=PTS-STARTPTS", f"settb=1/{fps}"]   # one timebase, or xfade refuses a zoomed picture next to a plain one
    for e in effects:
        if e.get("type") == "fade_in":
            f.append(f"fade=t=in:st=0:d={num(e, 'duration', 1, 0.1, dur)}")
        elif e.get("type") == "fade_out":
            d = num(e, "duration", 1, 0.1, dur)
            f.append(f"fade=t=out:st={dur - d:.3f}:d={d}")
    return f"[{idx}:v]" + ",".join(f) + f"[v{idx}]"


def build(timeline, output, preview, work, roots=None):
    """The ffmpeg command for a timeline, and its total length."""
    if not isinstance(timeline, dict):
        raise Failure("timeline must be an object")
    w, h = int(num(timeline, "width", 1920, 160, 3840)), int(num(timeline, "height", 1080, 160, 2160))
    w, h = w - w % 2, h - h % 2
    fps = num(timeline, "fps", 30, 10, 60)
    full_height = h
    if preview:
        w, h = 640, max(2, round(640 * h / w / 2) * 2)
    scale = h / full_height                 # titles shrink with a preview
    # Relative media paths ("assets/sky.png") count from the project: Studio's editor names it (roots[0]); for the agent's own
    # render, from the output's folder and each folder above it, inside the allowed folders.
    if roots:
        bases = [roots[0]]
    else:
        bases, here = [], os.path.dirname(os.path.realpath(os.path.expanduser(output))) if isinstance(output, str) and os.path.isabs(os.path.expanduser(output)) else ""
        while here and any(here == r or here.startswith(r + os.sep) for r in ROOTS):
            bases.append(here)
            here = os.path.dirname(here)
    clips = timeline.get("clips")
    if not isinstance(clips, list) or not clips or len(clips) > MAX_CLIPS:
        raise Failure(f"clips must be a list of 1 to {MAX_CLIPS} clips")
    inputs, chains, durations, transitions, audio_parts, sources = [], [], [], [], [], []
    keep_audio = timeline.get("clip_audio", True) is not False
    for i, clip in enumerate(clips):
        if not isinstance(clip, dict):
            raise Failure("each clip must be an object")
        speed = next((num(e, "factor", 1, 0.25, 4) for e in clip.get("effects") or [] if isinstance(e, dict) and e.get("type") == "speed"), 1.0)
        info = {"audio": False, "picture": True}
        if "color" in clip:
            dur = num(clip, "duration", 4, 0.1, MAX_SECONDS)
            inputs += ["-f", "lavfi", "-t", f"{dur:.3f}", "-i", f"color=c={color(clip['color'], '#000000')}:s={w}x{h}:r={fps}"]
        else:
            path = allowed(clip.get("src"), roots=roots, bases=bases)
            info = probe(path)
            if info["picture"]:
                dur = num(clip, "duration", 4, 0.1, MAX_SECONDS)
                inputs += ["-loop", "1", "-framerate", str(fps), "-t", f"{dur:.3f}", "-i", path]
            else:
                start = num(clip, "in", 0, 0, max(0.0, info["duration"]))
                end = num(clip, "out", info["duration"], start + 0.1, max(start + 0.1, info["duration"]))
                dur = (end - start) / speed
                inputs += ["-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", path]
        chains.append(clip_video_chain(clip, i, w, h, fps, dur, info))
        sources.append(info.get("duration") if not info.get("picture") and "color" not in clip else None)
        durations.append(dur)
        t = clip.get("transition")
        if i and isinstance(t, dict):
            kind = t.get("type", "dissolve")
            if kind not in TRANSITIONS:
                raise Failure(f"unknown transition {kind!r}; the transitions are {', '.join(TRANSITIONS)}")
            transitions.append((TRANSITIONS[kind], num(t, "duration", 1, 0.1, min(durations[-2], dur) - 0.05)))
        else:
            transitions.append(None)
        if keep_audio and info.get("audio") and not info.get("picture"):
            tempo = f",atempo={speed}" if speed != 1 else ""
            audio_parts.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo{tempo},atrim=duration={dur:.3f},asetpts=PTS-STARTPTS[a{i}]")
        else:
            audio_parts.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={dur:.3f}[a{i}]")
    # One track: cuts join with concat, transitions overlap the clips with xfade (and acrossfade for the sound).
    graph = chains + audio_parts
    v, a, length = "v0", "a0", durations[0]
    starts = [0.0]
    for i in range(1, len(clips)):
        if transitions[i]:
            kind, d = transitions[i]
            graph.append(f"[{v}][v{i}]xfade=transition={kind}:duration={d}:offset={length - d:.3f}[x{i}]")
            graph.append(f"[{a}][a{i}]acrossfade=d={d}[y{i}]")
            starts.append(length - d)
            length += durations[i] - d
        else:
            graph.append(f"[{v}][v{i}]concat=n=2:v=1:a=0,settb=1/{fps}[x{i}]")   # concat gives 1/1000000, which a later xfade refuses
            graph.append(f"[{a}][a{i}]concat=n=2:v=0:a=1[y{i}]")
            starts.append(length)
            length += durations[i]
        v, a = f"x{i}", f"y{i}"
    if length > MAX_SECONDS:
        raise Failure(f"the video would be {length:.0f} s; the limit is {MAX_SECONDS} s")
    layout = {"length": round(length, 3), "clips": [], "titles": [], "audio": []}
    for i, clip in enumerate(clips):
        layout["clips"].append({"start": round(starts[i], 3), "duration": round(durations[i], 3),
                                "label": os.path.basename(clip["src"]) if clip.get("src") else f"colour {clip.get('color')}",
                                "transition": (clip.get("transition") or {}).get("type", "dissolve") if transitions[i] else None,
                                "effects": [e.get("type") for e in clip.get("effects") or [] if isinstance(e, dict)],
                                "kind": "colour" if "color" in clip else "picture" if sources[i] is None else "video", "source": sources[i]})
    overlaps = sum(1 for c in clips[1:] if isinstance(c, dict) and c.get("transition"))
    titles = timeline.get("titles") or []
    if not isinstance(titles, list):
        raise Failure("titles must be a list")
    for n, title in enumerate(titles):
        if not isinstance(title, dict) or not isinstance(title.get("text"), str) or not title["text"].strip():
            raise Failure("each title needs text")
        start = start_within(title, length, f"title {n + 1} ({title['text'].strip()[:40]!r})", overlaps)
        end = min(length, start + num(title, "duration", 3, 0.2, MAX_SECONDS))
        size = max(8, round(num(title, "size", full_height * 0.06, 8, 400) * scale))
        position = title.get("position", "bottom")
        y = {"top": "h*0.08", "center": "(h-text_h)/2", "bottom": "h-text_h-h*0.08"}.get(position)
        if y is None:
            raise Failure("position must be top, center or bottom")
        layout["titles"].append({"start": round(start, 3), "duration": round(end - start, 3), "label": title["text"].strip()[:80]})
        text_file = os.path.join(work, f"title{n}.txt")
        with open(text_file, "w", encoding="utf-8") as fh:
            fh.write(title["text"].strip())
        fade = min(0.4, (end - start) / 4)
        alpha = f"if(lt(t,{start + fade:.3f}),(t-{start:.3f})/{fade:.3f},if(gt(t,{end - fade:.3f}),({end:.3f}-t)/{fade:.3f},1))"
        box = ":box=1:boxcolor=black@0.45:boxborderw=" + str(max(4, size // 3)) if title.get("box") else ":shadowcolor=black@0.6:shadowx=2:shadowy=2"
        graph.append(f"[{v}]drawtext=fontfile={FONT}:textfile={text_file}:fontsize={size}:fontcolor={color(title.get('color'), '#ffffff')}"
                     f":x=(w-text_w)/2:y={y}{box}:alpha='{alpha}':enable='between(t,{start:.3f},{end:.3f})'[t{n}]")
        v = f"t{n}"
    music = timeline.get("audio") or []
    if not isinstance(music, list):
        raise Failure("audio must be a list")
    mixes = [f"[{a}]"]
    for m, item in enumerate(music):
        if not isinstance(item, dict):
            raise Failure("each audio item must be an object")
        path = allowed(item.get("src"), roots=roots, bases=bases)
        info = probe(path)
        if not info["audio"]:
            raise Failure(f"{path} has no sound")
        start = start_within(item, length, f"sound {m + 1} ({os.path.basename(path)})", overlaps)
        cut_in = num(item, "in", 0, 0, max(0.0, info["duration"]))
        dur = min(num(item, "duration", info["duration"] - cut_in, 0.1, MAX_SECONDS), info["duration"] - cut_in, length - start)
        idx = len(clips) + m
        inputs += ["-ss", f"{cut_in:.3f}", "-t", f"{dur:.3f}", "-i", path]
        chain = [f"aresample=48000", "aformat=channel_layouts=stereo", f"volume={num(item, 'volume', 1, 0, 2)}"]
        if item.get("fade_in"):
            chain.append(f"afade=t=in:st=0:d={num(item, 'fade_in', 1, 0.1, dur)}")
        if item.get("fade_out"):
            d = num(item, "fade_out", 1, 0.1, dur)
            chain.append(f"afade=t=out:st={dur - d:.3f}:d={d}")
        delay = round(start * 1000)
        chain.append(f"adelay={delay}|{delay}")
        layout["audio"].append({"start": round(start, 3), "duration": round(dur, 3), "label": os.path.basename(path)})
        graph.append(f"[{idx}:a]" + ",".join(chain) + f"[m{m}]")
        mixes.append(f"[m{m}]")
    if len(mixes) > 1:
        graph.append("".join(mixes) + f"amix=inputs={len(mixes)}:duration=first:normalize=0[aout]")
        a = "aout"
    quality = ["-preset", "veryfast", "-crf", "28"] if preview else ["-preset", "medium", "-crf", "20"]
    argv = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", *inputs, "-filter_complex", ";".join(graph),
            "-map", f"[{v}]", "-map", f"[{a}]", "-c:v", "libx264", *quality, "-pix_fmt", "yuv420p", "-r", str(fps),
            "-c:a", "aac", "-b:a", "192k", "-t", f"{length:.3f}", "-movflags", "+faststart", output]
    return argv, length, layout


def frames(path, times, width=640):
    content = []
    with tempfile.TemporaryDirectory() as work:
        for n, t in enumerate(times):
            out = os.path.join(work, f"f{n}.png")
            run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, t):.3f}", "-i", path, "-frames:v", "1",
                 "-vf", f"scale={width}:-2", out], timeout=60)
            if os.path.isfile(out):
                with open(out, "rb") as fh:
                    content.append({"type": "image", "data": base64.b64encode(fh.read()).decode(), "mimeType": "image/png"})
                content.append({"type": "text", "text": f"Frame at {t:.1f} s."})
    return content


def tell_progress(token, done, total, message):
    """An MCP progress notification, when the caller asked for them with a progress token."""
    if token is None:
        return
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/progress",
                                 "params": {"progressToken": token, "progress": round(done, 2), "total": round(total, 2), "message": message}},
                                ensure_ascii=False) + "\n")
    sys.stdout.flush()


def render_following(argv, length, token):
    """Runs ffmpeg, reporting how far it is into the video once a second."""
    argv = argv[:-1] + ["-progress", "pipe:1", "-nostats", argv[-1]]
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    deadline, last = time.time() + RENDER_TIMEOUT_S, 0.0
    tell_progress(token, 0, length, f"Rendering 0 of {length:.0f} s")
    for line in proc.stdout:
        if time.time() > deadline:
            proc.kill()
            raise Failure(f"the render took longer than {RENDER_TIMEOUT_S} s")
        if line.startswith("out_time_us=") and line.strip()[12:].isdigit() and time.time() - last >= 1:
            last = time.time()
            done = min(length, int(line.strip()[12:]) / 1e6)
            tell_progress(token, done, length, f"Rendering {done:.0f} of {length:.0f} s")
    err = proc.stderr.read()
    if proc.wait() != 0:
        raise Failure("ffmpeg failed: " + (err.strip()[-600:] or f"exit {proc.returncode}"))
    tell_progress(token, length, length, "Rendered; checking the result")


def call(name, args, token=None):
    if name == "probe_media":
        path = allowed(args.get("path"))
        return [{"type": "text", "text": json.dumps({"path": path, **probe(path)})}]
    if name == "video_frames":
        path = allowed(args.get("path"))
        info = probe(path)
        times = args.get("times") or [0.5, info["duration"] / 2, max(0.0, info["duration"] - 0.5)]
        if not isinstance(times, list) or len(times) > 6 or not all(isinstance(t, (int, float)) and not isinstance(t, bool) for t in times):
            raise Failure("times must be a list of at most 6 numbers")
        return frames(path, [min(float(t), max(0.0, info["duration"] - 0.05)) for t in times])
    if name == "render_video":
        output = allowed(args.get("output"), must_exist=False)
        if not output.lower().endswith(".mp4"):
            raise Failure("output must end in .mp4")
        if not os.path.isdir(os.path.dirname(output)):
            os.makedirs(os.path.dirname(output), exist_ok=True)
        timeline, preview = args.get("timeline"), args.get("preview") is True
        started = time.time()
        with tempfile.TemporaryDirectory() as work:
            argv, length, layout = build(timeline, output, preview, work)
            partial = output[:-4] + ".rendering.mp4"
            argv[-1] = partial
            try:
                render_following(argv, length, token)
                os.replace(partial, output)
            finally:
                if os.path.exists(partial):
                    os.remove(partial)
        saved = output[:-4] + ".timeline.json"
        with open(saved, "w", encoding="utf-8") as fh:        # _layout: where everything fell, for Studio's timeline view
            json.dump({**{k: v for k, v in timeline.items() if k != "_layout"}, "_layout": {**layout, "video": os.path.basename(output)}},
                      fh, ensure_ascii=False, indent=1)
        info = probe(output)
        summary = (f"{'Preview' if preview else 'Video'} saved as {output}: {info['duration']:.1f} s, {info['width']}x{info['height']}, "
                   f"{os.path.getsize(output) // 1024} KB, rendered in {time.time() - started:.0f} s. Timeline saved as {saved}.")
        return [{"type": "text", "text": summary}] + frames(output, [0.5, info["duration"] / 2, max(0.0, info["duration"] - 0.5)])
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
            answer(msg_id, {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}},
                            "serverInfo": {"name": "video-local", "version": "1.0"}})
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
            answer(msg_id, {"content": content, "isError": failed})
        else:
            answer(msg_id, error={"code": -32601, "message": f"unknown method {method!r}"})


if __name__ == "__main__":
    main()
