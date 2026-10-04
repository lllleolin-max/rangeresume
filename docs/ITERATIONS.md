# Real review corrections

Initial implementation `3ef086f388e18cae94b6af1b16f0eefee1ab79da` already contained
the downloader, SDK/CLI, real local HTTP pilot, curl/full-redownload comparison,
ordinary-wheel packaging and nine tests. Initial feature work does not count as a
correction cycle. The following findings were made by implementation self-review;
the independent reviewer is a separate agent and may discover additional defects.

| Round | Before SHA | Product defect | After SHA | Original probe |
|---|---|---|---|---|
| 1 | 3ef086f388e18cae94b6af1b16f0eefee1ab79da | An empty SQLite file let a pre-existing unowned assembled.part be overwritten | d47576cf82a20c7f0dc08b2fe1c0e5ba5dc1831d | test_empty_unowned_journal_does_not_authorize_staging_overwrite |
| 2 | d47576cf82a20c7f0dc08b2fe1c0e5ba5dc1831d | Confirmed blocks with a lost settings ETag were silently rebound to a new server validator | 907670d792bb3be65e02c628689a9e2abac0175b | test_confirmed_blocks_without_validator_are_not_rebound |
| 3 | 907670d792bb3be65e02c628689a9e2abac0175b | Recovery with every block present ignored the allowance during old-block hashes, assembly and publication | 89fc95b5f9121f148dc8ccb3cd3749d057c850a3 | test_expired_resume_verification_allowance_cannot_publish |
| 4 | 89fc95b5f9121f148dc8ccb3cd3749d057c850a3 | A competing Windows process caused PermissionError while reading the locked byte, bypassing the SDK's documented StateError refusal | 3f867bc5f6b06c5353b1e8fd2a83932ccb06cb27 | test_competing_process_owner_is_typed_safe_refusal |

Each original probe exits1 at its before commit and0 after. Exact Git archives were
exported; the same final `tests/test_review_regressions.py` was copied into each
archive and only the named original test was executed. Canonical archive hashes,
complete stdout/stderr and exit codes are in the implementation evidence directory's
`round1-canonical-history.json`, `round2-canonical-history.json` and
`round3-canonical-history.json` and `round4-canonical-history.json`. These are historical source replays, separate from
the final installed-wheel suite. Original first-run before/after logs are retained.

Round1 refuses unowned block/staging files before creating configuration ownership.
Its probe uses a real HTTP200 service and preserves the original precious bytes.
Round2 refuses retained blocks with no saved validator before another HTTP request;
the server deliberately changes its strong ETag while retaining identical bytes.
Round3 uses real HTTP/disk hashes plus an explicit 30ms slow-storage injection and a
10ms allowance, after a modeled publication interruption. This deterministically
exposes the missing cooperative check; it is not a hardware latency measurement.
Round4 was exposed when a real held-lock child was used to test concurrent caller
refusal on Windows. The fix queries lock-file size without reading the locked byte
and maps both token setup and lock acquisition errors to StateError. The before
failure is Windows-specific; the same independent test also checks POSIX behavior.

To replay from any checkout, use this final regression file against each before
and after source export, with:

```sh
python -m unittest discover -s tests -p test_review_regressions.py -k ORIGINAL_TEST_NAME -v
```

Additional boundary tests kill a real child process from its parent after reaching
block-write/transaction and publication barriers. Tests, CI configuration, saved
curl-response ETag and timing separation added after these corrections are coverage
and integration work; they are not claimed as extra defect cycles. The lock defect
found by that new coverage is separately counted as round4.
