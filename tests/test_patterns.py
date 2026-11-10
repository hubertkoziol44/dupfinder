"""Testy wyrażeń regularnych."""

import unittest

from dupfinder.core.patterns import (COPY_NAME_RE, DEFAULT_EXCLUDES, compile_patterns,
                                     looks_like_copy, matches_any, parse_copy_name,
                                     parse_size)


class ParseSizeTest(unittest.TestCase):
    def test_valid_sizes(self):
        cases = {
            "0": 0, "500": 500, "7b": 7, "10KB": 10 * 1024, "10kb": 10 * 1024,
            "10 KiB": 10 * 1024, "1.5MB": int(1.5 * 1024**2), "1,5 MiB": int(1.5 * 1024**2),
            "2G": 2 * 1024**3, " 3 t ": 3 * 1024**4,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parse_size(text), expected)

    def test_invalid_sizes(self):
        for text in ("", "abc", "10 XB", "1.2.3", "-5", "10ib", "MB"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_size(text)


class CopyNameTest(unittest.TestCase):
    def test_recognized_copies(self):
        cases = {
            "zdjecie (1).jpg": ("zdjecie", 1, ".jpg"),
            "raport - Kopia.docx": ("raport", 1, ".docx"),
            "raport - Kopia (2).docx": ("raport", 2, ".docx"),
            "notatki copy 2.txt": ("notatki", 2, ".txt"),
            "photo - Copy (3).png": ("photo", 3, ".png"),
            "archiwum (1).tar.gz": ("archiwum", 1, ".tar.gz"),
            "README (4)": ("README", 4, ""),
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(tuple(parse_copy_name(name)), expected)

    def test_not_copies(self):
        for name in ("IMG_0001.jpg", "copywriting.txt", "data-copy-final.txt", "raport.docx"):
            with self.subTest(name=name):
                self.assertFalse(looks_like_copy(name))

    def test_named_groups(self):
        match = COPY_NAME_RE.fullmatch("film - Kopia (7).mp4")
        self.assertIsNotNone(match)
        self.assertEqual(match["stem"], "film")
        self.assertEqual(match["marker"], "Kopia")
        self.assertEqual(match["copy_no"], "7")
        self.assertEqual(match["ext"], ".mp4")

    def test_works_with_directories_in_path(self):
        self.assertTrue(looks_like_copy("katalog (1)/plik (2).txt"))
        self.assertFalse(looks_like_copy("katalog (1)/plik.txt"))


class ExcludePatternsTest(unittest.TestCase):
    def test_default_excludes(self):
        for path in (".git/config", "a/node_modules/x.js", "__pycache__/m.pyc",
                     "dane/plik.tmp", "plik.txt~", "film.mp4.part"):
            with self.subTest(path=path):
                self.assertTrue(matches_any(path, DEFAULT_EXCLUDES))
        for path in ("gitx/a.txt", "moj.git.txt", "dane/plik.txt", "tmp/plik.txt"):
            with self.subTest(path=path):
                self.assertFalse(matches_any(path, DEFAULT_EXCLUDES))

    def test_invalid_user_pattern_gives_value_error(self):
        with self.assertRaisesRegex(ValueError, "Nieprawidłowe wyrażenie"):
            compile_patterns(["(niezamkniety"])


if __name__ == "__main__":
    unittest.main()
