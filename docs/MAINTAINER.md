# Working on Eidoloom Studio

[SPEC.md](../SPEC.md) is the contract between the page and the server: every route, event and agent rule. The code
explains itself feature by feature in its comments; start from the route or the function a feature's name points to.

## Layout

| Path | What it is |
| --- | --- |
| `studio.py` | The whole server: Python standard library only (`ThreadingHTTPServer`). Accounts, files, terminals, the agent loop, tool servers, sandbox. |
| `web/` | The page: `index.html`, `app.js`, `app.css`, the in-app `guide.html`, and `assets/`. Monaco, xterm, pdf.js, three.js and KaTeX load from cdn.jsdelivr.net at pinned versions. |
| `installer.py`, `updates.py` | Account & settings → Tools & setup, and Updates (off unless `"updates": true`). |
| `tools/` | Studio's own MCP servers and engines (pictures, voices, sound, music, video, audio, Greek checks, browser), `view_page.mjs`, and `helper/`, the Go program that connects a person's own computer. |
| `sandbox/` | The sandbox images and firewall, each optional tool's README and example `panel.json`, the agent test arena and the model test prompts. |
| `mock_gateway.py` | A scripted model server for the tests. |
| `tests/` | `test_backend.py` (end to end against the mock), `browser/run.mjs` (the page in real Chromium), and fakes for Codex, MCP and listmonk. |

## Finding your way around the two big files

`studio.py` (about 14,800 lines) and `web/app.js` (about 9,200) are each split into sections by banners. Search for
`# ----` in `studio.py` or `/* ----` in `app.js`, or use your editor's outline. In `studio.py`, each feature's routes
sit in its own section beside the code they call. Every route is written `@route("GET", "/api/...")`, so searching for
a path from the page or from [SPEC.md](../SPEC.md) leads straight to its handler. In `app.js`, the matching page code
is usually named after the feature or carries a short prefix (`ae` for the audio editor, `tl` for timelines, `room` for
the waiting room, `cx` for Codex usage).

`studio.py`, top to bottom:

| Sections | What is there | Start from |
| --- | --- | --- |
| Small helpers, Configuration, Authentication | Config defaults (`DEFAULT_CONFIG`), passwords, sessions, sign-in limits | `Auth`, `load_config` |
| Waiting room, Application state | The one-user agent seat and the line; everything the server holds in memory | `WaitingRoom`, `App` |
| Model gateway, Model state, Connecting a model server | Which models exist, the loading bar, Strata's own server, the dashboard's figures | `fetch_models`, `strata_ensure`, `api_model_status` |
| HTTP plumbing, Keeping windows in step | Routing, server-sent events, static files, sync between a person's windows | `route`, `Handler`, `sync_publish` |
| Previews, Live servers | Pages, pictures and PDFs from the project; a folder's own web servers relayed into Studio | `preview_file`, `live_fetch` |
| Auth endpoints; Config, models, workspaces | Sign-in, accounts, users, opening folders, the sandbox switch | `api_workspace_open` |
| ComfyUI dashboard, Video timelines, Media view, Audio editor | The media features of the page | `comfyui_status`, `video_render`, `media_list`, `audio_edit_route` |
| Files API, Terminals | The Explorer, uploads, downloads, Office pages, search; pty terminals and the sandbox containers | `files_write`, `ensure_sandbox`, `Terminal` |
| Agent: sessions, Shared projects, Attachments, undo | Chats on disk, folder sharing and rights, attached files, Word and Excel, undo points | `load_session`, `share_need`, `check_attachments` |
| Agent: run object, tools | One run's events and approvals; every tool the model can call | `Run`, `execute_tool`, `tool_write_file` |
| MCP servers, the agent's web browser | Tool servers from other programs; the Browser tab | `McpClient`, `McpManager`, `BrowserView` |
| Agent: prompt, model calls, loop | The system prompt, streaming from the model server, the agent loop | `build_system_prompt`, `call_model`, `agent_loop` |
| Session cache, Reply checks | Saving a chat's engine state; mixed scripts, cut-off replies, the turn guard | `cache_restore`, `script_problem`, `turn_guard` |
| /compact and goals, Scheduled tasks, Time-skip, After a run | The longer-running agent features, and how a run starts and ends | `goal_next`, `TaskBook`, `timeskip_main`, `agent_main` |
| Codex | Codex as the chat's agent, its sign-in, usage and the bridge to Studio's tools | `codex_loop`, `codex_turn`, `codex_answer` |
| Agent endpoints, Passing a chat, Waiting room endpoints | Send, stream, stop and decide; handing a chat to someone else; the line's routes | `agent_send`, `handoff_accept`, `queue_state` |
| Chat box commands, Voices, a person's own computer | `@file`, `!command`; the voice cast; the studio-helper sync and its commands | `voices_speak`, `HelperHub`, `helper_sync` |
| Chat report, Git, Backups | The report of a chat, source control, the daily backup of `data/` | `git_status`, `make_backup` |
| Graphics card, Greek check, Updates and Tools & setup | The card's split between Strata and the tools, cleanup after runs, the Greek glossary, the admin panes | `card_apply`, `cleanup_main`, `greek_check` |
| Command line | `--backup`, `--restore`, `--set-password` and `main` | `main` |

`web/app.js`, top to bottom: helpers, icons, menus and themes; the API client (`api`) and the global state (`S` for
the editor and files, `A` for the agent panel); sign-in and Account & settings (`openAccount`, one `pane*` function per
section); the top bar and the folder (`setWorkspace`, `openFolderDialog`); the Explorer; the sidebar views (Skills,
Git, Search); the editor and its tabs (`openFile`, `activateTab`); the audio editor and the other editors (Markdown,
video, 3D models, tables, timelines); the Timeline, Media, Voices and Tasks views; tabs for previews, live servers, the
agent's browser and pictures; the terminal; the agent panel (`agentSend`, `handleEvent`, which draws every event of a
run, and `approvalCard`); the command palette and shortcuts; start-up and settings from other windows (`prefApply`); the model and ComfyUI
dashboards; the waiting room
(`roomRender`); the phone and tablet layout (`applyLayout`, `showSection`); and `start`, which runs it all.

When a feature grows large and stands on its own, it can move to its own file, as `installer.py` and `updates.py` did.
Run both test suites after such a move: the tests import `studio` and replace some of its globals, so a function that
moved to another module may no longer see the replacement.

## Running the tests

```bash
python3 tests/test_backend.py
```

```bash
cd tests/browser && npm install && npx playwright install chromium && node run.mjs
```

Both start private copies of Studio on free ports with the mock, so they never touch a running Studio or its `data/`.
`ONLY=test_model_servers python3 tests/test_backend.py` runs one part after the shared setup. The browser tests need
internet for the CDNs. The tests for a person's own computer need the helper built first
(`tools/helper/build.sh`, which uses the sandbox image), and are skipped without it.

`mock_gateway.py` plays the Manager by default. `MOCK_OPENAI_ONLY=1` makes it a server with only the OpenAI API, like
llama.cpp, vLLM or LM Studio, and `MOCK_API_KEY` then asks for that key. Its other switches are listed at its top.

## Rules of the house

- Commit each finished feature with both test suites passing.
- Update `web/guide.html` (English and Greek) with every feature people see; the browser tests check that every command
  it names exists.
- Colours are CSS tokens per theme (Dark, Light, Sepia); add new ones as tokens.
- Keep the system prompt the same through a run, or the model server reads the whole chat again from the start.
- Keep both sandbox modes, and both the streaming and the long-polling paths, working.

## Model servers

Studio talks to one `gateway`. Every server takes `/v1/chat/completions`; the model list comes from Ollama's `/api/tags`
or, failing that, `/v1/models`. The Strata features (loading progress, the graphics card choice, the session cache, the
model dashboard's gauges) work with the Ollama Manager's `/api/strata/*` and `/api/metrics` routes, or with Strata's
own server (`/v1/status`), which Studio can also start itself (`strata_dir`). With other servers they stay quiet.

## Names that stay as they were

The project was called Strata Studio until 2026-11-20. These internal names were kept so that existing installs and the
Ollama Manager keep working: the `studio_session_*` cookies, the `X-Studio` headers,
`.strata-studio/` in projects, the `strata-sandbox` images and network, and the gateway header
`X-Application-Name: Strata Studio`, which the Manager's exemptions match. The service was renamed `eidoloom-studio` in
0.1.1; `install-service.sh` removes an old `strata-studio` service when it installs the new one.

## This machine's settings

`studio.config.json` is not in Git; a new install copies `studio.config.example.json`. Before merging a branch that
stopped tracking it into a checkout that still has it, copy the file aside: Git deletes a file whose tracking a merge
removes. Put it back afterwards.
