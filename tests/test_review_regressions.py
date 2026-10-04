from contextlib import closing
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest

from rangeresume import StateError, download
from rangeresume.pilot import ArtifactService, serve


class ReviewRegressions(unittest.TestCase):
    def test_empty_unowned_journal_does_not_authorize_staging_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state = root / "state"
            state.mkdir()
            with closing(sqlite3.connect(state / "progress.sqlite3")):
                pass
            keep = state / "assembled.part"
            keep.write_bytes(b"unrelated precious bytes")
            data = b"a" * 2048
            with serve(ArtifactService(data, mode="200")) as url:
                with self.assertRaises(StateError):
                    download(url, root / "artifact", state,
                             expected_sha256=hashlib.sha256(data).hexdigest(), expected_size=len(data),
                             block_size=1024)
            self.assertEqual(keep.read_bytes(), b"unrelated precious bytes")
            self.assertFalse((root / "artifact").exists())


if __name__ == "__main__":
    unittest.main()
