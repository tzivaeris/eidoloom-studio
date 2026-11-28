# Changelog

## Unreleased

- **A rejected change made by a command is caught too**: a command that changes a file the way you rejected earlier in
  the run, without naming it (a script that builds the file name), is put back after it runs and shown to you again as
  an edit card. It stays only on a new yes. Only that file's text is put back: anything else the command did (other
  files, a database, a service) is not undone, so this is not a full rollback.
- **Fixed**: a command that named one file with a rejected change skipped the after-command check for the others, so
  it could make a rejected change in a second file unseen. Only the files you were warned about and allowed are left
  out now.
- **Fixed**: rolling Strata back from 0.2.16 or later to an older version, which keeps its server outside `terminal/`,
  left the models unlisted. Updates now refuses that rollback before changing anything, and if the earlier engine
  cannot be put back, the code stays at the version its engine was built for.
- **Fixed**: when a small model's context filled up, older finish summaries were shortened to a marker, and a model
  could copy that marker as its own answer. Only the text of older file writes is shortened now.

- **A larger edit that holds a change you rejected is not shown either**: the agent is told to ask you first. If you
  have since written to the agent about that change (naming the file or what it added, without a no), it is shown,
  and its card says it holds the rejected change. An unrelated message does not count.
- **Fixed**: a file name in bold code (``**`db.py`**``) made the check for words mixing Greek and Latin letters skip the
  prose up to the next code span, so «ενόςSingle» reached the chat. An agent that tries `ask_user` where questions
  are off is told to ask in its reply.
- **A request that something "behaves exactly as now"** gets the closing rules check even when it is the only request:
  the agent is asked to compare the old and new output, not to read the code.
- **Asides between tool calls with more English than Greek words get the note too** in a Greek chat (it took four times
  as many before). Such text is shown as it comes, so the note comes after the first one and keeps the later ones Greek.
- **Every guard records what it did** as a structured event in the chat: `guard_triggered` (a note to the model),
  `guard_blocked` (kept from you or put back) or `guard_recovered` (fixed by Studio), with the guard, the case and
  whether it was the model's mistake. The page ignores them; the benchmark counts them.
- Benchmark: each run is scored three ways (the task, the model alone, and what Studio caught before the user saw it,
  `sandbox/guard_log.py`), each check says whether it is about the task or your rules, test 4 counts whether the
  rejected change was shown or applied again (the model's own repeats are recorded apart), and a reply Studio sent
  back is not scored.

## 0.2.0 (2026-11-27)

See [docs/RELEASE-0.2.0.md](docs/RELEASE-0.2.0.md).

- **Local models from Tools & setup**: PolyStrata (Qwen3.6-35B-A3B, Ornith 1.5 122B) and Strata (Swift 1.5) install
  from Account & settings → Tools & setup with a model that fits the machine, through their own setup, with the
  download's progress on the page; Studio starts their server on a port of its own (`local_port`) when a chat picks
  one of their models, beside any Ollama or other server, and Updates keeps them current.
- **A rejected change is never made without a new yes**: a later edit that holds it with more lines added says so on
  its card, and a command that names the file asks even with auto-run on. Only a changed name counts as the same
  line: a changed number, string or operator makes a different edit (`n < 5` and `n <= 5` are not the same). When a
  small model's chat is summarized in the run, the user's earlier requests are kept beside the summary word for word.
- A rejected constant sent again under another name (`SECONDS_IN_MINUTE` for `SECONDS_PER_MINUTE`) is caught too,
  and after a summary in the run the reminders and the final check still list every one of the user's requests
  (they listed only the newest two). The command warning appears only with the repeat check on.
- Benchmark results by test, how runs are scored and the reviewer's verdicts: `docs/BENCHMARK.md`.
- A rejected edit sent again with a line renamed slightly (`_SECONDS_PER_MINUTE` for `SECONDS_PER_MINUTE`) also
  counts as the same edit. In a Greek chat, short English remarks between tool calls get a note to write Greek.
- **Models with a small context window** (Ollama models with `num_ctx`, read from `/api/show`) no longer run out of
  room (`context_fit`, on by default): near 70% of the window older tool output and long written files are shortened,
  and near 85% the chat is summarized in the run, keeping the latest request. qwen3.8:32k had ended long runs on empty
  replies at 32.7K of 32.8K tokens.
- In a Greek chat, a final reply written in English is sent back once. A run that stops on "Plan: add … then run
  them." is asked to carry it out (not in Plan first).
- A run that read the project and also ran its tests, or a command that only looks, still counts as only reading,
  so a finish that leaves the answer out is sent back then too. A word mixing Greek and Latin letters that cannot be
  repaired («Τnow») is removed from text that came with tool calls.
- **A rejected edit is not shown again** in the same run when only its comments or place changed (`repeat_check`): the
  agent is told to ask or try something else.
- **Rules from earlier in the chat** (`rule_checks`): when the user asked for docstrings on every function, each Python
  edit names the new functions without one (in Greek, when asked so); and a run that changed files checks the user's
  earlier requests once before it ends.
- The script ban also covers Arabic, Hebrew, the Indic scripts, Thai, Vietnamese and invisible zero-width and
  direction characters. In Greek chats, words of other languages («**Semua**», «που**All**») are sent back once, then
  removed.
- **Strata models cannot write Chinese, Japanese, Korean or Cyrillic text** in a chat that has none and does not ask
  for those languages (`script_ban`, on by default): each request carries `logit_bias` banning the tokens of the
  model's vocabulary that would write them, read from the Strata profiles' `tokenizer/vocab.json` (or config
  `strata_vocab`). An engine that refuses `logit_bias` is asked again without it. Studio's own notes and the model's replies do not
  turn it off.
- **Words mixing Greek and Latin letters are repaired** («αρχείa» → «αρχεία», «υπάρχουνnoch» → «υπάρχουν»,
  «τρέχειserver» → «τρέχει server») when the Greek lexicon says which word was meant; the rest are sent back as before.
- **A finish that leaves the answer out is sent back once** (`answer_check`, on by default). A run that only read
  the folder and ends on a one-line summary such as "Catalogued all the routes", without writing them, is asked for
  the answer itself.
- The README shows a benchmark of the reply guards, on and off, now over 40 tests, and the Greek tests again with
  the script guards.
- **Benchmark tests 15 to 40** (`sandbox/prompts/BENCH_TESTS.md`): 26 small agent tasks with fixtures and automatic
  checks (answers after reading, edits that must keep a file valid, failures hidden in a pipe, scope, honesty, Greek,
  several steps), scored by `sandbox/overnight.py`. The checks of tests 28 and 34 no longer fail a correct answer
  ("I changed nothing"; a usage line or an example that shows an error on purpose).
- `sandbox/demo/record.mjs` records the README's video: a real model in a private Studio, on one of the small projects
  in `sandbox/demo/`, with an optional rejected first edit and a steer (`--reject-first`).

## 0.1.1 (2026-11-25)

See [docs/RELEASE-0.1.1.md](docs/RELEASE-0.1.1.md).

## 0.1.0 (2026-11-24)

See [docs/RELEASE-0.1.0.md](docs/RELEASE-0.1.0.md).
