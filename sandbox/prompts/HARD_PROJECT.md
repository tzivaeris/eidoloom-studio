# Hard project: Batchline

A hard end-to-end test for Strata Studio's agent, in a normal instance with the Sandbox on.

## Setup
1. In Studio, open the folder `~/Projects/strata-hard-test`.
2. Turn **Sandbox** on. In its settings, keep the default image and set internet to **Install commands only**.
3. Pick the model, turn on both auto toggles, and open the gauges.
4. Paste the prompt below.

The model gets at most `max_turns` turns per message (40 by default). If it stops at the limit, send `Continue`.

## Prompt

```text
Build Batchline in this empty folder: a crash-safe pipeline where a TypeScript API accepts CSV jobs and Python workers process them. It must survive a worker being killed at any moment and still produce exactly correct results.

ENVIRONMENT
- You are in a container with Node 20 and Python 3. Each command is killed after 120 seconds, so never leave a server running in the foreground.
- Only commands that directly run a package manager get internet. Run npm install and pip install yourself as their own commands; running them through make or a script will have no internet.
- Start by checking the versions you have.

LAYOUT
- api/: Node 20, TypeScript in strict mode, Express. Tests use Node's built-in test runner.
- worker/: Python 3 in a virtual environment at worker/.venv, using pydantic for row validation. Tests use pytest.
- data/: created at runtime and not committed. The API and workers communicate only through files in this folder, with no database.
- Makefile with: "make test" (all API and worker tests) and "make e2e" (runs ./e2e.sh). Neither may need the internet.
- README.md: how it works, how crash safety is achieved, and known limits.

API (PORT and DATA_DIR come from the environment)
- POST /jobs: the body is a CSV file, and an Idempotency-Key header is required (400 without it). Returns 202 with {"id","status"} for a new job. The same key with the same body returns 200 and the existing job. The same key with a different body returns 409. A body over 20 MB returns 413.
- GET /jobs/:id returns 200 with {"id","status","rows_done","rows_total"}, or 404. Status is one of queued, running, done, failed.
- GET /jobs/:id/result returns the result as JSON once done, 409 if not done yet, 404 if unknown.
- GET /events is a Server-Sent Events stream that sends a job event whenever a job's status or progress changes. It must work for several clients at once and clean up when they disconnect.
- GET / serves a single page with no external resources: upload a CSV, and see the jobs update live from /events. Render all user text with textContent.
- All errors are JSON with an "error" string. Unknown paths return 404.

CSV RULES
- Columns are id,amount,currency,note, in any order, identified by the header row. The header may start with a UTF-8 BOM, and line endings may be LF or CRLF.
- Follow normal CSV quoting: quoted fields can contain commas, double quotes and newlines. The note may contain any Unicode, including Greek.
- A row is valid when: id is non-empty and not used by an earlier valid row in the same file; amount has an optional minus sign, digits, and at most 2 decimal places; currency is exactly 3 capital letters.
- Invalid rows do not fail the job. They are counted and listed by their row number, where the first row after the header is 1.
- A file with a missing required column, or that cannot be parsed at all, ends with status failed and an "error" message.

RESULT
{"rows_total":N,"rows_valid":N,"rows_invalid":N,"invalid_rows":[row numbers in order],"totals":{"EUR":"12.34",...}}
Totals are exact, computed without binary floating point, always have exactly 2 decimal places, are sorted by currency, and handle negative totals and "-0.00" correctly ("0.00"). The same input must always produce the same result, byte for byte.

WORKER (python -m worker, with DATA_DIR from the environment)
- Several workers can run at once. A job is claimed atomically, so two workers can never run the same job at the same time.
- A claim has a heartbeat. If a worker dies, another worker takes over its job within 5 seconds.
- Progress is saved durably at least every 500 rows, using write-then-rename with fsync, and a new worker resumes from the last saved progress instead of starting over. A half-written file must never corrupt a job.
- A worker that lost its claim must not overwrite the result of the worker that took over.
- A worker exits cleanly within 2 seconds on SIGTERM.

./e2e.sh MUST PROVE IT WORKS
It must use only local files and free ports, finish in under 90 seconds, stop everything it started even when it fails, and exit non-zero on any failure. It must:
1. Start the API and two workers with a temporary DATA_DIR.
2. Submit a generated 200,000-row CSV with a mix of valid and invalid rows, some quoted fields and Greek notes, along with a small tricky CSV (BOM, CRLF, quoted newline, duplicate id).
3. While the large job is running, kill -9 the worker that holds it, confirm the other one takes it over, and start a replacement.
4. Kill -9 a worker a second time, while it is writing progress if possible.
5. Compare the results with values computed independently inside e2e.sh, not by reusing the worker's code, and confirm the job resumed instead of restarting by checking the logs.
6. Submit the same key again and confirm it returns the same job.
7. Connect to /events and confirm job events arrive.

Write the tests for the tricky parts: CSV edge cases, money totals, claim races, and lost claims.

FINISH
Call finish only when "make test" and "make e2e" both pass in a final, clean run, with no servers left running, and show me the real output of both. If something does not work, say so plainly; do not weaken a test or the e2e script to make it pass. Your summary must list anything incomplete and the single weakest part of your design.
```

## Check the result yourself
Open a Studio terminal in that folder, where it runs in the same sandbox, and run:

```bash
make test && make e2e && make e2e && pgrep -a "node|python"
```

Running `e2e` twice shows whether it passes reliably, and the last command shows anything left running.

**What to read in the code:**
- Whether `e2e.sh` really computes the expected values separately, instead of reusing the worker's code.
- Whether the claim is truly atomic, such as a rename or `O_EXCL`, rather than "check, then write".
- Whether totals use `Decimal` or integer cents.
- Whether the "resumed" check would fail if the job had restarted from row 1.
- Whether any test was weakened along the way.

**Common failure points:**
- A lost worker overwriting the new worker's result.
- A quoted newline being counted as a new row.
- `-0.00` in the totals.
- `e2e.sh` running past the 120-second command limit.
- Running `npm install` through `make`, which has no internet.
