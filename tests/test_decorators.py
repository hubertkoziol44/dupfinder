"""Testy dekoratorów funkcji i metod."""

import unittest
from unittest import mock

from dupfinder.utils.decorators import measure_time, requires_result, retry, stage


class MeasureTimeTest(unittest.TestCase):
    def test_returns_value_and_keeps_metadata(self):
        @measure_time
        def add(a, b):
            """Dodaje dwie liczby."""
            return a + b

        self.assertEqual(add(2, 3), 5)
        self.assertEqual(add.__name__, "add")
        self.assertEqual(add.__doc__, "Dodaje dwie liczby.")
        self.assertGreaterEqual(add.last_elapsed, 0.0)

    def test_measures_even_when_exception_raised(self):
        @measure_time
        def boom():
            raise ValueError("x")

        with self.assertRaises(ValueError):
            boom()
        self.assertGreaterEqual(boom.last_elapsed, 0.0)


class RetryTest(unittest.TestCase):
    def test_succeeds_after_transient_errors(self):
        calls = []

        @retry(attempts=3, delay=0, exceptions=(OSError,))
        def flaky():
            calls.append(1)
            if len(calls) < 3:
                raise OSError("chwilowy błąd")
            return "ok"

        self.assertEqual(flaky(), "ok")
        self.assertEqual(len(calls), 3)

    def test_raises_last_exception_after_limit(self):
        calls = []

        @retry(attempts=2, delay=0, exceptions=(OSError,))
        def always_fails():
            calls.append(1)
            raise OSError(f"błąd {len(calls)}")

        with self.assertRaisesRegex(OSError, "błąd 2"):
            always_fails()
        self.assertEqual(len(calls), 2)

    def test_reraise_is_not_retried(self):
        calls = []

        @retry(attempts=5, delay=0, exceptions=(OSError,), reraise=(FileNotFoundError,))
        def missing():
            calls.append(1)
            raise FileNotFoundError("brak pliku")

        with self.assertRaises(FileNotFoundError):
            missing()
        self.assertEqual(len(calls), 1)

    def test_other_exceptions_are_not_retried(self):
        calls = []

        @retry(attempts=3, delay=0, exceptions=(OSError,))
        def bad_value():
            calls.append(1)
            raise ValueError("zła wartość")

        with self.assertRaises(ValueError):
            bad_value()
        self.assertEqual(len(calls), 1)

    def test_backoff_delays(self):
        @retry(attempts=3, delay=0.5, backoff=2, exceptions=(OSError,))
        def fails():
            raise OSError("x")

        with mock.patch("dupfinder.utils.decorators.time.sleep") as sleep:
            with self.assertRaises(OSError):
                fails()
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [0.5, 1.0])

    def test_invalid_parameters(self):
        with self.assertRaises(ValueError):
            retry(attempts=0)
        with self.assertRaises(ValueError):
            retry(delay=-1)


class MethodDecoratorsTest(unittest.TestCase):
    class Worker:
        def __init__(self):
            self.timings = {}
            self._result = None

        @stage("liczenie")
        def compute(self, x):
            self._result = x * 2
            return self._result

        @requires_result
        def show(self):
            return f"wynik={self._result}"

    def test_stage_records_time_in_instance(self):
        w = self.Worker()
        self.assertEqual(w.compute(21), 42)
        self.assertIn("liczenie", w.timings)
        self.assertEqual(self.Worker.compute.__name__, "compute")

    def test_requires_result_blocks_until_result_exists(self):
        w = self.Worker()
        with self.assertRaisesRegex(RuntimeError, "najpierw wywołaj scan"):
            w.show()
        w.compute(1)
        self.assertEqual(w.show(), "wynik=2")


if __name__ == "__main__":
    unittest.main()
