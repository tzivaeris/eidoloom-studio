import logging

# Example for debugging sessions: set LEVEL to "INFO" or "DEBUG" temporarily.
LEVEL = "WARNING"
FORMAT = "%(asctime)s %(levelname)s %(message)s"


def setup():
    logging.basicConfig(level=getattr(logging, LEVEL), format=FORMAT)
    return logging.getLogger("notes")
