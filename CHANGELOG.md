# Changelog

## Unreleased

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
