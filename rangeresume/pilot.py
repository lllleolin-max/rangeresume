"""Independent byte-counting local HTTP artifact application and fair curl workflow."""
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.request

from .download import DownloadError, download


class ArtifactService:
    def __init__(self, data, *, fault_after=None, mode="normal"):
        self.data = data
        self.etag = '"' + hashlib.sha256(data).hexdigest() + '"'
        self.fault_after = fault_after
        self.mode = mode
        self.logs = []
        self.lock = threading.Lock()
        self.total_sent = 0
        self.fired = False

    def handler(self):
        service = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_):
                pass

            def do_GET(self):
                start, end = 0, len(service.data) - 1
                range_header = self.headers.get("Range")
                conditional = self.headers.get("If-Range")
                status = 200
                if range_header and (conditional is None or conditional == service.etag):
                    match = re.fullmatch(r"bytes=([0-9]+)-([0-9]*)", range_header)
                    if not match:
                        self.send_error(400)
                        return
                    start = int(match[1])
                    end = min(int(match[2]) if match[2] else end, end)
                    status = 206 if start <= end else 416
                if service.mode == "200":
                    start, end, status = 0, len(service.data) - 1, 200
                if service.mode == "416":
                    status = 416
                body = service.data[start:end + 1] if status != 416 else b""
                self.send_response(status)
                self.send_header("Connection", "close")
                self.close_connection = True
                self.send_header("ETag", 'W/' + service.etag if service.mode == "weak" else service.etag)
                length = len(body) + (1 if service.mode == "long" else 0)
                self.send_header("Content-Length", str(length))
                if service.mode == "gzip":
                    self.send_header("Content-Encoding", "gzip")
                if status == 206:
                    first = start + 1 if service.mode == "range" else start
                    self.send_header("Content-Range", f"bytes {first}-{end}/{len(service.data)}")
                if status == 416:
                    self.send_header("Content-Range", f"bytes */{len(service.data)}")
                self.end_headers()
                with service.lock:
                    cut = len(body)
                    disconnect = False
                    if service.fault_after is not None and not service.fired and service.total_sent + cut > service.fault_after:
                        cut = max(0, service.fault_after - service.total_sent)
                        service.fired = True
                        disconnect = True
                    if service.mode == "short":
                        cut = max(0, cut - 1)
                        disconnect = True
                    service.total_sent += cut
                    service.logs.append(dict(range=range_header, if_range=conditional,
                                             accept_encoding=self.headers.get("Accept-Encoding"),
                                             status=status, attempted_body_bytes=cut))
                try:
                    self.wfile.write(body[:cut])
                    self.wfile.flush()
                except OSError:
                    pass
                if disconnect:
                    try:
                        self.connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    self.close_connection = True
        return Handler


@contextmanager
def serve(service):
    server = ThreadingHTTPServer(("127.0.0.1", 0), service.handler())
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/artifact.bin"
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def actual_consumer(path, data):
    raw = Path(path).read_bytes()
    # Hashlib + byte equality independent of downloader's file_hash helper.
    if raw != data or hashlib.sha256(raw).hexdigest() != hashlib.sha256(data).hexdigest():
        raise AssertionError("actual artifact consumer differs from source bytes")
    return dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), byte_equal=True)


def compare(root, data, fault_after):
    records = []
    sha = hashlib.sha256(data).hexdigest()
    for policy in ("full-redownload", "rangeresume", "curl-verified-resume"):
        directory = root / policy
        directory.mkdir()
        service = ArtifactService(data, fault_after=fault_after)
        destination = directory / "artifact.bin"
        attempts = []
        diagnostic_seconds = 0
        started = time.perf_counter()
        with serve(service) as url:
            ready = time.perf_counter()
            if policy == "rangeresume":
                for _ in range(2):
                    try:
                        attempts.append(download(url, destination, directory / "state", expected_sha256=sha,
                                                 expected_size=len(data), block_size=1024))
                        break
                    except DownloadError as exc:
                        attempts.append(dict(error=type(exc).__name__))
            elif policy == "full-redownload":
                for _ in range(2):
                    try:
                        with urllib.request.urlopen(url, timeout=5) as response:
                            body = response.read()
                        if len(body) != len(data) or hashlib.sha256(body).hexdigest() != sha:
                            raise ValueError("bad artifact")
                        destination.write_bytes(body)
                        attempts.append(dict(verified=True))
                        break
                    except Exception as exc:
                        attempts.append(dict(error=type(exc).__name__))
            else:
                executable = shutil.which("curl.exe") or shutil.which("curl")
                if not executable:
                    records.append(dict(policy=policy, skipped="curl executable absent"))
                    continue
                diagnostic_started = time.perf_counter()
                version = subprocess.run([executable, "--version"], capture_output=True, check=True).stdout.decode()
                diagnostic_seconds = time.perf_counter() - diagnostic_started
                part = directory / "curl.part"
                headers_file = directory / "response-headers.txt"
                stored_etag = None
                for retry in range(2):
                    args = [executable, "--silent", "--show-error", "--fail", "--noproxy", "*",
                            "--header", "Accept-Encoding: identity", "--max-time", "5", "--output", str(part),
                            "--dump-header", str(headers_file)]
                    if retry and part.exists():
                        if stored_etag is None:
                            raise ValueError("curl fixture response did not supply a strong ETag for reuse")
                        args += ["--continue-at", "-", "--header", "If-Range: " + stored_etag]
                    args += [url]
                    result = subprocess.run(args, capture_output=True)
                    response_headers = headers_file.read_text(encoding="ascii")
                    if retry == 0:
                        validators = [line.split(":", 1)[1].strip() for line in response_headers.splitlines()
                                      if line.lower().startswith("etag:")]
                        if len(validators) == 1 and re.fullmatch(r'"[\x21\x23-\x7e]{0,200}"', validators[0]):
                            stored_etag = validators[0]
                    attempts.append(dict(argv=args, exit_code=result.returncode,
                                         stderr=result.stderr.decode("utf-8", "replace"), response_headers=response_headers))
                    if result.returncode == 0 and part.stat().st_size == len(data) and hashlib.sha256(part.read_bytes()).hexdigest() == sha:
                        part.rename(destination)
                        break
                attempts.append(dict(curl_version=version))
            finished = time.perf_counter()
        consuming = time.perf_counter()
        consumed = actual_consumer(destination, data)
        records.append(dict(policy=policy, fault_after=fault_after, attempts=attempts,
                            server_requests=len(service.logs), server_attempted_bytes=service.total_sent,
                            server_log=service.logs, elapsed_seconds=time.perf_counter() - started,
                            workflow_seconds=finished - ready - diagnostic_seconds,
                            diagnostic_seconds=diagnostic_seconds, fixture_setup_seconds=ready - started,
                            consumer_seconds=time.perf_counter() - consuming,
                            consumer=consumed, synthetic=True))
    return records


def run_pilot(output):
    root = Path(output).resolve()
    root.mkdir(exist_ok=False)
    data = bytes((i * 17 + i // 37) % 256 for i in range(8192))
    (root / "original.bin").write_bytes(data)
    scenarios = []
    for label, content, fault in (("interrupted", data, 1536), ("small-clean", data[:512], None)):
        directory = root / label
        directory.mkdir()
        scenarios.append(dict(case=label, results=compare(directory, content, fault)))
    result = dict(format="rangeresume-pilot/v1", synthetic=True, scenarios=scenarios)
    (root / "pilot.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result
