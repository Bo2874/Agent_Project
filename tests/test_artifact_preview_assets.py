import hashlib
import io
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from test_artifact_api import api  # noqa: F401
from test_artifact_tools import bridge  # noqa: F401

from artifact_runtime.protocol import ArtifactOperation
from backend.dependencies import get_optional_user
from backend.main import app
from backend.models.artifact import ArtifactVersion
from backend.services.artifacts.validation import ValidatedArtifact, ValidatedCompanion


async def test_preview_assets_cannot_cross_owner_or_version(bridge, api):  # noqa: F811
    service, context, _ = bridge
    refs = []
    for index in range(2):
        result = await service.execute(
            replace(context, operation_key=f"preview-{index}"),
            ArtifactOperation(
                kind="create_docx",
                payload={"blocks": [{"kind": "paragraph", "text": f"Version {index}"}]},
            ),
        )
        assert not result.is_error, result
        refs.append(result.data["artifact_refs"][0])
    response = await api[0].get(f"/api/artifact-versions/{refs[0]['version_id']}/preview")
    preview = response.json()["preview"]
    assert preview["kind"] == "pages"
    assert preview["source_version_id"] == refs[0]["version_id"]
    assert preview["source_sha256"] == refs[0]["sha256"]
    asset = preview["assets"][0]
    url = f"/api/artifact-versions/{refs[0]['version_id']}/preview-assets/{asset['asset_id']}"
    image = await api[0].get(url)
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"
    assert hashlib.sha256(image.content).hexdigest() == asset["sha256"]
    assert (
        await api[0].get(url.replace(refs[0]["version_id"], refs[1]["version_id"]))
    ).status_code == 404
    original = app.dependency_overrides[get_optional_user]
    app.dependency_overrides[get_optional_user] = lambda: api[4]
    try:
        assert (await api[0].get(url)).status_code == 404
    finally:
        app.dependency_overrides[get_optional_user] = original


async def test_render_failure_does_not_publish_incomplete_bundle(bridge, monkeypatch):  # noqa: F811
    service, context, sessions = bridge
    original = service.run

    async def fail_page(*args, **kwargs):
        if kwargs.get("action") == "render_pages":
            raise ValueError("Synthetic Poppler failure")
        return await original(*args, **kwargs)

    monkeypatch.setattr(service, "run", fail_page)
    result = await service.execute(
        context,
        ArtifactOperation(
            kind="create_docx",
            payload={"blocks": [{"kind": "paragraph", "text": "Must not publish without preview"}]},
        ),
    )
    assert result.is_error
    async with sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ArtifactVersion)
                .where(ArtifactVersion.owner_user_id == context.user_id)
            )
            == 0
        )
    assert not list(service.storage_service().storage.root.rglob("content.*"))


async def test_preview_content_is_safe(api):  # noqa: F811
    _, sessions, service, owner, _, _, _ = api
    primary = service.storage.put(
        io.BytesIO(b"document"), f"{owner.id}/{uuid4()}/{uuid4()}/content.docx", max_bytes=100
    )
    malicious = service.storage.put(
        io.BytesIO(b"<svg onload='alert(1)'/>"),
        f"{owner.id}/{uuid4()}/{uuid4()}/content.png",
        max_bytes=100,
    )
    staged = ValidatedArtifact(
        primary, "report.docx", companions=(ValidatedCompanion("preview_page", malicious, page=1),)
    )
    async with sessions() as session:
        with pytest.raises(ValueError, match="PNG|preview"):
            await service.publish_version(
                session,
                owner.id,
                artifact_id=None,
                parent_version_id=None,
                staged=staged,
                operation_key="unsafe-preview",
            )
    assert not service.storage.path(malicious.key).exists()
