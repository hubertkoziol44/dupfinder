"""Wyrażenia regularne używane w projekcie (zagadnienie 5).

* ``SIZE_RE``       – walidacja i parsowanie rozmiaru podanego przez użytkownika
                      (np. ``10KB``, ``1.5 MiB``) – grupy nazwane ``value`` i ``unit``.
* ``COPY_NAME_RE``  – ekstrakcja: rozpoznaje nazwy kopii, np. ``zdjecie (1).jpg``,
                      ``raport - Kopia (2).docx``, ``notatki copy 2.txt`` – grupy
                      nazwane ``stem``, ``number``, ``marker``, ``copy_no``, ``ext``.
* ``DEFAULT_EXCLUDES`` – domyślne reguły wykluczeń ścieżek (katalogi systemów
                      kontroli wersji, cache, pliki tymczasowe).

Wzorce podawane przez użytkownika są kompilowane funkcją :func:`compile_patterns`:
``--include`` dopasowywany jest do nazwy pliku (np. ``\\.jpe?g$``), a ``--exclude``
do ścieżki względnej z separatorem ``/`` niezależnie od systemu
(np. ``(^|/)build(/|$)``).
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from typing import NamedTuple

# --- 1. Rozmiar pliku: walidacja + grupy nazwane ------------------------------
SIZE_RE = re.compile(
    r"""
    ^\s*
    (?P<value>\d+(?:[.,]\d+)?)     # liczba: 10, 1.5 albo 1,5
    \s*
    (?:
        (?P<unit>[kmgt])           # przedrostek: K, M, G, T (wielokrotności 1024)
        i?                         # opcjonalne 'i' (KiB, MiB…)
    )?
    b?                             # opcjonalne 'B'
    \s*$
    """,
    re.VERBOSE | re.IGNORECASE,
)

_UNIT_POWER = {"": 0, "k": 1, "m": 2, "g": 3, "t": 4}


def parse_size(text: str) -> int:
    """Zamienia napis z rozmiarem na liczbę bajtów.

    >>> parse_size("10KB")
    10240
    >>> parse_size("1,5 MiB")
    1572864

    Raises:
        ValueError: gdy napis nie pasuje do wzorca ``SIZE_RE``.
    """
    match = SIZE_RE.fullmatch(text)
    if match is None:
        raise ValueError(
            f"Nieprawidłowy rozmiar: {text!r} (przykłady: 500, 10KB, 1.5MB, 2G)"
        )
    value = float(match["value"].replace(",", "."))
    unit = (match["unit"] or "").lower()
    return int(value * 1024 ** _UNIT_POWER[unit])


# --- 2. Nazwy kopii plików: ekstrakcja + grupy nazwane -------------------------
COPY_NAME_RE = re.compile(
    r"""
    ^(?P<stem>.+?)                          # właściwa nazwa (dopasowanie leniwe)
    (?:
        \s*\((?P<number>\d+)\)              # "zdjecie (1)"   – Windows / przeglądarki
      |
        [\s_-]+                             # separator: spacja, '_' lub '-'
        (?P<marker>kopia|copy)              # "raport - Kopia", "notatki copy"
        (?:[\s_]*\(?(?P<copy_no>\d+)\)?)?   # opcjonalny numer: "copy 2", "Kopia (2)"
    )
    (?P<ext>(?:\.[^.\s()]+)*)$              # rozszerzenie(a): ".jpg", ".tar.gz" lub brak
    """,
    re.VERBOSE | re.IGNORECASE,
)


class CopyInfo(NamedTuple):
    """Informacja wyciągnięta z nazwy pliku wyglądającej na kopię."""

    stem: str
    copy_number: int
    extension: str


def parse_copy_name(filename: str) -> CopyInfo | None:
    """Zwraca :class:`CopyInfo`, jeśli nazwa pliku wygląda na kopię, w przeciwnym razie ``None``.

    >>> parse_copy_name("zdjecie (2).jpg")
    CopyInfo(stem='zdjecie', copy_number=2, extension='.jpg')
    >>> parse_copy_name("raport.docx") is None
    True
    """
    match = COPY_NAME_RE.fullmatch(os.path.basename(filename))
    if match is None:
        return None
    groups = match.groupdict()
    number = groups["number"] or groups["copy_no"] or "1"
    return CopyInfo(groups["stem"], int(number), groups["ext"])


def looks_like_copy(filename: str) -> bool:
    """Skrót: czy nazwa pliku wygląda na kopię innego pliku."""
    return parse_copy_name(filename) is not None


# --- 3. Wykluczenia ścieżek ----------------------------------------------------
DEFAULT_EXCLUDE_SOURCES: tuple[str, ...] = (
    # katalogi VCS, środowisk i cache – nigdzie nie ma sensu ich porównywać
    r"(^|/)(\.git|\.hg|\.svn|__pycache__|node_modules|\.venv|venv|\.tox)(/|$)",
    # pliki tymczasowe / niedokończone pobierania
    r"(~|\.(tmp|temp|swp|part|crdownload))$",
)


def compile_patterns(patterns: Iterable[str | re.Pattern[str]]) -> list[re.Pattern[str]]:
    """Kompiluje listę wzorców; błędny wzorzec zgłasza czytelny ``ValueError``."""
    compiled: list[re.Pattern[str]] = []
    for pattern in patterns:
        try:
            compiled.append(re.compile(pattern))
        except re.error as exc:
            raise ValueError(f"Nieprawidłowe wyrażenie regularne {pattern!r}: {exc}") from exc
    return compiled


DEFAULT_EXCLUDES: list[re.Pattern[str]] = compile_patterns(DEFAULT_EXCLUDE_SOURCES)


def normalize_path(path: str) -> str:
    """Ujednolica separator ścieżki do ``/``, aby wzorce działały tak samo na Windows i Linuksie."""
    return path.replace(os.sep, "/") if os.sep != "/" else path


def matches_any(path: str, patterns: Iterable[re.Pattern[str]]) -> bool:
    """Czy którykolwiek wzorzec występuje w (znormalizowanej) ścieżce."""
    normalized = normalize_path(path)
    return any(p.search(normalized) for p in patterns)
