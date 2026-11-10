"""Liczenie skrótów (hashy) zawartości plików.

Dostępne algorytmy:

* ``sha256``, ``sha1``, ``md5``, ``blake2b`` – z modułu ``hashlib`` (kod w C).
  Dla dużych bloków danych ``hashlib`` **zwalnia GIL**, więc wątki mogą liczyć
  skróty naprawdę równolegle.
* ``fnv1a`` – FNV-1a 64-bit napisany w czystym Pythonie. Każdy bajt przetwarzany
  jest w pętli interpretera, więc wątek przez cały czas trzyma GIL. Algorytm jest
  celowo „wolny” – pokazuje różnicę między wątkami a procesami (zagadnienie 8).
"""

from __future__ import annotations

import hashlib
from typing import Protocol

from dupfinder.core.models import FileEntry
from dupfinder.core.reader import DEFAULT_CHUNK_SIZE, ChunkReader
from dupfinder.utils.decorators import retry

HASHLIB_ALGORITHMS = ("sha256", "sha1", "md5", "blake2b")
ALGORITHMS = (*HASHLIB_ALGORITHMS, "fnv1a")

#: rozmiar bloku czytanego z początku i z końca pliku przy skrócie częściowym
PARTIAL_BLOCK = 4096

#: błędy, których nie ma sensu ponawiać – plik zniknął lub nie mamy do niego dostępu
PERMANENT_ERRORS: tuple[type[OSError], ...] = (
    FileNotFoundError, PermissionError, IsADirectoryError, NotADirectoryError,
)


class Hasher(Protocol):
    """Minimalny interfejs obiektu liczącego skrót (zgodny z ``hashlib``)."""

    def update(self, data: bytes, /) -> None: ...

    def hexdigest(self) -> str: ...


class FNV1a64:
    """FNV-1a (64 bity) w czystym Pythonie, z interfejsem jak obiekty ``hashlib``."""

    OFFSET_BASIS = 0xCBF29CE484222325
    PRIME = 0x100000001B3
    MASK = (1 << 64) - 1

    def __init__(self) -> None:
        self._value = self.OFFSET_BASIS

    def update(self, data: bytes, /) -> None:
        value, prime, mask = self._value, self.PRIME, self.MASK
        for byte in data:  # pętla w kodzie bajtowym Pythona – GIL nie jest zwalniany
            value = ((value ^ byte) * prime) & mask
        self._value = value

    def hexdigest(self) -> str:
        return f"{self._value:016x}"


def new_hasher(algorithm: str) -> Hasher:
    """Tworzy obiekt liczący skrót dla podanej nazwy algorytmu."""
    if algorithm == "fnv1a":
        return FNV1a64()
    if algorithm in HASHLIB_ALGORITHMS:
        return hashlib.new(algorithm)
    raise ValueError(f"Nieznany algorytm {algorithm!r}; dostępne: {', '.join(ALGORITHMS)}")


def _feed(hasher: Hasher, reader: ChunkReader) -> None:
    """Przekazuje do ``hasher`` wszystkie bloki z iteratora ``reader``."""
    with reader:  # plik zostanie zamknięty nawet przy wyjątku
        for chunk in reader:
            hasher.update(chunk)


@retry(attempts=3, delay=0.05, exceptions=(OSError,), reraise=PERMANENT_ERRORS)
def file_digest(path: str, algorithm: str = "sha256",
                chunk_size: int = DEFAULT_CHUNK_SIZE) -> str:
    """Skrót całej zawartości pliku, czytanej blokami przez :class:`ChunkReader`.

    Przejściowe błędy wejścia/wyjścia (np. na dysku sieciowym) są ponawiane
    przez dekorator ``@retry``; brak pliku lub uprawnień – nie.
    """
    hasher = new_hasher(algorithm)
    _feed(hasher, ChunkReader(path, chunk_size))
    return hasher.hexdigest()


@retry(attempts=3, delay=0.05, exceptions=(OSError,), reraise=PERMANENT_ERRORS)
def partial_digest(path: str, size: int, algorithm: str = "sha256",
                   block: int = PARTIAL_BLOCK) -> str:
    """Szybki skrót z początku i końca pliku (po ``block`` bajtów).

    Większość plików o tym samym rozmiarze różni się już w nagłówku lub na
    końcu, więc ten etap odrzuca je kosztem odczytu zaledwie 8 KiB.
    Dla małych plików (``size <= 2 * block``) skrót obejmuje cały plik i jest
    identyczny z :func:`file_digest` – wtedy pełne liczenie można pominąć.
    """
    hasher = new_hasher(algorithm)
    if covers_whole_file(size, block):
        _feed(hasher, ChunkReader(path, block))
    else:
        for offset in (0, size - block):  # początek i koniec pliku
            _feed(hasher, ChunkReader(path, block, offset=offset, limit=block))
    return hasher.hexdigest()


def covers_whole_file(size: int, block: int = PARTIAL_BLOCK) -> bool:
    """Czy skrót częściowy pliku o tym rozmiarze obejmuje całą jego zawartość."""
    return size <= 2 * block


def digest_entry(entry: FileEntry, *, algorithm: str = "sha256", partial: bool = False) -> str:
    """Zadanie dla puli wątków/procesów: skrót pliku opisanego przez ``FileEntry``.

    Funkcja jest zdefiniowana na poziomie modułu, więc ``functools.partial``
    z nią da się zserializować (pickle) i wysłać do procesu potomnego.
    """
    if partial:
        return partial_digest(entry.path, entry.size, algorithm)
    return file_digest(entry.path, algorithm)
