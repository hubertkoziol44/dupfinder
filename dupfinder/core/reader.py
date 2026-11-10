"""Własny iterator czytający plik blokami (zagadnienie 3)."""

from __future__ import annotations

import os
from types import TracebackType
from typing import BinaryIO

DEFAULT_CHUNK_SIZE = 1024 * 1024  # 1 MiB


class ChunkReader:
    """Iterator zwracający kolejne bloki (``bytes``) pliku binarnego.

    Implementuje protokół iteratora: ``__iter__`` zwraca ``self``, a ``__next__``
    zwraca kolejny blok lub zgłasza ``StopIteration`` po dojściu do końca.
    Dzięki temu nawet plik o rozmiarze kilku GB nie jest wczytywany do pamięci
    w całości – w danej chwili w RAM jest tylko jeden blok.

    Plik jest otwierany leniwie (przy pierwszym ``next``) i zamykany automatycznie
    po wyczerpaniu iteratora. Klasa jest też menedżerem kontekstu, więc
    ``with ChunkReader(...) as r:`` zamknie plik nawet po przerwaniu pętli.

    Args:
        path: ścieżka do pliku.
        chunk_size: rozmiar bloku w bajtach.
        offset: od którego bajtu zacząć czytanie.
        limit: maksymalna liczba bajtów do przeczytania (``None`` = do końca).

    Przykład::

        with ChunkReader("film.mp4", chunk_size=65536) as reader:
            for block in reader:
                hasher.update(block)
    """

    def __init__(self, path: str | os.PathLike[str], chunk_size: int = DEFAULT_CHUNK_SIZE,
                 *, offset: int = 0, limit: int | None = None) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk_size musi być dodatni")
        if offset < 0:
            raise ValueError("offset nie może być ujemny")
        if limit is not None and limit < 0:
            raise ValueError("limit nie może być ujemny")
        self.path = os.fspath(path)
        self.chunk_size = chunk_size
        self.offset = offset
        self._remaining = limit
        self._file: BinaryIO | None = None
        self._exhausted = False
        self.bytes_read = 0

    # --- protokół iteratora --------------------------------------------------
    def __iter__(self) -> ChunkReader:
        return self

    def __next__(self) -> bytes:
        if self._exhausted:
            raise StopIteration
        if self._file is None:
            # błąd otwarcia (brak pliku, brak uprawnień) trafia do wywołującego
            self._file = open(self.path, "rb")
            if self.offset:
                self._file.seek(self.offset)

        to_read = self.chunk_size
        if self._remaining is not None:
            to_read = min(to_read, self._remaining)
        chunk = self._file.read(to_read) if to_read > 0 else b""

        if not chunk:  # koniec pliku albo wyczerpany limit
            self.close()
            raise StopIteration

        self.bytes_read += len(chunk)
        if self._remaining is not None:
            self._remaining -= len(chunk)
        return chunk

    # --- sprzątanie / menedżer kontekstu ---------------------------------------
    def close(self) -> None:
        """Zamyka plik; kolejne ``next()`` zgłoszą ``StopIteration``."""
        self._exhausted = True
        if self._file is not None:
            self._file.close()
            self._file = None

    def __enter__(self) -> ChunkReader:
        return self

    def __exit__(self, exc_type: type[BaseException] | None,
                 exc: BaseException | None, tb: TracebackType | None) -> None:
        self.close()

    def __repr__(self) -> str:
        return (f"{type(self).__name__}({self.path!r}, chunk_size={self.chunk_size}, "
                f"bytes_read={self.bytes_read})")
