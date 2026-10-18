import json
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SKILL_SCHEMA_VERSION = 1
SKILL_MANIFEST_FILE = "skill.json"
SKILL_INSTRUCTIONS_FILE = "instructions.md"
MAX_MANIFEST_BYTES = 64_000
MAX_INSTRUCTIONS_CHARACTERS = 50_000

_SKILL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_TAG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_TOOL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_ROOT_FIELDS = frozenset({"schema_version", "metadata", "required_tools"})
_METADATA_FIELDS = frozenset({"name", "version", "description", "tags"})


class SkillFormatError(ValueError):
    """Raised when a Skill directory does not follow the application format."""


def _normalized_unique_strings(
    values: Collection[str],
    *,
    field_name: str,
    pattern: re.Pattern[str],
) -> tuple[str, ...]:
    if isinstance(values, str):
        raise SkillFormatError(f"{field_name} phải là một danh sách")
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_value in values:
        if not isinstance(raw_value, str):
            raise SkillFormatError(f"{field_name} chỉ được chứa chuỗi")
        value = raw_value.strip().casefold()
        if not pattern.fullmatch(value):
            raise SkillFormatError(f"{field_name} chứa giá trị không hợp lệ: {raw_value!r}")
        if value not in seen:
            normalized.append(value)
            seen.add(value)
    return tuple(normalized)


@dataclass(frozen=True, slots=True)
class SkillMetadata:
    """Small, searchable Skill data that can be loaded without its instructions."""

    name: str
    version: str
    description: str
    tags: tuple[str, ...] = ()
    required_tools: tuple[str, ...] = ()
    schema_version: int = SKILL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or not isinstance(self.name, str)
            or not isinstance(self.version, str)
            or not isinstance(self.description, str)
        ):
            raise SkillFormatError("Metadata của Skill sai kiểu dữ liệu")
        name = self.name.strip()
        version = self.version.strip()
        description = " ".join(self.description.split())
        if self.schema_version != SKILL_SCHEMA_VERSION:
            raise SkillFormatError(f"Chỉ hỗ trợ Skill schema_version={SKILL_SCHEMA_VERSION}")
        if not _SKILL_NAME_PATTERN.fullmatch(name):
            raise SkillFormatError(
                "Skill name phải bắt đầu bằng chữ thường và chỉ gồm a-z, 0-9, dấu gạch dưới"
            )
        if not _SEMVER_PATTERN.fullmatch(version):
            raise SkillFormatError("Skill version phải theo Semantic Versioning, ví dụ 1.0.0")
        if not description or len(description) > 500:
            raise SkillFormatError("Skill description phải có từ 1 đến 500 ký tự")
        tags = _normalized_unique_strings(
            self.tags,
            field_name="Skill tags",
            pattern=_TAG_PATTERN,
        )
        if len(tags) > 20:
            raise SkillFormatError("Skill chỉ được có tối đa 20 tags")
        required_tools = _normalized_unique_strings(
            self.required_tools,
            field_name="Skill required_tools",
            pattern=_TOOL_NAME_PATTERN,
        )
        if len(required_tools) > 50:
            raise SkillFormatError("Skill chỉ được yêu cầu tối đa 50 tools")

        object.__setattr__(self, "name", name)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "tags", tags)
        object.__setattr__(self, "required_tools", required_tools)

    def missing_tools(self, available_tools: Collection[str]) -> tuple[str, ...]:
        available = frozenset(available_tools)
        return tuple(name for name in self.required_tools if name not in available)


@dataclass(frozen=True, slots=True)
class SkillSpec:
    """Complete Skill loaded only after its lightweight metadata is selected."""

    metadata: SkillMetadata
    instructions: str

    def __post_init__(self) -> None:
        if not isinstance(self.instructions, str):
            raise SkillFormatError("Skill instructions phải là chuỗi Markdown")
        instructions = self.instructions.strip()
        if not instructions:
            raise SkillFormatError("Skill instructions không được để trống")
        if "\x00" in instructions:
            raise SkillFormatError("Skill instructions không được chứa ký tự null")
        if len(instructions) > MAX_INSTRUCTIONS_CHARACTERS:
            raise SkillFormatError(
                f"Skill instructions không được vượt {MAX_INSTRUCTIONS_CHARACTERS} ký tự"
            )
        object.__setattr__(self, "instructions", instructions)

    @property
    def name(self) -> str:
        return self.metadata.name

    @property
    def required_tools(self) -> tuple[str, ...]:
        return self.metadata.required_tools


def _read_manifest(skill_directory: Path) -> Mapping[str, Any]:
    manifest_path = skill_directory / SKILL_MANIFEST_FILE
    try:
        manifest_size = manifest_path.stat().st_size
    except FileNotFoundError as exc:
        raise SkillFormatError(f"Thiếu file {SKILL_MANIFEST_FILE}") from exc
    if manifest_size > MAX_MANIFEST_BYTES:
        raise SkillFormatError(f"{SKILL_MANIFEST_FILE} vượt giới hạn kích thước")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        raise SkillFormatError(f"{SKILL_MANIFEST_FILE} phải dùng UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise SkillFormatError(f"{SKILL_MANIFEST_FILE} không phải JSON hợp lệ") from exc
    if not isinstance(payload, Mapping):
        raise SkillFormatError(f"{SKILL_MANIFEST_FILE} phải là JSON object")
    unknown_fields = set(payload) - _ROOT_FIELDS
    missing_fields = _ROOT_FIELDS - set(payload)
    if unknown_fields:
        raise SkillFormatError(f"Field không được hỗ trợ: {sorted(unknown_fields)}")
    if missing_fields:
        raise SkillFormatError(f"Thiếu field bắt buộc: {sorted(missing_fields)}")
    return payload


def load_skill_metadata(skill_directory: str | Path) -> SkillMetadata:
    """Load only data needed for discovery and tool availability checks."""
    directory = Path(skill_directory)
    if not directory.is_dir():
        raise SkillFormatError("Đường dẫn Skill phải là một thư mục")
    payload = _read_manifest(directory)
    raw_metadata = payload["metadata"]
    raw_tools = payload["required_tools"]
    if not isinstance(raw_metadata, Mapping):
        raise SkillFormatError("metadata phải là JSON object")
    unknown_metadata = set(raw_metadata) - _METADATA_FIELDS
    missing_metadata = {"name", "version", "description"} - set(raw_metadata)
    if unknown_metadata:
        raise SkillFormatError(f"Metadata field không được hỗ trợ: {sorted(unknown_metadata)}")
    if missing_metadata:
        raise SkillFormatError(f"Thiếu metadata bắt buộc: {sorted(missing_metadata)}")
    raw_tags = raw_metadata.get("tags", [])
    if not isinstance(raw_tags, list):
        raise SkillFormatError("metadata.tags phải là JSON array")
    if not isinstance(raw_tools, list):
        raise SkillFormatError("required_tools phải là JSON array")
    try:
        schema_version = payload["schema_version"]
        if isinstance(schema_version, bool) or not isinstance(schema_version, int):
            raise TypeError
        return SkillMetadata(
            schema_version=schema_version,
            name=raw_metadata["name"],
            version=raw_metadata["version"],
            description=raw_metadata["description"],
            tags=tuple(raw_tags),
            required_tools=tuple(raw_tools),
        )
    except (TypeError, AttributeError) as exc:
        raise SkillFormatError("Metadata của Skill sai kiểu dữ liệu") from exc


def load_skill(skill_directory: str | Path) -> SkillSpec:
    """Load metadata first, then the Markdown instructions for a selected Skill."""
    directory = Path(skill_directory)
    metadata = load_skill_metadata(directory)
    instructions_path = directory / SKILL_INSTRUCTIONS_FILE
    try:
        if instructions_path.stat().st_size > MAX_INSTRUCTIONS_CHARACTERS * 4:
            raise SkillFormatError(f"{SKILL_INSTRUCTIONS_FILE} vượt giới hạn kích thước")
        instructions = instructions_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise SkillFormatError(f"Thiếu file {SKILL_INSTRUCTIONS_FILE}") from exc
    except UnicodeDecodeError as exc:
        raise SkillFormatError(f"{SKILL_INSTRUCTIONS_FILE} phải dùng UTF-8") from exc
    return SkillSpec(metadata=metadata, instructions=instructions)
