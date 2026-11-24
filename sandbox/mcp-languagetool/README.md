# Greek spelling and grammar checks for Eidoloom Studio's models

> `panel.json` is an example to paste into Account & settings → Tool servers: replace `/path/to/eidoloom-studio` with your Eidoloom Studio folder and `/home/you` with your home folder. Account & settings → Tools & setup adds this server for you, with the right paths.

Set up on 2026-11-09, because every model wrote Greek slips that no tool caught («Διόρθωσεις», «Έλεγουν», `tous`, «epveβαιώθη»).

- **LanguageTool** (open source), container `studio-languagetool` (image `erikvl87/languagetool`, 1.2 GB, Java), reachable only
  from this machine on `127.0.0.1:8010`, restarting with Docker, with up to 1 GB of memory. It supports Greek (`el-GR`).
  Check it: `curl -d 'language=el-GR&text=Διόρθωσεις' http://127.0.0.1:8010/v2/check`.
- **MCP server** `tools/languagetool_mcp.py` (Studio's own, plain Python): one tool, `check_text`, reporting each problem with
  its line, word, kind (spelling, mixed scripts, Latin word that is not English, grammar or style) and suggestions. English
  terms such as "server" are left out, by asking LanguageTool's English check about every Latin-letter word.
  Paste `panel.json` into Studio's Tool servers panel; it can follow auto-run, since it only reads the text it is given.
- Measured on six real slips from that day: it found the four spelling and mixed-script ones and missed the two agreement errors
  («Η κανόνας», «σε 1 δευτερόλεπτα»). It also applies the traditional «δε» before σ rule, so «δεν σώζει» is flagged as style.
- Remove it all: remove the server in the panel, then `docker rm -f studio-languagetool`.

## Grammar and glossary (2026-11-18)

LanguageTool's Greek has no morphology (its dictionary is four test words, three tags and 50 rules), so it could not see
agreement. `tools/greek_grammar.py` adds Studio's own rules on a morphology lexicon, and `check_text` merges both:

- **Lexicon**: every form of Wiktionary's Greek nouns, adjectives and verbs with gender, number, case, person and tense
  (kaikki.org's extraction, CC BY-SA 4.0), built into SQLite (about 550,000 rows, 54 MB) by `tools/greek_lexicon.py`:

      mkdir -p ~/.local/opt/greek && curl -L -o ~/.local/opt/greek/kaikki-greek.jsonl https://kaikki.org/dictionary/Greek/kaikki.org-dictionary-Greek.jsonl
      python3 tools/greek_lexicon.py ~/.local/opt/greek/kaikki-greek.jsonl ~/.local/opt/greek/lexicon.sqlite

  `GREEK_LEXICON` points elsewhere. Without it only the question, punctuation and glossary rules run.
- **Rules**: article, adjective and noun agreement (also ένας/μία/ένα and an adjective after είναι/ήταν), subject and verb
  (also two subjects joined by «και»), θα with the simple past, Που/Πως opening a question and «?», and confusables
  (ψιλός/ψηλός, κλήμα/κλίμα, τείχος/τοίχος). A rule speaks only when every reading of the words disagrees.
- **Severity**: findings come grouped as Fix these (errors), Check these (the final ν, confusables, Latin words) and Notes
  (LanguageTool's style notes).
- **Glossary**: `data/greek/glossary.json`, edited by admins in Account & settings → Greek (with a Try it box); terms to
  avoid with what to use and why, matched in any form, and words that are right (names), which the speller then leaves.
  Studio passes its path to the server as `GREEK_GLOSSARY`.
- **Measured** with `python3 tests/greek/measure.py [--lt]` on `tests/greek/sentences.tsv` (32 sentences with a mistake,
  51 without): Studio's rules alone 30/32 caught (the two misses are spelling, LanguageTool's part) and 0/51 false alarms;
  with LanguageTool 32/32, and its 4 flags on correct sentences are the final ν and style, reported as Check or Notes.
