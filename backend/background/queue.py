import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from backend.core.redaction import redact_text
from backend.database import SessionFactory
from backend.models import BackgroundJob

MAX_JOB_JSON_BYTES = 64 * 1024
MAX_JOB_ATTEMPTS = 5
_QUEUE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
_JOB_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,79}$")
_WORKER_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}$")
_IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}$")


class BackgroundQueueError(RuntimeError):
    pass


class BackgroundJobOwnershipError(BackgroundQueueError):
    pass


class BackgroundIdempotencyConflictError(BackgroundQueueError):
    pass


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    id: UUID
    queue_name: str
    job_type: str
    payload: dict[str, Any]
    worker_id: str
    started_at: datetime
    attempt_count: int
    max_attempts: int


def _canonical_json(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _validate_json_object(value: dict[str, Any], field_name: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} phải là object")
    try:
        encoded = _canonical_json(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} phải tuần tự hóa được thành JSON") from exc
    if len(encoded) > MAX_JOB_JSON_BYTES:
        raise ValueError(f"{field_name} vượt 64 KiB")


def _idempotency_fingerprint(
    *,
    job_type: str,
    payload: dict[str, Any],
    priority: int,
    max_attempts: int,
    agent_run_id: UUID | None,
) -> str:
    content = {
        "agent_run_id": str(agent_run_id) if agent_run_id else None,
        "job_type": job_type,
        "max_attempts": max_attempts,
        "payload": payload,
        "priority": priority,
    }
    return hashlib.sha256(_canonical_json(content)).hexdigest()


def _validate_lease_seconds(lease_seconds: float) -> None:
    if isinstance(lease_seconds, bool) or not 0 < lease_seconds <= 3600:
        raise ValueError("lease_seconds phải từ trên 0 đến 3600")


class PostgresJobQueue:
    """Durable multi-consumer queue backed by PostgreSQL row locks."""

    @staticmethod
    def build_claim_statement(*, queue_name: str, now: datetime):
        return (
            select(BackgroundJob)
            .where(
                BackgroundJob.queue_name == queue_name,
                BackgroundJob.status == "queued",
                BackgroundJob.available_at <= now,
            )
            .order_by(BackgroundJob.priority.desc(), BackgroundJob.created_at, BackgroundJob.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )

    async def enqueue(
        self,
        *,
        job_type: str,
        payload: dict[str, Any] | None = None,
        queue_name: str = "default",
        priority: int = 0,
        available_at: datetime | None = None,
        user_id: UUID | None = None,
        agent_run_id: UUID | None = None,
        max_attempts: int = 1,
        idempotency_key: str | None = None,
    ) -> BackgroundJob:
        if not _JOB_TYPE_PATTERN.fullmatch(job_type):
            raise ValueError("job_type không hợp lệ")
        if not _QUEUE_NAME_PATTERN.fullmatch(queue_name):
            raise ValueError("queue_name không hợp lệ")
        if (
            isinstance(priority, bool)
            or not isinstance(priority, int)
            or not -100 <= priority <= 100
        ):
            raise ValueError("priority phải từ -100 đến 100")
        if (
            isinstance(max_attempts, bool)
            or not isinstance(max_attempts, int)
            or not 1 <= max_attempts <= MAX_JOB_ATTEMPTS
        ):
            raise ValueError(f"max_attempts phải từ 1 đến {MAX_JOB_ATTEMPTS}")
        if idempotency_key is not None and not _IDEMPOTENCY_KEY_PATTERN.fullmatch(idempotency_key):
            raise ValueError("idempotency_key không hợp lệ")
        job_payload = {} if payload is None else payload
        _validate_json_object(job_payload, "payload")
        if available_at is not None and available_at.tzinfo is None:
            raise ValueError("available_at phải có timezone")

        scope = f"user:{user_id}" if user_id is not None else "system"
        fingerprint = _idempotency_fingerprint(
            job_type=job_type,
            payload=job_payload,
            priority=priority,
            max_attempts=max_attempts,
            agent_run_id=agent_run_id,
        )
        job = BackgroundJob(
            job_type=job_type,
            payload=job_payload,
            queue_name=queue_name,
            priority=priority,
            available_at=available_at or datetime.now(UTC),
            user_id=user_id,
            agent_run_id=agent_run_id,
            status="queued",
            max_attempts=max_attempts,
            idempotency_scope=scope if idempotency_key else None,
            idempotency_key=idempotency_key,
            idempotency_fingerprint=fingerprint if idempotency_key else None,
        )
        async with SessionFactory() as session:
            if idempotency_key:
                existing = await self._find_by_idempotency(
                    session,
                    queue_name=queue_name,
                    scope=scope,
                    key=idempotency_key,
                )
                if existing is not None:
                    return self._resolve_idempotent_job(existing, fingerprint)
            session.add(job)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                if not idempotency_key:
                    raise
                existing = await self._find_by_idempotency(
                    session,
                    queue_name=queue_name,
                    scope=scope,
                    key=idempotency_key,
                )
                if existing is None:
                    raise
                return self._resolve_idempotent_job(existing, fingerprint)
            await session.refresh(job)
        return job

    @staticmethod
    async def _find_by_idempotency(
        session: Any,
        *,
        queue_name: str,
        scope: str,
        key: str,
    ) -> BackgroundJob | None:
        statement = select(BackgroundJob).where(
            BackgroundJob.queue_name == queue_name,
            BackgroundJob.idempotency_scope == scope,
            BackgroundJob.idempotency_key == key,
        )
        return (await session.execute(statement)).scalar_one_or_none()

    @staticmethod
    def _resolve_idempotent_job(job: BackgroundJob, fingerprint: str) -> BackgroundJob:
        if job.idempotency_fingerprint != fingerprint:
            raise BackgroundIdempotencyConflictError(
                "Idempotency key đã được dùng cho nội dung job khác"
            )
        return job

    async def claim_next(
        self,
        *,
        queue_name: str,
        worker_id: str,
        lease_seconds: float = 30,
    ) -> ClaimedJob | None:
        if not _QUEUE_NAME_PATTERN.fullmatch(queue_name):
            raise ValueError("queue_name không hợp lệ")
        if not _WORKER_ID_PATTERN.fullmatch(worker_id):
            raise ValueError("worker_id không hợp lệ")
        _validate_lease_seconds(lease_seconds)
        now = datetime.now(UTC)
        async with SessionFactory() as session:
            statement = self.build_claim_statement(queue_name=queue_name, now=now)
            job = (await session.execute(statement)).scalar_one_or_none()
            if job is None:
                await session.rollback()
                return None
            job.status = "running"
            job.worker_id = worker_id
            job.started_at = now
            job.attempt_count += 1
            job.lease_expires_at = now + timedelta(seconds=lease_seconds)
            job.error_code = None
            job.error_message = None
            job.updated_at = now
            await session.commit()
            return ClaimedJob(
                id=job.id,
                queue_name=job.queue_name,
                job_type=job.job_type,
                payload=dict(job.payload),
                worker_id=worker_id,
                started_at=now,
                attempt_count=job.attempt_count,
                max_attempts=job.max_attempts,
            )

    async def request_cancel(self, job_id: UUID, *, user_id: UUID | None = None) -> str:
        now = datetime.now(UTC)
        async with SessionFactory() as session:
            statement = select(BackgroundJob).where(BackgroundJob.id == job_id).with_for_update()
            job = (await session.execute(statement)).scalar_one_or_none()
            if job is None:
                raise LookupError("Không tìm thấy background job")
            if user_id is not None and job.user_id != user_id:
                raise LookupError("Không tìm thấy background job")
            if job.status == "queued":
                job.status = "cancelled"
                job.cancel_requested_at = now
                job.cancelled_at = now
                job.finished_at = now
                job.updated_at = now
            elif job.status == "running" and job.cancel_requested_at is None:
                job.cancel_requested_at = now
                job.updated_at = now
            await session.commit()
            return job.status

    async def is_cancel_requested(self, job: ClaimedJob) -> bool:
        async with SessionFactory() as session:
            current = await session.get(BackgroundJob, job.id)
            return current is None or current.cancel_requested_at is not None

    async def renew_lease(self, job: ClaimedJob, *, lease_seconds: float) -> None:
        _validate_lease_seconds(lease_seconds)
        now = datetime.now(UTC)
        async with SessionFactory() as session:
            current = await self._owned_job(session, job)
            current.lease_expires_at = now + timedelta(seconds=lease_seconds)
            current.updated_at = now
            await session.commit()

    async def cancel_running(self, job: ClaimedJob) -> None:
        now = datetime.now(UTC)
        async with SessionFactory() as session:
            current = await self._owned_job(session, job)
            current.status = "cancelled"
            current.cancel_requested_at = current.cancel_requested_at or now
            current.cancelled_at = now
            current.finished_at = now
            current.lease_expires_at = None
            current.updated_at = now
            await session.commit()

    async def succeed(self, job: ClaimedJob, result: dict[str, Any]) -> None:
        _validate_json_object(result, "result")
        await self._finish(job, status="succeeded", result=result)

    async def fail(
        self,
        job: ClaimedJob,
        *,
        error_code: str,
        error_message: str,
    ) -> None:
        self._validate_error(error_code, error_message)
        await self._finish(
            job,
            status="failed",
            error_code=error_code,
            error_message=redact_text(error_message, max_length=500),
        )

    async def retry_or_fail(
        self,
        job: ClaimedJob,
        *,
        error_code: str,
        error_message: str,
        delay_seconds: float,
    ) -> str:
        self._validate_error(error_code, error_message)
        if isinstance(delay_seconds, bool) or not 0 <= delay_seconds <= 3600:
            raise ValueError("delay_seconds phải từ 0 đến 3600")
        now = datetime.now(UTC)
        async with SessionFactory() as session:
            current = await self._owned_job(session, job)
            if current.cancel_requested_at is not None:
                current.status = "cancelled"
                current.cancelled_at = now
                current.finished_at = now
            elif current.attempt_count < current.max_attempts:
                current.status = "queued"
                current.available_at = now + timedelta(seconds=delay_seconds)
                current.worker_id = None
                current.started_at = None
                current.finished_at = None
            else:
                current.status = "failed"
                current.finished_at = now
            current.error_code = error_code
            current.error_message = redact_text(error_message, max_length=500)
            current.lease_expires_at = None
            current.updated_at = now
            await session.commit()
            return current.status

    async def recover_expired_leases(self, *, queue_name: str, limit: int = 100) -> int:
        if not _QUEUE_NAME_PATTERN.fullmatch(queue_name):
            raise ValueError("queue_name không hợp lệ")
        if isinstance(limit, bool) or not 1 <= limit <= 1000:
            raise ValueError("limit phải từ 1 đến 1000")
        now = datetime.now(UTC)
        async with SessionFactory() as session:
            statement = (
                select(BackgroundJob)
                .where(
                    BackgroundJob.queue_name == queue_name,
                    BackgroundJob.status == "running",
                    or_(
                        BackgroundJob.lease_expires_at <= now,
                        BackgroundJob.lease_expires_at.is_(None),
                    ),
                )
                .order_by(BackgroundJob.lease_expires_at, BackgroundJob.id)
                .with_for_update(skip_locked=True)
                .limit(limit)
            )
            jobs = list((await session.execute(statement)).scalars())
            for job in jobs:
                job.worker_id = None
                job.started_at = None
                job.lease_expires_at = None
                job.updated_at = now
                if job.cancel_requested_at is not None:
                    job.status = "cancelled"
                    job.cancelled_at = now
                    job.finished_at = now
                elif job.attempt_count < job.max_attempts:
                    job.status = "queued"
                    job.available_at = now
                    job.error_code = "worker_lease_expired"
                    job.error_message = "Worker mất lease; job được đưa lại vào queue."
                else:
                    job.status = "failed"
                    job.finished_at = now
                    job.error_code = "worker_lease_expired"
                    job.error_message = "Worker mất lease và job đã hết số lần thử."
            await session.commit()
            return len(jobs)

    @staticmethod
    async def _owned_job(session: Any, claimed: ClaimedJob) -> BackgroundJob:
        statement = select(BackgroundJob).where(BackgroundJob.id == claimed.id).with_for_update()
        job = (await session.execute(statement)).scalar_one_or_none()
        if job is None or job.status != "running" or job.worker_id != claimed.worker_id:
            await session.rollback()
            raise BackgroundJobOwnershipError("Worker không sở hữu background job")
        return job

    @staticmethod
    def _validate_error(error_code: str, error_message: str) -> None:
        if not _JOB_TYPE_PATTERN.fullmatch(error_code):
            raise ValueError("error_code không hợp lệ")
        if not error_message.strip():
            raise ValueError("error_message không được rỗng")

    async def _finish(
        self,
        claimed: ClaimedJob,
        *,
        status: str,
        result: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        now = datetime.now(UTC)
        async with SessionFactory() as session:
            job = await self._owned_job(session, claimed)
            if status == "succeeded" and job.cancel_requested_at is not None:
                job.status = "cancelled"
                job.cancelled_at = now
                job.result = None
            else:
                job.status = status
                job.result = result
            job.error_code = error_code
            job.error_message = (
                redact_text(error_message, max_length=500) if error_message else None
            )
            job.finished_at = now
            job.lease_expires_at = None
            job.updated_at = now
            await session.commit()

    async def get(self, job_id: UUID) -> BackgroundJob | None:
        async with SessionFactory() as session:
            return await session.get(BackgroundJob, job_id)
