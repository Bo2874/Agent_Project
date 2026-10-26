"""Bounded provider orchestration. No authorization, tool execution or raw-data logging."""

import asyncio
import json
import math
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from time import monotonic, perf_counter

from agent_core.routing.types import (
    DecisionProvider,
    DecisionProviderError,
    DecisionRequest,
    DecisionResult,
    DecisionTelemetry,
)

POLICY_VERSION = "decision_policy1.2.1"
_EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_KEY = re.compile(r"(?i)\b(?:sk-or-v1-|sk-)[A-Za-z0-9_-]{8,}")
_CONNECTION = re.compile(r"(?i)\b(?:postgresql|postgres|redis|mysql|mongodb)://[^\s]+")
_SECRET = re.compile(
    r"(?i)(api[_-]?key|password|secret|access[_-]?token|refresh[_-]?token)"
    r"(\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;}]+)"
)


@dataclass(frozen=True)
class PrivacyPolicy:
    allow_external: bool = True
    send_observations: bool = True
    redact_pii: bool = True
    max_state_chars: int = 32768
    max_payload_bytes: int = 131072
    max_query_state_chars: int = 32768
    max_query_payload_bytes: int = 131072
    # Credential values supplied by the host are removed even outside key/value strings.
    secrets: tuple[str, ...] = field(default=(), repr=False)

    def prepare(self, request: DecisionRequest) -> tuple[DecisionRequest, int]:
        query = request.phase == "query"
        max_state_chars = self.max_query_state_chars if query else self.max_state_chars
        max_payload_bytes = self.max_query_payload_bytes if query else self.max_payload_bytes
        if not self.allow_external or len(request.state) > max_state_chars:
            raise DecisionProviderError("privacy_blocked")

        def clean(value: str) -> str:
            for secret in self.secrets:
                if secret:
                    value = value.replace(secret, "[REDACTED]")
            value = _KEY.sub("[REDACTED]", _BEARER.sub("[REDACTED]", value))
            value = _SECRET.sub(r"\1\2[REDACTED]", value)
            value = _CONNECTION.sub("[REDACTED_CONNECTION]", value)
            return _EMAIL.sub("[REDACTED_EMAIL]", value) if self.redact_pii else value

        try:
            state = json.loads(request.state)
        except (ValueError, TypeError):
            state = None
        if isinstance(state, dict):
            # Both decisions may receive conversation, with roles and content only.
            history = (
                [
                    {"role": item["role"], "content": item["content"]}
                    for item in state["history"]
                    if isinstance(item, dict)
                    and item.get("role") in {"user", "assistant"}
                    and isinstance(item.get("content"), str)
                ]
                if isinstance(state.get("history"), list)
                else []
            )
            if request.phase == "query":
                documents = state.get("selected_documents")
                documents = documents if isinstance(documents, list) else []
                # Only bounded display metadata may cross the provider boundary.
                # Scrub before truncation so a cut credential cannot escape redaction.
                selected_documents = [
                    {"title": clean(item["title"].strip())[:200], "status": item["status"]}
                    for item in documents[:20]
                    if isinstance(item, dict)
                    and isinstance(item.get("title"), str)
                    and item["title"].strip()
                    and isinstance(item.get("status"), str)
                    and 0 < len(item["status"]) <= 20
                ]
                count = state.get("selected_document_count")
                filtered = {
                    "query": state.get("query") if isinstance(state.get("query"), str) else "",
                    "history": history,
                    "selected_documents": selected_documents,
                    "selected_document_count": count
                    if type(count) is int and count >= len(selected_documents)
                    else len(selected_documents),
                    "trust": "untrusted_data",
                }
            else:
                filtered = {
                    "goal": state.get("goal") if isinstance(state.get("goal"), str) else "",
                    "trust": "untrusted_data",
                }
                if isinstance(state.get("history"), list):
                    filtered["history"] = history
            if request.phase == "capability" and type(state.get("page")) is int:
                filtered["page"] = state["page"]
            if request.phase == "capability" and type(state.get("has_more")) is bool:
                filtered["has_more"] = state["has_more"]
            if (
                request.phase == "capability"
                and self.send_observations
                and isinstance(state.get("observations"), list)
            ):
                filtered["observations"] = [
                    {
                        "tool": item.get("tool")
                        if isinstance(item.get("tool"), str) and len(item["tool"]) <= 128
                        else None,
                        "is_error": item.get("is_error") is True,
                        "error_code": item.get("error_code")
                        if isinstance(item.get("error_code"), str) and len(item["error_code"]) <= 80
                        else None,
                    }
                    for item in state["observations"][-4:]
                    if isinstance(item, dict)
                ]

            # Scrub each string, not serialized JSON, to preserve valid JSON and negation/numbers.
            def scrub(value):
                if isinstance(value, str):
                    return clean(value)
                if isinstance(value, list):
                    return [scrub(item) for item in value]
                if isinstance(value, dict):
                    return {k: scrub(v) for k, v in value.items()}
                return value

            text = json.dumps(scrub(filtered), ensure_ascii=False)
        else:
            text = clean(request.state)
        prepared = DecisionRequest(
            phase=request.phase,
            state=text,
            instructions=clean(request.instructions),
            options=tuple(
                o.model_copy(update={"description": clean(o.description)}) for o in request.options
            ),
        )
        size = len(json.dumps(prepared.model_dump(), ensure_ascii=False).encode("utf-8"))
        if size > max_payload_bytes:
            raise DecisionProviderError("state_budget")
        # Conservative reservation, distinct from measured usage returned by providers.
        return prepared, math.ceil(size / 3) + 256


class CircuitBreaker:
    """Process-local, shared across requests; only one half-open probe is admitted."""

    def __init__(self, threshold=3, cooldown=30.0, *, clock=monotonic):
        self.threshold, self.cooldown, self.clock = threshold, cooldown, clock
        self.failures = 0
        self.open_until = 0.0
        self.probing = False
        self.generation = 0

    def acquire(self):
        if self.open_until:
            if self.clock() < self.open_until or self.probing:
                return None
            self.probing = True
        return self.generation, self.probing

    def complete(self, ticket, *, success):
        generation, probe = ticket
        if generation != self.generation:
            return
        if success:
            self.failures = 0
            if probe:
                self.open_until, self.probing = 0.0, False
                self.generation += 1
        else:
            self.failures += 1
            if probe or self.failures >= self.threshold:
                self.open_until = self.clock() + self.cooldown
                self.probing = False
                self.generation += 1

    def cancel(self, ticket):
        if ticket[0] == self.generation and ticket[1]:
            self.probing = False
            self.open_until = self.clock() + self.cooldown
            self.generation += 1

    def snapshot(self):
        return {
            "state": "half_open" if self.probing else "open" if self.open_until else "closed",
            "failures": self.failures,
            "retry_after_seconds": max(0, round(self.open_until - self.clock(), 1)),
        }


@dataclass(frozen=True)
class DecisionPolicy:
    timeout_seconds: float = 20
    primary_timeout_seconds: float = 8
    query_timeout_seconds: float = 15
    max_calls: int = 12
    max_reserved_tokens: int = 20000
    min_probability: float = 0.6
    min_margin: float = 0.1
    shadow: bool = False


class ManagedDecisionProvider:
    def __init__(
        self,
        primary: DecisionProvider,
        *,
        fallback: DecisionProvider | None = None,
        breaker: CircuitBreaker | None = None,
        privacy: PrivacyPolicy | None = None,
        policy: DecisionPolicy | None = None,
        observer: Callable[[dict], Awaitable[None]] | None = None,
    ):
        self.primary, self.fallback = primary, fallback
        self.breaker, self.privacy = breaker or CircuitBreaker(), privacy or PrivacyPolicy()
        self.policy, self.observer = policy or DecisionPolicy(), observer
        self.shadow = self.policy.shadow
        self.calls = self.reserved_tokens = 0

    async def _call(self, provider, request, estimate, attempts, name):
        if (
            self.calls >= self.policy.max_calls
            or self.reserved_tokens + estimate > self.policy.max_reserved_tokens
        ):
            raise DecisionProviderError("state_budget")
        self.calls += 1
        self.reserved_tokens += estimate
        attempt = {"provider": name, "reserved_tokens": estimate, "status": "failed"}
        attempts.append(attempt)
        started = perf_counter()
        try:
            result = DecisionResult.model_validate(await provider.decide(request))
            if set(result.probabilities) != {o.id for o in request.options}:
                raise DecisionProviderError("invalid_response")
            if result.telemetry:
                version = result.telemetry.model_version
                if version and any(secret and secret in version for secret in self.privacy.secrets):
                    result = result.model_copy(
                        update={
                            "telemetry": result.telemetry.model_copy(update={"model_version": None})
                        }
                    )
                attempt.update(result.telemetry.model_dump(exclude={"latency_ms"}))
                measured = (result.telemetry.input_tokens or 0) + (
                    result.telemetry.output_tokens or 0
                )
                if measured > estimate:
                    self.reserved_tokens += measured - estimate
                    attempt["reserved_tokens"] = measured
            attempt["status"] = "success"
            return result
        except asyncio.CancelledError:
            attempt["status"] = "cancelled"
            raise
        except DecisionProviderError as exc:
            attempt["reason"] = exc.code
            raise
        except Exception:
            attempt["reason"] = "provider_error"
            raise DecisionProviderError("provider_error") from None
        finally:
            attempt["latency_ms"] = round((perf_counter() - started) * 1000, 2)

    async def decide(self, request: DecisionRequest) -> DecisionResult:
        request = DecisionRequest.model_validate(request)
        query_only = request.phase == "query"
        attempts, reason, result = [], None, None
        started = perf_counter()
        cancelled = False
        try:
            prepared, estimate = self.privacy.prepare(request)
            async with asyncio.timeout(self.policy.timeout_seconds):
                ticket = self.breaker.acquire()
                if ticket is None:
                    reason = "circuit_open"
                else:
                    try:
                        primary_timeout = (
                            self.policy.query_timeout_seconds
                            if query_only
                            else self.policy.primary_timeout_seconds
                        )
                        async with asyncio.timeout(primary_timeout):
                            result = await self._call(
                                self.primary, prepared, estimate, attempts, "jev"
                            )
                        self.breaker.complete(ticket, success=True)
                        if not query_only:
                            ranked = sorted(result.probabilities.values(), reverse=True)
                            if ranked[0] < self.policy.min_probability:
                                reason = "low_confidence"
                            elif ranked[0] - ranked[1] < self.policy.min_margin:
                                reason = "ambiguous"
                    except asyncio.CancelledError:
                        self.breaker.cancel(ticket)
                        raise
                    except (DecisionProviderError, TimeoutError) as exc:
                        reason = exc.code if isinstance(exc, DecisionProviderError) else "timeout"
                        if reason in {"state_budget", "privacy_blocked"}:
                            self.breaker.cancel(ticket)
                        else:
                            self.breaker.complete(ticket, success=False)
                    if reason:
                        result = None
                if result is None and self.fallback is not None and not query_only:
                    result = await self._call(self.fallback, prepared, estimate, attempts, "llm")
        except asyncio.CancelledError:
            cancelled = True
            raise
        except (DecisionProviderError, TimeoutError) as exc:
            reason = exc.code if isinstance(exc, DecisionProviderError) else "timeout"
        finally:
            if not cancelled and result is None and not query_only:
                # A safe local choice never turns a provider failure into tool authority.
                safe = next((o.id for o in request.options if o.id in {"none", "clarify"}), None)
                if safe:
                    result = DecisionResult(
                        selected_id=safe,
                        probabilities={o.id: float(o.id == safe) for o in request.options},
                        telemetry=DecisionTelemetry(
                            provider="policy", model_version=POLICY_VERSION
                        ),
                    )
            trace = {
                "phase": request.phase,
                "mode": "shadow" if self.shadow else "active",
                "status": "cancelled"
                if cancelled
                else "failed"
                if query_only and result is None
                else "safe_stop"
                if result is None or (result.telemetry and result.telemetry.provider == "policy")
                else "success",
                "provider": result.telemetry.provider if result and result.telemetry else "jev",
                "model_version": result.telemetry.model_version
                if result and result.telemetry
                else None,
                "selected_id": result.selected_id if result else None,
                "fallback_reason": reason,
                "latency_ms": round((perf_counter() - started) * 1000, 2),
                "candidate_count": len(request.options),
                "attempts": attempts,
                "policy_version": POLICY_VERSION,
            }
            if query_only:
                if result is not None:
                    maximum = max(result.probabilities.values())
                    tied = [k for k, value in result.probabilities.items() if value == maximum]
                    tie_route = "clarify" if "clarify" in result.probabilities else "chat"
                    trace.update(
                        probabilities=dict(result.probabilities),
                        confidence=result.confidence,
                        raw_selected_id=result.selected_id,
                        selected_id=tie_route if len(tied) > 1 else result.selected_id,
                        tied_routes=tied if len(tied) > 1 else [],
                        reason_code="query_route_tie" if len(tied) > 1 else "query_route_argmax",
                    )
            if self.observer:
                await self.observer(trace)
        if result is None:
            raise DecisionProviderError(reason or "provider_error")
        return result
