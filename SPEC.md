# Eidoloom Studio — specification (v1)

An AI agent harness with a code editor around it, served from your own machine, that edits real project folders
with an agent powered by the model server you choose (Ollama, llama.cpp, vLLM, LM Studio, Strata, or anything with
the OpenAI chat API and tool calls).
Layout: VS Code-like — file tree left, tabbed Monaco editor centre, agent chat right, terminal panel bottom.

## Hard constraints

- **Backend: Python 3.10+ standard library only** (no pip installs). One process: `python3 studio.py`.
  Nothing may require `pip`/`npm` on the server.
- **Frontend: static files** (`web/index.html`, `web/app.js`, `web/app.css`), no build step.
  Monaco, xterm.js, pdf.js, KaTeX and three.js load from `https://cdn.jsdelivr.net/npm/...` at exact versions pinned
  in `web/index.html` and `web/app.js` (search for `@` and a version), which Account & settings → Updates moves
  forward. On 2026-11-25: `monaco-editor@0.57.0` (AMD loader `min/vs/loader.js`), `@xterm/xterm@6.0.0`,
  `@xterm/addon-fit@0.11.0`, `pdfjs-dist@4.10.38`, `katex@0.19.1`, `three@0.187.0`.
- Default bind `127.0.0.1:8790` (this machine only) **with mandatory login**; `"host": "0.0.0.0"` opens it to the
  network. Config file decides.
- Model gateway = the `gateway` setting (Ollama at `http://127.0.0.1:11434` in the example config). Endpoints used
  (an OpenAI-only server answers `/v1/models` instead of `/api/tags`; the Ollama Manager and Strata's own server add
  the Strata routes):
  - `GET /api/tags` → `{"models":[{"name","size","details":{...}}]}` (Ollama models + Strata names containing `strata`).
  - `POST /api/show {"model": name}` → `capabilities` array may contain `"tools"`. Strata names always support tools.
    If it fails, tools = null ("unknown", still selectable).
  - `POST /v1/chat/completions` (OpenAI format) with `tools`, `stream: true` (SSE `data: {...}` lines, `data: [DONE]`).
    Deltas may contain `content`, `reasoning_content` (or `reasoning`), and `tool_calls` fragments
    (`index`, `id`, `function.name`, `function.arguments` pieces to concatenate). Always send header
    `X-Application-Name: Strata Studio` (the project's name before 2026-11-20, kept because the Ollama
    Manager's exemptions match it). If streaming fails or yields nothing usable, retry once with
    `stream: false` and read `choices[0].message`. `max_tokens` default 8192, never above 16384.
    The first request to a Strata model may take 30-60 s (model loading): use a long timeout (1200 s) and emit a
    `status` event "Waiting for the model (it may be loading)…".

## Layout

```
eidoloom-studio/
  studio.py            # whole backend (stdlib)
  web/index.html  web/app.js  web/app.css
  studio.config.json   # created on first start if missing
  data/                # runtime only: sessions/, checkpoints/, users.json, auth_sessions.json (never served)
  README.md  install-service.sh  eidoloom-studio.service
```

## Config (`studio.config.json`, created with defaults)

```json
{"host": "127.0.0.1", "port": 8790, "gateway": "http://127.0.0.1:11434",
 "workspace_roots": ["~/Projects"], "default_model": "",
 "max_turns": 200, "command_timeout_s": 120, "public_url": ""}
```
Only folders inside `workspace_roots` can be opened (expand `~`, realpath). CLI flags `--host`, `--port`, `--config` override.

## Security (must-haves)

- **Login required for every endpoint except** `GET /`, `/web/*` static files, `GET /api/auth/state`,
  `POST /api/auth/setup`, `POST /api/auth/login`. First start: no user → UI shows "Create password" (setup only
  while no user exists). Password: PBKDF2-SHA256, 200k iterations, random salt, in `data/users.json`.
  Session: random 32-byte token in cookie `studio_session_<hash of data dir>` (`HttpOnly; SameSite=Strict; Path=/`), 7-day expiry,
  kept in memory and `data/auth_sessions.json`. Logout endpoint. More than 5 failed logins per minute per IP → 429.
- **CSRF:** every POST must carry header `X-Studio: 1` (else 403); reject an `Origin` header whose host differs from `Host`.
- **Path confinement:** every file path is relative to the open workspace; resolve with `os.path.realpath`, reject
  anything outside (symlink escapes included). Never serve `data/` or the config file.
- Terminal and agent commands run as the server user with cwd = workspace. Agent commands need approval unless
  "auto-run commands" is on (separate from "auto-apply edits").
- Threaded server (`ThreadingHTTPServer`, daemon threads); SSE responses must not block other requests.

## Backend API (JSON; errors `{"error": "..."}` with proper 4xx/5xx)

Auth: `GET /api/auth/state` → `{"setup_needed","logged_in"}`; `POST /api/auth/setup {"password"}` (≥ 8 chars, logs in);
`POST /api/auth/login {"password"}`; `POST /api/auth/logout`.

Config/models:
- `GET /api/config` → public config + `"gateway_ok"`.
- `GET /api/models` → `{"models":[{"name","label","kind":"strata"|"ollama","tools":true|false|null,"size"}]}`.
  Labels: `mymodel:strata` → "Mymodel (Strata)"; `mymodel-sub2:strata` → "Mymodel (Strata) + 2 GB subagent pool";
  hide `-full/-sub4/-sub6:strata` variants and long profile names (`…:125b-strata-…`) except the default
  `qwen3.8-flash-next:125b-strata` → "Strata default profile". Strata first, then Ollama A–Z. Cache 30 s.

Workspaces:
- `GET /api/workspaces/browse?path=` → `{"path","parent","roots":[...],"dirs":[{"name","path"}]}` (path empty → roots).
- Accounts (roles `admin`, `user`). `GET /api/auth/state` adds `"user"`. `POST /api/auth/login {"username","password","remember"?}`; remember lasts 30 days, otherwise until the browser closes, 24 h at most.
- `GET /api/auth/invite?token=`, `POST /api/auth/signup {"token","username","display_name"?,"password"}`. `GET /api/auth/reset?token=`, `POST /api/auth/reset {"token","password"}`. Tokens are one-time, travel in the URL fragment (`#invite=`, `#reset=`), and expire after 7 days and 1 hour.
- `POST /api/account/password {"current","password"}`, `POST /api/account/logout-others`.
- Admin only: `GET /api/admin/users`, `POST /api/admin/invites {"role"}`, `POST /api/admin/invites/revoke {"id"}`, `POST /api/admin/users/update {"username","role"?,"disabled"?}`, `POST /api/admin/users/reset-link {"username"}`, `POST /api/admin/users/delete {"username"}`.
- Non-admins only see their folder under config `user_root`. A folder inside `user_root` is always sandboxed. Chats, terminals and runs are visible only to their owner, including to admins.
- Waiting room: one user at a time holds the agent's seat. `POST /api/agent/send` takes a free seat, or joins the line and returns 423. `GET /api/queue/state`, `GET /api/queue/stream` (SSE `state` events; an open stream counts as present), `POST /api/queue/join|leave|accept|release|keep`. Admin only: `POST /api/admin/queue/end`, `POST /api/admin/queue/remove {"username"}`, `POST /api/admin/queue/move {"username","position"}`. Config: `queue_idle_s`, `queue_max_s`, `queue_offer_s`, `queue_warn_s`, `queue_presence_s`. A run in progress is never ended by a timer, unless it sits at an unanswered approval past the idle limit while others wait.
- Chat: `GET /api/queue/chat`, `POST /api/queue/chat {"text","to"?}`, delivered as SSE `chat` events on the queue stream. Up to 1000 characters and 8 messages per 10 s. Private messages are visible only to sender and recipient. Admin only: `POST /api/admin/queue/chat/clear`. Kept in memory for 24 hours, up to 200 messages. Never sent to the model.
- Every window's first answer from `/api/queue/stream` or `/api/queue/poll` carries a `chat` event, even with no messages, so the page can tell history from new messages. The state also has `online[]` with `holds` and `position`, `stats {turns_today, typical_turn_s, measured}` (the middle of the last 30 turns, or min(queue_max_s, 20 min) before 3 are timed), `me.wait_s` (what is left of the current turn, from the run's estimate, the session limit or the idle deadline, plus a typical turn per person ahead) and `handoffs {incoming, outgoing}`.
- Passing a chat: `GET /api/people` → every other account `{username, name}`. `POST /api/handoff/offer {ws, session, to, note?}` (≤ 500 characters; refused for yourself, unknown people, a busy chat, a second open offer of the same chat to the same person, or a folder over `handoff_max_mb`, default 2048) → `{offer}`. The recipient `POST /api/handoff/accept {id}` → `{ws, session}`: the folder is copied with `cp -a --reflink=auto` into their own space as `<name>-from-<sender>` (`-2`, `-3` if taken; an admin's goes in the first workspace root when the user root is outside them), with the sandbox settings, undo points and attachments, and the chat is copied with a new id, owner and title `From <name>: …`, without its kept system prompt, ending in a `handoff {from, note, folder, at}` event. `POST /api/handoff/decline {id}` (recipient), `POST /api/handoff/cancel {id}` (sender). Offers live in `data/handoffs.json`, a week unanswered, a day once answered.
- Estimates: every agent tool accepts an optional `progress: "current/total"`. Queue state `holder.estimate` is `{"state":"early"|"range"|"over"|"approval","step","total","low_min"?,"high_min"?}`, calculated from the measured pace of finished steps.
- `POST /api/workspaces/mkdir {"parent","name"}` → `{"name","path"}`. 400 for an invalid name, 403 outside the roots, 409 if it exists.
- `POST /api/workspace/open {"path"}` → `{"workspace":{"id","path","name"}}` (id = first 12 hex of sha1(realpath));
  registry persisted in `data/workspaces.json`. All later calls pass `ws=<id>`.
- `POST /api/workspace/sandbox {"ws","enabled"?,"image"?,"network"?,"ports"?}` → `{"workspace"}`. Per-folder preference, persisted, defaults in config `sandbox_default`, `sandbox_image`, `sandbox_network`, `sandbox_memory`. On: agent commands and terminals run in that folder's container, with only the folder at `/work` and a private home. `network`: `off`, `install` (the agent's dependency commands get internet for that command) or `on`. `ports` are published on 127.0.0.1 and need `on`. `image: ""` restores the default. Off: commands run on the host and the container is removed. 409 during a run or with `sandbox_required`; 503 if Docker fails. File tools always act on the real folder.
- `GET /api/model/status?model=&full=1` → `{"model","state":"off"|"load"|"idle"|"work"|"err","label","detail","active_requests","last_generation","loaded","stream_chars","time","metrics"}`. `metrics` is the Manager's `/api/metrics` subset with `full=1`, else null.
- `GET /api/workspace/recent` → last 10 opened `{"workspaces":[...]}`.

Files (all take `ws`):
- `GET /api/files/tree?ws=&path=` → one level `{"entries":[{"name","path","type":"dir"|"file","size"}]}`, dirs first, A–Z,
  hiding `.git node_modules __pycache__ .venv venv dist build .next .strata-studio .pytest_cache` unless `all=1`.
- `GET /api/files/list?ws=` → `{"files":[relative paths]}` (max 20 000, same ignores) for quick-open.
- `GET /api/files/read?ws=&path=` → `{"path","content","mtime","binary":false}`; binary or > 2 MB → `{"binary":true,...}`.
- `POST /api/files/write {"ws","path","content","expected_mtime"?}` → `{"mtime"}`; 409 `{"error","mtime"}` if changed on disk.
- `POST /api/files/create {"ws","path","type":"file"|"dir"}`; `POST /api/files/rename {"ws","from","to"}`;
  `POST /api/files/delete {"ws","path"}` → moves into `<workspace>/.strata-studio/trash/<timestamp>/<path>` (never hard delete).
- `GET /api/files/search?ws=&q=&regex=0|1&case=0|1` → `{"matches":[{"path","line","text"}],"truncated"}` (≤ 500, ≤ 5 s,
  skip ignored dirs and binaries).

Terminal (stdlib `pty` + `os.fork`/`pty.fork`, one shell per id):
- `POST /api/term/open {"ws","cols","rows"}` → `{"id"}` (spawn `$SHELL -l` or `/bin/bash -l`, cwd workspace, TERM=xterm-256color).
- `GET /api/term/stream?id=` → **SSE** `event: data` `data: {"b64": "..."}`; `event: exit`. Keep a 64 KB scrollback buffer
  and replay it to a new stream.
- `POST /api/term/input {"id","data"}`, `POST /api/term/resize {"id","cols","rows"}` (TIOCSWINSZ), `POST /api/term/close {"id"}`.
  Kill terminals idle 2 h without any stream client.

Agent (one active run per workspace):
- `POST /api/agent/sessions {"ws"}` → `{"id"}`; `GET /api/agent/sessions?ws=` → `{"sessions":[{"id","title","updated","model"}]}`
  (title = first 60 chars of the first user message); `GET /api/agent/session?id=` → `{"id","events":[...],"model"}`;
  `POST /api/agent/session/delete {"id"}`. Stored in `data/sessions/<id>.json` (UI events + model `messages`).
- `POST /api/agent/send {"session","ws","message","model","auto_apply","auto_commands","context"?:{"path","selection"?:{"start_line","end_line","text"}}}`
  → `{"run"}`; 409 if a run is active in that workspace.
- `GET /api/agent/stream?run=&after=<seq>` → **SSE** of events; replays buffered events with seq > after, then live; closes after `done`.
- `GET /api/agent/active?ws=` → `{"run": id|null, "session": id|null}` (to reattach after page reload).
- `POST /api/agent/decision {"run","approval_id","decision":"accept"|"reject","note"?}`.
- `POST /api/agent/stop {"run"}` (pending approvals become rejections; loop ends after the current step).
- `GET /api/comfyui/status` (admins; with `comfyui_url` set) → `{available, state: idle|work|err, pending, live, version,
  memory{ram_*, vram_*, device}, current?{job, prompt, size, steps, seed, step?, of?, elapsed?, studio}, recent[{job, ok, prompt,
  size, seed, seconds, finished, images[{filename, subfolder, type}]}]}`; `{available:false}` without it.
  `GET /api/comfyui/image?filename&subfolder` relays a picture from ComfyUI's output folder.
  Video timelines: `GET /api/video/list?ws` → `{timelines[{path, mtime}], media[{path, full, kind, size}], folder}`;
  `GET /api/video/timeline?ws&path` → `{timeline, mtime, video, video_mtime, job}`; `POST /api/video/new {ws, name}` →
  `{path}` (`video/<name>.timeline.json`); `POST /api/video/save {ws, path, timeline}` → `{layout, mtime}` (400 with the
  reason if it could not render); `POST /api/video/render {ws, path, preview}` → `{job, length}`; `GET /api/video/job?id` →
  `{state: running|done|failed, percent, error, output}`; `POST /api/video/cancel {id}`.
  `/preview/<token>/<path>` honours `Range: bytes=` (206 with `Content-Range`, 416 past the end), so videos can be seeked.
  `POST /api/comfyui/settings {default_model: z-image-turbo|z-image-base|sdxl|qwen-image|auto}` (admins) saves the model the comfyui tool uses when
  the agent names none, in `data/comfyui.json`; the status carries it as `default_model`, and `swap{active, since}` while the
  tool has unloaded Strata for a big model (from config `comfyui_swap_file`). MCP servers take an optional `timeout_s`
  (10-3600) for their tool calls.
  `GET /api/comfyui/graph?job=` (admins) → `{job, state: running|waiting|done|error|stopped, nodes[{id, type, title, values, links[{input,
  node, slot}]}], node, done[], cached[], error?{node, message}, step?, of?, elapsed?, seconds?, images{node: [...]}, summary}`; without
  a job, the one being made or else the latest.
- `POST /api/agent/guide {"run","message"}` guides a running agent: the message is shown at once as `user{text,guided:true}`
  and reaches the model at its next step, as a user message starting "Message from the user while you were working".
  If the model finishes or answers while one is waiting, the run goes on to answer it. After Stop, or once the run is
  ending, it is refused with 409 (the page then queues it); messages still waiting when a run stops are kept in the chat
  for the next message. At most 20000 characters. If the model's next reply after one has no tool call, it is sent back
  once with a `Studio note` asking it to act or call finish. The page labels these messages "Steer".
- With `announce_check`, a reply without a tool call that only announces the next step, in a run that has already used
  tools, is sent back with a `Studio note` (at most 3 times a run, never twice in a row) instead of ending the run.
- With `answer_check`, a run that has only read the folder (list_files, read_file, search) and ends on a `finish` whose
  summary is at most 300 characters, with no reply text that long in the run, is sent back once with a `Studio note`
  asking for the full answer in the summary, since otherwise the user sees only "Catalogued all the routes".
- `POST /api/agent/settings {"run","auto_apply"?,"auto_commands"?}` changes the auto modes of a running agent; pending approvals of a kind switched on are accepted.
- `POST /api/agent/undo {"ws","checkpoint"}` → restores files changed by that checkpoint → `{"restored":[paths]}`.
- `POST /api/agent/decision` for an `ask_user` card (`approval{kind:"question", questions[{question, header, options[{label,
  description}], multi}]}`) takes `answers[{choices:[labels], text}]`, one per question; only offered labels, one unless `multi`,
  and every question needs a choice or text (400 otherwise). Skip is `decision:"reject"`. `approval_resolved` carries `answers`.
- `POST /api/agent/session/rewind {ws, id, user, undo_files?}` cuts the chat just before the user's `user`-th message (0-based,
  steers do not count) in both the events and the model's messages → `{text, attachments, restored}`; with `undo_files` the
  agent's file changes after it are undone, newest first. `POST /api/agent/session/branch {ws, id, user}` → `{id, title}`: a new
  chat with everything before that message (`user` = their count copies all). Both refuse 409 while a run is active in the chat,
  and before a `compacted` event. User events record `mi`, where the message starts in `messages`; older chats are counted.
- `POST /api/agent/send` takes `plan_mode: true` (Plan first: edits are refused, commands that are not `looks_only` and MCP
  calls always ask, even with auto-run, and the message carries `PLAN_NOTE`; `done` gets `plan_mode` and `plan_steps`) and
  `as_file: true` (the message is saved as `.strata-studio/messages/*.md`, and the model is told to read it in parts; the user
  event gets `saved_as` and the first 400 characters).
- `POST /api/agent/compact {ws, session, model?}` → `{run}`: a run that asks the model for a summary (not streamed) and
  replaces the chat's `messages` with it (`COMPACT_INTRO`); the events stay. It emits `compacted{before, after, summary}` and
  ends `done{reason:"compacted"}`; a chat under 4 messages is left alone.
- `/goal`: `POST /api/agent/send` with `goal: true` sets the chat's goal (`session.goal{text, state: active|paused|done|blocked,
  rounds, why}`) and adds `goal_note` to the message. When a run answers or finishes with the goal active, `goal_next` sends it
  back with a `Studio note` (a new round; `goal` event) until a finish summary starts with GOAL COMPLETE, a reply starts with
  BLOCKED:, the rounds reach `goal_max_rounds` (20), or the user pauses it. `POST /api/agent/goal {ws, id, action:
  pause|resume|edit|clear, text?}` → `{goal}`; `done` and `GET /api/agent/session` carry `goal`.
- Tasks (config `tasks`): `GET /api/tasks` → `{tasks, enabled, now}` (the user's own); `POST /api/tasks/save {ws, task{id?, title,
  prompt, mode: look|ask|auto, model, schedule{kind: hourly|daily|weekdays|weekly|every, time "HH:MM", weekday 0-6, minutes}}}`;
  `POST /api/tasks/action {id, action: pause|resume|delete|run|stop}`; `POST /api/tasks/loop {ws, session, text, model, mode}`
  (`parse_loop`: "10m <message>[, stop after N answers | for N hours | stop when it says X]", every minute at most, 100 runs).
  The timer (`task_loop`, every 5 s) starts a due task when the agent seat is free or its owner's and its folder is idle,
  otherwise skips it after 30 minutes; missed times are not made up. Each run is unattended (`run.unattended`): anything
  that needs a yes is answered no at once and listed in `last.refused`. Look only is plan first with read-only commands
  allowed. Tasks get a new chat per run; loops send into their chat. Three failed runs in a row pause a task.
- Time-skip: `POST /api/timeskip/start {ws, session, message, model, auto_commands}` → `{run, id}` copies the folder
  (`cp -a --reflink=auto`, at most `timeskip_max_mb`, without `.strata-studio`) to `data/timeskip/<id>/work` and runs the agent
  there (edits without asking; a sandboxed folder gets its own container). At the end Studio emits `timeskip{id, state:
  ready|nothing|failed, files[{path, status, change|binary}], total, checks{command, original, copy}}`, running the project's
  test command on both (asking unless auto-run). `POST /api/timeskip/decide {id, action: keep|discard, force?}`: keep writes
  the differing files into the folder with an undo point (409 with `conflicts` when the folder changed since the copy, unless
  `force`), and both remove the copy and append `timeskip_decided` to the chat. A chat with a run going refuses other runs (409).
- `POST /api/agent/session/settings {ws, id, effort: none|low|medium|high|xhigh|""}` → `{effort}`: the chat's reasoning
  effort, sent as `reasoning_effort` with its requests (the Manager honours it unless its profile is set to no thinking);
  empty means the Manager's default. `GET /api/agent/session` returns `effort`.
- Your own tools (config `user_tools`): `GET /api/tools/mine`, `POST /api/tools/save {tool{name, kind: instructions|command,
  description, text, on}, old_name?}`, `POST /api/tools/delete {name}`, in `data/user_tools/<sha1(user)>.json`. They reach the
  agent as `user__<name>`, fixed per run: instructions return the text; a command runs in the project (sandboxed when on)
  with the model's `input` in `$STUDIO_INPUT`, never in the command line, and asks like a command (`purpose: "user_tool"`).
- Command results: when an earlier part of a command's last pipe failed but the exit code is 0, the tool result is `ok: false` with
  summary "exit 0, but `<program>` exited N" and an explaining first line (not for status 141, or 1 from grep, rg, diff, cmp).
- Turn guard (config `turn_guard`): 4 failed tool calls in a row, or a 4th plan call, add one `Studio note`; 8 failures, 7 plan
  calls or the same call 4 times in a row end the run `done{reason:"stuck", stuck:"<why>"}`. Calls the user rejected do not count.
  A run that answers with no text ends with `done{silent:true}`.

### Agent SSE events — `event: <type>`, `data: <json incl. "seq">`

`status{text}` · `token{text}` · `thinking{text}` · `tool_call{id,name,args}` · `tool_result{id,name,ok,summary,output}` (output ≤ 4000 chars) ·
`approval{approval_id,kind:"edit"|"command",path?,old?,new?,command?}` (edits carry full old and new content) ·
`approval_resolved{approval_id,decision}` · `file_changed{path,checkpoint}` · `usage{prompt_tokens,completion_tokens,turn,max_turns}` ·
`error{text}` · `user{text,guided,plan_mode?,saved_as?,mi}` (guidance, above) · `compacted{before,after,summary}` ·
`files{files[{path,status:new|changed|deleted,added?,removed?}],total}` (what the run changed, its commands' files included, from a
look at the folder before and after; at most 60 listed) · `text_fixed{text}` (tool calls were read out of the streamed reply;
the reply is only this text) · `done{reason,turns,summary?}`. `file_changed` and edit approvals may have `action:"picture"`
(from `save_picture`, with `picture` in the approval). Also persist these events (merge consecutive token/thinking into one) in the session.

### Agent loop

System prompt (built in code, short and firm):
1. You are an expert software engineer working in the user's project; all paths are relative to the project root.
2. Method: (a) explore first — list/read/search relevant files before editing; (b) for non-trivial tasks write a short
   numbered plan first; (c) prefer `replace_in_file` with an exact, unique `old_text`; `write_file` only for new files or
   small full rewrites; (d) after edits run the project's tests/build/linter when they exist; (e) on failure read the
   error, fix, re-run — at most 3 attempts per problem, then report the blocker; (f) end with `finish(summary)` for
   tasks that changed files; for questions just answer.
3. Reply in the language of the user's message; never mix in words or characters from other languages or scripts.
4. Use only the provided tools with valid JSON arguments; never guess file contents — read them.
Then append: the project rules file if present (first of `STUDIO.md`, `CLAUDE.md`, `AGENTS.md` in the root, ≤ 12 000 chars),
the workspace tree (2 levels, ≤ 200 lines), and the user's current file/selection when `context` is given.

Tools: `list_files(path=".", depth=2)`, `read_file(path, start_line?, end_line?)` (numbered lines, ≤ 400 per call, says how
many remain), `search(query, regex=false, path=".")` (≤ 100 matches), `write_file(path, content)`,
`replace_in_file(path, old_text, new_text)` (error if 0 or > 1 matches, with counts), `run(command)` (bash -lc, cwd workspace,
timeout from config, returns exit code + output tail ≤ 6000 chars), `finish(summary)`.

Approvals: edit tools compute the new content without writing. If `auto_apply` is false, emit `approval{kind:"edit",...}`
and block until decided; accept → write + checkpoint + `file_changed`, tool result "applied"; reject → tool result
"The user rejected this change." + note. `run` the same with `kind:"command"` unless `auto_commands`. Auto modes apply at
once but still checkpoint. Checkpoint = `data/checkpoints/<ws>/<id>.json` with each file's previous content (or absent).

A reply that is cut off or ends in an unreadable tool call is sent back with a `Studio note:` up to twice before the run ends as `cut_off` (`retry_cut_off`). Tool calls written as bare JSON are recovered when the reply ends in nothing else and the tools exist (`text_tool_calls`).

Loop: up to `max_turns` model calls; full conversation kept (system, user, assistant with `tool_calls`, `tool` results).
Assistant reply without tool calls ends the run (`done{reason:"answered"}`). A reply ending in well-formed `<tool_call>` text
counts as tool calls; an unknown tool name gets a failed result listing the real tools, and the run goes on. Stop on `finish`, max turns, user stop,
3 consecutive model errors. Same tool call + args twice in a row → add a user nudge "You repeated the same call; change approach."
If tool outputs total > 90 000 chars, replace older `tool` contents (keep the last 6) with "[older output trimmed]".
A follow-up `send` in the same session continues from the stored messages (system prompt refreshed).
When the user's latest message is clearly Greek or English, the system prompt names that language; otherwise it adds nothing.
Extra SSE events: `check{status,command,summary,output,claimed}` (status passed, failed, skipped or none) and `git_branch{state,branch,base,committed,reason}` (state started, finished or skipped). `usage` also carries `context_tokens`.
Where streams do not arrive (a quick Cloudflare tunnel holds them back), a page that hears nothing on `/api/queue/stream` for 6 s switches to long polling for that tab: `GET /api/queue/poll?message=&sync=`, `/api/agent/poll?run=&after=` and `/api/term/poll?id=&sent=` answer `{events:[{type,data}], cursor:{...}, end?}` with the same events as the streams; send `cursor` back on the next request.
Live servers (config `live_view` for the tool): `GET /api/live/ports?ws=` → `{ports, sandboxed}` (in a sandbox, every listening port in the container; on the host, only ports of programs whose working folder is inside the folder, never Studio's or the gateway's). `POST /api/live/open {ws, port}` → `{url: "/live/<token>/", port, expires_in}`, 403 for a host port outside the folder. `/live/<token>/<path>` (any method, no login, token valid 1 h) relays to `127.0.0.1:<port>/<path>` on the host or, in a sandbox, through a Python relay run with `docker exec`. Browser Cookie, Referer, Origin, Accept-Encoding, X-Studio-* and hop-by-hop request headers are dropped. App cookies are instead kept in an in-memory jar isolated to the Live token and sent only to that app; `Set-Cookie` never reaches Studio's browser origin. Responses lose CSP, X-Frame-Options and HSTS and get `Content-Security-Policy: sandbox allow-scripts allow-forms allow-modals allow-popups; default-src 'self' …`, `Access-Control-Allow-Origin: null`, and `Access-Control-Allow-Credentials: true`; OPTIONS is answered by Studio. HTML, CSS and JavaScript have root paths or full-page `location.assign`/`location.replace` navigation rewritten into the live address. HTML gets a script mapping fetch, XHR, EventSource and history while making sandbox-forbidden `document.cookie` probes return empty (app cookies remain server-side); redirects to the app's own origin stay inside. Streams without a length are passed on as they arrive. The agent's `view_page{port, path?, actions?:[{click}|{type,text}|{press,hold_ms?}|{wait_ms}], width?, full_page?}` (at most 20 steps; width 320–2560, default 1280, under 700 is a phone with touch and a 844-pixel-high window; `full_page` screenshots the whole page; after a click or key that loads another page it waits for that page) asks like a command, renders the page in headless Chromium allowed to load only that live address, and returns the title, visible text (≤ 8000 characters), console errors, failed and blocked requests and step results; it emits `page_view{port,path,title,status,errors,shot,rendered,width}` (`errors` counts console errors only; warnings are listed for the model but not counted, and the GPU driver's performance notes are dropped), and `GET /api/live/shot?id=` serves the screenshot to its owner. Without Node and Playwright it returns the HTML as text (`rendered: false`).

Attachments: `POST /api/agent/send` takes `attachments: [{kind:"image", name, data:"data:image/(png|jpeg|webp|gif);base64,…"} | {kind:"text", name, text}]`, at most 8, pictures ≤ 8 MB each, text ≤ 200K characters in all, text with NUL refused, other kinds 400; pictures to a model without `vision` 400. Pictures are stored in `data/attachments/<session>/<16 hex>.<ext>`, the `user` event carries `images:[{id,name}]` and `files:[names]`, the stored user message keeps text as a string plus `images:[ids]`, and each request sends that message as a text part and `image_url` parts. Text files are added to the message as `Attached file NAME:` blocks before `USER_EXTRAS_END`. `GET /api/agent/attachment?session=&id=` serves a picture to the chat's owner. Deleting a chat removes its pictures. `/api/models` entries carry `vision`.

Scripts (config `script_check`): when the user writes Greek, the system prompt adds that Greek is written only in the Greek alphabet. A final reply or finish summary is sent back once, with `status{text:"The reply mixed scripts or languages…"}` and a `Studio note: your last reply mixed scripts or languages (…)` message, when it mixes scripts (in a Greek chat, or any reply with at least 10 Greek words) or when it is mostly Greek in an English chat.

The agent's `open_preview{port, path?}` (config `live_view`, no approval) shows the user a page of a server the folder runs: it emits `open_live{port,path}`, and an open window opens it in a Live tab. Ports follow the `GET /api/live/ports` rules.

Prompt cache (config `stable_prompt`): a chat stores its first system prompt (`system_prompt`, with `prompt_fixed` and `prompt_sections`) and every later run sends it unchanged. Changing parts (language, the user's earlier requests, project tree, skills, MCP servers, the open file) that differ from what the chat last heard are put at the start of the new user message as `Studio context, newer than the system prompt …:` before `USER_EXTRAS_END`. If the fixed parts change, the prompt is rebuilt. With it off, the prompt is built per run as before.

Model loading: while a run waits for a Strata model's first reply, Studio follows `/api/strata/status` and emits `status{text:"Loading model…", load:{percent, step, elapsed}}` until the engine is ready, then `status{text:"Model loaded. Reading the prompt…"}` unless the reply has already started. `GET /api/model/status` carries the same `load` while the model loads. The percentage is the furthest engine milestone reached, moved on by elapsed time against that model's last load time (default 75 s), and stays below 100 until the engine reports it is loaded.

Plan: the agent's `plan{steps}` (config `plan_tool`): 1 to 20 short step names (a string with a step per line is also taken; numbering and bullets are removed; each at most 120 characters). It emits `plan{steps,revised}` (`revised` when the same run already had a plan) and needs no approval. With it on, rule (b) of the system prompt tells the model to call it first for tasks of more than two steps; with it off the old wording stays. The chat shows a checklist card that each tool call's `progress` "n/total" moves along: steps before n are done, n is current. A run that finishes on its last or next-to-last step ticks every step, an earlier finish or a stop leaves the rest open, a revised plan marks the earlier card replaced, and a new message other than Continue starts fresh. The status line shows "Step n/total: name", or "Step n of total" from `progress` alone when there is no plan. The latest plan is also pinned above the messages: one line (current step, a progress bar) that opens to the whole checklist; each new plan starts closed. Once a run has a plan, its length is the total for the card and the waiting-room estimate, and the first tool result whose `progress` total disagrees gets `Studio note: your plan has N steps, so set progress as "n/N".`

Skills: the agent's `save_skill{name,kind,description,instructions}` (same permission as an edit, event `skill_saved`) and `use_skill{name}`. A user's skills are in `data/skills/`, in every folder. `GET /api/skills`, `POST /api/skills/delete {name}`, `POST /api/skills/save {name,kind,description,instructions,old_name?}` (the user writes a skill, or edits the one named `old_name`, possibly renaming it; same checks as `save_skill`; 409 if the name is taken by another skill, 404 if `old_name` is gone; renaming keeps `created`, `uses` and `folder`; a skill the user wrote has an empty `folder`). Config `skills`.
`/api/queue/stream` also sends `sync{type,origin,...}` (type prefs, sessions, run_started, terminals, files, git or workspace). `GET|POST /api/prefs`, `GET /api/term/list?ws=`.
With `full_thinking` on, chat requests carry `X-Studio-Full-Thinking: 1`, and the Manager then keeps the full thinking budget. With `no_planning_guard` on, they carry `X-Studio-No-Planning-Guard: 1`, and the Manager does not replace a long-planning reply with its tool-less 1536-token final pass.
Before reusing a folder's sandbox container, Studio checks once per container that `/work` and `/home/sandbox` inside it are the same folders (inode) as on disk now; a container that still sees a folder moved or replaced at the same path is removed and created again.
`file_changed` also has `action` (create, edit or delete) and `change{added,removed,rows,truncated,too_large}`, where each row is `[kind, old line, new line, text]`; edit approvals have `change` too. `tool_result` output is up to 12 000 characters from the end for `run`, and the first 4 000 otherwise. `delete_file{path}` is a tool unless `delete_tool` is false.
An edit `approval` with two or more separate changes has `hunks`; `POST /api/agent/decision` takes `parts`, the indexes to apply.
`POST /api/agent/send` takes `git_branch: true` and `mentions`, a list of up to 5 file paths. `POST /api/preview/token{ws}`, `GET /preview/<token>/<path>`. `POST /api/command/run{ws,session,command}`, `GET /api/command/test`, `GET /api/git/changes`; sessions gain `shell` events. Routes: `GET /api/git/status|diff|show`, `POST /api/git/init|commit|branch|run`, `GET /api/agent/session/export`, admin `GET|POST /api/admin/backups`.
MCP tool servers (config `mcp`, `mcp_call_timeout_s`): admins list servers in `data/mcp.json` (private) through `GET|POST /api/admin/mcp` (`{servers:[{name,transport:"stdio"|"http",command,args,env,url,headers,enabled,approval:"always"|"with_commands",users,description}]}`; GET adds `state,error,server_info,log,tools`, and `env` and `headers` values come back as `••••••`, which keep the stored value when sent back) and `POST /api/admin/mcp/restart{name}`. Enabled servers start with Studio and when saved: a program over stdio (newline JSON-RPC, started in the home folder, on the host even when the folder is sandboxed) or Streamable HTTP (JSON or event-stream answers, `Mcp-Session-Id` kept). Studio answers the server's `ping`, refuses its other requests, and rereads tools on `notifications/tools/list_changed`. Each run takes the tools of the ready servers once, when it starts (users only get servers with `users: true`), as `mcp__<server>__<tool>` (≤ 64 characters), with an optional `progress` that is never sent to the server, and one system-prompt paragraph naming the servers. A call asks with `approval{kind:"mcp_ask"|"mcp",server,tool,arguments}`; `mcp` (servers set to `with_commands`) is skipped when `auto_commands` is on. Text results reach the model (≤ 12 000 characters); pictures and audio are described; structured output is shown when there is no text; `isError` fails the call. A stopped run cancels a waiting call. For a model whose capabilities include vision, pictures in a result (PNG, JPEG, WebP, GIF, at most 3 per call) are stored like attachments, the event `tool_picture{server,tool,images:[{id,name}]}` is sent, and after the turn's tool results a user message `Studio note: the pictures returned by … are attached.` carries them as image parts.
Write the session file after each tool round and at the end.

## Frontend

- Login / create-password card first.
- Top bar: "Eidoloom Studio", workspace name (click → Open folder dialog: breadcrumbs inside allowed roots, folder list,
  recent list), model picker (optgroups Strata / Ollama; models with tools=false marked "chat only"), toggles
  "Auto-apply edits" and "Auto-run commands" (localStorage), logout.
- Left sidebar with two views (icon buttons): Explorer (lazy tree; new file, new folder, rename, delete, refresh; click opens)
  and Search (query, regex/case toggles, results grouped by file, click opens at line).
- Centre: Monaco editor tabs (dirty dot, Ctrl/Cmd+S save, close, language by extension, theme `vs-dark`, minimap, wrap toggle;
  409 on save → "File changed on disk: Reload / Overwrite"). Diff tabs "Proposed: <file>" using Monaco diff editor with
  Accept / Reject buttons in a bar above the diff.
- Right: agent panel — session dropdown + "New chat"; messages: user bubbles; assistant text rendered as safe minimal markdown
  (headings, lists, bold, inline code, fenced code with copy button; escape all HTML); collapsible "Thinking"; compact tool rows
  (name, short args, ✓/✗, expandable output); approval cards (path, +added/−removed counts, Open diff / Accept / Reject; commands:
  the command + Run / Skip); "Undo" chip per checkpoint. Input: textarea (Enter sends, Shift+Enter newline), "Stop" while running,
  "Include current file" toggle, status line (turn N/max, tokens, elapsed). Reattach to an active run after reload.
- Bottom: terminal panel (xterm.js + fit, tabs, "+", close), toggle with Ctrl+`; splitters resizable (left, right, bottom), sizes saved.
- Theme: VS Code dark — background `#1e1e1e`, sidebars `#181818`, panels `#1f1f1f`, accent `#3794ff`, borders `#2b2b2b`,
  UI font `system-ui`, code font `ui-monospace, "Cascadia Code", Menlo, Consolas, monospace`.
- Keys: Ctrl+S save, Ctrl+P quick open (fuzzy over `/api/files/list`), Ctrl+Shift+F search, Ctrl+` terminal, Escape closes dialogs.
- Every fetch sends `X-Studio: 1`; 401 → login screen. If Monaco/xterm fail to load from the CDN, show a notice and fall back to a
  plain `<textarea>` editor and a simple line-based terminal.

## Folders on a person's own computer

`tools/helper/` is `studio-helper`, a Go program built for Linux and Windows by `tools/helper/build.sh` into `tools/helper/dist/` (not in Git), downloadable from `GET /api/helpers/download?os=linux|windows`. An admin allows it per user (`POST /api/admin/users/update {pc_folders}`; admins always may; taking it away removes the user's computers).

- `POST /api/helpers/pair` → `{code, expires_in}`: eight characters, 10 minutes, once. `GET /api/helpers` → `{helpers[], allowed, downloads}`. `POST /api/helpers/remove {id}`: the copy stays as an ordinary folder.
- The helper's own calls carry `X-Helper-Key` (stored as its SHA-256 in `data/helpers.json`): `POST /api/helper/register {code, machine, os, shell: bash|powershell, folder}` → `{id, key, folder, skip, max_file}` (5 wrong codes a minute per address); `GET /api/helper/poll?wait=` → `{jobs, skip, max_file}` (long poll, at most 25 s; online while it asked within 40 s); `POST /api/helper/sync {changes: {path: {b64}|null}, skipped}` → `{ops: {path: {sha, b64}|null}, more, conflicts}`; `POST /api/helper/result {job, code, output, timed_out}`; `POST /api/helper/term {term, b64?, exit?}`.
- Jobs: `run {script+env (bash, with PIPE_SCRIPT) | command (PowerShell), timeout}` (the helper syncs, runs it in the folder, syncs, answers), `kill {job}`, `term_open {term, cols, rows}`, `term_in {term, b64}`, `term_resize`, `term_close`.
- Sync: both sides keep the agreed state (`data/helpers/<id>/base.json`, path → sha1). The computer sends what differs from it; Studio applies that to the copy and answers what changed in the copy. A file changed on both sides keeps the computer's version and moves Studio's to `.strata-studio/conflicts/`; one edited here and deleted there is kept. Not shared: `HELPER_SKIP` folders (node_modules, .venv, venv, __pycache__, caches, .next, target, .gradle, .tox, .strata-studio), `.git/*.lock`, symlinks and files over 25 MB. Paths are checked on both sides (no `..`, absolute paths, drive letters or `:`; the helper also refuses a parent that resolves outside its folder).
- The workspace record has `helper`; `public_ws` adds `helper {id, machine, os, shell, remote_path, online, last_seen, skipped}`. Such a folder is never sandboxed, its live ports are none, and the system prompt says which computer and shell its commands run in.
- The helper on the computer: with no arguments it runs in the tray (Windows has no console window) and serves a form on `127.0.0.1:<random>` with a per-run token (`ui.json` in its settings folder; calls need `X-Token`, the right Host and no other Origin): `GET /api/state`, `POST /api/pair {server, code, folder, trust}` (answers `{need_trust, fingerprint, pin}` for a certificate this computer does not trust, until `trust` is that pin), `/api/remove`, `/api/reconnect`, `/api/browse` (native folder picker), `/api/open`, `/api/autostart {on}`, `/api/quit`. A second start opens the running helper's form. `--background` starts it without the form (used at sign-in). Its version (`tools/helper/VERSION`) travels in `User-Agent: studio-helper/<version>`; `GET /api/helpers` adds `latest`, and each helper `version` and `outdated`.

## Voices

Config `voice_url` (tools/voice_engine.py). `GET /api/voices` → `{enabled, engine (its /health or {ok:false,error}), voices[] (+mine, can_edit), sample_text}`. `POST /api/voices/design {description ≤300, text?, takes 1–3}` → `{jobs}`; `POST /api/voices/speak {voice, text ≤20000, style?, format mp3|wav}` → `{job}`; `GET /api/voices/job?id=` → `{state queued|waiting_card|loading|working|done|error|cancelled, progress, audio_s, took_s, error, ahead, url?}`; `POST /api/voices/cancel {id}`; `GET /api/voices/audio?job=|voice=`; `POST /api/voices/save {job, name, description?}` (keep a design take as a new voice) or `{id, name, description?, style?}` (edit; maker or admin); `POST /api/voices/delete {id}` (maker or admin); `POST /api/voices/to_project {ws, job, path .mp3|.wav}` (a file already there goes to the trash). Jobs belong to whoever started them. Voices are shared by every account (`data/voices/voices.json`, recordings in `samples/`), readings kept in `data/voices/out/` (newest 200).
The engine: `GET /health`, `POST /speak {text, description?|reference?, style?, out (full path inside VOICE_OUT_ROOTS), format, cfg?, steps?}` → `{job}`, `GET /job?id=`, `POST /cancel`, `POST /unload`. The MCP server `tools/voice_mcp.py` (env `VOICE_URL`, `VOICES_FILE`, `VOICE_ROOTS`): `list_voices`, `speak {text, voice|description, style?, path}`.

## Account

`GET /api/account` → `{user (public, with last_login), sessions [{id (16 hex of the stored hash), created, device, remember, current}], pc_allowed}`. `POST /api/account/profile {display_name ≤ 60}`. `POST /api/account/sessions/revoke {id}` (not this browser's: 400; not yours or gone: 404). Sessions record `device` (e.g. "Chrome on Windows") at sign-in; users record `last_login`.

Tool servers may name a program they need: `app_command` (command line, `~` allowed), `app_port`, and `app_window` (`screen`, default, or `hidden`: on an Xvfb screen Studio starts and stops with it; without Xvfb the start fails saying so). Before a call, Studio starts it on this machine's screen if nothing answers on that port (a `status` event "Starting …"), and waits up to 90 s. `GET /api/admin/mcp` adds `app {command, port, running, started_by_studio}`; `POST /api/admin/mcp/app {name, action: start|stop}` (stop only for a program Studio started: 409 otherwise; 404 for a server with none).
