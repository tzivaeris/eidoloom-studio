# Eidoloom Studio 0.1.0

The first public release: an AI agent harness with a code editor around it, in your browser, on your own models.

## What is in it

- **The agent**: it plans, reads your project, edits files and runs commands. Each edit is a card with a diff (accept
  all, some parts or none), each command asks first, and every change can be undone. Auto-apply and auto-run are
  there when you trust it.
- **The editor**: Monaco, real terminals, search, Git, and previews of pages, pictures, PDFs, Word and Excel files,
  Markdown, CSV tables and 3D models.
- **Any model server** with the OpenAI chat API and tool calls: Ollama, llama.cpp, vLLM, LM Studio, or Strata, which
  Studio can also start and switch itself.
- **A sandbox per folder**: the agent's commands and terminals in that folder's own Docker container.
- **Several people**: invited users with private spaces, shared folders with rights per person, and a waiting line
  for the one model.
- **Help for the agent**: plans, questions with choices, goals, scheduled tasks, saved skills, and a check after
  every edit that the file still parses.
- **Optional tools**, each installed from Account & settings → Tools & setup: web search, a browser the agent
  drives, pictures (ComfyUI), voices, music, sound effects, video editing, Blender, and email through listmonk.
- **Phones**: one section at a time, with big touch targets.

## Install

```bash
git clone https://github.com/tzivaeris/eidoloom-studio.git
cd eidoloom-studio
./install.sh
```

Then open http://127.0.0.1:8790, create the admin password, and set `gateway` in `studio.config.json` to your
model server. See the README for the details.

## Known limits

- Linux only; developed on Ubuntu 24.04.
- The editor and terminal load from cdn.jsdelivr.net, so the browser needs to reach it.
- The picture, voice, music and video engines in Tools & setup are written for AMD graphics cards (ROCm).
- The Windows helper (folders on a person's own computer) builds but has not been run on Windows yet.
