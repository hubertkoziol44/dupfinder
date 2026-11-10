"""Funkcje pomocnicze do formatowania wyników."""

from __future__ import annotations

_UNITS = ("B", "KiB", "MiB", "GiB", "TiB")


def human_size(num_bytes: int | float) -> str:
    """Zamienia liczbę bajtów na czytelny napis, np. ``1536 -> '1.5 KiB'``."""
    if num_bytes < 0:
        raise ValueError("Rozmiar nie może być ujemny")
    value = float(num_bytes)
    for unit in _UNITS:
        if value < 1024 or unit == _UNITS[-1]:
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    raise AssertionError("nieosiągalne")
