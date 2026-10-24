from dataclasses import dataclass
from typing import Literal

from langchain_core.tools import BaseTool

from agent_core.context_budget import ContextBudgetError, estimate_tokens
from agent_core.tools.registry import ToolRegistry


@dataclass(frozen=True, slots=True)
class ToolSelectionConfig:
    lazy_load_threshold: int = 8
    max_loaded_tools: int = 4
    schema_token_budget: int = 6000

    def __post_init__(self) -> None:
        if self.lazy_load_threshold < 1 or self.max_loaded_tools < 1:
            raise ValueError("Tool selection limits must be positive")
        if self.schema_token_budget < 0:
            raise ValueError("Schema token budget cannot be negative")


@dataclass(frozen=True, slots=True)
class ToolSelection:
    tools: tuple[BaseTool, ...]
    total_accessible: int
    mode: Literal["all", "relevant", "skill"]
    catalog_version: str = ""
    definitions: tuple[dict, ...] = ()
    schema_tokens: int = 0
    omitted_names: tuple[str, ...] = ()

    @property
    def loaded_names(self) -> frozenset[str]:
        return frozenset(tool.name for tool in self.tools)


class ToolSelector:
    """Index metadata first; lazily convert and budget only candidate schemas."""

    def __init__(self, config: ToolSelectionConfig | None = None) -> None:
        self.config = config or ToolSelectionConfig()

    def fit(
        self,
        registry: ToolRegistry,
        role: str,
        names: tuple[str, ...],
        *,
        mode: Literal["all", "relevant", "skill"],
        required: tuple[str, ...] = (),
        max_tools: int | None = None,
    ) -> ToolSelection:
        catalog = registry.capability_catalog(role)
        selected: list[str] = []
        definitions: list[dict] = []
        omitted: list[str] = []
        limit = max_tools if max_tools is not None else self.config.max_loaded_tools
        # Required tools are one atomic group: never silently run a partial Skill.
        for name in dict.fromkeys((*required, *names)):
            if not registry.can_access(name, role):
                if name in required:
                    raise ContextBudgetError("A required Skill tool is unavailable")
                continue
            if len(selected) >= limit and name not in required:
                omitted.append(name)
                continue
            definition = registry.model_definition(name)
            candidate = sorted([*definitions, definition], key=lambda d: d["function"]["name"])
            if estimate_tokens(candidate) > self.config.schema_token_budget:
                if name in required:
                    raise ContextBudgetError("Required Skill schemas exceed the tool token budget")
                omitted.append(name)
                continue
            definitions.append(definition)
            selected.append(name)
        ordered = tuple(sorted(definitions, key=lambda d: d["function"]["name"]))
        return ToolSelection(
            tools=tuple(registry.get(name).tool for name in selected),
            total_accessible=len(catalog.records),
            mode=mode,
            catalog_version=catalog.version,
            definitions=ordered,
            schema_tokens=estimate_tokens(ordered) if ordered else 0,
            omitted_names=tuple(omitted),
        )

    def select(self, registry: ToolRegistry, role: str, query: str) -> ToolSelection:
        catalog = registry.capability_catalog(role)
        small = len(catalog.records) <= self.config.lazy_load_threshold
        records = catalog.records if small else catalog.search(query[:32768], limit=4096)
        return self.fit(
            registry,
            role,
            tuple(record.binding_name for record in records),
            mode="all" if small else "relevant",
            max_tools=len(records) if small else self.config.max_loaded_tools,
        )
