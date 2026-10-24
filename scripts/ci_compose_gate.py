"""Run the offline migration/integration CI gate on fresh, isolated Docker projects."""

import argparse
import json
import os
import re
import socket
import subprocess
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


class ComposeGate:
    def __init__(self, project: str, output: Path, *, cold_build: bool = False) -> None:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,45}", project):
            raise ValueError("Use a lowercase project name of at most 46 characters")
        self.project = project
        self.output = output.resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        # Do not inherit any dev provider keys, host port or image tags into an offline gate.
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("DEV_")}
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        self.env.update(
            DEV_HTTP_PORT=str(port), DEV_IMAGE_PREFIX=f"{project}-image", DEV_IMAGE_TAG="ci"
        )
        self.cold_build = cold_build

    def run(
        self,
        name: str,
        args: list[str],
        *,
        check: bool = True,
        stdin: str | None = None,
        timeout: int = 180,
    ) -> str:
        print(f"CI gate: {name}", flush=True)
        result = subprocess.run(
            args,
            cwd=ROOT,
            env=self.env,
            input=stdin,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
        )
        (self.output / f"{name}.stdout.log").write_text(result.stdout, encoding="utf-8")
        (self.output / f"{name}.stderr.log").write_text(result.stderr, encoding="utf-8")
        if check and result.returncode:
            raise RuntimeError(f"{name} exited {result.returncode}; see its logs in {self.output}")
        return result.stdout

    def compose(self, *, negative: bool = False) -> list[str]:
        args = [
            "docker",
            "compose",
            "--env-file",
            str(ROOT / ".env.docker.example"),
            "--project-name",
            self.project + ("-negative" if negative else ""),
            "-f",
            str(ROOT / "compose.yaml"),
        ]
        if negative:
            args += ["-f", str(self.output / "migration-failure.compose.yaml")]
        return args

    def fresh(self, project: str) -> None:
        containers = self.run(
            f"preflight-{project}-containers",
            ["docker", "ps", "-a", "-q", "--filter", f"label=com.docker.compose.project={project}"],
        )
        volumes = self.run(
            f"preflight-{project}-volumes",
            [
                "docker",
                "volume",
                "ls",
                "-q",
                "--filter",
                f"label=com.docker.compose.project={project}",
            ],
        )
        if containers.strip() or volumes.strip():
            raise RuntimeError(f"Project {project} already has data; choose a fresh project name")

    def state(self, name: str, *, negative: bool = False) -> dict:
        raw = self.run(name, self.compose(negative=negative) + ["ps", "-a", "--format", "json"])
        rows = (
            json.loads(raw)
            if raw.lstrip().startswith("[")
            else [json.loads(line) for line in raw.splitlines() if line.strip()]
        )
        return {row["Service"]: row for row in rows}

    def exec_app(self, name: str, *command: str) -> str:
        return self.run(name, self.compose() + ["exec", "-T", "app", *command])

    def probe(self, name: str, filename: str, *args: str) -> dict:
        code = (ROOT / "infra/ci/probes" / filename).read_text(encoding="utf-8")
        raw = self.run(
            name, self.compose() + ["exec", "-T", "app", "python", "-", *args], stdin=code
        )
        return json.loads(raw)

    def check(self) -> dict:
        if (
            self.run("docker-platform", ["docker", "info", "--format", "{{.OSType}}"]).strip()
            != "linux"
        ):
            raise RuntimeError("The Compose CI gate requires Linux containers")
        self.run("compose-version", ["docker", "compose", "version"])
        self.fresh(self.project)
        self.fresh(self.project + "-negative")
        override = self.output / "migration-failure.compose.yaml"
        override.write_text(
            'services:\n  migrate:\n    command: [python, -c, "raise SystemExit(17)"]\n',
            encoding="utf-8",
        )
        summary = {"status": "failed", "project": self.project, "llm_provider_calls": 0}
        try:
            build = self.compose() + ["--progress", "plain", "build", "--pull"]
            if self.cold_build:
                build.append("--no-cache")
            self.run("build", build, timeout=900)
            self.run(
                "startup",
                self.compose() + ["up", "-d", "--no-build", "--wait", "--wait-timeout", "180"],
                timeout=240,
            )
            state = self.state("startup-state")
            if state["migrate"]["ExitCode"] != 0 or any(
                state[s]["Health"] != "healthy" for s in ("app", "worker", "postgres")
            ):
                raise RuntimeError("Fresh startup is not healthy")
            self.exec_app("alembic-check", "alembic", "check")
            plugins = json.loads(
                self.exec_app(
                    "plugin-manifests", "python", "-m", "scripts.validate_plugin_manifests"
                )
            )
            if plugins["connections_started"] != 0 or plugins["enabled_capabilities"] != 0:
                raise RuntimeError(
                    "Plugin manifest validation must not activate runtime capabilities"
                )
            self.exec_app("migration-idempotent", "python", "-m", "scripts.migrate_database")
            self.exec_app("alembic-check-after", "alembic", "check")
            self.exec_app("routing-runtime", "python", "-m", "scripts.check_routing_runtime")
            plugin_migration = json.loads(
                self.exec_app("plugin-migration", "python", "-m", "scripts.check_plugin_migration")
            )
            runtime = json.loads(
                self.exec_app("runtime", "python", "-m", "scripts.check_compose_runtime")
            )
            self.exec_app("checkpoint", "python", "-m", "scripts.check_langgraph_checkpoint")
            self.exec_app(
                "queue-reliability", "python", "-m", "scripts.check_background_queue_reliability"
            )
            e2e = json.loads(
                self.exec_app(
                    "http-e2e",
                    "python",
                    "-m",
                    "scripts.run_e2e_smoke",
                    "--base-url",
                    "http://app:8000",
                )
            )
            memory = self.probe("memory-api", "memory.py")
            plugin_manager = json.loads(
                self.exec_app("plugin-manager", "python", "-m", "scripts.check_plugin_manager")
            )
            http_plugins = json.loads(
                self.exec_app("http-plugins", "python", "-m", "scripts.check_http_plugins")
            )
            capabilities = json.loads(
                self.exec_app(
                    "capability-catalog", "python", "-m", "scripts.check_capability_catalog"
                )
            )
            credentials = json.loads(
                self.exec_app(
                    "plugin-credentials",
                    "env",
                    "V2_ENABLED=true",
                    "V2_PLUGINS_ENABLED=true",
                    "python",
                    "-m",
                    "scripts.check_plugin_credentials",
                )
            )
            plugin_skills = json.loads(
                self.exec_app(
                    "plugin-skills",
                    "env",
                    "V2_ENABLED=true",
                    "V2_PLUGINS_ENABLED=true",
                    "python",
                    "-m",
                    "scripts.check_plugin_skills",
                )
            )
            plugin_api = json.loads(
                self.exec_app(
                    "plugin-api",
                    "env",
                    "V2_ENABLED=true",
                    "V2_PLUGINS_ENABLED=true",
                    "python",
                    "-m",
                    "scripts.check_plugin_api",
                )
            )
            query_routing = json.loads(
                self.exec_app(
                    "query-routing",
                    "env",
                    "V2_ENABLED=true",
                    "V2_ROUTING_ENABLED=true",
                    "V2_PLUGINS_ENABLED=false",
                    "python",
                    "-m",
                    "scripts.check_query_routing",
                )
            )
            before = self.probe("persistence-before", "persistence.py", "seed")
            capability_shortlist = json.loads(
                self.exec_app(
                    "capability-shortlist",
                    "env",
                    "V2_ENABLED=true",
                    "V2_ROUTING_ENABLED=true",
                    "V2_PLUGINS_ENABLED=false",
                    "python",
                    "-m",
                    "scripts.check_capability_shortlist",
                )
            )
            self.run("persistence-down", self.compose() + ["down"])
            self.run(
                "persistence-up",
                self.compose() + ["up", "-d", "--no-build", "--wait", "--wait-timeout", "180"],
                timeout=240,
            )
            after = self.probe("persistence-after", "persistence.py", "check")
            if before["job_id"] != after["job_id"]:
                raise RuntimeError("Database sentinel changed across container recreation")
            self.probe("persistence-cleanup", "persistence.py", "cleanup")
            packages = json.loads(
                self.exec_app(
                    "packages",
                    "python",
                    "-c",
                    "import importlib.metadata as m,json,platform; "
                    "print(json.dumps({'python':platform.python_version(),"
                    "'packages':dict(sorted((d.metadata['Name'],d.version) "
                    "for d in m.distributions()))}))",
                )
            )
            # This up is expected to fail. Runtime state proves it was the migration gate.
            self.run(
                "migration-failure",
                self.compose(negative=True)
                + ["up", "-d", "--no-build", "--wait", "--wait-timeout", "90"],
                check=False,
            )
            negative = self.state("migration-failure-state", negative=True)
            if negative["migrate"]["ExitCode"] != 17 or any(
                negative[s]["State"] != "created" for s in ("app", "worker")
            ):
                raise RuntimeError("Failed migration did not block app/worker startup")
            summary.update(
                status="passed",
                runtime=runtime,
                http_e2e=e2e,
                memory_api=memory,
                persistence=after,
                migration_failure_gate=True,
                plugin_manifests=plugins,
                plugin_api=plugin_api,
                plugin_manager=plugin_manager,
                http_plugins=http_plugins,
                capability_catalog=capabilities,
                plugin_credentials=credentials,
                plugin_skills=plugin_skills,
                query_routing=query_routing,
                capability_shortlist=capability_shortlist,
                plugin_migration=plugin_migration,
                installed=packages,
                healthy_state=self.state("final-state"),
            )
        finally:
            # Never request config/env dumps. down preserves all named data volumes.
            for negative in (False, True):
                label = "negative" if negative else "main"
                self.run(
                    f"{label}-logs",
                    self.compose(negative=negative) + ["logs", "--no-color", "--tail", "300"],
                    check=False,
                )
                self.run(f"{label}-down", self.compose(negative=negative) + ["down"])
            summary["volumes_retained"] = True
            (self.output / "summary.json").write_text(
                json.dumps(summary, indent=2) + "\n", encoding="utf-8"
            )
        return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-name", default=f"agent-ci-{uuid4().hex[:12]}")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "reports/ci/compose")
    parser.add_argument("--cold-build", action="store_true")
    args = parser.parse_args()
    ComposeGate(args.project_name, args.output_dir, cold_build=args.cold_build).check()
    print("FOUNDATION_COMPOSE_GATE_PASSED", flush=True)


if __name__ == "__main__":
    main()
