"""Raporty z wynikami: podsumowanie na konsolę oraz pliki TXT / JSON / CSV.

Zapis raportów do kilku formatów to zadanie I/O, więc pliki zapisywane są
współbieżnie w puli wątków (zagadnienie 6).
"""

from __future__ import annotations

import csv
import io
import json
import logging
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dupfinder.core.models import ScanResult
from dupfinder.utils.decorators import measure_time
from dupfinder.utils.formatting import human_size

logger = logging.getLogger(__name__)

FORMATS = ("txt", "json", "csv")


def render_summary(result: ScanResult, top: int | None = 10) -> str:
    """Czytelne podsumowanie; ``top`` ogranicza liczbę wypisanych grup (``None`` = wszystkie)."""
    lines = [
        f"Przejrzano plików: {result.files_scanned} ({human_size(result.bytes_scanned)})",
        f"Grupy duplikatów: {len(result)}, zbędne kopie: {result.duplicate_files}, "
        f"do odzyskania: {human_size(result.wasted_bytes)}",
    ]
    if result.timings:
        lines.append("Czasy etapów:")
        lines += [f"  {name:<52} {sec:8.3f} s" for name, sec in result.timings.items()]

    shown = result.groups if top is None else result.groups[:top]
    if shown:
        everything = top is None or top >= len(result)
        title = "Wszystkie grupy" if everything else f"Największe grupy (top {top})"
        lines += ["", f"{title}:"]
    for no, group in enumerate(shown, start=1):
        lines.append(f"[{no}] {len(group)} × {human_size(group.size)} – do odzyskania "
                     f"{human_size(group.wasted_bytes)} (skrót {group.digest[:12]}…)")
        lines.append(f"    oryginał: {group.original.path}")
        lines += [f"    kopia:    {f.path}" for f in group.copies]
    if top is not None and len(result) > top:
        lines.append(f"... i {len(result) - top} kolejnych grup (pełna lista w raporcie)")

    if result.errors:
        lines += ["", f"Pominięte pliki (błędy): {len(result.errors)}"]
        limit = None if top is None else 5
        lines += [f"  {path}: {err}" for path, err in result.errors[:limit]]
    return "\n".join(lines)


def render_txt(result: ScanResult) -> str:
    """Pełny raport tekstowy (wszystkie grupy i błędy)."""
    return render_summary(result, top=None) + "\n"


def render_json(result: ScanResult) -> str:
    """Raport w formacie JSON – wygodny do dalszego przetwarzania."""
    data = {
        "summary": {
            "files_scanned": result.files_scanned,
            "bytes_scanned": result.bytes_scanned,
            "groups": len(result),
            "duplicate_files": result.duplicate_files,
            "wasted_bytes": result.wasted_bytes,
        },
        "timings_s": {k: round(v, 4) for k, v in result.timings.items()},
        "groups": [
            {
                "digest": g.digest,
                "size": g.size,
                "wasted_bytes": g.wasted_bytes,
                "original": g.original.path,
                "copies": [f.path for f in g.copies],
            }
            for g in result
        ],
        "errors": [{"path": p, "error": e} for p, e in result.errors],
    }
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def render_csv(result: ScanResult) -> str:
    """Raport CSV: jeden wiersz na plik (łatwy do otwarcia w arkuszu)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["grupa", "rola", "rozmiar_B", "skrot", "sciezka"])
    for no, group in enumerate(result, start=1):
        for i, entry in enumerate(group):
            role = "oryginal" if i == 0 else "kopia"
            writer.writerow([no, role, group.size, group.digest, entry.path])
    return buffer.getvalue()


RENDERERS: dict[str, Callable[[ScanResult], str]] = {
    "txt": render_txt,
    "json": render_json,
    "csv": render_csv,
}


def _render_and_write(fmt: str, result: ScanResult, path: Path) -> Path:
    """Zadanie dla wątku: przygotowuje treść raportu i zapisuje ją na dysk."""
    content = RENDERERS[fmt](result)
    # CSV z BOM, żeby Excel poprawnie pokazał polskie znaki
    encoding = "utf-8-sig" if fmt == "csv" else "utf-8"
    path.write_text(content, encoding=encoding, newline="")
    logger.info("Zapisano raport %s", path)
    return path


@measure_time
def export_reports(result: ScanResult, out_dir: Path,
                   formats: Sequence[str] = FORMATS, basename: str = "duplikaty") -> list[Path]:
    """Zapisuje raporty w wybranych formatach współbieżnie (pula wątków).

    Raises:
        ValueError: nieznany format.
        OSError: nie udało się zapisać co najmniej jednego raportu.
    """
    unknown = [f for f in formats if f not in RENDERERS]
    if unknown:
        raise ValueError(f"Nieznane formaty: {', '.join(unknown)}; dostępne: {', '.join(FORMATS)}")
    out_dir.mkdir(parents=True, exist_ok=True)

    saved: list[Path] = []
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=max(1, len(formats)), thread_name_prefix="report") as pool:
        futures = {pool.submit(_render_and_write, fmt, result, out_dir / f"{basename}.{fmt}"): fmt
                   for fmt in formats}
        for future in as_completed(futures):
            try:
                saved.append(future.result())
            except OSError as exc:
                failures.append(f"{futures[future]}: {exc}")
    if failures:
        raise OSError("Nie udało się zapisać raportów: " + "; ".join(failures))
    return sorted(saved)
