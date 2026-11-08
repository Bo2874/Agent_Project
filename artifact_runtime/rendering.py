"""Networkless Writer/Poppler rendering, never imported by FastAPI."""

import hashlib
import subprocess
import tempfile
import time
from pathlib import Path
from uuid import uuid4


def render_metrics(started):
    try:
        peak = int(Path("/sys/fs/cgroup/memory.peak").read_text())
    except (OSError, ValueError):
        peak = None
    return {"elapsed_ms": round((time.monotonic() - started) * 1000), "container_peak_bytes": peak}


def export_docx_pdf(source: Path, target: Path):
    started = time.monotonic()
    import uno
    from com.sun.star.beans import PropertyValue
    from com.sun.star.document.MacroExecMode import NEVER_EXECUTE
    from com.sun.star.document.UpdateDocMode import NO_UPDATE

    from artifact_runtime.file_inspection import safe_office
    from artifact_runtime.pdf import read_pdf_text

    # Rendering never edits the source. Apply the active-content boundary,
    # not legacy python-docx's lossless structured-edit feature restrictions.
    safe_office(source)
    if target.exists():
        raise ValueError("Refusing to overwrite a rendered output")

    def prop(name, value):
        item = PropertyValue()
        item.Name, item.Value = name, value
        return item

    document = None
    with tempfile.TemporaryDirectory(prefix="writer-", dir="/tmp") as folder:
        pipe = "artifact_" + uuid4().hex
        process = subprocess.Popen(
            [
                "/usr/bin/soffice",
                "--headless",
                "--norestore",
                "--nodefault",
                "--nolockcheck",
                f"-env:UserInstallation={Path(folder).as_uri()}",
                f"--accept=pipe,name={pipe};urp;StarOffice.ComponentContext",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            local = uno.getComponentContext()
            resolver = local.ServiceManager.createInstanceWithContext(
                "com.sun.star.bridge.UnoUrlResolver", local
            )
            deadline = time.monotonic() + 15
            while True:
                try:
                    context = resolver.resolve(
                        f"uno:pipe,name={pipe};urp;StarOffice.ComponentContext"
                    )
                    break
                except Exception:
                    if process.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError("Writer did not open its private UNO pipe") from None
                    time.sleep(0.1)
            desktop = context.ServiceManager.createInstanceWithContext(
                "com.sun.star.frame.Desktop", context
            )
            document = desktop.loadComponentFromURL(
                source.resolve().as_uri(),
                "_blank",
                0,
                (
                    prop("Hidden", True),
                    prop("ReadOnly", True),
                    prop("MacroExecutionMode", NEVER_EXECUTE),
                    prop("UpdateDocMode", NO_UPDATE),
                    prop("AllowLinkUpdate", False),
                ),
            )
            if document is None or not document.supportsService("com.sun.star.text.TextDocument"):
                raise RuntimeError("Writer could not load the document")
            document.storeToURL(
                target.resolve().as_uri(),
                (prop("FilterName", "writer_pdf_Export"), prop("Overwrite", False)),
            )
            document.close(True)
            document = None
            desktop.terminate()
            read_pdf_text(target)
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        finally:
            if document is not None:
                try:
                    document.close(True)
                except Exception:
                    pass
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    return render_metrics(started)


def render_version(source: Path, *, mime_type: str, output_dir: Path, offset=0, limit=3):
    from artifact_runtime.document_types import PreviewManifest
    from artifact_runtime.pdf import read_pdf_text
    from artifact_runtime.protocol import DOCX_MIME, PDF_MIME

    if mime_type not in {DOCX_MIME, PDF_MIME} or not 1 <= limit <= 3:
        raise ValueError("Unsupported preview type or page batch")
    started = time.monotonic()
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pages-", dir="/tmp") as folder:
        pdf = source
        if mime_type == DOCX_MIME:
            pdf = Path(folder) / "source.pdf"
            export_docx_pdf(source, pdf)
        extracted = read_pdf_text(pdf)
        count = len(extracted.pages)
        if not 0 <= offset < count:
            raise ValueError("Preview page offset is outside the document")
        assets = []
        for index, page in enumerate(range(offset + 1, min(offset + limit, count) + 1)):
            prefix = output_dir / f"output-{index}"
            target = prefix.with_suffix(".png")
            if target.exists():
                raise ValueError("Refusing to overwrite a preview page")
            with (Path(folder) / "poppler.log").open("w+b") as logs:
                result = subprocess.run(
                    [
                        "/usr/bin/pdftoppm",
                        "-f",
                        str(page),
                        "-l",
                        str(page),
                        "-singlefile",
                        "-scale-to",
                        "1600",
                        "-r",
                        "100",
                        "-png",
                        str(pdf),
                        str(prefix),
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=logs,
                    timeout=30,
                    check=False,
                )
                if result.returncode:
                    raise RuntimeError("Poppler failed to render the pinned PDF page")
            data = target.read_bytes()
            assets.append(
                {
                    "name": target.name,
                    "page": page,
                    "asset_id": str(uuid4()),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
    return PreviewManifest(
        page_count=count,
        assets=assets,
        source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        warnings=extracted.warnings,
        render_metrics=render_metrics(started),
    )
