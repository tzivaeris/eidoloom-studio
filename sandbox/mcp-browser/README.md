# The agent's web browser

`tools/browser_mcp.py` is the `browser` tool server (admins only, set up 2026-11-13). It starts a headless Chromium
(the newest Playwright build in `~/.cache/ms-playwright`) and runs Microsoft's Playwright MCP (`npx -y @playwright/mcp`,
0.0.85 at first) against it with `--cdp-endpoint`. The model gets 25 tools: `browser_navigate`, `browser_snapshot`
(the page as an outline with element refs), `browser_click`, `browser_type`, `browser_fill_form`, `browser_take_screenshot`,
tabs, uploads and more. Navigation results link the outline to a file the model cannot read, so the server's description
tells it to call `browser_snapshot`.

Safety:
- A fresh profile in a temporary folder each time the server starts, deleted when it stops: no saved sign-ins.
- Every request goes through a proxy inside `browser_mcp.py` that refuses any address that is not public internet
  (127.0.0.1, studio.local, the LAN, link-local, private and reserved ranges), checked on the address it connects to;
  `--proxy-bypass-list=<-loopback>` keeps localhost on the proxy and WebRTC may not go around it.
- Calls ask like commands (approval `with_commands`).

Chromium's own sandbox: Ubuntu 24.04's AppArmor blocks the unprivileged user namespaces it needs, so Chromium stops
with "No usable sandbox" and `browser_mcp.py` starts it with `--no-sandbox` (as Playwright does by default; the
server log says so). To give it its sandbox back, an admin can allow user namespaces for Playwright's Chromium:

```
sudo tee /etc/apparmor.d/studio-chromium >/dev/null <<'PROFILE'
abi <abi/4.0>,
include <tunables/global>
profile studio-chromium /home/you/.cache/ms-playwright/chromium-*/chrome-linux64/chrome flags=(unconfined) {
  userns,
  include if exists <local/studio-chromium>
}
PROFILE
sudo apparmor_parser -r /etc/apparmor.d/studio-chromium
```

then restart the browser server under Tool servers; its log no longer says "without its sandbox".

Watching and taking over: Studio's Browser tab (`showBrowser` in `web/app.js`; the Explorer's globe menu, the
"Watch it" card, and it opens by itself the first time a run opens a page) shows the same Chromium live:
`tools/browser_view.mjs` joins it over DevTools (`session.json` in `data/browser/`), streams JPEG frames
(`Page.startScreencast`, at most about 7 a second) and passes input on. `GET /api/browser/frame` (long polling, so it
works through tunnels), `POST /api/browser/input`, `POST /api/browser/hold`. Take over gives the user the mouse and
keyboard (typing through a hidden text box, so Greek accents arrive whole); the agent's browser calls wait
(`wait_for_browser`) until Hand back, or until a minute after nobody watches.
