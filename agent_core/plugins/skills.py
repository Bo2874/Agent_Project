"""Host-reviewed, content-pinned plugin Skill sources. Never resolve remote instructions."""

import hashlib
import stat
from dataclasses import dataclass, replace
from pathlib import Path

from agent_core.plugins.manifest import PluginManifest, PluginSkill
from agent_core.plugins.namespace import tool_alias
from agent_core.skills.spec import (
    MAX_INSTRUCTIONS_CHARACTERS,
    MAX_MANIFEST_BYTES,
    SkillMetadata,
    SkillSpec,
    load_skill_metadata,
)
from agent_core.tools.types import ToolAuthorizationError


def skill_alias(namespace: str, name: str) -> str:
    return f"{namespace}__skill__{name}"


@dataclass(frozen=True, slots=True)
class PluginSkillSource:
    anchor: Path
    relative_root: str
    manifest: PluginManifest
    skill: PluginSkill
    instructions_sha256: str

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name=skill_alias(self.manifest.namespace, self.skill.name),
            version=self.skill.version,
            description=self.skill.description,
            tags=self.skill.tags,
            required_tools=tuple(
                sorted(
                    tool_alias(self.manifest.namespace, ref.server_id, ref.tool_name)
                    for ref in self.skill.required_tools
                )
            ),
        )

    def _path(self, filename: str) -> Path:
        anchor = self.anchor.absolute()
        directory = anchor / self.relative_root / self.skill.relative_directory
        path = directory / filename
        if anchor.resolve() != anchor or not path.resolve().is_relative_to(anchor):
            raise ValueError("Plugin package path escaped its reviewed root")
        for part in (path, *path.parents):
            if part.is_symlink():
                raise ValueError("Symbolic links are not accepted in Skill packages")
            if part == anchor:
                break
        return path

    def _read(self, filename: str, maximum: int) -> bytes:
        path = self._path(filename)
        expected = path.resolve()
        with path.open("rb") as stream:
            import os

            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
                raise ValueError("Invalid package file")
            value = stream.read(maximum + 1)
        if len(value) > maximum or self._path(filename).resolve() != expected:
            raise ValueError("Package changed while reading")
        return value

    def check_metadata(self) -> None:
        try:
            before = self._read("skill.json", MAX_MANIFEST_BYTES)
            metadata = load_skill_metadata(self._path("skill.json").parent)
            after = self._read("skill.json", MAX_MANIFEST_BYTES)
            expected = replace(self.metadata, name=self.skill.name)
            if before != after or metadata != expected:
                raise ValueError("Package metadata disagrees with the reviewed manifest")
        except (OSError, ValueError, RuntimeError):
            raise ToolAuthorizationError("plugin_package_changed") from None

    def load(self) -> SkillSpec:
        self.check_metadata()
        try:
            raw = self._read("instructions.md", MAX_INSTRUCTIONS_CHARACTERS * 4)
            if hashlib.sha256(raw).hexdigest() != self.instructions_sha256:
                raise ValueError("Instruction digest mismatch")
            return SkillSpec(
                metadata=self.metadata,
                instructions=raw.decode("utf-8"),
                requires_approval=self.skill.policy.requires_approval,
            )
        except (OSError, ValueError):
            raise ToolAuthorizationError("plugin_package_changed") from None

    def check(self) -> None:
        self.load()  # Revalidate even if a run already holds a cached SkillSpec.
