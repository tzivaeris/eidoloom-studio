# Eidoloom Studio 0.2.0

Local models installed from the page, and an agent that keeps to what you asked. Upgrading from 0.1.x is `git pull`
and `./install.sh` again.

## New

- **Local models from Tools & setup.** Account & settings → Tools & setup has a Local models group: PolyStrata
  (Qwen3.6-35B-A3B, Ornith 1.5 122B) and Strata (Swift 1.5). Studio offers the models that fit the machine's graphics
  card, memory and disk, and installs one through its own setup, with the download's progress on the page. **Add this
  model** adds another one later. When a chat picks one of these models, Studio starts its server on a port of its own
  (`local_port`), beside any Ollama or other model server. No hand-run scripts are needed.
- **Updates keeps them current.** Updates checks Strata and PolyStrata against their repositories, updates them
  through their own setup and can roll back. Strata 0.2.16 and later keep their server in `terminal/`. Studio finds
  it there or in the old place, so an update does not lose the models. Updates is now on in a new install. Tools a
  machine has not installed are listed under *Not installed here*, not as failed checks.
- **Strata models cannot write Chinese, Japanese, Korean, Cyrillic or other foreign scripts** in a chat that has none
  and does not ask for them (`script_ban`). Each request bans those tokens with `logit_bias`. An engine that refuses
  `logit_bias` is asked again without it.
- **Words mixing Greek and Latin letters are repaired** («αρχείa» → «αρχεία») when the Greek lexicon says which word
  was meant. The rest are sent back to the model. Words of other languages in Greek text are sent back once, then
  removed.
- **A rejected change is never made without a new yes** (`repeat_check`):
  - The same edit sent again is not shown, and the agent is told to ask or try something else. An edit with only a
    renamed constant counts as the same edit.
  - An edit that holds the rejected change with more lines added says so on its card.
  - A command that names the file asks first, even with auto-run on.
- **Rules from earlier in the chat** (`rule_checks`): when you ask for docstrings on every function, each Python edit
  names the new functions that lack one. Before a run that changed files ends, the agent checks its work against all
  of your requests.
- **A finish that leaves the answer out is sent back once** (`answer_check`): a run that only read the project and
  ends on "Catalogued all the routes" without listing them is asked for the answer.
- **Models with a small context window no longer run out of room** (`context_fit`). Near 70% of the model's `num_ctx`,
  older tool output is shortened. Near 85%, the chat is summarized in the run, keeping your requests word for word.
- **Benchmark:** 40 agent tests with fixtures and automatic checks (`sandbox/prompts/`), scored with the guards on and
  off. The results by test and the reviewer's verdicts are in `docs/BENCHMARK.md`, and the README has a 70-second
  video of a real run.

## Fixed

- Tools & setup no longer fails on a machine without Docker, and its root script runs where there is no `loginctl`.
- Account & settings: a late answer from one section no longer draws over the next.

## Tests

Backend tests (1849) and browser tests in real Chromium (702) run on GitHub on every push. Both local model servers
were also installed, run and updated from the page in a clean Ubuntu 24.04 container with an AMD graphics card passed
in. The run fixed a bug in 4 turns before and after an update.

## Files

`studio-helper.exe` (Windows) and `studio-helper` (Linux) are the helper, version 0.08, unchanged since 0.1.0, that
connects a folder on a person's own computer. Put them in `tools/helper/dist/` on the server (see the README's
"Folders on your own computer"). `SHA256SUMS` checks them.

## Known limits

- The server runs on Linux only (developed on Ubuntu 24.04).
- The editor and terminal load from cdn.jsdelivr.net, so the browser needs to reach it.
- The local models and the picture, voice, music and video engines in Tools & setup are written for AMD graphics
  cards (ROCm). Tools & setup only offers a model when the machine has room for it: Ornith 1.5 122B needs about 16 GB of graphics memory, 37 GB of RAM and 50 GB of disk, and Swift 1.5 needs 12 GB, 48 to 60 GB and 68 to 76 GB.
