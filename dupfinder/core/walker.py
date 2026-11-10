"""Leniwe przeglądanie katalogów – potok generatorów (zagadnienie 4).

Potok wygląda tak::

    walk_files()  →  unique_files()  →  filter_files()  →  group_by()
    (generator,       (generator,        (wyrażenie         (konsumuje
     yield from)       pomija ten sam     generujące)        strumień)
                       plik 2 razy)

Pliki przepływają przez potok pojedynczo, więc nawet katalog z milionem
plików nie jest w całości zbierany do listy na etapie przeglądania.
"""

from __future__ import annotations

import logging
import os
import re
from collections import defaultdict
from collections.abc import Callable, Hashable, Iterable, Iterator, Sequence
from typing import TypeVar

from dupfinder.core.models import FileEntry
from dupfinder.core.patterns import matches_any

logger = logging.getLogger(__name__)

T = TypeVar("T")
K = TypeVar("K", bound=Hashable)

ErrorLog = list[tuple[str, str]]


def _record_error(errors: ErrorLog | None, path: str, exc: OSError) -> None:
    message = f"{type(exc).__name__}: {exc.strerror or exc}"
    logger.warning("Pomijam %s (%s)", path, message)
    if errors is not None:
        errors.append((path, message))


def walk_files(root: str | os.PathLike[str],
               exclude: Sequence[re.Pattern[str]] = (),
               errors: ErrorLog | None = None,
               _prefix_len: int | None = None) -> Iterator[FileEntry]:
    """Rekurencyjnie przegląda katalog i leniwie zwraca znalezione pliki.

    * dowiązania symboliczne są pomijane (ochrona przed pętlami),
    * ścieżki pasujące do wzorców ``exclude`` są pomijane razem z zawartością;
      wzorce dopasowywane są do ścieżki WZGLĘDNEJ wobec ``root``
      (np. ``zdjecia/2024/a.jpg``), podobnie jak reguły w ``.gitignore``,
    * błędy dostępu nie przerywają skanowania – trafiają do listy ``errors``.
    """
    root = os.fspath(root)
    if _prefix_len is None:  # pierwsze (zewnętrzne) wywołanie
        _prefix_len = len(os.path.join(root, ""))
    try:
        with os.scandir(root) as iterator:
            # zamykamy katalog przed zejściem głębiej – nie trzymamy wielu
            # otwartych deskryptorów przy głębokich drzewach
            entries = sorted(iterator, key=lambda e: e.name)
    except OSError as exc:
        _record_error(errors, root, exc)
        return

    for entry in entries:
        if exclude and matches_any(entry.path[_prefix_len:], exclude):
            logger.debug("Wykluczono: %s", entry.path)
            continue
        try:
            if entry.is_symlink():
                logger.debug("Pomijam dowiązanie symboliczne: %s", entry.path)
            elif entry.is_dir(follow_symlinks=False):
                # rekurencyjna delegacja do generatora dla podkatalogu
                yield from walk_files(entry.path, exclude, errors, _prefix_len)
            elif entry.is_file(follow_symlinks=False):
                st = entry.stat(follow_symlinks=False)
                yield FileEntry(entry.path, st.st_size, st.st_mtime,
                                (st.st_dev, entry.inode()))
        except OSError as exc:  # np. plik usunięty w trakcie skanowania
            _record_error(errors, entry.path, exc)


def unique_files(roots: Iterable[str | os.PathLike[str]],
                 exclude: Sequence[re.Pattern[str]] = (),
                 errors: ErrorLog | None = None) -> Iterator[FileEntry]:
    """Łączy wyniki dla wielu katalogów i pomija pliki osiągnięte więcej niż raz
    (np. gdy podano ``dane`` i ``dane/zdjecia`` albo przy twardych dowiązaniach)."""
    seen: set[tuple[int, int] | str] = set()
    for root in roots:
        for entry in walk_files(root, exclude, errors):
            if entry.identity in seen:
                continue
            seen.add(entry.identity)
            yield entry


def filter_files(entries: Iterable[FileEntry], *, min_size: int = 1,
                 max_size: int | None = None,
                 include: Sequence[re.Pattern[str]] = ()) -> Iterator[FileEntry]:
    """Filtruje pliki po rozmiarze i (opcjonalnie) wzorcach ``include``
    dopasowywanych do NAZWY pliku (np. ``\\.jpe?g$``).

    Zwraca wyrażenie generujące – nic nie jest liczone, dopóki ktoś nie
    zacznie pobierać elementów.
    """
    return (
        e for e in entries
        if e.size >= min_size
        and (max_size is None or e.size <= max_size)
        and (not include or any(p.search(e.name) for p in include))
    )


def group_by(items: Iterable[T], key: Callable[[T], K]) -> dict[K, list[T]]:
    """Grupuje elementy strumienia według klucza (np. rozmiaru lub skrótu)."""
    groups: defaultdict[K, list[T]] = defaultdict(list)
    for item in items:
        groups[key(item)].append(item)
    return dict(groups)


def only_duplicates(groups: dict[K, list[T]]) -> Iterator[tuple[K, list[T]]]:
    """Generator zwracający tylko grupy mające więcej niż jeden element."""
    yield from ((k, v) for k, v in groups.items() if len(v) > 1)
