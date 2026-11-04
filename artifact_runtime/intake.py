"""Trusted intake: writes only generated names to a root-owned input volume."""

import json
import os
import sys
from pathlib import Path

from artifact_runtime.protocol import ArtifactRunRequest, copy_checked, read_manifest


def main():
    request = ArtifactRunRequest.model_validate(read_manifest(sys.stdin.buffer))
    root = Path("/input")
    for item in request.inputs:
        path = root / item.name
        with path.open("xb") as output:
            copy_checked(sys.stdin.buffer, output, item)
        os.chmod(path, 0o444)
    if sys.stdin.buffer.read(1):
        raise ValueError("Unexpected trailing intake data")
    path = root / "operation.json"
    path.write_text(json.dumps(request.model_dump(mode="json")), encoding="utf-8")
    os.chmod(path, 0o444)


if __name__ == "__main__":
    main()
