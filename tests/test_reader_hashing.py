"""Testy iteratora ChunkReader i funkcji liczących skróty."""

import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from dupfinder.core.hashing import (FNV1a64, file_digest, new_hasher, partial_digest)
from dupfinder.core.reader import ChunkReader


class TempDirTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def make(self, name, data):
        path = self.dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path


class ChunkReaderTest(TempDirTestCase):
    def test_reads_whole_file_in_chunks(self):
        data = os.urandom(10_000)
        path = self.make("a.bin", data)
        chunks = list(ChunkReader(path, chunk_size=4096))
        self.assertEqual([len(c) for c in chunks], [4096, 4096, 1808])
        self.assertEqual(b"".join(chunks), data)

    def test_iterator_protocol(self):
        path = self.make("a.bin", b"abcdef")
        reader = ChunkReader(path, chunk_size=4)
        self.assertIs(iter(reader), reader)
        self.assertEqual(next(reader), b"abcd")
        self.assertEqual(next(reader), b"ef")
        with self.assertRaises(StopIteration):
            next(reader)
        with self.assertRaises(StopIteration):  # wyczerpany iterator pozostaje wyczerpany
            next(reader)
        self.assertEqual(reader.bytes_read, 6)

    def test_offset_and_limit(self):
        path = self.make("a.bin", bytes(range(100)))
        self.assertEqual(b"".join(ChunkReader(path, 7, offset=10, limit=15)), bytes(range(10, 25)))
        self.assertEqual(b"".join(ChunkReader(path, 7, offset=95, limit=50)), bytes(range(95, 100)))

    def test_empty_file(self):
        path = self.make("empty", b"")
        self.assertEqual(list(ChunkReader(path)), [])

    def test_file_closed_after_break(self):
        path = self.make("a.bin", b"x" * 100)
        with ChunkReader(path, 10) as reader:
            next(reader)
            self.assertIsNotNone(reader._file)
        self.assertIsNone(reader._file)

    def test_missing_file_raises_on_first_next(self):
        reader = ChunkReader(self.dir / "brak.bin")  # utworzenie jest leniwe
        with self.assertRaises(FileNotFoundError):
            next(reader)

    def test_invalid_arguments(self):
        for kwargs in ({"chunk_size": 0}, {"offset": -1}, {"limit": -1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                ChunkReader("x", **kwargs)


class HashingTest(TempDirTestCase):
    def test_file_digest_matches_hashlib(self):
        data = os.urandom(3 * 1024 * 1024 + 17)
        path = self.make("big.bin", data)
        for algorithm in ("sha256", "md5", "blake2b"):
            with self.subTest(algorithm=algorithm):
                self.assertEqual(file_digest(str(path), algorithm),
                                 hashlib.new(algorithm, data).hexdigest())

    def test_fnv1a_known_vectors(self):
        cases = {b"": "cbf29ce484222325", b"a": "af63dc4c8601ec8c",
                 b"foobar": "85944171f73967e8"}
        for data, expected in cases.items():
            with self.subTest(data=data):
                h = FNV1a64()
                h.update(data)
                self.assertEqual(h.hexdigest(), expected)

    def test_fnv1a_incremental_update(self):
        whole, parts = FNV1a64(), FNV1a64()
        whole.update(b"hello world")
        parts.update(b"hello ")
        parts.update(b"world")
        self.assertEqual(whole.hexdigest(), parts.hexdigest())

    def test_unknown_algorithm(self):
        with self.assertRaises(ValueError):
            new_hasher("crc99")

    def test_partial_digest_ignores_middle(self):
        base = bytearray(os.urandom(100_000))
        a = self.make("a.bin", bytes(base))
        base[50_000] ^= 0xFF
        b = self.make("b.bin", bytes(base))
        size = len(base)
        self.assertEqual(partial_digest(str(a), size), partial_digest(str(b), size))
        self.assertNotEqual(file_digest(str(a)), file_digest(str(b)))

    def test_partial_digest_of_small_file_equals_full(self):
        path = self.make("small.txt", b"krotki plik" * 100)
        size = path.stat().st_size
        self.assertEqual(partial_digest(str(path), size), file_digest(str(path)))

    def test_missing_file_is_not_retried(self):
        with self.assertRaises(FileNotFoundError):
            file_digest(str(self.dir / "brak.bin"))


if __name__ == "__main__":
    unittest.main()
