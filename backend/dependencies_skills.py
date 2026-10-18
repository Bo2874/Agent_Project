from pathlib import Path
from typing import Annotated

from fastapi import Depends

from agent_core.skills import SkillRegistry

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SKILLS_ROOT = PROJECT_ROOT / "skills"

skill_registry = SkillRegistry()
skill_registry.discover(SKILLS_ROOT)


def get_skill_registry() -> SkillRegistry:
    return skill_registry


SkillRegistryDependency = Annotated[SkillRegistry, Depends(get_skill_registry)]
