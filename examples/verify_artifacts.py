"""Independent full-byte consumer; does not import rangeresume.

Run: python verify_artifacts.py PILOT_DIRECTORY [--require-curl]
"""
import hashlib
import json
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
report = json.loads((root / "pilot.json").read_bytes())
original = (root / "original.bin").read_bytes()
records = []
for scenario in report["scenarios"]:
    case = scenario["case"]
    expected = original if case == "interrupted" else original[:512]
    digest = hashlib.sha256(expected).hexdigest()
    for result in scenario["results"]:
        policy = result["policy"]
        if "skipped" in result:
            if "--require-curl" in sys.argv:
                raise AssertionError("required baseline skipped: " + policy)
            records.append(dict(case=case, policy=policy, skipped=result["skipped"]))
            continue
        raw = (root / case / policy / "artifact.bin").read_bytes()
        assert raw == expected and len(raw) == len(expected)
        assert hashlib.sha256(raw).hexdigest() == digest
        assert len(result["server_log"]) == result["server_requests"]
        assert sum(row["attempted_body_bytes"] for row in result["server_log"]) == result["server_attempted_bytes"]
        records.append(dict(case=case, policy=policy, bytes=len(raw), sha256=digest,
                            independent_byte_equal=True, requests=result["server_requests"],
                            attempted_bytes=result["server_attempted_bytes"]))
print(json.dumps(records, indent=2))
