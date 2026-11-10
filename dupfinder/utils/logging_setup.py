"""Konfiguracja modułu ``logging`` dla całej aplikacji."""

from __future__ import annotations

import logging
import sys

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging(verbosity: int = 0) -> None:
    """Ustawia poziom logowania na podstawie liczby flag ``-v``.

    0 → WARNING (tylko ostrzeżenia i błędy), 1 → INFO (etapy), 2+ → DEBUG.
    Logi trafiają na stderr, więc nie mieszają się z raportem na stdout.
    """
    if verbosity <= 0:
        level = logging.WARNING
    elif verbosity == 1:
        level = logging.INFO
    else:
        level = logging.DEBUG
    logging.basicConfig(level=level, format=LOG_FORMAT, datefmt="%H:%M:%S",
                        stream=sys.stderr, force=True)
