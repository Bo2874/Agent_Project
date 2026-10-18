from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from backend.core.logging import install_log_redaction
from backend.core.validation import translate_validation_error
from backend.database import SessionFactory, engine
from backend.dependencies_tools import close_tool_connections, start_tool_connections
from backend.routes import admin, auth, chat, memory, pages
from backend.services.langgraph_checkpoints import (
    close_langgraph_checkpoints,
    start_langgraph_checkpoints,
)
from backend.services.langsmith_tracing import (
    close_langsmith_tracing,
    start_langsmith_tracing,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    install_log_redaction()
    start_langsmith_tracing()
    await start_langgraph_checkpoints()
    try:
        await start_tool_connections()
        try:
            yield
        finally:
            await close_tool_connections()
    finally:
        await close_langgraph_checkpoints()
        await close_langsmith_tracing()
        await engine.dispose()


app = FastAPI(title="Personal AI Agent", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=PROJECT_ROOT / "frontend" / "static"), name="static")
app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(memory.router)
app.include_router(admin.router)
app.include_router(pages.router)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    _: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    messages = [translate_validation_error(error) for error in exc.errors()]
    return JSONResponse(
        status_code=422,
        content={
            "detail": " ".join(dict.fromkeys(messages)),
            "errors": messages,
        },
    )


@app.get("/health/live", tags=["health"])
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
async def readiness() -> dict[str, str]:
    async with SessionFactory() as session:
        await session.execute(text("SELECT 1"))
    return {"status": "ready"}
