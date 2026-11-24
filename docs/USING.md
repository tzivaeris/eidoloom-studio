# Using Eidoloom Studio

The guide inside Studio (the ? button, or `/guide` in the chat box) explains every feature. This page covers what
you need on the first day, and what to do when something goes wrong.

## Approvals

By default the agent asks before it changes a file and before it runs a command.

- An edit appears in the chat as a card with the file, the lines added and removed, and the diff. Accept writes it,
  Reject leaves the file as it was and tells the agent. With several parts you can accept some of them.
- A command appears with its text. Run starts it, Skip tells the agent it was not run.

Two switches above the chat box change this: **Auto-apply edits** and **Auto-run commands**. Both are off at first and
are kept per browser.

## Undo and the trash

- Every edit the agent applies is an undo point. The edit's card has Undo, and the chat's last card lists every file
  the run changed. Undo points live in Studio's `data/` folder, not in your project.
- Files deleted in the Explorer, or by the agent, go to `.strata-studio/trash/<time>/` inside the project. Delete that
  folder yourself to remove them for good.

## The project rules file

If the project's top folder has `STUDIO.md`, `CLAUDE.md` or `AGENTS.md`, the first one found is added to the agent's
instructions (its first 12,000 characters). Use it for build and test commands and the project's conventions. A line
`Test command: make test` in `STUDIO.md` sets the command Studio runs to check a run's changes.

## Shortcuts

| Key | Action |
| --- | --- |
| Ctrl+K | Command palette: every action; type `#` to find a chat |
| Ctrl+P | Go to file |
| Ctrl+/ | All keyboard shortcuts |
| Ctrl+S | Save |
| Ctrl+B | Side bar |
| Ctrl+` | Terminal |
| Escape | Close a dialog |

On macOS use Cmd for Ctrl.

## The service

```bash
systemctl --user status eidoloom-studio
systemctl --user restart eidoloom-studio
journalctl --user -u eidoloom-studio -f
./install-service.sh --uninstall
```

Studio keeps accounts, chats and settings in `data/` beside `studio.py`, and a daily copy of it in `backups/`.
`python3 studio.py --backup` and `--restore FILE` (with the service stopped) do the same by hand.

## Troubleshooting

**The model list is empty, or Studio says the gateway is not reachable.** Check the address in `gateway` with
`curl -s http://127.0.0.1:11434/v1/models` (use your server's address). Ollama answers `/api/tags` too. If the server
needs a key, set `gateway_key`. Restart Studio after changing the config.

**The agent forgets the task, or answers with half a sentence.** The model's context is too short. Agent prompts are
10–30K tokens, and Ollama's default context is much smaller: set `OLLAMA_CONTEXT_LENGTH=32768` or more for the Ollama
service, or start llama.cpp with a larger `-c`.

**The agent writes tool calls as text instead of doing them.** The model is too small or its server does not pass
tools through. Use a model that is good at tool calls; for llama.cpp start `llama-server` with `--jinja`, for vLLM with
`--enable-auto-tool-choice` and the model's `--tool-call-parser`.

**The first answer is slow.** The model loads into memory on its first request, which can take a minute.

**The port is already in use.** `ss -ltnp | grep 8790` shows what holds it. Change `port` in the config, or stop the
old copy. Why a start failed is in `journalctl --user -u eidoloom-studio -n 50`.

**The editor is a plain text box.** The browser could not load Monaco from cdn.jsdelivr.net. The terminal then falls
back to a simple line mode as well.

**A forgotten admin password.** Stop the service, run `python3 studio.py --set-password admin`, and start it again.
