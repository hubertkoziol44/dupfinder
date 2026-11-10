"""Uruchomienie: ``python -m dupfinder KATALOG [...]``."""

from dupfinder.cli import main

# Zabezpieczenie konieczne przy multiprocessing: na Windows/macOS (metoda „spawn”)
# każdy proces potomny importuje moduł główny ponownie – bez tego warunku
# tworzyłby kolejne procesy w nieskończoność.
if __name__ == "__main__":
    raise SystemExit(main())
