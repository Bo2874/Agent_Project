import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from dataclasses import asdict
from typing import Annotated

import httpx
from fastapi import Depends, HTTPException
from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy

from agent_core.routing.jev import JevDecisionProvider
from agent_core.routing.llm import StructuredLLMDecisionProvider
from agent_core.routing.policy import DecisionPolicy, PrivacyPolicy
from agent_core.routing.types import DecisionProvider
from backend.core.config import get_settings
from backend.services.routing import BackendDecisionProvider, shared_breaker


async def get_decision_provider() -> AsyncIterator[DecisionProvider | None]:
    settings = get_settings()

    if not settings.v2_features.is_enabled("routing"):
        yield None
        return

    key = settings.openrouter_api_key
    if key is None or not key.get_secret_value().strip():
        raise HTTPException(
            status_code=503,
            detail="Jev routing requires OPENROUTER_API_KEY",
        )

    async with AsyncExitStack() as stack:
        client = await stack.enter_async_context(
            AsyncTypeSafeClient(
                api_key=key.get_secret_value(),
                base_url=settings.jev_base_url,
                model=settings.jev_model,
                timeout=settings.jev_timeout_seconds,
                retry=RetryPolicy(max_retries=0),
            )
        )
        fallback = None
        if settings.routing_fallback_enabled and settings.deepseek_api_key:
            http = await stack.enter_async_context(
                httpx.AsyncClient(
                    timeout=settings.jev_total_timeout_seconds,
                    limits=httpx.Limits(max_connections=10),
                )
            )
            fallback = StructuredLLMDecisionProvider(
                http,
                api_key=settings.deepseek_api_key.get_secret_value(),
                model=settings.deepseek_model,
                base_url=settings.deepseek_base_url,
            )
        provider = BackendDecisionProvider(
            JevDecisionProvider(client),
            fallback=fallback,
            breaker=shared_breaker(
                settings.jev_base_url,
                settings.jev_model,
                settings.routing_breaker_threshold,
                settings.routing_breaker_cooldown_seconds,
            ),
            privacy=PrivacyPolicy(
                allow_external=settings.routing_allow_external,
                send_observations=settings.routing_send_observations,
                redact_pii=settings.routing_redact_pii,
                max_state_chars=settings.routing_max_state_chars,
                max_payload_bytes=settings.routing_max_payload_bytes,
                max_query_state_chars=settings.routing_query_max_state_chars,
                max_query_payload_bytes=settings.routing_query_max_payload_bytes,
                secrets=tuple(
                    s.get_secret_value()
                    for s in (key, settings.deepseek_api_key, settings.smtp_password)
                    if s
                ),
            ),
            policy=DecisionPolicy(
                timeout_seconds=settings.jev_total_timeout_seconds,
                primary_timeout_seconds=settings.routing_primary_timeout_seconds,
                query_timeout_seconds=settings.jev_timeout_seconds,
                max_calls=settings.routing_max_provider_calls,
                max_reserved_tokens=settings.routing_max_reserved_tokens,
                min_probability=settings.routing_min_probability,
                min_margin=settings.routing_min_margin,
                shadow=settings.routing_mode == "shadow",
            ),
        )
        privacy_signature = asdict(provider.privacy)
        privacy_signature.pop("secrets")
        provider.execution_signature = hashlib.sha256(
            json.dumps(
                {
                    "policy": asdict(provider.policy),
                    "privacy": privacy_signature,
                    "primary_model": settings.jev_model,
                    "primary_endpoint": settings.jev_base_url,
                    "fallback_model": settings.deepseek_model if fallback else None,
                    "fallback_endpoint": settings.deepseek_base_url if fallback else None,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        yield provider


DecisionProviderDependency = Annotated[
    DecisionProvider | None,
    Depends(get_decision_provider),
]
