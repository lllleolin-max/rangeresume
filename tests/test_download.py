import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from rangeresume import DownloadError, ProtocolError, StateError, download
from rangeresume.pilot import ArtifactService, actual_consumer, serve


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = bytes(range(256)) * 12
        self.sha = hashlib.sha256(self.data).hexdigest()
        self.destination = self.root / "artifact.bin"
        self.state = self.root / "state"

    def fetch(self, url, **kwargs):
        return download(url, self.destination, self.state, expected_sha256=self.sha,
                        expected_size=len(self.data), block_size=1024, **kwargs)

    def test_single_range_binding_real_consumer_and_existing_output(self):
        service = ArtifactService(self.data)
        with serve(service) as url:
            result = self.fetch(url)
            self.assertEqual(result["requests"], 3)
            self.assertEqual(service.logs[0]["if_range"], None)
            self.assertEqual(service.logs[1]["if_range"], service.etag)
            self.assertEqual(self.fetch(url)["status"], "ALREADY_PUBLISHED")
        self.assertTrue(actual_consumer(self.destination, self.data)["byte_equal"])

    def test_partial_resume_rechecks_corrupt_block(self):
        service = ArtifactService(self.data)
        with serve(service) as url:
            self.assertEqual(self.fetch(url, max_requests=2)["status"], "PARTIAL")
            (self.state / "blocks/0000000000000000.block").write_bytes(b"x" * 1024)
            result = self.fetch(url)
            self.assertEqual(result["blocks_reused"], 1)
            self.assertEqual(result["requests"], 2)
        self.assertEqual(self.destination.read_bytes(), self.data)

    def test_network_drop_retains_only_confirmed_block(self):
        service = ArtifactService(self.data, fault_after=1536)
        with serve(service) as url:
            with self.assertRaises(DownloadError):
                self.fetch(url)
            self.assertFalse(self.destination.exists())
            recovered = self.fetch(url)
            self.assertEqual(recovered["blocks_reused"], 1)
        self.assertEqual(self.destination.read_bytes(), self.data)

    def test_200_first_complete_allowed_but_resume_200_refused(self):
        service = ArtifactService(self.data, mode="200")
        with serve(service) as url:
            self.assertEqual(self.fetch(url)["status"], "PUBLISHED")
        self.destination.unlink()
        # New state is required after an already-published full response.
        self.state = self.root / "new-state"
        service = ArtifactService(self.data)
        with serve(service) as url:
            self.fetch(url, max_requests=1)
            service.mode = "200"
            with self.assertRaises(ProtocolError):
                self.fetch(url)
        self.assertFalse(self.destination.exists())

    def test_protocol_invalids_no_publication(self):
        for mode in ("weak", "416", "gzip", "range", "short", "long"):
            with self.subTest(mode=mode):
                self.state = self.root / mode
                with serve(ArtifactService(self.data, mode=mode)) as url:
                    with self.assertRaises(DownloadError):
                        self.fetch(url)
                self.assertFalse(self.destination.exists())

    def test_etag_change_rejected_and_trusted_hash_required(self):
        service = ArtifactService(self.data)
        with serve(service) as url:
            self.fetch(url, max_requests=1)
            service.etag = '"changed"'
            with self.assertRaises(ProtocolError):
                self.fetch(url)
        self.state = self.root / "wronghash"
        with serve(ArtifactService(self.data)) as url:
            with self.assertRaises(ProtocolError):
                download(url, self.destination, self.state, expected_sha256="0" * 64,
                         expected_size=len(self.data), block_size=1024)
        self.assertFalse(self.destination.exists())

    def test_path_collision_existing_file_and_input_bounds(self):
        self.destination.write_bytes(b"keep")
        with self.assertRaises(StateError):
            self.fetch("http://127.0.0.1/never")
        self.assertEqual(self.destination.read_bytes(), b"keep")
        for kw in (dict(expected_size=True), dict(block_size=0), dict(max_requests=0), dict(timeout=float("nan"))):
            args = dict(expected_sha256=self.sha, expected_size=len(self.data)) | kw
            with self.assertRaises(DownloadError):
                download("http://127.0.0.1/never", self.root / "new", self.root / "new-state", **args)

    def test_state_destination_inside_state_refused(self):
        with self.assertRaises(StateError):
            download("http://127.0.0.1/never", self.state / "progress.sqlite3", self.state,
                     expected_sha256=self.sha, expected_size=len(self.data))
        self.assertFalse(self.state.exists())

    def test_empty_expected_artifact_needs_no_network(self):
        result = download("http://127.0.0.1:1/unused", self.destination, self.state,
                          expected_sha256=hashlib.sha256(b"").hexdigest(), expected_size=0)
        self.assertEqual(result["requests"], 0)
        self.assertEqual(self.destination.read_bytes(), b"")


if __name__ == "__main__":
    unittest.main()
