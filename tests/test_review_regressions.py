from contextlib import closing
import hashlib
import importlib
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from rangeresume import StateError, download
from rangeresume.pilot import ArtifactService, serve


class ReviewRegressions(unittest.TestCase):
    def test_expired_resume_verification_allowance_cannot_publish(self):
        engine = importlib.import_module("rangeresume.download")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = b"a" * 3072
            state = root / "state"
            args = dict(expected_sha256=hashlib.sha256(data).hexdigest(), expected_size=len(data), block_size=1024)
            with serve(ArtifactService(data)) as url:
                with patch.object(engine, "publish", side_effect=OSError("simulated publication interruption")):
                    with self.assertRaises(OSError):
                        download(url, root / "artifact", state, **args)
                original = engine.file_hash

                def slow_read(path):
                    result = original(path)  # actual disk bytes are still fully hashed
                    time.sleep(0.03)  # deterministic model of slow local storage
                    return result

                with patch.object(engine, "file_hash", slow_read):
                    result = download(url, root / "artifact", state, max_seconds=0.01, **args)
                self.assertEqual(result["status"], "PARTIAL")
                self.assertFalse((root / "artifact").exists())

    def test_confirmed_blocks_without_validator_are_not_rebound(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state = root / "state"
            data = b"a" * 3072
            server = ArtifactService(data)
            args = dict(expected_sha256=hashlib.sha256(data).hexdigest(), expected_size=len(data), block_size=1024)
            with serve(server) as url:
                download(url, root / "artifact", state, max_requests=1, **args)
                with closing(sqlite3.connect(state / "progress.sqlite3")) as db:
                    db.execute("DELETE FROM settings WHERE name='etag'")
                    db.commit()
                server.etag = '"new-validator-same-bytes"'
                before_requests = len(server.logs)
                with self.assertRaises(StateError):
                    download(url, root / "artifact", state, **args)
                self.assertEqual(len(server.logs), before_requests)
            self.assertFalse((root / "artifact").exists())

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
