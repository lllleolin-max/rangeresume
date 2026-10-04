from contextlib import closing
import hashlib
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

from rangeresume import DownloadError, StateError, download
from rangeresume.pilot import ArtifactService, actual_consumer, serve


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = bytes(range(256)) * 12
        self.sha = hashlib.sha256(self.data).hexdigest()

    def test_parent_kills_real_process_at_block_transaction_and_publication_boundaries(self):
        script = r'''
import importlib,os,sys,time
from pathlib import Path
engine=importlib.import_module('rangeresume.download')
url,destination,state,sha,phase,marker=sys.argv[1:]
def stopped():
    Path(marker).write_text(phase)
    while True:
        time.sleep(.1)
if phase in ('before-block-commit','after-block-commit'):
    original=engine.sqlite3.connect
    class Connection:
        def __init__(self,*args,**kwargs):
            self.db=original(*args,**kwargs)
            self.dirty=False
        def __getattr__(self,name):
            return getattr(self.db,name)
        def execute(self,sql,*args):
            result=self.db.execute(sql,*args)
            if sql.startswith('INSERT INTO blocks'):
                self.dirty=True
                if phase=='before-block-commit':
                    stopped()
            return result
        def commit(self):
            self.db.commit()
            if self.dirty and phase=='after-block-commit':
                stopped()
    engine.sqlite3.connect=Connection
elif phase=='before-publish':
    engine.publish=lambda *args,**kwargs: stopped()
else:
    original=engine.os.link
    def link(*args,**kwargs):
        original(*args,**kwargs)
        stopped()
    engine.os.link=link
engine.download(url,destination,state,expected_sha256=sha,expected_size=3072,block_size=1024)
'''
        service = ArtifactService(self.data)
        with serve(service) as url:
            for phase, rows in (("before-block-commit", 0), ("after-block-commit", 1),
                                ("before-publish", 3), ("after-publish", 3)):
                state = self.root / phase
                output = self.root / (phase + ".bin")
                marker = self.root / (phase + ".marker")
                child = subprocess.Popen([sys.executable, "-c", script, url, str(output),
                                          str(state), self.sha, phase, str(marker)],
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                try:
                    deadline = time.monotonic() + 10
                    while not marker.exists() and child.poll() is None and time.monotonic() < deadline:
                        time.sleep(.01)
                    self.assertTrue(marker.exists(), "child did not reach requested durable boundary")
                    if phase != "after-publish":
                        before_requests = len(service.logs)
                        with self.assertRaises(StateError):
                            download(url, output, state, expected_sha256=self.sha,
                                     expected_size=len(self.data), block_size=1024)
                        self.assertEqual(len(service.logs), before_requests)
                finally:
                    child.kill()
                    stdout, stderr = child.communicate(timeout=5)
                self.assertNotEqual(child.returncode, 0, stderr)
                with closing(sqlite3.connect(state / "progress.sqlite3")) as db:
                    self.assertEqual(db.execute("SELECT count(*) FROM blocks").fetchone()[0], rows)
                self.assertEqual(output.exists(), phase == "after-publish")
                resumed = download(url, output, state, expected_sha256=self.sha,
                                   expected_size=len(self.data), block_size=1024)
                self.assertIn(resumed["status"], ("PUBLISHED", "ALREADY_PUBLISHED"))
                self.assertTrue(actual_consumer(output, self.data)["byte_equal"])

    def test_request_allowance_minus_exact_plus_one_and_expected_size_mismatch(self):
        service = ArtifactService(self.data)
        with serve(service) as url:
            for allowance, status in ((2, "PARTIAL"), (3, "PUBLISHED"), (4, "PUBLISHED")):
                output, state = self.root / str(allowance), self.root / ("state" + str(allowance))
                result = download(url, output, state, expected_sha256=self.sha,
                                  expected_size=len(self.data), block_size=1024, max_requests=allowance)
                self.assertEqual(result["status"], status)
                self.assertEqual(output.exists(), status == "PUBLISHED")
            for size in (len(self.data)-1, len(self.data)+1):
                output, state = self.root / (str(size) + ".bin"), self.root / ("size" + str(size))
                with self.assertRaises(DownloadError):
                    download(url, output, state, expected_sha256=self.sha,
                             expected_size=size, block_size=1024)
                self.assertFalse(output.exists())

    def test_source_state_config_geometry_and_size_bounds(self):
        service = ArtifactService(self.data)
        output, state = self.root / "artifact", self.root / "state"
        with serve(service) as url:
            download(url, output, state, expected_sha256=self.sha, expected_size=len(self.data),
                     block_size=1024, max_requests=1)
            for changes in (dict(block_size=2048), dict(expected_size=len(self.data)+1),
                            dict(expected_sha256="0" * 64)):
                args = dict(block_size=1024, expected_size=len(self.data), expected_sha256=self.sha) | changes
                with self.assertRaises(StateError):
                    download(url, output, state, **args)
        self.assertFalse(output.exists())
        for size in (1024**3 + 1, -1, True):
            with self.assertRaises(DownloadError):
                download("http://localhost:1", output, state, expected_sha256=self.sha, expected_size=size)

    def test_existing_symlink_and_hardlink_state_alias_preserves_user_file(self):
        target = self.root / "user-file"
        target.write_bytes(b"precious")
        state = self.root / "state"
        state.mkdir()
        os.link(target, state / "assembled.part")
        with self.assertRaises(StateError):
            download("http://localhost:1", self.root / "out", state, expected_sha256=self.sha,
                     expected_size=len(self.data))
        self.assertEqual(target.read_bytes(), b"precious")

    def test_symlink_blocks_directory_is_refused(self):
        outside = self.root / "outside"
        outside.mkdir()
        state = self.root / "state"
        state.mkdir()
        try:
            (state / "blocks").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Windows host cannot create symlinks without privileges")
        with self.assertRaises(StateError):
            download("http://localhost:1", self.root / "out", state, expected_sha256=self.sha,
                     expected_size=len(self.data))
        self.assertEqual(list(outside.iterdir()), [])

    def test_completed_blocks_skip_network_and_reverify_before_publish(self):
        # The real process test already recovers this cut; this checks corrupt tail behavior.
        service = ArtifactService(self.data)
        output, state = self.root / "artifact", self.root / "state"
        with serve(service) as url:
            download(url, output, state, expected_sha256=self.sha, expected_size=len(self.data), block_size=1024)
            output.unlink()
            old_count = len(service.logs)
            result = download(url, output, state, expected_sha256=self.sha, expected_size=len(self.data), block_size=1024)
            self.assertEqual(result["requests"], 0)
            self.assertEqual(len(service.logs), old_count)
            self.assertEqual(output.read_bytes(), self.data)


if __name__ == "__main__":
    unittest.main()
