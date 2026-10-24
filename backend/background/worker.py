import asyncio
import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from backend.background.queue import BackgroundJobOwnershipError, ClaimedJob, PostgresJobQueue

logger = logging.getLogger(__name__)
_JOB_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,79}$")


class RetryableJobError(RuntimeError):
    """A known transient failure that may be retried within the job budget."""

    def __init__(self, message: str, *, error_code: str = "retryable_job_error") -> None:
        if not _JOB_TYPE_PATTERN.fullmatch(error_code):
            raise ValueError("error_code không hợp lệ")
        if not message.strip():
            raise ValueError("message không được rỗng")
        super().__init__(message)
        self.error_code = error_code


class JobCancellationRequested(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class JobExecutionContext:
    queue: PostgresJobQueue
    job: ClaimedJob

    async def raise_if_cancelled(self) -> None:
        if await self.queue.is_cancel_requested(self.job):
            raise JobCancellationRequested("Background job đã được yêu cầu hủy")


BackgroundJobHandler = Callable[[JobExecutionContext, Mapping[str, Any]], Awaitable[dict[str, Any]]]


class BackgroundJobRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, BackgroundJobHandler] = {}

    def register(self, job_type: str, handler: BackgroundJobHandler) -> None:
        if not _JOB_TYPE_PATTERN.fullmatch(job_type):
            raise ValueError("job_type không hợp lệ")
        if job_type in self._handlers:
            raise ValueError(f"Background handler '{job_type}' đã tồn tại")
        self._handlers[job_type] = handler

    def get(self, job_type: str) -> BackgroundJobHandler | None:
        return self._handlers.get(job_type)

    def names(self) -> tuple[str, ...]:
        return tuple(self._handlers)


class BackgroundWorker:
    def __init__(
        self,
        *,
        queue: PostgresJobQueue,
        registry: BackgroundJobRegistry,
        queue_name: str,
        worker_id: str,
        poll_seconds: float = 1.0,
        lease_seconds: float = 30.0,
        control_poll_seconds: float = 1.0,
        retry_base_seconds: float = 1.0,
        retry_max_seconds: float = 60.0,
        heartbeat_handler: Callable[[], None] | None = None,
    ) -> None:
        if not 0 < poll_seconds <= 60:
            raise ValueError("poll_seconds phải từ trên 0 đến 60")
        if not 1 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds phải từ 1 đến 3600")
        if not 0.05 <= control_poll_seconds <= lease_seconds / 2:
            raise ValueError("control_poll_seconds phải từ 0.05 đến một nửa lease_seconds")
        if not 0 <= retry_base_seconds <= retry_max_seconds <= 3600:
            raise ValueError("retry backoff không hợp lệ")
        self.queue = queue
        self.registry = registry
        self.queue_name = queue_name
        self.worker_id = worker_id
        self.poll_seconds = poll_seconds
        self.lease_seconds = lease_seconds
        self.control_poll_seconds = control_poll_seconds
        self.retry_base_seconds = retry_base_seconds
        self.retry_max_seconds = retry_max_seconds
        self.heartbeat_handler = heartbeat_handler

    def _heartbeat(self) -> None:
        if self.heartbeat_handler is not None:
            self.heartbeat_handler()

    def retry_delay(self, attempt_count: int) -> float:
        return min(
            self.retry_base_seconds * (2 ** max(0, attempt_count - 1)),
            self.retry_max_seconds,
        )

    async def run_once(self) -> bool:
        self._heartbeat()
        await self.queue.recover_expired_leases(queue_name=self.queue_name)
        job = await self.queue.claim_next(
            queue_name=self.queue_name,
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
        )
        if job is None:
            return False
        await self._execute(job)
        return True

    async def _execute(self, job: ClaimedJob) -> None:
        try:
            await self._execute_owned(job)
        except BackgroundJobOwnershipError:
            logger.warning(
                "Background job %s không còn thuộc worker này; dừng ghi kết quả.", job.id
            )

    async def _execute_owned(self, job: ClaimedJob) -> None:
        handler = self.registry.get(job.job_type)
        if handler is None:
            await self.queue.fail(
                job,
                error_code="unknown_job_type",
                error_message="Không có handler cho background job.",
            )
            return
        context = JobExecutionContext(queue=self.queue, job=job)
        try:
            result = await self._run_with_control(handler, context, job.payload)
            if not isinstance(result, dict):
                raise TypeError("Background handler phải trả object")
        except BackgroundJobOwnershipError:
            raise
        except JobCancellationRequested:
            await self.queue.cancel_running(job)
            return
        except RetryableJobError as exc:
            logger.warning(
                "Background job %s gặp lỗi tạm thời ở lần %s/%s: %s",
                job.id,
                job.attempt_count,
                job.max_attempts,
                exc.error_code,
            )
            await self.queue.retry_or_fail(
                job,
                error_code=exc.error_code,
                error_message=str(exc),
                delay_seconds=self.retry_delay(job.attempt_count),
            )
            return
        except Exception:
            logger.exception("Background job %s thực thi thất bại", job.id)
            await self.queue.fail(
                job,
                error_code="job_handler_failed",
                error_message="Background job handler thực thi thất bại.",
            )
            return
        await self.queue.succeed(job, result)

    async def _run_with_control(
        self,
        handler: BackgroundJobHandler,
        context: JobExecutionContext,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        task = asyncio.create_task(handler(context, payload))
        try:
            while True:
                self._heartbeat()
                done, _ = await asyncio.wait({task}, timeout=self.control_poll_seconds)
                if task in done:
                    return task.result()
                if await self.queue.is_cancel_requested(context.job):
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    raise JobCancellationRequested("Background job đã được yêu cầu hủy")
                await self.queue.renew_lease(
                    context.job,
                    lease_seconds=self.lease_seconds,
                )
        except BaseException:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            raise

    async def run_forever(self, stop_event: asyncio.Event | None = None) -> None:
        stop = stop_event or asyncio.Event()
        while not stop.is_set():
            processed = await self.run_once()
            if processed:
                continue
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.poll_seconds)
            except TimeoutError:
                pass
