import hashlib
import io
import os
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from openpyxl import Workbook
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from artifact_runtime.app import create_app
from artifact_runtime.containers import DockerBroker
from backend.core.config import Settings
from backend.dependencies import get_db_session, get_optional_user
from backend.main import app
from backend.models import Conversation, Document, DocumentIngestionJob, DocumentVersion, User
from backend.routes import artifacts as routes
from backend.services.artifacts.runner_client import ArtifactRunnerClient
from backend.services.artifacts.service import ArtifactService
from backend.services.artifacts.storage import ArtifactStorage
from backend.services.artifacts.validation import ValidatedArtifact


@pytest.fixture
async def api(tmp_path, monkeypatch):
    url = os.environ.get("ARTIFACT_TEST_DATABASE_URL")
    if not url or os.environ.get("ARTIFACT_RUNNER_DOCKER") != "1":
        pytest.skip("Requires isolated migrated PostgreSQL and artifact code image")
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    owner, other = (
        User(id=uuid4(), email=f"{uuid4()}@example.com", password_hash="test"),
        User(id=uuid4(), email=f"{uuid4()}@example.com", password_hash="test"),
    )
    conversation = Conversation(id=uuid4(), user_id=owner.id)
    async with sessions() as session:
        session.add_all([owner, other])
        await session.flush()
        session.add(conversation)
        await session.commit()
    settings = Settings(
        _env_file=None,
        db_password="test-only",
        auth_secret_key="k" * 32,
        workspace_artifacts_enabled=True,
        artifact_storage_path=tmp_path / "private",
        document_storage_path=tmp_path / "documents",
        artifact_runner_key="k" * 32,
    )
    if os.name == "nt":
        settings.document_storage_path = Path(
            "\\\\?\\" + str(settings.document_storage_path.absolute())
        )
    service = ArtifactService(ArtifactStorage(settings.artifact_storage_path))
    runner = create_app(
        DockerBroker(tmp_path / "broker", docker=os.environ.get("ARTIFACT_DOCKER", "docker")),
        key="k" * 32,
    )
    monkeypatch.setattr(routes, "get_settings", lambda: settings)
    monkeypatch.setattr(
        routes,
        "runner_client",
        lambda current, current_owner: ArtifactRunnerClient(
            "http://runner",
            key="k" * 32,
            storage=current.storage,
            owner=current_owner,
            transport=ASGITransport(app=runner),
        ),
    )
    from backend.services import documents

    monkeypatch.setattr(documents, "get_settings", lambda: settings)

    async def user():
        return owner

    async def database():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_optional_user] = user
    app.dependency_overrides[get_db_session] = database
    app.dependency_overrides[routes.get_artifact_service] = lambda: service
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, sessions, service, owner, other, conversation, settings
    app.dependency_overrides.clear()
    async with sessions() as session:
        for user_id in (owner.id, other.id):
            await session.delete(await session.get(User, user_id))
        await session.commit()
    await engine.dispose()


def workbook_bytes():
    stream = io.BytesIO()
    workbook = Workbook()
    workbook.active["A1"] = "uploaded"
    workbook.save(stream)
    return stream.getvalue()


async def upload(client):
    response = await client.post(
        "/api/artifacts",
        files={
            "file": (
                "budget.xlsx",
                workbook_bytes(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_upload_xlsx_without_rag_index(api):
    client, sessions, _, owner, _, conversation, _ = api
    artifact = await upload(client)
    response = await client.put(
        f"/api/conversations/{conversation.id}/artifacts",
        json={"version_ids": [artifact["version_id"]]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["version_id"] == artifact["version_id"]
    async with sessions() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(DocumentIngestionJob)
            .where(DocumentIngestionJob.owner_user_id == owner.id)
        )
    assert count == 0
    download = await client.get(f"/api/artifact-versions/{artifact['version_id']}/download")
    assert hashlib.sha256(download.content).hexdigest() == artifact["sha256"]
    assert load_content(download.content) == "uploaded"
    assert download.headers["cache-control"] == "no-store"


def load_content(data):
    from openpyxl import load_workbook

    return load_workbook(io.BytesIO(data)).active["A1"].value


async def test_guess_version_and_import_foreign_document(api):
    client, sessions, _, _, other, _, _ = api
    foreign_id = uuid4()
    document = Document(id=uuid4(), owner_user_id=other.id, title="private")
    async with sessions() as session:
        session.add(document)
        await session.flush()
        session.add(
            DocumentVersion(
                id=foreign_id,
                document_id=document.id,
                owner_user_id=other.id,
                version_number=1,
                original_filename="private.txt",
                file_type="txt",
                mime_type="text/plain",
                file_size_bytes=4,
                file_sha256="0" * 64,
                storage_key=f"{other.id}/{document.id}/{foreign_id}/original.txt",
            )
        )
        await session.commit()
    response = await client.get(f"/api/artifact-versions/{uuid4()}/download")
    assert response.status_code == 404
    response = await client.post(
        "/api/artifacts/import-document", json={"document_version_id": str(foreign_id)}
    )
    assert response.status_code == 404


async def test_selected_version_is_pinned(api):
    client, sessions, service, owner, _, conversation, _ = api
    original = await upload(client)
    await client.put(
        f"/api/conversations/{conversation.id}/artifacts",
        json={"version_ids": [original["version_id"]]},
    )
    from uuid import UUID

    stored = service.storage.put(
        io.BytesIO(workbook_bytes()),
        f"{owner.id}/{uuid4()}/{uuid4()}/content.xlsx",
        max_bytes=20971520,
    )
    async with sessions() as session:
        child = await service.publish_version(
            session,
            owner.id,
            artifact_id=UUID(original["artifact_id"]),
            parent_version_id=UUID(original["version_id"]),
            staged=ValidatedArtifact(stored, "budget.xlsx"),
            operation_key=str(uuid4()),
        )
    selected = await client.get(f"/api/conversations/{conversation.id}/artifacts")
    assert selected.json()["items"][0]["version_id"] == original["version_id"]
    assert str(child.version_id) != original["version_id"]


async def test_import_copy_survives_source_deletion(api):
    client, sessions, _, owner, _, _, settings = api

    document = Document(id=uuid4(), owner_user_id=owner.id, title="Source")
    version = uuid4()
    key = f"{owner.id}/{document.id}/{version}/original.txt"
    path = settings.document_storage_path.joinpath(*key.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"original text")
    async with sessions() as session:
        session.add(document)
        await session.flush()
        session.add(
            DocumentVersion(
                id=version,
                document_id=document.id,
                owner_user_id=owner.id,
                version_number=1,
                original_filename="source.txt",
                file_type="txt",
                mime_type="text/plain",
                file_size_bytes=13,
                file_sha256=hashlib.sha256(b"original text").hexdigest(),
                storage_key=key,
            )
        )
        await session.commit()
    response = await client.post(
        "/api/artifacts/import-document", json={"document_version_id": str(version)}
    )
    assert response.status_code == 201, response.text
    imported = response.json()
    async with sessions() as session:
        await session.delete(await session.get(Document, document.id))
        await session.commit()
    path.unlink()
    download = await client.get(f"/api/artifact-versions/{imported['version_id']}/download")
    assert download.content == b"original text"


async def test_unknown_length_upload_stops_at_limit():
    application = FastAPI()
    application.add_middleware(routes.ArtifactUploadLimitMiddleware, max_body_bytes=1024)
    application.include_router(routes.router)
    sent = 0

    async def content():
        nonlocal sent
        yield (
            b'--test\r\nContent-Disposition: form-data; name="file"; filename="huge.xlsx"\r\n'
            b"Content-Type: application/octet-stream\r\n\r\n"
        )
        for _ in range(1000):
            sent += 512
            yield b"a" * 512
        yield b"\r\n--test--\r\n"

    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/artifacts",
            content=content(),
            headers={"Content-Type": "multipart/form-data; boundary=test"},
        )
    assert response.status_code == 413, response.text
    assert sent <= 1536


async def test_foreign_version_is_invisible_and_selection_is_atomic(api):
    client, sessions, service, owner, other, conversation, _ = api
    original = await upload(client)
    await client.put(
        f"/api/conversations/{conversation.id}/artifacts",
        json={"version_ids": [original["version_id"]]},
    )
    stored = service.storage.put(
        io.BytesIO(workbook_bytes()),
        f"{other.id}/{uuid4()}/{uuid4()}/content.xlsx",
        max_bytes=20971520,
    )
    async with sessions() as session:
        foreign = await service.publish_version(
            session,
            other.id,
            artifact_id=None,
            parent_version_id=None,
            staged=ValidatedArtifact(stored, "private.xlsx"),
            operation_key=str(uuid4()),
        )
    assert (
        await client.get(f"/api/artifact-versions/{foreign.version_id}/download")
    ).status_code == 404
    response = await client.put(
        f"/api/conversations/{conversation.id}/artifacts",
        json={"version_ids": [original["version_id"], str(foreign.version_id)]},
    )
    assert response.status_code == 404
    assert (await client.get(f"/api/conversations/{conversation.id}/artifacts")).json()["items"][0][
        "version_id"
    ] == original["version_id"]


async def test_cross_origin_mutation_is_rejected(api):
    client, *_ = api
    response = await client.post(
        "/api/artifacts/import-document",
        json={"document_version_id": str(uuid4())},
        headers={"Origin": "https://other.example"},
    )
    assert response.status_code == 403


async def test_unknown_length_json_body_stops_at_limit():
    application = FastAPI()
    application.add_middleware(routes.ArtifactUploadLimitMiddleware, max_body_bytes=20971520)
    application.include_router(routes.router)
    sent = 0

    async def content():
        nonlocal sent
        yield ('{"document_version_id":"' + str(uuid4()) + '"}').encode()
        for _ in range(100):
            sent += 16384
            yield b" " * 16384

    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/artifacts/import-document",
            content=content(),
            headers={"Content-Type": "application/json"},
        )
    assert response.status_code == 413
    assert sent <= 65536
