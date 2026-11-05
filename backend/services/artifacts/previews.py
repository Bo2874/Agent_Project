"""Stage every preview page before the immutable version transaction begins."""

from artifact_runtime.protocol import DOCX_MIME, PDF_MIME, PNG_MIME
from backend.services.artifacts.validation import ValidatedCompanion, validate_preview_png


async def stage_preview(storage, stored, run, cleanup):
    pdf, companions = stored, []
    if stored.mime_type == DOCX_MIME:
        rendered = await run("render-document", stored=stored, action="render_document")
        cleanup.update(item.key for item in rendered.files)
        if len(rendered.files) != 1 or rendered.files[0].mime_type != PDF_MIME:
            raise ValueError("Writer did not produce the expected preview PDF")
        pdf = rendered.files[0]
        companions.append(ValidatedCompanion("preview_pdf", pdf))
    elif stored.mime_type != PDF_MIME:
        raise ValueError("Unsupported document preview format")
    count, offset, assets, warnings, metrics = None, 0, [], [], []
    while count is None or offset < count:
        batch = await run(
            f"render-pages-{offset}",
            stored=pdf,
            action="render_pages",
            preview_offset=offset,
            preview_limit=3,
        )
        cleanup.update(item.key for item in batch.files)
        preview = batch.manifest.preview_manifest
        total = preview.get("page_count")
        if type(total) is not int or not 1 <= total <= 20 or (count is not None and count != total):
            raise ValueError("Invalid or inconsistent rendered page count")
        count = total
        expected = list(range(offset + 1, min(offset + 3, count) + 1))
        descriptions = preview.get("assets", [])
        if (
            preview.get("source_sha256") != pdf.sha256
            or len(batch.files) != len(expected)
            or [item.get("page") for item in descriptions] != expected
        ):
            raise ValueError("Incomplete or mismatched preview page batch")
        for file, description in zip(batch.files, descriptions, strict=True):
            if file.mime_type != PNG_MIME or file.sha256 != description.get("sha256"):
                raise ValueError("Invalid preview page checksum or MIME")
            width, height = validate_preview_png(storage.path(file.key))
            companion = ValidatedCompanion("preview_page", file, page=description["page"])
            companions.append(companion)
            assets.append(
                {
                    "asset_id": str(companion.asset_id),
                    "page": companion.page,
                    "sha256": file.sha256,
                    "width": width,
                    "height": height,
                }
            )
        warnings = preview.get("warnings", [])
        metrics.append(preview.get("render_metrics", {}))
        offset += len(expected)
    return {
        "kind": "pages",
        "page_count": count,
        "assets": assets,
        "source_sha256": stored.sha256,
        "warnings": warnings,
        "render_metrics": metrics,
    }, tuple(companions)
