"""Read-only inspection: reject active content without restricting passive features.

Unlike legacy structured-edit validators, these readers never save the workbook
or document. Unsupported previews are warnings, never a lossy file conversion.
"""

import codecs
import re
import stat
import zipfile
from pathlib import PurePosixPath
from urllib.parse import urlsplit

from defusedxml import ElementTree

from artifact_runtime.protocol import CalculationReport, GeneratedArtifact


def safe_office(path):
    with zipfile.ZipFile(path) as archive:
        entries, names = archive.infolist(), set()
        if not entries or len(entries) > 2000 or sum(x.file_size for x in entries) > 104857600:
            raise ValueError("Office archive exceeds limits")
        for entry in entries:
            name = entry.filename
            lower = name.lower()
            if (
                name in names
                or name.startswith("/")
                or ".." in PurePosixPath(name).parts
                or any(c in name for c in ("\\", ":", "\x00"))
                or entry.flag_bits & 1
                or stat.S_ISLNK(entry.external_attr >> 16)
                or (
                    entry.file_size > 1048576
                    and entry.file_size > 200 * max(entry.compress_size, 1)
                )
            ):
                raise ValueError("Unsafe Office archive member")
            names.add(name)
            if any(
                part in lower
                for part in (
                    "vbaproject",
                    "/embeddings/",
                    "/activex/",
                    "/macrosheets/",
                    "/externallinks/",
                    "/connections",
                    "/querytables/",
                )
            ):
                raise ValueError("Macro, embedded or active external Office content is not allowed")
            if name.endswith(".rels") or name == "[Content_Types].xml":
                if entry.file_size > 1048576:
                    raise ValueError("Office relationship exceeds limits")
                root = ElementTree.fromstring(
                    archive.read(name), forbid_dtd=True, forbid_entities=True, forbid_external=True
                )
                for node in root:
                    content_type = node.attrib.get("ContentType", "").lower()
                    if any(
                        word in content_type
                        for word in ("macroenabled", "vba", "activex", "oleobject")
                    ):
                        raise ValueError("Macro or active Office content is not allowed")
                    if node.attrib.get("TargetMode", "").lower() == "external":
                        target = urlsplit(node.attrib.get("Target", ""))
                        if not node.attrib.get("Type", "").endswith(
                            "/hyperlink"
                        ) or target.scheme.lower() not in {"https", "http", "mailto"}:
                            raise ValueError("Unsafe external Office relationship")
        expected = "xl/workbook.xml" if path.suffix == ".xlsx" else "word/document.xml"
        if not {"[Content_Types].xml", expected} <= names:
            raise ValueError("Office file does not match its declared format")
        # Formulas can launch applications or load external data when opened later.
        for name in names:
            if name.startswith("word/") and name.endswith(".xml"):
                instructions = []
                with archive.open(name) as source:
                    for _, node in ElementTree.iterparse(source, events=("end",)):
                        local = node.tag.rsplit("}", 1)[-1]
                        if local == "instrText":
                            instructions.append(node.text or "")
                        if local == "fldSimple":
                            instructions.extend(
                                value
                                for key, value in node.attrib.items()
                                if key.rsplit("}", 1)[-1] == "instr"
                            )
                        node.clear()
                instruction = "".join(instructions)
                if re.search(
                    r"DDE(?:AUTO)?|INCLUDETEXT|INCLUDEPICTURE|MACROBUTTON|DATABASE|\bLINK\s",
                    instruction,
                    re.I,
                ):
                    raise ValueError("Unsafe active Word field")
                for target in re.findall(r'HYPERLINK\s+"([^"]+)"', instruction, re.I):
                    if not target.startswith("#") and urlsplit(target).scheme.lower() not in {
                        "http",
                        "https",
                        "mailto",
                    }:
                        raise ValueError("Unsafe external Word hyperlink field")
            if name.startswith("xl/worksheets/") and name.endswith(".xml"):
                with archive.open(name) as source:
                    for _, node in ElementTree.iterparse(source, events=("end",)):
                        if node.tag.rsplit("}", 1)[-1] == "f":
                            formula = node.text or ""
                            if re.search(r"HYPERLINK\s*\(", formula, re.I):
                                target = re.search(r'HYPERLINK\s*\(\s*"([^"]+)"', formula, re.I)
                                if target is None or (
                                    not target[1].startswith("#")
                                    and urlsplit(target[1]).scheme.lower()
                                    not in {"http", "https", "mailto"}
                                ):
                                    raise ValueError(
                                        "Unsafe executable or external hyperlink formula"
                                    )
                            if (
                                re.search(
                                    r"(?:WEBSERVICE|DDE|RTD|CALL|REGISTER\.ID|IMAGE)\s*\(",
                                    formula,
                                    re.I,
                                )
                                or "|" in formula
                                or re.search(r"\[[^\]]+\].*!", formula)
                            ):
                                raise ValueError(
                                    "Unsafe executable or external spreadsheet formula"
                                )
                        node.clear()


def inspect_file(path, *, request=None):
    suffix = path.suffix
    if suffix in {".xlsx", ".docx"}:
        safe_office(path)
    if suffix == ".xlsx":
        return spreadsheet_preview(path, request=request)
    if suffix == ".docx":
        from docx import Document

        document = Document(path)
        text = "\n".join(p.text for p in document.paragraphs)
        for table in document.tables:
            for row in table.rows:
                text += "\n" + " | ".join(cell.text for cell in row.cells)
        return GeneratedArtifact(
            preview_manifest={
                "kind": "text",
                "text": text[:16000],
                "truncated": len(text) > 16000,
                "warnings": ["Text preview; original document formatting and bytes are preserved."],
            }
        )
    if suffix == ".pdf":
        from artifact_runtime.pdf import read_pdf_text

        extracted = read_pdf_text(path)
        data = extracted.model_dump(mode="json")
        # Coverage concerns every page, observation text stays bounded.
        remaining = 16000
        for page in data["pages"]:
            text = page["text"]
            page["text"] = text[:remaining]
            remaining -= len(page["text"])
        data["page_count"] = len(data["pages"])
        return GeneratedArtifact(preview_manifest=data)
    if suffix == ".png":
        from PIL import Image

        with Image.open(path) as image:
            if image.format != "PNG" or image.width * image.height > 16000000:
                raise ValueError("PNG exceeds supported dimensions")
            image.verify()
        return GeneratedArtifact(preview_manifest={"kind": "image"})
    if suffix in {".txt", ".md", ".csv", ".json"}:
        with path.open("rb") as source:
            content = source.read(64004)
        text = codecs.getincrementaldecoder("utf-8-sig")().decode(
            content, final=path.stat().st_size <= len(content)
        )
        return GeneratedArtifact(
            preview_manifest={
                "kind": "text",
                "text": text[:16000],
                "truncated": len(text) > 16000 or path.stat().st_size > len(content),
            }
        )
    raise ValueError("Unsupported file format")


def spreadsheet_preview(path, *, request=None):
    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=True, keep_links=False)
    cached = load_workbook(path, read_only=True, data_only=True, keep_links=False)
    try:
        title = (
            request.preview_sheet if request and request.preview_sheet else workbook.sheetnames[0]
        )
        sheet = workbook[title]
        offset = request.preview_offset if request else 0
        columns = min(sheet.max_column or 1, 100)
        limit = min(request.preview_limit if request else 100, max(1, 512 // columns))
        cached_rows = cached[title].iter_rows(
            min_row=offset + 1, max_row=offset + limit, max_col=columns
        )
        cells = []
        for row, cache_row in zip(
            sheet.iter_rows(min_row=offset + 1, max_row=offset + limit, max_col=columns),
            cached_rows,
            strict=True,
        ):
            for cell, cache_cell in zip(row, cache_row, strict=True):
                if cell.value is None:
                    continue
                value = cell.value
                formula = value if cell.data_type == "f" else None
                if not isinstance(value, (str, int, float, bool)):
                    value = str(value)
                cells.append(
                    {
                        "address": cell.coordinate,
                        "row": cell.row,
                        "column": cell.column,
                        "value": None
                        if formula
                        else value[:256]
                        if isinstance(value, str)
                        else value,
                        "formula": str(formula)[:256] if formula else None,
                        "cached_value": cache_cell.value
                        if isinstance(cache_cell.value, (str, int, float, bool))
                        else None,
                        "number_format": cell.number_format[:128],
                        "truncated": isinstance(value, str) and len(value) > 256,
                    }
                )
        return GeneratedArtifact(
            calculation=CalculationReport(
                warnings=(
                    "Original file retained; formula caches may need recalculation in Excel.",
                )
            ),
            preview_manifest={
                "sheet": title,
                "offset": offset,
                "limit": limit,
                "sheets": [
                    {
                        "name": s.title,
                        "rows": s.max_row or 0,
                        "columns": s.max_column or 0,
                        "charts": 0,
                    }
                    for s in workbook
                ],
                "cells": cells,
                "charts": [],
                "columns_truncated": (sheet.max_column or 0) > 100,
                "warnings": [
                    "Table preview only; charts and other formatting remain in the downloaded file."
                ],
            },
        )
    finally:
        workbook.close()
        cached.close()
