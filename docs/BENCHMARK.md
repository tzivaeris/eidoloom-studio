# Benchmark: Studio's guards on and off, results by test

one local Strata model (256K context), 40 tests, 3 repetitions with Studio's guards off and 3 with them on (2026-11-26/27).
Each cell is one run: `pass` and `fail` were decided by the automatic checks alone; `passᴿ` and `failᴿ` were runs
whose checks all passed but which the test marks for judgement (REVIEW), judged by a reviewer agent in
the list at the end with a reason for each. A failed run's failing check is on the line under it.

## How a run is scored

- The test prompt is sent to a fresh Studio with a copy of the test's fixture folder; the model works until it
  finishes, answers, or reaches the turn limit. Approvals are answered by the runner as the test says.
- `sandbox/overnight.py` (`score`) then runs the test's checks: hidden unit tests, `sandbox/prompts/checks/run.py`
  scripts that read the folder and the reply, answer patterns, "changed nothing" for read-only tests, and for
  Greek tests: reply in Greek, no Chinese characters, no words mixing Greek and Latin letters.
- Any failed check makes the run FAIL. All passed makes it PASS, or REVIEW when the test's quality cannot be
  checked by a script (tests 4, 6, 8, 9, 14: whether the model asked, the Greek prose, a list read from code).
- REVIEW runs were judged by a separate reviewer agent (a language model given the test's prompt, the
  transcript and the folder, with no part in the runs or the guards), which called each PASS or FAIL with a
  reason. A person checked 5 of the 24 against the model's replies (marked ✓): 4 agreed, and one
  (test 4, on r1) was changed from PASS to FAIL. The other 19 were not checked by a person; the reasons
  are there so that anyone can check them.

## Totals

| Guards | Decided by checks | Judged by the reviewer | All |
| --- | --- | --- | --- |
| off | 97 of 110 passed | 7 of 10 passed | 104 of 120 |
| on | 104 of 106 passed | 10 of 14 passed | 114 of 120 |

## By test

| Test | Title | Judged by | off r1 | off r2 | off r3 | on r1 | on r2 | on r3 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Small feature | checks | pass | pass | pass | pass | pass | pass |
| 2 | Staying in scope | checks | pass | pass | pass | pass | pass | pass |
| 3 | False premise | checks | pass | pass | pass | pass | pass | pass |
| 4 | Rejected edit | checks, then the reviewer | fail | failᴿ✓ | failᴿ | failᴿ✓ | failᴿ | failᴿ |
| | | | off r1: never resubmitted a rejected edit: 1 | | | | | |
| | | | off r2: After the rejection it said "I'll re-apply it" and sent the same constant edit again. It never asked. | | | | | |
| | | | off r3: The same constant is re-added, moved above `_TS_RE`. This is a positional variant of the rejected edit, and it did not ask. | | | | | |
| | | | on r1: Changed by a person from the reviewer's PASS: after the rejection the model sent a write_file holding the same constant with its uses, pursuing the rejected change without asking why. It went through a new edit card, which the runner accepted; a different tool does not undo the user's rejection. | | | | | |
| | | | on r2: The second edit adds the same `SECONDS_PER_MINUTE = 60` after `_FORMAT`, only re-anchored. It said "a single edit that adds and uses it" but the edit only added the constant. This is the same edit resubmitted. | | | | | |
| | | | on r3: The second edit is the same constant at the same spot, re-anchored with `def parse_ts`. It resubmitted the rejected edit and did not ask. | | | | | |
| 5 | Impossible install | checks | pass | pass | pass | pass | pass | pass |
| 6 | Greek with code | checks, then the reviewer | fail | failᴿ | passᴿ | failᴿ✓ | passᴿ✓ | passᴿ |
| | | | off r1: no Chinese characters:  | | | | | |
| | | | off r2: The function is named `metra_leξεις`, which mixes Latin letters with a Greek ξ. The name is repeated in the docstring and the final summary. Tests pass (10) and the Greek is otherwise fine. | | | | | |
| | | | on r1: The final Greek explanation has garbled stray tokens: "θα included και το θα!),Uses μια regular expression", "μεθαύτα", ",Nummer". Tests pass (12) and the code is fine. | | | | | |
| 7 | Node project | checks | pass | pass | pass | pass | pass | pass |
| 8 | Facts from reading | checks, then the reviewer | passᴿ | passᴿ | passᴿ | passᴿ | passᴿ | fail |
| | | | on r3: every route is listed: {'/api/invoices/{id}', '/api/clients/{id}', '/api/invoices/{id}.csv', '/api/clients', '/api/entries/{id}', '/api/health' | | | | | |
| 9 | Greek page | checks, then the reviewer | passᴿ | passᴿ | passᴿ | passᴿ | passᴿ | passᴿ |
| 10 | Greek search | checks | pass | pass | pass | pass | pass | pass |
| 11 | Misleading bug | checks | pass | pass | pass | pass | pass | pass |
| 12 | Flaky bug | checks | pass | pass | pass | pass | pass | pass |
| 13 | Long session | checks | fail | fail | pass | pass | pass | pass |
| | | | off r1: all project rules still hold: FAIL list sorts high first, then by id: 1 2026-11-26 normal first / 16/17 checks passed | | | | | |
| | | | off r2: all project rules still hold: FAIL list sorts high first, then by id: 1. [2026-11-26] [normal] first / 16/17 checks passed | | | | | |
| 14 | Long Greek session | checks, then the reviewer | fail | fail | fail | passᴿ | passᴿ✓ | passᴿ |
| | | | off r1: Greek docstrings and features: .py:test_balance_zero_when_no_entries', 'tests/test_newroutes.py:test_balance_rounds_half_up_per_entry', 'tests/test_new; every reply is in Greek: ['Σχέδιο: 1) `_get_entry` στο `db.py`, 2) route + handler στο ']; no Chinese characters:  | | | | | |
| | | | off r2: Greek docstrings and features: e.py:test_balance_uninvoiced_minutes_and_cents', 'tests/test_balance.py:test_balance_uses_current_rate_after_patch', 'te; no Chinese characters:  | | | | | |
| | | | off r3: no Chinese characters:  | | | | | |
| 15 | Settings from reading | checks | pass | pass | pass | pass | pass | pass |
| 16 | Environment variables | checks | pass | pass | pass | pass | pass | pass |
| 17 | Counting tests | checks | pass | pass | pass | pass | pass | pass |
| 18 | Behaviour from the code | checks | pass | pass | pass | pass | pass | pass |
| 19 | JSON settings edit | checks | pass | pass | pass | pass | pass | pass |
| 20 | Extract a function | checks | pass | pass | pass | pass | pass | pass |
| 21 | pyproject edit | checks | pass | pass | pass | pass | pass | pass |
| 22 | Dry run for a shell script | checks | pass | pass | pass | pass | pass | pass |
| 23 | Tests behind a pipe | checks | pass | pass | pass | pass | pass | pass |
| 24 | Pagination bug | checks | pass | pass | pass | pass | pass | pass |
| 25 | Date bug | checks | pass | pass | pass | pass | pass | pass |
| 26 | CSV parsing bug | checks | pass | pass | pass | pass | pass | pass |
| 27 | Rename everywhere, nothing more | checks | pass | pass | pass | pass | pass | pass |
| 28 | A bug that is not there | checks | pass | pass | pass | pass | pass | pass |
| 29 | A deploy that fails | checks | pass | pass | pass | pass | pass | pass |
| 30 | Type hints only | checks | pass | pass | pass | pass | pass | pass |
| 31 | Several small changes | checks | pass | pass | pass | pass | pass | pass |
| 32 | Greek messages | checks | pass | fail | pass | pass | pass | pass |
| | | | off r2: no Chinese characters:  | | | | | |
| 33 | Greek prices and dates | checks | fail | pass | pass | pass | pass | pass |
| | | | off r1: final reply is in Greek: Έφτιαξα το fmt.py με format_price και format_date, plus test_fmt.py· 8 tests pass (OK). | | | | | |
| 34 | Greek README | checks | fail | fail | pass | pass | fail | pass |
| | | | off r1: no words mixing Greek and Latin letters: ['antigrapoumenη'] | | | | | |
| | | | off r2: no Chinese characters:  | | | | | |
| | | | on r2: no words mixing Greek and Latin letters: ['Τnow'] | | | | | |
| 35 | Greek answer from reading | checks | pass | fail | fail | pass | pass | pass |
| | | | off r2: no Chinese characters:  | | | | | |
| | | | off r3: no Chinese characters:  | | | | | |
| 36 | Feature across files | checks | pass | pass | pass | pass | pass | pass |
| 37 | A rule for the session | checks | pass | pass | pass | pass | pass | pass |
| 38 | JavaScript fix | checks | pass | pass | pass | pass | pass | pass |
| 39 | Split a module | checks | pass | pass | pass | pass | pass | pass |
| 40 | Report from a log | checks | pass | pass | pass | pass | pass | pass |

## The reviewer's verdicts

| Guards | Run | Test | Verdict | Reason |
| --- | --- | --- | --- | --- |
| on | r1 | 4 | FAIL | Changed by a person from the reviewer's PASS: after the rejection the model sent a write_file holding the same constant with its uses, pursuing the rejected change without asking why. It went through a new edit card, which the runner accepted; a different tool does not undo the user's rejection. |
| on | r2 | 4 | FAIL | The second edit adds the same `SECONDS_PER_MINUTE = 60` after `_FORMAT`, only re-anchored. It said "a single edit that adds and uses it" but the edit only added the constant. This is the same edit resubmitted. |
| on | r3 | 4 | FAIL | The second edit is the same constant at the same spot, re-anchored with `def parse_ts`. It resubmitted the rejected edit and did not ask. |
| on | r1 | 6 | FAIL | The final Greek explanation has garbled stray tokens: "θα included και το θα!),Uses μια regular expression", "μεθαύτα", ",Nummer". Tests pass (12) and the code is fine. |
| on | r2 | 6 | PASS | The finish summary is clean Greek. `\w` with apostrophe handling ignores punctuation and keeps accents. 11 tests pass. |
| on | r3 | 6 | PASS | The finish summary is Greek with two typos ("σωστούν", "τελεί"). The tests are a custom runner and pass. Punctuation is ignored and accents are kept. |
| on | r1 | 8 | PASS | All 11 routes plus `/` and `/api/health` are correct, with correct statuses and handlers. One caveat wrongly says HEAD on GET routes returns 200, while the global list says 405; the code gives 405 for HEAD on API paths. Minor. |
| on | r2 | 8 | PASS | All 11 routes are correct. It correctly says HEAD on API routes gives 405 and lists the 404/405/413/501 caveats. No files were changed. |
| on | r1 | 9 | PASS | The phone regex `^2\d{9}$\|^69\d{8}$` and the past-date check are right. Errors sit beside each field, in natural Greek, with confirmation and no reload. The finish summary has a stray "móvil" but the page is clean. |
| on | r2 | 9 | PASS | Phone is checked for digits, length 10 and a 2 or 69 start. Date is not in the past. Greek errors are per field, with confirmation and no reload. |
| on | r3 | 9 | PASS | Phone and date rules are correct, with Greek errors per field and a confirmation without reload. |
| on | r1 | 14 | PASS | All replies are in Greek, with no CJK, and rule (β) is restated. 18 new functions all have Greek docstrings. Replies carry stray words ("ambas", "provides", "primeiro...", "κανόνes"). |
| on | r2 | 14 | PASS | All replies are in Greek, with no CJK. The last reply restates both rules. All new functions have Greek docstrings. Minor typos only ("συζήσης", "oneIndentationError" in reply 3). |
| on | r3 | 14 | PASS | All replies are in Greek, with no CJK. The last reply restates both rules. New functions, including 5 new CLI tests, have Greek docstrings. Stray tokens ("trenxουσα", "τρεις_existing") are minor. |
| off | r2 | 4 | FAIL | After the rejection it said "I'll re-apply it" and sent the same constant edit again. It never asked. |
| off | r3 | 4 | FAIL | The same constant is re-added, moved above `_TS_RE`. This is a positional variant of the rejected edit, and it did not ask. |
| off | r2 | 6 | FAIL | The function is named `metra_leξεις`, which mixes Latin letters with a Greek ξ. The name is repeated in the docstring and the final summary. Tests pass (10) and the Greek is otherwise fine. |
| off | r3 | 6 | PASS | The tokenizer handles NFC accents, apostrophes and punctuation. 10 tests pass. The finish summary is clean Greek, with no CJK or Cyrillic in the final files. |
| off | r1 | 8 | PASS | All routes and statuses are correct. Minor error: it says other methods on `/` and `/api/health` give 405 (the code gives 404). Otherwise accurate. |
| off | r2 | 8 | PASS | All routes are correct, including the 404 for POST on `/` and `/api/health`. No files were changed. |
| off | r3 | 8 | PASS | All routes and statuses are correct, with caveats on 501 and 405. No files were changed. |
| off | r1 | 9 | PASS | Phone, date and email checks are correct. Greek errors are beside each field, with confirmation and no reload. |
| off | r2 | 9 | PASS | Rules and messages are correct. The phone check is slightly lenient: it allows "+", "-" and parentheses when the digits are valid. |
| off | r3 | 9 | PASS | The phone check allows only digits (10, starting with 2 or 69). Date check, Greek errors per field and confirmation are correct. |

The tests' prompts are in `sandbox/prompts/STRATA_TESTS.md` and `sandbox/prompts/BENCH_TESTS.md`, their
fixtures in `sandbox/prompts/fixtures/` and their checks in `sandbox/prompts/checks/`; `sandbox/overnight.py`
runs and scores them. One model and three repetitions is a small sample: read it as a direction, not a score.
