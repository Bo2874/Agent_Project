"""Calculate in a credential-free LibreOffice container through a private UNO pipe."""

import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from uuid import uuid4

from artifact_runtime.protocol import CalculationReport
from artifact_runtime.spreadsheet import load_safe, unsupported_formulas, workbook_inventory


def recalculate_xlsx(source: Path, *, output_dir: Path) -> CalculationReport:
    workbook = load_safe(source)
    unsupported = unsupported_formulas(workbook)
    before = workbook_inventory(workbook)
    workbook.close()
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / "output-0.xlsx"
    if target.exists():
        raise ValueError("Refusing to overwrite an existing calculation output")
    if unsupported:
        shutil.copyfile(source, target)
        return CalculationReport(status="unsupported", warnings=tuple(sorted(unsupported)))
    # The system Python's python-uno binding is provided by the render image.
    import uno
    from com.sun.star.beans import PropertyValue
    from com.sun.star.document.MacroExecMode import NEVER_EXECUTE
    from com.sun.star.document.UpdateDocMode import NO_UPDATE

    def prop(name, value):
        item = PropertyValue()
        item.Name, item.Value = name, value
        return item

    document = None
    with tempfile.TemporaryDirectory(prefix="calc-", dir="/tmp") as folder:
        pipe = f"artifact_{uuid4().hex}"
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
                "com.sun.star.bridge.UnoUrlResolver",
                local,
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
                        raise RuntimeError(
                            "LibreOffice did not open the private UNO pipe"
                        ) from None
                    time.sleep(0.1)
            desktop = context.ServiceManager.createInstanceWithContext(
                "com.sun.star.frame.Desktop",
                context,
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
            if document is None or not document.supportsService(
                "com.sun.star.sheet.SpreadsheetDocument"
            ):
                raise RuntimeError("Calc could not load the workbook")
            document.calculateAll()
            document.storeToURL(
                target.resolve().as_uri(),
                (
                    prop("FilterName", "Calc MS Excel 2007 XML"),
                    prop("Overwrite", False),
                ),
            )
            document.close(True)
            document = None
            desktop.terminate()
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
    calculated = load_safe(target)
    after = workbook_inventory(calculated)
    calculated.close()
    if before != after:
        target.unlink(missing_ok=True)
        raise ValueError(
            "Calc changed worksheet content, formulas or chart inventory; output refused"
        )
    from openpyxl import load_workbook

    formulas = load_safe(target)
    cached = load_workbook(target, data_only=True, keep_links=False)
    try:
        for sheet in formulas:
            for cell in sheet._cells.values():
                if cell.data_type != "f":
                    continue
                value = cached[sheet.title][cell.coordinate]
                # Calc encodes an empty formula string as t="str" and an empty v.
                # This differs from an uncalculated numeric formula's missing cache.
                empty_string = value.value is None and value.data_type in {"s", "str"}
                if (value.value is None and not empty_string) or value.data_type == "e":
                    target.unlink(missing_ok=True)
                    raise ValueError(
                        f"Invalid formula cache at {sheet.title}!{cell.coordinate}: "
                        f"type={value.data_type}, value={value.value!r}"
                    )
    finally:
        formulas.close()
        cached.close()
    return CalculationReport(status="fresh")
