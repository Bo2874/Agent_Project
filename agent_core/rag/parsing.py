"""Bounded extraction for a worker child process. OCR and remote services stay off."""

import re
import shutil
import subprocess
import zipfile
from importlib.metadata import version
from pathlib import Path

from agent_core.rag.documents import ParsedBlock, ParsedDocument, PreparationError


def _convert_doc(path: Path, work: Path, options: dict) -> Path:
    executable = shutil.which(options["libreoffice_path"])
    if not executable:
        raise PreparationError(
            "libreoffice_missing",
            "Đọc DOC cần LibreOffice. Hãy cấu hình DOCUMENT_LIBREOFFICE_PATH.",
        )
    profile, output = work / "office-profile", work / "converted"
    (profile / "user").mkdir(parents=True)
    output.mkdir()
    # A separate profile prevents attaching to a user's desktop Office process.
    (profile / "user/registrymodifications.xcu").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<oor:items xmlns:oor="http://openoffice.org/2001/registry">'
        '<item oor:path="/org.openoffice.Office.Common/Security/Scripting">'
        '<prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item>'
        '<item oor:path="/org.openoffice.Office.Writer/Content/Update">'
        '<prop oor:name="Link" oor:op="fuse"><value>0</value></prop></item></oor:items>',
        encoding="utf-8",
    )
    try:
        completed = subprocess.run(
            [
                executable,
                f"-env:UserInstallation={profile.resolve().as_uri()}",
                "--headless",
                "--nologo",
                "--nodefault",
                "--norestore",
                "--nofirststartwizard",
                "--convert-to",
                "docx:Office Open XML Text",
                "--outdir",
                str(output),
                str(path),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=min(120, options["timeout_seconds"]),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise PreparationError(
            "doc_conversion_timeout", "Chuyển DOC sang DOCX quá thời gian."
        ) from exc
    converted = output / f"{path.stem}.docx"
    if completed.returncode or not converted.is_file():
        raise PreparationError("doc_conversion_failed", "Không chuyển được DOC sang DOCX.")
    if converted.stat().st_size > options["max_unpacked_bytes"]:
        raise PreparationError("doc_conversion_too_large", "Nội dung Word sau chuyển đổi quá lớn.")
    with zipfile.ZipFile(converted) as archive:
        entries = archive.infolist()
        if (
            len(entries) > options["max_archive_entries"]
            or sum(item.file_size for item in entries) > options["max_unpacked_bytes"]
        ):
            raise PreparationError(
                "doc_conversion_too_large", "Nội dung Word sau chuyển đổi quá lớn."
            )
    return converted


def _pdf_preflight(path: Path, options: dict) -> tuple[int, list[dict]]:
    from pypdf import PdfReader

    try:
        with path.open("rb") as source:
            reader = PdfReader(source, strict=False)
            if reader.is_encrypted:
                raise PreparationError(
                    "pdf_encrypted", "Chưa hỗ trợ PDF được mã hóa hoặc có mật khẩu."
                )
            count = len(reader.pages)
            if not count or count > options["max_pages"]:
                raise PreparationError(
                    "pdf_page_limit", f"PDF phải có từ 1 đến {options['max_pages']} trang."
                )
            empty, sparse, total = [], [], 0
            for number, page in enumerate(reader.pages, 1):
                text = page.extract_text() or ""
                total += len(text)
                if total > options["max_text_chars"]:
                    raise PreparationError(
                        "document_text_limit", "Nội dung trích xuất vượt giới hạn."
                    )
                meaningful = sum(char.isalnum() for char in text)
                if not meaningful:
                    empty.append(number)
                elif meaningful < 20:
                    sparse.append(number)
            if len(empty) == count:
                raise PreparationError(
                    "pdf_no_text", "PDF không có lớp văn bản đọc được. OCR chưa được hỗ trợ."
                )
    except PreparationError:
        raise
    except Exception as exc:
        raise PreparationError("pdf_invalid", "Không đọc được PDF; file có thể bị hỏng.") from exc
    warnings = []
    if empty or sparse:
        warnings.append(
            {
                "code": "pdf_text_coverage",
                "pages_without_text": empty,
                "pages_with_little_text": sparse,
                "message": "Một số trang ít/không có text. Chưa đọc chữ trong ảnh bằng OCR.",
            }
        )
    return count, warnings


def _table_text(item, document, max_chars: int) -> tuple[str, int]:
    from docling_core.transforms.serializer.markdown import MarkdownDocSerializer
    from docling_core.types.doc import RichTableCell

    data = item.data
    if data.num_rows * data.num_cols > 200_000:
        raise PreparationError("table_too_large", "Bảng trong tài liệu vượt giới hạn xử lý.")
    grid = [[""] * data.num_cols for _ in range(data.num_rows)]
    header_rows = set()
    size = 0
    serializer = (
        MarkdownDocSerializer(doc=document)
        if any(isinstance(cell, RichTableCell) for cell in data.table_cells)
        else None
    )
    for cell in data.table_cells:
        row, column = cell.start_row_offset_idx, cell.start_col_offset_idx
        if 0 <= row < data.num_rows and 0 <= column < data.num_cols:
            # Keep one copy of merged cell content at its source coordinate.
            text = (
                serializer.serialize(item=cell.ref.resolve(document)).text
                if isinstance(cell, RichTableCell)
                else cell.text
            )
            value = text.replace("\r", " ").replace("\n", " ").replace("\t", " ")
            size += len(value)
            if size > max_chars:
                raise PreparationError("document_text_limit", "Nội dung bảng vượt giới hạn.")
            grid[row][column] = value
            if cell.column_header:
                header_rows.add(row)
    header_count = 0
    while header_count in header_rows:
        header_count += 1
    return "\n".join("\t".join(row) for row in grid), header_count


def parse_document(path: Path, file_type: str, work: Path, options: dict) -> ParsedDocument:
    if file_type == "txt":
        raw = path.read_bytes()
        encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
        text = raw.decode(encoding)
        if len(text) > options["max_text_chars"]:
            raise PreparationError("document_text_limit", "Nội dung TXT vượt giới hạn.")
        blocks, headings, start = [], [], 0
        boundaries = [
            (match.start(), match.end()) for match in re.finditer(r"\r?\n[ \t]*\r?\n", text)
        ]
        for end, next_start in [*boundaries, (len(text), len(text))]:
            paragraph = text[start:end]
            if not paragraph.strip():
                start = next_start
                continue
            heading = re.match(r"^(#{1,6})\s+(.+)$", paragraph.strip())
            if heading:
                level = len(heading[1])
                headings = headings[: level - 1] + [heading[2].strip()]
            blocks.append(
                ParsedBlock(
                    kind="heading" if heading else "text",
                    start=start,
                    end=end,
                    section=" / ".join(headings)[-1000:] or None,
                    heading_path=list(headings),
                )
            )
            start = next_start
        if not blocks:
            raise PreparationError("document_no_text", "Tài liệu không có nội dung văn bản.")
        return ParsedDocument(
            original_text=text,
            blocks=blocks,
            parser_version="python-txt-v1",
            warnings=[{"code": "text_encoding", "encoding": encoding}],
        )

    from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
    from docling.datamodel.backend_options import MsWordBackendOptions
    from docling.datamodel.base_models import ConversionStatus, InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption, WordFormatOption
    from docling_core.types.doc import ContentLayer

    page_count, warnings = _pdf_preflight(path, options) if file_type == "pdf" else (None, [])
    converted = _convert_doc(path, work, options) if file_type == "doc" else path
    pdf_options = PdfPipelineOptions(
        do_ocr=False,
        do_table_structure=True,
        enable_remote_services=False,
        allow_external_plugins=False,
        document_timeout=options["timeout_seconds"],
        accelerator_options=AcceleratorOptions(device=AcceleratorDevice.CPU, num_threads=2),
    )
    converter = DocumentConverter(
        allowed_formats=[InputFormat.PDF, InputFormat.DOCX],
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pdf_options),
            InputFormat.DOCX: WordFormatOption(
                backend_options=MsWordBackendOptions(
                    enable_remote_fetch=False,
                    enable_local_fetch=False,
                    max_total_bytes=options["max_unpacked_bytes"],
                    max_file_bytes=options["max_unpacked_bytes"],
                    max_member_count=options["max_archive_entries"],
                )
            ),
        },
    )
    result = converter.convert(
        converted,
        raises_on_error=False,
        max_num_pages=options["max_pages"],
        max_file_size=options["max_unpacked_bytes"],
    )
    if result.status != ConversionStatus.SUCCESS:
        # Never publish partially parsed documents as if their text were complete.
        raise PreparationError("document_parse_failed", "Không trích xuất đầy đủ được tài liệu.")
    document = result.document
    pieces, blocks, headings, length = [], [], [], 0
    image_pages, text_pages = set(), set()
    image_count = len(document.pictures)
    furniture = 0
    table_children = set()
    for item, _level in document.iterate_items(included_content_layers=set(ContentLayer)):
        if item.self_ref in table_children:
            continue
        label = str(getattr(item.label, "value", item.label))
        prov = getattr(item, "prov", [])
        pages = sorted({int(p.page_no) for p in prov}) if file_type == "pdf" else []
        if label == "picture":
            image_pages.update(pages)
            continue
        is_table = label in {"table", "document_index"}
        text, header_rows = (
            _table_text(item, document, options["max_text_chars"])
            if is_table
            else (getattr(item, "orig", None) or getattr(item, "text", ""), 0)
        )
        if is_table:
            from docling_core.types.doc import RichTableCell

            for cell in item.data.table_cells:
                if isinstance(cell, RichTableCell):
                    table_children.update(
                        child.self_ref
                        for child, _ in document.iterate_items(
                            root=cell.ref.resolve(document),
                            with_groups=True,
                            included_content_layers=set(ContentLayer),
                        )
                    )
        if not text.strip():
            continue
        if "\x00" in text:
            raise PreparationError(
                "document_invalid_text", "Văn bản trích xuất chứa ký tự không hợp lệ."
            )
        heading = label in {"section_header", "title"}
        if heading:
            depth = min(12, max(1, int(getattr(item, "level", 1))))
            headings = headings[: depth - 1] + [text.strip()]
        kind = "heading" if heading else "table" if is_table else "text"
        if label in {"page_header", "page_footer"}:
            kind = "furniture"
            furniture += 1
        prefix = "\n\n" if pieces else ""
        start, end = length + len(prefix), length + len(prefix) + len(text)
        if end > options["max_text_chars"]:
            raise PreparationError("document_text_limit", "Nội dung trích xuất vượt giới hạn.")
        pieces.extend((prefix, text))
        blocks.append(
            ParsedBlock(
                kind=kind,
                start=start,
                end=end,
                section=" / ".join(headings)[-1000:] or None,
                heading_path=list(headings),
                page_start=pages[0] if pages else None,
                page_end=pages[-1] if pages else None,
                source_ref=item.self_ref,
                table_header_rows=header_rows,
            )
        )
        length = end
        if kind != "furniture":
            text_pages.update(pages)
    if not blocks or not any(block.kind != "furniture" for block in blocks):
        raise PreparationError("document_no_text", "Không trích xuất được nội dung văn bản.")
    if furniture:
        warnings.append(
            {
                "code": "page_furniture",
                "count": furniture,
                "message": "Header/footer được giữ trong bản trích nhưng không đưa vào chunk.",
            }
        )
    if image_count:
        warnings.append(
            {
                "code": "images_without_ocr",
                "pages": sorted(image_pages),
                "count": image_count,
                "message": "Chưa đọc nội dung chữ trong ảnh bằng OCR.",
            }
        )
    missing = sorted(set(range(1, (page_count or 0) + 1)) - text_pages)
    if missing:
        warnings.append(
            {
                "code": "pages_without_extracted_text",
                "pages": missing,
                "message": "Các trang này không có nội dung text được đưa vào chunk.",
            }
        )
    parser = f"docling-{version('docling-slim')}/core-{version('docling-core')}/v1"
    if file_type == "doc":
        parser += "/libreoffice-docx"
        warnings.append(
            {"code": "doc_converted", "message": "DOC được chuyển sang DOCX trước khi đọc."}
        )
    return ParsedDocument(
        original_text="".join(pieces),
        blocks=blocks,
        page_count=page_count,
        parser_version=parser,
        warnings=warnings,
    )
