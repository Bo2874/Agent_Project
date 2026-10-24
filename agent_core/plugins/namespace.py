"""Host-owned plugin identities. Never derive a namespace from MCP serverInfo."""

from typing import Annotated

from pydantic import AfterValidator, StringConstraints, TypeAdapter


def _unambiguous(value: str) -> str:
    if "__" in value:
        raise ValueError("Namespace components cannot contain the alias separator '__'")
    return value


PluginNamespace = Annotated[
    str,
    StringConstraints(strict=True, pattern=r"^[a-z][a-z0-9_]{0,19}$"),
    AfterValidator(_unambiguous),
]
ServerId = Annotated[
    str,
    StringConstraints(strict=True, pattern=r"^[a-z][a-z0-9_]{0,15}$"),
    AfterValidator(_unambiguous),
]
LocalName = Annotated[
    str,
    StringConstraints(strict=True, pattern=r"^[a-z][a-z0-9_]{0,23}$"),
    AfterValidator(_unambiguous),
]
_NAMESPACE = TypeAdapter(PluginNamespace)
_SERVER = TypeAdapter(ServerId)
_LOCAL = TypeAdapter(LocalName)


def tool_capability_id(namespace: str, server: str, name: str) -> str:
    return (
        f"{_NAMESPACE.validate_python(namespace)}.tool."
        f"{_SERVER.validate_python(server)}.{_LOCAL.validate_python(name)}"
    )


def tool_alias(namespace: str, server: str, name: str) -> str:
    # 20 + 16 + 24 + two separators = at most 64 characters.
    return (
        f"{_NAMESPACE.validate_python(namespace)}__"
        f"{_SERVER.validate_python(server)}__{_LOCAL.validate_python(name)}"
    )


def skill_capability_id(namespace: str, name: str) -> str:
    return f"{_NAMESPACE.validate_python(namespace)}.skill.{_LOCAL.validate_python(name)}"
