"""Host-only package bindings: exact manifest fingerprint and instruction content hashes."""

import hashlib
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from agent_core.contracts.base import Identifier
from agent_core.plugins.skills import PluginSkillSource
from backend.core.config import PROJECT_ROOT
from backend.core.mcp_config import HostConfig, _object

Digest = Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$")]


class PackageBinding(HostConfig):
    plugin_id: Identifier
    manifest_fingerprint: Digest
    relative_root: Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9_/-]{1,200}$")]
    instruction_hashes: dict[str, Digest] = Field(max_length=64)

    @model_validator(mode="after")
    def paths(self):
        if self.relative_root.startswith("/") or any(
            part in {"", ".", ".."} for part in self.relative_root.split("/")
        ):
            raise ValueError("Invalid package relative root")
        return self


class PackageConfiguration(HostConfig):
    schema_version: Literal["1.0"]
    packages: Annotated[tuple[PackageBinding, ...], Field(max_length=64)] = ()

    @model_validator(mode="after")
    def identities(self):
        if len({p.plugin_id for p in self.packages}) != len(self.packages):
            raise ValueError("Duplicate package binding")
        return self


class ReviewedPackages:
    def __init__(self, path: Path):
        self.path = path if path.is_absolute() else PROJECT_ROOT / path
        raw = self._read()
        self.fingerprint = hashlib.sha256(raw).hexdigest()
        self.config = PackageConfiguration.model_validate(
            json.loads(raw, object_pairs_hook=_object)
        )

    def _read(self) -> bytes:
        with self.path.open("rb") as stream:
            raw = stream.read(256 * 1024 + 1)
        if len(raw) > 256 * 1024:
            raise ValueError("Package config exceeds size limit")
        return raw

    def current(self) -> bool:
        return hashlib.sha256(self._read()).hexdigest() == self.fingerprint

    def sources(self, manifest):
        binding = next((p for p in self.config.packages if p.plugin_id == manifest.id), None)
        if binding is None:
            return ()
        if binding.manifest_fingerprint != manifest.fingerprint() or set(
            binding.instruction_hashes
        ) != {skill.name for skill in manifest.skills}:
            raise ValueError("Package pin does not match the current plugin release")
        return tuple(
            PluginSkillSource(
                anchor=PROJECT_ROOT / "plugins/packages",
                relative_root=binding.relative_root,
                manifest=manifest,
                skill=skill,
                instructions_sha256=binding.instruction_hashes[skill.name],
            )
            for skill in manifest.skills
        )
