"""Conservative Word editing. Imported only inside a disposable sandbox."""

import hashlib
import json
import zipfile
from pathlib import Path

from defusedxml import ElementTree

from artifact_runtime.document_types import (
    DocumentBlocks,
    DocumentEdits,
    DocumentNode,
    DocumentOutline,
)

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
TEXT_RUN_CHILDREN = {W + name for name in ("rPr", "t", "tab", "br", "cr")}
UNSUPPORTED = {
    "drawing",
    "pict",
    "object",
    "footnoteReference",
    "endnoteReference",
    "commentReference",
    "ins",
    "del",
    "moveFrom",
    "moveTo",
    "fldSimple",
    "fldChar",
    "instrText",
    "sdt",
    "altChunk",
    "subDoc",
    "customXml",
    "hyperlink",
    "oMath",
    "oMathPara",
}


def load_safe_docx(source: Path):
    from docx import Document

    if source.stat().st_size > 20971520:
        raise ValueError("DOCX exceeds size limit")
    with zipfile.ZipFile(source) as archive:
        members = archive.infolist()
        if len(members) > 2000 or sum(item.file_size for item in members) > 104857600:
            raise ValueError("DOCX expanded size limit exceeded")
        if len({item.filename for item in members}) != len(members):
            raise ValueError("Duplicate DOCX members")
        for item in members:
            name = item.filename
            if any(part in {"..", ""} for part in name.split("/")) or "\\" in name:
                raise ValueError("Invalid DOCX member")
            if name.startswith(("word/media/", "word/embeddings/", "word/activeX/")) or any(
                marker in name.lower()
                for marker in ("vbaproject", "footnotes.xml", "endnotes.xml", "comments.xml")
            ):
                raise ValueError(
                    "Unsupported DOCX content; images, fields and annotations are not supported"
                )
            if name.endswith((".xml", ".rels")):
                tree = ElementTree.fromstring(archive.read(item))
                if name.endswith(".rels") and any(
                    node.get("TargetMode") == "External" for node in tree.iter()
                ):
                    raise ValueError("Unsupported external DOCX relationship")
                if name.startswith("word/") and any(
                    node.tag.rsplit("}", 1)[-1] in UNSUPPORTED or node.tag.endswith("Change")
                    for node in tree.iter()
                ):
                    raise ValueError("Unsupported DOCX content; refusing a lossy edit")
                if name.startswith("word/"):
                    for run in tree.iter(W + "r"):
                        for child in run:
                            # run.text reconstructs children: only accept the text forms it
                            # roundtrips. Symbols, discretionary hyphens and special breaks
                            # must never disappear when adjacent text is replaced.
                            if child.tag not in TEXT_RUN_CHILDREN or (
                                child.tag == W + "br"
                                and (
                                    child.get(W + "type", "textWrapping") != "textWrapping"
                                    or child.get(W + "clear", "none") != "none"
                                )
                            ):
                                raise ValueError(
                                    "Unsupported DOCX run content; refusing a lossy edit"
                                )
    document = Document(source)
    if any(child.tag not in {W + "p", W + "tbl", W + "sectPr"} for child in document.element.body):
        raise ValueError("Unsupported document block")
    return document


def _blocks(document):
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    return [
        Table(child, document) if child.tag == W + "tbl" else Paragraph(child, document)
        for child in document.element.body
        if child.tag in {W + "p", W + "tbl"}
    ]


def inspect_docx(source: Path, *, offset=0, limit=100) -> DocumentOutline:
    from docx.table import Table

    document = load_safe_docx(source)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    blocks = _blocks(document)
    nodes, warnings = [], []
    remaining = 12000
    for index in range(offset, min(len(blocks), offset + min(limit, 100))):
        block = blocks[index]
        if isinstance(block, Table):
            rows, budget, truncated = [], remaining - 400, False
            for row in block.rows[:50]:
                cells = []
                for cell in row.cells[:30]:
                    if budget < 30:
                        truncated = True
                        break
                    # Six is the maximum JSON escape expansion for one character.
                    text = cell.text[: min(2000, max(1, (budget - 10) // 6))]
                    truncated |= len(text) < len(cell.text)
                    cells.append(text)
                    budget -= len(json.dumps(text, ensure_ascii=False)) + 4
                rows.append(cells)
                budget -= 4
                if budget < 30:
                    break
            nodes.append(DocumentNode(node_id=f"{digest}:{index}", kind="table", rows=rows))
            if (
                truncated
                or len(rows) < len(block.rows)
                or any(len(row.cells) > 30 for row in block.rows)
            ):
                warnings.append("Table preview truncated; source table is retained")
        else:
            style = block.style.name
            kind = (
                "heading"
                if style.startswith("Heading")
                else "list"
                if style.startswith("List")
                else "paragraph"
            )
            text = block.text[: max(1, (remaining - 400) // 6)]
            nodes.append(
                DocumentNode(node_id=f"{digest}:{index}", kind=kind, text=text, style=style)
            )
            if len(text) < len(block.text):
                warnings.append("Paragraph preview truncated; source text is retained")
        remaining -= len(nodes[-1].model_dump_json()) + 50
        if remaining <= 500:
            warnings.append("Outline limit reached; continue with a later block offset")
            break
    return DocumentOutline(
        source_sha256=digest, nodes=nodes, total_nodes=len(blocks), warnings=warnings
    )


def add_blocks(document, content: DocumentBlocks):
    for block in content.blocks:
        if block.kind == "table":
            table = document.add_table(rows=len(block.rows), cols=len(block.rows[0]))
            table.style = "Table Grid"
            for row_index, row in enumerate(block.rows):
                for col_index, text in enumerate(row):
                    table.cell(row_index, col_index).text = text
        elif block.kind == "heading":
            document.add_heading(block.text, level=block.level)
        elif block.kind == "list":
            document.add_paragraph(
                block.text, style="List Number" if block.ordered else "List Bullet"
            )
        else:
            document.add_paragraph(block.text)


def _replace(paragraph, old, new):
    # Resolve one unambiguous span before changing any runs. Never replace paragraph XML.
    text = paragraph.text
    if text.count(old) != 1:
        raise ValueError("Replacement must match exactly one text span")
    start, end, position = text.index(old), text.index(old) + len(old), 0
    inserted = False
    for run in paragraph.runs:
        original = run.text
        left, right = position, position + len(original)
        position = right
        if right <= start or left >= end:
            continue
        prefix = original[: max(0, start - left)]
        suffix = original[max(0, end - left) :] if end < right else ""
        run.text = prefix + (new if not inserted else "") + suffix
        inserted = True


def apply_document(operation, *, input_path: Path | None, output_dir: Path):
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt
    from docx.table import Table

    from artifact_runtime.protocol import BlobDescriptor, GeneratedArtifact

    if operation.kind == "create_docx":
        if input_path is not None:
            raise ValueError("Creating a document cannot consume an input")
        document = Document()
        document.styles["Normal"].font.name = "DejaVu Sans"
        document.styles["Normal"].font.size = Pt(11)
        add_blocks(document, DocumentBlocks.model_validate(operation.payload))
    elif operation.kind == "edit_docx" and input_path is not None:
        document = load_safe_docx(input_path)
        edits = DocumentEdits.model_validate(operation.payload)
        digest = hashlib.sha256(input_path.read_bytes()).hexdigest()
        original = _blocks(document)
        targets = {}
        for edit in edits.operations:
            if edit.kind == "append_blocks":
                continue
            token, index = edit.node_id.split(":")
            if token != digest or int(index) >= len(original):
                raise ValueError("Stale node ID: inspect the pinned input version first")
            targets[edit.node_id] = original[int(index)]
        for edit in edits.operations:
            if edit.kind == "append_blocks":
                add_blocks(document, DocumentBlocks(blocks=edit.blocks))
                continue
            target = targets[edit.node_id]
            if edit.kind == "set_table_cell":
                if (
                    not isinstance(target, Table)
                    or edit.row >= len(target.rows)
                    or edit.column >= len(target.rows[edit.row].cells)
                ):
                    raise ValueError("Invalid table cell")
                cell = target.cell(edit.row, edit.column)
                if cell.tables or len(cell.paragraphs) != 1:
                    raise ValueError("Unsupported complex cell replacement")
                paragraph = cell.paragraphs[0]
                if paragraph.text:
                    _replace(paragraph, paragraph.text, edit.text)
                else:
                    paragraph.add_run(edit.text)
            elif isinstance(target, Table):
                raise ValueError("Operation requires a paragraph node")
            elif edit.kind == "replace_text":
                _replace(target, edit.old, edit.new)
            elif edit.kind == "format_block":
                if edit.style is not None:
                    if edit.style not in document.styles:
                        raise ValueError("Unknown document style")
                    target.style = document.styles[edit.style]
                if edit.alignment is not None:
                    target.alignment = getattr(WD_ALIGN_PARAGRAPH, edit.alignment.upper())
                for run in target.runs:
                    if edit.font_size is not None:
                        run.font.size = Pt(edit.font_size)
                    if edit.bold is not None:
                        run.bold = edit.bold
                    if edit.italic is not None:
                        run.italic = edit.italic
    else:
        raise ValueError("Editing requires a pinned DOCX input")
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / "output-0.docx"
    document.save(target)
    load_safe_docx(target)  # ZIP validity alone is insufficient.
    return GeneratedArtifact(files=(BlobDescriptor.from_bytes(target.name, target.read_bytes()),))
