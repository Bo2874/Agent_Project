from pathlib import Path

from artifact_runtime.containers import DockerBroker
from backend.dependencies_tools import get_tool_registry


def test_execution_profile_keeps_isolation_and_resource_boundaries():
    options = DockerBroker.limits()
    assert {
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--cpus=1",
        "--security-opt=no-new-privileges:true",
        "--user=65532:65532",
        "--memory=1024m",
        "--memory-swap=1024m",
        "--pids-limit=64",
        "--ulimit=cpu=90:90",
    } <= set(options)
    assert not {"--privileged", "--env-file", "--volume"} & set(options)


def test_shared_image_has_no_project_data_or_secrets():
    dockerfile = (
        Path(__file__).resolve().parents[1] / "infra/docker/python-runtime/Dockerfile"
    ).read_text()
    assert "COPY ." not in dockerfile
    assert "COPY artifact_runtime/" in dockerfile
    assert not any(
        secret in dockerfile for secret in ("DB_PASSWORD", "AUTH_SECRET_KEY", "DEEPSEEK_API_KEY")
    )
    assert "USER 65532:65532" in dockerfile


def test_production_python_does_not_retry_or_wait_for_approval():
    registry = get_tool_registry()
    entry = registry.get("run_python_code")
    assert registry.is_allowlisted("run_python_code")
    assert not entry.policy.requires_approval
    assert not entry.policy.idempotent
    assert entry.policy.max_retries == 0
