"""Text extraction/reflow only, inside bounded code/render containers. No OCR."""

import hashlib
from pathlib import Path

from artifact_runtime.document_types import DocumentBlocks
from artifact_runtime.pdf_types import PdfPage, PdfTextDocument


def _stream_has_images(stream, reader, page_bytes, total_bytes):
    from pypdf.generic import ContentStream

    if not stream:
        return False
    size = len(stream.get_data())
    page_bytes[0] += size
    total_bytes[0] += size
    if page_bytes[0] > 8388608 or total_bytes[0] > 33554432:
        raise ValueError("PDF expanded content exceeds extraction limit")
    content = stream if isinstance(stream, ContentStream) else ContentStream(stream, reader)
    return any(operator == b"INLINE IMAGE" for _, operator in content.operations)


def _has_images(resources, reader, page_bytes, total_bytes, depth=0):
    if depth > 10:
        raise ValueError("PDF resource nesting limit exceeded")
    objects = resources.get("/XObject", {})
    if hasattr(objects, "get_object"):
        objects = objects.get_object()
    if len(objects) > 200:
        raise ValueError("PDF resource limit exceeded")
    images = False
    for value in objects.values():
        item = value.get_object()
        if item.get("/Subtype") == "/Image":
            images = True
        if item.get("/Subtype") == "/Form":
            inline = _stream_has_images(item, reader, page_bytes, total_bytes)
            nested = _has_images(
                item.get("/Resources", {}), reader, page_bytes, total_bytes, depth + 1
            )
            images |= inline or nested
    return images


def read_pdf_text(source: Path) -> PdfTextDocument:
    from pypdf import PdfReader

    if not 0 < source.stat().st_size <= 20971520:
        raise ValueError("PDF exceeds file size limit")
    reader = PdfReader(source, strict=True)
    if reader.is_encrypted or not 1 <= len(reader.pages) <= 20:
        raise ValueError("Encrypted PDFs and PDFs exceeding 20 pages are not supported")
    root = reader.trailer["/Root"]
    from pypdf.generic import ArrayObject

    action = root.get("/OpenAction")
    if hasattr(action, "get_object"):
        action = action.get_object()
    safe_navigation = action is None or (
        isinstance(action, ArrayObject)
        and len(action) >= 2
        and action[1] in {"/XYZ", "/Fit", "/FitH", "/FitV", "/FitR", "/FitB", "/FitBH", "/FitBV"}
    )
    names = root.get("/Names", {})
    if hasattr(names, "get_object"):
        names = names.get_object()
    if (
        not safe_navigation
        or any(key in root for key in ("/AA", "/AcroForm"))
        or set(names) - {"/Dests"}
    ):
        raise ValueError("Active PDF content, forms and embedded files are not supported")
    pages, omitted, total_bytes = [], [], [0]
    for number, page in enumerate(reader.pages, 1):
        if any(key in page for key in ("/AA", "/Annots")):
            raise ValueError("PDF annotations and active content are not supported")
        stream = page.get_contents()
        page_bytes = [0]
        inline = _stream_has_images(stream, reader, page_bytes, total_bytes)
        resources = page.get("/Resources", {})
        if hasattr(resources, "get_object"):
            resources = resources.get_object()
        images = _has_images(resources, reader, page_bytes, total_bytes) or inline
        text = page.extract_text() or ""
        if len(text) > 200000 or sum(len(item.text) for item in pages) + len(text) > 400000:
            raise ValueError("PDF text exceeds extraction limit")
        if images or not text.strip():
            omitted.append(number)
        pages.append(PdfPage(number=number, text=text, has_images=images))
    coverage = "complete" if not omitted else "partial"
    if not any(page.text.strip() for page in pages):
        coverage = "unsupported_scanned_pdf"
    warnings = (
        []
        if coverage == "complete"
        else [
            "Text extraction is incomplete. Images/blank or unreadable pages are not represented: "
            + ", ".join(map(str, omitted))
            + ". OCR is unavailable. Text-only rebuilding requires user approval."
        ]
    )
    return PdfTextDocument(
        source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        pages=pages,
        coverage=coverage,
        omitted_pages=omitted,
        warnings=warnings,
    )


def pdf_preview(extracted: PdfTextDocument, request):
    if extracted.coverage == "unsupported_scanned_pdf":
        raise ValueError("unsupported_scanned_pdf: OCR is unavailable")
    data = extracted.model_dump(mode="json")
    data["page_count"] = len(data["pages"])
    limit = min(request.preview_limit, 20)
    text_limit = 6000 // limit
    pages = []
    for page in data["pages"][request.preview_offset : request.preview_offset + limit]:
        text = page["text"]
        end = request.preview_text_offset + text_limit
        pages.append(
            {
                **page,
                "text": text[request.preview_text_offset : end],
                "text_total_chars": len(text),
                "text_offset": request.preview_text_offset,
                "next_text_offset": end if end < len(text) else None,
            }
        )
    data["pages"] = pages
    if any(page["next_text_offset"] is not None for page in pages):
        data["warnings"].append(
            "Text observation truncated: use limit=1, page offset and next_text_offset "
            "to read all text before rebuilding."
        )
    return data


def rebuild_pdf(
    content: DocumentBlocks,
    *,
    source_pdf: Path | None,
    output_dir: Path,
    approved_omitted_pages: list[int] | None = None,
):
    from artifact_runtime.document import apply_document
    from artifact_runtime.protocol import ArtifactOperation, BlobDescriptor, GeneratedArtifact
    from artifact_runtime.rendering import export_docx_pdf

    if source_pdf:
        extracted = read_pdf_text(source_pdf)
        if extracted.coverage == "unsupported_scanned_pdf":
            raise ValueError("unsupported_scanned_pdf: no extractable text; OCR is unavailable")
        if extracted.coverage == "partial" and approved_omitted_pages != extracted.omitted_pages:
            raise ValueError(
                "User approval required for text-only rebuild; omitted pages: "
                + str(extracted.omitted_pages)
            )
    # Intermediate files live on private tmpfs, never among published output candidates.
    import tempfile

    with tempfile.TemporaryDirectory(dir="/tmp") as folder:
        source_dir = Path(folder)
        apply_document(
            ArtifactOperation(kind="create_docx", payload=content),
            input_path=None,
            output_dir=source_dir,
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        source = output_dir / "output-1.docx"
        source.write_bytes((source_dir / "output-0.docx").read_bytes())
        target = output_dir / "output-0.pdf"
        export_docx_pdf(source, target)
    return GeneratedArtifact(
        files=(
            BlobDescriptor.from_bytes(target.name, target.read_bytes()),
            BlobDescriptor.from_bytes(source.name, source.read_bytes()),
        ),
        preview_manifest={"editable_source": source.name},
    )
