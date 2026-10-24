from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from backend.core.config import get_settings
from backend.dependencies import get_optional_user
from backend.models import User

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
templates = Jinja2Templates(directory=FRONTEND_DIR / "templates")
router = APIRouter(include_in_schema=False)
OptionalUser = Annotated[User | None, Depends(get_optional_user)]


@router.get("/", response_class=HTMLResponse)
async def home(user: OptionalUser) -> RedirectResponse:
    return RedirectResponse("/dashboard" if user else "/login", status_code=303)


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, user: OptionalUser) -> Response:
    if user:
        return RedirectResponse("/dashboard", status_code=303)
    return templates.TemplateResponse(request=request, name="login.html")


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request, user: OptionalUser) -> Response:
    if user:
        return RedirectResponse("/dashboard", status_code=303)
    return templates.TemplateResponse(request=request, name="register.html")


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, user: OptionalUser) -> Response:
    if user is None:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={"user": user},
    )


@router.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request, user: OptionalUser) -> Response:
    if user is None:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="chat.html",
        context={"user": user, "model_name": get_settings().deepseek_model},
    )


@router.get("/memories", response_class=HTMLResponse)
async def memories_page(request: Request, user: OptionalUser) -> Response:
    if user is None:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="memories.html",
        context={"user": user},
    )


@router.get("/documents", response_class=HTMLResponse)
async def documents_page(request: Request, user: OptionalUser) -> Response:
    if user is None:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="documents.html",
        context={"user": user},
        headers={"Cache-Control": "no-store"},
    )


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request, user: OptionalUser) -> Response:
    if user is None:
        return RedirectResponse("/login", status_code=303)
    if user.role != "admin":
        return templates.TemplateResponse(
            request=request,
            name="forbidden.html",
            context={"user": user},
            status_code=403,
        )
    return templates.TemplateResponse(request=request, name="admin.html", context={"user": user})


@router.get("/plugins", response_class=HTMLResponse)
@router.get("/admin/plugins", response_class=HTMLResponse)
@router.get("/plugins/oauth/callback", response_class=HTMLResponse)
async def plugins_page(request: Request, user: OptionalUser) -> Response:
    headers = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
    if user is None:
        return RedirectResponse("/login", status_code=303, headers=headers)
    admin_view = request.url.path == "/admin/plugins"
    if admin_view and user.role != "admin":
        return templates.TemplateResponse(
            request=request,
            name="forbidden.html",
            context={"user": user},
            status_code=403,
            headers=headers,
        )
    return templates.TemplateResponse(
        request=request,
        name="plugins.html",
        context={
            "user": user,
            "plugins_enabled": get_settings().v2_features.is_enabled("plugins"),
            "plugins_admin_view": admin_view,
        },
        headers=headers,
    )
