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
    assert name == "run_python_code"
    assert args["outputs"][0]["path"].endswith("." + format)
    assert "code" in args


async def test_offline_fixture_inspects_before_editing_a_document():
    parent = str(uuid4())
    messages = [{"role": "user", "content": f"edit {parent} DOCX"}]
    name, args = await model_call(messages)
    assert name == "run_python_code" and args["input_version_ids"] == [parent]
    assert args["outputs"][0]["parent_version_id"] == parent
    assert "Document(" in args["code"]
    messages.append(
        {
            "role": "tool",
            "tool_call_id": "python-fixture-generate",
            "content": json.dumps(
                {
                    "content": "generated",
                    "data": {"artifact_refs": [{"version_id": parent}]},
                    "is_error": False,
                }
            ),
        }
    )
    name, args = await model_call(messages)
    assert name == "run_python_code" and args["input_version_ids"] == [parent]
    assert not args.get("outputs")
    assert "print" in args["code"]
