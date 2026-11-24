# Voices: VoxCPM2 for Studio

Studio's Voices view and the agent's voice tools (`list_voices`, `speak`) read text aloud in a shared cast of voices,
made from descriptions (never copied from a real person). The engine is VoxCPM2 (OpenBMB, Apache 2.0, business use
allowed), 30 languages including Greek, about 7 GB of graphics memory while it reads.

## What is where

- `~/.local/opt/voice/.venv`: its own environment. It borrows ComfyUI's PyTorch for AMD (ROCm 7.2) through
  `zz-comfyui-torch.pth`, so ComfyUI is not touched. VoxCPM 2.0.3 is installed without gradio, funasr, modelscope and
  torchcodec, which it does not need here.
- `~/.local/opt/voice/models/VoxCPM2`: the model (4.7 GB), from Hugging Face `openbmb/VoxCPM2`.
- `tools/voice_engine.py`: the engine on 127.0.0.1:8189 (`/health`, `/speak`, `/job`, `/cancel`, `/unload`). It loads the
  model only with 8 GB free on the card (otherwise the reading waits) and unloads it after 3 idle minutes.
- `tools/voice_mcp.py`: the MCP server for the agent.
- `data/voices/voices.json` and `data/voices/samples/`: the cast. A voice is its description plus the take someone kept,
  which every later reading copies, so it sounds the same each time.

## Install the engine as a service

```bash
cp sandbox/mcp-voice/strata-voice.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now strata-voice
```

Studio needs `"voice_url": "http://127.0.0.1:8189"` in `studio.config.json`. The voice MCP server is a Tool server:
command `python3`, argument `tools/voice_mcp.py` (full path), environment `VOICE_URL=http://127.0.0.1:8189`,
`VOICES_FILE=<Studio>/data/voices/voices.json`, `VOICE_ROOTS=~/Projects`, timeout 1800 s.

## Speed

About 3.3 seconds of work per second of speech on the RX 7900 XTX (2026-11-11), against 0.3 on an RTX 4090 in the
authors' numbers. `torch.compile` (VoxCPM's `optimize=True`) could help, but on this machine it fails to build
Triton's helper: it needs Python's headers, `sudo apt install python3.12-dev`. Not tried since.

## The Greek test (2026-11-11)

`~/.local/opt/voice/test/`: six voices from descriptions, two takes each, the same Greek passage; the starter cast uses
the first takes. The user judged them good.
