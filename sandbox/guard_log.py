"""What Studio's guards did in one benchmark run, read from the chat after it, and the run's three measures.

A run is scored three ways (the user's proposal of 2026-11-28):
- task: did it do the job? The test's task checks, and the reviewer's verdict for quality tests.
- model: did the model itself keep the user's rules and rejections? The test's rule checks, and no lapse that
  Studio had to catch (a rejected edit sent again, mixed scripts sent back, an unreadable reply, a missing docstring...).
- harness: what Studio caught before the user saw it, and whether any lapse still reached the user.

`lapses(messages, events)` reads the notes Studio added to the model's conversation (`messages`, the session file's
wire history) and the chat's `text_fixed` events (replies Studio repaired before showing them). Notes that every
run of a kind gets (the closing check against the user's requests, reminders, context notes) are not lapses.
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


def lapses(messages, events):
    """{"caught": {kind: n}, "routine": {kind: n}} for one chat."""
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
    return {"caught": caught, "routine": routine}


# Checks about the user's rules (language, scope, rejections, rules set earlier in the chat); the rest are the task.
RULE_CHECK = re.compile(r"in Greek|Chinese|mixing Greek|rules still hold|remembered the rules|resubmitted|changed nothing|"
                        r"only README\.md changed|exactly one line|no rewrite of unrelated|docstrings", re.I)


def measures(result, verdict=None, caught=None):
    """The run's task, model and harness measures. `verdict` is the reviewer's PASS/FAIL for a REVIEW run (test 4's is
    about the rules, the others' about the task); `caught` is lapses()["caught"]."""
    checks = result.get("checks") or []
    task = [c for c in checks if not RULE_CHECK.search(c["label"])]
    rules = [c for c in checks if RULE_CHECK.search(c["label"])]
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
            "model": user_ok and not caught, "caught": sum(caught.values()), "kinds": sorted(caught),
            "reached_user": not rules_ok}
