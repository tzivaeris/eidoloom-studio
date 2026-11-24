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
model dashboard's gauges) need the Ollama Manager's `/api/strata/*` and `/api/metrics` routes, and stay quiet without
them.

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
