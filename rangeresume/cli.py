import argparse
import json
from pathlib import Path
import sqlite3
import sys

from .download import DownloadError, download


def main(argv=None):
    p = argparse.ArgumentParser(description="Verified representation-bound HTTP range recovery")
    sub = p.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser("fetch")
    fetch.add_argument("url")
    fetch.add_argument("destination", type=Path)
    fetch.add_argument("--state", required=True, type=Path)
    fetch.add_argument("--sha256", required=True)
    fetch.add_argument("--size", required=True, type=int)
    fetch.add_argument("--block-size", type=int, default=262144)
    fetch.add_argument("--max-requests", type=int, default=256)
    fetch.add_argument("--max-seconds", type=float, default=60)
    fetch.add_argument("--timeout", type=float, default=10)
    pilot = sub.add_parser("pilot")
    pilot.add_argument("output", type=Path)
    args = p.parse_args(argv)
    try:
        if args.command == "pilot":
            from .pilot import run_pilot
            result = run_pilot(args.output)
        else:
            result = download(args.url, args.destination, args.state, expected_sha256=args.sha256,
                              expected_size=args.size, block_size=args.block_size,
                              max_requests=args.max_requests, max_seconds=args.max_seconds, timeout=args.timeout)
        print(json.dumps(result, sort_keys=True, ensure_ascii=True))
        return 4 if result.get("status") == "PARTIAL" else 0
    except (DownloadError, OSError, sqlite3.Error) as exc:
        print(json.dumps(dict(error=type(exc).__name__, message=str(exc)), ensure_ascii=True), file=sys.stderr)
        return 2
