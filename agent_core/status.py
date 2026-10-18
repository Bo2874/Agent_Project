from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class AgentStatusBudget:
    current_step: int
    max_steps: int
    used_tokens: int
    max_tokens: int
    elapsed_seconds: float
    max_seconds: float

    def __post_init__(self) -> None:
        if self.current_step < 1 or self.current_step > self.max_steps:
            raise ValueError("Current step không hợp lệ")
        if self.used_tokens < 0 or self.used_tokens > self.max_tokens:
            raise ValueError("Token usage không hợp lệ")
        if self.elapsed_seconds < 0 or self.max_seconds <= 0:
            raise ValueError("Time budget không hợp lệ")

    def to_payload(self) -> dict[str, Any]:
        return {
            "steps": {
                "current": self.current_step,
                "limit": self.max_steps,
                "remaining": self.max_steps - self.current_step + 1,
            },
            "tokens": {
                "used": self.used_tokens,
                "limit": self.max_tokens,
                "remaining": max(self.max_tokens - self.used_tokens, 0),
            },
            "time_seconds": {
                "elapsed": round(self.elapsed_seconds, 1),
                "limit": self.max_seconds,
                "remaining": round(max(self.max_seconds - self.elapsed_seconds, 0), 1),
            },
        }


@dataclass(frozen=True, slots=True)
class AgentStatusBlock:
    goal: str
    progress: tuple[str, ...]
    remaining_work: tuple[str, ...]
    budget: AgentStatusBudget

    def __post_init__(self) -> None:
        if not self.goal.strip():
            raise ValueError("Status goal không được để trống")
        if not self.progress or not all(item.strip() for item in self.progress):
            raise ValueError("Status progress không hợp lệ")
        if not self.remaining_work or not all(item.strip() for item in self.remaining_work):
            raise ValueError("Status remaining_work không hợp lệ")

    def to_payload(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "progress": list(self.progress),
            "remaining_work": list(self.remaining_work),
            "budget": self.budget.to_payload(),
        }
