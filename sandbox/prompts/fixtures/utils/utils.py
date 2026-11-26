"""Helper functions used across the project."""
import hashlib
import os
import re
import tempfile


# ---- text ----

def slugify(text):
    """Lower-case text with dashes instead of anything that is not a letter or digit."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def truncate(text, limit):
    """Cut text to at most limit characters, ending with '...' when it was cut."""
    if len(text) <= limit:
        return text
    return text[: max(limit - 3, 0)] + "..."


def title_case(text):
    """Capitalise every word and lower-case the rest of it."""
    return " ".join(word.capitalize() for word in text.split())


# ---- numbers ----

def clamp(value, low, high):
    """Keep value between low and high."""
    return max(low, min(high, value))


def percent(part, whole):
    """Part as a percentage of whole, rounded to one decimal; 0 when whole is 0."""
    if whole == 0:
        return 0.0
    return round(part * 100 / whole, 1)


def round_half_up(value):
    """Round to the nearest whole number, halves going up (2.5 becomes 3)."""
    return int(value + 0.5) if value >= 0 else -int(-value + 0.5)


# ---- files ----

def read_lines(path):
    """The lines of a text file without their line endings."""
    with open(path, encoding="utf-8") as handle:
        return handle.read().splitlines()


def write_atomic(path, text):
    """Write text so that readers never see a half-written file."""
    folder = os.path.dirname(os.path.abspath(path))
    fd, temp = tempfile.mkstemp(dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temp, path)
    except BaseException:
        if os.path.exists(temp):
            os.remove(temp)
        raise


def file_hash(path):
    """The SHA-256 of a file's bytes as hex."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()
