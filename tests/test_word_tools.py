import io
import json
from dataclasses import replace
from uuid import UUID

from docx import Document
from test_artifact_api import api  # noqa: F401
from test_artifact_tools import bridge  # noqa: F401

from agent_core.tools import ToolExecutor, ToolRegistry
from agent_core.tools.artifacts import register_artifact_tools
from artifact_runtime.protocol import DOCX_MIME
from backend.models.artifact import ArtifactVersion


async def test_word_create_edit_through_tool_registry_and_real_runner(bridge):  # noqa: F811
    service, context, sessions = bridge
    registry = ToolRegistry()
    register_artifact_tools(registry, service)
    result = await ToolExecutor().execute(
        registry,
        context,
        "artifact_create",
        {
            "format": "docx",
            "name": "report.docx",
            "payload": {
                "blocks": [
                    {"kind": "heading", "text": "Vietnamese report", "level": 1},
                    {"kind": "paragraph", "text": "Original content"},
                ]
            },
        },
    )
    assert not result.is_error, result
    ref = result.data["artifact_refs"][0]
    assert ref["mime_type"] == DOCX_MIME
    inspection = await service.inspect(context, version_id=UUID(ref["version_id"]))
    assert not inspection.is_error, inspection
    outline = json.loads(inspection.content)
    node = outline["nodes"][1]["node_id"]
    changed = await ToolExecutor().execute(
        registry,
        replace(context, operation_key="word-edit"),
        "artifact_edit",
        {
            "format": "docx",
            "parent_version_id": ref["version_id"],
            "payload": {
                "operations": [
                    {"kind": "replace_text", "node_id": node, "old": "Original", "new": "Revised"}
                ]
            },
        },
    )
    assert not changed.is_error, changed
    child = changed.data["artifact_refs"][0]
    async with sessions() as session:
        old = await session.get(ArtifactVersion, UUID(ref["version_id"]))
        new = await session.get(ArtifactVersion, UUID(child["version_id"]))
        storage = service.storage_service().storage
        assert Document(storage.path(old.storage_key)).paragraphs[1].text == "Original content"
        assert Document(storage.path(new.storage_key)).paragraphs[1].text == "Revised content"
        assert new.parent_version_id == old.id


async def test_uploaded_word_is_selectable_and_editable(bridge, api):  # noqa: F811
    service, context, _ = bridge
    content = io.BytesIO()
    document = Document()
    document.add_paragraph("Uploaded Word")
    document.save(content)
    response = await api[0].post(
        "/api/artifacts", files={"file": ("uploaded.docx", content.getvalue(), DOCX_MIME)}
    )
    assert response.status_code == 201, response.text
    ref = response.json()
    version = UUID(ref["version_id"])
    inspection = await service.inspect(
        replace(context, artifact_version_ids=(version,)), version_id=version
    )
    assert not inspection.is_error and "Uploaded Word" in inspection.content
