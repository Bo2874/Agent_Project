import re
import unicodedata
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from pathlib import Path

from agent_core.contracts import CapabilityDescriptor
from agent_core.skills.spec import (
    SKILL_INSTRUCTIONS_FILE,
    SKILL_MANIFEST_FILE,
    SkillMetadata,
    SkillSpec,
    load_skill,
    load_skill_metadata,
)

_WORD_PATTERN = re.compile(r"[a-z0-9]+")
_STOP_WORDS = {
    "a",
    "an",
    "and",
    "cho",
    "cua",
    "de",
    "for",
    "hay",
    "is",
    "la",
    "mot",
    "of",
    "so",
    "the",
    "toi",
    "tu",
    "va",
    "ve",
}


class SkillRegistryError(RuntimeError):
    """Base error raised by Skill Registry operations."""


class DuplicateSkillError(SkillRegistryError):
    pass


class SkillNotFoundError(SkillRegistryError):
    pass


class SkillDependencyError(SkillRegistryError):
    pass


class SkillChangedError(SkillRegistryError):
    pass


@dataclass(frozen=True, slots=True)
class RegisteredSkill:
    metadata: SkillMetadata
    directory: Path | None = None
    loader: Callable[[], SkillSpec] | None = None
    validator: Callable[[], Awaitable[None]] | None = None
    capability: CapabilityDescriptor | None = None
    metadata_validator: Callable[[], Awaitable[None]] | None = None


@dataclass(frozen=True, slots=True)
class SkillMatch:
    metadata: SkillMetadata
    score: int
    missing_tools: tuple[str, ...] = ()

    @property
    def is_available(self) -> bool:
        return not self.missing_tools


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold().replace("_", " "))
    without_marks = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    ).replace("đ", "d")
    return " ".join(_WORD_PATTERN.findall(without_marks))


def _score_skill(query: str, query_words: set[str], metadata: SkillMetadata) -> int:
    name = _normalize(metadata.name)
    description_words = set(_normalize(metadata.description).split())
    tags = tuple(_normalize(tag) for tag in metadata.tags)
    tag_words = {word for tag in tags for word in tag.split()}

    score = 0
    if query == name:
        score += 100
    elif name and name in query:
        score += 50
    score += len(query_words & set(name.split())) * 12
    score += len(query_words & tag_words) * 10
    score += len(query_words & description_words) * 3
    score += sum(20 for tag in tags if tag and tag in query)
    return score


class SkillRegistry:
    """Metadata-first Skill catalog with lazy instruction loading."""

    def __init__(self) -> None:
        self._skills: dict[str, RegisteredSkill] = {}
        self._loaded: dict[str, SkillSpec] = {}

    def fork(self) -> "SkillRegistry":
        result = SkillRegistry()
        result._skills = dict(self._skills)
        result._loaded = dict(self._loaded)
        return result

    def register_loader(
        self,
        metadata: SkillMetadata,
        loader: Callable[[], SkillSpec],
        *,
        validator: Callable[[], Awaitable[None]],
        capability: CapabilityDescriptor,
        metadata_validator: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        if capability.kind != "skill":
            raise ValueError("A Skill loader requires a Skill capability descriptor")
        if metadata.name in self._skills:
            raise DuplicateSkillError("Skill alias already registered")
        self._skills[metadata.name] = RegisteredSkill(
            metadata=metadata,
            loader=loader,
            validator=validator,
            capability=capability,
            metadata_validator=metadata_validator,
        )

    async def validate_metadata(self, name: str) -> None:
        registered = self._skills.get(name)
        if registered is None:
            raise SkillNotFoundError("Skill no longer registered")
        guard = registered.metadata_validator or registered.validator
        if guard:
            await guard()

    async def validate(self, name: str) -> None:
        registered = self._skills.get(name)
        if registered is None:
            raise SkillNotFoundError("Skill no longer registered")
        if registered.validator:
            await registered.validator()

    def capabilities(self) -> tuple[CapabilityDescriptor, ...]:
        return tuple(s.capability for s in self._skills.values() if s.capability is not None)

    def capability(self, name: str) -> CapabilityDescriptor:
        """Metadata-only identity for both builtin and plugin Skills."""
        metadata = self.get_metadata(name)
        return self._skills[name].capability or CapabilityDescriptor(
            capability_id=f"skill.{metadata.name}",
            kind="skill",
            name=metadata.name,
            description=metadata.description,
            version=metadata.version,
            tags=metadata.tags,
        )

    def _read_registration(self, skill_directory: str | Path) -> RegisteredSkill:
        directory = Path(skill_directory).resolve()
        metadata = load_skill_metadata(directory)
        if directory.name != metadata.name:
            raise SkillRegistryError(f"Tên thư mục Skill phải trùng metadata.name: {metadata.name}")
        if not (directory / SKILL_INSTRUCTIONS_FILE).is_file():
            raise SkillRegistryError(f"Skill '{metadata.name}' thiếu {SKILL_INSTRUCTIONS_FILE}")
        return RegisteredSkill(metadata=metadata, directory=directory)

    def register(self, skill_directory: str | Path) -> SkillMetadata:
        registered = self._read_registration(skill_directory)
        name = registered.metadata.name
        if name in self._skills:
            raise DuplicateSkillError(f"Skill '{name}' đã được đăng ký")
        self._skills[name] = registered
        return registered.metadata

    def discover(self, root_directory: str | Path) -> tuple[SkillMetadata, ...]:
        """Register immediate child Skill directories without reading instructions."""
        root = Path(root_directory)
        if not root.is_dir():
            raise SkillRegistryError("Thư mục gốc chứa Skill không tồn tại")
        pending: list[RegisteredSkill] = []
        pending_names: set[str] = set()
        for child in sorted(root.iterdir(), key=lambda path: path.name):
            if (
                not child.is_dir()
                or child.name.startswith(".")
                or not (child / SKILL_MANIFEST_FILE).is_file()
            ):
                continue
            registered = self._read_registration(child)
            name = registered.metadata.name
            if name in self._skills or name in pending_names:
                raise DuplicateSkillError(f"Skill '{name}' đã được đăng ký")
            pending.append(registered)
            pending_names.add(name)

        for registered in pending:
            self._skills[registered.metadata.name] = registered
        return tuple(item.metadata for item in pending)

    def get_metadata(self, name: str) -> SkillMetadata:
        try:
            return self._skills[name].metadata
        except KeyError as exc:
            raise SkillNotFoundError(f"Không tìm thấy Skill '{name}'") from exc

    def list_metadata(self) -> tuple[SkillMetadata, ...]:
        return tuple(
            registered.metadata
            for registered in sorted(
                self._skills.values(),
                key=lambda item: item.metadata.name,
            )
        )

    def search(
        self,
        query: str,
        *,
        available_tools: Collection[str],
        limit: int = 3,
        include_unavailable: bool = False,
    ) -> tuple[SkillMatch, ...]:
        if limit < 1:
            raise ValueError("Skill search limit phải lớn hơn hoặc bằng 1")
        normalized_query = _normalize(query)
        if not normalized_query:
            return ()
        query_words = {word for word in normalized_query.split() if word not in _STOP_WORDS}
        if not query_words:
            return ()
        matches: list[SkillMatch] = []
        for registered in self._skills.values():
            metadata = registered.metadata
            score = _score_skill(normalized_query, query_words, metadata)
            if score <= 0:
                continue
            missing_tools = metadata.missing_tools(available_tools)
            if missing_tools and not include_unavailable:
                continue
            matches.append(
                SkillMatch(
                    metadata=metadata,
                    score=score,
                    missing_tools=missing_tools,
                )
            )
        matches.sort(key=lambda item: (-item.score, item.metadata.name))
        return tuple(matches[:limit])

    def load(self, name: str, *, available_tools: Collection[str]) -> SkillSpec:
        """Validate dependencies, then lazily load and cache full instructions."""
        try:
            registered = self._skills[name]
        except KeyError as exc:
            raise SkillNotFoundError(f"Không tìm thấy Skill '{name}'") from exc
        missing_tools = registered.metadata.missing_tools(available_tools)
        if missing_tools:
            raise SkillDependencyError(f"Skill '{name}' thiếu tool: {', '.join(missing_tools)}")
        if name in self._loaded and registered.loader is None:
            return self._loaded[name]

        skill = registered.loader() if registered.loader else load_skill(registered.directory)
        if skill.metadata != registered.metadata:
            raise SkillChangedError(
                f"Metadata của Skill '{name}' đã thay đổi; cần đăng ký lại Registry"
            )
        self._loaded[name] = skill
        return skill

    def unregister(self, name: str) -> RegisteredSkill:
        try:
            registered = self._skills.pop(name)
        except KeyError as exc:
            raise SkillNotFoundError(f"Không tìm thấy Skill '{name}'") from exc
        self._loaded.pop(name, None)
        return registered

    @property
    def loaded_names(self) -> frozenset[str]:
        return frozenset(self._loaded)

    def __contains__(self, name: object) -> bool:
        return name in self._skills

    def __len__(self) -> int:
        return len(self._skills)
