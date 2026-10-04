# Representation-bound artifact recovery

The caller knows one artifact's trusted SHA256 and byte size from an authenticated
release manifest. ETag is a server assertion about one representation, not content
authentication. TLS uses Python's default certificate validation. Plain HTTP is
supported for trusted local services and the laboratory pilot. The utility does
not obtain or authenticate the release manifest.

## State and invariants

The canonical configuration binds the literal URL, resolved destination, expected
digest/size, block geometry and identity coding. Reusing state with a changed
configuration fails before HTTP. Query strings remain part of the identity. No
redirect, proxy environment, credential URL, custom authorization header,
compression, multi-range response or automatic network retry is supported.

A SQLite schema-version-1 journal records only complete blocks. A block is written,
flushed and fsynced before its digest/length/offset and the first strong ETag commit
in one SQLite FULL transaction. This order means a termination before commit leaves
an orphan, not a confirmed block. A termination after commit leaves bytes to be
independently checked next run. Files are named from bounded integer offsets;
journal strings cannot select paths. State is caller-owned and cooperative;
hostile simultaneous filesystem mutation is outside the guarantee.

On rerun every retained record is checked for legal geometry, one-link ordinary
file, exact length and SHA256 of the actual disk bytes. Corrupt or missing blocks
lose their confirmed record and are fetched again. Confirmed blocks without a
persisted strong validator are refused instead of binding to a new representation.
An empty unowned journal cannot authorize overwriting existing staging or blocks.

The process lock is a nonblocking OS file lock (Windows msvcrt/POSIX flock).
Ordinary process termination releases it. It is not a distributed lock. Safe paths
are resolved before directories are created; destination inside state is refused.
Top-level symlinks and hard-linked state files, and symlink/hard-linked block files
are refused. Parent aliases resolve to one path; a caller may intentionally place
state beneath an already resolved parent. Local filesystem races are not sandboxed.

## HTTP contract

Each GET requests `Range: bytes=start-end` and `Accept-Encoding: identity`.
Subsequent requests add the saved strong `If-Range`. The supported strong ETag is
quoted, ASCII, bounded to 200 opaque characters; valid RFC obs-text tags are outside
this narrower scope. Critical response headers must occur exactly once.
Transfer-Encoding is rejected, Content-Length is bounded, and content coding must
be absent or identity. Python http.client also applies its standard header caps.

A 206 must contain exactly `Content-Range: bytes start-end/trusted-size`, with
the requested closed interval and Content-Length equal to its length. Received
bytes must complete that HTTP body. Truncated body, changed validator, wrong
interval/total, weak ETag, gzip, duplicate headers, redirects and 416 are refusals.
416 never proves that a local artifact is complete. Parsing is deliberately strict
rather than accepting every legal formatting variation of RFC9110.

A 200 is permitted only at offset zero with no confirmed blocks and the full trusted
size. It is written as a fresh full staging file and complete SHA256 checked. A 200
after any retained block is refused, so a server ignoring Range or a false If-Range
condition cannot be appended to old blocks. Interrupted full200 bytes are not
reused; completed single-range blocks are the recoverable unit.

HTTP Content-Length defines framing. Bytes a misbehaving server sends after the
declared body are outside that body in http.client; this utility does not claim to
audit arbitrary trailing TCP bytes. An overlong declared range/body is rejected.

## Publication and limits

Confirmed blocks assemble in offset order into `assembled.part`. The complete
file is fsynced and checked against trusted SHA256 and size. `os.link` then creates
the destination name exclusively and atomically: existing mismatching destinations
are never replaced. An already matching destination is independently hashed and
returns ALREADY_PUBLISHED with no HTTP. Assembly remains in state for diagnosis.
Delete the completed state only after downstream acceptance if space matters.

State/destination must share a local filesystem supporting exclusive hard links.
Unsupported or cross-volume link fails closed. POSIX publication fsyncs the parent
directory; Windows only offers the file fsync used here. Tests cover process kill,
not hardware power loss, network filesystems, antivirus behavior or disk-controller
durability. Temporary storage is approximately retained blocks plus one assembled
artifact, a small SQLite journal and lock; final hard link does not duplicate data.

Accepted artifact size is 0 through 1 GiB; block size 1 KiB through 8 MiB;
at most 65,536 blocks and 65,536 requests per call. Request allowance 1..65,536;
max_seconds and socket timeout are finite 0.01..3600 seconds. Booleans are refused
as numbers. No buffering of an entire artifact in the downloader is required.

The allowance is cooperative, checked between old-block hashes, network reads,
assembly chunks and before/after the final full hash. A single blocking OS read,
fsync, connection operation, lock/journal operation or complete hash can exceed its
time allowance before control returns. It is not a hard wall-clock/CPU guarantee.
Expiring an allowance returns PARTIAL or a handled transfer failure, never an
unverified successful publication. PARTIAL CLI exit4 retains recoverable state.
Path/configuration preflight and the complete verification of an existing matching
destination occur before this allowance begins. ALREADY_PUBLISHED therefore still
pays that full-file hash cost and is not limited by max_seconds. The before/after
final-hash deadline checks described above apply to new publication.

`bytes_received` counts completed accepted bodies in the current call, omitting
discarded partial responses. The pilot server's attempted-body counter separately
records all attempted bytes, including the failed partial transfer; it is not a
wire/TLS packet or billing meter. Request counters and logs are process-local.

## Existing tools and scope

[RFC9110 If-Range](https://www.rfc-editor.org/rfc/rfc9110.html#name-if-range)
requires a strong validator and defines full200 when the condition is false.
[curl](https://curl.se/docs/manpage.html) already implements ranges, continuation,
ETag handling and many broader protocols. `--etag-compare` sends If-None-Match;
the pilot uses a real saved response ETag as explicit If-Range on `--continue-at -`.
Sources were checked 2026-10-05. This is an application of existing mechanisms,
with a small SDK combining independently rechecked block state and trusted
manifest-bound exclusive publication, not an invention of HTTP resume.
