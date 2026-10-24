"""Validate host-reviewed plugin manifests and build a disabled metadata catalog offline."""

import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from agent_core.plugins import CatalogSnapshot, PluginCatalog, PluginManifest, load_plugin_manifest

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST_DIRECTORY = ROOT / "plugins/manifests"


def validate_directory(directory: Path) -> dict:
    paths = sorted(directory.glob("*.json"))
    if not paths:
        raise ValueError("No plugin manifests found in the selected directory")
    manifests = sorted((load_plugin_manifest(path) for path in paths), key=lambda item: item.id)
    catalog = PluginCatalog()
    summaries = []
    ids = set()
    for manifest in manifests:
        if manifest.id in ids:
            raise ValueError("Manifest directory contains duplicate plugin ids")
        ids.add(manifest.id)
        catalog.put(manifest)
        summaries.append(
            {
                "id": manifest.id,
                "version": manifest.version,
                "policy_version": manifest.policy_version,
                "namespace": manifest.namespace,
                "fingerprint": manifest.fingerprint(),
                "servers": len(manifest.servers),
                "tools": sum(len(server.tools) for server in manifest.servers),
                "skills": len(manifest.skills),
                "enabled": False,
            }
        )
    snapshot = catalog.snapshot
    return {
        "schema_version": "1.0",
        "catalog_version": snapshot.catalog_version,
        "revision": snapshot.revision,
        "plugins": summaries,
        "capabilities": len(snapshot.capabilities(include_disabled=True)),
        "enabled_capabilities": len(snapshot.capabilities()),
        "connections_started": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, nargs="?", default=DEFAULT_MANIFEST_DIRECTORY)
    parser.add_argument("--schema-dir", type=Path, help="Export JSON schemas to this directory.")
    args = parser.parse_args()
    try:
        summary = validate_directory(args.directory)
    except ValidationError as exc:
        # Do not print offending payloads: rejected raw secrets must not enter CI logs.
        print(
            json.dumps(
                {
                    "valid": False,
                    "errors": exc.errors(
                        include_input=False, include_context=False, include_url=False
                    ),
                }
            )
        )
        raise SystemExit(1) from None
    except (ValueError, OSError) as exc:
        print(json.dumps({"valid": False, "error_type": type(exc).__name__}))
        raise SystemExit(1) from None
    if args.schema_dir is not None:
        args.schema_dir.mkdir(parents=True, exist_ok=True)
        for name, model in (
            ("plugin-manifest", PluginManifest),
            ("catalog-snapshot", CatalogSnapshot),
        ):
            (args.schema_dir / f"{name}.schema.json").write_text(
                json.dumps(model.model_json_schema(), indent=2) + "\n", encoding="utf-8"
            )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
