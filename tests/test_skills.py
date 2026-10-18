import json
from pathlib import Path

import pytest

from agent_core.skills import (
    SKILL_SCHEMA_VERSION,
    SkillFormatError,
    SkillMetadata,
    SkillSpec,
    load_skill,
    load_skill_metadata,
)


def write_skill(
    directory: Path,
    *,
    manifest: dict[str, object] | None = None,
    instructions: str = "# Quy trình\n\n1. Tìm nguồn.\n2. Viết tóm tắt.",
) -> None:
    directory.mkdir()
    payload = manifest or {
        "schema_version": SKILL_SCHEMA_VERSION,
        "metadata": {
            "name": "research_summary",
            "version": "1.0.0",
            "description": "Tìm nguồn và viết bản tóm tắt có dẫn chứng.",
            "tags": ["Research", "summary", "research"],
        },
        "required_tools": ["web_search", "document_reader", "web_search"],
    }
    (directory / "skill.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    (directory / "instructions.md").write_text(instructions, encoding="utf-8")


def test_load_metadata_without_loading_instructions(tmp_path: Path) -> None:
    skill_directory = tmp_path / "research_summary"
    write_skill(skill_directory)
    (skill_directory / "instructions.md").unlink()

    metadata = load_skill_metadata(skill_directory)

    assert metadata.name == "research_summary"
    assert metadata.version == "1.0.0"
    assert metadata.tags == ("research", "summary")
    assert metadata.required_tools == ("web_search", "document_reader")
    assert metadata.missing_tools({"web_search"}) == ("document_reader",)


def test_load_complete_skill_after_selection(tmp_path: Path) -> None:
    skill_directory = tmp_path / "research_summary"
    write_skill(skill_directory)

    skill = load_skill(skill_directory)

    assert skill.name == "research_summary"
    assert skill.required_tools == ("web_search", "document_reader")
    assert skill.instructions.startswith("# Quy trình")


@pytest.mark.parametrize(
    ("metadata", "message"),
    [
        (
            {"name": "Invalid Name", "version": "1.0.0", "description": "Mô tả"},
            "Skill name",
        ),
        (
            {"name": "valid_name", "version": "v1", "description": "Mô tả"},
            "Semantic Versioning",
        ),
        (
            {"name": "valid_name", "version": "1.0.0", "description": ""},
            "description",
        ),
    ],
)
def test_metadata_rejects_invalid_identity(
    metadata: dict[str, str],
    message: str,
) -> None:
    with pytest.raises(SkillFormatError, match=message):
        SkillMetadata(**metadata)


def test_metadata_rejects_invalid_tool_name() -> None:
    with pytest.raises(SkillFormatError, match="required_tools"):
        SkillMetadata(
            name="valid_name",
            version="1.0.0",
            description="Mô tả",
            required_tools=("Dangerous Tool",),
        )


def test_manifest_rejects_unknown_field_to_catch_typos(tmp_path: Path) -> None:
    skill_directory = tmp_path / "invalid"
    write_skill(
        skill_directory,
        manifest={
            "schema_version": 1,
            "metadata": {
                "name": "valid_name",
                "version": "1.0.0",
                "description": "Mô tả",
            },
            "required_tools": [],
            "required_tool": ["typo"],
        },
    )

    with pytest.raises(SkillFormatError, match="không được hỗ trợ"):
        load_skill_metadata(skill_directory)


def test_complete_skill_requires_non_empty_instruction_file(tmp_path: Path) -> None:
    skill_directory = tmp_path / "empty"
    write_skill(skill_directory, instructions="   ")

    with pytest.raises(SkillFormatError, match="instructions"):
        load_skill(skill_directory)


def test_skill_spec_is_immutable_and_uses_validated_metadata() -> None:
    metadata = SkillMetadata(
        name="answer_review",
        version="2.1.0",
        description="Kiểm tra câu trả lời trước khi gửi.",
        tags=("quality",),
        required_tools=(),
    )
    skill = SkillSpec(metadata=metadata, instructions="Kiểm tra dữ kiện quan trọng.")

    assert skill.metadata.schema_version == 1
    with pytest.raises(AttributeError):
        skill.instructions = "Thay đổi"  # type: ignore[misc]
