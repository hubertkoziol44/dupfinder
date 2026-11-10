"""Modele danych: plik, grupa duplikatów, wynik skanowania."""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass, field

from dupfinder.core.patterns import looks_like_copy


@dataclass(frozen=True, slots=True)
class FileEntry:
    """Pojedynczy plik znaleziony podczas przeglądania katalogów.

    Obiekt jest niezmienny (``frozen``) i lekki (``slots``) – takich obiektów
    mogą być setki tysięcy, a do tego trafiają do innych procesów (pickle).
    """

    path: str
    size: int
    mtime: float
    file_id: tuple[int, int] = (0, 0)  # (urządzenie, i-węzeł); (0, 0) = nieznany

    @property
    def name(self) -> str:
        """Sama nazwa pliku (bez katalogu)."""
        return os.path.basename(self.path)

    @property
    def identity(self) -> tuple[int, int] | str:
        """Klucz identyfikujący fizyczny plik – wykrywa ten sam plik osiągnięty dwa razy
        (nakładające się katalogi startowe, twarde dowiązania)."""
        return self.file_id if self.file_id[1] else os.path.normcase(os.path.abspath(self.path))


@dataclass(slots=True)
class DuplicateGroup:
    """Grupa plików o identycznej zawartości (ten sam rozmiar i skrót)."""

    digest: str
    size: int
    files: list[FileEntry]

    def __post_init__(self) -> None:
        if len(self.files) < 2:
            raise ValueError("Grupa duplikatów musi zawierać co najmniej 2 pliki")
        # najpierw "oryginał", potem kopie – kolejność jest stała i przewidywalna
        self.files.sort(key=self._original_rank)

    @staticmethod
    def _original_rank(entry: FileEntry) -> tuple[bool, float, int, str]:
        """Klucz sortowania: plik bez znacznika kopii w nazwie, potem najstarszy,
        potem o najkrótszej ścieżce."""
        return (looks_like_copy(entry.name), entry.mtime, len(entry.path), entry.path)

    @property
    def original(self) -> FileEntry:
        """Plik uznany za oryginał (pozostałe to kopie)."""
        return self.files[0]

    @property
    def copies(self) -> list[FileEntry]:
        """Pliki uznane za kopie – kandydaci do usunięcia."""
        return self.files[1:]

    @property
    def wasted_bytes(self) -> int:
        """Ile miejsca zajmują zbędne kopie."""
        return self.size * (len(self.files) - 1)

    def __len__(self) -> int:
        return len(self.files)

    def __iter__(self) -> Iterator[FileEntry]:
        return iter(self.files)


@dataclass(slots=True)
class ScanResult:
    """Wynik skanowania: grupy duplikatów oraz statystyki."""

    groups: list[DuplicateGroup]
    files_scanned: int = 0
    bytes_scanned: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)

    @property
    def duplicate_files(self) -> int:
        """Liczba zbędnych kopii (bez oryginałów)."""
        return sum(len(g) - 1 for g in self.groups)

    @property
    def wasted_bytes(self) -> int:
        """Łączne miejsce do odzyskania."""
        return sum(g.wasted_bytes for g in self.groups)

    def __len__(self) -> int:
        return len(self.groups)

    def __iter__(self) -> Iterator[DuplicateGroup]:
        return iter(self.groups)
