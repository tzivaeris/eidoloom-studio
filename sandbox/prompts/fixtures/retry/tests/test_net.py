import unittest

from net import retry_with_backoff


class Flaky:
    def __init__(self, failures):
        self.failures = failures
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.calls <= self.failures:
            raise ConnectionError("down")
        return "ok"


class RetryTests(unittest.TestCase):
    def test_succeeds_after_failures(self):
        fn = Flaky(2)
        self.assertEqual(retry_with_backoff(fn, sleep=lambda s: None), "ok")
        self.assertEqual(fn.calls, 3)

    def test_gives_up_after_max_retries(self):
        fn = Flaky(100)
        with self.assertRaises(ConnectionError):
            retry_with_backoff(fn, max_retries=3, sleep=lambda s: None)
        self.assertEqual(fn.calls, 4)

    def test_delays_double(self):
        delays = []
        fn = Flaky(3)
        retry_with_backoff(fn, max_retries=3, base_delay=0.5, sleep=delays.append)
        self.assertEqual(delays, [0.5, 1.0, 2.0])

    def test_zero_retries(self):
        fn = Flaky(1)
        with self.assertRaises(ConnectionError):
            retry_with_backoff(fn, max_retries=0, sleep=lambda s: None)
        self.assertEqual(fn.calls, 1)


if __name__ == "__main__":
    unittest.main()
