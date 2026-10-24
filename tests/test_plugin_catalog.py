import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from pydantic import ValidationError

from agent_core.plugins import (
    CatalogConflictError,
    CatalogSnapshot,
    PluginCatalog,
    PluginManifest,
    PluginNotFoundError,
    load_plugin_manifest,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def manifest():
    return load_plugin_manifest(ROOT / "plugins/manifests/demo.json")


def changed(manifest, **updates):
    return PluginManifest.model_validate({**manifest.model_dump(mode="json"), **updates})


def test_snapshot_is_immutable_and_disabled_by_default(manifest):
    catalog = PluginCatalog()
    empty = catalog.snapshot
    registered = catalog.put(manifest)
    enabled = catalog.set_enabled(manifest.id, True)
    assert empty.revision == 0 and not empty.entries
    assert registered.revision == 1 and not registered.capabilities()
    assert len(registered.capabilities(include_disabled=True)) == 7
    assert enabled.revision == 2 and len(enabled.capabilities()) == 7
    assert not registered.get(manifest.id).enabled
    with pytest.raises(ValidationError, match="frozen"):
        enabled.entries[0].enabled = False
    data = enabled.model_dump(mode="json")
    data["entries"][0]["manifest"]["servers"][0]["tools"][0]["policy"]["requires_approval"] = True
    assert not enabled.entries[0].manifest.servers[0].tools[0].policy.requires_approval


def test_catalog_version_changes_across_disable_enable_even_when_content_returns(manifest):
    catalog = PluginCatalog()
    first = catalog.put(manifest, enabled=True)
    disabled = catalog.set_enabled(manifest.id, False)
    last = catalog.set_enabled(manifest.id, True)
    assert len({snapshot.catalog_version for snapshot in (first, disabled, last)}) == 3
    assert first.catalog_version.rsplit(":", 1)[1] == last.catalog_version.rsplit(":", 1)[1]
    with pytest.raises(CatalogConflictError, match="changed"):
        catalog.remove(manifest.id, expected_catalog_version=first.catalog_version)


def test_semantically_identical_registration_is_a_noop(manifest):
    payload = manifest.model_dump(mode="json")
    payload["tags"].reverse()
    payload["servers"][0]["tools"].reverse()
    for tool in payload["servers"][0]["tools"]:
        tool["policy"]["allowed_roles"].reverse()
    reordered = PluginManifest.model_validate(payload)
    catalog = PluginCatalog()
    first = catalog.put(manifest)
    assert manifest.fingerprint() == reordered.fingerprint()
    assert catalog.put(reordered) is first
    assert catalog.set_enabled(manifest.id, False) is first


def test_content_hash_and_versions_are_deterministic_across_registration_order(manifest):
    other = changed(manifest, id="fixture.other", namespace="other")
    first, second = PluginCatalog(), PluginCatalog()
    for item in (manifest, other):
        first.put(item)
    for item in (other, manifest):
        second.put(item)
    assert first.snapshot.catalog_version == second.snapshot.catalog_version
    assert first.snapshot.capabilities(include_disabled=True) == second.snapshot.capabilities(
        include_disabled=True
    )


def test_failed_namespace_collision_preserves_entire_old_snapshot(manifest):
    catalog = PluginCatalog()
    first = catalog.put(manifest)
    collision = changed(manifest, id="another.plugin")
    with pytest.raises(CatalogConflictError, match="namespace"):
        catalog.put(collision)
    assert catalog.snapshot is first
    with pytest.raises(PluginNotFoundError):
        first.get("another.plugin")


def test_namespace_is_stable_across_release_updates(manifest):
    catalog = PluginCatalog()
    first = catalog.put(manifest)
    with pytest.raises(CatalogConflictError, match="namespace"):
        catalog.put(changed(manifest, version="1.0.1", policy_version="1.0.1", namespace="renamed"))
    assert catalog.snapshot is first


def test_reusing_a_release_with_changed_content_is_rejected_even_after_removal(manifest):
    catalog = PluginCatalog()
    catalog.put(manifest)
    catalog.remove(manifest.id)
    removed = catalog.snapshot
    with pytest.raises(CatalogConflictError, match="immutable"):
        catalog.put(changed(manifest, description="Changed without a new release."))
    assert catalog.snapshot is removed


def test_metadata_update_requires_manifest_version_but_not_policy_version(manifest):
    catalog = PluginCatalog()
    first = catalog.put(manifest)
    updated = changed(manifest, version="1.0.1", description="Updated metadata.")
    assert manifest.policy_fingerprint() == updated.policy_fingerprint()
    second = catalog.put(updated, expected_catalog_version=first.catalog_version)
    assert second.revision == 2 and second.get(manifest.id).manifest.version == "1.0.1"


def test_policy_edit_also_requires_a_new_policy_version(manifest):
    catalog = PluginCatalog()
    first = catalog.put(manifest)
    payload = manifest.model_dump(mode="json")
    payload["version"] = "1.0.1"
    payload["servers"][0]["tools"][0]["policy"]["requires_approval"] = True
    with pytest.raises(CatalogConflictError, match="policy_version"):
        catalog.put(PluginManifest.model_validate(payload))
    assert catalog.snapshot is first
    payload["policy_version"] = "1.0.1"
    assert catalog.put(PluginManifest.model_validate(payload)).revision == 2


def test_snapshot_wire_roundtrip_detects_stale_hash_and_revalidates_nested_policy(manifest):
    catalog = PluginCatalog()
    snapshot = catalog.put(manifest)
    restored = CatalogSnapshot.model_validate_json(snapshot.model_dump_json())
    assert PluginCatalog(restored).snapshot == snapshot
    schema = json.loads((ROOT / "docs/plugins/schemas/catalog-snapshot.schema.json").read_text())
    assert schema == CatalogSnapshot.model_json_schema()
    data = snapshot.model_dump(mode="json")
    data["entries"][0]["enabled"] = True
    with pytest.raises(ValidationError, match="does not match"):
        CatalogSnapshot.model_validate(data)
    forged = manifest.model_copy(update={"max_concurrency": 0})
    with pytest.raises(ValidationError):
        catalog.put(forged)
    assert catalog.snapshot is snapshot


def test_mutation_never_succeeds_for_an_unknown_plugin(manifest):
    catalog = PluginCatalog()
    with pytest.raises(PluginNotFoundError):
        catalog.set_enabled(manifest.id, True)
    with pytest.raises(PluginNotFoundError):
        catalog.remove(manifest.id)
    assert catalog.snapshot.revision == 0


def test_concurrent_compare_and_swap_has_exactly_one_winner(manifest):
    catalog = PluginCatalog()
    version = catalog.snapshot.catalog_version
    barrier = Barrier(2)

    def update(namespace):
        item = changed(manifest, id=namespace, namespace=namespace)
        barrier.wait(timeout=5)
        try:
            catalog.put(item, expected_catalog_version=version)
            return True
        except CatalogConflictError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(update, ("first", "second")))
    assert sorted(results) == [False, True]
    assert catalog.snapshot.revision == 1 and len(catalog.snapshot.entries) == 1


def test_revision_overflow_cannot_publish_a_partial_update(manifest):
    catalog = PluginCatalog()
    original = catalog.put(manifest)
    revision = 2_147_483_647
    fingerprint = original.catalog_version.rsplit(":", 1)[1]
    last = CatalogSnapshot(
        revision=revision, catalog_version=f"pc1:{revision}:{fingerprint}", entries=original.entries
    )
    assert len(last.catalog_version) <= 80
    catalog = PluginCatalog(last)
    with pytest.raises(ValidationError):
        catalog.set_enabled(manifest.id, True)
    assert catalog.snapshot is not original and catalog.snapshot == last
