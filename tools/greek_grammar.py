#!/usr/bin/env python3
"""Greek grammar checks that LanguageTool's Greek lacks, and the glossary, for tools/languagetool_mcp.py.

LanguageTool's Greek has no morphology (its dictionary is a few test words), so it cannot see «Η κανόνας» or
«Οι ναυτικοί βλέπει». This module reads the lexicon built by tools/greek_lexicon.py (every form of Wiktionary's Greek
nouns, adjectives and verbs with gender, number, case, person and tense) and checks:
  agreement      article, adjective and noun in one phrase; ένας/μία/ένα with its noun; an adjective after είναι/ήταν
  subject-verb   a nominative phrase (or two joined by «και») and the verb right after it
  tense          θα followed by a past perfective form («θα πήγα» → «θα πάω»)
  question       Που/Πως opening a question («Πού», «Πώς»), and «?» in Greek text («;»)
  confusable     words that are right but often meant as another (ψιλός/ψηλός)
  glossary       the house glossary: terms to avoid with what to use, and words that are right (names, terms)
A rule speaks only when every reading of the words disagrees, so an unknown or ambiguous word stays quiet: a missed
error is better than a false alarm a model will "fix". Standard library only.
"""
import json
import os
import re
import sqlite3
import unicodedata

LEXICON = os.path.expanduser(os.environ.get("GREEK_LEXICON", "~/.local/opt/greek/lexicon.sqlite"))
WORD = re.compile(r"[^\W\d_]+(?:[’'][^\W\d_]+)?", re.UNICODE)
GREEK = re.compile(r"[Ͱ-Ͽἀ-῿]")

# The articles, written out: Wiktionary's article tables mix them up across entries.
G3 = ("m", "f", "n")
ARTICLES = {
    "ο": {("m", "sg", "nom")}, "η": {("f", "sg", "nom")}, "το": {("n", "sg", "nom"), ("n", "sg", "acc")},
    "του": {("m", "sg", "gen"), ("n", "sg", "gen")}, "της": {("f", "sg", "gen")},
    "τον": {("m", "sg", "acc")}, "την": {("f", "sg", "acc")}, "τη": {("f", "sg", "acc")},
    "οι": {("m", "pl", "nom"), ("f", "pl", "nom")}, "τα": {("n", "pl", "nom"), ("n", "pl", "acc")},
    "των": {(g, "pl", "gen") for g in G3}, "τους": {("m", "pl", "acc")}, "τις": {("f", "pl", "acc")},
}
INDEFINITE = {
    "ένας": {("m", "sg", "nom")}, "έναν": {("m", "sg", "acc")},
    "ένα": {("n", "sg", "nom"), ("n", "sg", "acc"), ("m", "sg", "acc")},      # «ένα φάρο» is common speech
    "μια": {("f", "sg", "nom"), ("f", "sg", "acc")}, "μία": {("f", "sg", "nom"), ("f", "sg", "acc")},
    "ενός": {("m", "sg", "gen"), ("n", "sg", "gen")}, "μιας": {("f", "sg", "gen")}, "μίας": {("f", "sg", "gen")},
}
# Weak pronouns and little words that may stand between a subject and its verb.
CLITICS = {"το", "τον", "την", "τη", "τους", "τις", "τα", "του", "της", "μου", "σου", "μας", "σας", "με", "σε", "τον",
           "δεν", "δε", "μην", "μη", "θα", "να", "ας", "ήδη", "πάντα", "ποτέ", "συχνά", "μόλις", "πια", "επίσης", "κιόλας"}
COPULA = {"είναι": None, "ήταν": None, "είμαι": "sg", "είσαι": "sg", "ήμουν": "sg", "ήσουν": "sg", "ήμουνα": "sg",
          "είμαστε": "pl", "είστε": "pl", "είσαστε": "pl", "ήμασταν": "pl", "ήσασταν": "pl", "γίνεται": "sg", "γίνονται": "pl",
          "έγινε": "sg", "έγιναν": "pl", "φαίνεται": "sg", "φαίνονται": "pl", "μένει": "sg", "μένουν": "pl"}
INTENSIFIERS = {"πολύ", "πιο", "τόσο", "αρκετά", "λίγο", "πάρα", "εντελώς", "τελείως", "ιδιαίτερα", "εξαιρετικά", "σχεδόν", "όλο", "πάντα"}
CONFUSABLES = {
    "ψιλός": ("ψηλός", "«ψιλός» means thin or fine (ψιλή βροχή, ψιλά γράμματα); for height it is «ψηλός»."),
    "κλήμα": ("κλίμα", "«κλήμα» is a vine; weather and atmosphere are «κλίμα»."),
    "τείχος": ("τοίχος", "«τείχος» is a city or castle wall; a house wall is «τοίχος»."),
    "λίθος": ("λύθηκε", None),
}
CONFUSABLES.pop("λίθος")
FINITE = {"pres", "impf", "aor", "dep"}


def plain(word):
    """Lower case without accents or diaeresis, for matching the glossary."""
    return "".join(c for c in unicodedata.normalize("NFD", word.lower()) if unicodedata.category(c) != "Mn").replace("ς", "σ")


class Lexicon:
    def __init__(self, path=LEXICON):
        self.path, self.db, self.cache = path, None, {}

    def ready(self):
        if self.db is None and os.path.exists(self.path):
            self.db = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, check_same_thread=False)
        return self.db is not None

    def look(self, word):
        """Every reading of a word: set of (pos, lemma, g, n, c, p, t)."""
        w = word.lower()
        if w not in self.cache:
            rows = set(self.db.execute("SELECT pos, lemma, g, n, c, p, t FROM forms WHERE form = ?", (w,)).fetchall()) if self.ready() else set()
            if len(self.cache) > 20000:
                self.cache.clear()
            self.cache[w] = rows
        return self.cache[w]

    def dependent(self, lemma, p, n):
        rows = self.db.execute("SELECT form FROM forms WHERE lemma = ? AND pos = 'verb' AND t = 'dep' AND p = ? AND n = ? ORDER BY length(form) LIMIT 1", (lemma, p, n)).fetchall()
        return rows[0][0] if rows else None


LEX = Lexicon()


def nominal(readings):
    """The (g, n, c) a word can have as a noun, adjective, name, determiner or numeral; empty fields fit anything."""
    out = set()
    for pos, _lemma, g, n, c, _p, _t in readings:
        if pos in ("noun", "name", "adj", "det", "num", "pron"):
            out.add((g, n, c))
    return out


def fits(a, b):
    return all(x == y or not x or not y for x, y in zip(a, b))


def meet(left, right):
    """The readings of `left` that some reading of `right` fits, made as specific as both say."""
    out = set()
    for a in left:
        for b in right:
            if fits(a, b):
                out.add(tuple(x or y for x, y in zip(a, b)))
    return out


def kinds_of(readings):
    return {r[0] for r in readings}


def say_gnc(f):
    g = {"m": "masculine", "f": "feminine", "n": "neuter"}.get(f[0], "")
    n = {"sg": "singular", "pl": "plural"}.get(f[1], "")
    c = {"nom": "nominative", "gen": "genitive", "acc": "accusative", "voc": "vocative"}.get(f[2], "")
    return " ".join(x for x in (g, n, c) if x)


def tokens(text):
    return [(m.group(0), m.start(), m.end()) for m in WORD.finditer(text)]


def sentences(text):
    """(start, end, ending) of each sentence: ending is the mark that closes it ('.', ';', '?', '!' or '')."""
    out, start = [], 0
    for m in re.finditer(r"[.!;;?·]+|\n\s*\n|\n", text):
        out.append((start, m.start(), m.group(0).strip()[-1:] if m.group(0).strip() else ""))
        start = m.end()
    if start < len(text):
        out.append((start, len(text), ""))
    return out


def finding(kind, severity, start, end, word, message, suggestions=()):
    return {"kind": kind, "severity": severity, "start": start, "end": end, "word": word, "message": message,
            "suggestions": [s for s in suggestions if s]}


def phrase_after(toks, i):
    """The words after a determiner at i that belong to its phrase: adjectives, numerals and the noun, at most 4, ending
    at the noun. Returns (indexes, readings of each)."""
    idx, reads = [], []
    for j in range(i + 1, min(len(toks), i + 5)):
        w = toks[j][0].lower()
        if w in ARTICLES or w in INDEFINITE:
            break
        r = LEX.look(w)
        k = kinds_of(r)
        if not r or "verb" in k or k & {"prep", "conj", "adv", "part", "intj", "contr"} and not k & {"noun", "name", "adj"}:
            break
        idx.append(j)
        reads.append(r)
        if k & {"noun", "name"} and not k & {"adj"}:
            break                          # the noun ends the phrase
    return idx, reads


def check_phrases(toks, text, out):
    """Article or ένας/μία/ένα with the words of its phrase; returns the phrases, for the subject and predicate rules."""
    phrases = []
    for i, (word, s, e) in enumerate(toks):
        w = word.lower()
        table = ARTICLES.get(w) or INDEFINITE.get(w)
        if not table or i + 1 >= len(toks):
            continue
        if text[e:toks[i + 1][1]].strip():
            continue                       # punctuation between them
        nxt = LEX.look(toks[i + 1][0])
        if w in ARTICLES and (not nxt or "verb" in kinds_of(nxt)):
            continue                       # a weak pronoun before its verb («τον είδα», «της έδωσε»)
        # «του», «της», «των», «τους» are also possessives («τα δικά του βιβλία», «τα παιδιά τους»): after a noun, or
        # before a word that cannot be in their case, a mismatch means a possessive, so it is not reported.
        maybe_mine = False
        if w in ("του", "της", "των", "τους"):
            prev = LEX.look(toks[i - 1][0]) if i else set()
            maybe_mine = not any(f[2] == c for f in nominal(nxt) for c in {x[2] for x in table}) or bool(kinds_of(prev) & {"noun", "adj", "name", "det"})
        idx, reads = phrase_after(toks, i)
        if not idx:
            continue
        have, ok = set(table), True
        for j, r in zip(idx, reads):
            forms = nominal(r)
            if not forms:
                ok = False
                break
            both = meet(have, forms)
            if not both and maybe_mine:
                ok = False
                break
            if not both:
                kind = "agreement"
                lead = "ένας/μία/ένα" if w in INDEFINITE else "the article"
                wants = ", ".join(sorted({say_gnc(f) for f in have}))
                is_ = ", ".join(sorted({say_gnc(f) for f in forms}))[:120]
                out.append(finding(kind, "error", s, toks[j][2], text[s:toks[j][2]],
                                   f"«{toks[j][0]}» does not agree with {lead} «{word}» ({wants}); «{toks[j][0]}» is {is_}.",
                                   suggest_phrase(word, toks, idx, reads, j)))
                ok = False
                break
            have = both
        if ok:
            phrases.append({"start": i, "end": idx[-1], "feats": have, "art": w})
    return phrases


def suggest_phrase(article, toks, idx, reads, bad):
    """A likely fix: the article that fits the noun, or the adjective's form that fits the article."""
    noun = next((j for j, r in zip(idx, reads) if kinds_of(r) & {"noun", "name"}), None)
    if noun is None:
        return []
    nf = nominal(reads[idx.index(noun)])
    table = ARTICLES if article.lower() in ARTICLES else INDEFINITE
    fixes = [a for a, fs in table.items() if meet(fs, nf)]
    if article[:1].isupper():
        fixes = [a.capitalize() for a in fixes]
    return [f"{a} … {toks[noun][0]}" for a in fixes[:3]]


def finite_numbers(readings):
    """The numbers a third-person finite verb form can have, or None if the word is not only such a verb."""
    nums, other = set(), False
    for pos, _lemma, _g, n, _c, p, t in readings:
        if pos == "verb" and t in FINITE and p == "3":
            nums.add(n)
        elif pos not in ("verb", "intj"):
            other = True                   # also a noun or adjective: leave it («φτάνει!» as a word of its own is fine)
    return None if other or not nums else nums


def nominative(feats):
    """The nominative readings of a phrase (a neuter one is nominative or accusative), or none."""
    return {f for f in feats if f[2] in ("nom", "")}


def check_subjects(toks, text, phrases, out):
    by_start = {p["start"]: p for p in phrases}
    second = {by_start[p["end"] + 2]["start"] for p in phrases if p["end"] + 2 in by_start and p["end"] + 1 < len(toks) and toks[p["end"] + 1][0].lower() == "και"}
    for p in phrases:
        nom = nominative(p["feats"])
        if not nom or p["start"] in second or p["art"] not in ("ο", "η", "οι", "το", "τα", "ένας", "μια", "μία", "ένα"):
            continue
        j, joined, last = p["end"] + 1, False, p["end"]
        # «Η Ηλέκτρα και ο παππούς της»: two subjects make a plural.
        if j + 1 < len(toks) and toks[j][0].lower() == "και" and j + 1 in by_start and nominative(by_start[j + 1]["feats"]):
            joined, last = True, by_start[j + 1]["end"]
            j = last + 1
        if j < len(toks) and toks[j][0].lower() in ("μου", "σου", "του", "της", "μας", "σας", "τους"):
            j += 1                         # a possessive right after the noun: «ο παππούς της»
        while j < len(toks) and toks[j][0].lower() in ("δεν", "δε", "θα", "μην"):
            j += 1
        if j >= len(toks) or text[toks[j - 1][2]:toks[j][1]].strip(" "):
            continue
        verb = finite_numbers(LEX.look(toks[j][0]))
        if not verb:
            continue
        want = {"pl"} if joined else {f[1] for f in nom}
        if "" in want or len(want) != 1 or want & verb:
            continue
        readings = [r for r in LEX.look(toks[j][0]) if r[0] == "verb"]
        num = next(iter(want))
        fix = LEX.db.execute("SELECT form FROM forms WHERE lemma = ? AND pos = 'verb' AND p = '3' AND n = ? AND t = ? ORDER BY length(form) LIMIT 1",
                             (readings[0][1], num, readings[0][6])).fetchall()
        subject = text[toks[p["start"]][1]:toks[last][2]]
        why = "two subjects joined by «και» take a plural verb" if joined else f"the subject is {'plural' if num == 'pl' else 'singular'}"
        out.append(finding("subject-verb", "error", toks[j][1], toks[j][2], toks[j][0],
                           f"«{toks[j][0]}» does not agree with «{subject}»: {why}.", [fix[0][0]] if fix else []))


def check_predicates(toks, text, phrases, out):
    """«Το φως του φάρου είναι δυνατή»: an adjective after είναι/ήταν agrees with the subject before it."""
    ends = {}
    for p in phrases:
        ends[p["end"]] = p
    for i, (word, s, e) in enumerate(toks):
        if word.lower() not in COPULA:
            continue
        # the subject: the nearest nominative phrase before the verb, past genitive phrases such as «του φάρου»
        k, subj = i - 1, None
        while k >= 0:
            p = ends.get(k)
            if not p:
                break
            if nominative(p["feats"]):
                subj = p
                break
            if all(f[2] == "gen" for f in p["feats"]):
                k = p["start"] - 1
                continue
            break
        if not subj:
            continue
        j = i + 1
        while j < len(toks) and toks[j][0].lower() in INTENSIFIERS:
            j += 1
        if j >= len(toks):
            continue
        r = LEX.look(toks[j][0])
        if not r or kinds_of(r) != {"adj"}:
            continue
        adj = {(g, n, "nom") for g, n, c in nominal(r) if c in ("nom", "")}
        sub = {(g, n, "nom") for g, n, c in nominative(subj["feats"])}
        if not adj or meet(sub, adj):
            continue
        want = sorted({say_gnc(f) for f in sub})
        lemma = next(iter({x[1] for x in r}))
        g, n = next(iter(sub))[:2]
        fix = LEX.db.execute("SELECT form FROM forms WHERE lemma = ? AND pos = 'adj' AND g = ? AND n = ? AND c = 'nom' ORDER BY length(form) LIMIT 1", (lemma, g, n)).fetchall()
        out.append(finding("agreement", "error", toks[j][1], toks[j][2], toks[j][0],
                           f"«{toks[j][0]}» describes «{text[toks[subj['start']][1]:toks[subj['end']][2]]}», which is {', '.join(want)}.",
                           [fix[0][0]] if fix else []))


def check_future(toks, text, out):
    for i, (word, s, e) in enumerate(toks):
        if word.lower() != "θα":
            continue
        j = i + 1
        while j < len(toks) and toks[j][0].lower() in CLITICS - {"θα", "να"}:
            j += 1
        if j >= len(toks):
            continue
        r = LEX.look(toks[j][0])
        if not r or any(x[0] != "verb" or x[6] != "aor" for x in r):
            continue
        lemma, p, n = next(iter((x[1], x[5], x[3]) for x in r))
        dep = LEX.dependent(lemma, p, n)
        out.append(finding("tense", "error", s, toks[j][2], text[s:toks[j][2]],
                           f"«θα» takes the dependent or the present form, not the simple past «{toks[j][0]}».",
                           [f"θα {dep}" if dep else ""]))


def check_questions(text, out):
    for start, end, ending in sentences(text):
        body = text[start:end]
        if not GREEK.search(body):
            continue
        lead = re.match(r"\s*(?:[«\"'(]\s*)?(Που|Πως|που|πως)\b", body)
        if lead and ending in (";", ";", "?"):
            w = lead.group(1)
            fix = {"Που": "Πού", "Πως": "Πώς", "που": "πού", "πως": "πώς"}[w]
            at = start + lead.start(1)
            out.append(finding("question", "error", at, at + len(w), w,
                               f"In a question «{w.lower()}» is written with an accent: «{fix.lower()}» ({"where" if fix.lower() == "πού" else "how"}).", [fix]))
        if ending == "?":
            at = end + text[end:].index("?")
            out.append(finding("punctuation", "error", at, at + 1, "?", "Greek questions end with «;», not «?».", [";"]))


def check_confusables(toks, out):
    for word, s, e in toks:
        for r in LEX.look(word):
            if r[1] in CONFUSABLES:
                other, why = CONFUSABLES[r[1]]
                out.append(finding("confusable", "check", s, e, word, why + f" Make sure «{word}» is what you mean.", []))
                break


# --- glossary ------------------------------------------------------------------------------------------------------

def stem(word):
    """A word without its ending, for matching inflected forms: «δυσλεξικό», «δυσλεξικά» → «δυσλεξικ»."""
    p = plain(word)
    return p[:-2] if len(p) >= 6 else p[:-1] if len(p) >= 4 else p


def glossary_findings(text, glossary):
    out = []
    if not glossary:
        return out
    toks = tokens(text)
    stems = [stem(t[0]) for t in toks]
    for entry in glossary.get("terms") or []:
        use, why = (entry.get("use") or "").strip(), (entry.get("why") or "").strip()
        for phrase in entry.get("avoid") or []:
            want = [stem(w) for w in WORD.findall(phrase)]
            if not want:
                continue
            for i in range(len(toks) - len(want) + 1):
                if stems[i:i + len(want)] == want:
                    s, e = toks[i][1], toks[i + len(want) - 1][2]
                    msg = f"The glossary says to avoid «{phrase}»" + (f": use «{use}»." if use else ".") + (f" {why}" if why else "")
                    out.append(finding("glossary", "error", s, e, text[s:e], msg, [use] if use else []))
    return out


def accepted(word, glossary):
    """A word the glossary lists as right (a name, a term), in any of its forms: Ηλέκτρα also covers Ηλέκτρας, not
    Ηλεκτρισμός."""
    p = plain(word)
    for w in (glossary or {}).get("accept") or []:
        base = plain(w)
        core = base[:-1] if base.endswith("σ") else base      # the ending: λογοθεραπευτής → λογοθεραπευτ
        while len(core) > 3 and core[-1] in "αεηιουω" and len(base) - len(core) < 3:
            core = core[:-1]
        if p == base or (p.startswith(core) and len(p) - len(core) <= 3 and len(core) >= 3):
            return True
    return False


def load_glossary(path):
    try:
        with open(os.path.expanduser(path), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def check(text, glossary=None):
    """Every grammar and glossary finding in text, in order. Without the lexicon only the question, punctuation and
    glossary rules run."""
    out, toks = [], tokens(text)
    if LEX.ready():
        phrases = check_phrases(toks, text, out)
        check_subjects(toks, text, phrases, out)
        check_predicates(toks, text, phrases, out)
        check_future(toks, text, out)
        check_confusables(toks, out)
    check_questions(text, out)
    out += glossary_findings(text, glossary)
    seen, unique = set(), []
    for f in sorted(out, key=lambda f: (f["start"], f["kind"])):
        if (f["start"], f["end"], f["kind"]) not in seen:
            seen.add((f["start"], f["end"], f["kind"]))
            unique.append(f)
    return unique


if __name__ == "__main__":
    import sys
    for line in sys.stdin:
        for f in check(line.rstrip("\n")):
            print(f"{f['severity']:5} {f['kind']:12} «{f['word']}» {f['message']} {f['suggestions']}")
