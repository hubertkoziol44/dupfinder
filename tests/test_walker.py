"""Testy generatorów przeglądających katalogi."""

import os
import tempfile
import types
import unittest
from pathlib import Path

from dupfinder.core.patterns import DEFAULT_EXCLUDES, compile_patterns
from dupfinder.core.walker import (filter_files, group_by, only_duplicates, unique_files,
                                   walk_files)


class WalkerTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        for rel, size in {"a.txt": 10, "sub/b.jpg": 20, "sub/deep/c.jpg": 30,
                          ".git/obj": 40, "sub/tmp.tmp": 50, "empty.txt": 0}.items():
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x" * size)

    def tearDown(self):
        self._tmp.cleanup()

    def rel(self, entries):
        return sorted(Path(e.path).relative_to(self.root).as_posix() for e in entries)

    def test_walk_is_lazy_generator(self):
        gen = walk_files(self.root)
        self.assertIsInstance(gen, types.GeneratorType)

    def test_walk_finds_all_files_recursively(self):
        self.assertEqual(self.rel(walk_files(self.root)),
                         [".git/obj", "a.txt", "empty.txt", "sub/b.jpg", "sub/deep/c.jpg",
                          "sub/tmp.tmp"])

    def test_default_excludes(self):
        self.assertEqual(self.rel(walk_files(self.root, DEFAULT_EXCLUDES)),
                         ["a.txt", "empty.txt", "sub/b.jpg", "sub/deep/c.jpg"])

    def test_exclude_uses_relative_path(self):
        exclude = compile_patterns([r"^sub(/|$)"])
        self.assertEqual(self.rel(walk_files(self.root, exclude)),
                         [".git/obj", "a.txt", "empty.txt"])

    @unittest.skipUnless(hasattr(os, "symlink"), "brak dowiązań symbolicznych")
    def test_symlinks_are_skipped(self):
        try:
            os.symlink(self.root, self.root / "sub" / "petla", target_is_directory=True)
        except OSError:
            self.skipTest("system nie pozwala tworzyć dowiązań")
        self.assertEqual(len(list(walk_files(self.root))), 6)

    def test_missing_directory_is_recorded_as_error(self):
        errors = []
        self.assertEqual(list(walk_files(self.root / "nie_ma", errors=errors)), [])
        self.assertEqual(len(errors), 1)

    def test_unique_files_skips_overlapping_roots(self):
        entries = list(unique_files([self.root, self.root / "sub"], DEFAULT_EXCLUDES))
        self.assertEqual(len(entries), 4)

    def test_filter_files(self):
        entries = list(walk_files(self.root, DEFAULT_EXCLUDES))
        self.assertEqual(self.rel(filter_files(entries)),  # domyślnie pomija puste
                         ["a.txt", "sub/b.jpg", "sub/deep/c.jpg"])
        self.assertEqual(self.rel(filter_files(entries, min_size=15, max_size=25)),
                         ["sub/b.jpg"])
        jpg = compile_patterns([r"\.jpe?g$"])
        self.assertEqual(self.rel(filter_files(entries, include=jpg)),
                         ["sub/b.jpg", "sub/deep/c.jpg"])

    def test_group_by_and_only_duplicates(self):
        groups = group_by(["aa", "b", "cc", "dd", "e"], key=len)
        self.assertEqual(groups, {2: ["aa", "cc", "dd"], 1: ["b", "e"]})
        self.assertEqual(dict(only_duplicates({1: ["x"], 2: ["y", "z"]})), {2: ["y", "z"]})


if __name__ == "__main__":
    unittest.main()
