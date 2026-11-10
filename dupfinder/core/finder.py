"""Główna logika wyszukiwania duplikatów.

Algorytm działa w trzech etapach – każdy kolejny jest droższy, ale dotyczy
coraz mniejszej liczby plików:

1. **Rozmiar** – pliki o unikalnym rozmiarze nie mogą mieć duplikatu
   (sam odczyt metadanych, bez otwierania plików).
2. **Skrót częściowy** (4 KiB z początku + 4 KiB z końca) – zadanie I/O-bound:
   dużo małych odczytów → **wątki**.
3. **Pełny skrót** całej zawartości – zadanie CPU-bound przy dużych plikach
   → domyślnie **procesy** (tryb można zmienić opcją ``mode``).
"""

from __future__ import annotations

import argparse
import functools
import logging
import os
import re
from collections.abc import Iterable, Iterator, Sequence
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path

from dupfinder.concurrency.executors import (MODES, HashOutcome, ProgressReporter,
                                             ProgressTracker, hash_many)
from dupfinder.core.hashing import ALGORITHMS, covers_whole_file, digest_entry
from dupfinder.core.models import DuplicateGroup, FileEntry, ScanResult
from dupfinder.core.patterns import DEFAULT_EXCLUDES, compile_patterns, parse_size
from dupfinder.core.walker import filter_files, group_by, only_duplicates, unique_files
from dupfinder.report import export_reports, render_summary
from dupfinder.utils.decorators import requires_result, stage

logger = logging.getLogger(__name__)

PathLike = str | os.PathLike[str]
PatternLike = str | re.Pattern[str]
# grupa kandydatów: (rozmiar, skrót częściowy, pliki)
Candidate = tuple[int, str, list[FileEntry]]


class DuplicateFinder:
    """Wyszukiwarka zduplikowanych plików.

    Przykład::

        finder = DuplicateFinder(["~/Pobrane"], min_size="10KB", mode="processes")
        result = finder.scan()
        for group in result:
            print(group.original.path, "->", [f.path for f in group.copies])
    """

    def __init__(self, roots: Iterable[PathLike], *,
                 min_size: int | str = 1,
                 max_size: int | str | None = None,
                 include: Iterable[PatternLike] = (),
                 exclude: Iterable[PatternLike] = (),
                 use_default_excludes: bool = True,
                 algorithm: str = "sha256",
                 mode: str = "processes",
                 workers: int | None = None,
                 show_progress: bool = False) -> None:
        self.roots = self._validate_roots(roots)
        self.min_size = min_size          # walidacja w setterach (@property)
        self.max_size = max_size
        self.algorithm = algorithm
        self.mode = mode
        self.workers = workers
        self.show_progress = show_progress
        self._include = compile_patterns(include)
        self._exclude = compile_patterns(exclude)
        if use_default_excludes:
            self._exclude += DEFAULT_EXCLUDES
        self.timings: dict[str, float] = {}   # wypełniane przez dekorator @stage
        self._errors: list[tuple[str, str]] = []
        self._result: ScanResult | None = None

    # ------------------------------------------------------------------
    # Konstruktor alternatywny (@classmethod)
    # ------------------------------------------------------------------
    @classmethod
    def from_args(cls, args: argparse.Namespace) -> DuplicateFinder:
        """Tworzy obiekt na podstawie argumentów wiersza poleceń (``argparse``)."""
        return cls(
            args.paths,
            min_size=args.min_size,
            max_size=args.max_size,
            include=args.include,
            exclude=args.exclude,
            use_default_excludes=not args.no_default_excludes,
            algorithm=args.algorithm,
            mode=args.mode,
            workers=args.workers,
            show_progress=args.progress,
        )

    @staticmethod
    def _validate_roots(roots: Iterable[PathLike]) -> list[str]:
        """Sprawdza, czy wszystkie katalogi startowe istnieją."""
        result = []
        for root in roots:
            path = Path(root).expanduser()
            if not path.exists():
                raise FileNotFoundError(f"Katalog nie istnieje: {path}")
            if not path.is_dir():
                raise NotADirectoryError(f"To nie jest katalog: {path}")
            result.append(str(path))
        if not result:
            raise ValueError("Podaj co najmniej jeden katalog do przeszukania")
        return result

    # ------------------------------------------------------------------
    # Właściwości z walidacją (@property + setter)
    # ------------------------------------------------------------------
    @property
    def min_size(self) -> int:
        """Minimalny rozmiar pliku w bajtach (akceptuje też napisy typu ``"10KB"``)."""
        return self._min_size

    @min_size.setter
    def min_size(self, value: int | str) -> None:
        size = parse_size(value) if isinstance(value, str) else value
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ValueError(f"min_size musi być liczbą całkowitą >= 0, a jest {value!r}")
        self._min_size = size

    @property
    def max_size(self) -> int | None:
        """Maksymalny rozmiar pliku w bajtach (``None`` = bez limitu)."""
        return self._max_size

    @max_size.setter
    def max_size(self, value: int | str | None) -> None:
        size = parse_size(value) if isinstance(value, str) else value
        if size is not None:
            if isinstance(size, bool) or not isinstance(size, int) or size < 0:
                raise ValueError(f"max_size musi być liczbą całkowitą >= 0, a jest {value!r}")
            if size < self.min_size:
                raise ValueError("max_size nie może być mniejszy niż min_size")
        self._max_size = size

    @property
    def algorithm(self) -> str:
        """Nazwa algorytmu skrótu."""
        return self._algorithm

    @algorithm.setter
    def algorithm(self, value: str) -> None:
        if value not in ALGORITHMS:
            raise ValueError(f"Nieznany algorytm {value!r}; dostępne: {', '.join(ALGORITHMS)}")
        self._algorithm = value

    @property
    def mode(self) -> str:
        """Tryb liczenia pełnych skrótów: sequential / threads / processes."""
        return self._mode

    @mode.setter
    def mode(self, value: str) -> None:
        if value not in MODES:
            raise ValueError(f"Nieznany tryb {value!r}; dostępne: {', '.join(MODES)}")
        self._mode = value

    @property
    def workers(self) -> int | None:
        """Liczba wątków/procesów (``None`` = wartość domyślna)."""
        return self._workers

    @workers.setter
    def workers(self, value: int | None) -> None:
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)
                                  or value < 1):
            raise ValueError(f"workers musi być liczbą całkowitą >= 1, a jest {value!r}")
        self._workers = value

    @property
    @requires_result
    def result(self) -> ScanResult:
        """Wynik ostatniego skanowania (dostępny dopiero po ``scan()``)."""
        assert self._result is not None
        return self._result

    # ------------------------------------------------------------------
    # Skanowanie
    # ------------------------------------------------------------------
    def scan(self) -> ScanResult:
        """Wykonuje wszystkie etapy i zwraca :class:`ScanResult`."""
        self.timings = {}
        self._errors = []
        size_groups, files_scanned, bytes_scanned = self._collect_by_size()
        candidates = self._partial_hash_stage(size_groups)
        groups = self._full_hash_stage(candidates)
        self._result = ScanResult(groups, files_scanned, bytes_scanned,
                                  errors=self._errors, timings=dict(self.timings))
        return self._result

    @stage("1. przegląd katalogów i grupowanie po rozmiarze")
    def _collect_by_size(self) -> tuple[list[list[FileEntry]], int, int]:
        files = filter_files(unique_files(self.roots, self._exclude, self._errors),
                             min_size=self.min_size, max_size=self.max_size,
                             include=self._include)
        files_scanned = bytes_scanned = 0

        def counted(entries: Iterable[FileEntry]) -> Iterator[FileEntry]:
            nonlocal files_scanned, bytes_scanned
            for entry in entries:  # generator „podglądający” strumień
                files_scanned += 1
                bytes_scanned += entry.size
                yield entry

        by_size = group_by(counted(files), key=lambda e: e.size)
        size_groups = [group for _, group in only_duplicates(by_size)]
        logger.info("Przejrzano %d plików; kandydatów o wspólnym rozmiarze: %d",
                    files_scanned, sum(len(g) for g in size_groups))
        return size_groups, files_scanned, bytes_scanned

    @stage("2. skróty częściowe (wątki)")
    def _partial_hash_stage(self, size_groups: Sequence[list[FileEntry]]) -> list[Candidate]:
        entries = [entry for group in size_groups for entry in group]
        # odczyt małych fragmentów wielu plików to typowe zadanie I/O → wątki
        mode = "sequential" if self.mode == "sequential" else "threads"
        job = functools.partial(digest_entry, algorithm=self.algorithm, partial=True)
        outcomes = self._run(entries, job, mode, "Skróty częściowe")
        return self._regroup(outcomes)

    @stage("3. pełne skróty")
    def _full_hash_stage(self, candidates: Sequence[Candidate]) -> list[DuplicateGroup]:
        groups: list[DuplicateGroup] = []
        to_hash: list[FileEntry] = []
        for size, digest, files in candidates:
            if covers_whole_file(size):
                # mały plik: skrót częściowy objął całą zawartość – nie liczymy ponownie
                groups.append(DuplicateGroup(digest, size, files))
            else:
                to_hash.extend(files)

        job = functools.partial(digest_entry, algorithm=self.algorithm, partial=False)
        outcomes = self._run(to_hash, job, self.mode, "Pełne skróty")
        groups.extend(DuplicateGroup(digest, size, files)
                      for size, digest, files in self._regroup(outcomes))
        groups.sort(key=lambda g: (-g.wasted_bytes, g.original.path))
        return groups

    # ------------------------------------------------------------------
    # Pomocnicze
    # ------------------------------------------------------------------
    def _run(self, entries: Sequence[FileEntry], job: functools.partial[str],
             mode: str, label: str) -> list[HashOutcome]:
        if not entries:
            return []
        tracker = ProgressTracker(len(entries), sum(e.size for e in entries))
        reporter: AbstractContextManager[object] = (
            ProgressReporter(tracker, label) if self.show_progress else nullcontext()
        )
        with reporter:
            outcomes = hash_many(entries, job, mode=mode, workers=self.workers,
                                 tracker=tracker)
        self._errors.extend((o.entry.path, o.error) for o in outcomes if o.error)
        return outcomes

    @staticmethod
    def _regroup(outcomes: Iterable[HashOutcome]) -> list[Candidate]:
        """Grupuje pliki po (rozmiar, skrót) i zostawia tylko grupy z duplikatami."""
        # wyrażenie generujące: pomijamy pliki, dla których wystąpił błąd
        hashed = ((o.entry, o.digest) for o in outcomes if o.digest is not None)
        groups = group_by(hashed, key=lambda pair: (pair[0].size, pair[1]))
        return [(size, digest, [entry for entry, _ in items])
                for (size, digest), items in only_duplicates(groups)]

    # ------------------------------------------------------------------
    # Metody wymagające wyniku (dekorator metody @requires_result)
    # ------------------------------------------------------------------
    @requires_result
    def summary(self, top: int = 10) -> str:
        """Tekstowe podsumowanie ostatniego skanowania."""
        return render_summary(self.result, top=top)

    @requires_result
    def export(self, out_dir: PathLike, formats: Sequence[str] = ("txt", "json", "csv"),
               basename: str = "duplikaty") -> list[Path]:
        """Zapisuje raporty ostatniego skanowania do katalogu ``out_dir``."""
        return export_reports(self.result, Path(out_dir), formats, basename)
