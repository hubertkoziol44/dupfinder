"""Własne dekoratory projektu.

Zagadnienie 1 – dekoratory funkcji:
    * ``measure_time``  – prosty dekorator (bez parametrów) mierzący czas,
    * ``retry``         – dekorator Z PARAMETRAMI (fabryka dekoratorów).

Zagadnienie 2 – dekoratory metod (korzystają z ``self``):
    * ``stage``           – mierzy czas etapu i zapisuje go w ``self.timings``,
    * ``requires_result`` – blokuje metodę, dopóki obiekt nie ma wyniku skanowania.

Wszystkie dekoratory używają ``functools.wraps``, dzięki czemu udekorowana
funkcja zachowuje nazwę, docstring i ``__qualname__``. To ostatnie jest ważne
w tym projekcie: funkcje wysyłane do innych procesów są serializowane przez
``pickle`` po nazwie, więc bez ``wraps`` nie dałoby się ich przekazać do puli
procesów.
"""

from __future__ import annotations

import functools
import logging
import time
from collections.abc import Callable
from typing import Any, ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Zagadnienie 1 – dekoratory funkcji
# ---------------------------------------------------------------------------

def measure_time(func: Callable[P, R]) -> Callable[P, R]:
    """Mierzy czas wykonania funkcji i zapisuje go w logu (poziom DEBUG).

    Ostatni zmierzony czas (w sekundach) jest dostępny jako atrybut
    ``last_elapsed`` funkcji udekorowanej. Czas jest mierzony także wtedy,
    gdy funkcja zgłosi wyjątek (blok ``finally``).
    """

    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        start = time.perf_counter()
        try:
            return func(*args, **kwargs)
        finally:
            elapsed = time.perf_counter() - start
            wrapper.last_elapsed = elapsed  # type: ignore[attr-defined]
            logger.debug("%s wykonano w %.4f s", func.__qualname__, elapsed)

    wrapper.last_elapsed = 0.0  # type: ignore[attr-defined]
    return wrapper


def retry(
    attempts: int = 3,
    delay: float = 0.1,
    backoff: float = 2.0,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
    reraise: tuple[type[BaseException], ...] = (),
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Fabryka dekoratorów ponawiających wywołanie po błędzie.

    Args:
        attempts: maksymalna liczba prób (>= 1).
        delay: czas oczekiwania przed drugą próbą (s).
        backoff: mnożnik czasu oczekiwania po każdej nieudanej próbie.
        exceptions: wyjątki, po których warto ponowić próbę.
        reraise: wyjątki przepuszczane od razu, bez ponawiania
            (np. ``FileNotFoundError`` – ponowienie nic nie zmieni).

    Po wyczerpaniu prób zgłaszany jest ostatni wyjątek, więc kod wywołujący
    może go normalnie obsłużyć.
    """
    if attempts < 1:
        raise ValueError("attempts musi być >= 1")
    if delay < 0 or backoff < 1:
        raise ValueError("delay musi być >= 0, a backoff >= 1")

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            wait = delay
            for attempt in range(1, attempts + 1):
                try:
                    return func(*args, **kwargs)
                except reraise:
                    raise
                except exceptions as exc:
                    if attempt == attempts:
                        logger.warning("%s: wyczerpano limit %d prób",
                                       func.__qualname__, attempts)
                        raise
                    logger.warning("%s: próba %d/%d nieudana (%s), ponawiam za %.2f s",
                                   func.__qualname__, attempt, attempts, exc, wait)
                    time.sleep(wait)
                    wait *= backoff
            raise AssertionError("nieosiągalne")  # pętla zawsze kończy się return/raise

        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# Zagadnienie 2 – dekoratory metod (pierwszym argumentem wrappera jest self)
# ---------------------------------------------------------------------------

def stage(name: str) -> Callable[[Callable[..., R]], Callable[..., R]]:
    """Dekorator metody: mierzy czas etapu i zapisuje go w ``self.timings[name]``.

    Obiekt, którego metodę dekorujemy, musi mieć atrybut ``timings`` (słownik).
    """

    def decorator(method: Callable[..., R]) -> Callable[..., R]:
        method_logger = logging.getLogger(method.__module__)  # logi pod nazwą modułu metody

        @functools.wraps(method)
        def wrapper(self: Any, *args: Any, **kwargs: Any) -> R:
            method_logger.info("Start etapu: %s", name)
            start = time.perf_counter()
            try:
                return method(self, *args, **kwargs)
            finally:
                elapsed = time.perf_counter() - start
                self.timings[name] = elapsed
                method_logger.info("Koniec etapu: %s (%.3f s)", name, elapsed)

        return wrapper

    return decorator


def requires_result(method: Callable[..., R]) -> Callable[..., R]:
    """Dekorator metody: pozwala ją wywołać dopiero, gdy ``self._result`` istnieje.

    Chroni przed np. eksportem raportu, zanim wykonano skanowanie.
    """

    @functools.wraps(method)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> R:
        if getattr(self, "_result", None) is None:
            raise RuntimeError(
                f"{type(self).__name__}.{method.__name__}: najpierw wywołaj scan()"
            )
        return method(self, *args, **kwargs)

    return wrapper
