"""The only component allowed Docker access. All runtime choices are fixed here."""

import asyncio
import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO
from uuid import UUID

from artifact_runtime.protocol import (
    MAX_MANIFEST_BYTES,
    MAX_TOTAL_BYTES,
    ArtifactRunRequest,
    bundle_header,
    canonical,
    copy_checked,
    unpack_bundle,
)

LABEL = "personal-agent.artifact-runner"
JOB_LABEL = "personal-agent.artifact-job"
CREATED_LABEL = "personal-agent.artifact-created"
CODE_IMAGE = "personal-agent-artifact-code:1"
RENDER_IMAGE = "personal-agent-artifact-render:1"


class RunnerError(ValueError):
    pass


class DockerBroker:
    def __init__(self, root: Path, *, docker="docker"):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.docker = docker
        self.db_path = self.root / "jobs.sqlite3"
        with self.db() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, "
                "status TEXT NOT NULL, created REAL NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS nonces "
                "(nonce TEXT PRIMARY KEY, expires INTEGER NOT NULL)"
            )
        self.capacity = asyncio.Semaphore(2)

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.execute("PRAGMA busy_timeout=10000")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def accept_nonce(self, nonce: str, expires: int) -> None:
        with self.db() as db:
            db.execute("DELETE FROM nonces WHERE expires < ?", (int(time.time()),))
            try:
                db.execute("INSERT INTO nonces VALUES (?,?)", (nonce, expires))
            except sqlite3.IntegrityError as error:
                raise RunnerError("Runner request nonce already used") from error

    async def command(self, *args: str, check=True) -> str:
        process = await asyncio.create_subprocess_exec(
            self.docker,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=20)
        except BaseException:
            if process.returncode is None:
                process.kill()
                await process.wait()
            raise
        if process.returncode:
            if check:
                raise RunnerError(stderr[-4096:].decode("utf-8", "replace"))
            return ""
        return stdout.decode("utf-8", "replace").strip()

    async def resources(self, job_id: UUID) -> list[str]:
        job = str(UUID(str(job_id)))
        containers = await self.command(
            "ps",
            "-aq",
            "--filter",
            f"label={LABEL}=1",
            "--filter",
            f"label={JOB_LABEL}={job}",
        )
        volumes = await self.command(
            "volume",
            "ls",
            "-q",
            "--filter",
            f"label={LABEL}=1",
            "--filter",
            f"label={JOB_LABEL}={job}",
        )
        return containers.split() + volumes.split()

    async def cleanup(self, job_id: UUID) -> None:
        job = str(UUID(str(job_id)))
        for item in await self.resources(job_id):
            is_volume = item.startswith("artifact-input-")
            args = ("volume", "inspect", item) if is_volume else ("inspect", item)
            raw = await self.command(*args, check=False)
            if not raw:
                continue
            metadata = json.loads(raw)[0]
            labels = (
                metadata.get("Labels") if is_volume else metadata.get("Config", {}).get("Labels")
            )
            if (labels or {}).get(LABEL) != "1" or (labels or {}).get(JOB_LABEL) != job:
                raise RunnerError("Refusing to clean a resource without matching ownership labels")
            if is_volume:
                # Containers must be removed before the volume, regardless of list ordering.
                continue
            await self.command("rm", "-f", item)
        name = f"artifact-input-{job}"
        raw = await self.command("volume", "inspect", name, check=False)
        if raw:
            labels = json.loads(raw)[0].get("Labels", {})
            if labels.get(LABEL) == "1" and labels.get(JOB_LABEL) == job:
                await self.command("volume", "rm", name)

    async def recover(self) -> None:
        """Run before accepting requests. Completed output bundles remain replayable."""
        with self.db() as db:
            jobs = db.execute("SELECT id FROM jobs WHERE status = 'running'").fetchall()
        for (job,) in jobs:
            await self.cleanup(UUID(job))
            self.set_status(job, "interrupted")

    async def reap_stale(self) -> None:
        # All resources have an expiry label. A restarted broker also catches orphaned
        # resources when its filesystem transaction was interrupted before registration.
        ids = (await self.command("ps", "-aq", "--filter", f"label={LABEL}=1")).split()
        for identifier in ids:
            raw = await self.command("inspect", identifier, check=False)
            if not raw:
                continue
            labels = json.loads(raw)[0].get("Config", {}).get("Labels", {})
            if time.time() - float(labels.get(CREATED_LABEL, time.time())) > 150:
                await self.cleanup(UUID(labels[JOB_LABEL]))
                self.set_status(labels[JOB_LABEL], "interrupted")
        volumes = (
            await self.command(
                "volume",
                "ls",
                "-q",
                "--filter",
                f"label={LABEL}=1",
            )
        ).split()
        for volume in volumes:
            raw = await self.command("volume", "inspect", volume, check=False)
            if not raw:
                continue
            labels = json.loads(raw)[0].get("Labels", {})
            if time.time() - float(labels.get(CREATED_LABEL, time.time())) > 150:
                await self.cleanup(UUID(labels[JOB_LABEL]))
                self.set_status(labels[JOB_LABEL], "interrupted")
        with self.db() as db:
            expired = db.execute(
                "SELECT id FROM jobs WHERE status <> 'running' AND created < ?",
                (time.time() - 3600,),
            ).fetchall()
        for (job,) in expired:
            root = self.root / str(UUID(job))
            for name in ("result.bundle", "pending.bundle", "intake.bundle", "intake.log"):
                (root / name).unlink(missing_ok=True)

    def set_status(self, job: str, status: str) -> None:
        with self.db() as db:
            db.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, job))

    def mark_cancelled(self, job_id: UUID) -> None:
        # A DELETE may arrive while the POST is still uploading. A durable tombstone
        # makes that late POST harmless even when no running task exists yet.
        with self.db() as db:
            db.execute(
                "INSERT INTO jobs VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE "
                "SET status='cancelled' WHERE jobs.status <> 'completed'",
                (str(job_id), "", "cancelled", time.time()),
            )

    @staticmethod
    def limits(*, render=False, intake=False) -> list[str]:
        memory = "1024m" if render else "512m"
        cpu = 60 if render else 15
        args = [
            "--pull=never",
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true",
            "--cpus=1",
            f"--memory={memory}",
            f"--memory-swap={memory}",
            "--pids-limit=64",
            f"--ulimit=cpu={cpu}:{cpu}",
            "--ulimit=nofile=128:128",
            "--ulimit=fsize=41943040:41943040",
            "--log-driver=none",
            "--tmpfs=/tmp:rw,nosuid,nodev,size=64m,mode=1777",
        ]
        if not intake:
            args += [
                "--user=65532:65532",
                "--tmpfs=/output:rw,nosuid,nodev,size=48m,uid=65532,gid=65532,mode=700",
            ]
        else:
            args += ["--user=0:0"]
        return args

    async def pipe_container(
        self, args: list[str], *, source: Path | None, output: Path, deadline: int
    ):
        process = await asyncio.create_subprocess_exec(
            self.docker,
            *args,
            stdin=asyncio.subprocess.PIPE if source else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        async def feed():
            if source:
                with source.open("rb") as data:
                    while chunk := data.read(65536):
                        process.stdin.write(chunk)
                        await process.stdin.drain()
                process.stdin.close()
                await process.stdin.wait_closed()

        async def drain():
            total = 0
            with output.open("xb") as target:
                while chunk := await process.stdout.read(65536):
                    total += len(chunk)
                    if total > MAX_TOTAL_BYTES + MAX_MANIFEST_BYTES + 4:
                        raise RunnerError("Container output exceeds transport limit")
                    target.write(chunk)

        async def errors():
            retained = bytearray()
            while chunk := await process.stderr.read(65536):
                retained.extend(chunk)
                del retained[:-4096]
            return bytes(retained)

        tasks = [asyncio.create_task(coro()) for coro in (feed, drain, errors)]
        try:
            async with asyncio.timeout(deadline):
                _, _, stderr = await asyncio.gather(*tasks)
                code = await process.wait()
                if code:
                    raise RunnerError(
                        f"Artifact container exited {code}: " + stderr.decode("utf-8", "replace")
                    )
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if process.returncode is None:
                process.kill()
                await process.wait()

    async def run(self, request: ArtifactRunRequest, inputs: tuple[BinaryIO, ...]) -> Path:
        if len(inputs) != len(request.inputs):
            raise RunnerError("Input count mismatch")
        job = str(request.job_id)
        fingerprint = hashlib.sha256(canonical(request.model_dump(mode="json"))).hexdigest()
        root = self.root / job
        result = root / "result.bundle"
        async with self.capacity:
            with self.db() as db:
                row = db.execute(
                    "SELECT fingerprint,status FROM jobs WHERE id = ?", (job,)
                ).fetchone()
                if row:
                    if row[1] == "cancelled":
                        raise RunnerError("Job was cancelled before execution")
                    if row[0] != fingerprint:
                        raise RunnerError("Job ID was already used for another request")
                    if row[1] == "completed" and result.is_file():
                        return result
                    raise RunnerError(f"Job already started ({row[1]}); it will not execute again")
                db.execute(
                    "INSERT INTO jobs VALUES (?,?,?,?)", (job, fingerprint, "running", time.time())
                )
            root.mkdir(mode=0o700, exist_ok=True)
            volume = f"artifact-input-{job}"
            labels = [
                "--label",
                f"{LABEL}=1",
                "--label",
                f"{JOB_LABEL}={job}",
                "--label",
                f"{CREATED_LABEL}={time.time()}",
            ]
            try:
                intake = root / "intake.bundle"
                with intake.open("xb") as target:
                    target.write(bundle_header(request.model_dump(mode="json")))
                    for item, source in zip(request.inputs, inputs, strict=True):
                        copy_checked(source, target, item)
                await self.command("volume", "create", *labels, volume)
                await self.pipe_container(
                    [
                        "run",
                        "-i",
                        "--name",
                        f"artifact-{job}-intake",
                        *labels,
                        *self.limits(intake=True),
                        "--mount",
                        f"type=volume,src={volume},dst=/input",
                        CODE_IMAGE,
                        "python",
                        "-I",
                        "-B",
                        "-m",
                        "artifact_runtime.intake",
                    ],
                    source=intake,
                    output=root / "intake.log",
                    deadline=15,
                )
                render = request.action == "recalculate"
                await self.pipe_container(
                    [
                        "run",
                        "--name",
                        f"artifact-{job}-exec",
                        *labels,
                        *self.limits(render=render),
                        "--mount",
                        f"type=volume,src={volume},dst=/input,readonly",
                        RENDER_IMAGE if render else CODE_IMAGE,
                        "python",
                        "-I",
                        "-B",
                        "-m",
                        "artifact_runtime.execute",
                    ],
                    source=None,
                    output=root / "pending.bundle",
                    deadline=90 if render else 30,
                )
                with (root / "pending.bundle").open("rb") as source:
                    report, validated = unpack_bundle(source, root / "validated")
                if bool(report.files) != (request.action != "inspect"):
                    raise RunnerError("Unexpected output count for runner action")
                for path in validated:
                    path.unlink()
                (root / "validated").rmdir()
                (root / "pending.bundle").replace(result)
                self.set_status(job, "completed")
                return result
            except BaseException:
                self.set_status(job, "failed")
                raise
            finally:
                await asyncio.shield(self.cleanup(request.job_id))
                (root / "intake.bundle").unlink(missing_ok=True)
