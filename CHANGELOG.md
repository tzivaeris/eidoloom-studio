# Changelog

## Unreleased

- **A finish that leaves the answer out is sent back once** (`answer_check`, on by default). A run that only read
  the folder and ends on a one-line summary such as "Catalogued all the routes", without writing them, is asked for
  the answer itself.
- The README shows a benchmark of the reply guards, on and off.
- **Benchmark tests 15 to 40** (`sandbox/prompts/BENCH_TESTS.md`): 26 small agent tasks with fixtures and automatic
  checks (answers after reading, edits that must keep a file valid, failures hidden in a pipe, scope, honesty, Greek,
  several steps), scored by `sandbox/overnight.py`.
- `sandbox/demo/record.mjs` records the README's video: a real model in a private Studio, on one of the small projects
  in `sandbox/demo/`, with an optional rejected first edit and a steer (`--reject-first`).

## 0.1.1 (2026-11-25)

See [docs/RELEASE-0.1.1.md](docs/RELEASE-0.1.1.md).

## 0.1.0 (2026-11-24)

See [docs/RELEASE-0.1.0.md](docs/RELEASE-0.1.0.md).
