from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from test_artifact_api import api  # noqa: F401

from agent_core.tools import ToolContext
from artifact_runtime.protocol import PythonCodeInput
from backend.models import AgentOperation, AgentRun
from backend.models.artifact import ArtifactVersion
from backend.routes import artifacts as routes


@pytest.fixture
async def python_bridge(api):  # noqa: F811
    from backend.services.artifacts.python import PythonToolService

    _, sessions, storage, owner, _, conversation, settings = api
    run = AgentRun(id=uuid4(), conversation_id=conversation.id, status="running")
    async with sessions() as session:
        session.add(run)
        await session.commit()
    service = PythonToolService(
        service=storage,
        sessions=sessions,
        runner_factory=lambda user: routes.runner_client(storage, user),
        publication_enabled=True,
    )
    context = ToolContext(
        user_id=owner.id,
        conversation_id=conversation.id,
        user_role="user",
        run_id=run.id,
        operation_key="python-call-1",
    )
    return service, context, sessions, storage


async def test_zero_output_execution_is_durably_replayed(python_bridge):
    service, context, sessions, _ = python_bridge
    args = PythonCodeInput(code="print(1 + 2)")
    first = await service.execute(context, args)
    assert not first.is_error, first
    assert first.data["execution"]["stdout"] == "3\n"
    second = await service.execute(context, args)
    assert second == first
    async with sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ArtifactVersion)
                .where(ArtifactVersion.source_run_id == context.run_id)
            )
            == 0
        )
        receipts = (
            await session.scalars(
                select(AgentOperation).where(
                    AgentOperation.agent_run_id == context.run_id,
                    AgentOperation.key.like("python.receipt.%"),
                )
            )
        ).all()
        assert len(receipts) == 1
        assert receipts[0].status == "completed"
    changed = await service.execute(context, PythonCodeInput(code="print(4)"))
    assert changed.is_error


async def test_multiple_outputs_primary_survives_readback_and_replay(python_bridge):
    from backend.services.artifacts.publication import run_artifacts

    service, context, sessions, _ = python_bridge
    args = PythonCodeInput(
        code=(
            "from pathlib import Path; Path('a.txt').write_text('A'); Path('b.txt').write_text('B')"
        ),
        outputs=[{"path": "a.txt"}, {"path": "b.txt"}],
        primary_output="a.txt",
    )
    first = await service.execute(context, args)
    assert not first.is_error, first
    assert len(first.data["outputs"]) == 2
    assert len(first.data["artifact_refs"]) == 1
    primary = first.data["artifact_refs"][0]
    assert primary["name"] == "a.txt"
    readback = await service.execute(
        replace(context, operation_key="python-call-2"),
        PythonCodeInput(
            code=(
                "import os; from pathlib import Path; "
                "print((Path(os.environ['AGENT_INPUT_DIR'])/'input-0.txt').read_text())"
            ),
            input_version_ids=[primary["version_id"]],
        ),
    )
    assert not readback.is_error, readback
    assert readback.data["execution"]["stdout"] == "A\n"
    async with sessions() as session:
        refs = await run_artifacts(
            session, context.user_id, context.conversation_id, context.run_id
        )
        assert [item.model_dump(mode="json") for item in refs] == [primary]
    assert (await service.execute(context, args)) == first


async def test_outputs_can_edit_uploaded_file_and_keep_original(python_bridge, api):  # noqa: F811
    from test_artifact_api import upload

    service, context, sessions, storage = python_bridge
    parent = await upload(api[0])
    version = UUID(parent["version_id"])
    args = PythonCodeInput(
        code=(
            "from pathlib import Path; import os; from openpyxl import load_workbook; "
            "w=load_workbook(Path(os.environ['AGENT_INPUT_DIR'])/'input-0.xlsx'); "
            "w.active['B1']='edited'; w.save('new.xlsx')"
        ),
        input_version_ids=[version],
        outputs=[{"path": "new.xlsx", "parent_version_id": version}],
    )
    denied = await service.execute(context, args)
    assert denied.is_error and denied.error_code == "artifact_not_in_scope"
    allowed = await service.execute(replace(context, artifact_version_ids=(version,)), args)
    assert not allowed.is_error, allowed
    assert allowed.data["artifact_refs"][0]["parent_version_id"] == str(version)
    response = await api[0].get(f"/api/artifact-versions/{version}/download")
    import hashlib

    assert hashlib.sha256(response.content).hexdigest() == parent["sha256"]


async def test_standalone_pdf_needs_no_editable_docx_companion(python_bridge):
    service, context, _, _ = python_bridge
    result = await service.execute(
        context,
        PythonCodeInput(
            code=(
                "from reportlab.pdfgen import canvas; c=canvas.Canvas('report.pdf'); "
                "c.drawString(50,750,'Report'); c.save()"
            ),
            outputs=[{"path": "report.pdf"}],
        ),
    )
    assert not result.is_error, result
    assert len(result.data["artifact_refs"]) == 1
    assert result.data["artifact_refs"][0]["mime_type"] == "application/pdf"


async def test_recovery_after_published_file_before_receipt_commit(python_bridge, monkeypatch):
    service, context, sessions, _ = python_bridge
    original = service.save_receipt
    interrupted = False

    async def save(*args, **kwargs):
        nonlocal interrupted
        if kwargs["status"] == "completed" and not interrupted:
            interrupted = True
            raise RuntimeError("simulated worker interruption")
        return await original(*args, **kwargs)

    monkeypatch.setattr(service, "save_receipt", save)
    arguments = PythonCodeInput(
        code="from pathlib import Path; Path('result.txt').write_text('once')",
        outputs=[{"path": "result.txt"}],
    )
    with pytest.raises(RuntimeError, match="interruption"):
        await service.execute(context, arguments)
    recovered = await service.execute(context, arguments)
    assert not recovered.is_error, recovered
    async with sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ArtifactVersion)
                .where(ArtifactVersion.source_run_id == context.run_id)
            )
            == 1
        )


async def test_png_and_text_preview_and_upload_roundtrip(python_bridge, api):  # noqa: F811
    service, context, _, _ = python_bridge
    result = await service.execute(
        context,
        PythonCodeInput(
            code="from PIL import Image; Image.new('RGB',(80,60),'red').save('plot.png')",
            outputs=[{"path": "plot.png"}],
        ),
    )
    assert not result.is_error, result
    ref = result.data["artifact_refs"][0]
    preview = await api[0].get(f"/api/artifact-versions/{ref['version_id']}/preview")
    assert preview.json()["preview"]["kind"] == "image"
    image = await api[0].get(f"/api/artifact-versions/{ref['version_id']}/content")
    assert image.status_code == 200 and image.content.startswith(b"\x89PNG")
    upload = await api[0].post(
        "/api/artifacts", files={"file": ("data.csv", b"a,b\n1,2", "text/csv")}
    )
    assert upload.status_code == 201, upload.text
    text = await api[0].get(f"/api/artifact-versions/{upload.json()['version_id']}/preview")
    assert text.json()["preview"]["kind"] == "text"


async def test_every_pdf_input_is_preflighted_and_text_only_intent_is_pinned(python_bridge):
    from backend.models import Message

    service, context, sessions, storage = python_bridge
    # A generated blank second page is unreadable: the PDF need not be an output parent.
    source = await service.execute(context, PythonCodeInput(
        code=("from reportlab.pdfgen import canvas; c=canvas.Canvas('partial.pdf'); "
              "c.drawString(50,750,'Text'); c.showPage(); c.showPage(); c.save()"),
        outputs=[{"path": "partial.pdf"}]))
    assert not source.is_error, source
    pdf = source.data["artifact_refs"][0]
    child_context = replace(context, operation_key="partial-read")
    arguments = dict(code="from pathlib import Path; Path('result.txt').write_text('Text')",
        input_version_ids=[pdf["version_id"]], outputs=[{"path": "result.txt"}])
    denied = await service.execute(child_context, PythonCodeInput(**arguments))
    assert denied.is_error and "text-only" in denied.content
    # The model cannot self-attest consent. Require the exact user clarification.
    pinned = [{"version_id": pdf["version_id"], "sha256": pdf["sha256"], "omitted_pages": [2]}]
    no_user = await service.execute(child_context,
        PythonCodeInput(**arguments, text_only_pdf_inputs=pinned))
    assert no_user.is_error
    async with sessions() as session:
        message = Message(conversation_id=context.conversation_id, role="user",
            content="Đồng ý chỉ xử lý văn bản")
        session.add(message)
        await session.flush()
        run = await session.get(AgentRun, context.run_id)
        run.user_message_id = message.id
        await session.commit()
    # A different call gets a new immutable fingerprint and the pinned consent.
    accepted = await service.execute(replace(context, operation_key="partial-confirmed"),
        PythonCodeInput(**arguments, text_only_pdf_inputs=pinned))
    assert not accepted.is_error, accepted
    assert accepted.data["pdf_inputs"][0]["sha256"] == pdf["sha256"]
    assert accepted.data["pdf_inputs"][0]["omitted_pages"] == [2]
