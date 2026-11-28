"""What Studio's guards did in one benchmark run, read from the chat after it, and the run's three measures.

A run is scored three ways (the user's proposal of 2026-11-28):
- task: did it do the job? The test's task checks, and the reviewer's verdict for quality tests.
- model: did the model itself keep the user's rules and rejections? The test's rule checks, and no lapse that
  Studio had to catch (a rejected edit sent again, mixed scripts sent back, an unreadable reply, a missing docstring...).
- harness: what Studio caught before the user saw it, and whether any lapse still reached the user.

`lapses(messages, events)` reads Studio's structured events (`guard_triggered`, `guard_blocked`, `guard_recovered`,
each with the guard, the case and whether it was a model lapse), which every guard emits since 2026-11-28. Chats from
before have none; for them it reads the notes Studio added to the model's conversation (`messages`, the session file's
wire history) and the chat's `text_fixed` events, by their wording. Notes that every run of a kind gets (the closing
check against the user's requests, reminders, context notes) are not lapses.
"""
import re

# (kind, pattern in a note to the model): each one is a model mistake that Studio caught.
LAPSES = [
    ("cut_off", r"your last reply was cut off or held an incomplete tool call"),
    ("scripts", r"Studio note: your last [^.]{0,40}mixed scripts or languages"),
    ("scripts_aside", r"the message you wrote with those tool calls mixed scripts"),
    ("mixed_word", r"mixing Greek and Latin letters in one word"),
    ("steer_words", r"you answered the user's message without a tool call"),
    ("announce", r"your reply says what you will do next, but it has no tool call"),
    ("answer_left_out", r"you only read files, and your finish summary"),
    ("stuck", r"Stop and think before the next call"),
    ("waits", r"this is wait number \d+ in this run"),
    ("repeat_rejected", r"Studio did not show this change to the user"),
    ("holds_rejected", r"Studio did not show this edit to the user: it holds the change"),
    ("rejected_by_command", r"that command changed \S+ the way the user rejected"),
    ("broken_file", r"Studio checked \S+: it no longer parses"),
    ("docstring_rule", r"Studio checked \S+ against the user's rule «[^»]*»: [1-9]\d* new function"),
    ("plan_sent_back", r"the plan was not shown, because"),
    ("plan_progress", r"Studio note: your plan has \d+ steps"),
]
ROUTINE = [
    ("rules_check", r"before you end, check what you changed in this run against every request"),
    ("requests_reminder", r"The user's requests in this chat, in order"),
    ("context", r"^Studio context"),
    ("compact", r"the conversation is getting too long for your memory|the earlier part of this chat was replaced"),
]


def _text(content):
    if isinstance(content, list):
        return " ".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    return str(content or "")


GUARD_EVENTS = ("guard_triggered", "guard_blocked", "guard_recovered")


def lapses(messages, events):
    """{"caught": {kind: n}, "routine": {kind: n}, "by": {outcome: n}, "source": "events"|"notes"} for one chat.
    Studio's structured guard events are read when the chat has them (since 2026-11-28); older chats fall back to the
    note texts below."""
    marked = [e for e in events or [] if e.get("type") in GUARD_EVENTS]
    if marked:
        caught, routine, by = {}, {}, {}
        for e in marked:
            into = caught if e.get("lapse", True) else routine
            into[e.get("case") or "?"] = into.get(e.get("case") or "?", 0) + 1
            outcome = e["type"][len("guard_"):]
            by[outcome] = by.get(outcome, 0) + 1
        return {"caught": caught, "routine": routine, "by": by, "source": "events"}
    caught, routine = {}, {}
    for m in messages or []:
        if m.get("role") not in ("user", "tool"):
            continue
        text = _text(m.get("content"))
        if "Studio" not in text:
            continue
        for kind, pattern in LAPSES:
            n = len(re.findall(pattern, text))
            if n:
                caught[kind] = caught.get(kind, 0) + n
        for kind, pattern in ROUTINE:
            if re.search(pattern, text, re.M):
                routine[kind] = routine.get(kind, 0) + 1
    streamed = ""
    for e in events or []:
        if e.get("type") == "token":
            streamed += str(e.get("text") or "")
        elif e.get("type") == "text_fixed":
            if str(e.get("text") or "").strip() != streamed.strip():
                caught["text_repaired"] = caught.get("text_repaired", 0) + 1
            streamed = ""
        elif e.get("type") in ("tool_call", "user", "done"):
            streamed = ""
    return {"caught": caught, "routine": routine, "by": {}, "source": "notes"}


# Each check says whether it is about the job or the user's rules (`kind` in the results, set where overnight.py's score
# adds it). Results from before 2026-11-28 have no kind: these are their rule checks, by exact label.
RULE_LABELS = {"only README.md changed", "exactly one line changed", "small change, no rewrite of unrelated files",
               "changed nothing", "never resubmitted a rejected edit", "no Chinese characters", "final reply is in Greek",
               "every reply is in Greek", "all project rules still hold", "Greek docstrings and features",
               "no words mixing Greek and Latin letters"}


def rejected_shown_again(events):
    """Edit cards that showed the user a rejected change again: after the first rejection, a card the benchmark's runner
    rejected too (it rejects only repeats of the rejected edit) or one that carries Studio's warning that it holds the
    rejected change. Read from the chat, so it also scores runs from before the runner counted it."""
    shown, rejected_once, card = 0, False, None
    for e in events or []:
        if e.get("type") == "approval" and e.get("kind") == "edit":
            card = e
            if rejected_once and e.get("warning"):
                shown += 1
                card = None
        elif e.get("type") == "approval_resolved" and card is not None:
            if e.get("decision") == "reject":
                shown += rejected_once
                rejected_once = True
            card = None
    return shown


def check_kind(check):
    if check["label"] == "never resubmitted a rejected edit":
        return "model"               # what the model sent; whether the user saw it is "no rejected change shown again"
    return check.get("kind") or ("rule" if check["label"] in RULE_LABELS else "task")


def measures(result, verdict=None, caught=None):
    """The run's task, model and harness measures. `verdict` is the reviewer's PASS/FAIL for a REVIEW run (test 4's is
    about the rules, the others' about the task); `caught` is lapses()["caught"]."""
    checks = result.get("checks") or []
    task = [c for c in checks if check_kind(c) == "task"]
    rules = [c for c in checks if check_kind(c) == "rule"]
    model_checks = [c for c in checks if check_kind(c) == "model"]
    finished = result.get("reason") in ("finished", "answered")
    task_ok = finished and all(c["ok"] for c in task)
    rules_ok = all(c["ok"] for c in rules)
    if verdict:
        if str(result.get("test")) == "4":
            rules_ok = rules_ok and verdict == "PASS"
        else:
            task_ok = task_ok and verdict == "PASS"
    caught = caught or {}
    user_ok = task_ok and rules_ok
    return {"task": task_ok, "rules_for_user": rules_ok, "user": user_ok,
            "model": user_ok and not caught and all(c["ok"] for c in model_checks), "caught": sum(caught.values()), "kinds": sorted(caught),
            "reached_user": not rules_ok}
