from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import re
from collections.abc import Callable, Mapping
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.tracers.langchain import LangChainTracer
from langsmith import Client

from agent_core.prompts import SYSTEM_PROMPT_VERSION
from agent_core.tracing import AgentTraceConfig
from backend.core.config import get_settings
from backend.core.redaction import REDACTED, redact_data

logger = logging.getLogger(__name__)

_IDENTIFIER_KEY = re.compile(
    r"(?:^|_)(?:user|run|thread|conversation|message|approval|tool_call)_?id$",
    re.IGNORECASE,
)
_UUID_VALUE = re.compile(
    r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b"
)


def anonymous_trace_id(value: UUID | str, *, salt: str, namespace: str) -> str:
    digest = hmac.new(
        salt.encode("utf-8"),
        f"{namespace}:{value}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return digest[:20]


def redact_langsmith_payload(value: Any) -> Any:
    """Redact secrets, PII and application identifiers before trace export."""

    if isinstance(value, Mapping):
        base = redact_data(value)
        if not isinstance(base, Mapping):  # pragma: no cover - defensive contract
            return REDACTED
        return {
            str(key): (
                REDACTED if _IDENTIFIER_KEY.search(str(key)) else redact_langsmith_payload(item)
            )
            for key, item in base.items()
        }
    if isinstance(value, list | tuple):
        return [redact_langsmith_payload(item) for item in value]
    redacted = redact_data(value)
    return _UUID_VALUE.sub("[REDACTED_ID]", redacted) if isinstance(redacted, str) else redacted


def _redact_mapping(value: dict[str, Any]) -> dict[str, Any]:
    redacted = redact_langsmith_payload(value)
    return redacted if isinstance(redacted, dict) else {}


def _trace_export_error(error: Exception) -> None:
    logger.warning("LangSmith trace export failed: %s", type(error).__name__)


class LangSmithTracingService:
    def __init__(
        self,
        *,
        enabled: bool,
        api_key: str,
        endpoint: str,
        project: str,
        sampling_rate: float,
        hash_salt: str,
        app_env: str,
        client_factory: Callable[..., Any] = Client,
        tracer_factory: Callable[..., BaseCallbackHandler] = LangChainTracer,
    ) -> None:
        self._hash_salt = hash_salt
        self._app_env = app_env
        self._client: Any | None = None
        self._tracer: BaseCallbackHandler | None = None
        if not enabled or not api_key.strip():
            return
        self._client = client_factory(
            api_url=endpoint,
            api_key=api_key,
            anonymizer=_redact_mapping,
            hide_inputs=True,
            hide_outputs=True,
            hide_metadata=_redact_mapping,
            omit_traced_runtime_info=True,
            tracing_sampling_rate=sampling_rate,
            tracing_error_callback=_trace_export_error,
        )
        self._tracer = tracer_factory(
            project_name=project,
            client=self._client,
        )

    @property
    def enabled(self) -> bool:
        return self._tracer is not None

    @property
    def client(self) -> Any | None:
        """Share the privacy-configured client with LangSmith eval export."""

        return self._client

    def trace_config(
        self,
        *,
        run_id: UUID,
        user_id: UUID,
        provider: str,
        model: str,
    ) -> AgentTraceConfig | None:
        if self._tracer is None:
            return None
        anonymous_run = anonymous_trace_id(
            run_id,
            salt=self._hash_salt,
            namespace="agent-run",
        )
        anonymous_user = anonymous_trace_id(
            user_id,
            salt=self._hash_salt,
            namespace="user",
        )
        return AgentTraceConfig(
            callbacks=(self._tracer,),
            tags=(
                "personal-agent",
                f"env:{self._app_env}",
                f"run:{anonymous_run}",
                f"user:{anonymous_user}",
            ),
            metadata={
                "provider": provider,
                "model": model,
                "prompt_version": SYSTEM_PROMPT_VERSION,
                "payload_policy": "inputs_outputs_hidden",
            },
        )

    async def close(self) -> None:
        tracer = self._tracer
        client = self._client
        self._tracer = None
        self._client = None
        if tracer is not None and hasattr(tracer, "wait_for_futures"):
            await asyncio.to_thread(tracer.wait_for_futures)
        if client is not None:
            if hasattr(client, "flush"):
                await asyncio.to_thread(client.flush)
            if hasattr(client, "close"):
                await asyncio.to_thread(client.close)


_tracing_service: LangSmithTracingService | None = None


def start_langsmith_tracing() -> None:
    global _tracing_service
    if _tracing_service is not None:
        return
    settings = get_settings()
    api_key = (
        settings.langsmith_api_key.get_secret_value()
        if settings.langsmith_api_key is not None
        else ""
    )
    _tracing_service = LangSmithTracingService(
        enabled=settings.langsmith_tracing_enabled,
        api_key=api_key,
        endpoint=settings.langsmith_endpoint,
        project=settings.langsmith_project,
        sampling_rate=settings.langsmith_sampling_rate,
        hash_salt=settings.auth_secret_key.get_secret_value(),
        app_env=settings.app_env,
    )


def get_langsmith_tracing() -> LangSmithTracingService:
    if _tracing_service is None:
        raise RuntimeError("LangSmith tracing service chưa được khởi động")
    return _tracing_service


async def close_langsmith_tracing() -> None:
    global _tracing_service
    service = _tracing_service
    _tracing_service = None
    if service is not None:
        await service.close()
