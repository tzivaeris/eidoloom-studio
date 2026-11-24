#!/usr/bin/env python3
"""An MCP server (stdio) with one tool, check_text, that checks Greek (or other) text with a LanguageTool server.

It talks only to the LanguageTool at LANGUAGETOOL_URL (default http://127.0.0.1:8010), so text never leaves the machine.
Studio's models use it to check page text and replies before showing them; it catches misspellings and words mixing
Greek and Latin letters well, some grammar, and misses agreement errors such as «Η κανόνας».
"""
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import greek_grammar  # noqa: E402

LANGUAGETOOL_URL = os.environ.get("LANGUAGETOOL_URL", "http://127.0.0.1:8010").rstrip("/")
PROTOCOL = "2025-06-18"
MAX_TEXT = 50000
GLOSSARY = os.environ.get("GREEK_GLOSSARY", "")
SEVERITY_ORDER = {"error": 0, "check": 1, "note": 2}
SEVERITY_HEAD = {"error": "Fix these", "check": "Check these (may be right)", "note": "Notes (style, optional)"}
GREEK, LATIN = re.compile(r"[Ͱ-Ͽἀ-῿]"), re.compile(r"[A-Za-z]")
TOOL = {
    "name": "check_text",
    "description": "Check text for spelling and grammar with a local LanguageTool, by default as Greek. Use it on Greek text "
                   "you write for the user (page text, messages, replies) before you show it. It reports misspelled words, "
                   "words mixing Greek and Latin letters, Latin-letter words that are not English (such as Greeklish), and grammar "
                   "or style notes, with suggestions. English terms such as \"server\" are not reported unless asked for. "
                   "It also checks Greek grammar with a morphology lexicon: article, adjective and noun agreement, subject and "
                   "verb, θα with the simple past, πού/πώς in questions and «;», and the house glossary (terms to avoid, "
                   "names that are right). Findings come in three groups: Fix these (real mistakes), Check these (may be "
                   "right, such as the final ν or a confusable word) and Notes (style). Fix the first group; keep the "
                   "others only with a reason.",
    "inputSchema": {"type": "object", "properties": {
        "text": {"type": "string", "description": "The text to check, at most 50000 characters"},
        "language": {"type": "string", "description": "Language code, default el-GR; en-US, de-DE and others also work"},
        "include_latin_words": {"type": "boolean", "description": "Also list words written only in Latin letters, such as "
                                "English terms; default false, since they are usually intended"}},
        "required": ["text"]},
}


def kind_of(word, match):
    """What sort of problem a LanguageTool match is, so the model can tell a mistake from an English term."""
    if GREEK.search(word) and LATIN.search(word):
        return "mixed scripts"
    if match["rule"]["id"].startswith("MORFOLOGIK"):
        return "latin word" if LATIN.search(word) and not GREEK.search(word) else "spelling"
    return "grammar or style"


def lt_severity(match, kind):
    rule, issue = match["rule"].get("id", ""), match["rule"].get("issueType", "")
    if kind == "mixed scripts" or issue in ("misspelling", "duplication") or kind.startswith("latin word, not"):
        return "error"
    if "EXTRA_N" in rule or "MISSING_N" in rule or kind == "latin word":
        return "check"                    # the school rule for the final ν; many writers keep «δεν» before every word
    if issue == "style" or (match["rule"].get("category") or {}).get("id") in ("STYLE", "TYPOGRAPHY", "REDUNDANCY"):
        return "note"
    return "error"


NOUN_ENDINGS = ("ας", "ης", "ες", "ος", "ου", "ων", "ους", "ής", "ές", "ός", "ού", "ών", "ούς", "άς", "ία", "ίας", "ιών",
                "α", "η", "ε", "ο", "ι", "ά", "ή", "έ", "ό", "ί")
BASE_ENDINGS = ("α", "η", "ος", "ης", "ας", "ο", "ι", "ά", "ή", "ός", "ής", "ό", "ί", "ία", "ας", "ής", "ές")


def other_forms(word):
    """The same stem with a dictionary form's ending: λογοθεραπευτή → λογοθεραπευτής, λογοθεραπείας → λογοθεραπεία."""
    out = set()
    for end in NOUN_ENDINGS:
        if word.endswith(end) and len(word) - len(end) >= 4:
            stem = word[:-len(end)]
            out.update(stem + e for e in BASE_ENDINGS)
    out.discard(word)
    return out


def known_inflections(words):
    """The Greek words LanguageTool calls misspelled that are probably forms its dictionary lacks: another ending on
    the same stem is a word it knows. One request for all of them."""
    tries = {w: other_forms(w) for w in words}
    every = sorted({f for fs in tries.values() for f in fs})
    if not every:
        return {}
    text = "\n".join(every)
    data = urllib.parse.urlencode({"text": text, "language": "el-GR"}).encode()
    try:
        with urllib.request.urlopen(LANGUAGETOOL_URL + "/v2/check", data, timeout=60) as resp:
            matches = json.load(resp).get("matches", [])
    except (urllib.error.URLError, OSError, ValueError):
        return {}
    unknown = {text[m["offset"]:m["offset"] + m["length"]] for m in matches if m["rule"].get("id", "").startswith("MORFOLOGIK")}
    out = {}
    for w, forms in tries.items():
        good = sorted(f for f in forms if f not in unknown)
        if good:
            out[w] = good[0]
    return out


def not_english(words):
    """The Latin-letter words that are not English either, such as Greeklish «tous»; "server" and other terms pass."""
    if not words:
        return set()
    data = urllib.parse.urlencode({"text": "\n".join(sorted(words)), "language": "en-US"}).encode()
    try:
        with urllib.request.urlopen(LANGUAGETOOL_URL + "/v2/check", data, timeout=60) as resp:
            matches = json.load(resp).get("matches", [])
    except (urllib.error.URLError, OSError, ValueError):
        return set(words)
    text = "\n".join(sorted(words))
    return {text[m["offset"]:m["offset"] + m["length"]] for m in matches if m["rule"]["id"].startswith("MORFOLOGIK")}


def position(text, offset):
    line = text.count("\n", 0, offset) + 1
    return line, offset - (text.rfind("\n", 0, offset) + 1) + 1


def check_text(args):
    text = args.get("text")
    if not isinstance(text, str) or not text.strip():
        return "Error: text is required.", True
    if len(text) > MAX_TEXT:
        return f"Error: text is longer than {MAX_TEXT} characters; check it in parts.", True
    language = args.get("language") or "el-GR"
    data = urllib.parse.urlencode({"text": text, "language": language}).encode()
    try:
        with urllib.request.urlopen(LANGUAGETOOL_URL + "/v2/check", data, timeout=60) as resp:
            result = json.load(resp)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return f"Error: LanguageTool at {LANGUAGETOOL_URL} did not answer ({exc}).", True
    glossary = greek_grammar.load_glossary(GLOSSARY) if GLOSSARY else {}
    matches = result.get("matches", [])
    latin = {text[m["offset"]:m["offset"] + m["length"]] for m in matches if kind_of(text[m["offset"]:m["offset"] + m["length"]], m) == "latin word"}
    foreign = not_english(latin) if language.startswith("el") else set()
    found = []
    for match in matches:
        word = text[match["offset"]:match["offset"] + match["length"]]
        kind = kind_of(word, match)
        if kind == "latin word":
            if word in foreign:
                kind = "latin word, not English"
            elif not args.get("include_latin_words"):
                continue
        if kind in ("spelling", "latin word", "latin word, not English") and greek_grammar.accepted(word.strip("?;.,!·"), glossary):
            continue                      # a name or term the glossary lists as right
        if kind == "spelling" and language.startswith("el") and greek_grammar.LEX.ready() and greek_grammar.LEX.look(word.strip("?;.,!·")):
            continue                      # a form Wiktionary knows: LanguageTool's dictionary is smaller
        suggestions = [r["value"] for r in match.get("replacements", [])[:4]]
        found.append({"start": match["offset"], "word": word, "kind": kind, "severity": lt_severity(match, kind),
                      "message": match.get("message", "").strip(), "suggestions": suggestions})
    greek = language.startswith("el")
    unknown = {f["word"] for f in found if f["kind"] == "spelling" and greek_grammar.GREEK.search(f["word"]) and not LATIN.search(f["word"])}
    for f in found:
        base = known_inflections(unknown).get(f["word"]) if f["kind"] == "spelling" and f["word"] in unknown else None
        if base:
            f.update(severity="check", message=f"LanguageTool does not know this form, but it knows «{base}»: probably a correct form of it. Keep it if it reads right.")
    taken = {f["start"] for f in found}
    for f in greek_grammar.check(text, glossary) if greek else []:
        if f["start"] not in taken or f["kind"] in ("agreement", "subject-verb", "tense", "glossary"):
            found.append(f)
    tools = "LanguageTool"
    if greek:
        tools += " and Studio's Greek grammar" + ("" if greek_grammar.LEX.ready() else " (no lexicon: only questions, punctuation and glossary)")
    checked = f"{tools}, {language}, {len(text)} characters" + (f", glossary of {len(glossary.get('terms') or [])} terms" if glossary else "")
    if not found:
        return f"No issues found ({checked}).", False
    found.sort(key=lambda f: (SEVERITY_ORDER[f["severity"]], f["start"]))
    lines, group = [], None
    for n, f in enumerate(found, 1):
        if f["severity"] != group:
            group = f["severity"]
            lines.append(f"{SEVERITY_HEAD[group]}:")
        line, col = position(text, f["start"])
        sug = ", ".join(f["suggestions"]) or "none"
        lines.append(f"{n}. line {line}, column {col}: «{f['word']}» ({f['kind']}) {f['message']} Suggestions: {sug}")
    counts = ", ".join(f"{sum(1 for f in found if f['severity'] == s)} {s}" for s in ("error", "check", "note") if any(f["severity"] == s for f in found))
    return f"{len(found)} issue{'s' if len(found) != 1 else ''}: {counts} ({checked}):\n" + "\n".join(lines), False


def answer(msg_id, result=None, error=None):
    reply = {"jsonrpc": "2.0", "id": msg_id}
    reply.update({"error": error} if error else {"result": result})
    sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        method, msg_id = msg.get("method"), msg.get("id")
        if msg_id is None:
            continue                      # notifications need no answer
        if method == "initialize":
            answer(msg_id, {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}},
                            "serverInfo": {"name": "languagetool-local", "version": "1.0"}})
        elif method == "ping":
            answer(msg_id, {})
        elif method == "tools/list":
            answer(msg_id, {"tools": [TOOL]})
        elif method == "tools/call":
            params = msg.get("params") or {}
            if params.get("name") != TOOL["name"]:
                answer(msg_id, error={"code": -32602, "message": f"unknown tool {params.get('name')!r}"})
                continue
            text, failed = check_text(params.get("arguments") or {})
            answer(msg_id, {"content": [{"type": "text", "text": text}], "isError": failed})
        else:
            answer(msg_id, error={"code": -32601, "message": f"unknown method {method!r}"})


if __name__ == "__main__":
    main()
