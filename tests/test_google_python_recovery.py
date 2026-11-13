import asyncio
from copy import deepcopy
from dataclasses import asdict
from io import BytesIO
from uuid import uuid4

import pytest

from agent_core.tools import ToolContext, ToolResult
from artifact_runtime.protocol import PythonCodeInput
from backend.services.artifacts.python import PythonToolService
from backend.services.artifacts.storage import ArtifactStorage
from backend.services.google.snapshots import conflict


class StopBeforeCode(RuntimeError):
    pass


class MemoryService(PythonToolService):
    saved = None
    receipt_status = "prepared"

    async def allowed(self, context, request):
        return [], {}, []

    async def load_receipt(self, *args):
        self.loaded = True
        return self.receipt_status, deepcopy(self.saved)

    async def save_receipt(self, context, key, *, status, result, primary=None):
        self.receipt_status, self.saved = status, deepcopy(result)

    async def run(self, *args, **kwargs):
        raise StopBeforeCode()


def context():
    return ToolContext(
        user_id=uuid4(),
        conversation_id=uuid4(),
        user_role="user",
        run_id=uuid4(),
        operation_key="python-snapshot-call",
    )


@pytest.mark.asyncio
async def test_input_capture_resumes_only_missing_sources(tmp_path):
    current = context()
    storage = ArtifactStorage(tmp_path)
    captures = []

    class Bridge:
        version = 1
        fail = True

        async def check(self, context, *, allow_conflict=False):
            pass

        async def capture(self, context, source):
            captures.append((source.ref.file_id, self.version))
            if source.ref.file_id == "B" and self.fail:
                raise asyncio.CancelledError()
            stored = storage.put(
                BytesIO(b'{"text":"snapshot"}'),
                f"{context.user_id}/{uuid4()}/{uuid4()}/content.json",
                max_bytes=1000,
            )
            return {"version": self.version, "stored": asdict(stored)}

    bridge = Bridge()
    service = MemoryService(google_bridge=bridge)
    request = PythonCodeInput(
        code="pass",
        google_inputs=[
            {
                "ref": {
                    "connection_id": uuid4(),
                    "context_id": current.conversation_id,
                    "file_id": name,
                }
            }
            for name in ("A", "B")
        ],
    )
    with pytest.raises(asyncio.CancelledError):
        await service.execute(current, request)
    bridge.fail, bridge.version = False, 2
    with pytest.raises(StopBeforeCode):
        await service.execute(current, request)
    assert [item["version"] for item in service.saved["google_snapshots"]] == [1, 2]
    assert captures == [("A", 1), ("B", 1), ("B", 2)]


@pytest.mark.asyncio
async def test_completed_partial_receipt_replays_despite_conflict_stop():
    class Bridge:
        async def check(self, context, *, allow_conflict=False):
            if not allow_conflict:
                raise conflict()

    result = ToolResult.failure(
        "Stopped after partial write",
        error_code="google_conflict",
        data={
            "google_results": [
                {"status": "completed", "url": "https://docs.google.com/document/d/A/edit"}
            ]
        },
    )
    service = MemoryService(google_bridge=Bridge())
    service.saved = asdict(result)
    service.receipt_status = "completed"
    current = context()
    request = PythonCodeInput(
        code="pass",
        google_inputs=[
            {
                "ref": {
                    "connection_id": uuid4(),
                    "context_id": current.conversation_id,
                    "file_id": "A",
                }
            }
        ],
    )
    replay = await service.execute(current, request)
    assert replay == result
    assert service.loaded
