"""Atomic, immutable plugin metadata snapshots. No connections, grants or executable tools."""

import hashlib
import json
from threading import RLock
from typing import Annotated, Self

from pydantic import Field, StrictBool, model_validator

from agent_core.contracts import CapabilityDescriptor
from agent_core.contracts.base import ContractModel, Version, require_unique
from agent_core.plugins.manifest import PluginManifest

CatalogRevision = Annotated[int, Field(strict=True, ge=0, le=2_147_483_647)]


class CatalogConflictError(ValueError):
    """Duplicate identity, immutable release mismatch or stale compare-and-swap version."""


class PluginNotFoundError(LookupError):
    pass


class CatalogEntry(ContractModel):
    manifest: PluginManifest
    enabled: StrictBool = False


def _content_hash(entries: tuple[CatalogEntry, ...]) -> str:
    payload = [
        {"manifest": entry.manifest.canonical_data(), "enabled": entry.enabled}
        for entry in sorted(entries, key=lambda item: item.manifest.id)
    ]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class CatalogSnapshot(ContractModel):
    revision: CatalogRevision
    catalog_version: Version
    entries: tuple[CatalogEntry, ...] = ()

    @classmethod
    def build(cls, entries: tuple[CatalogEntry, ...], revision: int) -> Self:
        ordered = tuple(sorted(entries, key=lambda item: item.manifest.id))
        return cls(
            revision=revision,
            catalog_version=f"pc1:{revision}:{_content_hash(ordered)}",
            entries=ordered,
        )

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        require_unique(tuple(entry.manifest.id for entry in self.entries), "catalog.plugin_id")
        require_unique(
            tuple(entry.manifest.namespace for entry in self.entries), "catalog.namespace"
        )
        if self.catalog_version != f"pc1:{self.revision}:{_content_hash(self.entries)}":
            raise ValueError("Catalog version does not match revision and manifest content")
        return self

    def get(self, plugin_id: str) -> CatalogEntry:
        for entry in self.entries:
            if entry.manifest.id == plugin_id:
                return entry
        raise PluginNotFoundError("Plugin is not registered in this catalog snapshot")

    def capabilities(self, *, include_disabled: bool = False) -> tuple[CapabilityDescriptor, ...]:
        """Host metadata only. Backend must still filter grants, connection state and role."""
        return tuple(
            sorted(
                (
                    cap
                    for entry in self.entries
                    if entry.enabled or include_disabled
                    for cap in entry.manifest.capabilities()
                ),
                key=lambda cap: cap.capability_id,
            )
        )


def _snapshot(entries: tuple[CatalogEntry, ...], revision: int) -> CatalogSnapshot:
    return CatalogSnapshot.build(entries, revision)


class PluginCatalog:
    def __init__(self, snapshot: CatalogSnapshot | None = None) -> None:
        self._lock = RLock()
        self._snapshot = (
            CatalogSnapshot.model_validate(snapshot) if snapshot is not None else _snapshot((), 0)
        )
        self._releases = {
            (entry.manifest.id, entry.manifest.version): entry.manifest.fingerprint()
            for entry in self._snapshot.entries
        }
        self._policy_releases = {
            (entry.manifest.id, entry.manifest.policy_version): entry.manifest.policy_fingerprint()
            for entry in self._snapshot.entries
        }

    @property
    def snapshot(self) -> CatalogSnapshot:
        return self._snapshot

    def _check_version(self, expected_catalog_version: str | None) -> None:
        if (
            expected_catalog_version is not None
            and expected_catalog_version != self._snapshot.catalog_version
        ):
            raise CatalogConflictError("Catalog has changed; refresh the snapshot before updating")

    def _publish(self, entries: tuple[CatalogEntry, ...]) -> CatalogSnapshot:
        candidate = _snapshot(entries, self._snapshot.revision + 1)
        # Validation, collision checks and hashing all complete before one pointer is replaced.
        self._snapshot = candidate
        return candidate

    def put(
        self,
        manifest: PluginManifest,
        *,
        enabled: bool = False,
        expected_catalog_version: str | None = None,
    ) -> CatalogSnapshot:
        """Register/replace a host-reviewed release; no user-level authorization is implied."""
        validated = PluginManifest.model_validate(manifest)
        candidate = CatalogEntry(manifest=validated, enabled=enabled)
        with self._lock:
            self._check_version(expected_catalog_version)
            key = (validated.id, validated.version)
            fingerprint = validated.fingerprint()
            known = self._releases.get(key)
            if known is not None and known != fingerprint:
                raise CatalogConflictError(
                    "Published manifest versions are immutable; use a new version"
                )
            policy_key = (validated.id, validated.policy_version)
            policy_fingerprint = validated.policy_fingerprint()
            if self._policy_releases.get(policy_key, policy_fingerprint) != policy_fingerprint:
                raise CatalogConflictError("Policy changed; use a new policy_version")
            if any(
                entry.manifest.id == validated.id
                and entry.manifest.namespace != validated.namespace
                for entry in self._snapshot.entries
            ):
                raise CatalogConflictError("An existing plugin namespace cannot be renamed")
            entries = tuple(
                entry for entry in self._snapshot.entries if entry.manifest.id != validated.id
            )
            if any(entry.manifest.namespace == validated.namespace for entry in entries):
                raise CatalogConflictError("Plugin namespace is already owned by another plugin")
            if any(
                entry.manifest.id == validated.id
                and entry.enabled == enabled
                and entry.manifest.fingerprint() == fingerprint
                for entry in self._snapshot.entries
            ):
                return self._snapshot
            published = self._publish((*entries, candidate))
            self._releases[key] = fingerprint
            self._policy_releases[policy_key] = policy_fingerprint
            return published

    def set_enabled(
        self, plugin_id: str, enabled: bool, *, expected_catalog_version: str | None = None
    ) -> CatalogSnapshot:
        with self._lock:
            self._check_version(expected_catalog_version)
            old = self._snapshot.get(plugin_id)
            candidate = CatalogEntry(manifest=old.manifest, enabled=enabled)
            if candidate == old:
                return self._snapshot
            return self._publish(
                tuple(
                    candidate if entry.manifest.id == plugin_id else entry
                    for entry in self._snapshot.entries
                )
            )

    def remove(
        self, plugin_id: str, *, expected_catalog_version: str | None = None
    ) -> CatalogSnapshot:
        with self._lock:
            self._check_version(expected_catalog_version)
            self._snapshot.get(plugin_id)
            return self._publish(
                tuple(entry for entry in self._snapshot.entries if entry.manifest.id != plugin_id)
            )
