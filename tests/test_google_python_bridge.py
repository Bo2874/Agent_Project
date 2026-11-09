import json
from contextlib import asynccontextmanager
from dataclasses import asdict
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import pytest

from artifact_runtime.protocol import PythonCodeInput
from backend.services.artifacts.storage import ArtifactStorage
from backend.services.google.bridge import GooglePythonBridge
from backend.services.google.errors import GoogleServiceError


def update_request():
    return PythonCodeInput(
        code="print('ok')",
        google_inputs=[
            {"ref": {"connection_id": uuid4(), "context_id": uuid4(), "file_id": "file1"}}
        ],
        outputs=[{"path": "changes.json", "publish": False}],
        google_actions=[{"kind": "batch_update", "input_index": 0, "request_path": "changes.json"}],
    )


@pytest.mark.asyncio
async def test_all_control_actions_are_validated_before_remote_write(tmp_path):
    storage = ArtifactStorage(tmp_path)
    key = f"{uuid4()}/{uuid4()}/{uuid4()}/content.json"
    stored = storage.put(
        BytesIO(
            json.dumps(
                {"requests": [{"insertInlineImage": {"uri": "https://outside.test"}}]}
            ).encode()
        ),
        key,
        max_bytes=2097152,
    )
    bridge = GooglePythonBridge(SimpleNamespace(), None, storage)
    request = update_request()
    snapshots = [
        {
            "mime_type": "application/vnd.google-apps.document",
            "source": request.google_inputs[0].model_dump(mode="json"),
        }
    ]
    with pytest.raises(ValueError, match="Unsupported"):
        await bridge.validate_actions(None, request, snapshots, [asdict(stored)])


@pytest.mark.asyncio
async def test_valid_native_action_uses_host_snapshot_target(tmp_path):
    storage = ArtifactStorage(tmp_path)
    key = f"{uuid4()}/{uuid4()}/{uuid4()}/content.json"
    stored = storage.put(
        BytesIO(b'{"requests":[{"insertText":{"text":"Hi","location":{"index":1}}}]}'),
        key,
        max_bytes=2097152,
    )
    bridge = GooglePythonBridge(SimpleNamespace(), None, storage)
    request = update_request()
    snapshot = {
        "mime_type": "application/vnd.google-apps.document",
        "source": request.google_inputs[0].model_dump(mode="json"),
        "revision_id": "rev-host",
    }
    actions = await bridge.validate_actions(None, request, [snapshot], [asdict(stored)])
    assert actions[0]["snapshot"] == snapshot
    assert actions[0]["body"] == {
        "requests": [{"insertText": {"text": "Hi", "location": {"index": 1}}}]
    }


class Journal:
    async def prepare(self, context, **kwargs):
        self.row = SimpleNamespace(id=uuid4(), payload={"request": kwargs["request"]})
        return SimpleNamespace(id=self.row.id, digest="a" * 64, preview=kwargs["preview"])

    async def execute(self, context, **kwargs):
        assert kwargs["automatic"] is True
        assert context.approved_call is None
        auth = await kwargs["authorize"](None, self.row)
        await kwargs["snapshot"](self.row, auth)
        return await kwargs["write"](self.row, auth, None)


@pytest.mark.asyncio
async def test_docs_write_revision_comes_from_snapshot_without_approval(tmp_path):
    from agent_core.tools import ToolContext

    request = update_request()
    ref = request.google_inputs[0].ref
    calls = []

    async def rpc(method, url, **kwargs):
        calls.append((method, url, kwargs["json"]))
        return {"documentId": "file1"}

    bridge = GooglePythonBridge(
        SimpleNamespace(credentials=SimpleNamespace(http=SimpleNamespace(request=rpc))),
        None,
        ArtifactStorage(tmp_path),
    )
    bridge.journal = Journal()

    async def authorized(*args):
        return SimpleNamespace(
            ref=ref, connection_generation=3, grant_generation=2, token="host-only-token"
        )

    async def unchanged(*args):
        pass

    bridge.plan_authority = authorized
    bridge.snapshots.validate = unchanged
    context = ToolContext(
        user_id=uuid4(),
        conversation_id=ref.context_id,
        user_role="user",
        run_id=uuid4(),
        operation_key="python-call-1",
    )
    snapshot = {"mime_type": "application/vnd.google-apps.document", "revision_id": "trusted-rev"}
    receipt = await bridge.execute_one(
        context,
        {
            "snapshot": snapshot,
            "action": {"kind": "batch_update"},
            "body": {"requests": [{"insertText": {"text": "Hi", "location": {"index": 1}}}]},
        },
        "python_native_update",
    )
    assert calls[0][2]["writeControl"] == {"requiredRevisionId": "trusted-rev"}
    assert "token" not in json.dumps(receipt)
    assert receipt["url"].endswith("/file1/edit")


@pytest.mark.asyncio
async def test_all_sources_preflight_before_any_remote_change(tmp_path):
    class Session:
        async def scalar(self, statement):
            return None

    @asynccontextmanager
    async def sessions():
        yield Session()

    bridge = GooglePythonBridge(SimpleNamespace(), sessions, ArtifactStorage(tmp_path))
    events = []

    async def checked(*args):
        pass

    async def plans(*args):
        return [{"snapshot": {"id": 0}, "action": {"kind": "batch_update"}}]

    async def validate(context, snapshot):
        events.append("check" + str(snapshot["id"]))
        if snapshot["id"] == 1:
            raise GoogleServiceError(409, "Source changed", "google_conflict")

    async def mark(*args):
        events.append("blocked")

    async def write(*args):
        events.append("write")

    bridge.check = checked
    bridge.validate_actions = plans
    bridge.snapshots.validate = validate
    bridge.mark_conflict = mark
    bridge.execute_one = write
    from agent_core.tools import ToolContext

    context = ToolContext(
        user_id=uuid4(), conversation_id=uuid4(), user_role="user", run_id=uuid4()
    )
    with pytest.raises(GoogleServiceError) as caught:
        await bridge.execute(context, None, [{"id": 0}, {"id": 1}], [], [], "job")
    assert caught.value.code == "google_conflict"
    assert caught.value.completed == []
    assert events == ["check0", "check1", "blocked"]
