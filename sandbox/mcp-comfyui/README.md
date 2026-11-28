# ComfyUI for Eidoloom Studio

> `panel.json` is an example to paste into Account & settings → Tool servers: replace `/path/to/eidoloom-studio` with your Eidoloom Studio folder and `/home/you` with your home folder. Account & settings → Tools & setup adds this server for you, with the right paths.

Set up on 2026-11-10 so Studio's models can make pictures through MCP, on this machine only.

- **ComfyUI** 0.44.0 in `~/.local/opt/comfyui` (git clone of `comfyanonymous/ComfyUI`, its own `.venv`), PyTorch 2.14.1 for
  ROCm 7.2 from `https://download.pytorch.org/whl/rocm7.2` (the wheels carry their own ROCm runtime; the system's ROCm 6.2.4
  is not used). User service `comfyui` (`~/.config/systemd/user/comfyui.service`, enabled) on `127.0.0.1:8188` only. Open
  http://127.0.0.1:8188 on this machine to use it by hand. `systemctl --user stop comfyui` frees everything.
- **Z-Image Turbo** (Tongyi-MAI, Apache 2.0), added on 2026-11-10 and the default: Comfy-Org's packaging, the int8 model
  (`models/diffusion_models/z_image_turbo_int8_convrot.safetensors`, 5.8 GB), the fp8 text encoder
  (`text_encoders/qwen_3_4b_fp8_mixed.safetensors`) and `vae/ae.safetensors`, sha256 checked. The workflow is ComfyUI's own
  template: 8 steps, cfg 1, `res_multistep`, `simple`, AuraFlow shift 3, and a zeroed negative (negative prompts do nothing).
  Clean on ROCm, 12-18 s a picture. On the same prompts and seeds it beat SDXL on hands (four people's hands around a lamp,
  an older woman gluing a book spine), followed details SDXL dropped (one cat on a stack in the window, not on the pavement),
  wrote "BOOKS" on a sign where SDXL wrote gibberish, and kept "photograph" where SDXL painted a watercolour. SDXL's scenes
  were more dramatic and varied. The default is set in Studio's ComfyUI panel (Default model: Z-Image Turbo, SDXL, or Choose by style), saved in
  Studio's `data/comfyui.json` and read on every picture through `COMFYUI_SETTINGS`; an agent passes `model` only when the
  user asks for one.
- **Z-Image Base** (the full, non-turbo Z-Image, Apache 2.0): `diffusion_models/z_image_int8_convrot.safetensors` (5.8 GB),
  sharing Turbo's text encoder and VAE. 25 steps, cfg 4, real negative prompts. Beside Strata it took 71-86 s a picture (each
  step runs twice for the negative prompt), with clearly more natural light, skin and materials than Turbo.
- **Qwen-Image-2512** (Qwen, Apache 2.0, 20B), for swap mode: `diffusion_models/qwen_image_2512_fp8_e4m3fn.safetensors`
  (19 GiB), `text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors`, `vae/qwen_image_vae.safetensors`, from Comfy-Org/Qwen-Image.
  20 steps, cfg 4, euler, shift 3.1, 1328 px native. Comfy-Org/Qwen-Image-2.1 (newer, 7B, would fit beside Strata) is marked
  license "other" and was left out.
- **Swap mode** (`qwen-image`): the tool checks that Strata is not answering another client (the gateway's `active_requests`,
  waiting up to 90 s) and that no Ollama model holds the card, writes the swap file
  (`~/.local/opt/comfyui/user/strata_swap.json`, `COMFYUI_SWAP_FILE`) so the add-on lets ComfyUI use the whole card, stops
  Strata (`POST /api/strata/stop`), renders the whole batch, frees ComfyUI and closes the swap file. Strata loads again by
  itself with the chat's next request (the gateway's auto start), which costs the load and a fresh read of the conversation:
  two or three minutes a batch, so the tool asks models to put every picture into one `generate_images` call (at most 8).
  The server's `timeout_s` in Studio is 1800 and `COMFYUI_SWAP_WAIT_S` 1500; past that, the queue is cleared and the swap
  ends, so Strata never loads onto a full card. The swap file ends by itself 5 minutes after that limit, in case of a crash.
  Known risk: a request from another client (the Bridge PCs) arriving during a swap makes the gateway start Strata on a full
  card. The gateway's `/api/ps` lists the loaded Strata model too (`runtime: strata`); the Ollama check skips it.
  First real swap (2026-11-10 15:35): Strata unloaded, two pictures in 237 s (114 s at 1472x1104, 124 s at 1328x1328,
  including the first load of the 20 GB model from disk), card peak 23.4 GB, the decoder falling back to tiles both times
  (ComfyUI does that by itself), then 572 MB and 0.8 GB of RAM once the swap closed. Strata loaded again on the next
  request in 34 s. The bookshop was the best of all models (ornamented hand-painted "BOOKS" sign, a cat asleep on a stack,
  wet street); the hands picture showed two people's four hands instead of four people, where Z-Image Turbo got the count.
- **Model:** SDXL base 1.0 (`models/checkpoints/sd_xl_base_1.0.safetensors`, 6.9 GB, Stability AI, CreativeML Open RAIL++-M,
  sha256 checked against Hugging Face). More checkpoints go in the same folder; `list_models` shows them.
  Tested: clean pictures (no black images, which newer ROCm builds give on this card), 1024x1024 in 9-16 s.
- **Graphics memory:** ComfyUI runs on a 24 GB AMD card (`HIP_VISIBLE_DEVICES=0`; ROCm does not support the RX 550). Strata
  normally fills that card, so the gateway's Subagent pool is set to a fixed 9 GB (gateway data file, `strata_subagent_pool`),
  and Strata gets about 15 GB, as before 2026-11-06. `--reserve-vram 15` keeps ComfyUI within about 9 GB even when Strata is
  not loaded. Set the pool back to Off in the gateway to give Strata the whole card again. ComfyUI sizes its work from the
  card's free memory at that moment, so `--reserve-vram` cannot cap it: `--reserve-vram 15` left it nothing while Strata was
  loaded (every model streamed from RAM, 24-66 s a picture), and 1 GB let Z-Image take 21.5 GB while Strata was not. The
  add-on `custom_nodes/strata_vram_cap.py` (not part of ComfyUI) caps what it sees as free at `STRATA_COMFY_VRAM_GB` (8.5) less
  what it holds, with a PyTorch limit at 10 GB as a backstop; measured peak 9.2 GB, no out-of-memory errors.
- **System memory:** after a picture ComfyUI kept about 10.5 GB of RAM, whatever its options (`/free`, `--cache-none`,
  `--fast-disk`, `--disable-dynamic-vram`), because glibc held on to the freed weights. The Strata engine needs most of the
  62 GB and has been killed for lack of RAM before, so the service sets `MALLOC_MMAP_THRESHOLD_` and `MALLOC_TRIM_THRESHOLD_`
  and the MCP server calls `/free` after each picture (`COMFYUI_FREE_AFTER`, default on): ComfyUI drops to about 1.5 GB, and
  each picture reloads the model, about 5 s more.
- **MCP server:** Studio's own `tools/comfyui_mcp.py` (standard library only, talks only to `COMFYUI_URL`), in Studio's Tool
  servers as `comfyui` (asks unless auto-run is on; entry in `panel.json`). Tools: `generate_image` (prompt, negative prompt,
  size, steps, cfg, seed, sampler, checkpoint; SDXL text to image), `get_image` (a picture that took longer than the 110 s
  the tool waits) and `list_models`. Pictures come back as MCP images: Studio shows them in the chat and passes them to
  models with vision; the reply also names the PNG in `~/.local/opt/comfyui/output/`. A model can copy it into a project
  folder on the host. With the Sandbox on it uses Studio's `save_picture` tool instead, with the picture id the result
  names, since a sandboxed folder cannot see ComfyUI's output folder.
- **Dashboard:** Studio's status bar has a Pictures pill for admins (`comfyui_url` in `studio.config.json`), opening a panel
  with the picture being made, its step, the queue, memory and the latest pictures. The MCP server queues as client
  `strata-studio` (`COMFYUI_CLIENT_ID`) so Studio receives the step progress; pictures started in ComfyUI's own page show
  without steps.
- Remove it all: remove the server in Studio's Tool servers panel, `systemctl --user disable --now comfyui`, delete
  `~/.config/systemd/user/comfyui.service` and `~/.local/opt/comfyui`, and set the gateway's Subagent pool back to Off.

## First real run (2026-11-10)
A Strata model in a private Studio (`sandbox/runs/comfyui-20261110/`, `try.py`, `log.txt`): Strata loaded with the 9 GB pool in
35 s (16 GB of the card in use), then one `generate_image` call (1344x768, 28 steps, its own detailed prompt) made a good
header picture in 24 s beside the loaded engine. A Strata model looked at it, copied it into `assets/header.png` with `cp` and
finished, 3 turns in 79 s. It finished with a one-line summary instead of the two sentences asked for. RAM with both loaded:
the engine 43.6 GB, ComfyUI 1.6 GB, 8 GB still available, which is why the RAM settings above matter.
