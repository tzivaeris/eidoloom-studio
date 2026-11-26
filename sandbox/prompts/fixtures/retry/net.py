"""Network helpers."""
import time


def retry_with_backoff(fn, max_retries=3, base_delay=0.1, sleep=time.sleep):
    """Call fn(); if it raises, wait and try again.

    fn is called at most max_retries + 1 times (the first call plus max_retries retries).
    The delay doubles after each failure. When the retries are used up, the last
    exception is raised.
    """
    attempt = 0
    while True:
        try:
            return fn()
        except Exception:
            if attempt >= max_retries:
                raise
            sleep(base_delay * (2 ** attempt))
            attempt += 1
