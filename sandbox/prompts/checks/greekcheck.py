"""Small helpers for the Greek checks."""
import re

GREEK = re.compile(r"[Ͱ-Ͽἀ-῿]")
LATIN = re.compile(r"[A-Za-z]")
CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]")


def prose(text):
    """Text without fenced code, inline code spans, links' targets, paths and identifiers."""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"`[^`]*`", " ", text)
    text = re.sub(r"\]\([^)]*\)", "] ", text)
    text = re.sub(r"\S*[/\\_.]\S*[A-Za-z]\S*", lambda m: " " if re.search(r"[A-Za-z]", m.group(0)) and not GREEK.search(m.group(0)) else m.group(0), text)
    return text


def greek_share(text):
    text = prose(text)
    greek = len(GREEK.findall(text))
    latin = len(LATIN.findall(text))
    return greek / (greek + latin) if greek + latin else 0.0


def has_cjk(text):
    return bool(CJK.search(text))


def mixed_words(text):
    found = []
    for word in re.findall(r"[^\W\d_]+", prose(text)):
        if len(word) >= 4 and GREEK.search(word) and LATIN.search(word):
            found.append(word)
    return found
