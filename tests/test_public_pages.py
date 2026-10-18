from types import SimpleNamespace
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from backend.dependencies import get_optional_user
from backend.main import app


async def normal_user() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        email="reader@example.com",
        full_name="Markdown Reader",
        role="user",
        is_active=True,
    )


async def test_liveness_endpoint() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_login_page_is_available_without_database_connection() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/login")

    assert response.status_code == 200
    assert "Đăng nhập" in response.text
    assert "/api/auth/login" in response.text


async def test_admin_page_redirects_anonymous_user_to_login() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        follow_redirects=False,
    ) as client:
        response = await client.get("/admin")

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


async def test_memories_page_redirects_anonymous_user_to_login() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        follow_redirects=False,
    ) as client:
        response = await client.get("/memories")

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


async def test_chat_page_loads_markdown_renderer_before_chat_script() -> None:
    app.dependency_overrides[get_optional_user] = normal_user
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/chat")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    markdown_position = response.text.index("/static/markdown.js")
    chat_position = response.text.index("/static/chat.js")
    assert markdown_position < chat_position


async def test_invalid_email_error_is_returned_in_vietnamese() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/auth/login",
            json={"email": "user@test.local", "password": "Test@123456"},
        )

    assert response.status_code == 422
    assert response.json()["detail"] == ("Email không đúng định dạng. Ví dụ: ten@example.com.")


async def test_missing_fields_are_returned_in_vietnamese() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/auth/login", json={})

    assert response.status_code == 422
    assert response.json()["detail"] == "Email là bắt buộc. Mật khẩu là bắt buộc."
