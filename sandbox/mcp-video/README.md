# Video editing and generation for Eidoloom Studio

> `panel.json` is an example to paste into Account & settings → Tool servers: replace `/path/to/eidoloom-studio` with your Eidoloom Studio folder and `/home/you` with your home folder. Account & settings → Tools & setup adds this server for you, with the right paths.

Set up on 2026-11-10 so Studio's models can edit short videos and make clips, on this machine only.

- **Programs:** Ubuntu's `ffmpeg` 6.1.1, `melt` 7.22 and Kdenlive 23.08 (`sudo apt install libpcre2-16-0=10.42-4ubuntu2 melt
  ffmpeg kdenlive`: the Greek mirror listed a `libpcre2-16-0` build that no mirror had any more, so the release build was
  pinned; a later `sudo apt upgrade` moves it on). Kdenlive is for editing by hand; the agent's renders use ffmpeg.
- **MCP server:** Studio's own `tools/video_mcp.py` (standard library only), in Studio's Tool servers as `video` (asks unless
  auto-run is on, `timeout_s` 1800, entry in `panel.json`). Files are read and written only inside `VIDEO_ROOTS`
  (~/Projects, ComfyUI's output folder, ~/Videos); Studio gives the model the project's full path when MCP tools are present.
  Tools: `render_video` (a whole edit as one timeline: clips from videos, pictures and colour cards with in and out points,
  13 transitions, fades, greyscale, sepia, blur, brightness, contrast, saturation, speed, Ken Burns zoom, mirror, titles that
  fade in and out (DejaVu Sans Bold, Greek works), music or voice with volume and fades mixed with the clips' sound; `preview`
  for a quick 640 px render; three still frames come back to look at), `probe_media` and `video_frames`. Each render saves
  `<name>.timeline.json` beside the MP4 with a `_layout` section (where every clip, title and sound fell), which the agent
  reads back to change one thing, and which Studio opens as a timeline.
- **Studio:** a Video view in the activity bar lists the project's timelines and makes new ones. A `.timeline.json` opens as
  an editor: the rendered video with an inspector beside it, the Video, Titles and Sound tracks to scale (drag to move or
  reorder, the right edge to trim), a media bin, Preview and Render with live progress, and Ask the agent, which fills the
  chat box with a reference to the selected part. The agent's renders and edits show up within 2 s. `.mp4`, `.webm` and
  `.mov` files open as a player, served in byte ranges so they can be seeked. Code shows the JSON.
- **First real edit** (2026-11-10): the four ComfyUI pictures of the reading-club site as a 16 s slideshow at 1280x720 with
  slow zooms, two dissolves and a slide, three Greek titles and a music bed, rendered in 5 s
  (`~/Videos/studio-demo/reading-club.mp4`).
- **Video generation:** `generate_video` in the `comfyui` server, with Wan 2.2 TI2V-5B (Wan-AI, Apache 2.0; from Comfy-Org/Wan-2.2:
  `wan2.2_ti2v_5B_fp16`, `umt5_xxl_fp8_e4m3fn_scaled`, `wan2.2_vae`, sha256 checked), as in ComfyUI's
  `video_wan2_2_5B_ti2v` template (20 steps, cfg 5, uni_pc, shift 8, 24 fps, 4n+1 frames), from text or from a start picture,
  1-5 s, 1280x704 or portrait or smaller. It always uses swap mode (see `../mcp-comfyui/README.md`).
  First real clips (2026-11-10, picture to video from the reading-club header, 1280x704): 5 s (121 frames) sampled in about
  11 minutes and then ran out of memory in the decoder, which asked for 18.6 GB at once; with `VAEDecodeTiled` (256 px tiles,
  32 frames at a time) 3 s (73 frames) took 383 s in all, peak 21.6 GB, a smooth push-in that kept the room. Graphics memory
  went back to 624 MB a few seconds after the swap closed. Expect about 2 minutes a second of clip at 1280x704; the
  smaller sizes are much quicker.
- Remove it all: remove the `video` server in the Tool servers panel, delete `tools/video_mcp.py`, and
  `sudo apt remove melt ffmpeg kdenlive` if nothing else needs them.
