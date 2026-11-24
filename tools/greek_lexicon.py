#!/usr/bin/env python3
"""Builds the Greek morphology lexicon that tools/greek_grammar.py reads.

Source: kaikki.org's extraction of English Wiktionary's Greek entries (CC BY-SA 4.0, as Wiktionary), one JSON entry a line:
  curl -L -o ~/.local/opt/greek/kaikki-greek.jsonl https://kaikki.org/dictionary/Greek/kaikki.org-dictionary-Greek.jsonl
  python3 tools/greek_lexicon.py ~/.local/opt/greek/kaikki-greek.jsonl ~/.local/opt/greek/lexicon.sqlite

The lexicon is one table, forms(form, pos, lemma, g, n, c, p, t): every written form in lower case with what it can be.
g gender (m, f, n), n number (sg, pl), c case (nom, gen, acc, voc), p person (1, 2, 3), t for verbs: pres, impf (past
imperfective), aor (past perfective), dep (the dependent form after θα/να), imp, part. Empty means not said.
Standard library only.
"""
import json
import os
import sqlite3
import sys

POS = {"noun": "noun", "name": "name", "adj": "adj", "verb": "verb", "article": "art", "det": "det", "pron": "pron",
       "num": "num", "adv": "adv", "prep": "prep", "conj": "conj", "particle": "part", "intj": "intj", "contraction": "contr"}
GENDER = {"masculine": "m", "feminine": "f", "neuter": "n"}
NUMBER = {"singular": "sg", "plural": "pl"}
CASE = {"nominative": "nom", "genitive": "gen", "accusative": "acc", "vocative": "voc"}
PERSON = {"first-person": "1", "second-person": "2", "third-person": "3"}
SKIP_TAGS = {"romanization", "table-tags", "inflection-template", "alternative", "canonical", "obsolete", "archaic",
             "dialectal", "rare", "Katharevousa", "Ancient", "Byzantine", "Medieval", "comparative", "superlative",
             "diminutive", "augmentative", "colloquial", "poetic", "informal"}


def head_genders(entry):
    """A noun's genders from its head templates: el-noun's first argument (m, f, n, m-p…) or head's g, g2, g3."""
    out = set()
    for h in entry.get("head_templates") or []:
        args = h.get("args") or {}
        vals = [args.get("g"), args.get("g2"), args.get("g3")]
        if h.get("name", "").startswith("el-noun") or h.get("name", "").startswith("el-proper"):
            vals.append(args.get("1"))
        for v in vals:
            if isinstance(v, str) and v[:1] in "mfn":
                out.add(v[:1])
    return out


def verb_tense(tags):
    if "participle" in tags:
        return "part"
    if "imperative" in tags:
        return "imp"
    if "dependent" in tags:
        return "dep"
    if "imperfect" in tags:
        return "impf"
    if "past" in tags and "perfective" in tags and "indicative" in tags:
        return "aor"
    if "present" in tags:
        return "pres"
    return ""


def form_of(entry):
    """An entry that only points at another word ("plural of παιδί"): its forms are in that word's table."""
    senses = entry.get("senses") or []
    return bool(senses) and all(s.get("form_of") or "form-of" in (s.get("tags") or []) for s in senses)


def rows_for(entry, lemma_genders):
    pos = POS.get(entry.get("pos"))
    word = (entry.get("word") or "").strip()
    if not pos or not word or " " in word or form_of(entry):
        return
    genders = (head_genders(entry) or lemma_genders.get(word, set())) if pos in ("noun", "name") else set()
    seen_lemma = False
    for form in entry.get("forms") or []:
        text, tags = (form.get("form") or "").strip(), set(form.get("tags") or [])
        if not text or " " in text or tags & SKIP_TAGS or text.startswith("-"):
            continue
        if pos == "verb":
            t = verb_tense(tags)
            if not t or "future" in tags:
                continue
            n = next((NUMBER[x] for x in tags if x in NUMBER), "")
            p = next((PERSON[x] for x in tags if x in PERSON), "")
            yield text, pos, word, "", n, "", p, t
            continue
        c = next((CASE[x] for x in tags if x in CASE), "")
        n = next((NUMBER[x] for x in tags if x in NUMBER), "")
        gs = {GENDER[x] for x in tags if x in GENDER} or genders or {""}
        if not c and not n and pos in ("noun", "name", "adj"):
            continue                  # a bare "plural" or "feminine" line repeats the table below it
        if pos == "pron":
            p = next((PERSON[x] for x in tags if x in PERSON), "")
        else:
            p = ""
        for g in gs:
            yield text, pos, word, g, n, c, p, ""
        if text == word:
            seen_lemma = True
    if not seen_lemma:
        if pos in ("noun", "name"):
            for g in genders or {""}:
                yield word, pos, word, g, "sg" if pos == "noun" else "", "nom", "", ""
        elif pos == "verb":
            yield word, pos, word, "", "", "", "", ""     # a verb entry without a table: person and tense unknown
        elif pos not in ("adj",):
            yield word, pos, word, "", "", "", "", ""


def build(src, out):
    tmp = out + ".part"
    if os.path.exists(tmp):
        os.remove(tmp)
    db = sqlite3.connect(tmp)
    db.execute("CREATE TABLE forms (form TEXT, pos TEXT, lemma TEXT, g TEXT, n TEXT, c TEXT, p TEXT, t TEXT)")
    batch, count, lemma_genders = set(), 0, {}
    with open(src, encoding="utf-8") as f:         # first pass: each noun's genders, from any of its entries
        for line in f:
            if '"pos": "noun"' in line or '"pos": "name"' in line:
                entry = json.loads(line)
                lemma_genders.setdefault(entry.get("word"), set()).update(head_genders(entry))
    with open(src, encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            for row in rows_for(entry, lemma_genders):
                batch.add((row[0].lower(),) + row[1:])
            if len(batch) > 50000:
                db.executemany("INSERT INTO forms VALUES (?,?,?,?,?,?,?,?)", batch)
                count += len(batch)
                batch.clear()
    db.executemany("INSERT INTO forms VALUES (?,?,?,?,?,?,?,?)", batch)
    count += len(batch)
    db.execute("CREATE INDEX forms_form ON forms(form)")
    # A genderless row of a noun that has gendered rows for the same form says nothing more.
    db.execute("""DELETE FROM forms WHERE g = '' AND pos IN ('noun', 'name') AND EXISTS (SELECT 1 FROM forms f2
                  WHERE f2.form = forms.form AND f2.lemma = forms.lemma AND f2.pos = forms.pos AND f2.g != '')""")
    db.execute("CREATE TABLE meta (key TEXT, value TEXT)")
    db.execute("INSERT INTO meta VALUES ('source', ?), ('licence', 'CC BY-SA 4.0 (Wiktionary, via kaikki.org)')", (os.path.basename(src),))
    db.commit()
    db.execute("VACUUM")
    db.close()
    os.replace(tmp, out)
    return count


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: greek_lexicon.py <kaikki-greek.jsonl> <lexicon.sqlite>")
    n = build(os.path.expanduser(sys.argv[1]), os.path.expanduser(sys.argv[2]))
    print(f"{n} rows in {sys.argv[2]}")
