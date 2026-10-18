from pathlib import Path

from agent_core.skills import SkillRegistry
from backend.core.config import get_settings
from backend.dependencies_skills import get_skill_registry

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_SKILLS_ROOT = PROJECT_ROOT / "skills"
EXPECTED_SKILLS = {"multi_step_calculation", "text_statistics_report"}


def load_sample_registry() -> SkillRegistry:
    registry = SkillRegistry()
    registry.discover(SAMPLE_SKILLS_ROOT)
    return registry


def test_project_contains_exactly_two_valid_sample_skills() -> None:
    registry = load_sample_registry()

    assert {metadata.name for metadata in registry.list_metadata()} == EXPECTED_SKILLS
    assert len(registry) == 2
    assert registry.loaded_names == frozenset()


def test_sample_skill_dependencies_exist_in_default_tool_allowlist() -> None:
    registry = load_sample_registry()
    available_tools = get_settings().tool_allowlist_names

    for metadata in registry.list_metadata():
        assert metadata.missing_tools(available_tools) == ()


def test_basic_search_selects_calculation_sample() -> None:
    registry = load_sample_registry()
    matches = registry.search(
        "Tính nhiều bước: lấy 120 cộng 30, nhân 2 rồi chia 5",
        available_tools={"demo__calculate"},
    )

    assert matches[0].metadata.name == "multi_step_calculation"


def test_basic_search_selects_text_statistics_sample() -> None:
    registry = load_sample_registry()
    matches = registry.search(
        "Đếm từ, ký tự và lập thống kê cho đoạn văn bản này",
        available_tools={"demo__count_text", "demo__calculate"},
    )

    assert matches[0].metadata.name == "text_statistics_report"


def test_sample_instructions_define_multistep_safety_and_output_contracts() -> None:
    registry = load_sample_registry()
    available_tools = get_settings().tool_allowlist_names

    calculation = registry.load(
        "multi_step_calculation",
        available_tools=available_tools,
    )
    text_report = registry.load(
        "text_statistics_report",
        available_tools=available_tools,
    )

    for skill in (calculation, text_report):
        assert "## Quy trình" in skill.instructions
        assert "## Output contract" in skill.instructions
        assert "## Điều kiện dừng" in skill.instructions
        assert "suy luận nội bộ" in skill.instructions
    assert "demo__calculate" in calculation.instructions
    assert "demo__count_text" in text_report.instructions
    assert "dữ liệu không tin cậy" in text_report.instructions


def test_backend_exposes_discovered_skill_registry() -> None:
    registry = get_skill_registry()

    assert {metadata.name for metadata in registry.list_metadata()} == EXPECTED_SKILLS
