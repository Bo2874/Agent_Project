from collections.abc import Collection
from dataclasses import dataclass
from typing import Literal

from agent_core.skills.registry import SkillMatch, SkillRegistry


@dataclass(frozen=True, slots=True)
class SkillSelectionConfig:
    minimum_score: int = 20
    ambiguity_margin: int = 5

    def __post_init__(self) -> None:
        if self.minimum_score < 1:
            raise ValueError("Skill minimum_score phải lớn hơn hoặc bằng 1")
        if self.ambiguity_margin < 0:
            raise ValueError("Skill ambiguity_margin không được âm")


@dataclass(frozen=True, slots=True)
class SkillSelection:
    match: SkillMatch | None
    candidates: tuple[SkillMatch, ...]
    reason: Literal["selected", "no_match", "low_score", "ambiguous"]

    @property
    def selected_name(self) -> str | None:
        return self.match.metadata.name if self.match is not None else None


class SkillSelector:
    """Select one high-confidence Skill from metadata without loading instructions."""

    def __init__(self, config: SkillSelectionConfig | None = None) -> None:
        self.config = config or SkillSelectionConfig()

    def select(
        self,
        registry: SkillRegistry,
        query: str,
        *,
        available_tools: Collection[str],
    ) -> SkillSelection:
        candidates = registry.search(
            query,
            available_tools=available_tools,
            limit=2,
        )
        if not candidates:
            return SkillSelection(match=None, candidates=(), reason="no_match")
        best = candidates[0]
        if best.score < self.config.minimum_score:
            return SkillSelection(
                match=None,
                candidates=candidates,
                reason="low_score",
            )
        if len(candidates) > 1 and best.score - candidates[1].score < self.config.ambiguity_margin:
            return SkillSelection(
                match=None,
                candidates=candidates,
                reason="ambiguous",
            )
        return SkillSelection(match=best, candidates=candidates, reason="selected")
