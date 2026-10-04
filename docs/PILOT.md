# Laboratory artifact workflow

`rangeresume pilot NEW_DIRECTORY` starts a real local HTTP artifact service and
executes three consumers on identical bytes and the same one-time aggregate-body
disconnect after1536 bytes: complete redownload; RangeResume1024-byte blocks;
and curl resume. A clean512-byte artifact preserves a case with no bandwidth benefit.
All inputs are synthetic laboratory data, with no customer or production economics.

The server logs received Range/If-Range/Accept-Encoding, status, request count and
attempted body bytes. Its counter includes the cut transfer but is not a packet
capture. Every complete output is independently compared byte-for-byte and fully
SHA256 hashed against the original. The standalone `examples/verify_artifacts.py`
does this again without importing the downloader. No faster-but-wrong output passes.

Curl's first real response headers are saved to disk. The second invocation uses
the saved strong ETag as If-Range plus `--continue-at -`, identity and final full
manifest hash/size. It receives the same disconnect and is not portrayed as lacking
resume. Curl invocation/version/header/stderr are retained. If curl is absent the
baseline is explicitly skipped; the final local evidence requires it to be present.

The interrupted8192-byte example typically needs9728 attempted bytes for complete
redownload,8704 for complete-block reuse and8192 for curl byte continuation. Exact
values and request counts belong to a particular run's pilot.json. Curl can reuse
the incomplete prefix more efficiently than RangeResume's confirmed-block policy;
RangeResume can require more HTTP requests, fsyncs, journal operations and hashes.
Its benefit here is an explicit manifest-bound state/publication contract, not a
claim of superior bandwidth or speed versus properly configured curl.

`workflow_seconds` covers transfer, retries, policy-specific setup, hash and write
work after the local service is ready. Fixture setup and independent-consumer time
are separate; curl's version diagnostic is recorded separately and excluded from
workflow time. `elapsed_seconds` additionally includes test-service shutdown, which
can take about0.5s and should not be interpreted as downloader speed. Timing is one
local observation, without a universal latency conclusion or production billing.

The final evidence also serves the built ordinary wheel over real HTTP. Two
registered `rangeresume fetch` processes exercise request-limited PARTIAL then
resume, independently check all original/downloaded bytes and the SHA256, install
that downloaded wheel in a second new virtual environment, and consume its module
and registered CLI. A fair fault comparison is also run on these actual wheel bytes.
This demonstrates a local release-artifact consumer, not deployment authorization.

Current curl and HTTP prior art, limits, and trust assumptions are in DESIGN.md.
