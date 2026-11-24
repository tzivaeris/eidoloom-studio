# Music and sound effects

## Music: ACE-Step 1.5 in ComfyUI (`generate_music` in `tools/comfyui_mcp.py`)

ACE-Step 1.5 (StepFun / ACE-Step, MIT on its model card; the weights repo has no LICENSE file, keep a copy of the card)
runs natively in ComfyUI 0.44. Files from `Comfy-Org/ace_step_1.5_ComfyUI_files`, in `~/.local/opt/comfyui/models/`:

| File | Folder | Size |
|---|---|---|
| acestep_v1.5_turbo.safetensors (2B, `quality: fast`) | diffusion_models | 4.8 GB |
| acestep_v1.5_xl_turbo_bf16.safetensors (4B, `quality: best`, swap) | diffusion_models | 10.0 GB |
| qwen_0.6b_ace15.safetensors, qwen_1.7b_ace15.safetensors (turbo), qwen_4b_ace15.safetensors (XL) | text_encoders | 1.2 / 3.7 / 8.4 GB |
| ace_1.5_vae.safetensors | vae | 0.3 GB |

The workflow is ComfyUI's `audio_ace_step_1_5_split` / `audio_ace_step1_5_xl_turbo` template: 8 euler steps, shift 3,
`SaveAudioMP3`. Lyrics in 50 languages (Greek is `el`, picked automatically for Greek letters); no lyrics means
`[instrumental]`. Progress comes from ComfyUI's sampler steps through Studio's ComfyWatch.

## Music: HeartMuLa-oss-3B (`tools/music_engine.py`, service `strata-music`)

The second music model, chosen in Studio's ComfyUI panel (**Music**: ACE-Step 1.5 or HeartMuLa 3B, saved as
`music_model` in `data/comfyui.json`) or by `generate_music`'s `model`. HeartMuLa team, Apache 2.0 for code and weights
(since 2026-01-20); `HeartMuLa-oss-3B-happy-new-year` with `HeartCodec-oss-20260123`, as their README recommends. It is a
language model over 12.5 Hz codec frames: tags plus lyrics in, up to 240 s, 48 kHz stereo out; the song may end before
the length asked for. Tags work best as `piano,happy,female vocal` (the tool joins comma-separated parts without spaces).
No bpm, key or XL mode. The official code is in `~/.local/opt/heartlib` (github.com/HeartMuLa/heartlib); its pins
(torch < 2.11, transformers 4.57, numpy 2.0.2) would break ComfyUI, so it has its own venv borrowing ComfyUI's ROCm
PyTorch through `zz-comfyui-torch.pth`, like the sound engine. Set up once (the user runs the pip step):

```bash
cd ~/.local/opt/music
# .venv made with ComfyUI's python, zz-comfyui-torch.pth already in it
.venv/bin/pip install "transformers==4.57.1" "tokenizers==0.22.1" "huggingface_hub<1.0" vector-quantize-pytorch==1.27.15 einx accelerate soundfile sentencepiece tiktoken "blobfile>=2" omegaconf psutil datasets
.venv/bin/pip install --no-deps torchtune==0.4.0 torchao==0.9.0
.venv/bin/pip install --no-deps -e ~/.local/opt/heartlib
H=~/.local/opt/comfyui/.venv/bin/hf; export HF_HUB_DISABLE_XET=1       # 22.4 GB in all
$H download --local-dir ./ckpt HeartMuLa/HeartMuLaGen
$H download --local-dir ./ckpt/HeartMuLa-oss-3B HeartMuLa/HeartMuLa-oss-3B-happy-new-year
$H download --local-dir ./ckpt/HeartCodec-oss HeartMuLa/HeartCodec-oss-20260123
cp ~/Projects/Tools/StrataStudio/sandbox/mcp-sound/strata-music.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now strata-music
```

The engine runs heartlib's frame loop itself (for progress and Stop), lazy-loads HeartMuLa (bf16) and then HeartCodec
(fp32) so only one is on the card at a time, writes the WAV with soundfile (torchaudio's save needs torchcodec, which has
no ROCm build here) and the MP3 with ffmpeg. Like the sound engine it unloads an idle Strata when the card is short,
and ends its process when it unloads. `MUSIC_FAKE=1` (a tone) drives the tests.

First real runs (2026-11-16, RX 7900 XTX, Strata unloaded): 45 s of song in 45-48 s (writing about 0.7 s per second of
song, decoding the rest), peak up to 15.6 GB reserved, so `MUSIC_NEED_GB=16`. **No `expandable_segments`** for this
engine: with `PYTORCH_HIP_ALLOC_CONF=expandable_segments:True` (copied from the sound engine's service) heartlib's own
pipeline, same prompt and seed, wrote hiss and then noise instead of music, and without it music; the first samples the
user heard were that noise. Even without it the songs were still garbled, heartlib's own example too: the real cause
was the decoder (the 300M model that writes codebooks 1-7 of each frame, 384-wide attention heads), for which ROCm's
efficient-attention SDPA kernel returns NaN on this card (checked against the CPU in fp32: backbone and codebook 0 match,
the decoder's logits are NaN; the math kernel matches, flash has no kernel). The engine runs the decoder under
`sdpa_kernel([SDPBackend.MATH])` (`math_attention`); 40 s of song now takes 45-53 s. The first run failed in the codec:
heartlib's `_unload` drops only its own reference, and the engine's own ones kept 12.7 GB on the card; it now drops them
and the KV caches. ACE-Step turbo makes the same 45 s in 12-17 s. Seconds after the first HeartMuLa songs, a 3-day-old
ComfyUI process failed an ACE-Step job with `hipErrorIllegalAddress` and needed a restart (`systemctl --user restart
comfyui`); after that, HeartMuLa then ACE-Step, and both at once, ran without it, so the cause is not known. Samples:
`samples/heartmula_test_*.mp3` and `samples/acestep_test_*.mp3`.

## Sound effects: MOSS-SoundEffect v2.0 (`tools/sound_engine.py`, `tools/sound_mcp.py`)

OpenMOSS-Team/MOSS-SoundEffect-v2.0, Apache 2.0, 1.3B DiT + DAC VAE + Qwen3 text encoder, 48 kHz, up to 30 s, English
and Chinese prompts. Its package pins CUDA PyTorch; here it runs on ComfyUI's ROCm PyTorch through a `.pth` file, with
`TORCHDYNAMO_DISABLE=1` (its `torch.compile` + CUDA graphs are NVIDIA only). Set up once (the user runs it):

```bash
cd ~/.local/opt/sound
# .venv made with ComfyUI's python and zz-comfyui-torch.pth already, source in src/ (github.com/OpenMOSS/MOSS-TTS)
.venv/bin/pip install "transformers==4.57.1" "diffusers==0.37.1" ftfy soundfile accelerate huggingface_hub
.venv/bin/pip install --no-deps descript-audiotools==0.7.2
.venv/bin/pip install --no-deps -e src/moss_soundeffect_v2
# audiotools' own needs, without the ones that would replace ComfyUI's torch (julius, torch-stoi go in with --no-deps)
.venv/bin/pip install argbind pyloudnorm importlib-resources ffmpy ipython rich matplotlib librosa pystoi flatten-dict markdown2 randomname tensorboard
.venv/bin/pip install --no-deps julius torch-stoi
HF_HUB_DISABLE_XET=1 .venv/bin/hf download OpenMOSS-Team/MOSS-SoundEffect-v2.0 --local-dir models/MOSS-SoundEffect-v2.0   # 11 GB
cp ~/Projects/Tools/StrataStudio/sandbox/mcp-sound/strata-sound.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now strata-sound
```

pip then warns that MOSS pins older numpy, pillow, safetensors and others: that is expected, MOSS runs with ComfyUI's
newer ones. The Xet downloader failed once here ("error decoding response body"), hence `HF_HUB_DISABLE_XET=1`.

Notes from the first real runs (2026-11-12, RX 7900 XTX): MOSS's `save_audio` needs torchcodec, which has no ROCm build
here, so the engine writes the WAV with soundfile and raises the peak to -1 dB (MOSS's sounds peak around -6 to -9 dB).
Without `MIOPEN_FIND_MODE=FAST` (in the service file) a new process spent ~100 s finding kernels before its first sound;
with it, about 23 s for the first and 17-27 s for 5 s of sound at 100 steps (50 steps: 17 s). With everything on the card
it reserved 19.6 GB; the engine now turns on DiffSynth's per-stage offloading (`SOUND_OFFLOAD`), which brings the peak to
about 10.6-11.3 GB (expandable segments on), so it asks for 11 GB free (`SOUND_NEED_GB`). That does not fit the 9 GB the
Graphics card setting's Tools ready leaves, so a batch of sounds unloads an idle Strata either way, and an out-of-memory
error makes room and retries once (Keystone's first click failed that way, with 6 GB as the old need).

When it unloads (3 idle minutes, or 5 s after it made room), the engine ends its process and systemd starts an empty
one: offloaded parts sat in RAM (11.6 GB) and PyTorch's card context (1.1 GB) stayed after the model was dropped, and Strata
then failed to start with "Process exited with code 1" (2026-11-12).

The engine's fake mode (`SOUND_FAKE=1`, a tone) drives the tests.
