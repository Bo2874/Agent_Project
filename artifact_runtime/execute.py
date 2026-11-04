"""Fixed container entry point. Arbitrary code runs in a child, never the broker."""

import subprocess
import sys
from pathlib import Path

from artifact_runtime.protocol import (
    ArtifactRunRequest,
    GeneratedArtifact,
    collect_outputs,
    write_bundle,
)


def main():
    request = ArtifactRunRequest.model_validate_json(Path("/input/operation.json").read_bytes())
    output = Path("/output")
    report = GeneratedArtifact()
    if request.action == "inspect":
        from artifact_runtime.spreadsheet import inspect_spreadsheet

        report = inspect_spreadsheet(Path("/input/input-0.xlsx"), request=request)
    elif request.action == "recalculate":
        from artifact_runtime.recalculation import recalculate_xlsx

        calculation = recalculate_xlsx(Path("/input/input-0.xlsx"), output_dir=output)
        report = GeneratedArtifact(calculation=calculation)
    elif request.operation.kind == "python":
        # X4 preflight is deliberately shared with structured editing.
        if request.inputs:
            from artifact_runtime.spreadsheet import load_safe

            for item in request.inputs:
                workbook = load_safe(Path("/input") / item.name)
                workbook.close()
        with Path("/tmp/python.log").open("wb") as logs:
            result = subprocess.run(
                [sys.executable, "-I", "-B", "-c", request.operation.code],
                cwd="/output",
                stdout=logs,
                stderr=logs,
                timeout=25,
                check=False,
                env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp", "LANG": "C.UTF-8"},
            )
        if result.returncode:
            # Do not return arbitrary megabytes of process output to the caller.
            raise RuntimeError(
                Path("/tmp/python.log").read_bytes()[-4096:].decode("utf-8", "replace")
            )
    else:
        from artifact_runtime.spreadsheet import apply_spreadsheet

        report = apply_spreadsheet(
            request.operation,
            input_path=Path("/input/input-0.xlsx") if request.inputs else None,
            output_dir=output,
        )
    if request.action == "inspect":
        write_bundle(sys.stdout.buffer, report, ())
    else:
        files = collect_outputs(output)
        report = report.model_copy(update={"files": tuple(item[0] for item in files)})
        write_bundle(sys.stdout.buffer, report, tuple(item[1] for item in files))


if __name__ == "__main__":
    main()
