#!/usr/bin/env python3
"""Scripted stand-in for the Ollama Manager gateway, used by the tests.

Usage: python3 mock_gateway.py [port]    (default 8799)

The scripted agent run is chosen by the number of `tool` messages that follow the last real user message:
  0 list_files, 1 read_file README.md, 2 replace_in_file README.md ("Sample" -> "Demo"), 3 run `echo ok`, 4+ finish.
Special cases:
  - last user message contains "hello" (or the request has no tools): plain text answer, no tool calls
  - model name contains "nostream": streaming requests are refused with HTTP 400 (exercises the fallback)
  - model name contains "repeat": always calls list_files with the same arguments (exercises the nudge)
  - GET /_mock/requests returns a summary of every chat request received
  - MOCK_CONTEXT_TOKENS in the environment: report that context size on /api/strata/status
  - MOCK_STRATA=1: Strata's own server with no Manager (MOCK_STRATA_MODEL names its model, MOCK_STRATA_LOG gets the
    engine's loading lines) (/health, /v1/status, /v1/load, /v1/unload, /slots; GET or POST
    /_mock/strata reads or changes its state; MOCK_STRATA_LOAD_S makes the first request load for that long)
  - MOCK_OPENAI_ONLY=1: only the OpenAI API (/v1/models, /v1/chat/completions), like llama.cpp, vLLM or LM Studio;
    MOCK_API_KEY then requires that key as a Bearer token
  - a message containing "slow load": the engine pretends to load for LOAD_SECONDS first, with its startup lines
  - "work slowly": three `sleep 1` commands, then finish (or answer, "and answer") naming every message the user sent
    while it worked; "slow end" also waits 2 s before that last reply; "only talk" first answers a steer with words only
  - ComfyUI's /queue, /system_stats, /history and /view for Studio's ComfyUI dashboard: one finished picture, and a
    running one after POST /_mock/comfy {"busy": true}
  - "save the picture to PATH [as ID]": the mock MCP server's picture, then save_picture with the id Studio gave it
  - "ask me": asks two questions with ask_user ("ask me one" sends a single question as a bare object), then finishes with the answer
  - "plan the rename": reads, tries an edit, runs `git status` and `touch made.txt`, sends a plan, then answers with numbered steps
  - "fail a lot": reads a different missing file every turn; "replan forever": sends a new plan every turn; "say nothing": an empty reply;
    "skip commands": runs a command per turn, nine times (the tests reject them), then finishes
  - "look quietly": lists, reads README.md and searches with no words between the calls, then edits README.md and finishes
  - "reach the goal": works a round, finishes without the goal, then finishes with GOAL COMPLETE after Studio's goal note;
    "goal blocked" answers BLOCKED; "goal forever" finishes every round without reaching it
  - "use my tools": calls user__shout with input "hi; rm -rf x", then user__house_rules, then finishes with what they returned
  - "edit back and forth": writes notes.txt with three lines, then rewrites it with one of them, then finishes
  - "write a report": write_document report.docx from Markdown, then finishes
  - "announce": works once, then only says what it will do next ("twice": says it again after Studio's note; "ask": asks a question)
"""
import json
import re
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TAGS = [
    ("keystone:strata", 9_000_000_000), ("keystone-sub2:strata", 9_000_000_000),
    ("keystone-full:strata", 9_000_000_000), ("keystone-sub4:strata", 9_000_000_000),
    ("qwen3.8-flash-next:125b-strata", 40_000_000_000),
    ("qwen3.8-flash-next:125b-strata-swift-iq3-xxs", 40_000_000_000),
    ("qwen2.5-coder:3b", 1_900_000_000), ("llama3.2:3b", 2_000_000_000),
]

STEPS = [
    ("list_files", {"path": ".", "depth": 2}, "Let me look at the project layout first."),
    ("read_file", {"path": "README.md"}, "Now reading README.md."),
    ("replace_in_file", {"path": "README.md", "old_text": "Sample", "new_text": "Demo"}, "Replacing the word."),
    ("run", {"command": "echo ok"}, "Checking that the shell works."),
    ("finish", {"summary": "Renamed Sample to Demo in README.md and ran echo ok."}, "All done."),
]

LOG = []
LOAD = {}                 # a pretend Strata engine load: when it started; empty until "slow load" asks for one
LOAD_SECONDS = 6
LOAD_LINES = ["strata generate: memory guard: limit 62.7 GiB", "strata generate: 1466 MiB of weights loaded in 0.7 s",
              "prefill gemm: hipBLASLt tuning enabled", "strata prefill: warmed the BF16 and FP16 GEMM libraries",
              "strata generate: loaded 39.97 GiB at 3.68 GiB/s", "strata generate: pre-filled 8908 of 8908 slots",
              "strata generate: session is up (engine 0.1.47)"]


def strata_status():
    started = LOAD.get("started")
    if started is None:
        return None
    share = min(1.0, (time.time() - started) / LOAD_SECONDS)
    job = {"kind": "start", "state": "running", "started_at": started, "output": "\n".join(LOAD_LINES[:int(share * len(LOAD_LINES))])}
    return {"running": share >= 1, "health": {"loaded": share >= 1}, "job": job}
LOCK = threading.Lock()
COMFY = {"busy": False}
# The Manager's Subagent pool, for Studio's graphics card setting; off until a test posts /_mock/card.
# MOCK_STRATA=1: Strata's own server (serve/server.py) with no Manager in front: /health, /v1/models, /v1/status,
# /v1/load, /v1/unload and /slots, one model, loaded by the first chat request (MOCK_STRATA_LOAD_S seconds).
STRATA_OWN = {"model": os.environ.get("MOCK_STRATA_MODEL") or "swift-1.5-iq3_xxs", "loaded": False, "started": int(time.time()), "requests": 0, "in_flight": 0,
              "last_request_at": None, "unloads": 0, "loads": 0, "load_s": float(os.environ.get("MOCK_STRATA_LOAD_S") or 0)}


def strata_own_status():
    s = STRATA_OWN
    return {"service": "strata", "model": s["model"], "loaded": s["loaded"], "auto_load": True, "engine": "0.1.61",
            "started": s["started"], "uptime_s": int(time.time()) - s["started"], "cache_max_tokens": 262144,
            "context": {"native": 262144, "max_positions": 262144}, "vision": {"enabled": True, "available": True, "error": None},
            "activity": {"requests": s["requests"], "in_flight": s["in_flight"], "last_request_at": s["last_request_at"]},
            "last_timings": {"cache_n": 1000, "prompt_n": 3000, "predicted_n": 200, "predicted_per_second": 70.0,
                             "at": s["last_request_at"]} if s["last_request_at"] else None,
            "vram": {"elastic": False},
            "machine": {"at": int(time.time()), "gpu": {"name": "AMD Radeon", "used_mib": 24092.0 if s["loaded"] else 900.0,
                                                        "total_mib": 24560.0, "util_pct": 12, "temp_c": 41.0, "power_w": 88.0},
                        "ram": {"used_gib": 51.3 if s["loaded"] else 9.0, "total_gib": 62.0}}}


# A Strata engine with session files, for Studio's session cache; off until a test posts /_mock/engine.
ENGINE = {"on": False, "pid": 1, "loaded": True, "saves": [], "restores": [], "files": [], "port": 0}
CARD = {"on": False, "running": True, "busy": 0, "mode": "fixed", "pool_mib": 9216, "posts": [],
        "runtime": {"auto_start": False, "idle_timeout_minutes": 15, "startup_script": "run-test.sh"}}
PIXEL = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                      "1f15c4890000000d49444154789c63f8cfc0f01f0005000201ff89993d1d0000000049454e44ae426082")
COMFY_GRAPH = {"2": {"class_type": "CLIPTextEncode", "inputs": {"text": "a red fox in the snow"}},
               "4": {"class_type": "EmptyLatentImage", "inputs": {"width": 1152, "height": 896}},
               "5": {"class_type": "KSampler", "inputs": {"positive": ["2", 0], "latent_image": ["4", 0], "steps": 25, "seed": 7, "cfg": 7.0}},
               "7": {"class_type": "SaveImage", "inputs": {"images": ["5", 0], "filename_prefix": "studio"}}}
GUIDED = "Message from the user while you were working"


def last_user_index(messages):
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        content = m.get("content", "")
        text = content[0].get("text", "") if isinstance(content, list) and content and isinstance(content[0], dict) else str(content)
        if m["role"] == "user" and not text.startswith(("You repeated", "Studio note:", GUIDED)):
            return i
    return -1


def plan(req):
    """Decide the reply: (reasoning, content, tool_call or None)."""
    messages = req["messages"]
    idx = last_user_index(messages)
    user_text = str(messages[idx].get("content", "")) if idx >= 0 else ""
    user_text = user_text.split("\n[End of attached context]\n\n")[-1]    # what was typed, as Studio's typed_part reads it
    tools_seen = sum(1 for m in messages[idx + 1:] if m["role"] == "tool")
    if str(messages[-1].get("content", "")).startswith("Studio note: the conversation is getting too long"):
        return "", "Summary: the user asked to rename Sample to Demo in README.md; it is done and echo ok ran. Nothing is left to do.", None
    if "repeat" in req.get("model", "") and req.get("tools"):
        return "Same again.", "Listing.", ("list_files", {"path": "."})
    if not req.get("tools") or ("hello" in user_text.lower() and tools_seen == 0):
        return "The user greets me.", "Hello! I am the mock model. How can I help?", None
    if "truncated call" in user_text.lower():
        return "", "<tool_call>\n<function=write_file>\n<parameter=content>\nhalf a fi", None
    if "garbled once" in user_text.lower():
        if not any(str(m.get("content", "")).startswith("Studio note:") for m in messages):
            return "", "<tool_call>\n<function=replace_in>\n</function>", None
        return "", "Done.", ("finish", {"summary": "Sent it again."})
    if "bare call" in user_text.lower():
        if tools_seen == 0:
            return "", 'Let me look.\n```json\n{"name": list_files, "arguments": {" path": "."}}\n```', None
        return "", "Done.", ("finish", {"summary": "Listed the folder."})
    if "ξένοι χαρακτήρες" in user_text:
        # Greek words with Chinese ones between, written beside a tool call (Keystone, 2026-11-15)
        if tools_seen == 0:
            return "", "Ο用户使用: θα κοιτάξω τα αρχεία του φακέλου.", ("list_files", {"path": "."})
        return "", "Έτοιμο.", ("finish", {"summary": "Κοίταξα τον φάκελο."})
    if "mixed call" in user_text.lower():
        # a real call and another written as text in the same reply (Keystone, 2026-11-15)
        if tools_seen == 0:
            return "", "<tool_call>\n<function=search>\n<parameter=query>\nSample\n</parameter>\n</function>\n</tool_call>", ("list_files", {"path": "."})
        return "", "Done.", ("finish", {"summary": "Listed and searched."})
    if "leaked call" in user_text.lower() and tools_seen == 0:
        return "", 'Let me look.\n<tool_call>\n<function=list_files>\n<parameter=path>\n.\n</parameter>\n</function>\n</tool_call>', None
    if "learn a skill" in user_text.lower():
        if tools_seen == 0:
            return "", "Saving what I learned.", ("save_skill", {"name": "Check a README", "kind": "testing", "description": "When a README must match the code.",
                                                                 "instructions": "1. Read the README.\n2. Run each command in it."})
        if tools_seen == 1:
            return "", "Loading it.", ("use_skill", {"name": "check a readme (testing)"})
        return "", "Done.", ("finish", {"summary": "Saved and used a skill."})
    if user_text.lower().startswith("save the picture to "):
        # "save the picture to PATH [as ID]": asks the mock MCP server for a picture, then saves it with save_picture.
        words = user_text.split()
        found = re.search(r'picture "([0-9a-f]{16}\.\w+)"', next(str(m["content"]) for m in reversed(messages) if m["role"] == "tool")) if tools_seen == 1 else None
        if tools_seen == 0:
            return "", "Getting it.", ("mcp__mock__picture", {})
        if tools_seen == 1:
            picture = words[6] if len(words) > 6 and words[5] == "as" else (found.group(1) if found else "none")
            return "", "Saving it.", ("save_picture", {"picture": picture, "path": words[4]})
        return "", "Done.", ("finish", {"summary": "Saved: " + str(messages[-1].get("content", ""))[:300]})
    if "use mcp" in user_text.lower():
        # "use mcp <tool name> <JSON arguments>": calls it once, then finishes with what it returned.
        if tools_seen == 0:
            words = user_text.split(None, 3)
            return "", "Using the tool.", (words[2], json.loads(words[3]) if len(words) > 3 else {})
        return "", "Done.", ("finish", {"summary": "Tool said: " + str(messages[-1].get("content", ""))[:500]})
    if "chinese aside" in user_text.lower():
        # Writes Chinese inside Greek beside a tool call, as Keystone did; then says whether a note followed the call's result.
        if tools_seen == 0:
            return "", "Δύο ευρήματα ορθογραφίας需要 διόρθωσης, η σωστή αόριστος形态. Κοιτάζω τα αρχεία.", ("list_files", {"path": "."})
        noted = any("Do not write it again" in str(m.get("content", "")) for m in messages if m["role"] == "user")
        return "", "Η σημείωση ήρθε μετά το αποτέλεσμα." if noted else "Καμία σημείωση.", None
    if "greeklish" in user_text.lower():
        # Answers in Greek written in Latin letters until a Studio note asks again; "greeklish finish" does it in a finish summary.
        noted = any("mixed scripts" in str(m.get("content", "")) for m in messages if m["role"] == "user")
        bad = ("To programma trexei kanonika kai ta tests pernane ola. I selida deixnei ti lexi gia tria defterolepta kai meta tin krivei. "
               "O xristis grafei tin apantisi kai vlepei to apotelesma me to skor. Den vrika kanena provlima stin ylopoiisi.")
        good = "Το πρόγραμμα τρέχει κανονικά και τα tests περνούν όλα. Η σελίδα δείχνει τη λέξη για τρία δευτερόλεπτα και μετά την κρύβει."
        if "twice" in user_text.lower():        # after the one send-back, a summary with Chinese inside, as Keystone wrote
            return "", "Τέλος.", ("finish", {"summary": good.replace("τρέχει", "τρέχει写了") if noted else bad})
        if "finish" in user_text.lower():
            return "", "Τέλος.", ("finish", {"summary": good if noted else bad})
        return "", good if noted else bad, None
    if "make a plan" in user_text.lower():
        # Plans three steps, works through two, then revises the plan ("and revise") or finishes; "and stop" answers without finishing.
        steps = ["1. Look at the files", "- Read README.md", "Finish"]
        script = [("plan", {"steps": "\n".join(steps) if "as text" in user_text.lower() else steps, "progress": "1/3"}),
                  ("list_files", {"path": ".", "progress": "1/3"}), ("read_file", {"path": "README.md", "progress": "2/3"})]
        if "miscount" in user_text.lower():      # totals that disagree with the plan, twice
            script[1:] = [("list_files", {"path": ".", "progress": "1/5"}), ("read_file", {"path": "README.md", "progress": "2/5"})]
        if "and revise" in user_text.lower():
            script.append(("plan", {"steps": ["Look at the files", "Read README.md", "Check the notes", "Finish"], "progress": "3/4"}))
            script.append(("run", {"command": "echo notes", "progress": "3/4"}))
        if tools_seen < len(script):
            return "", "Working.", script[tools_seen]
        if "and stop" in user_text.lower():
            return "", "I stopped here.", None
        return "", "Done.", ("finish", {"summary": "Followed the plan."})
    if "work slowly" in user_text.lower():
        guided = [str(m["content"]).split("\n[End of attached context]\n\n")[-1] for m in messages[idx + 1:]
                  if m["role"] == "user" and str(m["content"]).startswith(GUIDED)]
        nudged = any(str(m.get("content", "")).startswith("Studio note: you answered the user's message without a tool call") for m in messages[idx + 1:])
        if guided and "only talk" in user_text.lower() and not nudged:
            return "", "Adding it now.", None          # says what it will do instead of doing it
        if tools_seen < 3:
            return "", "Working.", ("run", {"command": "sleep 1", "progress": f"{tools_seen + 1}/3"})
        if "slow end" in user_text.lower() and not guided:
            time.sleep(2)
        text = "Done." + (" Guidance: " + " | ".join(guided) if guided else "")
        return "", text, None if "and answer" in user_text.lower() else ("finish", {"summary": text})
    if "goal" in user_text.lower() and any(k in user_text.lower() for k in ("reach the goal", "goal blocked", "goal forever")):
        rounds = sum(str(m.get("content", "")).startswith("Studio note: the goal is not reached yet") for m in messages[idx + 1:])
        if "goal blocked" in user_text.lower():
            return "", "BLOCKED: I need the API key for the payment service.", None
        if "goal forever" in user_text.lower():
            return "", "Done.", ("finish", {"summary": f"Did a bit more (round {rounds})."})
        if rounds == 0 and tools_seen == 0:
            return "", "Working.", ("run", {"command": "echo part1"})
        if rounds == 0:
            return "", "Done.", ("finish", {"summary": "Did part 1."})
        return "", "Done.", ("finish", {"summary": "GOAL COMPLETE: both parts are done."})
    if "use my tools" in user_text.lower():
        if tools_seen == 0:
            return "", "Shouting.", ("user__shout", {"input": "hi; rm -rf x"})
        if tools_seen == 1:
            return "", "Rules.", ("user__house_rules", {})
        outs = [str(m.get("content", "")) for m in messages[idx + 1:] if m["role"] == "tool"]
        return "", "Done.", ("finish", {"summary": " | ".join(o.replace("\n", " ") for o in outs)})
    if "edit back and forth" in user_text.lower():
        script = [("write_file", {"path": "notes.txt", "content": "keep\nextra one\nextra two\n"}), ("write_file", {"path": "notes.txt", "content": "keep\n"})]
        if tools_seen < len(script):
            return "", "Writing.", script[tools_seen]
        return "", "Done.", ("finish", {"summary": "Wrote notes.txt."})
    if "look quietly" in user_text.lower():
        script = [("list_files", {"path": "."}), ("read_file", {"path": "README.md"}), ("search", {"query": "Sample"}),
                  ("replace_in_file", {"path": "README.md", "old_text": "Sample", "new_text": "Demo"})]
        if tools_seen < len(script):
            return "Next step.", "", script[tools_seen]
        return "", "Done.", ("finish", {"summary": "Renamed it."})
    if "fail a lot" in user_text.lower():
        return "", "Trying.", ("read_file", {"path": f"missing-{tools_seen}.txt"})
    if "replan forever" in user_text.lower():
        return "", "Planning.", ("plan", {"steps": [f"Step {tools_seen}", "Then the rest"]})
    if "say nothing" in user_text.lower():
        return "", " ", None
    if "skip commands" in user_text.lower():
        if tools_seen < 9:
            return "", "Running.", ("run", {"command": f"echo {tools_seen}"})
        return "", "Done.", ("finish", {"summary": "Gave up on commands."})
    if "plan the rename" in user_text.lower():
        script = [("read_file", {"path": "README.md"}), ("write_file", {"path": "README.md", "content": "changed"}),
                  ("run", {"command": "git status"}), ("run", {"command": "touch made.txt"}),
                  ("plan", {"steps": ["Rename Sample to Demo in README.md", "Run the tests"]})]
        if tools_seen < len(script):
            return "", "Looking.", script[tools_seen]
        return "", "Here is my plan:\n1. Rename Sample to Demo in README.md\n2. Run the tests", None
    if "ask in mixed letters" in user_text.lower():
        # A question card with «potάμι»: Studio sends it back unseen; the second card is fixed.
        last = str(messages[-1].get("content", "")) if messages else ""
        if tools_seen == 0:
            return "", "A question.", ("ask_user", {"question": "Ποιο θέμα;", "options": [{"label": "Δάσος", "description": "δέντρο, λουλούδι, potάμι"}, {"label": "Θάλασσα"}]})
        if tools_seen == 1 and "not shown" in last:
            return "", "Fixed it.", ("ask_user", {"question": "Ποιο θέμα;", "options": [{"label": "Δάσος", "description": "δέντρο, λουλούδι, ποτάμι"}, {"label": "Θάλασσα"}]})
        return "", "Done.", ("finish", {"summary": "Got: " + last[:300]})
    if "write broken code" in user_text.lower():
        # A Python file that no longer parses, then fixed; a broken file edited again stays quiet; a good JS file.
        script = [("write_file", {"path": "calc.py", "content": "def add(a, b):\n    return a +\n"}),
                  ("replace_in_file", {"path": "calc.py", "old_text": "return a +\n", "new_text": "return a + b\n"}),
                  ("write_file", {"path": "old.py", "content": "x = (\n"}),
                  ("write_file", {"path": "app.js", "content": "export const a = 1;\n"}),
                  ("write_file", {"path": "data.json", "content": "{\"a\": 1,}\n"})]
        if tools_seen < len(script):
            return "", "Writing.", script[tools_seen]
        return "", "Done.", ("finish", {"summary": "Wrote them."})
    if "write mixed letters" in user_text.lower():
        if tools_seen == 0:
            return "", "Writing.", ("write_file", {"path": "story.md", "content": "Το νησί έχει ένα potάμι και μια βάρκα.\nconst mapΑ = 1;\n"})
        return "", "Done.", ("finish", {"summary": "Wrote it."})
    if "ask me" in user_text.lower():
        if tools_seen == 0:
            one = {"question": "Which colour should the button be?", "header": "Colour", "options": ["1. Blue", "2. Green", {"label": "Red", "description": "Stands out most"}]}
            if "ask me one" in user_text.lower():
                return "", "One question first.", ("ask_user", one)
            return "", "Two questions first.", ("ask_user", {"questions": [one, {"question": "Which pages need it?", "options": [{"label": "Home"}, {"label": "Settings"}, {"label": "Help"}], "multi": True}], "progress": "1/2"})
        return "", "Done.", ("finish", {"summary": "Got: " + str(messages[-1].get("content", ""))[:600]})
    if "announce" in user_text.lower():
        # Works once, then says what it will do instead of doing it; "twice" says it again after Studio's note, "ask" asks instead.
        notes = sum(str(m.get("content", "")).startswith("Studio note: your reply says what you will do next") for m in messages[idx + 1:])
        if tools_seen == 0:
            return "", "Looking first.", ("list_files", {"path": "."})
        if "ask" in user_text.lower():
            return "", "Should I also rebuild the back covers?", None
        if notes == 0 or ("twice" in user_text.lower() and notes == 1 and tools_seen == 1):
            return "", "Adding the back covers now." if notes == 0 else "I'll rebuild them next.", None
        if tools_seen == 1:
            return "", "Doing it.", ("run", {"command": "echo covers"})
        return "", "Done.", ("finish", {"summary": "Added the back covers."})
    if user_text.startswith("Run this: "):
        if tools_seen == 0:
            return "", "Running it.", ("run", {"command": user_text[len("Run this: "):], "progress": "1/1"})
        return "", "Done.", ("finish", {"summary": "Output: " + str(messages[-1].get("content", ""))[:500]})
    if "show math" in user_text.lower():
        return "", "Euler: $e^{i\\pi} + 1 = 0$, and it costs $5 and $10.\n\n$$\\int_0^1 x^2\\,dx = \\frac{1}{3}$$\n\nDone.", None
    if "show a table" in user_text.lower():
        return "", ("**Chair rotations:**\n\n| Object | Position (x, y, z) | Rotation Z |\n|---|---|---:|\n"
                    "| Chair_North_1 | (−0.55, 1.05, 0.0) | +27.6° |\n| Chair_South_1 | `(−0.55, −1.05, 0.0)` | 152.4° |\n\nScene unchanged otherwise."), None
    if "open preview" in user_text.lower():
        if tools_seen == 0:
            return "", "Opening it for you.", ("open_preview", {"port": int(user_text.lower().split("open preview", 1)[1].split()[0]), "progress": "1/1"})
        return "", "Done.", ("finish", {"summary": "Opened: " + str(messages[-1].get("content", ""))[:300]})
    if "greek-reply" in user_text.lower():
        # An English chat answered in Greek with Chinese mixed in, until a Studio note asks again.
        if any("mixed scripts" in str(m.get("content", "")) for m in messages if m["role"] == "user"):
            if "in greek" in user_text.lower():
                return "", "Ο server τρέχει στη θύρα 8000 και η σελίδα φορτώνει χωρίς σφάλματα. Οι λέξεις εμφανίζονται κανονικά κάτω από το πλαίσιο.", None
            return "", "The server is running on port 8000 and the page loads without errors.", None
        return "", ("Ο server τρέχει τώρα στη θύρα 8000 και η σελίδα φορτώνει σωστά. Οι 20 λέξεις作为 κουμπιά εμφανίζονται κάτω από το πλαίσιο "
                    "και το σκορ ενημερώνεται μετά από κάθε απάντηση του χρήστη."), None
    if "view page" in user_text.lower():
        # "view page <port> [path]": opens it, clicks #go, then finishes with what it saw.
        if tools_seen == 0:
            words = user_text.lower().split("view page", 1)[1].split()
            return "", "Looking at the page.", ("view_page", {"port": int(words[0]), "path": words[1] if len(words) > 1 else "/",
                                                                "actions": [{"click": "#go"}, {"wait_ms": 300}], "progress": "1/1"})
        return "", "Done.", ("finish", {"summary": "Page: " + str(messages[-1].get("content", ""))[:3000]})
    if "two parts" in user_text.lower():
        if tools_seen == 0:
            return "", "Editing.", ("write_file", {"path": "parts.txt", "content": "ONE\nb\nc\nd\ne\nf\ng\nSEVEN\n"})
        return "", "Done.", ("finish", {"summary": "Changed both lines."})
    if "write a report" in user_text.lower():
        if tools_seen == 0:
            return "", "Writing it.", ("write_document", {"path": "report.docx", "content": "# Report\n\nAll **good**.\n\n| Name | Score |\n| --- | --- |\n| Maria | 18 |\n"})
        return "", "Done.", ("finish", {"summary": "Wrote report.docx."})
    if "delete a file" in user_text.lower():
        if tools_seen == 0:
            return "", "Removing it.", ("delete_file", {"path": "old.txt"})
        return "", "Done.", ("finish", {"summary": "Deleted old.txt."})
    if "two edits" in user_text.lower():
        if tools_seen == 0:
            return "", "Editing.", ("write_file", {"path": "lines.txt", "content": "".join(f"LINE {i}\n" if i in (2, 30) else f"line {i}\n" for i in range(1, 41))})
        if tools_seen == 1:
            return "", "Checking.", ("run", {"command": "seq 1 9; exit 4"})
        return "", "Done.", ("finish", {"summary": "Edited."})
    if "make secrets" in user_text.lower():
        if tools_seen == 0:
            return "", "Working.", ("run", {"command": "echo SECRET=1 > .env; mkdir -p node_modules; echo x > node_modules/big.js; echo more >> README.md"})
        return "", "Done.", ("finish", {"summary": "Made files."})
    if "touch git folder" in user_text.lower():
        if tools_seen == 0:
            return "", "Editing.", ("write_file", {"path": ".git/config", "content": "x"})
        return "", "Done.", ("finish", {"summary": "Tried it."})
    if "unknown call" in user_text.lower() and tools_seen == 0:
        return "", 'Editing.\n<tool_call>\n<function=edit_file>\n<parameter=path>\nREADME.md\n</parameter>\n</function>\n</tool_call>', None
    name, args, text = STEPS[min(tools_seen, len(STEPS) - 1)]
    if "with progress" in user_text.lower():
        args = {**args, "progress": f"{min(tools_seen + 1, len(STEPS))}/{len(STEPS)}"}
    reasoning = "I should start by exploring the project." if tools_seen == 0 else ""
    if tools_seen == 0:
        text = "Plan:\n1. List files\n2. Read README.md\n3. Replace the word\n4. Run echo ok\n5. Finish\n"
    return reasoning, text, (name, args)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")

    def strata_own(self):
        """Strata's own server: answers what it has, refuses Ollama's and the Manager's routes (MOCK_STRATA=1)."""
        if not os.environ.get("MOCK_STRATA"):
            return False
        path, get = self.path, self.command == "GET"
        if get and path == "/health":
            self.send_json({"status": "ok"})
        elif get and path == "/v1/models":
            self.send_json({"object": "list", "data": [{"id": STRATA_OWN["model"], "object": "model", "owned_by": "strata"}]})
        elif get and path == "/v1/status":
            self.send_json(strata_own_status())
        elif path in ("/v1/load", "/v1/unload") and not get:
            if self.headers.get("Origin"):                 # Strata refuses a foreign page; Studio sends none
                self.send_json({"error": {"message": "only from Strata's own page"}}, 403)
                return True
            if path == "/v1/unload":
                STRATA_OWN.update(loaded=False, unloads=STRATA_OWN["unloads"] + 1)
            else:
                STRATA_OWN.update(loaded=True, loads=STRATA_OWN["loads"] + 1)
            self.send_json({"ok": True, "loaded": STRATA_OWN["loaded"]})
        elif path == "/_mock/strata":
            if not get:
                STRATA_OWN.update(self._req)
            self.send_json(dict(STRATA_OWN))
        elif path == "/v1/chat/completions" and not get:
            if not STRATA_OWN["loaded"]:                     # Strata loads the model on a request by itself (auto_load)
                log = os.environ.get("MOCK_STRATA_LOG")     # the engine's log, which the loading bar reads (LOAD_STEPS)
                for line in ("[strata] reading the model's weights", "[strata] experts loaded: 512", "[strata] almost ready ..."):
                    if log:
                        with open(log, "a") as fh:
                            fh.write(line + "\n")
                    time.sleep(STRATA_OWN["load_s"] / 3)
                STRATA_OWN.update(loaded=True, loads=STRATA_OWN["loads"] + 1)
            STRATA_OWN.update(in_flight=STRATA_OWN["in_flight"] + 1, requests=STRATA_OWN["requests"] + 1)
            try:
                self.chat(self._req)
            finally:
                STRATA_OWN.update(in_flight=STRATA_OWN["in_flight"] - 1, last_request_at=int(time.time()))
        elif (path.startswith("/slots/0?action=") and not get) or path.startswith("/_mock/"):
            return False                                     # the same session files as the Manager's engine, and the mock's own controls
        else:
            self.send_json({"error": {"message": "not found"}}, 404)
        return True

    def openai_only(self):
        """MOCK_OPENAI_ONLY=1 plays llama.cpp, vLLM or LM Studio: no Ollama or Manager API, only /v1/*, and with
        MOCK_API_KEY every request needs that key as a Bearer token."""
        if not os.environ.get("MOCK_OPENAI_ONLY"):
            return False
        key = os.environ.get("MOCK_API_KEY")
        if key and self.headers.get("Authorization") != f"Bearer {key}":
            self.send_json({"error": {"message": "Invalid API Key"}}, 401)
            return True
        if not self.path.startswith("/v1/") and not self.path.startswith("/_mock/"):
            self.send_json({"error": "Not Found"}, 404)
            return True
        if self.command == "GET" and self.path == "/v1/models":
            self.send_json({"object": "list", "data": [{"id": n, "object": "model", "owned_by": "llamacpp"} for n, _ in TAGS]})
            return True
        return False

    def do_GET(self):
        self._req = {}
        if self.strata_own() or self.openai_only():
            return
        if self.path == "/api/strata/status" and ENGINE["on"]:
            self.send_json({"running": ENGINE["loaded"], "health": {"loaded": ENGINE["loaded"]}, "base_url": f"http://127.0.0.1:{ENGINE['port']}",
                            "server_processes": [{"pid": ENGINE["pid"]}] if ENGINE["loaded"] else [], "job": {"started_at": float(ENGINE["pid"])}})
        elif self.path == "/api/strata/status" and CARD["on"]:
            self.send_json({"running": CARD["running"], "lifecycle": {"active_requests": CARD["busy"], **CARD["runtime"]},
                            "subagent_pool": {"mode": CARD["mode"], "pool_mib": CARD["pool_mib"], "keep_ollama": False,
                                              "active": {"pool_mib": CARD["pool_mib"]} if CARD["running"] else {}}})
        elif self.path == "/api/strata/status" and strata_status():
            self.send_json(strata_status())
        elif self.path == "/api/strata/status" and os.environ.get("MOCK_CONTEXT_TOKENS"):
            self.send_json({"last_generation": {"context_tokens": int(os.environ["MOCK_CONTEXT_TOKENS"])}})
        elif self.path == "/api/tags":
            self.send_json({"models": [{"name": n, "model": n, "size": s, "details": {"family": "mock"}}
                                       for n, s in TAGS]})
        elif self.path == "/api/ps":
            self.send_json({"models": []})
        elif self.path == "/_mock/requests":
            with LOCK:
                self.send_json({"requests": LOG})
        elif self.path == "/queue":
            running = [[1, "job-now", {**COMFY_GRAPH, "2": {"inputs": {"text": "a lighthouse at dusk"}}}, {"client_id": "strata-studio"}, []]]
            self.send_json({"queue_running": running if COMFY["busy"] else [], "queue_pending": [[2, "job-next", {}, {}, []]] if COMFY["busy"] else []})
        elif self.path == "/system_stats":
            self.send_json({"system": {"comfyui_version": "0.44.0", "ram_total": 64 * 2 ** 30, "ram_free": 20 * 2 ** 30},
                            "devices": [{"name": "cuda:0 AMD Radeon RX 7900 XTX : native", "vram_total": 24 * 2 ** 30, "vram_free": 9 * 2 ** 30}]})
        elif self.path.startswith("/history/") and not self.path.endswith("/job-done"):
            self.send_json({})
        elif self.path.startswith("/history"):
            self.send_json({"job-done": {"prompt": [0, "job-done", COMFY_GRAPH, {"client_id": "strata-studio"}, ["7"]],
                                         "outputs": {"7": {"images": [{"filename": "studio_00001_.png", "subfolder": "", "type": "output"}]}},
                                         "status": {"status_str": "success", "completed": True,
                                                    "messages": [["execution_start", {"timestamp": 1794300000000}],
                                                                 ["execution_success", {"timestamp": 1794300014000}]]}}})
        elif self.path.startswith("/view?"):
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(PIXEL)))
            self.end_headers()
            self.wfile.write(PIXEL)
        else:
            self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        req = self.body()
        self._req = req
        if self.strata_own() or self.openai_only():
            return
        if self.path == "/api/show":
            name = req.get("model", "")
            caps = ["completion"] if "llama3.2" in name else ["completion", "tools"]
            self.send_json({"capabilities": caps})
        elif self.path == "/v1/chat/completions":
            if ENGINE["on"] and not ENGINE["loaded"]:      # a request loads the engine, as the Manager does
                ENGINE.update(loaded=True, pid=ENGINE["pid"] + 1)
            self.chat(req)
        elif self.path == "/_mock/engine":
            ENGINE.update(req)
            self.send_json(dict(ENGINE))
        elif self.path.startswith("/slots/0?action="):
            action, name = self.path.split("=", 1)[1], req.get("filename")
            if action == "save":
                ENGINE["saves"].append(name)
                ENGINE["files"] = sorted(set(ENGINE["files"]) | {name})
                self.send_json({"id_slot": 0, "filename": name, "n_saved": 120000, "n_written": 2 * 10 ** 9})
            elif name in ENGINE["files"]:
                ENGINE["restores"].append(name)
                self.send_json({"id_slot": 0, "filename": name, "n_restored": 120000, "n_read": 2 * 10 ** 9})
            else:
                self.send_response(400); self.send_header("Content-Length", "0"); self.end_headers()
        elif self.path == "/_mock/card":
            CARD.update(req)
            self.send_json({k: v for k, v in CARD.items()})
        elif self.path == "/api/strata/stop" and CARD["on"]:
            CARD["stops"] = CARD.get("stops", 0) + 1
            CARD["running"] = False
            self.send_json({"ok": True, "job": {"kind": "stop", "state": "stopped"}})
        elif self.path == "/api/strata/runtime_settings":
            CARD["runtime"] = {k: req.get(k) for k in ("auto_start", "idle_timeout_minutes", "startup_script")}
            self.send_json({"ok": True, "settings": CARD["runtime"]})
        elif self.path == "/api/strata/pool":
            CARD["posts"].append({**req, "while_running": CARD["running"]})
            CARD.update(mode=req.get("mode"), pool_mib=req.get("pool_mib", 0))
            self.send_json({"ok": True, "restarted": CARD["running"]})
        elif self.path == "/_mock/comfy":
            COMFY["busy"] = bool(req.get("busy"))
            self.send_json({"ok": True})
        elif self.path == "/_mock/reset_load":
            LOAD.clear()
            self.send_json({"ok": True})
        else:
            self.send_json({"error": "not found"}, 404)

    # -- chat completions -------------------------------------------------
    def chat(self, req):
        messages = req["messages"]
        with LOCK:
            LOG.append({"model": req.get("model"), "stream": bool(req.get("stream")),
                        "max_tokens": req.get("max_tokens"), "tools": bool(req.get("tools")),
                        "tool_names": [t["function"]["name"] for t in req.get("tools") or []],
                        "image_parts": [p["image_url"]["url"][:40] for m in messages if isinstance(m.get("content"), list)
                                        for p in m["content"] if isinstance(p, dict) and p.get("type") == "image_url"],
                        "app": self.headers.get("X-Application-Name"), "n_messages": len(messages),
                        "assistant_texts": [m.get("content") for m in messages if m.get("role") == "assistant" and isinstance(m.get("content"), str)][-3:],
                        "reasoning_effort": req.get("reasoning_effort"),
                        "all_user": "\n".join(str(m.get("content")) for m in messages if m.get("role") == "user"),
                        "full_thinking": self.headers.get("X-Studio-Full-Thinking"),
                        "no_planning_guard": self.headers.get("X-Studio-No-Planning-Guard"),
                        "system": messages[0]["content"] if messages and messages[0]["role"] == "system" else "",
                        "last_user": next((str(m["content"] if isinstance(m["content"], str) else m["content"][0].get("text", ""))
                                           for m in reversed(messages) if m["role"] == "user"), ""),
                        "nudged": any(m["role"] == "user" and str(m["content"]).startswith("You repeated")
                                      for m in messages)})
        if "slow load" in json.dumps(messages[-1:]).lower() and not LOAD:
            LOAD["started"] = time.time()
        while LOAD and time.time() - LOAD["started"] < LOAD_SECONDS:
            time.sleep(0.1)
        reasoning, content, call = plan(req)
        tool_calls = None
        if call:
            tool_calls = [{"id": f"call_{len(messages)}", "type": "function",
                           "function": {"name": call[0], "arguments": json.dumps(call[1])}}]
        usage = {"prompt_tokens": 100 + len(messages), "completion_tokens": 20, "total_tokens": 120}
        if any("full memory" in str(m.get("content")).lower() for m in messages):
            usage["prompt_tokens"] = 900
        if not req.get("stream"):
            msg = {"role": "assistant", "content": content, "reasoning_content": reasoning}
            if tool_calls:
                msg["tool_calls"] = tool_calls
            self.send_json({"choices": [{"index": 0, "message": msg,
                                         "finish_reason": "tool_calls" if call else "stop"}], "usage": usage})
            return
        if "nostream" in req.get("model", ""):
            self.send_json({"error": {"message": "streaming not supported for this model"}}, 400)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()

        def send(delta, finish=None, extra=None):
            obj = {"id": "mock", "object": "chat.completion.chunk",
                   "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
            obj.update(extra or {})
            self.wfile.write(b"data: " + json.dumps(obj).encode() + b"\n\n")
            self.wfile.flush()
            time.sleep(0.005)

        send({"role": "assistant"})
        for i in range(0, len(reasoning), 12):
            send({"reasoning_content": reasoning[i:i + 12]})
        for i in range(0, len(content), 10):
            send({"content": content[i:i + 10]})
        if call:
            args = tool_calls[0]["function"]["arguments"]
            send({"tool_calls": [{"index": 0, "id": tool_calls[0]["id"], "type": "function",
                                  "function": {"name": call[0], "arguments": ""}}]})
            third = max(1, len(args) // 3)
            for i in range(0, len(args), third):
                send({"tool_calls": [{"index": 0, "function": {"arguments": args[i:i + third]}}]})
        send({}, "tool_calls" if call else "stop")
        send({}, None, {"choices": [], "usage": usage})
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8799
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    print(f"mock gateway on http://127.0.0.1:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
