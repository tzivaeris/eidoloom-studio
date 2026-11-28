#!/usr/bin/env python3
"""An MCP server (stdio) that makes pictures with a local ComfyUI: generate_image, generate_images, get_image, list_models.

Models: z-image-turbo (Tongyi-MAI, Apache 2.0; realistic, follows detailed prompts, 8 steps), z-image-base (the same family
in full: more variety, negative prompts work, 25 steps), sdxl (painted styles, varied) and qwen-image (Qwen-Image-2512,
Apache 2.0, 20B: the best quality and text in pictures). The first three fit beside the Strata engine; qwen-image needs the
whole graphics card, so the server first unloads Strata through the Strata gateway ("swap mode"). Strata loads again by
itself on the chat's next request.

It talks only to the ComfyUI at COMFYUI_URL and the gateway at STRATA_MANAGER_URL, so prompts and pictures stay on this
machine. Pictures come back as MCP image content, which Studio shows in the chat and passes to models that can see.
"""
import base64
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

COMFYUI_URL = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188").rstrip("/")
MANAGER_URL = os.environ.get("STRATA_MANAGER_URL", "http://127.0.0.1:8787").rstrip("/")
OUTPUT_DIR = os.path.expanduser(os.environ.get("COMFYUI_OUTPUT_DIR", "~/.local/opt/comfyui/output"))
DEFAULT_CHECKPOINT = os.environ.get("COMFYUI_CHECKPOINT", "sd_xl_base_1.0.safetensors")
# The user's default model, chosen in Studio's ComfyUI panel; "auto" means SDXL for painted styles, Z-Image Turbo otherwise.
SETTINGS = os.environ.get("COMFYUI_SETTINGS", "")
# While this file says so, ComfyUI's custom_nodes/strata_vram_cap.py lets it use the whole card.
SWAP_FILE = os.path.expanduser(os.environ.get("COMFYUI_SWAP_FILE", "~/.local/opt/comfyui/user/strata_swap.json"))
PAINTED = re.compile(r"\b(water ?colou?r|oil painting|painting|painted|gouache|illustration|illustrated|drawing|drawn|sketch|"
                     r"cartoon|comic|anime|manga|pixel art|storybook|children'?s book|ink wash|pastel|crayon|vector art)\b", re.I)
WAIT_S = int(os.environ.get("COMFYUI_WAIT_S", "110"))           # Studio gives a tool call 120 s unless the server sets timeout_s
SWAP_WAIT_S = int(os.environ.get("COMFYUI_SWAP_WAIT_S", "1500"))  # a whole swap batch, with Studio's timeout_s above it
BUSY_WAIT_S = int(os.environ.get("COMFYUI_BUSY_WAIT_S", "90"))    # for another client's Strata request to end before a swap
# Unload the model after each picture: kept in system RAM it holds about 12 GB, next to a Strata engine that needs most of the rest.
FREE_AFTER = os.environ.get("COMFYUI_FREE_AFTER", "1") != "0"
PROTOCOL = "2025-06-18"
CLIENT_ID = os.environ.get("COMFYUI_CLIENT_ID", "strata-studio")    # Studio's ComfyUI dashboard listens as this for step progress
SAMPLERS = ("euler", "euler_ancestral", "dpmpp_2m", "dpmpp_2m_sde", "dpmpp_sde", "ddim", "res_multistep")
MAX_BATCH = 8

# As in ComfyUI's own templates (Comfy-Org/workflow_templates), in the sizes that fit this machine.
SPECS = {
    "z-image-turbo": {"unet": os.environ.get("COMFYUI_ZIMAGE_MODEL", "z_image_turbo_int8_convrot.safetensors"),
                      "clip": "qwen_3_4b_fp8_mixed.safetensors", "clip_type": "lumina2", "vae": "ae.safetensors", "shift": 3,
                      "steps": 8, "cfg": 1.0, "sampler": "res_multistep", "negative": False, "size": 1024, "max": 1536, "swap": False},
    "z-image-base": {"unet": "z_image_int8_convrot.safetensors", "clip": "qwen_3_4b_fp8_mixed.safetensors", "clip_type": "lumina2",
                     "vae": "ae.safetensors", "shift": 3, "steps": 25, "cfg": 4.0, "sampler": "res_multistep", "negative": True,
                     "size": 1024, "max": 1536, "swap": False},
    "sdxl": {"checkpoint": True, "steps": 25, "cfg": 7.0, "sampler": "dpmpp_2m", "negative": True, "size": 1024, "max": 1536, "swap": False},
    "qwen-image": {"unet": "qwen_image_2512_fp8_e4m3fn.safetensors", "clip": "qwen_2.5_vl_7b_fp8_scaled.safetensors",
                   "clip_type": "qwen_image", "vae": "qwen_image_vae.safetensors", "shift": 3.1, "steps": 20, "cfg": 4.0,
                   "sampler": "euler", "negative": True, "size": 1328, "max": 1664, "swap": True},
}
MODELS = tuple(SPECS)
# Wan 2.2 TI2V-5B (Wan-AI, Apache 2.0) as in ComfyUI's video_wan2_2_5B_ti2v template; video always gets the whole card.
SPECS["wan-video"] = {"unet": "wan2.2_ti2v_5B_fp16.safetensors", "clip": "umt5_xxl_fp8_e4m3fn_scaled.safetensors", "clip_type": "wan",
                      "vae": "wan2.2_vae.safetensors", "steps": 20, "cfg": 5.0, "sampler": "uni_pc", "swap": True, "video": True}
# ACE-Step 1.5 (StepFun/ACE-Step, MIT) as in ComfyUI's audio_ace_step_1_5 templates: songs with lyrics in 50 languages
# (Greek is "el") or instrumentals, 10 s to 10 min. Turbo (2B) fits beside Strata; XL (4B) needs the whole card.
SPECS["ace-music"] = {"unet": "acestep_v1.5_turbo.safetensors", "clips": ("qwen_0.6b_ace15.safetensors", "qwen_1.7b_ace15.safetensors"),
                      "vae": "ace_1.5_vae.safetensors", "steps": 8, "swap": False, "music": True}
SPECS["ace-music-xl"] = {"unet": "acestep_v1.5_xl_turbo_bf16.safetensors", "clips": ("qwen_0.6b_ace15.safetensors", "qwen_4b_ace15.safetensors"),
                         "vae": "ace_1.5_vae.safetensors", "steps": 8, "swap": True, "music": True}
MUSIC_LANGUAGES = ("el", "en", "de", "fr", "it", "es", "pt", "nl", "ru", "tr", "ar", "zh", "ja", "ko")
MUSIC_KEYS = [f"{root} {quality}" for quality in ("major", "minor") for root in ("C", "C#", "Db", "D", "D#", "Eb", "E", "F", "F#", "Gb", "G", "G#", "Ab", "A", "A#", "Bb", "B")]
MUSIC_WAIT_S = int(os.environ.get("COMFYUI_MUSIC_WAIT_S", "1500"))
# HeartMuLa-oss-3B (HeartMuLa team, Apache 2.0) runs in Studio's own music engine (tools/music_engine.py, service
# strata-music), not in ComfyUI; the user picks which of the two makes songs in Studio's ComfyUI panel ("music_model").
MUSIC_URL = os.environ.get("MUSIC_URL", "http://127.0.0.1:8191").rstrip("/")
MUSIC_MODELS = ("ace-step", "heartmula")
PROGRESS = {"token": None}  # the progress token of the call being answered
AUDIO_MADE = []          # the audio files of the last collect, for generate_music's copy into the project
VIDEO_MADE = []          # the videos of the last collect, for generate_video's copy into the project
VIDEO_SIZES = ("1280x704", "704x1280", "960x544", "544x960", "832x480", "480x832")
VIDEO_FPS = 24
# The template's own negative prompt, in Chinese as Wan was trained: garish colours, overexposed, static, blurred detail,
# subtitles, still frame, grey cast, worst quality, low quality, ugly, deformed hands and faces, extra fingers, crowded background.
WAN_NEGATIVE = "色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，整体发灰，最差质量，低质量，JPEG压缩残留，丑陋的，残缺的，多余的手指，画得不好的手部，画得不好的脸部，畸形的，毁容的，形态畸形的肢体，手指融合，静止不动的画面，杂乱的背景"
FILE_ROOTS = [os.path.realpath(os.path.expanduser(p)) for p in
              os.environ.get("COMFYUI_FILE_ROOTS", "~/Projects:~/.local/opt/comfyui/output").split(":") if p.strip()]
FFMPEG = os.environ.get("FFMPEG", "ffmpeg")
MODEL_HELP = ("z-image-turbo is photorealistic, follows long detailed prompts closely, draws hands well and can write a few "
              "words of English text, but ignores negative_prompt, so say what you want instead of what to avoid; "
              "z-image-base is the same family in full, with more variety and working negative prompts, a little slower; "
              "sdxl gives more varied pictures, suits painted and illustrated styles and honours negative_prompt, but cannot "
              "write text; qwen-image gives the best quality and the clearest text in pictures, but needs the whole graphics "
              "card, so it unloads you, the chat model, while it works: that adds two or three minutes, so put every picture "
              "a task needs into one generate_images call.")
PICTURE_PROPS = {
    "prompt": {"type": "string", "description": "What the picture shows"},
    "negative_prompt": {"type": "string", "description": "What to avoid, e.g. \"blurry, low quality, watermark\" (not for z-image-turbo)"},
    "width": {"type": "integer", "description": "Pixels; default 1024 (1328 for qwen-image)"},
    "height": {"type": "integer", "description": "Pixels; default 1024 (1328 for qwen-image)"},
    "seed": {"type": "integer", "description": "Repeatable result; random when left out"},
}
SHARED_PROPS = {
    "model": {"type": "string", "enum": list(MODELS), "description": "Only when the user asks for a model; otherwise the user's default from Studio"},
    "steps": {"type": "integer", "description": "Sampling steps, 1-60; each model has its own default"},
    "cfg": {"type": "number", "description": "How closely to follow the prompt, 1-15; keep 1 for z-image-turbo"},
    "sampler": {"type": "string", "enum": list(SAMPLERS), "description": "Each model has its own default"},
    "checkpoint": {"type": "string", "description": "For sdxl: a checkpoint file from list_models; default " + DEFAULT_CHECKPOINT},
}

TOOLS = [
    {"name": "generate_image",
     "description": "Make a picture from a text prompt with the local ComfyUI and show it. Leave model out unless the user "
                    "asks for a particular one: the user chooses the default in Studio. " + MODEL_HELP + " Describe the "
                    "subject, style, lighting and composition in English. Sizes around one megapixel work best: 1024x1024, "
                    "1152x896, 896x1152, 1344x768, 768x1344 (qwen-image: 1328x1328, 1664x928, 928x1664). The reply says which "
                    "model made the picture, the saved file and the picture's id for Studio. Use the same seed to change one "
                    "thing at a time.",
     "inputSchema": {"type": "object", "properties": {**PICTURE_PROPS, **SHARED_PROPS}, "required": ["prompt"]}},
    {"name": "generate_images",
     "description": "Make several pictures in one call (at most 8), all with the same model and settings, and show them in "
                    "order. Use it whenever a task needs more than one picture, and always with qwen-image, so the chat model "
                    "is unloaded only once.",
     "inputSchema": {"type": "object", "properties": {
         "pictures": {"type": "array", "minItems": 1, "maxItems": MAX_BATCH, "items": {"type": "object", "properties": PICTURE_PROPS,
                                                                                       "required": ["prompt"]}},
         **SHARED_PROPS}, "required": ["pictures"]}},
    {"name": "generate_video",
     "description": "Make a short video clip (1-5 seconds, 24 frames a second) from a text prompt, or bring a picture to life "
                    "with start_image, with Wan 2.2 in the local ComfyUI. It needs the whole graphics card, so it unloads you, "
                    "the chat model, while it works: several minutes. Describe the scene, the motion and the camera in English, "
                    "e.g. \"slow dolly forward through a cozy reading room, lamp light flickering, dust in the light\". The reply "
                    "names the MP4 and shows a few frames; give path to save it into the project, and put it on a video timeline "
                    "with the video tools to edit it.",
     "inputSchema": {"type": "object", "properties": {
         "prompt": {"type": "string", "description": "What happens in the clip, the motion and the camera"},
         "negative_prompt": {"type": "string", "description": "What to avoid; a good default is used when left out"},
         "seconds": {"type": "number", "description": "1 to 5; default 5"},
         "size": {"type": "string", "enum": list(VIDEO_SIZES), "description": "Default 1280x704 (landscape); 704x1280 is portrait; smaller sizes are faster"},
         "start_image": {"type": "string", "description": "Full path of a picture to start from (picture to video)"},
         "path": {"type": "string", "description": "Full path ending in .mp4 to copy the clip into the project, ready for a video timeline"},
         "steps": {"type": "integer", "description": "1-50; default 20"},
         "seed": {"type": "integer", "description": "Repeatable result; random when left out"}},
         "required": ["prompt"]}},
    {"name": "generate_music",
     "description": "Make music with ACE-Step 1.5 in the local ComfyUI: a song with lyrics (50 languages, Greek is \"el\") or an "
                    "instrumental, 5 s to 10 min, saved as MP3. Describe the style in English: genre, instruments, mood, tempo and "
                    "voice, e.g. \"cheerful children's song, ukulele and glockenspiel, bright female voice\". Lyrics take section tags "
                    "such as [Verse], [Chorus], [Bridge]; leave lyrics out for an instrumental. quality \"best\" uses the XL model, "
                    "which unloads you, the chat model, while it works. Give path to save it into the project, ready for a video timeline. "
                    "Two models: ace-step (ACE-Step 1.5) and heartmula (HeartMuLa-oss-3B: up to 4 minutes, strong on following lyrics; "
                    "about real time or slower, and it may unload you while it works; bpm, key and quality do not apply). Leave "
                    "model out unless the user asks for one: the user chooses the default in Studio, and the reply says which made it.",
     "inputSchema": {"type": "object", "properties": {
         "model": {"type": "string", "enum": list(MUSIC_MODELS), "description": "Only when the user asks for one; otherwise the user's default"},
         "style": {"type": "string", "description": "Genre, instruments, mood, tempo and voice, in English (for heartmula best as short comma-separated tags: piano,happy,female vocal)"},
         "lyrics": {"type": "string", "description": "The words to sing, with [Verse]/[Chorus] tags; leave out for an instrumental"},
         "language": {"type": "string", "enum": list(MUSIC_LANGUAGES), "description": "Language of the lyrics; Greek letters mean el"},
         "seconds": {"type": "number", "description": "5 to 600; default 60"},
         "bpm": {"type": "integer", "description": "30-220; default 100"},
         "key": {"type": "string", "description": "e.g. 'C major' or 'A minor'; default C major"},
         "quality": {"type": "string", "enum": ["fast", "best"], "description": "fast (default, beside you) or best (XL, the whole card)"},
         "path": {"type": "string", "description": "Full path of an .mp3 in the project to save it as"},
         "seed": {"type": "integer", "description": "Repeatable result; random when left out"}},
         "required": ["style"]}},
    {"name": "get_image",
     "description": "Fetch a picture that generate_image was still making when it answered, by its job id.",
     "inputSchema": {"type": "object", "properties": {"job": {"type": "string", "description": "The job id generate_image gave"}},
                     "required": ["job"]}},
    {"name": "list_models",
     "description": "List the picture models (which are installed, the user's default, which unload the chat model), and the graphics memory.",
     "inputSchema": {"type": "object", "properties": {}}},
]


class Failure(Exception):
    pass


def http(base, name, path, body=None, timeout=30, raw=False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data, {"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:1500]
        raise Failure(f"{name} answered {exc.code}: {detail}") from None
    except (urllib.error.URLError, OSError) as exc:
        raise Failure(f"{name} at {base} did not answer ({exc}). Is it running?") from None
    return payload if raw else json.loads(payload or b"{}")


def comfy(path, body=None, timeout=30, raw=False):
    return http(COMFYUI_URL, "ComfyUI", path, body, timeout, raw)


def manager(path, body=None, timeout=30):
    if not MANAGER_URL:                 # STRATA_MANAGER_URL="": no model server on this machine, so nothing to unload
        return {}
    return http(MANAGER_URL, "The Strata gateway", path, body, timeout)


def choices(node, field):
    """The files ComfyUI offers for a loader node's field."""
    info = comfy(f"/object_info/{node}").get(node) or {}
    options = ((info.get("input") or {}).get("required") or {}).get(field) or [[]]
    return list(options[0]) if isinstance(options[0], list) else []


def checkpoints():
    return choices("CheckpointLoaderSimple", "ckpt_name")


def missing(model, cache=None):
    """The files a model needs that ComfyUI does not have."""
    spec = SPECS[model]
    cache = cache if cache is not None else {}
    if spec.get("music"):
        for key, node, field in (("unet", "UNETLoader", "unet_name"), ("dual", "DualCLIPLoader", "clip_name1"), ("vae", "VAELoader", "vae_name")):
            if key not in cache:
                cache[key] = choices(node, field)
        return [f for f in (spec["unet"],) if f not in cache["unet"]] + [f for f in spec["clips"] if f not in cache["dual"]] + \
               [f for f in (spec["vae"],) if f not in cache["vae"]]
    if spec.get("checkpoint"):
        return [] if checkpoints() else ["an SDXL checkpoint"]
    cache = cache if cache is not None else {}
    for key, node, field in (("unet", "UNETLoader", "unet_name"), ("clip", "CLIPLoader", "clip_name"), ("vae", "VAELoader", "vae_name")):
        if key not in cache:
            cache[key] = choices(node, field)
    return [spec[key] for key in ("unet", "clip", "vae") if spec[key] not in cache[key]]


def chosen_default():
    """The default the user set in Studio: a model, or auto."""
    try:
        with open(SETTINGS, encoding="utf-8") as fh:
            chosen = json.load(fh).get("default_model")
    except (OSError, ValueError, AttributeError):
        chosen = None
    return chosen if chosen in MODELS + ("auto",) else "z-image-turbo"


def pick_model(args, prompt):
    """The model for a picture and why: asked for, the user's default, or a fallback when that one is not installed."""
    model = args.get("model")
    if model is not None:
        if model not in MODELS:
            raise Failure(f"model must be one of {', '.join(MODELS)}")
        return model, "asked for"
    if args.get("checkpoint"):
        return "sdxl", "for the checkpoint"
    chosen = chosen_default()
    model, how = (("sdxl" if PAINTED.search(prompt) else "z-image-turbo"), "chosen by style") if chosen == "auto" else (chosen, "the default")
    if model != "sdxl" and missing(model):
        fallback = "z-image-turbo" if model != "z-image-turbo" and not missing("z-image-turbo") else "sdxl"
        return fallback, f"{model} is not installed"
    return model, how


def number(args, key, default, lo, hi, kind=int):
    value = args.get(key, default)
    try:
        value = kind(value)
    except (TypeError, ValueError):
        raise Failure(f"{key} must be a number") from None
    if not lo <= value <= hi:
        raise Failure(f"{key} must be between {lo} and {hi}")
    return value


def workflow(args):
    """(model, graph, settings line) for one picture."""
    prompt = args.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise Failure("prompt is required")
    model, how = pick_model(args, prompt)
    spec = SPECS[model]
    grid = 64 if spec.get("checkpoint") else 16
    width, height = number(args, "width", spec["size"], 512, spec["max"]), number(args, "height", spec["size"], 512, spec["max"])
    if width % grid or height % grid:
        raise Failure(f"width and height must be multiples of {grid}")
    steps, cfg = number(args, "steps", spec["steps"], 1, 60), number(args, "cfg", spec["cfg"], 1.0, 15.0, float)
    seed = number(args, "seed", random.randrange(2 ** 32), 0, 2 ** 63 - 1)
    sampler = args.get("sampler") or spec["sampler"]
    if sampler not in SAMPLERS:
        raise Failure(f"sampler must be one of {', '.join(SAMPLERS)}")
    negative = (args.get("negative_prompt") or "").strip()
    if spec.get("checkpoint"):
        checkpoint = args.get("checkpoint") or DEFAULT_CHECKPOINT
        known = checkpoints()
        if checkpoint not in known:
            raise Failure(f"no model {checkpoint!r}; ComfyUI has: {', '.join(known) or 'none'}")
        graph = {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": checkpoint}},
            "2": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt.strip(), "clip": ["1", 1]}},
            "3": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["1", 1]}},
            "4": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
            "5": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0], "latent_image": ["4", 0],
                                                        "seed": seed, "steps": steps, "cfg": cfg, "sampler_name": sampler,
                                                        "scheduler": "karras", "denoise": 1.0}},
            "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
            "7": {"class_type": "SaveImage", "inputs": {"images": ["6", 0], "filename_prefix": "studio"}},
        }
        return model, graph, f"sdxl ({how}), {width}x{height}, {steps} steps, cfg {cfg:g}, {sampler}, seed {seed}, {checkpoint}"
    lacking = missing(model)
    if lacking:
        raise Failure(f"{model} is not installed in ComfyUI (missing {', '.join(lacking)}); use another model")
    # Without negative prompts (z-image-turbo) the negative side is zeroed and guidance stays at 1.
    negative_node = ({"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["8", 0]}} if spec["negative"]
                     else {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["2", 0]}})
    graph = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": spec["unet"], "weight_dtype": "default"}},
        "8": {"class_type": "CLIPLoader", "inputs": {"clip_name": spec["clip"], "type": spec["clip_type"], "device": "default"}},
        "9": {"class_type": "VAELoader", "inputs": {"vae_name": spec["vae"]}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt.strip(), "clip": ["8", 0]}},
        "3": negative_node,
        "4": {"class_type": "EmptySD3LatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "10": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": spec["shift"]}},
        "5": {"class_type": "KSampler", "inputs": {"model": ["10", 0], "positive": ["2", 0], "negative": ["3", 0], "latent_image": ["4", 0],
                                                    "seed": seed, "steps": steps, "cfg": cfg, "sampler_name": sampler,
                                                    "scheduler": "simple", "denoise": 1.0}},
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["9", 0]}},
        "7": {"class_type": "SaveImage", "inputs": {"images": ["6", 0], "filename_prefix": "studio"}},
    }
    ignored = " (negative_prompt ignored)" if negative and not spec["negative"] else ""
    return model, graph, f"{model} ({how}), {width}x{height}, {steps} steps, cfg {cfg:g}, {sampler}, seed {seed}{ignored}"


def upload_image(path):
    """Copies a picture into ComfyUI's input folder for LoadImage, and returns its name there."""
    real = os.path.realpath(os.path.expanduser(path)) if isinstance(path, str) else ""
    if not os.path.isabs(os.path.expanduser(path or "")) or not any(real == r or real.startswith(r + os.sep) for r in FILE_ROOTS):
        raise Failure(f"start_image must be a full path inside {', '.join(FILE_ROOTS)}")
    if not os.path.isfile(real):
        raise Failure(f"no picture {path}")
    boundary = "studio" + str(random.randrange(10 ** 12))
    with open(real, "rb") as fh:
        data = fh.read()
    name = "studio-" + os.path.basename(real)
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{name}\"\r\n"
            "Content-Type: application/octet-stream\r\n\r\n").encode() + data + \
           f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(COMFYUI_URL + "/upload/image", body, {"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read()).get("name", name)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise Failure(f"could not give ComfyUI the picture ({exc})") from None


def video_workflow(args):
    """("wan-video", graph, settings line) for one clip."""
    prompt = args.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise Failure("prompt is required")
    spec = SPECS["wan-video"]
    lacking = missing("wan-video")
    if lacking:
        raise Failure(f"Wan 2.2 is not installed in ComfyUI (missing {', '.join(lacking)})")
    size = args.get("size") or VIDEO_SIZES[0]
    if size not in VIDEO_SIZES:
        raise Failure(f"size must be one of {', '.join(VIDEO_SIZES)}")
    width, height = (int(n) for n in size.split("x"))
    seconds = number(args, "seconds", 5, 1, 5, float)
    frames = int(round(seconds * VIDEO_FPS / 4)) * 4 + 1          # Wan takes 4n+1 frames
    steps = number(args, "steps", spec["steps"], 1, 50)
    seed = number(args, "seed", random.randrange(2 ** 32), 0, 2 ** 63 - 1)
    latent = {"vae": ["39", 0], "width": width, "height": height, "length": frames, "batch_size": 1}
    graph = {
        "37": {"class_type": "UNETLoader", "inputs": {"unet_name": spec["unet"], "weight_dtype": "default"}},
        "38": {"class_type": "CLIPLoader", "inputs": {"clip_name": spec["clip"], "type": spec["clip_type"], "device": "default"}},
        "39": {"class_type": "VAELoader", "inputs": {"vae_name": spec["vae"]}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt.strip(), "clip": ["38", 0]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": (args.get("negative_prompt") or WAN_NEGATIVE).strip(), "clip": ["38", 0]}},
        "48": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["37", 0], "shift": 8}},
        "55": {"class_type": "Wan22ImageToVideoLatent", "inputs": latent},
        "3": {"class_type": "KSampler", "inputs": {"model": ["48", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["55", 0],
                                                    "seed": seed, "steps": steps, "cfg": spec["cfg"], "sampler_name": spec["sampler"],
                                                    "scheduler": "simple", "denoise": 1.0}},
        # Tiled, in chunks of frames: decoding 121 frames at 1280x704 at once asked for 18.6 GB on 2026-11-10 and ran out.
        "8": {"class_type": "VAEDecodeTiled", "inputs": {"samples": ["3", 0], "vae": ["39", 0], "tile_size": 256, "overlap": 64,
                                                          "temporal_size": 32, "temporal_overlap": 8}},
        "57": {"class_type": "CreateVideo", "inputs": {"images": ["8", 0], "fps": VIDEO_FPS}},
        "58": {"class_type": "SaveVideo", "inputs": {"video": ["57", 0], "filename_prefix": "video/studio", "format": "mp4", "codec": "h264"}},
    }
    start = ""
    if args.get("start_image"):
        graph["56"] = {"class_type": "LoadImage", "inputs": {"image": upload_image(args["start_image"])}}
        latent["start_image"] = ["56", 0]
        start = ", from " + os.path.basename(args["start_image"])
    return "wan-video", graph, f"wan-video, {size}, {frames} frames ({frames / VIDEO_FPS:.1f} s), {steps} steps, seed {seed}{start}"


def music_choice(args):
    """(model, why): the one asked for, else the user's default from Studio, else ace-step."""
    asked = args.get("model")
    if asked is not None:
        if asked not in MUSIC_MODELS:
            raise Failure(f"model must be one of {', '.join(MUSIC_MODELS)}")
        return asked, "asked for"
    try:
        with open(SETTINGS, encoding="utf-8") as fh:
            chosen = json.load(fh).get("music_model")
    except (OSError, ValueError, AttributeError):
        chosen = None
    return (chosen, "the user's default") if chosen in MUSIC_MODELS else ("ace-step", "the default")


def music_engine(path, body=None, timeout=15):
    req = urllib.request.Request(MUSIC_URL + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"}, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        try:
            detail = json.load(exc).get("error")
        except ValueError:
            detail = str(exc)
        raise Failure(f"the HeartMuLa engine refused: {detail}") from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise Failure(f"the HeartMuLa engine at {MUSIC_URL} does not answer ({exc}); is the strata-music service running?") from None


def tell_progress(percent, message, waiting=False):
    if PROGRESS["token"] is None:
        return
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/progress",
                                 "params": {"progressToken": PROGRESS["token"], "progress": percent, "total": 100,
                                            "message": message}}, ensure_ascii=False) + "\n")
    sys.stdout.flush()


HEART_WORDS = {"queued": "Waiting for the music engine…", "waiting_card": "Waiting for the graphics card…",
               "making_room": "Waiting for the graphics card: unloading Strata to make room…", "loading": "Loading HeartMuLa…",
               "writing": "Writing the song", "decoding": "Turning it into sound"}


def heartmula_song(args, target, why):
    """Makes a song with the HeartMuLa engine; MCP content, like render()'s."""
    style = args.get("style")
    if not isinstance(style, str) or not style.strip():
        raise Failure("style is required: the genre, instruments, mood and voice, e.g. 'acoustic,folk,guitar,flute,warm female vocal'")
    tags = ",".join(t.strip() for t in style.split(",") if t.strip())       # HeartMuLa learned tags as "a,b,c", no spaces
    lyrics = args.get("lyrics") if isinstance(args.get("lyrics"), str) and args.get("lyrics").strip() else "[instrumental]"
    seconds = number(args, "seconds", 60, 5, 240, float)
    seed = number(args, "seed", random.randrange(2 ** 32), 0, 2 ** 63 - 1)
    out = os.path.expanduser(target) if target else os.path.join(OUTPUT_DIR, "audio", f"heartmula_{time.strftime('%Y%m%d-%H%M%S')}.mp3")
    started = time.time()
    job = music_engine("/make", {"style": tags, "lyrics": lyrics, "seconds": seconds, "out": out, "format": "mp3", "seed": seed})["job"]
    deadline = started + MUSIC_WAIT_S
    try:
        while True:
            time.sleep(1)
            try:
                st = music_engine(f"/job?id={job}")
            except Failure as exc:
                if "unknown job" in str(exc):
                    raise Failure("the HeartMuLa engine restarted and lost this song; call generate_music again") from None
                if time.time() > deadline:
                    raise
                continue
            if st["state"] == "done":
                break
            if st["state"] in ("error", "cancelled"):
                raise Failure(f"HeartMuLa could not make it: {st.get('error')}")
            word = HEART_WORDS.get(st["state"], st["state"])
            tell_progress(round(100 * (st.get("progress") or 0)), word, st["state"] in ("queued", "waiting_card", "making_room", "loading"))
            if time.time() > deadline:
                music_engine("/cancel", {"job": job})
                raise Failure(f"the song was not finished within {MUSIC_WAIT_S // 60} minutes, so it was stopped")
    except BaseException:
        if not isinstance(sys.exc_info()[1], Failure):
            try:
                music_engine("/cancel", {"job": job})
            except Failure:
                pass
        raise
    size = os.path.getsize(out) // 1024 if os.path.exists(out) else 0
    sung = "instrumental" if lyrics == "[instrumental]" else "with lyrics"
    lines = [f"Audio saved as {out} ({size} KB)." if not target else f"Saved into the project as {target} ({size} KB).",
             f"heartmula (HeartMuLa-oss-3B, {why}), asked {seconds:g} s, made {st.get('audio_s') or '?'} s, {sung}, tags {tags}, "
             f"seed {seed}; took {time.time() - started:.0f} s."
             + (" HeartMuLa still ended it well before the length asked for; try again with another seed or more descriptive tags, "
                "or use ace-step, which always makes the full length." if st.get("ended_early") else "")]
    return [{"type": "text", "text": line} for line in lines]


def music_workflow(args):
    """(model, graph, settings line) for one piece of music."""
    style = args.get("style")
    if not isinstance(style, str) or not style.strip():
        raise Failure("style is required: the genre, instruments, mood and voice, e.g. 'gentle acoustic folk, guitar and flute, warm female voice'")
    model = "ace-music-xl" if args.get("quality") == "best" else "ace-music"
    lacking = missing(model)
    if lacking:
        raise Failure(f"ACE-Step 1.5 is not installed in ComfyUI (missing {', '.join(lacking)})")
    spec = SPECS[model]
    lyrics = args.get("lyrics") if isinstance(args.get("lyrics"), str) and args.get("lyrics").strip() else "[instrumental]"
    seconds = number(args, "seconds", 60, 5, 600, float)
    language = args.get("language") or ("el" if re.search(r"[\u0370-\u03ff]", lyrics) else "en")
    if language not in MUSIC_LANGUAGES:
        raise Failure(f"language must be one of {', '.join(MUSIC_LANGUAGES)}")
    key = args.get("key") or "C major"
    if key not in MUSIC_KEYS:
        raise Failure("key must be like 'C major' or 'E minor'")
    bpm = number(args, "bpm", 100, 30, 220)
    seed = number(args, "seed", random.randrange(2 ** 32), 0, 2 ** 63 - 1)
    graph = {
        "104": {"class_type": "UNETLoader", "inputs": {"unet_name": spec["unet"], "weight_dtype": "default"}},
        "105": {"class_type": "DualCLIPLoader", "inputs": {"clip_name1": spec["clips"][0], "clip_name2": spec["clips"][1], "type": "ace", "device": "default"}},
        "106": {"class_type": "VAELoader", "inputs": {"vae_name": spec["vae"]}},
        "78": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["104", 0], "shift": 3}},
        "94": {"class_type": "TextEncodeAceStepAudio1.5", "inputs": {"clip": ["105", 0], "tags": style.strip(), "lyrics": lyrics.strip(), "seed": seed,
                                                                      "bpm": bpm, "duration": seconds, "timesignature": "4", "language": language,
                                                                      "keyscale": key, "generate_audio_codes": True, "cfg_scale": 2.0,
                                                                      "temperature": 0.85, "top_p": 0.9, "top_k": 0, "min_p": 0.0}},
        "47": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["94", 0]}},
        "98": {"class_type": "EmptyAceStep1.5LatentAudio", "inputs": {"seconds": seconds, "batch_size": 1}},
        "3": {"class_type": "KSampler", "inputs": {"model": ["78", 0], "positive": ["94", 0], "negative": ["47", 0], "latent_image": ["98", 0],
                                                    "seed": seed, "steps": spec["steps"], "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "18": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["3", 0], "vae": ["106", 0]}},
        "107": {"class_type": "SaveAudioMP3", "inputs": {"audio": ["18", 0], "filename_prefix": "audio/studio", "quality": "V0"}},
    }
    sung = "instrumental" if lyrics == "[instrumental]" else f"sung in {language}"
    return model, graph, f"{model} ({'XL, best quality' if model == 'ace-music-xl' else 'turbo'}), {seconds:g} s, {sung}, {bpm} bpm, {key}, seed {seed}"


def video_frames(path):
    """A few frames of a finished clip as pictures, when ffmpeg is installed."""
    content = []
    try:
        length = float(subprocess.run([FFMPEG.replace("ffmpeg", "ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of",
                                       "default=nw=1:nk=1", path], capture_output=True, text=True, timeout=30).stdout.strip() or 0)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return content
    for at in (0.15, 0.5, 0.9):
        try:
            png = subprocess.run([FFMPEG, "-v", "error", "-ss", f"{length * at:.2f}", "-i", path, "-frames:v", "1",
                                  "-vf", "scale=512:-2", "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True, timeout=60).stdout
        except (OSError, subprocess.TimeoutExpired):
            return content
        if png:
            content.append({"type": "image", "data": base64.b64encode(png).decode(), "mimeType": "image/png"})
    return content


def collect(job, wait_s):
    """The job's pictures as MCP content, or None while ComfyUI is still working on it."""
    deadline = time.time() + wait_s
    while True:
        try:
            history = comfy(f"/history/{urllib.parse.quote(job)}", timeout=max(1, min(30, deadline - time.time()))).get(job)
        except Failure:
            # While it streams a model from system RAM, ComfyUI can leave requests unanswered for half a minute or more.
            if time.time() >= deadline:
                return None
            continue
        if history:
            status = history.get("status") or {}
            if status.get("status_str") == "error":
                notes = [m[1].get("exception_message", "") for m in status.get("messages", []) if m[0] == "execution_error"]
                raise Failure("ComfyUI could not make it: " + ("; ".join(n for n in notes if n) or "unknown error"))
            images = [img for out in (history.get("outputs") or {}).values() for img in out.get("images", [])]
            audio = [a for out in (history.get("outputs") or {}).values() for a in out.get("audio", [])]
            if images or audio or status.get("completed"):
                content = []
                AUDIO_MADE.clear()
                VIDEO_MADE.clear()
                for item in audio:
                    path = os.path.join(OUTPUT_DIR, item.get("subfolder", ""), item["filename"])
                    AUDIO_MADE.append(path)
                    size = os.path.getsize(path) // 1024 if os.path.exists(path) else 0
                    content.append({"type": "text", "text": f"Audio saved as {path} ({size} KB)."})
                for img in images:
                    path = os.path.join(OUTPUT_DIR, img.get("subfolder", ""), img["filename"])
                    if img["filename"].lower().endswith((".mp4", ".webm", ".mov")):
                        VIDEO_MADE.append(path)
                        size = os.path.getsize(path) // 1024 if os.path.exists(path) else 0
                        content.append({"type": "text", "text": f"Video saved as {path} ({size} KB). Frames from it follow."})
                        content += video_frames(path)
                        continue
                    query = urllib.parse.urlencode({"filename": img["filename"], "subfolder": img.get("subfolder", ""), "type": img.get("type", "output")})
                    data = comfy("/view?" + query, raw=True, timeout=60)
                    path = os.path.join(OUTPUT_DIR, img.get("subfolder", ""), img["filename"])
                    content.append({"type": "image", "data": base64.b64encode(data).decode(), "mimeType": "image/png"})
                    content.append({"type": "text", "text": f"Saved as {path} ({len(data) // 1024} KB PNG)."})
                return content or [{"type": "text", "text": "ComfyUI finished without a picture."}]
        if time.time() >= deadline:
            return None
        time.sleep(0.5)


def free_memory(always=False):
    if not FREE_AFTER and not always:
        return
    try:
        if always or not comfy("/queue").get("queue_pending"):          # the next picture would only load it again
            comfy("/free", {"unload_models": True, "free_memory": True}, raw=True)
    except Failure:
        pass


def still_working(job):
    queue = comfy("/queue")
    ahead = [item for item in queue.get("queue_pending", [])]
    return [{"type": "text", "text": f"Still being made (job {job}; {len(queue.get('queue_running', []))} running, {len(ahead)} waiting). "
                                     f"Call get_image with this job id in a little while."}]


# ---- swap mode: the whole card for a big model, with Strata unloaded meanwhile

def write_swap(full):
    os.makedirs(os.path.dirname(SWAP_FILE), exist_ok=True)
    tmp = SWAP_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"full": full, "until": time.time() + SWAP_WAIT_S + 300 if full else 0, "since": time.time() if full else None}, fh)
    os.replace(tmp, SWAP_FILE)


def strata_state():
    status = manager("/api/strata/status")
    return bool(status.get("running") or status.get("server_processes")), int((status.get("lifecycle") or {}).get("active_requests") or 0)


BESIDE_MIB = int(os.environ.get("COMFYUI_BESIDE_MIB", "8192"))   # what Z-Image and SDXL need beside a loaded Strata


def no_room_beside():
    """True when Strata is loaded without a pool for tools (Studio's "Strata first" setting): then every picture swaps."""
    try:
        status = manager("/api/strata/status")
    except Failure:
        return False
    if not (status.get("running") or status.get("server_processes")):
        return False
    active = (status.get("subagent_pool") or {}).get("active") or {}
    return int(active.get("pool_mib") or 0) < BESIDE_MIB


def swap_begin():
    """Unloads Strata, unless another client is using it or Ollama models hold the card. Returns whether it was loaded."""
    running, busy = strata_state()
    deadline = time.time() + BUSY_WAIT_S
    while busy:
        if time.time() > deadline:
            raise Failure("Strata is answering another client's request, so it cannot be unloaded to make room for the pictures now. Try again in a while.")
        time.sleep(3)
        running, busy = strata_state()
    # The gateway lists the loaded Strata model here too (runtime "strata"); that one is unloaded below.
    holding = [m.get("name") or m.get("model") for m in manager("/api/ps").get("models", [])
               if int(m.get("size_vram") or 0) > 0 and m.get("runtime") != "strata" and (m.get("details") or {}).get("format") != "strata"]
    if holding:
        raise Failure(f"Ollama models hold graphics memory ({', '.join(holding)}), so the pictures cannot have the whole card; "
                      "unload them in the Strata gateway.")
    write_swap(True)
    if running:
        manager("/api/strata/stop", {}, timeout=120)
        deadline = time.time() + 120
        while strata_state()[0]:
            if time.time() > deadline:
                write_swap(False)
                raise Failure("Strata did not unload within two minutes, so the pictures did not start.")
            time.sleep(1)
    return running


def comfy_holds():
    """Bytes of graphics memory ComfyUI's PyTorch still holds, or None when it cannot tell."""
    try:
        devices = comfy("/system_stats").get("devices") or []
        return max(int(d.get("torch_vram_total") or 0) for d in devices) if devices else None
    except (Failure, ValueError, TypeError):
        return None


def release_card(wait_s=20):
    """Unloads ComfyUI's models and waits until the card is really free. A single /free was sometimes not acted on
    (2026-11-13: after an XL song ComfyUI kept 20 GB, and Strata could not start), so it is checked and sent again."""
    deadline = time.time() + wait_s
    while True:
        free_memory(always=True)
        for _ in range(8):
            held = comfy_holds()
            if held is None or held < 2 * 2 ** 30:
                return
            time.sleep(0.5)
        if time.time() >= deadline:
            print(f"comfyui: ComfyUI still holds {held / 2 ** 30:.1f} GB after asking it to free the card", file=sys.stderr, flush=True)
            return


def swap_end():
    release_card()
    write_swap(False)


def render(graphs, wait_s=None):
    """Queues the pictures, waits for them, and returns the content in order. Swap models get the card first."""
    swap = any(SPECS[model]["swap"] for model, _, _ in graphs) or no_room_beside()
    unloaded = swap_begin() if swap else False
    content, started, unfinished = [], time.time(), False
    try:
        jobs = [comfy("/prompt", {"prompt": graph, "client_id": CLIENT_ID})["prompt_id"] for _, graph, _ in graphs]
        deadline = started + (wait_s or (SWAP_WAIT_S if swap else WAIT_S))
        for index, (job, (_, _, settings)) in enumerate(zip(jobs, graphs)):
            begun = time.time()
            part = collect(job, max(1, deadline - time.time()))
            if part is None:
                if swap:            # Strata must not load again while ComfyUI still fills the card
                    comfy("/queue", {"clear": True}, raw=True)
                    comfy("/interrupt", {}, raw=True)
                    content.append({"type": "text", "text": f"Stopped: pictures {index + 1}-{len(jobs)} were not finished within "
                                                            f"{SWAP_WAIT_S // 60} minutes."})
                    break
                content += still_working(job)
                unfinished = True
                continue
            label = f"Picture {index + 1} of {len(jobs)}: " if len(jobs) > 1 else ""
            content += part + [{"type": "text", "text": f"{label}{settings}; made in {time.time() - begun:.0f} s (job {job})."}]
    finally:
        if swap:
            swap_end()
    if swap:
        note = (f"Strata was unloaded to make room on the graphics card and loads again with the next chat request (about a minute). "
                if unloaded else "") + f"The whole batch took {time.time() - started:.0f} s."
        content.append({"type": "text", "text": note})
    elif not unfinished:                # get_image unloads it once the last picture is fetched
        free_memory()
    return content


def project_target(target, ext):
    """A path to copy a result into: full, ending in ext, inside FILE_ROOTS; None when not given."""
    if target is None:
        return None
    real = os.path.realpath(os.path.dirname(os.path.expanduser(str(target))))
    if not os.path.isabs(os.path.expanduser(str(target))) or not str(target).lower().endswith(ext) \
            or not any(real == r or real.startswith(r + os.sep) for r in FILE_ROOTS):
        raise Failure(f"path must be a full path ending in {ext} inside {', '.join(FILE_ROOTS)}")
    return target


def call(name, args):
    if name == "generate_image":
        return render([workflow(args)])
    if name == "generate_video":
        target = project_target(args.get("path"), ".mp4")
        content = render([video_workflow(args)])
        if target and VIDEO_MADE and os.path.exists(VIDEO_MADE[-1]):
            os.makedirs(os.path.dirname(os.path.expanduser(target)), exist_ok=True)
            shutil.copyfile(VIDEO_MADE[-1], os.path.expanduser(target))
            content.insert(1, {"type": "text", "text": f"Copied into the project as {target}."})
        return content
    if name == "generate_music":
        target = project_target(args.get("path"), ".mp3")
        model, why = music_choice(args)
        if model == "heartmula":
            return heartmula_song(args, target, why)
        content = render([music_workflow(args)], wait_s=MUSIC_WAIT_S)
        if why != "the default":
            content.append({"type": "text", "text": f"ace-step (ACE-Step 1.5) made it: {why}."})
        if target and AUDIO_MADE and os.path.exists(AUDIO_MADE[-1]):
            os.makedirs(os.path.dirname(os.path.expanduser(target)), exist_ok=True)
            shutil.copyfile(AUDIO_MADE[-1], os.path.expanduser(target))
            content.append({"type": "text", "text": f"Copied into the project as {target}."})
        return content
    if name == "generate_images":
        pictures = args.get("pictures")
        if not isinstance(pictures, list) or not 1 <= len(pictures) <= MAX_BATCH or not all(isinstance(p, dict) for p in pictures):
            raise Failure(f"pictures must be a list of 1 to {MAX_BATCH} pictures, each with a prompt")
        shared = {k: args[k] for k in SHARED_PROPS if k in args}
        graphs = [workflow({**shared, **picture}) for picture in pictures]
        if len({model for model, _, _ in graphs}) > 1:
            first = graphs[0][0]
            graphs = [workflow({**shared, "model": first, **picture}) for picture in pictures]
        return render(graphs)
    if name == "get_image":
        job = str(args.get("job") or "").strip()
        if not job:
            raise Failure("job is required")
        content = collect(job, 30)
        if content is None:
            return still_working(job)
        free_memory()
        return content
    if name == "list_models":
        stats = comfy("/system_stats")
        devices = "; ".join(f"{d.get('name')}: {d.get('vram_free', 0) // 2 ** 20} of {d.get('vram_total', 0) // 2 ** 20} MiB free"
                            for d in stats.get("devices", []))
        cache, lines = {}, []
        for model in MODELS:
            lacking = missing(model, cache)
            note = " (unloads the chat model while it works)" if SPECS[model]["swap"] else ""
            lines.append(f"{model}: " + ("installed" if not lacking else f"not installed (missing {', '.join(lacking)})") + note)
        for music, label in (("ace-music", "fast"), ("ace-music-xl", "best, unloads the chat model while it works")):
            lacking = missing(music, cache)
            lines.append(f"{music} (generate_music, {label}): " + ("installed" if not lacking else f"not installed (missing {', '.join(lacking)})"))
        try:
            health = music_engine("/health", timeout=5)
            heart = "installed" if health.get("installed") else "the engine runs but the model files are missing"
        except Failure:
            heart = "not running (service strata-music)"
        lines.append(f"heartmula (generate_music model heartmula, HeartMuLa-oss-3B): {heart}")
        lines.append(f"The user's music default: {music_choice({})[0]}")
        lacking = missing("wan-video", cache)
        lines.append("wan-video (generate_video): " + ("installed" if not lacking else f"not installed (missing {', '.join(lacking)})")
                     + " (unloads the chat model while it works)")
        chosen = chosen_default()
        lines.append(f"The user's default: {'chosen by style (sdxl for painted and drawn styles)' if chosen == 'auto' else chosen}")
        lines.append("sdxl checkpoints: " + (", ".join(checkpoints()) or "none"))
        return [{"type": "text", "text": "\n".join(lines) + f"\nDevices: {devices or 'unknown'}"}]


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
            continue                      # notifications need no answer
        if method == "initialize":
            answer(msg_id, {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}},
                            "serverInfo": {"name": "comfyui-local", "version": "1.0"}})
        elif method == "ping":
            answer(msg_id, {})
        elif method == "tools/list":
            answer(msg_id, {"tools": TOOLS})
        elif method == "tools/call":
            params = msg.get("params") or {}
            if params.get("name") not in {tool["name"] for tool in TOOLS}:
                answer(msg_id, error={"code": -32602, "message": f"unknown tool {params.get('name')!r}"})
                continue
            PROGRESS["token"] = (params.get("_meta") or {}).get("progressToken")
            try:
                content, failed = call(params.get("name"), params.get("arguments") or {}), False
            except Failure as exc:
                content, failed = [{"type": "text", "text": f"Error: {exc}"}], True
            answer(msg_id, {"content": content, "isError": failed})
        else:
            answer(msg_id, error={"code": -32601, "message": f"unknown method {method!r}"})


if __name__ == "__main__":
    main()
