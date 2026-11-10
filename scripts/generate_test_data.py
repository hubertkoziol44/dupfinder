"""Generator danych testowych dla wyszukiwarki duplikatów.

Tworzy drzewo katalogów z plikami, wśród których są:

* zwykłe duplikaty (także pod nazwami typu ``IMG_0003 (1).jpg``, ``raport - Kopia.txt``),
* duże pliki (do obserwacji wydajności etapu pełnych skrótów),
* „pułapki”: pliki o tym samym rozmiarze, różniące się początkiem (odrzuca je
  etap 2) albo tylko środkiem (odrzuca je dopiero etap 3), puste pliki,
* duplikaty w miejscach domyślnie wykluczanych (``.git``, ``node_modules``, ``*.tmp``).

Dane są deterministyczne (stałe ziarno), a lista oczekiwanych grup zapisywana jest
do ``expected.json`` – testy porównują z nią wynik programu.

Użycie::

    python -m scripts.generate_test_data                # → katalog test_data/
    python -m scripts.generate_test_data -o dane --scale 3 --big-mb 32 --force
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

MARKER = ".dupfinder-testdata"   # plik-znacznik: tylko takie katalogi wolno nadpisać
BASE_TIME = time.mktime((2024, 1, 1, 12, 0, 0, 0, 0, -1))
DAY = 24 * 3600

WORDS = ("dane plik raport projekt python wątek proces generator iterator dekorator "
         "kopia zapas zdjęcie dokument wynik pomiar analiza system katalog skrót").split()


class DataBuilder:
    """Zapisuje pliki i zapamiętuje, które z nich powinny zostać uznane za duplikaty."""

    def __init__(self, root: Path, rng: random.Random) -> None:
        self.root = root
        self.rng = rng
        self.groups: defaultdict[str, list[str]] = defaultdict(list)
        self.files = 0
        self.bytes = 0

    def write(self, rel: str, data: bytes, *, group: str | None = None,
              age_days: float = 0.0) -> None:
        """Zapisuje plik; ``group`` – klucz grupy oczekiwanych duplikatów (None = nie liczymy).

        ``age_days`` przesuwa datę modyfikacji wstecz – „oryginał” jest najstarszy.
        """
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        mtime = BASE_TIME - age_days * DAY
        os.utime(path, (mtime, mtime))
        self.files += 1
        self.bytes += len(data)
        if group is not None:
            self.groups[group].append(rel)

    def binary(self, size: int, header: bytes = b"") -> bytes:
        return header + self.rng.randbytes(max(0, size - len(header)))

    def text(self, words: int) -> bytes:
        lines = []
        for _ in range(max(1, words // 12)):
            lines.append(" ".join(self.rng.choice(WORDS) for _ in range(12)).capitalize() + ".")
        return ("\n".join(lines) + "\n").encode("utf-8")

    def expected(self) -> list[list[str]]:
        """Oczekiwane grupy duplikatów (posortowane ścieżki względne, separator '/')."""
        return sorted(sorted(paths) for paths in self.groups.values() if len(paths) > 1)


def build(b: DataBuilder, scale: int, big_mb: int) -> None:
    rng = b.rng
    jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00"

    # --- zdjęcia + kopie zapasowe pod różnymi nazwami ---------------------------
    photos = 12 * scale
    for i in range(1, photos + 1):
        name = f"IMG_{i:04d}.jpg"
        data = b.binary(rng.randint(30, 300) * 1024, jpeg)
        key = f"photo-{i}"
        b.write(f"zdjecia/{name}", data, group=key, age_days=30)
        if i % 3 == 0:
            b.write(f"zdjecia_backup/{name}", data, group=key, age_days=10)
        if i % 4 == 0:
            b.write(f"zdjecia/{name[:-4]} (1).jpg", data, group=key, age_days=5)
        if i % 6 == 0:
            b.write(f"zdjecia/2024/wakacje/{name}", data, group=key, age_days=1)

    # --- dokumenty tekstowe (małe pliki – wystarczy skrót częściowy) -------------
    for i in range(1, 6 * scale + 1):
        data = b.text(rng.randint(50, 600))
        key = f"doc-{i}"
        b.write(f"dokumenty/raport_{i:02d}.txt", data, group=key, age_days=20)
        if i % 2 == 0:
            b.write(f"dokumenty/raport_{i:02d} - Kopia.txt", data, group=key, age_days=2)
        if i % 5 == 0:
            b.write(f"dokumenty/stare/raport_{i:02d} copy 2.txt", data, group=key, age_days=1)
    notes = b.text(200)
    b.write("dokumenty/notatki.md", notes, group="notes", age_days=15)
    b.write("pobrane/notatki (1).md", notes, group="notes", age_days=3)

    # --- duże pliki --------------------------------------------------------------
    installer = b.binary(big_mb * 1024 * 1024, b"MZ")
    b.write("pobrane/instalator.bin", installer, group="installer", age_days=40)
    b.write("pobrane/instalator (1).bin", installer, group="installer", age_days=4)
    b.write("pobrane/instalator (2).bin", installer, group="installer", age_days=3)
    movie = b.binary(2 * big_mb * 1024 * 1024, b"\x00\x00\x00\x18ftypmp42")
    b.write("pobrane/film.mp4", movie, group="movie", age_days=50)
    b.write("archiwum/2023/film.mp4", movie, group="movie", age_days=8)
    b.write("pobrane/inny_film.mp4", b.binary(len(movie)))  # ten sam rozmiar, inna treść

    # --- pułapki -----------------------------------------------------------------
    for i in range(5):  # ten sam rozmiar, różny początek → odpadają w etapie 2
        b.write(f"pulapki/rozne_poczatki_{i}.dat", b.binary(64 * 1024))
    base = bytearray(b.binary(64 * 1024))
    variants = []
    for i in range(3):  # ten sam początek i koniec, inny środek → odpadają dopiero w etapie 3
        variant = bytearray(base)
        variant[len(variant) // 2] = i
        variants.append(bytes(variant))
        b.write(f"pulapki/rozny_srodek_{i}.dat", variants[-1],
                group=f"middle-{i}", age_days=6)
    b.write("pulapki/rozny_srodek_0 - Kopia.dat", variants[0], group="middle-0", age_days=1)
    b.write("pulapki/pusty_1.txt", b"")  # puste pliki pomija --min-size (domyślnie 1 B)
    b.write("pulapki/pusty_2.txt", b"")

    # --- duplikaty w miejscach domyślnie wykluczanych (nie powinny się pojawić) ---
    blob = b.binary(20 * 1024)
    b.write("projekt/.git/objects/ab/cdef", blob)
    b.write("projekt/node_modules/lib/index.js", blob)
    b.write("projekt/kopia_robocza.tmp", blob)
    b.write("projekt/src/main.py", b"print('Hello')\n", group="main", age_days=9)
    b.write("projekt/src/main (1).py", b"print('Hello')\n", group="main", age_days=1)


@dataclass(frozen=True)
class GeneratedData:
    """Opis wygenerowanych danych (zapisywany też do ``expected.json``)."""

    seed: int
    scale: int
    big_mb: int
    files: int
    bytes: int
    expected_groups: list[list[str]]


def generate(output: Path, *, seed: int = 42, scale: int = 1, big_mb: int = 8,
             force: bool = False) -> GeneratedData:
    """Tworzy dane testowe w ``output`` i zwraca ich opis."""
    if scale < 1 or big_mb < 1:
        raise ValueError("scale i big_mb muszą być >= 1")
    if output.exists() and any(output.iterdir()):
        if not force:
            raise FileExistsError(f"Katalog {output} nie jest pusty (użyj --force)")
        if not (output / MARKER).exists():
            # zabezpieczenie: nie usuwamy katalogu, którego nie utworzył ten skrypt
            raise FileExistsError(f"{output} nie zawiera pliku {MARKER} – nie usunę go")
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)
    (output / MARKER).write_text("katalog wygenerowany przez generate_test_data.py\n",
                                 encoding="utf-8")

    builder = DataBuilder(output, random.Random(seed))
    build(builder, scale, big_mb)
    info = GeneratedData(seed, scale, big_mb, builder.files, builder.bytes, builder.expected())
    (output / "expected.json").write_text(json.dumps(asdict(info), ensure_ascii=False, indent=2),
                                          encoding="utf-8")
    return info


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generuje dane testowe z duplikatami.")
    parser.add_argument("-o", "--output", type=Path, default=Path("test_data"))
    parser.add_argument("--seed", type=int, default=42, help="ziarno generatora (powtarzalność)")
    parser.add_argument("--scale", type=int, default=1, help="mnożnik liczby małych plików")
    parser.add_argument("--big-mb", type=int, default=8, help="rozmiar dużego pliku w MiB")
    parser.add_argument("--force", action="store_true", help="nadpisz istniejące dane testowe")
    args = parser.parse_args(argv)
    try:
        info = generate(args.output, seed=args.seed, scale=args.scale,
                        big_mb=args.big_mb, force=args.force)
    except (FileExistsError, ValueError) as exc:
        print(f"Błąd: {exc}", file=sys.stderr)
        return 1
    groups = info.expected_groups
    print(f"Utworzono {info.files} plików ({info.bytes / 2**20:.1f} MiB) w {args.output}")
    print(f"Oczekiwane grupy duplikatów: {len(groups)} "
          f"(zbędnych kopii: {sum(len(g) - 1 for g in groups)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
