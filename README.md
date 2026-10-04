# RangeResume

Recover HTTP artifact downloads as verified, representation-bound blocks, then
publish one complete file atomically. A release/data engineer supplies a trusted
SHA256 and size from their release manifest. Strong ETag and identity single-range
responses bind retained blocks to the same representation; the trusted complete
digest decides whether the final artifact can be accepted.

```sh
python -m pip install .
rangeresume pilot pilot-output
rangeresume fetch https://example.org/artifact.bin artifact.bin --state artifact-state --sha256 YOUR_TRUSTED_SHA256 --size 1048576
```

```python
from rangeresume import download
result = download(url, "artifact.bin", "artifact-state",
                  expected_sha256=trusted_sha256, expected_size=trusted_size)
```

Rerun the identical command after a disconnect or process termination. Each old
block is independently hashed again before reuse. Blocks flush/fsync before their
SQLite FULL journal record; orphan or corrupt bytes are not treated as completed.
PARTIAL (CLI exit4) means the request/time allowance was exhausted with recoverable
confirmed state. Handled failures exit2 and never claim artifact publication.

Only Python3.11+ standard library is required. Supports HTTP(S) GET, identity
content coding, bounded Content-Length, strong ASCII ETag and exact single-range
206. A full200 is only accepted with no confirmed old blocks and the complete trusted
digest. Changed ETag, resumed200, malformed ranges, gzip, redirects,416, credential
URLs and unknown transfer formats are rejected. No proxy environment is used.

State/output must be on one local filesystem supporting exclusive hard links.
Publication refuses existing mismatching files; a matching destination is fully
hashed and reported ALREADY_PUBLISHED. Source ETag is a server assertion, not a
signature. Trusted manifest identity, cooperative local state and TLS trust are
caller responsibilities; this is not an OS sandbox or hardware power-loss claim.

中文：按强ETag绑定的表示持久保存完整块，重启后逐块复核，仅补取缺块。
最终文件必须满足调用者可信SHA256和总长，再原子发布；不会混拼续传中的200响应。

The pilot executes a real local HTTP artifact service with byte/request logs and
independent original-byte consumer. It compares complete redownload, verified curl
resume and this block workflow under the same1536-byte disconnect and a clean small
file. Data is synthetic laboratory data; no customers, revenue or production
savings are asserted. curl already has mature range/resume support; this is not its
invention or a thin wrapper around curl.

HTTP semantics follow [RFC9110](https://www.rfc-editor.org/rfc/rfc9110.html#name-if-range).
The [curl manual](https://curl.se/docs/manpage.html) documents existing continue/range
and ETag support. `--etag-compare` means If-None-Match, so the pilot's correct resume
uses `--continue-at -` with an explicit If-Range strong validator and final hash.

MIT. Run `python -m unittest discover -s tests -v`. Final independent review and
publication/CI observations remain separate from implementation claims.
