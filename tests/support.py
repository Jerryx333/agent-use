"""Bounded polling for process fixtures; no process-table commands needed."""

import time


def wait_until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(.02, remaining))
