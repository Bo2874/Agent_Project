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
        elif input_path.suffix == ".pdf":
            from artifact_runtime.pdf import pdf_preview, read_pdf_text

            extracted = read_pdf_text(input_path)
            report = GeneratedArtifact(preview_manifest=pdf_preview(extracted, request))
        else:
            from artifact_runtime.spreadsheet import inspect_spreadsheet

            report = inspect_spreadsheet(input_path, request=request)
    elif request.action == "render_document":
        from artifact_runtime.rendering import export_docx_pdf

        metrics = export_docx_pdf(input_path, output / "output-0.pdf")
        report = GeneratedArtifact(preview_manifest={"render_metrics": metrics})
    elif request.action == "render_pages":
        from artifact_runtime.rendering import render_version

        preview = render_version(
            input_path,
            mime_type=request.inputs[0].mime_type,
            output_dir=output,
            offset=request.preview_offset,
            limit=request.preview_limit,
        )
        report = GeneratedArtifact(preview_manifest=preview.model_dump(mode="json"))
    elif request.action == "recalculate":
        from artifact_runtime.recalculation import recalculate_xlsx

        calculation = recalculate_xlsx(Path("/input/input-0.xlsx"), output_dir=output)
        report = GeneratedArtifact(calculation=calculation)
    elif request.operation.kind == "python":
        if request.operation.python is not None:
            from artifact_runtime.python_execution import execute_python

            report, files = execute_python(request, input_dir=Path("/input"), output_dir=output)
            write_bundle(sys.stdout.buffer, report, tuple(item[1] for item in files))
            return
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
    elif request.operation.kind in {"rebuild_pdf", "rebuild_pdf_text_only"}:
        from artifact_runtime.document_types import DocumentEdits
        from artifact_runtime.pdf import rebuild_pdf

        content = request.operation.payload
        if isinstance(content, DocumentEdits):
            if len(request.inputs) != 2 or request.inputs[1].name != "input-1.docx":
                raise ValueError("PDF node edits require that version's editable DOCX source")
            import tempfile

            from artifact_runtime.document import apply_document
            from artifact_runtime.protocol import ArtifactOperation
            from artifact_runtime.rendering import export_docx_pdf

            with tempfile.TemporaryDirectory(dir="/tmp") as folder:
                result = apply_document(
                    ArtifactOperation(
                        kind="edit_docx",
                        parent_version_id=request.operation.parent_version_id,
                        payload=content,
                    ),
                    input_path=Path("/input/input-1.docx"),
                    output_dir=Path(folder),
                )
                (output / "output-1.docx").write_bytes(
                    (Path(folder) / result.files[0].name).read_bytes()
                )
                export_docx_pdf(output / "output-1.docx", output / "output-0.pdf")
            report = GeneratedArtifact(preview_manifest={"editable_source": "output-1.docx"})
        else:
            approved = None
            if request.operation.kind == "rebuild_pdf_text_only":
                approved = content.omitted_pages
                content = content.content
            report = rebuild_pdf(
                content, source_pdf=input_path, output_dir=output, approved_omitted_pages=approved
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
