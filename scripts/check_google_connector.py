"""Explicit offline Google acceptance; live setup is a separate operator gate."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--offline", action="store_true")
    mode.add_argument("--live", action="store_true")
    parser.add_argument("--connection-id")
    parser.add_argument("--file-id")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.live:
        if not args.connection_id or not args.file_id:
            parser.error("--live requires --connection-id and --file-id for the chosen test file")
        print(
            json.dumps(
                {
                    "status": "NOT RUN",
                    "connection_id": args.connection_id,
                    "file_id": args.file_id,
                    "plan": [
                        "Select only this test file in a test conversation",
                        "Read and analyze A1:B10",
                        "Research and append rows through run_python_code",
                        "Read, edit native content/layout, then read it again",
                        "Revoke/reconnect and check old run cannot use old grants",
                    ],
                    "reason": "Use authenticated chat with individually selected test files. "
                    "This command does not change a cloud document.",
                },
                indent=2,
            )
        )
        return 2
    from sqlalchemy.engine import make_url

    url = make_url(os.environ.get("GOOGLE_TEST_DATABASE_URL", "postgresql:///missing"))
    if not url.database or not url.database.endswith("_test"):
        print("NOT RUN: provide task-owned GOOGLE_TEST_DATABASE_URL ending in _test")
        return 2
    tests = [
        "tests/test_google_oauth.py",
        "tests/test_google_credentials.py",
        "tests/test_google_file_access.py",
        "tests/test_google_python_protocol.py",
        "tests/test_google_python_snapshots.py",
        "tests/test_google_python_requests.py",
        "tests/test_google_python_bridge.py",
        "tests/test_google_python_service.py",
        "tests/test_google_python_registry.py",
        "tests/test_google_python_integration.py",
        "tests/test_google_python_graph.py",
        "tests/test_google_ui.py",
        "tests/test_google_review.py",
    ]
    root = Path(__file__).resolve().parents[1]
    command = [sys.executable, "-m", "pytest", *tests, "-q", "-ra"]
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        command.append("--junitxml=" + str(args.report.resolve()))
    return subprocess.run(command, cwd=root, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
