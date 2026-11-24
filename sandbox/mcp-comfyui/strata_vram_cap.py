"""Keeps ComfyUI inside a fixed share of the graphics card, beside the Strata engine (Eidoloom Studio, 2026-11-10).

ComfyUI sizes its work from the card's free memory at the moment, so with Strata unloaded it filled 21.5 GB, which a
Strata start during a picture would not survive. With STRATA_COMFY_VRAM_GB set, ComfyUI sees at most that many GB as
free, less what it already holds, and PyTorch refuses allocations a little above it as a backstop. No nodes are added.

Swap mode: while Studio's comfyui tool has unloaded Strata for a big model, it writes {"full": true, "until": time} to
STRATA_COMFY_SWAP_FILE and ComfyUI may use the whole card until then. The time limit undoes a swap left behind by a crash.
"""
import json
import logging
import os
import time

import torch

import comfy.model_management as mm

NODE_CLASS_MAPPINGS = {}
CAP = float(os.environ.get("STRATA_COMFY_VRAM_GB") or 0) * 2 ** 30
BACKSTOP = 1.5 * 2 ** 30
SWAP_FILE = os.environ.get("STRATA_COMFY_SWAP_FILE") or os.path.join(os.path.dirname(__file__), os.pardir, "user", "strata_swap.json")
STATE = {"mtime": None, "until": 0.0, "fraction": None}


def whole_card():
    """True while a swap gives ComfyUI the whole card."""
    try:
        mtime = os.stat(SWAP_FILE).st_mtime
    except OSError:
        return False
    if mtime != STATE["mtime"]:
        try:
            with open(SWAP_FILE, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            data = {}
        STATE["mtime"], STATE["until"] = mtime, float(data.get("until") or 0) if data.get("full") is True else 0.0
    return time.time() < STATE["until"]


def set_fraction(device, fraction):
    if STATE["fraction"] != fraction:
        torch.cuda.set_per_process_memory_fraction(fraction, device)
        STATE["fraction"] = fraction
        logging.info("Strata: ComfyUI may use %s of the graphics card", "all" if fraction >= 1 else f"{fraction * 100:.0f}%")


if CAP and torch.cuda.is_available():
    _free_memory = mm.get_free_memory
    DEVICE = mm.get_torch_device()
    TOTAL = torch.cuda.get_device_properties(DEVICE).total_memory

    def capped_free_memory(dev=None, torch_free_too=False):
        real = _free_memory(dev, torch_free_too)
        device = dev if dev is not None else DEVICE
        if getattr(device, "type", "") != "cuda":
            return real
        if whole_card():
            set_fraction(device, 1.0)
            return real
        set_fraction(device, min(1.0, (CAP + BACKSTOP) / TOTAL))
        held = torch.cuda.memory_stats(device).get("active_bytes.all.current", 0)
        room = max(0, CAP - held)
        if torch_free_too:
            return min(real[0], room), min(real[1], room)
        return min(real, room)

    mm.get_free_memory = capped_free_memory
    set_fraction(DEVICE, 1.0 if whole_card() else min(1.0, (CAP + BACKSTOP) / TOTAL))
    logging.info("Strata: ComfyUI keeps to %.1f GB of graphics memory (hard limit %.1f GB) unless %s allows the whole card",
                 CAP / 2 ** 30, (CAP + BACKSTOP) / 2 ** 30, SWAP_FILE)
