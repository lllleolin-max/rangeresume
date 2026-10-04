"""Independent HTTP response fixture, separate from the pilot's range parser."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest

from rangeresume import DownloadError, download


class ProtocolBoundaryTests(unittest.TestCase):
    def test_duplicate_headers_transfer_encoding_redirect_and_bad_content_range(self):
        data = b"a" * 1024
        cases = {
            "duplicate-etag": [("ETag", '"v1"')],
            "duplicate-length": [("Content-Length", "1024")],
            "duplicate-range": [("Content-Range", "bytes 0-1023/1024")],
            "chunked": [("Transfer-Encoding", "chunked")],
            "unknown-total": [("Content-Range", "bytes 0-1023/*")],
            "invalid-unit": [("Content-Range", "items 0-1023/1024")],
            "redirect": [("Location", "http://127.0.0.1:1/unwanted")],
        }
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for label, headers in cases.items():
                with self.subTest(case=label):
                    requests = []

                    class Response(BaseHTTPRequestHandler):
                        def log_message(self, *_):
                            pass

                        def do_GET(self):
                            requests.append(dict(self.headers))
                            self.send_response(302 if label == "redirect" else 206)
                            self.send_header("ETag", '"v1"')
                            self.send_header("Content-Length", "1024")
                            if label not in ("unknown-total", "invalid-unit"):
                                self.send_header("Content-Range", "bytes 0-1023/1024")
                            for name, value in headers:
                                self.send_header(name, value)
                            self.end_headers()
                            try:
                                self.wfile.write(data)
                            except OSError:
                                pass

                    server = ThreadingHTTPServer(("127.0.0.1", 0), Response)
                    worker = threading.Thread(target=server.serve_forever, daemon=True)
                    worker.start()
                    try:
                        with self.assertRaises(DownloadError):
                            download(f"http://127.0.0.1:{server.server_port}/artifact", root / label,
                                     root / (label + "-state"), expected_sha256=hashlib.sha256(data).hexdigest(),
                                     expected_size=1024, block_size=1024)
                        self.assertEqual(len(requests), 1)
                        self.assertEqual(requests[0]["Range"], "bytes=0-1023")
                        self.assertFalse((root / label).exists())
                    finally:
                        server.shutdown()
                        server.server_close()
                        worker.join()


if __name__ == "__main__":
    unittest.main()
