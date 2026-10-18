import json
import sys

from agent_core.skills import (
    DEFAULT_ADHERENCE_CASES,
    DEFAULT_SELECTION_CASES,
    evaluate_skill_adherence,
    evaluate_skill_selection,
)
from backend.core.config import get_settings
from backend.dependencies_skills import get_skill_registry


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    registry = get_skill_registry()
    available_tools = get_settings().tool_allowlist_names
    selection = evaluate_skill_selection(
        registry,
        DEFAULT_SELECTION_CASES,
        available_tools=available_tools,
    )
    adherence = evaluate_skill_adherence(registry, DEFAULT_ADHERENCE_CASES)
    payload = {
        "selection": selection.to_payload(),
        "adherence": adherence.to_payload(),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if selection.pass_rate < 1 or adherence.pass_rate < 1:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
