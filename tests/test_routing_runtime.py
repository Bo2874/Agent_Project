import asyncio
import json
from types import SimpleNamespace

import httpx
import httpx2
import pytest
from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy

from agent_core.routing.jev import JevDecisionProvider
from agent_core.routing.llm import StructuredLLMDecisionProvider
from agent_core.routing.policy import (
    CircuitBreaker,
    DecisionPolicy,
    ManagedDecisionProvider,
    PrivacyPolicy,
)
from agent_core.routing.types import (
    DecisionOption,
    DecisionProviderError,
    DecisionRequest,
    DecisionResult,
    DecisionTelemetry,
)


def request(state="Calculate 25 times 17", *, safe=True):
    return DecisionRequest(
        state=state,
        instructions="Choose the next action",
        options=(
            DecisionOption(id="calculator", description="Calculate exact arithmetic"),
            DecisionOption(id="none" if safe else "other", description="No tool needed"),
        ),
    )


def answer(req, selected="calculator", probability=0.98, *, provider="jev", tokens=20):
    return DecisionResult(
        selected_id=selected,
        probabilities={
            o.id: probability if o.id == selected else 1 - probability for o in req.options
        },
        telemetry=DecisionTelemetry(
            provider=provider,
            model_version="model-20261014",
            input_tokens=tokens,
            output_tokens=2,
        ),
    )


class Provider:
    def __init__(self, *, error=None, probability=0.98, delay=0):
        self.error, self.probability, self.delay = error, probability, delay
        self.requests = []

    async def decide(self, req):
        self.requests.append(req)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return answer(req, probability=self.probability)


@pytest.mark.parametrize("react", [False, True])
async def test_missing_jev_key_is_optional_only_for_react_rollout(monkeypatch, react):
    from fastapi import HTTPException

    from agent_core.contracts import FeatureFlags
    from backend import dependencies_decisions

    monkeypatch.setattr(
        dependencies_decisions,
        "get_settings",
        lambda: SimpleNamespace(
            v2_features=FeatureFlags(enabled=True, routing=True),
            openrouter_api_key=None,
            agent_react_enabled=react,
        ),
    )
    provider = dependencies_decisions.get_decision_provider()
    try:
        if react:
            assert await anext(provider) is None
        else:
            with pytest.raises(HTTPException) as exc:
                await anext(provider)
            assert exc.value.status_code == 503
    finally:
        await provider.aclose()


async def managed(primary, fallback=None, **kwargs):
    traces = []

    async def observe(trace):
        traces.append(trace)

    provider = ManagedDecisionProvider(primary, fallback=fallback, observer=observe, **kwargs)
    result = await provider.decide(request())
    return result, provider, traces


async def test_success_records_measured_version_usage_without_fallback():
    fallback = Provider()
    result, _, traces = await managed(Provider(), fallback)
    assert result.selected_id == "calculator" and not fallback.requests
    attempt = traces[0]["attempts"][0]
    assert attempt["model_version"] == "model-20261014" and attempt["input_tokens"] == 20
    assert "state" not in json.dumps(traces) and "Calculate" not in json.dumps(traces)


@pytest.mark.parametrize(
    "error",
    [
        DecisionProviderError("provider_error"),
        DecisionProviderError("invalid_response"),
        RuntimeError("api_key=private"),
    ],
)
async def test_failure_falls_back_once_and_never_exposes_exception(error):
    fallback = Provider()
    result, _, traces = await managed(Provider(error=error), fallback)
    assert result.selected_id == "calculator" and len(fallback.requests) == 1
    assert traces[0]["fallback_reason"] in {"provider_error", "invalid_response"}
    assert "private" not in json.dumps(traces)
    assert len(traces[0]["attempts"]) == 2


@pytest.mark.parametrize("probability,reason", [(0.55, "low_confidence"), (0.65, "ambiguous")])
async def test_uncertain_choice_uses_fallback_with_provisional_thresholds(probability, reason):
    policy = DecisionPolicy(min_probability=0.6, min_margin=0.4)
    _, _, traces = await managed(Provider(probability=probability), Provider(), policy=policy)
    assert traces[0]["fallback_reason"] == reason


async def test_primary_timeout_leaves_time_for_fallback():
    result, _, traces = await managed(
        Provider(delay=10),
        Provider(),
        policy=DecisionPolicy(primary_timeout_seconds=0.01, timeout_seconds=0.2),
    )
    assert result.selected_id == "calculator" and traces[0]["fallback_reason"] == "timeout"


async def test_total_deadline_covers_both_providers_and_stops_tools():
    result, _, traces = await managed(
        Provider(error=DecisionProviderError("provider_error")),
        Provider(delay=10),
        policy=DecisionPolicy(timeout_seconds=0.01),
    )
    assert result.selected_id == "none" and result.telemetry.provider == "policy"
    assert traces[0]["fallback_reason"] == "timeout"


async def test_cancel_propagates_without_fallback():
    fallback = Provider()
    provider = ManagedDecisionProvider(Provider(delay=10), fallback=fallback)
    task = asyncio.create_task(provider.decide(request()))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not fallback.requests


async def test_both_providers_fail_returns_only_local_safe_choice():
    result, _, traces = await managed(Provider(error=ValueError()), Provider(error=ValueError()))
    assert result.selected_id == "none" and result.confidence is None
    assert traces[0]["status"] == "safe_stop"


async def test_no_safe_option_raises_instead_of_selecting_a_tool():
    provider = ManagedDecisionProvider(Provider(error=ValueError()))
    with pytest.raises(DecisionProviderError):
        await provider.decide(request(safe=False))


async def test_circuit_survives_request_objects_and_only_allows_one_half_open_probe():
    now = [0.0]
    breaker = CircuitBreaker(1, 10, clock=lambda: now[0])
    failing = Provider(error=ValueError())
    await managed(failing, Provider(), breaker=breaker)
    _, _, traces = await managed(failing, Provider(), breaker=breaker)
    assert len(failing.requests) == 1 and traces[0]["fallback_reason"] == "circuit_open"
    now[0] = 11
    probe = breaker.acquire()
    assert probe and breaker.acquire() is None
    breaker.complete(probe, success=True)
    assert breaker.snapshot()["state"] == "closed"


def test_stale_success_cannot_close_newly_opened_circuit():
    breaker = CircuitBreaker(1, 10)
    first, second = breaker.acquire(), breaker.acquire()
    breaker.complete(first, success=False)
    breaker.complete(second, success=True)
    assert breaker.snapshot()["state"] == "open"


@pytest.mark.parametrize(
    "privacy",
    [
        PrivacyPolicy(allow_external=False),
        PrivacyPolicy(max_state_chars=1),
        PrivacyPolicy(max_payload_bytes=1),
    ],
)
async def test_privacy_or_payload_budget_blocks_all_providers(privacy):
    primary, fallback = Provider(), Provider()
    _, _, traces = await managed(primary, fallback, privacy=privacy)
    assert not primary.requests and not fallback.requests
    assert traces[0]["status"] == "safe_stop"


def test_privacy_preserves_negation_numbers_and_drops_tool_body_memory_and_secrets():
    state = json.dumps(
        {
            "goal": "Không gọi tool. Mã 00123, số -42. Contact user@example.com token=privatevalue",
            "memory": "secret memory",
            "tool_body": "private tool response",
            "observations": [{"tool": "calculator", "is_error": True, "content": "PRIVATE"}],
        },
        ensure_ascii=False,
    )
    privacy = PrivacyPolicy(secrets=("privatevalue",), send_observations=False)
    prepared, _ = privacy.prepare(request(state))
    data = json.loads(prepared.state)
    assert "Không gọi tool. Mã 00123, số -42." in data["goal"]
    assert set(data) == {"goal", "trust"}
    assert "user@example.com" not in prepared.state and "privatevalue" not in prepared.state
    assert "privatevalue" not in repr(privacy)


def test_privacy_blocks_nested_payloads_inside_allowed_fields_and_scrubs_connection_urls():
    state = json.dumps(
        {
            "goal": "Read postgresql://user:password@private.test/db",
            "trust": {"private": "data"},
            "observations": [
                {
                    "tool": {"private": "tool-body"},
                    "error_code": ["secret"],
                    "is_error": {"raw": "data"},
                }
            ],
        }
    )
    prepared, _ = PrivacyPolicy().prepare(request(state))
    value = json.loads(prepared.state)
    assert "private.test" not in prepared.state and "tool-body" not in prepared.state
    assert value["observations"] == [{"tool": None, "error_code": None, "is_error": False}]


async def test_run_call_budget_includes_primary_and_fallback():
    primary, fallback = Provider(error=ValueError()), Provider()
    provider = ManagedDecisionProvider(
        primary, fallback=fallback, policy=DecisionPolicy(max_calls=1)
    )
    result = await provider.decide(request())
    assert result.selected_id == "none" and not fallback.requests and provider.calls == 1
    await provider.decide(request())
    assert len(primary.requests) == 1


async def test_estimated_token_reservations_stop_calls_before_network():
    result, provider, traces = await managed(
        Provider(), policy=DecisionPolicy(max_reserved_tokens=1)
    )
    assert result.selected_id == "none" and provider.calls == 0
    assert not traces[0]["attempts"]


async def test_measured_tokens_above_reservation_are_carried_into_budget():
    class Expensive:
        async def decide(self, req):
            return answer(req, tokens=10000)

    _, provider, traces = await managed(Expensive())
    assert provider.reserved_tokens == 10002
    assert traces[0]["attempts"][0]["reserved_tokens"] == 10002


async def test_real_sdk_http_contract_and_adapter_telemetry():
    calls = []

    def handle(req):
        calls.append(json.loads(req.content))
        assert str(req.url) == "https://openrouter.ai/api/v1/systemone"
        return httpx2.Response(
            200,
            json={
                "model": "typesafe/jev-1.14-20261014",
                "usage": {"input_tokens": 123, "output_tokens": 0, "cost": 0.001},
                "provider": "TypeSafe",
                "id": "synthetic-id",
                "answers": {
                    "decision": {
                        "type": "choice",
                        "choice": "calculator",
                        "probabilities": {"calculator": 0.98, "none": 0.02},
                        "confidence": 0.96,
                    }
                },
            },
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as http:
        async with AsyncTypeSafeClient(
            api_key="synthetic-key",
            model="typesafe/jev-1.14",
            base_url="https://openrouter.ai/api",
            http_client=http,
            retry=RetryPolicy(max_retries=0),
        ) as client:
            result = await JevDecisionProvider(client).decide(request())
    assert len(calls) == 1 and calls[0]["questions"]["decision"]["type"] == "choice"
    assert result.telemetry.input_tokens == 123 and result.telemetry.output_tokens == 0
    assert result.telemetry.model_version == "typesafe/jev-1.14-20261014"


async def test_missing_usage_stays_unknown():
    class Client:
        async def system_one(self, **kwargs):
            return SimpleNamespace(
                choices={
                    "decision": SimpleNamespace(
                        choice="none",
                        probabilities={"calculator": 0.0, "none": 1.0},
                        confidence=None,
                    )
                }
            )

    result = await JevDecisionProvider(Client()).decide(request())
    assert result.telemetry.input_tokens is None and result.telemetry.model_version is None


@pytest.mark.parametrize(
    "content,finish",
    [
        (json.dumps({"selected_id": "unknown"}), "stop"),
        ('{"selected_id":"calculator"}', "length"),
        ('{"selected_id":"none","arguments":{}}', "stop"),
        ("not JSON", "stop"),
    ],
)
async def test_llm_rejects_invalid_choice_fields_or_truncated_response(content, finish):
    def handle(req):
        body = json.loads(req.content)
        assert body["response_format"] == {"type": "json_object"} and "tools" not in body
        return httpx.Response(
            200, json={"choices": [{"finish_reason": finish, "message": {"content": content}}]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = StructuredLLMDecisionProvider(
            client, api_key="fake", model="fake", base_url="https://example.test"
        )
        with pytest.raises(DecisionProviderError):
            await provider.decide(request())


async def test_llm_accepts_exact_choice_and_records_actual_usage_model():
    def handle(req):
        return httpx.Response(
            200,
            json={
                "model": "served-model",
                "usage": {"prompt_tokens": 50, "completion_tokens": 8},
                "choices": [
                    {"finish_reason": "stop", "message": {"content": '{"selected_id":"none"}'}}
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = StructuredLLMDecisionProvider(
            client, api_key="fake", model="requested-model", base_url="https://example.test"
        )
        result = await provider.decide(request())
    assert result.confidence is None and result.selected_id == "none"
    assert result.telemetry.model_version == "served-model" and result.telemetry.input_tokens == 50
