"""Write a credential-free machine/dependency snapshot for the Day 76 baseline."""

from __future__ import annotations

import hashlib
import importlib.metadata as metadata
import json
import platform
import subprocess
import sys
import tomllib
from pathlib import Path

import httpx
from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from backend.core.config import get_settings  # noqa: E402

project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
settings = get_settings()
requirements = []
for raw in project["project"]["dependencies"] + project["project"]["optional-dependencies"]["dev"]:
    requirement = Requirement(raw)
    installed = metadata.version(requirement.name)
    requirements.append(
        {
            "name": requirement.name,
            "required": str(requirement.specifier),
            "installed": installed,
            "satisfies": installed in requirement.specifier,
        }
    )

health = {}
with httpx.Client(timeout=5, trust_env=False) as client:
    for route in ["/health/live", "/health/ready"]:
        try:
            response = client.get("http://127.0.0.1:8000" + route)
            health[route] = {"status_code": response.status_code, "body": response.json()}
        except Exception as exc:
            health[route] = {"error_type": type(exc).__name__}

repo = subprocess.run(
    ["git", "rev-parse", "--show-toplevel"], cwd=ROOT, capture_output=True, text=True, check=False
)
tracked = sorted(
    p
    for folder in [
        "agent_core",
        "backend",
        "migrations",
        "scripts",
        "skills",
        "frontend",
        "tests",
        "evals",
    ]
    for p in (ROOT / folder).rglob("*")
    if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
)
hashes = {
    p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in tracked
}
for name in ["pyproject.toml", "alembic.ini"]:
    hashes[name] = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
digest = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
snapshot = {
    "python": sys.version,
    "executable": sys.executable,
    "platform": platform.platform(),
    "git_repository": repo.returncode == 0,
    "git_root": repo.stdout.strip() or None,
    "requirements": requirements,
    "installed_distributions": dict(
        sorted(
            (d.metadata["Name"], d.version) for d in metadata.distributions() if d.metadata["Name"]
        )
    ),
    "lock_files_present": [
        p
        for p in ["uv.lock", "poetry.lock", "requirements.lock", "requirements.txt"]
        if (ROOT / p).exists()
    ],
    "source_fingerprint_sha256": digest,
    "source_file_sha256": hashes,
    "configuration": {
        "app_env": settings.app_env,
        "model": settings.deepseek_model,
        "llm_key_configured": bool(settings.deepseek_api_key),
        "langsmith_enabled": settings.langsmith_tracing_enabled,
        "mcp_demo_enabled": settings.mcp_demo_enabled,
        "python_sandbox_enabled": settings.python_sandbox_enabled,
        "auth_cookie_secure": settings.auth_cookie_secure,
    },
    "health_at_inspection": health,
}
target = Path(__file__).with_name("environment-snapshot.json")
target.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(
    json.dumps(
        {
            "snapshot": target.name,
            "source_fingerprint_sha256": digest,
            "direct_requirements_satisfied": all(r["satisfies"] for r in requirements),
            "dependency_count": len(requirements),
            "git_repository": snapshot["git_repository"],
            "lock_files_present": snapshot["lock_files_present"],
            "configuration": snapshot["configuration"],
            "health": health,
        },
        ensure_ascii=False,
        indent=2,
    )
)
