Build **Tallybook**, a small time-tracking and invoicing app, as a complete project in this empty folder.

## Environment
- Python 3.12, standard library only. There is no internet access, so nothing can be installed.
- Each `run` command is killed after 120 seconds. Never leave a server running in the foreground; tests must start and stop their own server on a free port.

## Required project layout
- `tallybook/`: the Python package, runnable with `python -m tallybook`
- `tests/`: at least 10 of your own `unittest` tests, passing with `python -m unittest discover -s tests`
- `README.md`: what the app does, how to run it, how to run the tests

## Command line
- `python -m tallybook serve --port PORT --db PATH` starts the server on 127.0.0.1, using SQLite at PATH (create it if missing). Handle concurrent requests. Data must survive a restart.
- `python -m tallybook summary --db PATH --from TS --to TS` prints a JSON array, one object per client that has entries starting in the range, sorted by client name: `{"client": name, "minutes": total minutes of all those entries, "uninvoiced_cents": sum of the per-entry amounts of those not yet invoiced, at the client's current rate}`. An invalid timestamp, or `from` not before `to`, prints a message to stderr and exits with code 2.

## Rules
- **Timestamps** are UTC strings in exactly the form `YYYY-MM-DDTHH:MM:SSZ` and must be real dates. `2026-02-30T10:00:00Z` and `2026-01-01T10:00:00+02:00` are invalid.
- **Minutes** for an entry are its duration rounded up to a whole minute: 60 seconds is 1 minute, 61 seconds is 2.
- **Amounts** are integer cents: `minutes * rate_cents / 60`, rounded per line, with exact halves rounded up. Do not use floats for this: 1 minute at rate 150 is 3 cents.
- **Date ranges** are half-open: an entry is in `[from, to)` when `from <= start < to`.

## JSON API
All request and response bodies are JSON unless stated. Every error response has the matching status and a body with an `"error"` string. This includes malformed JSON (400) and unknown paths (404). An unsupported method on a known path returns 404 or 405.

**Clients**: `{"id", "name", "rate_cents"}`
- `GET /api/health` returns 200 `{"status": "ok"}`.
- `POST /api/clients` with `{"name", "rate_cents"}` returns 201 and the client. A name must be a non-blank string, stored trimmed, and may contain any Unicode. `rate_cents` must be an integer >= 0; floats, strings and booleans are invalid (400). A name already used, ignoring case, returns 409.
- `GET /api/clients` returns 200 and a list sorted by id.
- `PATCH /api/clients/{id}` with `{"rate_cents"}` returns 200 and the client, 400 if invalid, 404 if unknown.

**Entries**: `{"id", "client_id", "start", "end", "note", "minutes", "invoice_id"}`
- `POST /api/entries` with `{"client_id", "start", "end", "note"}` returns 201 and the entry. `note` is optional and defaults to `""`; `invoice_id` starts as `null`. It returns 400 for invalid input or `end <= start`, 404 for an unknown client, and 409 if it overlaps another entry of the same client. Entries that only touch (one ends exactly when the next starts) do not overlap, and different clients never conflict. Two simultaneous conflicting requests must never both succeed.
- `GET /api/entries` with optional `client_id`, `from` and `to` query parameters returns 200 and a list sorted by start, then id. Invalid parameters return 400.
- `DELETE /api/entries/{id}` returns 204 with no body, 404 if unknown, 409 if already invoiced.

**Invoices**: `{"id", "number", "client_id", "from", "to", "lines": [{"entry_id", "minutes", "amount_cents"}], "total_cents"}`
- `POST /api/invoices` with `{"client_id", "from", "to"}` bills every uninvoiced entry of that client starting in the range, and returns 201. Lines are sorted by entry start. It sets `invoice_id` on those entries. It returns 400 for invalid input or `from >= to`, 404 for an unknown client, and 409 if there is nothing to bill. Simultaneous requests must never bill an entry twice.
- `number` is `INV-YYYY-NNNN`, where YYYY is the year of `from` and NNNN is a per-year sequence starting at `0001`.
- An invoice never changes after creation, even if the client's rate changes later.
- `GET /api/invoices/{id}` returns 200 and the invoice, 404 if unknown.
- `GET /api/invoices/{id}.csv` returns 200 with content type `text/csv`, 404 if unknown. The header is `entry_id,start,end,minutes,amount_cents`, then one row per line, then a final row `TOTAL,,,<total minutes>,<total cents>`.

## Web UI
`GET /` returns one self-contained HTML page with inline CSS and JavaScript and no external resources. It must include forms with these ids: `client-form` (add a client), `entry-form` (add an entry) and `invoice-form` (create an invoice). It must also list clients, entries and invoices, and show API errors to the user. Render user text with `textContent`; do not use `innerHTML` anywhere. The server must serve only this page and the API, never other files from disk.

## How to work
Plan briefly, then build it, run your tests and fix what fails. Call `finish` only when your tests pass, and include in your summary what you built and anything you know is incomplete.
