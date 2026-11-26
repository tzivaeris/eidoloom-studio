import os

POOL_SIZE = int(os.environ.get("POOL_SIZE", 8))
DEBUG = os.environ.get("APP_DEBUG", "0") == "1"
LOG_FILE = "/tmp/worker.log"


def run():
    if DEBUG:
        print("debug on")
    return POOL_SIZE
