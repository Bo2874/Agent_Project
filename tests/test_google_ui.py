import os
import shutil
import subprocess


def test_google_file_selection_in_real_browser():
    result = subprocess.run(
        [shutil.which("node"), "tests/google_connectors_browser.cjs"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=90,
        env={**os.environ},
    )
    assert result.returncode == 0, result.stdout + result.stderr
