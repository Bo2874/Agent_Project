import os
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from docx import Document
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from test_artifact_api import api  # noqa: F401
from test_artifact_tools import bridge  # noqa: F401

from artifact_runtime.document_types import DocumentBlocks
from artifact_runtime.protocol import ArtifactOperation, ArtifactRunRequest, unpack_bundle
from backend.models.artifact import ArtifactVersion


def text_pdf(path, *, blank=False, mixed=False, image=False):
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    if not blank:
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
        )
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 12 Tf 50 740 Td (Original text) Tj ET")
        page[NameObject("/Contents")] = writer._add_object(stream)
    if image:
        picture = DecodedStreamObject()
        picture.set_data(b"\xff\x00\x00")
        picture.update(
            {
                NameObject("/Type"): NameObject("/XObject"),
                NameObject("/Subtype"): NameObject("/Image"),
                NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
            }
        )
        page.setdefault(NameObject("/Resources"), DictionaryObject())[NameObject("/XObject")] = (
            DictionaryObject({NameObject("/Im1"): writer._add_object(picture)})
        )
    if mixed:
        writer.add_blank_page(width=612, height=792)
    with path.open("wb") as output:
        writer.write(output)
    return path


def test_scan_and_mixed_pages_not_reported_complete(tmp_path):
    from artifact_runtime.pdf import read_pdf_text

    scan = read_pdf_text(text_pdf(tmp_path / "scan.pdf", blank=True))
    assert scan.coverage == "unsupported_scanned_pdf" and scan.warnings
    mixed = read_pdf_text(text_pdf(tmp_path / "mixed.pdf", mixed=True))
    assert mixed.coverage == "partial" and mixed.omitted_pages == [2]
    assert mixed.pages[0].text == "Original text"
    assert mixed.warnings
    picture = read_pdf_text(text_pdf(tmp_path / "picture.pdf", image=True))
    assert picture.coverage == "partial" and picture.omitted_pages == [1]


def test_pdf_bounds_and_loss_consent_are_enforced(tmp_path):
    from artifact_runtime.pdf import rebuild_pdf

    content = DocumentBlocks(blocks=[{"kind": "paragraph", "text": "New text"}])
    with pytest.raises(ValueError, match="unsupported_scanned_pdf"):
        rebuild_pdf(
            content,
            source_pdf=text_pdf(tmp_path / "scan.pdf", blank=True),
            output_dir=tmp_path / "a",
        )
    with pytest.raises(ValueError, match="[Aa]pproval|[Cc]onsent"):
        rebuild_pdf(
            content,
            source_pdf=text_pdf(tmp_path / "mixed.pdf", mixed=True),
            output_dir=tmp_path / "b",
        )
    assert not list(tmp_path.glob("*/output*"))


def test_safe_pdf_navigation_is_not_active_content(tmp_path):
    from pypdf.generic import ArrayObject, NumberObject

    from artifact_runtime.pdf import read_pdf_text

    path = text_pdf(tmp_path / "navigation.pdf")
    writer = PdfWriter(clone_from=path)
    writer._root_object[NameObject("/OpenAction")] = ArrayObject(
        [
            writer.pages[0].indirect_reference,
            NameObject("/XYZ"),
            NumberObject(0),
            NumberObject(0),
            NumberObject(0),
        ]
    )
    writer.add_named_destination("Heading", page_number=0)
    with path.open("wb") as stream:
        writer.write(stream)
    assert read_pdf_text(path).coverage == "complete"


def test_pdf_inspection_can_page_within_long_page(tmp_path):
    from artifact_runtime.pdf import pdf_preview, read_pdf_text
    from artifact_runtime.protocol import BlobDescriptor

    path = text_pdf(tmp_path / "long.pdf")
    writer = PdfWriter(clone_from=path)
    stream = DecodedStreamObject()
    text = "Long text " * 2000
    stream.set_data(b"BT /F1 12 Tf 50 740 Td (" + text.encode() + b") Tj ET")
    writer.pages[0][NameObject("/Contents")] = writer._add_object(stream)
    with path.open("wb") as target:
        writer.write(target)
    extracted = read_pdf_text(path)
    recovered, offset = "", 0
    while True:
        request = ArtifactRunRequest(
            job_id=uuid4(),
            action="inspect",
            preview_limit=1,
            preview_text_offset=offset,
            inputs=(BlobDescriptor.from_bytes("input-0.pdf", path.read_bytes()),),
        )
        result = pdf_preview(extracted, request)
        page = result["pages"][0]
        recovered += page["text"]
        if page["next_text_offset"] is None:
            break
        offset = page["next_text_offset"]
    assert recovered == extracted.pages[0].text


async def test_pdf_text_edit_reflows_and_preserves_requested_content(tmp_path):
    from artifact_runtime.containers import DockerBroker

    if os.environ.get("ARTIFACT_RUNNER_DOCKER") != "1":
        pytest.skip("Requires artifact render image")
    content = {
        "blocks": [
            {"kind": "heading", "text": "Báo cáo tiếng Việt sau chỉnh sửa", "level": 1},
            {"kind": "paragraph", "text": "Nội dung được dàn lại. " * 250},
            {"kind": "table", "rows": [["Hạng mục", "Số lượng"], ["Sách", "15"]]},
        ]
    }
    broker = DockerBroker(tmp_path / "broker", docker=os.environ.get("ARTIFACT_DOCKER", "docker"))
    bundle = await broker.run(
        ArtifactRunRequest(
            job_id=uuid4(), operation=ArtifactOperation(kind="rebuild_pdf", payload=content)
        ),
        (),
    )
    with bundle.open("rb") as stream:
        manifest, paths = unpack_bundle(stream, tmp_path / "result")
    pdf = next(path for path in paths if path.suffix == ".pdf")
    source = next(path for path in paths if path.suffix == ".docx")
    text = "\n".join(page.extract_text() for page in PdfReader(pdf).pages)
    assert "Báo cáo tiếng Việt sau chỉnh sửa" in text
    assert "Hạng mục" in text and "Sách" in text and "15" in text
    assert len(PdfReader(pdf).pages) > 1
    assert Document(source).tables[0].cell(1, 1).text == "15"
    assert manifest.preview_manifest["editable_source"] == next(
        item.name for item in manifest.files if item.name.endswith(".docx")
    )


async def test_pdf_revision_uses_own_source(bridge):  # noqa: F811
    service, context, sessions = bridge
    first = await service.execute(
        context,
        ArtifactOperation(
            kind="rebuild_pdf", payload={"blocks": [{"kind": "paragraph", "text": "Base version"}]}
        ),
        name="report.pdf",
    )
    assert not first.is_error, first
    ref = first.data["artifact_refs"][0]
    for index, text in enumerate(["Branch A", "Branch B"]):
        result = await service.execute(
            replace(context, operation_key=f"pdf-{index}"),
            ArtifactOperation(
                kind="rebuild_pdf",
                parent_version_id=ref["version_id"],
                payload={"blocks": [{"kind": "paragraph", "text": text}]},
            ),
            name="report.pdf",
        )
        assert not result.is_error, result
        async with sessions() as session:
            row = await session.get(
                ArtifactVersion, UUID(result.data["artifact_refs"][0]["version_id"])
            )
            companion = next(
                item for item in row.companion_sources if item["role"] == "editable_source"
            )
            assert companion["version_id"] == str(row.id)
            assert row.parent_version_id == UUID(ref["version_id"])
            document = Document(service.storage_service().storage.path(companion["storage_key"]))
            assert document.paragraphs[0].text == text
    # Inspect the original after sibling revisions: it must still resolve its own DOCX.
    inspection = await service.inspect(context, version_id=UUID(ref["version_id"]))
    assert not inspection.is_error and "Base version" in inspection.content
    assert "Branch A" not in inspection.content and "Branch B" not in inspection.content
    import json

    node = json.loads(inspection.content)["nodes"][0]["node_id"]
    edited = await service.execute(
        replace(context, operation_key="original-source-edit"),
        ArtifactOperation(
            kind="rebuild_pdf",
            parent_version_id=ref["version_id"],
            payload={
                "operations": [
                    {
                        "kind": "replace_text",
                        "node_id": node,
                        "old": "Base",
                        "new": "Original branch",
                    }
                ]
            },
        ),
    )
    assert not edited.is_error, edited
    child = await service.inspect(
        context, version_id=UUID(edited.data["artifact_refs"][0]["version_id"])
    )
    assert "Original branch version" in child.content and "Branch B" not in child.content


async def test_text_only_pdf_rebuild_requires_host_approval():
    from agent_core.tools import ToolContext, ToolExecutor, ToolRegistry
    from agent_core.tools.artifacts import register_artifact_tools

    registry = ToolRegistry()
    register_artifact_tools(registry, object())
    context = ToolContext(user_id=uuid4(), conversation_id=uuid4(), user_role="user")
    result = await ToolExecutor().execute(
        registry,
        context,
        "artifact_rebuild_pdf_text_only",
        {
            "parent_version_id": str(uuid4()),
            "payload": {
                "content": {"blocks": [{"kind": "paragraph", "text": "Approved text"}]},
                "omitted_pages": [2],
            },
        },
    )
    assert result.error_code == "tool_approval_required"


async def test_partial_pdf_rebuild_only_after_exact_coverage_approval(bridge, api, tmp_path):  # noqa: F811
    from agent_core.tools import ToolExecutor, ToolRegistry
    from agent_core.tools.artifacts import register_artifact_tools

    client = api[0]
    path = text_pdf(tmp_path / "mixed.pdf", mixed=True)
    uploaded = await client.post(
        "/api/artifacts", files={"file": ("mixed.pdf", path.read_bytes(), "application/pdf")}
    )
    assert uploaded.status_code == 201, uploaded.text
    ref = uploaded.json()
    service, context, _ = bridge
    context = replace(context, artifact_version_ids=(UUID(ref["version_id"]),))
    registry = ToolRegistry()
    register_artifact_tools(registry, service)
    content = {"blocks": [{"kind": "paragraph", "text": "Approved extracted text"}]}
    ordinary = await ToolExecutor().execute(
        registry,
        context,
        "artifact_edit",
        {"format": "pdf", "parent_version_id": ref["version_id"], "payload": content},
    )
    assert ordinary.is_error and "approval" in ordinary.content.lower()
    executor = ToolExecutor()
    for omitted, success in [([1], False), ([2], True)]:
        result = await executor.execute(
            registry,
            replace(context, operation_key=f"approved-{omitted[0]}"),
            "artifact_rebuild_pdf_text_only",
            {
                "parent_version_id": ref["version_id"],
                "payload": {"content": content, "omitted_pages": omitted},
            },
            approval_granted=True,
        )
        assert (not result.is_error) == success, result
