# Immediate worker-to-browser streaming

User requested removing the 0.5-second delay; existing approval is to implement streaming and leave live UI acceptance to the user. Work continues on `fix/chat-final-artifact` without changing `.env` or the user database.

Evidence: `agent_jobs.agent_run_handler` additionally buffers text until 1,000 characters. `run_events.subscribe` sleeps 0.5 seconds when no events exist. Remove both causes.

Approach: persist each SSE packet immediately. Issue PostgreSQL `pg_notify` in the same transaction as the persisted event; the notification carries only the run UUID. Use one shared asyncpg LISTEN connection per web process, dispatching wakeups to active subscriptions. Register before querying and clear wakeups before each catch-up query to prevent lost notifications. The event table and sequence remain authoritative for replay and authorization. Failed listeners reconnect with bounded backoff and use a short database fallback; normal delivery waits on notifications rather than a timer. Close the listener when the last subscriber leaves.

- [x] Test small chunk persistence before the next chunk, shared listener lifetime, wakeup races, listener failure/reconnect and transaction notification.
- [x] Implement notifications, immediate packet persistence and notification-driven authorized event catch-up.
- [x] Verify focused regressions and notification delivery against an isolated PostgreSQL. No live model calls.
- [x] Independent review, record verification, commit on the existing branch. No merge/push.

## Verification and review

- Initial regressions failed on missing immediate persistence, missing transaction notification and the polling subscriber. Final focused run: **47 passed**, including **4 real PostgreSQL integration tests**. Model/browser SSE fixture checks: **11 passed**.
- PostgreSQL cases cover a first small chunk received while the producer is deliberately paused before the second, ordered replay, notification after commit only (not rollback), rejection of another owner, and immediate terminal delivery after worker lease recovery.
- Independent review identified three Important issues. One fix pass added notifications to every current RunEvent writer (including enqueue and recovery), woke every subscriber on reconnect, and closed the model generator deterministically after persistence failure/cancellation. Regressions reproduced the issues before the fixes and pass after them.
- Ruff check/format, JavaScript SSE test runner and `git diff --check` passed. Full project suite and live-provider/browser acceptance were not run.
- QA used a separately created PostgreSQL container on loopback port 65419, migrated to head with fake settings and dotenv loading disabled. Actual `.env` and user database were not touched. No new migration, dependency, Redis service or environment variable is needed.
