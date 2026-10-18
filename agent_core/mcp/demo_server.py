from datetime import UTC, datetime
from typing import Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

server = MCPServer(
    "personal-agent-demo",
    version="0.1.0",
    instructions="MCP server local dùng để kiểm tra kết nối của Personal AI Agent.",
)

READ_ONLY_TOOL = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=False,
)
DETERMINISTIC_TOOL = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)
SENSITIVE_TOOL = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=False,
    open_world_hint=False,
)


@server.tool(annotations=READ_ONLY_TOOL)
def get_server_time() -> dict[str, str]:
    """Return the current server time in UTC using ISO 8601 format."""
    return {
        "timezone": "UTC",
        "iso_time": datetime.now(UTC).isoformat(),
    }


@server.tool(annotations=DETERMINISTIC_TOOL)
def calculate(
    a: float,
    b: float,
    operation: Literal["add", "subtract", "multiply", "divide"],
) -> dict[str, float | str]:
    """Calculate two numbers using one of: add, subtract, multiply, or divide."""
    if operation == "add":
        result = a + b
    elif operation == "subtract":
        result = a - b
    elif operation == "multiply":
        result = a * b
    else:
        if b == 0:
            raise ValueError("Cannot divide by zero")
        result = a / b

    return {
        "a": a,
        "b": b,
        "operation": operation,
        "result": result,
    }


@server.tool(annotations=DETERMINISTIC_TOOL)
def count_text(text: str) -> dict[str, int]:
    """Count Unicode characters, whitespace-separated words, and lines in text."""
    return {
        "characters": len(text),
        "words": len(text.split()),
        "lines": 0 if not text else text.count("\n") + 1,
    }


@server.tool(annotations=DETERMINISTIC_TOOL)
def convert_temperature(
    value: float,
    from_unit: Literal["celsius", "fahrenheit", "kelvin"],
    to_unit: Literal["celsius", "fahrenheit", "kelvin"],
) -> dict[str, float | str]:
    """Convert a temperature between Celsius, Fahrenheit, and Kelvin."""
    if from_unit == "celsius":
        celsius = value
    elif from_unit == "fahrenheit":
        celsius = (value - 32) * 5 / 9
    else:
        celsius = value - 273.15

    if celsius < -273.15:
        raise ValueError("Temperature cannot be below absolute zero")

    if to_unit == "celsius":
        result = celsius
    elif to_unit == "fahrenheit":
        result = celsius * 9 / 5 + 32
    else:
        result = celsius + 273.15

    return {
        "input_value": value,
        "from_unit": from_unit,
        "to_unit": to_unit,
        "result": round(result, 6),
    }


@server.tool(annotations=SENSITIVE_TOOL)
def simulate_sensitive_action(action: str) -> dict[str, str | bool]:
    """Simulate a sensitive action after approval without changing external data."""
    return {
        "action": action,
        "simulated": True,
        "message": "The approved action was simulated; no external data was changed.",
    }


@server.tool(annotations=READ_ONLY_TOOL)
def get_admin_runtime_info() -> dict[str, str]:
    """Return non-sensitive runtime identity for an administrator."""
    return {
        "server": "personal-agent-demo",
        "version": "0.1.0",
    }


if __name__ == "__main__":
    server.run("stdio")
