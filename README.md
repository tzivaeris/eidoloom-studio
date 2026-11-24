<p align="center">
  <img src="web/assets/eidoloom-mark-original.png" width="140" alt="The Eidoloom Studio logo">
</p>

<h1 align="center">Eidoloom Studio</h1>

<p align="center">
  <a href="https://github.com/tzivaeris/eidoloom-studio/actions/workflows/tests.yml"><img src="https://github.com/tzivaeris/eidoloom-studio/actions/workflows/tests.yml/badge.svg" alt="Tests"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/licence-AGPL--3.0-blue" alt="Licence: AGPL-3.0"></a>
</p>

Eidoloom Studio is an AI agent harness with a code editor around it, in your browser. The harness is everything that
turns a model into an agent: the loop that plans and calls tools, the tools themselves, approvals and undo, context
management, and checks after every edit. The model is yours and interchangeable: [Ollama](https://ollama.com),
[llama.cpp](https://github.com/ggml-org/llama.cpp), [vLLM](https://github.com/vllm-project/vllm),
[LM Studio](https://lmstudio.ai) or [Strata](https://github.com/Niko1221/Strata), on your own machine. Nothing goes to a cloud service unless you add one.

The editor looks like VS Code (file tree, editor tabs, terminals), with the agent's chat on the side.

The agent reads your project, edits files and runs commands. It asks before each change unless you let it work on its
own, and every change can be undone.

The server is one Python file using only the standard library, so there is nothing to `pip install`.

![Eidoloom Studio: the agent has added a Reset button to a small web page, and its run ends with the files it changed and a summary](docs/screenshots/agent-run.png)

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

## Screenshots

<p>
  <img src="docs/screenshots/approval.png" width="49%" alt="An edit waiting for approval: the diff, with Choose parts, Open diff, Reject and Accept">
  <img src="docs/screenshots/dashboard.png" width="49%" alt="The model dashboard: GPU, VRAM, CPU, RAM, context and temperature gauges, and what is loaded on the graphics card">
</p>

Every edit waits for your yes unless you turn on auto-apply (left), and the model dashboard shows the graphics card and
memory live (right). The model in these pictures is Qwen3.8-Flash-Next, served by Strata.

<p>
  <img src="docs/screenshots/phone-chat.png" width="260" alt="Studio on a phone: the agent's summary of a finished run">
  &nbsp;
  <img src="docs/screenshots/phone-audio.png" width="260" alt="Studio on a phone: the narrated line in the sound player">
</p>

On a phone, Studio shows one section at a time: the chat, the files, the editor and players, the terminal.

## Made with Studio

In one chat, the agent made all of this for the focus timer: a title picture (ComfyUI), a 3D model of the timer
(Blender), a chime, a calm music bed and a narrated line ("Twenty-five minutes. One task. Let's begin."), then a
15-second intro video with all of them on a timeline. It checked each piece itself, from the 3D viewport to the
sound's waveform.

<p>
  <img src="docs/screenshots/picture.png" width="49%" alt="The title picture, made by ComfyUI, open in Studio's image viewer">
  <img src="docs/screenshots/blender.png" width="49%" alt="The 3D tomato timer the agent built in Blender, with the agent's Blender steps beside it">
</p>
<p>
  <img src="docs/screenshots/audio-editor.png" width="49%" alt="The music bed in Studio's wave editor">
  <img src="docs/screenshots/timeline.png" width="49%" alt="The intro video in Studio's timeline editor: the picture, the music, the narration and the chime">
</p>
<p>
  <img src="docs/screenshots/media.png" width="49%" alt="The Media view: every picture, sound and video in the project">
</p>

The picture, sound, music, voice, video and Blender tools are optional; Account & settings → Tools & setup installs
them.

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
   `systemctl --user restart eidoloom-studio`.

   | Server | `gateway` | Notes |
   | --- | --- | --- |
   | [Ollama](https://ollama.com) | `http://127.0.0.1:11434` (the default) | Give it a long context, since agent prompts are 10–30K tokens: set `OLLAMA_CONTEXT_LENGTH=32768` or more for the Ollama service. |
   | [llama.cpp](https://github.com/ggml-org/llama.cpp) | `http://127.0.0.1:8080` | Start `llama-server` with `--jinja`, so tool calls work, and a large `-c`. |
   | [vLLM](https://github.com/vllm-project/vllm) | `http://127.0.0.1:8000` | Start it with `--enable-auto-tool-choice` and the model's `--tool-call-parser`. |
   | [LM Studio](https://lmstudio.ai) | `http://127.0.0.1:1234` | Start its server in the Developer tab. |
   | [Strata](https://github.com/Niko1221/Strata) | `http://127.0.0.1:8080` | Its own server; or let Studio start it, see [Works with Strata](#works-with-strata). |

   If the server needs a key (`--api-key`), put it in `gateway_key`. An address ending in `/v1` works too.

4. Open a folder in `~/Projects`, pick a model at the top, and ask the agent for something.

Pick a model that is good at tool calls, such as Qwen 3 or Qwen 2.5 Coder (7B or larger), Llama 3.1 or later, Mistral
Small or gpt-oss. Small models (3B and below) often write tool calls as text; Studio tries to recover them.

## Works with Strata

[Strata](https://github.com/Niko1221/Strata) (MIT licence) runs Qwen3.8-Flash-Next, a 125B mixture-of-experts model, on
an ordinary graphics card with 64 GB of RAM. Eidoloom Studio was built and tested night after night with it, and does
more with it than with other model servers. Strata is a separate project by its own authors.

**Use a Strata server that is already running.** Point `gateway` at it (`http://127.0.0.1:8080` by default). Studio
recognises it and then:

- shows a loading bar while Strata loads the model, and its state in the status bar: not loaded, loading, working, idle;
- reads the graphics card's memory, load, temperature and power, and the context in use, into the dashboard's gauges;
- saves a long chat's state when a run ends and restores it after a reload, instead of reading the whole chat again
  (this needs `slot_save_path` in the Strata profile);
- unloads the model after `strata_idle_minutes` without requests (15), so the card is free for other work;
- passes pictures to it, and lets you give its model a friendly name (Account & settings → Models → Rename).

**Or let Studio start Strata.** Set `strata_dir` to your Strata folder and `gateway` to a free port, for example:

```json
"strata_dir": "~/Strata",
"gateway": "http://127.0.0.1:18090"
```

Then every profile you made with Strata's setup is a model in the picker. The first message starts Strata with that
profile, and choosing another profile switches it once nothing else is using Strata. Account & settings → Graphics
card decides how much of the card Strata leaves free for the picture, voice and music tools: **Strata first** keeps
the profile's own setting, **Tools ready** leaves 9 GB (`strata_tools_reserve_mib`), applied the next time Strata
starts. Studio never changes Strata's own profiles; it starts from its own copy.

Agent prompts are long, so give the profile a large context (128K or more), and set `slot_save_path` for the saved
chats.

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
| `strata_dir` | A Strata folder: Studio starts Strata itself, one profile at a time (see above). Empty: it does not. |
| `strata_idle_minutes` | With Strata, unload the model after this many minutes without requests; `0` never. |

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
