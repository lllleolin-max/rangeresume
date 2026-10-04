"""HTTP identity single-range downloader. Trusted expected digest is mandatory."""
from __future__ import annotations

from contextlib import closing, contextmanager
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import urlsplit


class DownloadError(ValueError):
    """Safe refusal or incomplete HTTP transfer; confirmed blocks remain reusable."""


class ProtocolError(DownloadError):
    """Server response does not match the supported representation/range contract."""


class StateError(DownloadError):
    """Unsafe or conflicting state/output paths or a corrupt journal."""


def integer(value, name, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise DownloadError(f"{name} must be an integer in [{minimum},{maximum}]")
    return value


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while block := stream.read(65536):
            digest.update(block)
    return digest.hexdigest()


def etag(value):
    if type(value) is not str or not re.fullmatch(r'"[\x21\x23-\x7e]{0,200}"', value):
        raise ProtocolError("a bounded ASCII strong quoted ETag is required")
    return value


def one_header(response, name, optional=False):
    values = response.headers.get_all(name, [])
    if len(values) != 1:
        if optional and not values:
            return None
        raise ProtocolError(f"exactly one {name} response header is required")
    return values[0]


@contextmanager
def process_lock(path):
    with Path(path).open("a+b") as lock:
        lock.seek(0)
        if os.name == "nt":
            import msvcrt
            if lock.read(1) == b"":
                lock.write(b"0")
                lock.flush()
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise StateError("another process owns this state directory") from exc
        else:
            import fcntl
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise StateError("another process owns this state directory") from exc
        try:
            yield
        finally:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


def fsync_directory(path):
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def safe_path(path):
    path = Path(path).absolute()
    if path.is_symlink():
        raise StateError("symlink final paths are unsupported")
    return path.resolve()


def open_journal(state, config):
    path = state / "progress.sqlite3"
    if path.is_symlink() or (path.exists() and path.stat().st_nlink != 1):
        raise StateError("symlink state files are unsupported")
    db = sqlite3.connect(path, timeout=5, isolation_level=None)
    try:
        db.execute("PRAGMA synchronous=FULL")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            raise StateError("unsupported progress schema")
        if version == 0 and db.execute("SELECT 1 FROM sqlite_schema WHERE type='table'").fetchone():
            raise StateError("unversioned state contains unrelated database tables")
        db.execute("BEGIN IMMEDIATE")
        db.execute("CREATE TABLE IF NOT EXISTS settings(name TEXT PRIMARY KEY,value TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS blocks(offset INTEGER PRIMARY KEY,length INTEGER NOT NULL,sha256 TEXT NOT NULL)")
        prior = db.execute("SELECT value FROM settings WHERE name='config'").fetchone()
        if prior and prior[0] != canonical(config):
            raise StateError("state belongs to a different URL/destination/digest/size/block geometry")
        if not prior:
            db.execute("INSERT INTO settings VALUES('config',?)", (canonical(config),))
        db.execute("PRAGMA user_version=1")
        db.commit()
        return db
    except BaseException:
        db.rollback()
        db.close()
        raise


def block_path(state, offset):
    return state / "blocks" / f"{offset:016x}.block"


def recover(db, state, size, block_size):
    valid = set()
    rows = db.execute("SELECT offset,length,sha256 FROM blocks ORDER BY offset").fetchall()
    for offset, length, sha256 in rows:
        if (type(offset) is not int or not 0 <= offset < size or offset % block_size
                or length != min(block_size, size - offset)
                or type(sha256) is not str or not re.fullmatch("[0-9a-f]{64}", sha256)):
            raise StateError("block journal geometry or digest is corrupt")
        path = block_path(state, offset)
        if path.is_symlink() or (path.exists() and path.stat().st_nlink != 1):
            raise StateError("symlink block files are unsupported")
        if path.is_file() and path.stat().st_size == length and file_hash(path) == sha256:
            valid.add(offset)
        else:
            db.execute("DELETE FROM blocks WHERE offset=?", (offset,))
            if path.exists():
                path.unlink()
    return valid


def receive(response, target, expected, deadline):
    seen = 0
    hasher = hashlib.sha256()
    with target.open("wb") as stream:
        while seen < expected:
            if time.monotonic() >= deadline:
                raise DownloadError("transfer deadline exhausted; confirmed blocks retained")
            body = response.read(min(65536, expected - seen))
            if not body:
                raise ProtocolError("response body is truncated")
            seen += len(body)
            stream.write(body)
            hasher.update(body)
        if response.read(1):
            raise ProtocolError("response body exceeds declared range length")
        stream.flush()
        os.fsync(stream.fileno())
    return hasher.hexdigest(), seen


def publish(assembly, destination, expected_sha256, expected_size):
    if assembly.stat().st_size != expected_size or file_hash(assembly) != expected_sha256:
        raise ProtocolError("complete artifact does not match trusted expected SHA256 and size")
    # Hard-link publication creates one complete name atomically and refuses an
    # existing destination on Windows/POSIX. Unsupported/cross-volume fails closed.
    os.link(assembly, destination)
    fsync_directory(destination.parent)
    return dict(status="PUBLISHED", destination=str(destination), size=expected_size,
                sha256=expected_sha256)


def download(url, destination, state, *, expected_sha256, expected_size,
             block_size=262144, max_requests=256, max_seconds=60, timeout=10):
    """Recover verified blocks for one representation and atomically publish the artifact.

    A trusted expected SHA256/size is required. Reuse requires the identical URL,
    destination, digest, size and block geometry. No redirects, proxy, credentials,
    compression, automatic network retries, multi-range or live state writers.
    """
    if type(url) is not str or len(url) > 8192 or any(ord(c) < 33 or ord(c) > 126 for c in url):
        raise DownloadError("URL must be bounded visible ASCII")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise DownloadError("invalid URL authority") from exc
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username is not None or parsed.fragment:
        raise DownloadError("HTTP(S) URL without credentials or fragment is required")
    if type(expected_sha256) is not str or not re.fullmatch("[0-9a-f]{64}", expected_sha256):
        raise DownloadError("trusted expected_sha256 must be lowercase 64-digit hex")
    integer(expected_size, "expected_size", 0, 1024**3)
    integer(block_size, "block_size", 1024, 8 * 1024 * 1024)
    integer(max_requests, "max_requests", 1, 65536)
    if (expected_size + block_size - 1) // block_size > 65536:
        raise DownloadError("artifact exceeds the 65536-block limit")
    for name, value in (("max_seconds", max_seconds), ("timeout", timeout)):
        if type(value) not in (int, float) or not 0.01 <= value <= 3600 or not math.isfinite(value):
            raise DownloadError(f"{name} must be finite and in [0.01,3600]")
    destination, state = safe_path(destination), safe_path(state)
    if destination == state or state in destination.parents:
        raise StateError("destination must be outside the state directory")
    if not destination.parent.is_dir():
        raise StateError("destination parent must already exist")
    if destination.exists():
        if destination.is_file() and destination.stat().st_size == expected_size and file_hash(destination) == expected_sha256:
            return dict(status="ALREADY_PUBLISHED", destination=str(destination), size=expected_size,
                        sha256=expected_sha256, requests=0, bytes_received=0, blocks_reused=0)
        raise StateError("existing destination differs; refusing overwrite")
    if state.exists():
        allowed = {"blocks", "process.lock", "progress.sqlite3", "progress.sqlite3-journal", "progress.sqlite3-wal", "progress.sqlite3-shm", "assembled.part"}
        if any(path.name not in allowed for path in state.iterdir()):
            raise StateError("state directory contains unrelated files")
        for path in state.iterdir():
            if path.is_symlink() or (path.is_file() and path.stat().st_nlink != 1):
                raise StateError("state symlinks and hard-linked files are unsupported")
        if (state / "assembled.part").exists() and not (state / "progress.sqlite3").exists():
            raise StateError("unowned assembled staging file")
    state.mkdir(parents=False, exist_ok=True)
    (state / "blocks").mkdir(exist_ok=True)
    config = dict(url=url, destination=str(destination), sha256=expected_sha256,
                  size=expected_size, block_size=block_size, coding="identity")
    deadline = time.monotonic() + max_seconds
    received = 0
    requests = 0
    with process_lock(state / "process.lock"), closing(open_journal(state, config)) as db:
        good = recover(db, state, expected_size, block_size)
        reused = len(good)
        saved = db.execute("SELECT value FROM settings WHERE name='etag'").fetchone()
        validator = etag(saved[0]) if saved else None
        missing = [offset for offset in range(0, expected_size, block_size) if offset not in good]
        assembly = state / "assembled.part"
        for offset in missing:
            if requests >= max_requests or time.monotonic() >= deadline:
                return dict(status="PARTIAL", requests=requests, bytes_received=received,
                            blocks_reused=reused, confirmed_blocks=len(good), etag=validator)
            count = min(block_size, expected_size - offset)
            headers = {"Accept-Encoding": "identity", "Range": f"bytes={offset}-{offset + count - 1}"}
            if validator:
                headers["If-Range"] = validator
            factory = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
            connection = factory(parsed.hostname, port, timeout=min(timeout, max(0.01, deadline - time.monotonic())))
            try:
                requests += 1
                connection.request("GET", (parsed.path or "/") + ("?" + parsed.query if parsed.query else ""), headers=headers)
                response = connection.getresponse()
                coding = one_header(response, "Content-Encoding", optional=True)
                if coding not in (None, "identity") or one_header(response, "Transfer-Encoding", optional=True) is not None:
                    raise ProtocolError("only identity with a declared Content-Length is supported")
                current = etag(one_header(response, "ETag"))
                if validator and current != validator:
                    raise ProtocolError("ETag changed; use a new state with the trusted new artifact identity")
                length = one_header(response, "Content-Length")
                if not re.fullmatch("[0-9]{1,12}", length):
                    raise ProtocolError("invalid Content-Length")
                length = int(length)
                if response.status == 200:
                    if good or offset != 0 or length != expected_size:
                        raise ProtocolError("200 cannot be appended to confirmed range blocks")
                    sha, got = receive(response, assembly, expected_size, deadline)
                    received += got
                    result = publish(assembly, destination, expected_sha256, expected_size)
                    return result | dict(requests=requests, bytes_received=received, blocks_reused=reused, etag=current)
                if response.status != 206:
                    raise ProtocolError(f"HTTP {response.status} is not a valid single range; 416 does not prove completion")
                content_range = one_header(response, "Content-Range")
                expected = f"bytes {offset}-{offset + count - 1}/{expected_size}"
                if content_range != expected or length != count:
                    raise ProtocolError("Content-Range/Length does not match exact requested offset and total")
                path = block_path(state, offset)
                if path.is_symlink() or (path.exists() and path.stat().st_nlink != 1):
                    raise StateError("symlink block files are unsupported")
                sha, got = receive(response, path, count, deadline)
                received += got
                db.execute("BEGIN IMMEDIATE")
                if validator is None:
                    db.execute("INSERT INTO settings VALUES('etag',?)", (current,))
                db.execute("INSERT INTO blocks VALUES(?,?,?)", (offset, count, sha))
                db.commit()
                validator = current
                good.add(offset)
            except (http.client.HTTPException, OSError) as exc:
                raise DownloadError(f"HTTP transfer incomplete: {type(exc).__name__}; confirmed blocks retained") from exc
            finally:
                connection.close()
        with assembly.open("wb") as target:
            for offset in range(0, expected_size, block_size):
                with block_path(state, offset).open("rb") as source:
                    while chunk := source.read(65536):
                        target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
        result = publish(assembly, destination, expected_sha256, expected_size)
        return result | dict(requests=requests, bytes_received=received, blocks_reused=reused, etag=validator)
