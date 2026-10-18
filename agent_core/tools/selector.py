import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from langchain_core.tools import BaseTool

from agent_core.tools.registry import RegisteredTool, ToolRegistry

_WORD_PATTERN = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True, slots=True)
class ToolSelectionConfig:
    lazy_load_threshold: int = 8
    max_loaded_tools: int = 4

    def __post_init__(self) -> None:
        if self.lazy_load_threshold < 1:
            raise ValueError("Tool lazy-load threshold phải lớn hơn hoặc bằng 1")
        if self.max_loaded_tools < 1:
            raise ValueError("Số tool được nạp phải lớn hơn hoặc bằng 1")


@dataclass(frozen=True, slots=True)
class ToolSelection:
    tools: tuple[BaseTool, ...]
    total_accessible: int
    mode: Literal["all", "relevant", "skill"]

    @property
    def loaded_names(self) -> frozenset[str]:
        return frozenset(tool.name for tool in self.tools)


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold().replace("_", " "))
    without_marks = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return " ".join(_WORD_PATTERN.findall(without_marks))


def _score_tool(query: str, query_words: set[str], tool: RegisteredTool) -> int:
    score = 0
    for keyword in tool.routing.keywords:
        normalized_keyword = _normalize(keyword)
        if normalized_keyword and normalized_keyword in query:
            score += 20 + len(normalized_keyword.split())

    name_words = set(_normalize(tool.tool.name).split())
    description_words = set(_normalize(tool.tool.description).split())
    score += len(query_words & name_words) * 6
    score += len(query_words & description_words)
    return score


class ToolSelector:
    """Load full schemas only for tools relevant to the current request."""

    def __init__(self, config: ToolSelectionConfig | None = None) -> None:
        self.config = config or ToolSelectionConfig()

    def select(self, registry: ToolRegistry, role: str, query: str) -> ToolSelection:
        accessible = registry.list_accessible_tools(role)
        if len(accessible) <= self.config.lazy_load_threshold:
            return ToolSelection(
                tools=tuple(
                    sorted(
                        (registered.tool for registered in accessible),
                        key=lambda tool: tool.name,
                    )
                ),
                total_accessible=len(accessible),
                mode="all",
            )

        normalized_query = _normalize(query)
        query_words = set(normalized_query.split())
        ranked: list[tuple[int, str, BaseTool]] = []
        for tool in accessible:
            score = _score_tool(normalized_query, query_words, tool)
            if tool.routing.always_load:
                score += 1_000_000
            if score > 0:
                ranked.append((score, tool.tool.name, tool.tool))

        ranked.sort(key=lambda item: (-item[0], item[1]))
        selected = tuple(item[2] for item in ranked[: self.config.max_loaded_tools])
        return ToolSelection(
            tools=selected,
            total_accessible=len(accessible),
            mode="relevant",
        )
