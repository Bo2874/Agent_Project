# Workspace 04 — Scheduled Automations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Nhiệm vụ chạy theo lịch khi tab đóng, dùng Artifacts/Google đã có, quản lý stop/pause và xem kết quả bền vững.

**Architecture:** Scheduler process riêng chỉ ghi scheduled tick/job vào PostgreSQL; queue/worker/checkpoint/journal hiện có chạy Agent. Automation giữ revision instruction/file/action grant; mỗi lượt dùng execution conversation riêng và policy token tổng nullable. Không có Redis/Celery hay Agent sống để chờ giờ.

**Tech Stack:** PostgreSQL locking/unique indexes, queue worker sẵn có, LangGraph, stdlib datetime/zoneinfo + tzdata, FastAPI/Jinja2/JavaScript.

**Spec:** `docs/superpowers/specs/2026-11-04-agent-workspace-design.md`; phụ thuộc plan 01–03 và quy ước roadmap.

## Global Constraints

- “Không có trần token/bước/thời gian tổng cho Automation theo lựa chọn user; vẫn ghi usage/chi phí.”
- “Mỗi Automation tối đa một lượt active, tính cả đang chờ duyệt.”
- “Dừng lượt chạy: Hủy job/run hiện tại, giữ lịch; không tự retry lượt bị user hủy”.
- “Bật lại: Chờ mốc kế tiếp, không chạy bù khoảng đã tắt”.
- “Python tùy ý giữ approval từng call trong bản đầu”.
- Giữ context/output mỗi model call, RPC timeout, sandbox limits, quyền và unknown-outcome journal. Không dùng số hữu hạn lớn để giả unlimited.

## Review Focus

1. JSON null ở root/task budget khi resume, nhưng checkpoint chat cũ vẫn finite — A1.
2. Scheduler tắt 2 ngày hoặc DST lặp giờ: không chạy bù/nhân lượt — A3.
3. Pause đúng lúc worker chuyển queued→running: không lách qua trạng thái disabled — A4/A5.
4. Google ghi xong trước cancel/crash: giữ phần kết quả và không báo rollback/append lại — A4/A7.
5. Lượt waiting_approval hoặc needs_reconciliation kéo dài qua ngày mới: không overlap và không mất quyền can thiệp — A2/A6.

---

## File map và state contract

Mới: `backend/models/automation.py`, `backend/models/notification.py`, `backend/schemas/automation.py`, `backend/services/automations/{service,schedules,dispatch,execution,authority,notifications}.py`, `backend/routes/automations.py`, `backend/routes/notifications.py`, `scripts/run_automation_scheduler.py`, `frontend/templates/automations.html`, `frontend/static/automations.js`, `frontend/static/notifications.js`; migration `20261104_0033_create_automations.py`. Package exports/init theo repo.

Sửa runtime budget/task pool/planner, `backend/models/chat.py`, `backend/services/chat.py`, `agent_jobs.py`, `agent_context.py`, `background/handlers.py`, routes chat approvals/cancel, Google/Artifact authority, shell/pages/main/config/Compose.

`Automation` (owner, enabled, immutable revision refs, next_run_at UTC); `AutomationRevision` (instruction/schedule/output/grant snapshot); `AutomationRun` (trigger/scheduled_for/revision/job/agent_run/status); `AutomationGrant` (capability + exact connection/file/range restrictions + credential generation); `UserNotification` (owner/event key/read_at/result refs).

Run statuses `queued|running|waiting_approval|cancelling|needs_reconciliation|succeeded|failed|cancelled|skipped_overlap|missed`. Active partial unique index theo automation cho 5 trạng thái đầu; skipped/missed có thể không có AgentRun/job. Scheduled uniqueness `(automation_id,scheduled_for)` độc lập revision; manual run dùng request idempotency key riêng. Lock order khi mutation: Automation → AutomationRun → BackgroundJob.

### A1 — Token tổng nullable xuyên runtime, giữ policy chat

**Files:** `agent_core/graph/{runtime,state,limits,budgeting,runner}.py`, `agent_core/task_workers.py`, `agent_core/planning.py`, `agent_core/contracts/tasks.py`, `backend/services/agent_jobs.py`, `frontend/static/chat.js`; sửa tests budgeting/jobs/planning/workers hiện có, thêm `tests/test_automation_budget_policy.py`.

**Interfaces:** `AgentLimits.max_total_tokens: int|None`, `TaskBudget.max_tokens: PositiveInt|None`, `SharedWorkerBudget(available: int|None)`; None là không trần aggregate, reservation và actual usage vẫn ghi. `execution_limits_for_source(source: Literal['chat','automation'], settings: Settings) -> AgentLimits` mới trong execution service, source do DB xác minh. Checkpoint budget/source pin theo run, không theo flag đổi sau.

- [ ] Red tests: `test_unbounded_root_and_children_can_exceed_old_cap` asserts > cap fixture vẫn hoàn tất và usage đúng; `test_null_budget_checkpoint_resume` asserts null round-trip; `test_existing_chat_and_finite_checkpoint_unchanged` asserts finite vẫn dừng đúng; `test_unknown_billing_still_blocks_replay` asserts unknown không retry.
- [ ] Chạy `python -m pytest tests/test_automation_budget_policy.py tests/test_agent_budgeting.py tests/test_agent_jobs.py -q` và thấy đỏ cho None.
- [ ] Guard mọi arithmetic/comparison/reserve/final allowance, task aggregate, serialization và status format khi cap None. Automation children mặc định không nhận finite aggregate từ planner nếu root unlimited; context/output mỗi call vẫn hữu hạn. Giữ parallelism/task count limits hiện có. Chỉ `source='automation'` từ trusted run admission dùng policy mới, không nhận tùy chọn unlimited từ LLM.
- [ ] Green target + tests task workers/planning hiện có; chạy graph > cap nhỏ với model fixture và resume bằng checkpoint thật. Không sửa `.env` hoặc migrate token budget lịch sử. Commit `feat: support unbounded automation token accounting`.

### A2 — Automation, revision, grant và API owner

**Files:** model/schema/service/authority/migration trên; `models/__init__.py`, main/pages/config; mới `tests/test_automation_models.py`, `tests/test_automation_api.py`.

**Interfaces:** `create_automation(session, owner: UUID, draft: AutomationDraft) -> AutomationView`; `revise_automation(session, owner, automation_id, draft) -> AutomationView`; `get_owned_automation_run(session, owner, run_id) -> AutomationRunView`. Draft: name/instruction, ScheduleSpec, file outputs và grants có explicit action/range. API `POST/GET /api/automations`, `GET/PATCH /api/automations/{id}`, `GET /api/automations/{id}/runs`.

- [ ] Red owner isolation 404, invalid timezone/blank instruction rejected, grant không vượt selected Google files, revision cũ không thay đổi khi edit, two active runs vi phạm unique index. `test_waiting_approval_and_unknown_run_are_active` asserts không tạo lượt chạy song song mới.
- [ ] Run hai file test trên thấy đỏ; implement DB/schema/grants với unique/FK và audit. Migration 0033 bao gồm `Conversation.source_kind` default `chat` ngay từ đầu; A4 chỉ sử dụng, không sửa migration đã áp dụng. Review UI draft trước bật lịch; không LLM tự đăng ký lịch chỉ từ câu trả lời prose.
- [ ] Không cấp quyền xóa/chia sẻ/owner transfer mặc định; action scope lấy từ các công cụ thật plan 01–03. Sửa revision áp dụng lượt sau; quyền thu hồi luôn chặn call tiếp theo. Automation disabled tạo được rồi chạy thử trước bật.
- [ ] Green API + migration scratch schema; commit `feat: persist owner-scoped automation revisions and grants`.

### A3 — Lịch timezone và scheduler PostgreSQL

**Files:** schedules/dispatch/scheduler script; config/env mẫu, `pyproject.toml`, `uv.lock`, Compose; mới `tests/test_automation_schedule.py`, `tests/test_automation_dispatch.py`.

**Interfaces:** `ScheduleSpec(kind: Literal['once','daily','weekly'], timezone: str, local_time: time|None, weekday: int|None, at: datetime|None)`; `next_occurrence(schedule: ScheduleSpec, *, after: datetime) -> datetime|None` strictly future UTC; `dispatch_due(session, *, now: datetime, limit: int=100) -> DispatchReport`. `AUTOMATION_SCHEDULE_GRACE_SECONDS=60`, tick 1s; quá grace ghi missed, không backfill. Đây là dung sai trễ hệ thống được công bố, không tạo backlog.

- [ ] Red daily VN: 08:00 ngày 05/11/2026 → `2026-11-05T01:00:00Z`; weekly thứ Hai đúng ngày; bật lại sau 2 ngày → chỉ next future. DST giờ nonexistent bỏ mốc, ambiguous chỉ fold=0 một lần. Hai scheduler cùng tick assert một job/one run, không hai revision tạo hai lượt cùng scheduled_for.
- [ ] Run `python -m pytest tests/test_automation_schedule.py tests/test_automation_dispatch.py -q` thấy đỏ.
- [ ] Implement stdlib zoneinfo với tzdata cho Windows. Claim automation row SKIP LOCKED, khóa ngắn; tick/run + queue.enqueue + next_run_at trong một transaction. Có active thì ghi skipped_overlap và tăng mốc; offline quá grace ghi missed cùng số mốc bị lỡ, nhảy future. Once hết hạn → không có next occurrence.
- [ ] Scheduler độc lập với lifespan web; health heartbeat và shutdown hợp tác. Không gọi model/tool trong scheduler. Green tests + PostgreSQL 2 dispatcher thật; Compose scheduler service bật bằng flag, commit `feat: dispatch durable timezone-aware automation schedules`.

### A4 — Execution conversation và worker dùng runtime chung

**Files:** execution/authority, `backend/services/agent_jobs.py`, `agent_context.py`, `chat.py`, `background/handlers.py`, `models/chat.py`; Google/Artifact tool services; mới `tests/test_automation_execution.py`. Dùng trường schema A2 đã tạo.

**Interfaces:** `start_automation_run(context: JobExecutionContext, automation_run_id: UUID) -> dict`; job type `automation.run`; `resolve_run_authority(session, *, agent_run_id: UUID) -> RunAuthority` (source, owner, revision/grant IDs, file restrictions). Conversation thêm `source_kind='chat'|'automation'`, mặc định chat; chỉ list chat thường lọc, owner vẫn mở execution conversation qua run detail được.

- [ ] Red tắt tab không hủy job; instruction/context chỉ từ revision/selected inputs, không nạp history lượt hôm qua; disabled queued scheduled job không start; manual disabled được start; revoke giữa model/tool chặn call; checkpoint resume không lặp Google receipt đã completed.
- [ ] Run `python -m pytest tests/test_automation_execution.py -q` thấy fail.
- [ ] Trong lock automation recheck owner active/enabled cho scheduled run và active uniqueness, chuyển queued→running; tạo execution conversation/input/AgentRun cùng owner, gọi admission + runtime chung. Extract headless execution ra service khi cần từ code `stream_assistant_response`; chat adapter vẫn stream SSE, không tạo executor thứ hai hoặc gọi HTTP chat endpoint từ worker.
- [ ] RunAuthority từ DB cho phép Google action đã được grant rõ khi tạo lịch, audit `approved_by_automation_grant`; không bool bypass do caller gửi. Vượt scope → waiting_approval. Raw Python vẫn chờ approval từng call; structured Artifact tools trong grant có thể chạy vắng mặt. Mỗi lời gọi kiểm generation hiện tại.
- [ ] Map completed/failed/cancelled/waiting_approval/unknown ra AutomationRun; unknown → needs_reconciliation và giữ slot active. Giữ partial results/remote receipts. Green fixtures + worker/checkpoint thật, commit `feat: execute scheduled work through durable agent runtime`.

### A5 — Dừng lượt, tắt lịch và race claim

**Files:** service/dispatch/execution/routes và cancel/approval adapters hiện có; mới `tests/test_automation_controls.py`.

**Interfaces:** `request_run_stop(session, owner, run_id) -> AutomationRunView`; `set_schedule_enabled(session, owner, automation_id, *, enabled: bool, stop_active: bool=False) -> AutomationView`; `run_now(session, owner, automation_id, *, idempotency_key: str) -> AutomationRunView`; `close_unknown_run(session, owner, run_id) -> AutomationRunView`. Routes `/runs/{id}/stop`, `/{id}/enabled`, `/{id}/run-now`, `/runs/{id}/close-unknown`.

- [ ] Red stop chỉ hủy lượt hiện tại, enabled vẫn true; pause-only để active chạy tiếp nhưng cancel scheduled queued; pause+stop hủy cả hai; bật lại không catch-up; manual khi disabled được phép, manual khi active trả 409. Test PostgreSQL barriers cho pause race scheduler/worker start.
- [ ] Implement lock order A2; queued cancel ngay, active → cancelling rồi dùng cờ hủy queue/runtime hiện có. Không terminal cancelled trước khi worker xác nhận drain hoặc đối soát request ngoài; deadline RPC riêng vẫn giúp lời gọi đang chờ kết thúc. Approval muộn sau user cancel không hồi sinh run.
- [ ] Cho owner đối soát file đích rồi chọn “Kết thúc lượt chưa rõ kết quả”: `close_unknown_run` chỉ nhận run needs_reconciliation, ghi audit xác nhận thủ công, terminal failed với reason `closed_without_replay` và giải phóng active slot. Giữ GoogleMutation/usage unknown nguyên trạng, không biến thành success/0 token và không gọi lại write. Test `assert provider_write.call_count == 1`, `assert run.status == 'failed'`, `assert run.stop_reason == 'closed_without_replay'`; actor khác 404.
- [ ] Green controls + existing background/cancel/approval regressions. Kiểm Google append fixture đã completed trước stop vẫn hiển thị receipt, không báo undo. Commit `feat: separate stopping automation runs from disabling schedules`.

### A6 — UI Automations, lịch sử và inbox

**Files:** templates/JS/routes/notifications models/services; shell/pages/main; mới `tests/test_automation_notifications.py`.

**Interfaces:** `notify_automation_result(session, run_id: UUID) -> None` idempotent theo `(owner,event_key)`; `GET /api/notifications`, `POST /api/notifications/{id}/read`. Automation detail dùng run replay API owner-scoped hiện có, có result refs/Google URLs và source revision; `GET /automations` page.

- [ ] Red duplicate completion event chỉ một notification; read flag theo owner; result links không cross-owner; waiting_approval/needs_reconciliation vẫn tìm và thao tác được sau reload.
- [ ] Implement form task/schedule/timezone/file/action preview; Run now, Enabled, “Dừng lượt chạy”. Tắt lịch khi active hiện hai lựa chọn đúng mock đã duyệt. Needs_reconciliation có link file đích, mô tả phần chưa xác định và action kết thúc không phát lại A5 để user xử lý sau đối soát. Hiển thị mốc tiếp theo/missed/overlap/usage, không token budget control hoặc số remaining giả khi None. Badge inbox là dữ liệu DB, SSE chỉ cập nhật realtime.
- [ ] Green notification API; browser create draft→review→run thử→bật→stop/pause→bật lại. Kiểm reload/tab khác và trạng thái đang hủy; thẻ file chỉ mở preview khi bấm. Không email/push ngoài app.
- [ ] Commit `feat: manage automation schedules runs and result inbox`.

### A7 — Nghiệm thu restart, không lặp ghi và vận hành

**Files:** mới `scripts/check_automation_workflow.py`, `docs/manual-automations.md`; sửa `OPERATIONS.md`, Compose/env mẫu, `PROJECT_NOTES.md`, `PROJECT_PLAN.md`.

**Interfaces:** `python -m scripts.check_automation_workflow --offline` dùng DB/queue/scheduler/worker thật + model/Google fixtures; `--live --automation-id <selected-test-id>` chỉ sau user chọn dữ liệu thử. Tất cả cleanup theo owner/schema/job resource fixture.

- [ ] Khởi chạy lịch thật ở mốc gần, đóng browser, kiểm output và inbox bằng session mới; hai scheduler/worker, kill giữa dispatch và job claim, restart checkpoint sau receipt completed, unknown receipt không tự append lại.
- [ ] Simulate offline 2 ngày, overlap qua mốc tiếp theo, waiting_approval qua đêm, pause/start race và stop trong RPC. Kiểm một slot active, skipped/missed đúng và cancel không tự retry.
- [ ] Kiểm automation root/task aggregate > old token cap bằng model fixture, context/output/RPC/sandbox limits vẫn đúng; giữ checkpoint chat finite cũ. Chạy toàn suite/Ruff/JS/Compose/diff và browser QA.
- [ ] Live Google chỉ với selected test Sheet; tách kết quả live/fixture, lưu usage thực, không suy từ mock. Document scheduler health, reconnect, reconciliation thủ công, disabling flag và rollback migration/config phù hợp dữ liệu đang có. Không tự drop dữ liệu để downgrade.
- [ ] Cập nhật gate có bằng chứng, commit `docs: verify scheduled agent workflows and recovery`.

## Sau plan 04

Condition triggers/webhook/polling, OCR/ảnh, connector khác và workflow builder là các dự án tiếp theo. Không tick chúng chỉ vì scheduler đã chạy đúng theo lịch.
