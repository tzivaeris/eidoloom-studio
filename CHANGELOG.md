# Changelog

## Unreleased

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
