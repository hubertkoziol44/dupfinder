"""Interfejs wiersza poleceń (argparse)."""

from __future__ import annotations

import argparse
import logging
import re
import sys
from collections.abc import Sequence
from pathlib import Path

from dupfinder import __version__
from dupfinder.concurrency.executors import MODES
from dupfinder.core.finder import DuplicateFinder
from dupfinder.core.hashing import ALGORITHMS
from dupfinder.core.patterns import parse_size
from dupfinder.report import FORMATS
from dupfinder.utils.logging_setup import setup_logging

logger = logging.getLogger("dupfinder")

EPILOG = r"""
przykłady:
  python -m dupfinder test_data
  python -m dupfinder ~/Zdjecia ~/Pobrane --min-size 100KB --include "\.(jpe?g|png)$"
  python -m dupfinder test_data --mode threads --workers 8 --progress -v
  python -m dupfinder test_data --exclude "(^|/)backup(/|$)" --report-dir raporty
"""


# --- walidatory argumentów (type=...) – błąd → czytelny komunikat argparse ------
def size_arg(text: str) -> int:
    try:
        return parse_size(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def regex_arg(text: str) -> re.Pattern[str]:
    try:
        return re.compile(text)
    except re.error as exc:
        raise argparse.ArgumentTypeError(
            f"nieprawidłowe wyrażenie regularne {text!r}: {exc}") from exc


def positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"to nie jest liczba całkowita: {text!r}") from None
    if value < 1:
        raise argparse.ArgumentTypeError("wartość musi być >= 1")
    return value


def formats_arg(text: str) -> list[str]:
    formats = [f.strip().lower() for f in text.split(",") if f.strip()]
    unknown = [f for f in formats if f not in FORMATS]
    if not formats or unknown:
        raise argparse.ArgumentTypeError(
            f"dozwolone formaty: {', '.join(FORMATS)} (np. txt,json)")
    return formats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dupfinder",
        description="Wyszukiwarka zduplikowanych plików (rozmiar → skrót częściowy → pełny skrót).",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("paths", nargs="+", metavar="KATALOG", help="katalogi do przeszukania")
    filters = parser.add_argument_group("filtrowanie")
    filters.add_argument("--min-size", type=size_arg, default=1, metavar="ROZMIAR",
                         help="minimalny rozmiar pliku, np. 500, 10KB, 1.5MB (domyślnie 1 B)")
    filters.add_argument("--max-size", type=size_arg, default=None, metavar="ROZMIAR",
                         help="maksymalny rozmiar pliku")
    filters.add_argument("-i", "--include", type=regex_arg, action="append", default=[],
                         metavar="REGEX", help="uwzględnij tylko pliki, których NAZWA pasuje "
                                               "(można podać wiele razy)")
    filters.add_argument("-e", "--exclude", type=regex_arg, action="append", default=[],
                         metavar="REGEX", help="pomiń ścieżki (względne) pasujące do wzorca")
    filters.add_argument("--no-default-excludes", action="store_true",
                         help="nie pomijaj domyślnie .git, __pycache__, node_modules, *.tmp itp.")

    perf = parser.add_argument_group("wydajność")
    perf.add_argument("-a", "--algorithm", choices=ALGORITHMS, default="sha256",
                      help="algorytm skrótu (fnv1a – czysty Python, wolny; do eksperymentów z GIL)")
    perf.add_argument("-m", "--mode", choices=MODES, default="processes",
                      help="sposób liczenia pełnych skrótów (domyślnie: processes)")
    perf.add_argument("-w", "--workers", type=positive_int, default=None,
                      help="liczba wątków/procesów (domyślnie zależna od liczby rdzeni)")

    out = parser.add_argument_group("wyniki")
    out.add_argument("-o", "--report-dir", type=Path, default=None, metavar="KATALOG",
                     help="zapisz raporty do katalogu")
    out.add_argument("-f", "--formats", type=formats_arg, default=list(FORMATS),
                     metavar="LISTA", help="formaty raportów, np. txt,json,csv")
    out.add_argument("--top", type=positive_int, default=10,
                     help="ile największych grup pokazać na ekranie (domyślnie 10)")
    out.add_argument("--progress", action="store_true", help="pokazuj postęp na stderr")
    out.add_argument("-v", "--verbose", action="count", default=0,
                     help="więcej logów (-v: etapy, -vv: szczegóły)")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Punkt wejścia programu. Zwraca kod wyjścia (0 – sukces)."""
    args = build_parser().parse_args(argv)
    setup_logging(args.verbose)
    try:
        finder = DuplicateFinder.from_args(args)
        finder.scan()
        print(finder.summary(top=args.top))
        if args.report_dir is not None:
            saved = finder.export(args.report_dir, args.formats)
            print("\nZapisano raporty:", *(f"  {p}" for p in saved), sep="\n")
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        logger.error("%s", exc)
        return 1
    except OSError as exc:
        logger.error("Błąd wejścia/wyjścia: %s", exc)
        return 1
    except KeyboardInterrupt:
        print("\nPrzerwano przez użytkownika.", file=sys.stderr)
        return 130
    return 0
