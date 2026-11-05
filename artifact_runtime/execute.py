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
    input_path = Path("/input") / request.inputs[0].name if request.inputs else None
    if request.action == "inspect":
        if input_path.suffix == ".docx":
            from artifact_runtime.document import inspect_docx

            outline = inspect_docx(
                input_path, offset=request.preview_offset, limit=request.preview_limit
            )
            report = GeneratedArtifact(preview_manifest=outline.model_dump(mode="json"))
        else:
            from artifact_runtime.spreadsheet import inspect_spreadsheet

            report = inspect_spreadsheet(input_path, request=request)
    elif request.action == "recalculate":
        from artifact_runtime.recalculation import recalculate_xlsx

        calculation = recalculate_xlsx(Path("/input/input-0.xlsx"), output_dir=output)
        report = GeneratedArtifact(calculation=calculation)
    elif request.operation.kind == "python":
        # X4 preflight is deliberately shared with structured editing.
        if request.inputs:
            from artifact_runtime.spreadsheet import load_safe

            for item in request.inputs:
                if item.name.endswith(".docx"):
                    from artifact_runtime.document import load_safe_docx

                    load_safe_docx(Path("/input") / item.name)
                elif item.name.endswith(".xlsx"):
                    workbook = load_safe(Path("/input") / item.name)
                    workbook.close()
                else:
                    raise ValueError("Python input format is not supported")
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
    elif request.operation.kind in {"create_docx", "edit_docx"}:
        from artifact_runtime.document import apply_document

        report = apply_document(request.operation, input_path=input_path, output_dir=output)
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
