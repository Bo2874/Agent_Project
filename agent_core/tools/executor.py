import asyncio
import hashlib
import json
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from pydantic import BaseModel, ValidationError

from agent_core.tools.registry import RegisteredTool, ToolNotFoundError, ToolRegistry
from agent_core.tools.types import ToolAuthorizationError, ToolContext, ToolResult

_RETRYABLE_ERROR_CODES = frozenset(
    {
        "mcp_not_connected",
        "mcp_protocol_error",
        "temporary_failure",
        "tool_timeout",
        "tool_unavailable",
    }
)


class ToolExecutor:
    """Apply application policy around validated LangChain tool invocation."""

    def __init__(
        self,
        *,
        expected_tools: Mapping[str, RegisteredTool] | None = None,
        force_approval: bool = False,
    ) -> None:
        self._successful_results: dict[str, tuple[RegisteredTool, ToolResult]] = {}
        self._expected_tools = dict(expected_tools) if expected_tools is not None else None
        self._force_approval = force_approval

    async def execute(
        self,
        registry: ToolRegistry,
        context: ToolContext,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        approval_granted: bool | None = None,
    ) -> ToolResult:
        if registry.authorization_guard is not None or registry.owner_user_id is not None:
            incoming_context, incoming_guard = context, context.authorization_guard

            async def authorize(name):
                await registry.authorize(incoming_context, name)
                if incoming_guard:
                    await incoming_guard(name)

            context = replace(context, authorization_guard=authorize)
        try:
            registered = registry.get(tool_name)
        except ToolNotFoundError:
            return ToolResult.failure(
                f"Tool '{tool_name}' không tồn tại.",
                error_code="tool_not_found",
            )

        if not registry.is_allowlisted(tool_name):
            return ToolResult.failure(
                f"Tool '{tool_name}' không nằm trong allowlist.",
                error_code="tool_not_allowlisted",
            )
        if not registry.is_role_allowed(tool_name, context.user_role):
            return ToolResult.failure(
                f"Tài khoản không có quyền dùng tool '{tool_name}'.",
                error_code="tool_permission_denied",
            )

        if (
            self._expected_tools is not None
            and self._expected_tools.get(tool_name) is not registered
        ):
            return ToolResult.failure(
                "Tool catalog changed; start a new run", error_code="tool_catalog_changed"
            )
        try:
            if context.authorization_guard is not None:
                await context.authorization_guard(tool_name)
        except ToolAuthorizationError as exc:
            return ToolResult.failure(str(exc), error_code=exc.code)
        validation_error = self._validate_arguments(registered.tool.args_schema, arguments)
        if validation_error is not None:
            return validation_error

        policy = registered.policy
        idempotency_key = self._idempotency_key(context, tool_name, arguments)
        cached = self._successful_results.get(idempotency_key)
        if policy.idempotent and cached is not None and cached[0] is registered:
            return cached[1]
        if (policy.requires_approval or self._force_approval) and approval_granted is not True:
            if approval_granted is False:
                return ToolResult.failure(
                    f"User đã từ chối tool '{tool_name}'.",
                    error_code="tool_approval_denied",
                )
            return ToolResult.failure(
                f"Tool '{tool_name}' cần user phê duyệt trước khi chạy.",
                error_code="tool_approval_required",
            )

        max_attempts = policy.max_retries + 1 if policy.idempotent else 1
        for attempt in range(1, max_attempts + 1):
            if tool_name not in registry or registry.get(tool_name) is not registered:
                return ToolResult.failure(
                    "Tool catalog changed; start a new run", error_code="tool_catalog_changed"
                )
            try:
                if context.authorization_guard is not None:
                    await context.authorization_guard(tool_name)
            except ToolAuthorizationError as exc:
                return ToolResult.failure(str(exc), error_code=exc.code)
            try:
                async with asyncio.timeout(policy.timeout_seconds):
                    result = await registered.tool.ainvoke(
                        dict(arguments),
                        config={"configurable": {"tool_context": context}},
                    )
                if not isinstance(result, ToolResult):
                    raise TypeError("LangChain tool không trả về ToolResult")
            except TimeoutError:
                result = ToolResult.failure(
                    f"Tool '{tool_name}' vượt quá giới hạn {policy.timeout_seconds:g} giây.",
                    error_code="tool_timeout",
                )
            except Exception:
                return ToolResult.failure(
                    f"Tool '{tool_name}' thực thi thất bại.",
                    error_code="tool_execution_failed",
                )

            # A revoke committed while RPC was in flight must also suppress its observation/cache.
            try:
                if context.authorization_guard is not None:
                    await context.authorization_guard(tool_name)
            except ToolAuthorizationError as exc:
                return ToolResult.failure(str(exc), error_code=exc.code)

            should_retry = (
                result.is_error
                and result.error_code in _RETRYABLE_ERROR_CODES
                and attempt < max_attempts
            )
            if not should_retry:
                if policy.idempotent and not result.is_error:
                    self._successful_results[idempotency_key] = (registered, result)
                return result

            if policy.retry_backoff_seconds:
                await asyncio.sleep(policy.retry_backoff_seconds * attempt)

        raise RuntimeError("Tool retry loop kết thúc bất thường")

    def needs_approval(
        self,
        registry: ToolRegistry,
        context: ToolContext,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> bool:
        try:
            registered = registry.get(tool_name)
        except ToolNotFoundError:
            return False
        if not registry.can_access(tool_name, context.user_role):
            return False
        if (
            self._expected_tools is not None
            and self._expected_tools.get(tool_name) is not registered
        ):
            return False
        if self._validate_arguments(registered.tool.args_schema, arguments) is not None:
            return False
        if not registered.policy.requires_approval and not self._force_approval:
            return False

        key = self._idempotency_key(context, tool_name, arguments)
        cached = self._successful_results.get(key)
        return not (registered.policy.idempotent and cached is not None and cached[0] is registered)

    @staticmethod
    def _validate_arguments(
        schema: type[BaseModel] | None,
        arguments: Mapping[str, Any],
    ) -> ToolResult | None:
        if schema is None:
            return ToolResult.failure(
                "LangChain tool thiếu Pydantic args_schema.",
                error_code="tool_schema_validation_failed",
            )
        try:
            schema.model_validate(dict(arguments))
        except ValidationError as exc:
            detail = exc.errors(include_url=False)[0]
            path = ".".join(str(part) for part in detail.get("loc", ())) or "arguments"
            return ToolResult.failure(
                f"Arguments không hợp lệ tại '{path}': {detail['msg']}",
                error_code="tool_schema_validation_failed",
            )
        return None

    @staticmethod
    def _idempotency_key(
        context: ToolContext,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> str:
        scope_id = context.run_id or context.conversation_id
        canonical_arguments = json.dumps(
            dict(arguments),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        raw_key = f"{context.user_id}:{scope_id}:{tool_name}:{canonical_arguments}"
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
