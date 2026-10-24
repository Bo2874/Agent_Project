"""Synthetic reviewed Skill package shared by offline unit/integration probes."""

import hashlib
import json
from pathlib import Path

from agent_core.plugins import PluginManifest


def make_manifest(plugin_id="fixture.skills", namespace="fixture", version="1.0.0"):
    policy = {
        "allowed_roles": ["user", "admin"],
        "side_effect": "read_only",
        "requires_approval": False,
    }
    return PluginManifest.model_validate(
        {
            "id": plugin_id,
            "namespace": namespace,
            "version": version,
            "policy_version": version,
            "name": "Plugin Skill fixture",
            "description": "Offline plugin report fixture",
            "servers": [
                {
                    "server_id": "main",
                    "transport": "stdio",
                    "release": "0.1.0",
                    "connection_profile_ref": "fixture.stdio",
                    "tools": [
                        {
                            "name": "echo",
                            "remote_name": "echo",
                            "description": "Echo synthetic report",
                            "policy": {**policy, "idempotent": True, "max_retries": 1},
                        },
                    ],
                }
            ],
            "skills": [
                {
                    "name": "brief",
                    "version": version,
                    "description": "Create a plugin report brief",
                    "tags": ["pluginreport"],
                    "relative_directory": "skills/brief",
                    "required_tools": [{"server_id": "main", "tool_name": "echo"}],
                    "policy": policy,
                }
            ],
        }
    )


def write_package(
    root: Path, manifest, *, text="Use the required tool for a synthetic plugin report."
):
    directory = root / "plugins/packages/fixture/skills/brief"
    directory.mkdir(parents=True, exist_ok=True)
    metadata = {
        "schema_version": 1,
        "metadata": {
            "name": "brief",
            "version": manifest.skills[0].version,
            "description": manifest.skills[0].description,
            "tags": list(manifest.skills[0].tags),
        },
        "required_tools": [f"{manifest.namespace}__main__echo"],
    }
    (directory / "skill.json").write_text(json.dumps(metadata), encoding="utf-8")
    instructions = directory / "instructions.md"
    instructions.write_text(text, encoding="utf-8")
    config = root / "packages.json"
    config.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "packages": [
                    {
                        "plugin_id": manifest.id,
                        "manifest_fingerprint": manifest.fingerprint(),
                        "relative_root": "fixture",
                        "instruction_hashes": {
                            "brief": hashlib.sha256(instructions.read_bytes()).hexdigest()
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return config, directory
