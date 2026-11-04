"""Office parsing is imported only inside the fixed sandbox image."""

import stat
import warnings
import zipfile
from copy import copy
from datetime import date, datetime, time
from pathlib import Path, PurePosixPath

from defusedxml import ElementTree

from artifact_runtime.protocol import (
    ArtifactOperation,
    ArtifactRunRequest,
    CalculationReport,
    GeneratedArtifact,
    canonical,
    collect_outputs,
)
from artifact_runtime.spreadsheet_types import (
    AddChart,
    AddSheet,
    FormatRange,
    SetFormula,
    SpreadsheetPlan,
    WriteCells,
    coordinates,
)

SUPPORTED_FUNCTIONS = {"SUM", "SUMIF", "AVERAGE", "COUNT", "IF"}
DANGEROUS_FUNCTIONS = {"WEBSERVICE", "HYPERLINK", "DDE", "RTD", "CALL", "REGISTER.ID", "IMAGE"}


def preflight_workbook(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > 2000 or sum(item.file_size for item in entries) > 104857600:
            raise ValueError("Spreadsheet exceeds archive limits")
        names = set()
        for item in entries:
            name = item.filename
            if (
                name in names
                or name.startswith("/")
                or ".." in PurePosixPath(name).parts
                or "\\" in name
                or ":" in name
                or item.flag_bits & 1
                or stat.S_ISLNK(item.external_attr >> 16)
            ):
                raise ValueError("Unsafe spreadsheet archive member")
            names.add(name)
        if not {"[Content_Types].xml", "xl/workbook.xml"}.issubset(names):
            raise ValueError("Not an XLSX workbook")
        unsupported = (
            "vbaproject",
            "externallinks/",
            "xl/media/",
            "xl/embeddings/",
            "xl/activex/",
            "xl/macrosheets/",
            "xl/pivot",
            "xl/slicer",
            "xl/querytables/",
            "xl/connections",
            "customxml/",
            "vml",
            "xl/threadedcomments/",
        )
        if any(any(part in name.lower() for part in unsupported) for name in names):
            raise ValueError(
                "Macros, external links, images and embedded objects are not supported"
            )
        # A relationship may target the network even without an externalLinks part.
        for name in names:
            if name.endswith(".rels") or name == "[Content_Types].xml":
                if archive.getinfo(name).file_size > 1048576:
                    raise ValueError("Spreadsheet relationship exceeds limit")
                root = ElementTree.fromstring(
                    archive.read(name), forbid_dtd=True, forbid_entities=True, forbid_external=True
                )
                if name == "[Content_Types].xml":
                    types = [item.attrib.get("ContentType", "").lower() for item in root]
                    if any(
                        any(marker in value for marker in ("macro", "vba", "activex", "oleobject"))
                        for value in types
                    ):
                        raise ValueError("Macro-enabled workbook parts are not supported")
                if any(item.attrib.get("TargetMode", "").lower() == "external" for item in root):
                    raise ValueError("External spreadsheet relationships are not supported")
            if name.startswith("xl/drawings/") and name.endswith(".xml"):
                root = ElementTree.fromstring(archive.read(name))
                if any(
                    item.tag.rsplit("}", 1)[-1] in {"pic", "sp", "grpSp", "cxnSp", "oleObject"}
                    for item in root.iter()
                ):
                    raise ValueError("Unsupported drawing would be lost during editing")
            if name.startswith("xl/charts/") and name.endswith(".xml"):
                root = ElementTree.fromstring(archive.read(name))
                charts = [
                    item.tag.rsplit("}", 1)[-1]
                    for item in root.iter()
                    if item.tag.rsplit("}", 1)[-1].endswith("Chart")
                ]
                if len(charts) != 1 or charts[0] not in {"barChart", "lineChart"}:
                    raise ValueError("Only column, bar and line charts can be preserved")


def load_safe(path: Path):
    from openpyxl import load_workbook

    preflight_workbook(path)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        workbook = load_workbook(path, keep_links=False)
    if any(
        "removed" in str(item.message).lower()
        or "lost" in str(item.message).lower()
        or "not supported" in str(item.message).lower()
        for item in caught
    ):
        workbook.close()
        raise ValueError("Workbook contains unsupported content that would be lost")
    for defined in workbook.defined_names.values():
        if "[" in defined.attr_text or "|" in defined.attr_text:
            raise ValueError("External defined names are not supported")
        if defined.attr_text:
            formula_functions("=" + defined.attr_text.lstrip("="))
    for sheet in workbook:
        for cell in sheet._cells.values():
            if cell.data_type == "f":
                formula_functions(cell.value)
        for validation in sheet.data_validations.dataValidation:
            for formula in (validation.formula1, validation.formula2):
                if isinstance(formula, str) and formula:
                    formula_functions(formula if formula.startswith("=") else "=" + formula)
        for rules in sheet.conditional_formatting._cf_rules.values():
            for rule in rules:
                for formula in rule.formula or ():
                    formula_functions(formula if formula.startswith("=") else "=" + formula)
    return workbook


def formula_functions(formula: str) -> set[str]:
    from openpyxl.formula.tokenizer import Tokenizer

    if not isinstance(formula, str):
        raise ValueError("Array/data-table formulas are not supported in this version")
    functions = set()
    for token in Tokenizer(formula).items:
        if token.type == "FUNC" and token.subtype == "OPEN":
            functions.add(token.value[:-1].upper())
        if token.type == "OPERAND" and token.subtype == "RANGE":
            if any(character in token.value for character in "[]|"):
                raise ValueError("External or DDE formulas are not supported")
    if any(
        function == bad or function.endswith("." + bad)
        for function in functions
        for bad in DANGEROUS_FUNCTIONS
    ):
        raise ValueError("External or executable formula functions are not supported")
    return functions


def unsupported_formulas(workbook) -> set[str]:
    unsupported = set()
    for sheet in workbook:
        for cell in sheet._cells.values():
            if cell.data_type == "f":
                unsupported.update(formula_functions(cell.value) - SUPPORTED_FUNCTIONS)
    return unsupported


def workbook_inventory(workbook) -> dict:
    import re

    def ref(value):
        if not value:
            return None
        return re.sub(r"'([A-Za-z_][A-Za-z0-9_]*)'!", r"\1!", value.replace("$", ""))

    sheets = []
    for sheet in workbook:
        formulas, content = {}, {}
        for cell in sheet._cells.values():
            if cell.data_type == "f":
                formulas[cell.coordinate] = cell.value
            elif cell.value is not None:
                content[cell.coordinate] = cell.value
        charts = []
        for chart in sheet._charts:
            series = []
            for item in chart.series:
                value = item.val.numRef.f if item.val and item.val.numRef else None
                series.append(ref(value))
            charts.append((type(chart).__name__, getattr(chart, "type", None), tuple(series)))
        sheets.append((sheet.title, formulas, content, charts))
    return {"sheets": sheets}


def apply_spreadsheet(
    operation: ArtifactOperation,
    *,
    input_path: Path | None,
    output_dir: Path,
) -> GeneratedArtifact:
    from openpyxl import Workbook
    from openpyxl.chart import BarChart, LineChart, Reference
    from openpyxl.styles import Alignment, PatternFill
    from openpyxl.utils.cell import range_boundaries

    plan = SpreadsheetPlan.model_validate(operation.payload)
    if operation.kind == "edit_xlsx":
        if input_path is None:
            raise ValueError("Editing requires an input workbook")
        workbook = load_safe(input_path)
    elif operation.kind == "create_xlsx":
        if input_path is not None:
            raise ValueError("Creating does not accept an input workbook")
        workbook = Workbook()
        workbook.active.title = plan.sheet_name
    else:
        raise ValueError("Use the isolated code executor for Python operations")
    for edit in plan.operations:
        if isinstance(edit, AddSheet):
            if edit.name in workbook.sheetnames:
                raise ValueError("Worksheet already exists")
            workbook.create_sheet(edit.name)
            continue
        if edit.sheet not in workbook.sheetnames:
            raise ValueError(f"Worksheet not found: {edit.sheet}")
        sheet = workbook[edit.sheet]
        if isinstance(edit, WriteCells):
            first_row, first_column = coordinates(edit.start)
            for row_index, row in enumerate(edit.values, first_row):
                for column_index, value in enumerate(row, first_column):
                    cell = sheet.cell(row_index, column_index)
                    cell.value = value
                    if isinstance(value, str):
                        cell.data_type = "s"
        elif isinstance(edit, SetFormula):
            if formula_functions(edit.formula) - SUPPORTED_FUNCTIONS:
                raise ValueError("New formulas support SUM, SUMIF, AVERAGE, COUNT and IF")
            sheet[edit.cell] = edit.formula
        elif isinstance(edit, FormatRange):
            minimum_column, minimum_row, maximum_column, maximum_row = range_boundaries(edit.range)
            for row in sheet.iter_rows(
                min_row=minimum_row,
                max_row=maximum_row,
                min_col=minimum_column,
                max_col=maximum_column,
            ):
                for cell in row:
                    font = copy(cell.font)
                    for key, value in (
                        ("bold", edit.bold),
                        ("italic", edit.italic),
                        ("size", edit.font_size),
                        ("color", edit.font_color),
                    ):
                        if value is not None:
                            setattr(font, key, value)
                    cell.font = font
                    if edit.fill_color:
                        cell.fill = PatternFill("solid", fgColor=edit.fill_color)
                    if edit.number_format is not None:
                        cell.number_format = edit.number_format
                    if edit.alignment:
                        cell.alignment = Alignment(horizontal=edit.alignment)
        elif isinstance(edit, AddChart):
            chart = LineChart() if edit.chart_type == "line" else BarChart()
            if isinstance(chart, BarChart):
                chart.type = "bar" if edit.chart_type == "bar" else "col"
            chart.title = edit.title
            chart.add_data(
                Reference(sheet, range_string=f"'{sheet.title}'!{edit.data_range}"),
                titles_from_data=edit.titles_from_data,
            )
            if edit.categories_range:
                chart.set_categories(
                    Reference(sheet, range_string=f"'{sheet.title}'!{edit.categories_range}")
                )
            sheet.add_chart(chart, edit.position)
    unsupported = unsupported_formulas(workbook)
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / "output-0.xlsx"
    if target.exists():
        raise ValueError("Refusing to overwrite an existing output")
    workbook.save(target)
    workbook.close()
    preview = inspect_spreadsheet(target)
    files = collect_outputs(output_dir)
    return GeneratedArtifact(
        files=tuple(item[0] for item in files),
        calculation=CalculationReport(
            status="unsupported" if unsupported else "not_calculated",
            warnings=tuple(sorted(unsupported)),
        ),
        preview_manifest=preview.preview_manifest,
    )


def inspect_spreadsheet(
    path: Path, *, request: ArtifactRunRequest | None = None
) -> GeneratedArtifact:
    from openpyxl import load_workbook

    workbook = load_safe(path)
    cached = load_workbook(path, data_only=True, keep_links=False)
    title = request.preview_sheet if request and request.preview_sheet else workbook.sheetnames[0]
    if title not in workbook.sheetnames:
        raise ValueError("Worksheet not found")
    sheet = workbook[title]
    offset, limit = (request.preview_offset, request.preview_limit) if request else (0, 100)
    limit = min(limit, max(1, 512 // min(sheet.max_column, 100)))
    cells = []

    def value(cell_value):
        if isinstance(cell_value, (datetime, date, time)):
            return cell_value.isoformat()
        if isinstance(cell_value, str) and len(cell_value) > 256:
            return cell_value[:255] + "…"
        return cell_value

    for row in sheet.iter_rows(
        min_row=offset + 1,
        max_row=min(offset + limit, sheet.max_row),
        max_col=min(sheet.max_column, 100),
    ):
        for cell in row:
            if cell.value is None:
                continue
            formula = cell.value if cell.data_type == "f" else None
            cached_cell = cached[title][cell.coordinate]
            cached_value = cached_cell.value
            if formula and cached_value is None and cached_cell.data_type in {"s", "str"}:
                cached_value = ""
            cells.append(
                {
                    "address": cell.coordinate,
                    "row": cell.row,
                    "column": cell.column,
                    "value": None if formula else value(cell.value),
                    "formula": value(formula),
                    "cached_value": value(cached_value) if formula else None,
                    "number_format": cell.number_format[:128],
                    "truncated": isinstance(cell.value, str) and len(cell.value) > 256,
                }
            )
    unsupported = unsupported_formulas(workbook)
    manifest = {
        "sheet": title,
        "offset": offset,
        "limit": limit,
        "sheets": [
            {
                "name": item.title,
                "rows": item.max_row,
                "columns": item.max_column,
                "charts": len(item._charts),
            }
            for item in workbook
        ],
        "cells": cells,
        "columns_truncated": sheet.max_column > 100,
    }
    # Keep the entire transport manifest bounded even for very long formulas,
    # number formats or multi-byte cell text. Trim complete rows, never file data.
    while len(canonical(manifest)) > 524288 and cells:
        last_row = cells[-1]["row"]
        cells[:] = [cell for cell in cells if cell["row"] < last_row]
        manifest["limit"] = max(1, last_row - offset - 1)
    if len(canonical(manifest)) > 524288:
        raise ValueError("Worksheet inventory exceeds preview limits")
    workbook.close()
    cached.close()
    return GeneratedArtifact(
        calculation=CalculationReport(
            status="unsupported" if unsupported else "not_calculated",
            warnings=tuple(sorted(unsupported)),
        ),
        preview_manifest=manifest,
    )
