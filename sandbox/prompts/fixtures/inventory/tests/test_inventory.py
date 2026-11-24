import sys
import threading
import unittest

from inventory import Stock


class InventoryTests(unittest.TestCase):
    def test_reserve(self):
        stock = Stock(2)
        self.assertTrue(stock.reserve(2))
        self.assertFalse(stock.reserve(1))
        self.assertEqual(stock.available(), 0)

    def test_no_overselling(self):
        sys.setswitchinterval(1e-6)
        self.addCleanup(sys.setswitchinterval, 0.005)
        stock = Stock(5)
        results = []
        barrier = threading.Barrier(16)

        def buy():
            barrier.wait()
            results.append(stock.reserve(1))

        threads = [threading.Thread(target=buy) for _ in range(16)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(results.count(True), 5)
        self.assertEqual(stock.available(), 0)


if __name__ == "__main__":
    unittest.main()
