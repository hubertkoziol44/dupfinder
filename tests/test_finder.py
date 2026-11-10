"""Testy integracyjne: DuplicateFinder, współbieżność, raporty, CLI."""

import contextlib
import csv
import functools
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path

from dupfinder.cli import main
from dupfinder.concurrency.executors import MODES, ProgressTracker, hash_many
from dupfinder.core.finder import DuplicateFinder
from dupfinder.core.hashing import digest_entry
from dupfinder.core.models import DuplicateGroup, FileEntry
from scripts.generate_test_data import generate


class ProgressTrackerTest(unittest.TestCase):
    def test_no_lost_updates_with_many_threads(self):
        tracker = ProgressTracker()

        def work():
            for _ in range(20_000):
                tracker.update(1)

        threads = [threading.Thread(target=work) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(tracker.snapshot(), (160_000, 160_000, 0))


class GeneratedDataTest(unittest.TestCase):
    """Porównanie wyników z listą oczekiwanych grup z generatora danych."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name) / "data"
        cls.info = generate(cls.root, seed=7, scale=1, big_mb=1)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def groups_as_rel_paths(self, result):
        return sorted(sorted(Path(f.path).relative_to(self.root).as_posix() for f in g)
                      for g in result)

    def test_all_modes_find_expected_groups(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                result = DuplicateFinder([self.root], mode=mode, workers=2).scan()
                self.assertEqual(self.groups_as_rel_paths(result), self.info.expected_groups)

    def test_pure_python_algorithm_gives_same_groups(self):
        result = DuplicateFinder([self.root], algorithm="fnv1a", mode="threads").scan()
        self.assertEqual(self.groups_as_rel_paths(result), self.info.expected_groups)

    def test_files_differing_only_in_middle_are_not_duplicates(self):
        result = DuplicateFinder([self.root], mode="sequential").scan()
        groups = self.groups_as_rel_paths(result)
        flat = {p for g in groups for p in g}
        self.assertNotIn("pulapki/rozny_srodek_1.dat", flat)
        self.assertIn(["pulapki/rozny_srodek_0 - Kopia.dat", "pulapki/rozny_srodek_0.dat"],
                      groups)

    def test_original_is_file_without_copy_marker(self):
        result = DuplicateFinder([self.root], mode="sequential").scan()
        for group in result:
            self.assertNotRegex(group.original.name, r"\(\d+\)|Kopia|copy")

    def test_include_and_min_size(self):
        result = DuplicateFinder([self.root], include=[r"\.jpg$"], min_size="100KB",
                                 mode="threads").scan()
        self.assertTrue(len(result) > 0)
        for group in result:
            self.assertGreaterEqual(group.size, 100 * 1024)
            self.assertTrue(all(f.name.endswith(".jpg") for f in group))

    def test_without_default_excludes_finds_git_duplicates(self):
        result = DuplicateFinder([self.root], use_default_excludes=False,
                                 mode="sequential").scan()
        self.assertEqual(len(result), len(self.info.expected_groups) + 1)

    def test_export_reports(self):
        finder = DuplicateFinder([self.root], mode="threads")
        with self.assertRaises(RuntimeError):  # @requires_result
            finder.export(self.root.parent / "raporty")
        result = finder.scan()
        with tempfile.TemporaryDirectory() as out:
            paths = finder.export(out)
            self.assertEqual(sorted(p.suffix for p in paths), [".csv", ".json", ".txt"])
            data = json.loads((Path(out) / "duplikaty.json").read_text(encoding="utf-8"))
            self.assertEqual(data["summary"]["groups"], len(result))
            with open(Path(out) / "duplikaty.csv", encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), sum(len(g) for g in result))

    def test_cli_end_to_end(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main([str(self.root), "--mode", "threads", "--top", "3"])
        self.assertEqual(code, 0)
        self.assertIn(f"Grupy duplikatów: {len(self.info.expected_groups)}", out.getvalue())


class FinderValidationTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_property_setters_validate(self):
        finder = DuplicateFinder([self.dir])
        finder.min_size = "2KB"
        self.assertEqual(finder.min_size, 2048)
        for attr, value in (("min_size", -1), ("min_size", "dużo"), ("mode", "gpu"),
                            ("workers", 0), ("algorithm", "crc99"), ("max_size", 10)):
            with self.subTest(attr=attr, value=value), self.assertRaises(ValueError):
                setattr(finder, attr, value)

    def test_missing_root(self):
        with self.assertRaises(FileNotFoundError):
            DuplicateFinder([Path(self.dir) / "nie_ma"])

    def test_result_requires_scan(self):
        finder = DuplicateFinder([self.dir])
        with self.assertRaises(RuntimeError):
            _ = finder.result
        finder.scan()
        self.assertEqual(len(finder.result), 0)

    def test_cli_errors_return_nonzero(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(main([str(Path(self.dir) / "nie_ma")]), 1)
            with self.assertRaises(SystemExit) as ctx:
                main([self.dir, "--min-size", "10XB"])
        self.assertEqual(ctx.exception.code, 2)

    def test_unreadable_file_is_reported_not_fatal(self):
        path = Path(self.dir) / "znika.bin"
        path.write_bytes(b"abc")
        entry = FileEntry(str(path), 3, 0.0)
        path.unlink()  # plik zniknął między skanowaniem a liczeniem skrótu
        job = functools.partial(digest_entry, algorithm="sha256")
        for mode in MODES:
            with self.subTest(mode=mode):
                [outcome] = hash_many([entry], job, mode=mode, workers=1)
                self.assertIsNone(outcome.digest)
                self.assertIn("FileNotFoundError", outcome.error)

    def test_duplicate_group_needs_two_files(self):
        with self.assertRaises(ValueError):
            DuplicateGroup("abc", 1, [FileEntry("a", 1, 0.0)])


if __name__ == "__main__":
    unittest.main()
