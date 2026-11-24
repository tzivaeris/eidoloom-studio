import os
import threading


class Stock:
    def __init__(self, quantity, audit_path=os.devnull):
        self._quantity = quantity
        self._lock = threading.Lock()
        self._audit = open(audit_path, "a", encoding="utf-8")

    def available(self):
        with self._lock:
            return self._quantity

    def _take(self, amount):
        with self._lock:
            self._quantity -= amount

    def reserve(self, amount):
        """Reserve stock if enough is left. Returns True if the reservation succeeded."""
        if self.available() >= amount:
            self._audit.write(f"reserve {amount}\n")
            self._audit.flush()
            self._take(amount)
            return True
        return False
