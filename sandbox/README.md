# Studio agent sandbox

Tests the Studio agent on a full project, in the style of the Ollama Manager arena: a throw-away container, limited resources and hidden tests.

## Run a test
1. Start it with `python3 sandbox/arena.py serve` and open http://127.0.0.1:8791. It has its own password, separate from the main instance on :8790.
2. Open the folder `sandbox/workspace`, pick a model, and paste `sandbox/PROMPT.md` into the agent chat. Auto-apply and auto-run are safe to turn on here.
3. When the agent finishes, run `python3 sandbox/arena.py grade`. Results and a copy of the project go to `sandbox/runs/<time>/`.
4. Run `python3 sandbox/arena.py reset` before the next model. The old workspace is archived under `sandbox/runs/`, not deleted.

Other commands: `up` and `down` start and remove the container. `selftest` repeats the escape checks and grades an empty project (must fail) and `reference/` (must pass).

## What is isolated
| | Agent commands and terminals | Agent file tools | Grader |
|---|---|---|---|
| Runs in | the folder's `strata-sandbox-<id>` container | Studio on the host | fresh container per grade |
| Sees | `workspace/` at `/work`, `/tmp` | `workspace/` only, symlinks out are refused | read-only project and `grader/` |
| Network | none, not even the gateway | n/a | none |

Containers run as your user with no capabilities, `no-new-privileges`, 6 GB RAM, up to 6 CPUs and 1024 processes.

This instance sets `sandbox_required`, so its Sandbox toggle stays on. In a normal instance, the same toggle is a per-folder choice.

The Studio backend itself runs on the host because it has to reach the gateway. `grader/` and `reference/` sit outside the workspace root, so the agent can't read them.

## Result
A run passes only if the layout, compile, own tests (at least 10) and all hidden tests pass. Hidden tests check only behaviour stated in `PROMPT.md`. Report the hidden-test count alongside the result, since partial scores are the useful comparison between models.

The grader doesn't exercise the web UI in a browser. It only checks that the page has the required forms, loads nothing external and avoids `innerHTML`, so open the page yourself to judge it.

## Blocking access to this machine and the LAN
With internet on, a sandbox can otherwise reach services on this machine and the local network. `firewall.sh` blocks that for Studio's `strata-sandbox` network and keeps internet and DNS working:

- `sudo bash sandbox/firewall.sh apply` adds the rules, and `remove` takes them out again.
- `sudo bash sandbox/firewall.sh install-boot` reapplies them after each reboot, and `remove-boot` undoes that.
