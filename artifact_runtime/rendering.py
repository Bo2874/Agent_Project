"""Networkless Writer/Poppler rendering, never imported by FastAPI."""

import subprocess
import tempfile
import time
from pathlib import Path
from uuid import uuid4


def export_docx_pdf(source: Path, target: Path):
    import uno
    from com.sun.star.beans import PropertyValue
    from com.sun.star.document.MacroExecMode import NEVER_EXECUTE
    from com.sun.star.document.UpdateDocMode import NO_UPDATE

    from artifact_runtime.document import load_safe_docx
    from artifact_runtime.pdf import read_pdf_text

    load_safe_docx(source)
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
