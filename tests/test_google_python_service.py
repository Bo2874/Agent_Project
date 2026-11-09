from dataclasses import asdict
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agent_core.tools import ToolContext
from artifact_runtime.protocol import PythonCodeInput
from backend.services.artifacts.python import PythonToolService
from backend.services.artifacts.storage import ArtifactStorage


@pytest.mark.asyncio
@pytest.mark.parametrize("resume", [False, True])
async def test_native_control_not_published_and_snapshots_saved_before_code(tmp_path, resume):
    storage = ArtifactStorage(tmp_path)
    events = []
    context = ToolContext(
        user_id=uuid4(),
        conversation_id=uuid4(),
        user_role="user",
        run_id=uuid4(),
        operation_key="native-call-1",
    )
    source = storage.put(
        BytesIO(b'{"documentId":"doc-a"}'),
        f"{context.user_id}/{uuid4()}/{uuid4()}/content.json",
        max_bytes=1000,
    )
    control = storage.put(
        BytesIO(b'{"requests":[{"insertText":{"text":"Hi","location":{"index":1}}}]}'),
        f"{context.user_id}/{uuid4()}/{uuid4()}/content.json",
        max_bytes=1000,
    )
    snapshot = {"stored": asdict(source)}

    class Bridge:
        async def check(self, context):
            pass

        async def capture(self, context, source):
            events.append("capture")
            return snapshot

        async def execute(self, context, request, snapshots, files, published, key):
            events.append("google")
            assert snapshots == [snapshot]
            assert published == []
            return [{"status": "completed", "url": "https://docs.google.com/document/d/doc-a/edit"}]

    class Service(PythonToolService):
        async def allowed(self, context, request):
            return [], {}, []

        async def load_receipt(self, *args):
            return "prepared", {"google_snapshots": [snapshot]} if resume else None

        async def save_receipt(self, context, key, *, status, result, primary=None):
            events.append(status)
            if status == "prepared":
                assert result["google_snapshots"] == [snapshot]

        async def run(self, context, job, **kwargs):
            events.append("code")
            assert kwargs["stored"] == source
            return SimpleNamespace(
                manifest=SimpleNamespace(
                    execution=SimpleNamespace(
                        model_dump=lambda **kwargs: {
                            "status": "success",
                            "stdout": "ok\n",
                            "stderr": "",
                        }
                    )
                ),
                files=[control],
            )

    service = Service(google_bridge=Bridge())
    request = PythonCodeInput(
        code="print('ok')",
        google_inputs=[
            {
                "ref": {
                    "connection_id": uuid4(),
                    "context_id": context.conversation_id,
                    "file_id": "doc-a",
                }
            }
        ],
        outputs=[{"path": "changes.json", "publish": False}],
        google_actions=[{"kind": "batch_update", "input_index": 0, "request_path": "changes.json"}],
    )
    result = await service.execute(context, request)
    assert not result.is_error, result
    assert result.data["artifact_refs"] == []
    assert result.data["google_results"][0]["status"] == "completed"
    assert events == ([] if resume else ["capture", "prepared"]) + [
        "code",
        "executed",
        "google",
        "completed",
    ]
