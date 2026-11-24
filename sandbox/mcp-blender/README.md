# Blender and its MCP server for Eidoloom Studio

> `panel.json` is an example to paste into Account & settings → Tool servers: replace `/path/to/eidoloom-studio` with your Eidoloom Studio folder and `/home/you` with your home folder. Account & settings → Tools & setup adds this server for you, with the right paths.

Set up on 2026-11-09.

- **Blender 5.2.3 LTS** in `~/.local/opt/blender-5.2.3-linux-x64` (checksum matched blender.org), command `~/.local/bin/blender`,
  menu entry `~/.local/share/applications/blender.desktop`. Remove: delete those three and `~/.config/blender/5.2`.
- **Graphics:** the RX 550 (4 GB) drives the screen and the RX 7900 XTX is left to Strata. Blender's preferences are set to the
  OpenGL backend, so its window draws on the RX 550, and Cycles to CPU (the RX 550 is too old for HIP, the 7900 XTX is full).
- **MCP for Blender 2.0.0** (`mcp-for-blender`, formerly `blender-mcp`) in its own environment `~/.local/opt/mcp-for-blender`
  (pip, since uv is not installed). Its add-on is `~/.config/blender/5.2/scripts/addons/blender_mcp.py`, enabled in the saved
  preferences; it starts its socket server by itself when Blender opens, on localhost:9876 only.
- `panel.json` for Studio's Tool servers panel sets `BLENDER_MCP_SAFE_MODE=1` (scripts that touch files, programs or the network
  are refused and sent back to the model) and `DISABLE_TELEMETRY=true` (the default collects anonymous usage).
- `execute_blender_code` runs Python inside Blender, on the host, outside Studio's sandbox. Keep the server on "Ask before every
  call". 31 tools; the Sketchfab, Hyper3D, Hunyuan3D and Poly Pizza ones need accounts and stay unused; Poly Haven needs none.
- Studio describes pictures from MCP tools as text, so a model cannot see `get_viewport_screenshot` yet; it checks its work with
  `get_scene_info` and `get_object_info`.
