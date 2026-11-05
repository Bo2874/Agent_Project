import json
from uuid import uuid4

import pytest

from scripts.artifact_fixture import OfflineArtifactModel


async def model_call(messages):
    chunks = [chunk async for chunk in OfflineArtifactModel().stream(messages, tools=[{}])]
    delta = next(chunk.tool_call_deltas[0] for chunk in chunks if chunk.tool_call_deltas)
    return delta.name, json.loads(delta.arguments)


@pytest.mark.parametrize("format", ["docx", "pdf"])
async def test_offline_fixture_creates_requested_document_format(format):
    name, args = await model_call([{"role": "user", "content": "create " + format.upper()}])
    assert name == "artifact_create" and args["format"] == format
    assert args["payload"]["blocks"]


async def test_offline_fixture_inspects_before_editing_a_document():
    parent = str(uuid4())
    messages = [{"role": "user", "content": f"edit {parent} DOCX"}]
    name, args = await model_call(messages)
    assert name == "artifact_inspect" and args["version_id"] == parent
    node = "a" * 64 + ":0"
    messages.append(
        {
            "role": "tool",
            "tool_call_id": "artifact-fixture-inspect",
            "content": json.dumps(
                {
                    "content": json.dumps(
                        {"nodes": [{"node_id": node, "kind": "paragraph", "text": "Old text"}]}
                    ),
                    "data": {},
                }
            ),
        }
    )
    name, args = await model_call(messages)
    assert name == "artifact_edit" and args["parent_version_id"] == parent
    assert args["payload"]["operations"][0]["node_id"] == node
