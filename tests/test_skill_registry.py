import json
from pathlib import Path

import pytest

from agent_core.skills import (
    DuplicateSkillError,
    SkillChangedError,
    SkillDependencyError,
    SkillFormatError,
    SkillNotFoundError,
    SkillRegistry,
)


def write_skill(
    root: Path,
    name: str,
    *,
    description: str,
    tags: list[str],
    required_tools: list[str],
    instructions: str = "Thực hiện từng bước và kiểm tra kết quả.",
    version: str = "1.0.0",
) -> Path:
    directory = root / name
    directory.mkdir()
    manifest = {
        "schema_version": 1,
        "metadata": {
            "name": name,
            "version": version,
            "description": description,
            "tags": tags,
        },
        "required_tools": required_tools,
    }
    (directory / "skill.json").write_text(
        json.dumps(manifest, ensure_ascii=False),
        encoding="utf-8",
    )
    (directory / "instructions.md").write_text(instructions, encoding="utf-8")
    return directory


def test_discover_registers_only_immediate_skill_directories(tmp_path: Path) -> None:
    write_skill(
        tmp_path,
        "research_summary",
        description="Nghiên cứu và tóm tắt nguồn đáng tin cậy.",
        tags=["research", "summary"],
        required_tools=["web_search"],
    )
    (tmp_path / "README.md").write_text("Không phải Skill", encoding="utf-8")
    (tmp_path / ".hidden").mkdir()
    registry = SkillRegistry()

    discovered = registry.discover(tmp_path)

    assert [metadata.name for metadata in discovered] == ["research_summary"]
    assert len(registry) == 1
    assert registry.loaded_names == frozenset()


def test_search_ranks_name_tags_and_description(tmp_path: Path) -> None:
    write_skill(
        tmp_path,
        "research_summary",
        description="Nghiên cứu nguồn và viết tóm tắt có dẫn chứng.",
        tags=["nghien_cuu", "summary"],
        required_tools=["web_search"],
    )
    write_skill(
        tmp_path,
        "meeting_notes",
        description="Chuẩn hóa ghi chú và công việc sau cuộc họp.",
        tags=["meeting", "notes"],
        required_tools=[],
    )
    registry = SkillRegistry()
    registry.discover(tmp_path)

    matches = registry.search(
        "Hãy nghiên cứu và tóm tắt chủ đề này",
        available_tools={"web_search"},
    )

    assert matches[0].metadata.name == "research_summary"
    assert matches[0].score > 0
    assert matches[0].is_available


def test_search_filters_skill_when_required_tool_is_unavailable(tmp_path: Path) -> None:
    write_skill(
        tmp_path,
        "research_summary",
        description="Nghiên cứu và tóm tắt.",
        tags=["research"],
        required_tools=["web_search", "document_reader"],
    )
    registry = SkillRegistry()
    registry.discover(tmp_path)

    assert registry.search("research", available_tools={"web_search"}) == ()
    diagnostic = registry.search(
        "research",
        available_tools={"web_search"},
        include_unavailable=True,
    )
    assert diagnostic[0].missing_tools == ("document_reader",)
    assert not diagnostic[0].is_available


def test_search_ignores_only_stop_words(tmp_path: Path) -> None:
    write_skill(
        tmp_path,
        "meeting_notes",
        description="Chuẩn hóa nội dung cuộc họp.",
        tags=["meeting"],
        required_tools=[],
    )
    registry = SkillRegistry()
    registry.discover(tmp_path)

    assert registry.search("hãy và cho tôi", available_tools=set()) == ()


def test_progressive_disclosure_does_not_read_instructions_until_load(
    tmp_path: Path,
) -> None:
    directory = write_skill(
        tmp_path,
        "meeting_notes",
        description="Tóm tắt cuộc họp.",
        tags=["meeting"],
        required_tools=[],
    )
    registry = SkillRegistry()
    registry.discover(tmp_path)
    (directory / "instructions.md").write_text("   ", encoding="utf-8")

    matches = registry.search("meeting", available_tools=set())

    assert matches[0].metadata.name == "meeting_notes"
    assert registry.loaded_names == frozenset()
    with pytest.raises(SkillFormatError, match="instructions"):
        registry.load("meeting_notes", available_tools=set())


def test_load_checks_dependencies_before_reading_and_caches_skill(tmp_path: Path) -> None:
    directory = write_skill(
        tmp_path,
        "research_summary",
        description="Nghiên cứu và tóm tắt.",
        tags=["research"],
        required_tools=["web_search"],
        instructions="Phiên bản hướng dẫn đầu tiên.",
    )
    registry = SkillRegistry()
    registry.discover(tmp_path)

    with pytest.raises(SkillDependencyError, match="web_search"):
        registry.load("research_summary", available_tools=set())

    loaded = registry.load("research_summary", available_tools={"web_search"})
    (directory / "instructions.md").write_text("Đã thay đổi trên đĩa.", encoding="utf-8")
    cached = registry.load("research_summary", available_tools={"web_search"})

    assert loaded is cached
    assert cached.instructions == "Phiên bản hướng dẫn đầu tiên."
    assert registry.loaded_names == frozenset({"research_summary"})


def test_load_detects_manifest_change_after_discovery(tmp_path: Path) -> None:
    directory = write_skill(
        tmp_path,
        "meeting_notes",
        description="Tóm tắt cuộc họp.",
        tags=["meeting"],
        required_tools=[],
    )
    registry = SkillRegistry()
    registry.discover(tmp_path)
    manifest_path = directory / "skill.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["metadata"]["version"] = "2.0.0"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(SkillChangedError, match="đăng ký lại"):
        registry.load("meeting_notes", available_tools=set())


def test_duplicate_unknown_and_unregister_behaviour(tmp_path: Path) -> None:
    directory = write_skill(
        tmp_path,
        "meeting_notes",
        description="Tóm tắt cuộc họp.",
        tags=["meeting"],
        required_tools=[],
    )
    registry = SkillRegistry()
    registry.register(directory)

    with pytest.raises(DuplicateSkillError):
        registry.register(directory)
    with pytest.raises(SkillNotFoundError):
        registry.get_metadata("unknown")

    removed = registry.unregister("meeting_notes")
    assert removed.metadata.name == "meeting_notes"
    assert "meeting_notes" not in registry
