# Live test: Sessio, a hard project for a normal Studio folder

Use an empty folder with the Sandbox on. It tests planning across three languages, concurrency, crash recovery, time zones and Greek text.
A long run reaches the turn limit, which also tests the Continue button. Afterwards, run `make test` and `make e2e` yourself in a terminal.

```text
Build "Sessio", a booking system for a tutoring centre in Athens, in this empty folder. Work in small steps and keep going until everything below passes. Do not stop to ask me questions; choose sensibly and record each choice in README.md.

LANGUAGES AND LIMITS
- Server: Go, standard library only (Go's embedded time zone data is allowed, because the system may have none).
- Web page: one page served by the server, plain JavaScript, no external resources, no innerHTML.
- Test tools: Python 3, standard library only.
- All data lives in DATA_DIR. No database.
- Commands: `make test` runs all unit tests, and `make e2e` runs ./e2e.sh. Both exit non-zero on any failure, leave no processes behind, and work when run twice in a row from a clean checkout.

BOOKING RULES
1. A session has a tutor, a room, a start time, a length of 15 to 180 minutes in steps of 15, and a capacity of 1 to 8 students.
2. A tutor or a room can never have two overlapping sessions. A session ending at 10:00 and one starting at 10:00 do not overlap.
3. Times are stored and sent as the local time in Europe/Athens with its offset. A weekly series keeps the same local time across daylight saving changes. A session that falls in the missing hour moves forward to the first valid time. For the repeated hour, use the first occurrence. A series conflict rejects the whole series and changes nothing.
4. A student joins a session, or its waiting list when it is full. When a student leaves, the first on the waiting list takes the seat in the same atomic step, and that is announced as an event.
5. A student cannot be in two overlapping sessions, including through waiting list promotion. In that case promotion skips them, keeps them waiting, and moves on to the next student.

API
All responses are JSON. Errors are {"error": "..."} with a suitable status. Unknown paths give 404 JSON, and bodies over 1 MB give 413 JSON.
- POST /sessions and POST /series. An Idempotency-Key header is required. Repeating a key returns the first result, even after a restart. The same key with a different body gives 409.
- GET /sessions?from=&to=&tutor=&room=
- POST /sessions/{id}/join and POST /sessions/{id}/leave with {"student": "..."}
- DELETE /sessions/{id}. It removes the seats and waiting list too, and announces this.
- GET /students/search?q= finds names ignoring case and Greek accents and treating final sigma as sigma, so "ΓΙΩΡΓΟΣ", "γιώργος" and "γιωργοσ" all match "Γιώργος".
- GET /events streams changes as server-sent events, with an id on each. Reconnecting with Last-Event-ID replays what was missed, in order, with no gaps or repeats, including across a restart.

DURABILITY
Keep state in an append-only log with a checksum on each record. A record is safe on disk before the client gets a success response. On start, rebuild from the log. A torn final record is dropped and logged, while damage anywhere else stops the server with a clear error and changes no data. Add log compaction that a crash at any moment cannot damage, and make a restart after `kill -9` lose nothing that was acknowledged.

PAGE
All visible text is in Greek. It shows a week view that updates live from /events, with forms to create a session and to join or leave. The time zone is always Athens, wherever the browser is. Show server errors in Greek without losing what the user typed.

TESTS
Unit tests must cover overlap edges, the March and October daylight saving changes, promotion skipping, Greek name matching and every kind of log damage.

./e2e.sh must start the real server and run a Python script that proves each of these, working out expected results itself rather than trusting the server:
- 200 clients race for 8 seats and 10 waiting places: exactly 8 get seats and 10 wait, in a consistent order.
- 50 clients race to book one room at one time: exactly one wins.
- 30 clients send one Idempotency-Key at once: one session is created.
- During a mixed load, kill -9 the server 3 times at random moments and restart it: every acknowledged change survives, and no rule above is ever broken.
- An /events client that disconnects and reconnects receives every event exactly once, across restarts.
- A torn final record is recovered from, and damage in the middle refuses to start.

FINISHING
Run `make test` and `make e2e` twice, and show the real output. README.md must explain how to run it, the design, your choices, and the known weaknesses. End by telling me the weakest part of your work and anything incomplete. Never claim something passes unless you ran it and saw it pass.
```
