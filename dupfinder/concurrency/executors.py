"""Wykonywanie zadań sekwencyjnie, w wątkach lub w procesach (zagadnienia 6, 7, 8).

Wszystkie trzy tryby mają ten sam interfejs (:func:`hash_many`), dzięki czemu
benchmark GIL porównuje dokładnie ten sam kod uruchamiany na trzy sposoby.

* ``threads``   – ``ThreadPoolExecutor``; wątki współdzielą pamięć, więc wspólny
                  licznik postępu jest chroniony blokadą (:class:`ProgressTracker`).
* ``processes`` – ``ProcessPoolExecutor``; każdy proces ma własny interpreter
                  i własny GIL. Zadanie i wyniki są serializowane (pickle), dlatego
                  funkcje zadań muszą być zdefiniowane na poziomie modułu.
"""

from __future__ import annotations

import functools
import logging
import os
import sys
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from types import TracebackType
from typing import NamedTuple, TextIO

from dupfinder.core.models import FileEntry
from dupfinder.utils.decorators import measure_time
from dupfinder.utils.formatting import human_size

logger = logging.getLogger(__name__)

MODES = ("sequential", "threads", "processes")

Job = Callable[[FileEntry], str]


class HashOutcome(NamedTuple):
    """Wynik zadania dla jednego pliku: skrót albo opis błędu."""

    entry: FileEntry
    digest: str | None
    error: str | None


# ---------------------------------------------------------------------------
# Synchronizacja wątków
# ---------------------------------------------------------------------------

class ProgressTracker:
    """Licznik postępu współdzielony przez wiele wątków.

    Operacja ``self._files += 1`` to kilka instrukcji kodu bajtowego, między
    którymi interpreter może przełączyć wątek – bez blokady część aktualizacji
    by przepadła (wyścig). GIL przed tym NIE chroni, dlatego używamy ``Lock``.
    """

    def __init__(self, total_files: int = 0, total_bytes: int = 0) -> None:
        self.total_files = total_files
        self.total_bytes = total_bytes
        self._lock = threading.Lock()
        self._files = 0
        self._bytes = 0
        self._errors = 0

    def update(self, num_bytes: int, *, error: bool = False) -> None:
        """Rejestruje przetworzenie jednego pliku (bezpieczne wątkowo)."""
        with self._lock:  # sekcja krytyczna
            self._files += 1
            self._bytes += num_bytes
            if error:
                self._errors += 1

    def snapshot(self) -> tuple[int, int, int]:
        """Spójny odczyt stanu: (pliki, bajty, błędy)."""
        with self._lock:
            return self._files, self._bytes, self._errors


class ProgressReporter:
    """Wątek w tle, który co ``interval`` sekund wypisuje stan :class:`ProgressTracker`.

    Zatrzymywany jest przez ``threading.Event`` – ``Event.wait(timeout)`` czeka
    na sygnał albo upływ czasu, więc wątek kończy się natychmiast po ``set()``.
    """

    def __init__(self, tracker: ProgressTracker, label: str,
                 interval: float = 0.5, stream: TextIO | None = None) -> None:
        self._tracker = tracker
        self._label = label
        self._interval = interval
        self._stream = stream or sys.stderr
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="progress", daemon=True)

    def _line(self) -> str:
        files, num_bytes, errors = self._tracker.snapshot()
        total = self._tracker.total_files or 1
        text = (f"{self._label}: {files}/{self._tracker.total_files} plików "
                f"({100 * files / total:.0f}%), {human_size(num_bytes)}")
        return text + (f", błędy: {errors}" if errors else "")

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            print("\r" + self._line(), end="", file=self._stream, flush=True)

    def __enter__(self) -> ProgressReporter:
        self._thread.start()
        return self

    def __exit__(self, exc_type: type[BaseException] | None,
                 exc: BaseException | None, tb: TracebackType | None) -> None:
        self._stop.set()
        self._thread.join()
        print("\r" + self._line(), file=self._stream, flush=True)


# ---------------------------------------------------------------------------
# Wykonanie zadań
# ---------------------------------------------------------------------------

def run_job(job: Job, entry: FileEntry) -> HashOutcome:
    """Uruchamia zadanie dla jednego pliku i zamienia błąd I/O na wynik z opisem.

    Dzięki temu jeden nieczytelny plik nie przerywa przetwarzania tysięcy innych.
    """
    try:
        return HashOutcome(entry, job(entry), None)
    except OSError as exc:
        return HashOutcome(entry, None, f"{type(exc).__name__}: {exc.strerror or exc}")


def default_workers(mode: str) -> int:
    """Domyślna liczba wykonawców: liczba rdzeni dla procesów, więcej dla wątków (I/O)."""
    cpus = os.cpu_count() or 1
    return cpus if mode == "processes" else min(32, cpus + 4)


@measure_time
def hash_many(entries: Sequence[FileEntry], job: Job, *, mode: str = "threads",
              workers: int | None = None,
              tracker: ProgressTracker | None = None) -> list[HashOutcome]:
    """Wykonuje ``job`` dla każdego pliku w wybranym trybie i zwraca wyniki w kolejności wejścia.

    Args:
        entries: pliki do przetworzenia.
        job: funkcja ``FileEntry -> str`` (dla procesów: musi dać się zserializować).
        mode: ``"sequential"``, ``"threads"`` lub ``"processes"``.
        workers: liczba wątków/procesów (``None`` = wartość domyślna dla trybu).
        tracker: opcjonalny licznik postępu.
    """
    if mode not in MODES:
        raise ValueError(f"Nieznany tryb {mode!r}; dostępne: {', '.join(MODES)}")
    if workers is not None and workers < 1:
        raise ValueError("workers musi być >= 1")
    tracker = tracker or ProgressTracker(len(entries), sum(e.size for e in entries))
    worker = functools.partial(run_job, job)  # partial funkcji modułu – da się „spicklować”
    n_workers = workers or default_workers(mode)
    logger.debug("hash_many: %d plików, tryb=%s, wykonawców=%d", len(entries), mode, n_workers)

    outcomes: list[HashOutcome] = []
    if mode == "sequential":
        for entry in entries:
            outcome = worker(entry)
            tracker.update(entry.size, error=outcome.error is not None)
            outcomes.append(outcome)
        return outcomes

    if mode == "threads":
        def tracked(entry: FileEntry) -> HashOutcome:
            # wykonywane równolegle w wielu wątkach – licznik chroni Lock
            outcome = worker(entry)
            tracker.update(entry.size, error=outcome.error is not None)
            return outcome

        with ThreadPoolExecutor(max_workers=n_workers, thread_name_prefix="hash") as threads:
            return list(threads.map(tracked, entries))

    # mode == "processes": wyniki zbiera proces główny, więc tu licznik
    # aktualizuje tylko jeden wątek; chunksize ogranicza liczbę komunikatów IPC
    chunksize = max(1, len(entries) // (n_workers * 4))
    with ProcessPoolExecutor(max_workers=n_workers) as processes:
        for outcome in processes.map(worker, entries, chunksize=chunksize):
            tracker.update(outcome.entry.size, error=outcome.error is not None)
            outcomes.append(outcome)
    return outcomes


def timed_run(entries: Sequence[FileEntry], job: Job, mode: str,
              workers: int | None = None) -> tuple[float, list[HashOutcome]]:
    """Pomocniczo dla benchmarku: zwraca (czas w sekundach, wyniki)."""
    start = time.perf_counter()
    outcomes = hash_many(entries, job, mode=mode, workers=workers)
    return time.perf_counter() - start, outcomes
