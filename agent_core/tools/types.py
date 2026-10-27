import re
from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, ClassVar
from uuid import UUID

from jsonschema.exceptions import SchemaError
from jsonschema.validators import validator_for
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, ValidationInfo, create_model, model_validator

from agent_core.llm.types import TokenUsage

_TOOL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Trusted request information supplied by the application, not by the model."""

    user_id: UUID
    conversation_id: UUID
    user_role: str
    run_id: UUID | None = None
    authorization_guard: Callable[[str], Awaitable[None]] | None = field(
        default=None, repr=False, compare=False
    )
    capability_version: str | None = None


class ToolAuthorizationError(RuntimeError):
    """Sanitized failure from an application-owned dispatch authorization check."""

    def __init__(self, code: str = "plugin_authorization_changed") -> None:
        self.code = code
        super().__init__("Capabilities changed; start a new run")


@dataclass(frozen=True, slots=True)
class ToolExecutionPolicy:
    """Runtime limits and retry safety declared by the application."""

    timeout_seconds: float = 10
    max_retries: int = 0
    retry_backoff_seconds: float = 0.2
    idempotent: bool = False
    allowed_roles: frozenset[str] = frozenset({"user", "admin"})
    requires_approval: bool = False

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("Tool timeout phải lớn hơn 0")
        if self.max_retries < 0:
            raise ValueError("Số lần retry không được âm")
        if self.retry_backoff_seconds < 0:
            raise ValueError("Thời gian chờ retry không được âm")
        if not self.allowed_roles:
            raise ValueError("Tool phải cho phép ít nhất một role")
        if not self.allowed_roles.issubset({"user", "admin"}):
            raise ValueError("Tool chỉ hỗ trợ role user hoặc admin")


@dataclass(frozen=True, slots=True)
class ToolRoutingPolicy:
    """Application-owned hints used to select tools before loading full schemas."""

    keywords: frozenset[str] = frozenset()
    always_load: bool = False

    def __post_init__(self) -> None:
        normalized = frozenset(
            keyword.strip().casefold() for keyword in self.keywords if keyword.strip()
        )
        object.__setattr__(self, "keywords", normalized)


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Standard result returned by every LangChain tool."""

    content: str
    data: Mapping[str, Any] = field(default_factory=dict)
    is_error: bool = False
    error_code: str | None = None
    usage: TokenUsage | None = None

    def __post_init__(self) -> None:
        if not self.content.strip():
            raise ValueError("ToolResult.content không được để trống")
        if not self.is_error and self.error_code is not None:
            raise ValueError("ToolResult thành công không được có error_code")

    @classmethod
    def success(
        cls, content: str, *, data: Mapping[str, Any] | None = None, usage: TokenUsage | None = None
    ) -> "ToolResult":
        return cls(content=content, data=data or {}, usage=usage)

    @classmethod
    def failure(
        cls,
        content: str,
        *,
        error_code: str,
        data: Mapping[str, Any] | None = None,
        usage: TokenUsage | None = None,
    ) -> "ToolResult":
        return cls(
            content=content, data=data or {}, is_error=True, error_code=error_code, usage=usage
        )


ContextToolCoroutine = Callable[[ToolContext, Mapping[str, Any]], Awaitable[ToolResult]]


class _JsonSchemaArguments(BaseModel):
    """Pydantic adapter for schemas discovered dynamically from MCP servers."""

    model_config = ConfigDict(extra="allow")
    json_schema: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}

    @model_validator(mode="before")
    @classmethod
    def validate_json_schema(cls, value: Any, _: ValidationInfo) -> Any:
        if not isinstance(value, Mapping):
            raise ValueError("Tool arguments phải là object")
        schema_validator = validator_for(cls.json_schema)(cls.json_schema)
        errors = sorted(
            schema_validator.iter_errors(dict(value)),
            key=lambda error: tuple(str(part) for part in error.path),
        )
        if errors:
            error = errors[0]
            path = ".".join(str(part) for part in error.absolute_path) or "arguments"
            raise ValueError(f"Arguments không hợp lệ tại '{path}': {error.message}")
        return value

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        del args, kwargs
        return deepcopy(cls.json_schema)

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        kwargs.setdefault("exclude_unset", True)
        return super().model_dump(*args, **kwargs)


def pydantic_model_from_json_schema(
    name: str,
    schema: Mapping[str, Any],
) -> type[BaseModel]:
    """Wrap a runtime JSON Schema in a Pydantic model for LangChain tools."""
    copied_schema = deepcopy(dict(schema))
    if copied_schema.get("type") != "object":
        raise ValueError("Tool args schema phải là JSON Schema có type='object'")
    try:
        validator_for(copied_schema).check_schema(copied_schema)
    except SchemaError as exc:
        raise ValueError("Tool args schema không phải JSON Schema hợp lệ") from exc

    properties = copied_schema.get("properties", {})
    if not isinstance(properties, Mapping):
        raise ValueError("Tool args schema properties phải là object")
    required = set(copied_schema.get("required", []))
    field_definitions = {
        str(field_name): (Any, ... if field_name in required else None) for field_name in properties
    }
    model_name = "".join(part.title() for part in name.split("_")) + "Input"
    model = create_model(
        model_name,
        __base__=_JsonSchemaArguments,
        **field_definitions,
    )
    model.json_schema = copied_schema
    return model


def create_context_tool(
    *,
    name: str,
    description: str,
    args_schema: type[BaseModel] | Mapping[str, Any],
    coroutine: ContextToolCoroutine,
    return_direct: bool = False,
) -> StructuredTool:
    """Create a LangChain tool while keeping trusted request context out of model input."""
    if not _TOOL_NAME_PATTERN.fullmatch(name):
        raise ValueError(
            "Tool name phải bắt đầu bằng chữ thường và chỉ gồm a-z, 0-9, dấu gạch dưới"
        )
    clean_description = description.strip()
    if not clean_description:
        raise ValueError("Tool description không được để trống")
    if not callable(coroutine):
        raise TypeError("Tool coroutine phải là callable")

    pydantic_schema = (
        args_schema
        if isinstance(args_schema, type) and issubclass(args_schema, BaseModel)
        else pydantic_model_from_json_schema(name, args_schema)
    )

    async def invoke(config: RunnableConfig, **arguments: Any) -> ToolResult:
        context = config.get("configurable", {}).get("tool_context")
        if not isinstance(context, ToolContext):
            raise RuntimeError("LangChain tool thiếu ToolContext tin cậy")
        result = await coroutine(context, arguments)
        if not isinstance(result, ToolResult):
            raise TypeError("LangChain tool phải trả về ToolResult")
        return result

    return StructuredTool(
        name=name,
        description=clean_description,
        args_schema=pydantic_schema,
        coroutine=invoke,
        return_direct=return_direct,
    )
