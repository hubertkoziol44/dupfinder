"""Pomiar wpływu GIL: sekwencyjnie vs wątki vs procesy (zagadnienie 8).

Benchmark uruchamia PRAWDZIWY kod aplikacji (``hash_many`` + ``digest_entry``)
w trzech scenariuszach:

1. **CPU – czysty Python (FNV-1a)**: każdy bajt w pętli interpretera, wątek cały
   czas trzyma GIL → wątki nie powinny przyspieszać, procesy tak.
2. **CPU – hashlib SHA-256**: obliczenia w C, ``hashlib`` zwalnia GIL dla dużych
   bloków → wątki również przyspieszają.
3. **I/O – symulowany dysk sieciowy**: ``time.sleep`` (opóźnienie) przed odczytem;
   podczas czekania GIL jest zwolniony → wątki przyspieszają niemal liniowo.

Wyniki (mediana z kilku powtórzeń) trafiają do ``benchmarks/results/``:
tabela Markdown (do wklejenia w README), JSON i – z opcją ``--chart`` – wykres PNG.

Uruchomienie (z katalogu głównego projektu)::

    python -m benchmarks.gil_benchmark --chart
    python -m benchmarks.gil_benchmark --workers 4 --repeats 5 --chart
    python3.13t -m benchmarks.gil_benchmark --chart    # Python bez GIL (free-threading)
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import platform
import random
import statistics
import sys
import sysconfig
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

if __package__ in (None, ""):  # uruchomienie jako skrypt: python benchmarks/gil_benchmark.py
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dupfinder.concurrency.executors import MODES, Job, timed_run  # noqa: E402
from dupfinder.core.hashing import digest_entry  # noqa: E402
from dupfinder.core.models import FileEntry  # noqa: E402
from dupfinder.utils.formatting import human_size  # noqa: E402

MODE_LABELS = {"sequential": "Sekwencyjnie", "threads": "Wątki", "processes": "Procesy"}
RESULTS_DIR = Path(__file__).resolve().parent / "results"


def simulated_network_digest(entry: FileEntry, latency: float) -> str:
    """Symulacja odczytu z wolnego dysku sieciowego: opóźnienie, potem skrót częściowy.

    Zdefiniowana na poziomie modułu, aby dało się ją przekazać do procesów (pickle).
    """
    time.sleep(latency)  # czekanie na „sieć” – w tym czasie GIL jest zwolniony
    return digest_entry(entry, algorithm="sha256", partial=True)


@dataclass
class Scenario:
    key: str
    title: str
    files: int
    file_size: int
    job: Job


@dataclass
class ScenarioResult:
    key: str
    title: str
    files: int
    total_bytes: int
    times: dict[str, float] = field(default_factory=dict)       # mediana [s]
    all_runs: dict[str, list[float]] = field(default_factory=dict)

    def speedup(self, mode: str) -> float:
        return self.times["sequential"] / self.times[mode]


def environment() -> dict[str, object]:
    """Informacje o środowisku – bez nich wyniki pomiarów są nieporównywalne."""
    gil_enabled = sys._is_gil_enabled() if hasattr(sys, "_is_gil_enabled") else True
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "free_threading_build": bool(sysconfig.get_config_var("Py_GIL_DISABLED")),
        "gil_enabled": gil_enabled,
        "switch_interval_s": sys.getswitchinterval(),
        "cpu_count": os.cpu_count(),
        "system": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "processor": platform.processor() or "nieznany",
    }


def result_tag(env: dict[str, object]) -> str:
    """Np. ``py313`` albo ``py313t`` (kompilacja free-threading) – nazwa plików wyników."""
    major, minor, *_ = str(env["python"]).split(".")
    return f"py{major}{minor}{'t' if env['free_threading_build'] else ''}"


def make_files(directory: Path, prefix: str, count: int, size: int,
               rng: random.Random) -> list[FileEntry]:
    """Tworzy ``count`` plików losowej treści i zwraca ich opisy."""
    entries = []
    for i in range(count):
        path = directory / f"{prefix}_{i:03d}.bin"
        path.write_bytes(rng.randbytes(size))
        st = path.stat()
        entries.append(FileEntry(str(path), st.st_size, st.st_mtime))
    return entries


def run_scenario(scenario: Scenario, entries: list[FileEntry], workers: int,
                 repeats: int) -> ScenarioResult:
    """Mierzy scenariusz we wszystkich trybach i sprawdza zgodność wyników."""
    result = ScenarioResult(scenario.key, scenario.title, len(entries),
                            sum(e.size for e in entries))
    reference: list[str | None] | None = None
    for mode in MODES:
        runs = []
        for _ in range(repeats):
            elapsed, outcomes = timed_run(entries, scenario.job, mode, workers)
            runs.append(elapsed)
            digests = [o.digest for o in outcomes]
            if reference is None:
                reference = digests
            elif digests != reference:
                raise RuntimeError(f"{scenario.title}: tryb {mode} dał inne wyniki!")
        result.all_runs[mode] = runs
        result.times[mode] = statistics.median(runs)
        print(f"  {MODE_LABELS[mode]:<13} {result.times[mode]:7.3f} s "
              f"(pomiary: {', '.join(f'{t:.3f}' for t in runs)})", flush=True)
    return result


def markdown_table(results: list[ScenarioResult], env: dict[str, object], workers: int) -> str:
    gil = "wyłączony (free-threading)" if not env["gil_enabled"] else "włączony"
    lines = [
        f"Python {env['python']} ({env['implementation']}), GIL: {gil}, "
        f"rdzenie: {env['cpu_count']}, wątków/procesów: {workers}, system: {env['system']}",
        "",
        "| Scenariusz | Dane | Sekwencyjnie | Wątki | Procesy | Przysp. wątki | Przysp. procesy |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for r in results:
        data = f"{r.files} plików, {human_size(r.total_bytes)}"
        lines.append(
            f"| {r.title} | {data} | {r.times['sequential']:.2f} s | {r.times['threads']:.2f} s | "
            f"{r.times['processes']:.2f} s | {r.speedup('threads'):.2f}× | "
            f"{r.speedup('processes'):.2f}× |"
        )
    return "\n".join(lines) + "\n"


def save_chart(results: list[ScenarioResult], env: dict[str, object], workers: int,
               path: Path) -> bool:
    """Wykres przyspieszenia względem wersji sekwencyjnej (wymaga matplotlib)."""
    try:
        import matplotlib
        matplotlib.use("Agg")  # renderowanie do pliku, bez okna
        import matplotlib.pyplot as plt
    except ImportError:
        print("Brak biblioteki matplotlib – pomijam wykres (pip install matplotlib).")
        return False

    surface, ink, ink2, grid, axis = "#fcfcfb", "#0b0b0b", "#52514e", "#e1e0d9", "#c3c2b7"
    series = {"threads": "#2a78d6", "processes": "#eb6834"}

    from matplotlib.lines import Line2D

    fig, ax = plt.subplots(figsize=(9, 5), dpi=150)
    fig.patch.set_facecolor(surface)
    ax.set_facecolor(surface)
    width = 0.34
    xs = range(len(results))
    for offset, (mode, color) in zip((-width / 2, width / 2), series.items(), strict=True):
        values = [r.speedup(mode) for r in results]
        bars = ax.bar([x + offset for x in xs], values, width, color=color,
                      edgecolor=surface, linewidth=2, label=MODE_LABELS[mode], zorder=3)
        for bar, value in zip(bars, values, strict=True):
            ax.annotate(f"{value:.1f}×", (bar.get_x() + bar.get_width() / 2, bar.get_height()),
                        xytext=(0, 4), textcoords="offset points", ha="center", va="bottom",
                        fontsize=9, color=ink, zorder=4,
                        bbox={"boxstyle": "square,pad=0.15", "fc": surface, "ec": "none"})

    # linie odniesienia: 1× = wersja sekwencyjna, N× = idealne skalowanie
    ax.axhline(1.0, color=ink2, linewidth=1, zorder=2)
    ax.axhline(workers, color=ink2, linewidth=1, linestyle=(0, (4, 4)), zorder=2)
    handles, _ = ax.get_legend_handles_labels()
    handles += [Line2D([], [], color=ink2, linewidth=1, label="sekwencyjnie (1×)"),
                Line2D([], [], color=ink2, linewidth=1, linestyle=(0, (4, 4)),
                       label=f"idealnie ({workers}×)")]
    ax.legend(handles=handles, frameon=False, loc="lower left", bbox_to_anchor=(0, 1.0),
              ncol=4, labelcolor=ink, fontsize=9, handlelength=1.6)

    ax.set_xticks(list(xs), [r.title.replace(" – ", "\n") for r in results], color=ink)
    ax.set_ylabel("przyspieszenie względem wersji sekwencyjnej", color=ink2)
    ax.tick_params(colors=ink2, length=0)
    ax.yaxis.grid(True, color=grid, linewidth=0.8, zorder=0)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(axis)
    top = max(max(r.speedup(m) for r in results for m in series), workers)
    ax.set_ylim(0, top * 1.15)
    gil = "bez GIL" if not env["gil_enabled"] else "z GIL"
    fig.suptitle(f"Python {env['python']} ({gil}) · wątki/procesy: {workers} · "
                 f"rdzenie: {env['cpu_count']}", color=ink, fontsize=11, x=0.02, ha="left")
    fig.tight_layout()
    fig.savefig(path, facecolor=surface)
    plt.close(fig)
    return True


def main(argv: list[str] | None = None) -> int:
    cpus = os.cpu_count() or 2
    parser = argparse.ArgumentParser(description="Benchmark GIL: sekwencyjnie / wątki / procesy")
    parser.add_argument("-w", "--workers", type=int, default=min(cpus, 8),
                        help="liczba wątków i procesów (domyślnie liczba rdzeni, max 8)")
    parser.add_argument("-r", "--repeats", type=int, default=3, help="powtórzenia (mediana)")
    parser.add_argument("--fnv-mb", type=float, default=16,
                        help="łączna ilość danych dla FNV-1a w MiB (czysty Python – wolny)")
    parser.add_argument("--sha-mb", type=float, default=256,
                        help="łączna ilość danych dla SHA-256 w MiB")
    parser.add_argument("--io-files", type=int, default=None,
                        help="liczba plików w scenariuszu I/O (domyślnie 4 × workers)")
    parser.add_argument("--latency", type=float, default=0.05,
                        help="symulowane opóźnienie odczytu w sekundach")
    parser.add_argument("-o", "--output", type=Path, default=RESULTS_DIR)
    parser.add_argument("--chart", action="store_true", help="zapisz wykres PNG (matplotlib)")
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args(argv)
    if args.workers < 1 or args.repeats < 1 or args.latency < 0:
        parser.error("workers i repeats muszą być >= 1, latency >= 0")

    env = environment()
    workers = args.workers
    cpu_files = max(8, 2 * workers)
    io_files = args.io_files or 4 * workers
    scenarios = [
        Scenario("fnv1a", "CPU – czysty Python (FNV-1a)", cpu_files,
                 int(args.fnv_mb * 2**20 / cpu_files),
                 functools.partial(digest_entry, algorithm="fnv1a")),
        Scenario("sha256", "CPU – hashlib SHA-256", cpu_files,
                 int(args.sha_mb * 2**20 / cpu_files),
                 functools.partial(digest_entry, algorithm="sha256")),
        Scenario("io", "I/O – dysk sieciowy (symulacja)", io_files, 64 * 1024,
                 functools.partial(simulated_network_digest, latency=args.latency)),
    ]

    print("Środowisko:", ", ".join(f"{k}={v}" for k, v in env.items()))
    rng = random.Random(args.seed)
    results = []
    with tempfile.TemporaryDirectory(prefix="dupfinder-bench-") as tmp:
        for scenario in scenarios:
            entries = make_files(Path(tmp), scenario.key, scenario.files, scenario.file_size, rng)
            print(f"\n{scenario.title}: {scenario.files} plików × "
                  f"{human_size(scenario.file_size)}", flush=True)
            results.append(run_scenario(scenario, entries, workers, args.repeats))

    table = markdown_table(results, env, workers)
    print("\n" + table)

    args.output.mkdir(parents=True, exist_ok=True)
    tag = result_tag(env)
    (args.output / f"benchmark_{tag}.md").write_text(table, encoding="utf-8")
    payload = {"environment": env, "workers": workers, "repeats": args.repeats,
               "results": [asdict(r) for r in results]}
    (args.output / f"benchmark_{tag}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Zapisano: {args.output / f'benchmark_{tag}.md'}, .json")
    if args.chart and save_chart(results, env, workers, args.output / f"benchmark_{tag}.png"):
        print(f"Wykres:   {args.output / f'benchmark_{tag}.png'}")
    return 0


if __name__ == "__main__":  # konieczne dla ProcessPoolExecutor (Windows/macOS: spawn)
    raise SystemExit(main())
