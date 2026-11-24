# Eidoloom Studio

Eidoloom Studio is a code editor in your browser with an AI agent working beside you. It looks like VS Code (file tree,
editor tabs, terminals) with an agent chat on the side, and it runs on your own machine with your own models: Ollama,
llama.cpp, vLLM or LM Studio. Nothing goes to a cloud service unless you add one.

The agent reads your project, edits files and runs commands. It asks before each change unless you let it work on its
own, and every change can be undone.

The server is one Python file using only the standard library, so there is nothing to `pip install`.

## What it does

- **An agent that asks first.** Edits arrive as cards with a diff (accept all, some parts, or none); commands as cards
  with Run and Skip. Turn on auto-apply or auto-run when you trust it. Each edit is an undo point.
- **A real editor.** Monaco (VS Code's editor), real terminals, search, Git, and previews of pages, pictures, PDFs,
  Word and Excel files, Markdown, CSV tables and 3D models.
- **A sandbox per folder.** Switch it on, and the agent's commands and terminals run in that folder's own Docker
  container, with the internet on, off, or only for installing packages.
- **Several people.** Admins invite users; each user gets a private space, always sandboxed. Folders can be shared with
  rights per person, and a waiting line takes turns on the one model.
- **Help for the agent.** Plans as checklists, questions with choices, goals it keeps working towards, scheduled tasks,
  skills it saves and reuses, and a check after every edit that the file still parses.
- **Optional tools.** Web search, a browser the agent drives, pictures (ComfyUI), voices, music, sound effects, video
  editing and Blender, each installed with one button in Account & settings → Tools & setup.
- **Phones too.** One section at a time, with big touch targets.

## Requirements

- Linux with systemd and Python 3.10 or newer. It is developed on Ubuntu 24.04.
- A model server that speaks the OpenAI chat API with tool calls (see below).
- A browser that can reach cdn.jsdelivr.net, where the editor and terminal load from.
- Optional: Docker for the sandbox, Git for source control, Node.js for checking pages. Tools & setup lists what each
  tool needs and writes one `sudo` script for all of it.

## Start in ten minutes

1. Get the code and install the service, as your normal user (not root):

   ```bash
   git clone <repository address> eidoloom-studio
   cd eidoloom-studio
   ./install.sh
   ```

   It checks Python, makes `~/Projects`, copies `studio.config.example.json` to `studio.config.json`, and installs a
   systemd user service that starts at boot. Where there is no systemd (a container, WSL without it), it sets up the
   rest and you start Studio yourself with `python3 studio.py`.

2. Open http://127.0.0.1:8790 and create the admin password.

3. Point Studio at your model server: set `gateway` in `studio.config.json`, then run
   `systemctl --user restart strata-studio`.

   | Server | `gateway` | Notes |
   | --- | --- | --- |
   | Ollama | `http://127.0.0.1:11434` (the default) | Give it a long context, since agent prompts are 10–30K tokens: set `OLLAMA_CONTEXT_LENGTH=32768` or more for the Ollama service. |
   | llama.cpp | `http://127.0.0.1:8080` | Start `llama-server` with `--jinja`, so tool calls work, and a large `-c`. |
   | vLLM | `http://127.0.0.1:8000` | Start it with `--enable-auto-tool-choice` and the model's `--tool-call-parser`. |
   | LM Studio | `http://127.0.0.1:1234` | Start its server in the Developer tab. |

   If the server needs a key (`--api-key`), put it in `gateway_key`. An address ending in `/v1` works too.

4. Open a folder in `~/Projects`, pick a model at the top, and ask the agent for something.

Pick a model that is good at tool calls, such as Qwen 3 or Qwen 2.5 Coder (7B or larger), Llama 3.1 or later, Mistral
Small or gpt-oss. Small models (3B and below) often write tool calls as text; Studio tries to recover them.

## Configuration

`studio.config.json` sits next to `studio.py`. Restart the service after changing it.

| Field | What it does |
| --- | --- |
| `host` | `127.0.0.1` (default): only this machine. `0.0.0.0`: other computers on your network too. |
| `port` | The page's port, `8790`. |
| `gateway` | The model server's address. |
| `gateway_key` | A key sent as `Authorization: Bearer …`, for servers started with one. It never reaches the page. |
| `workspace_roots` | The folders Studio may open. `~` works. |
| `default_model` | The model picked first. Empty picks the first one the server lists. |
| `max_turns` | How many model calls one agent run may make. |
| `command_timeout_s` | How many seconds one command of the agent may run. |
| `public_url` | The address people use when Studio sits behind a proxy, for invite links. |

Every other setting and its default is in `DEFAULT_CONFIG` near the top of `studio.py`.

## Reaching it from other computers

Keep Studio on your own network. Anyone who can sign in can run commands as the user Studio runs as, unless their
folder is sandboxed. For other computers, either set `"host": "0.0.0.0"`, or keep `127.0.0.1` and put a web server
with HTTPS in front (for Caddy, `reverse_proxy 127.0.0.1:8790`, leaving the Host header as it is). Do not open the
port to the internet. Read [SECURITY.md](SECURITY.md) first.

## More

- [docs/USING.md](docs/USING.md): approvals, undo, the project rules file, shortcuts and troubleshooting.
- The guide inside Studio (the ? button) explains every feature, in English and Greek.
- [docs/MAINTAINER.md](docs/MAINTAINER.md): how the code is laid out, how to run the tests, and how to work on it.
- [SPEC.md](SPEC.md): the API and event contract between the page and the server.

## Licence

Eidoloom Studio is free software under the GNU Affero General Public License, version 3 ([LICENSE](LICENSE)).
Copyright (C) 2026 John Chatzopoulos; see [NOTICE](NOTICE) for the third-party parts. A commercial licence without
the AGPL's conditions is available from the copyright holder. Contributions are welcome under the agreement in
[CONTRIBUTING.md](CONTRIBUTING.md).
