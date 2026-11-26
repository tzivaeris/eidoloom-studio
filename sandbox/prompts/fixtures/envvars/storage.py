import os

CACHE_DIR = os.getenv("CACHE_DIR", "/var/cache/app")
DEFAULT_MODE = "append"
MAX_SIZE_MB = 200


def save(text):
    path = os.path.join(CACHE_DIR, "log.txt")
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(path, DEFAULT_MODE[0]) as fh:
        fh.write(text + "\n")
