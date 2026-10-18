# Nghiệm thu Ngày 78 — Docker dev

Ngày thực hiện: 2026-10-18. Kết quả: **PASS**.

Đã build dependency Linux bằng `--pull --no-cache`, khởi tạo database/volume mới,
kiểm tra runtime, tạo lại container và xác minh persistence. Sau khi làm nhẹ healthcheck,
đã build image cuối và khởi động lại trên project hoàn toàn mới để kiểm tra migration từ đầu.
Đây là mô phỏng checkout mới trên máy chỉ có Docker: image không nhận `.env`, `.venv`,
PostgreSQL hay Docker socket của host. Không thực hiện cài lại Windows hoặc kiểm thử mọi OS/CPU.

## Môi trường và image

| Thành phần | Giá trị đã kiểm tra |
| --- | --- |
| Host | Windows, Docker Desktop 4.43.1, Linux containers/WSL2 |
| Docker Engine / Compose | 28.3.0 / v2.38.1-desktop.1 |
| Python trong image | 3.11.17, `python:3.11-slim-bookworm` |
| PostgreSQL / pgvector | 17.11 / 0.8.7; `pgvector/pgvector:0.8.7-pg17-bookworm` |
| Alembic | `20261004_0022` head, 22 revision trên DB mới |
| Runtime user / filesystem | UID/GID 10001, root read-only, `/tmp` tmpfs |
| Port host | Chỉ app `127.0.0.1:8000`; PostgreSQL/worker không publish port |
| Model provider calls | 0; graph dùng fixture xác định kết quả, MCP/DB/checkpoint thật |

Base Python digest trong cold build:
`sha256:0a310eeecf4e1f5a0743f9a6520c90c88d089c903ca5fd283f501e3a805f5f89`.

Image config ID cuối:

- App: `sha256:9a1385470b1649641fc175e4006bc8f384a4487784098fe49bb463f44e3bbbb6`.
- Worker: `sha256:f54e97107414356d941bb23b5eb0ae7b75c3e992529cf70bb0e79fc9e38b0df9`.
- Migration: `sha256:dd7ee4fde3f924d4ccb700ab45b5a1e1eb3f4b9ee23e0731a1bf45d16e1c03a5`.
- PostgreSQL: `sha256:ac08538c6f8b9904c33c8224c5e5706dbe760aca29db1d096972b4052c22a75d`.

Package versions đầy đủ: [linux-packages.json](linux-packages.json).
Chưa có dependency lock; resolver Linux chọn các patch/transitive version phù hợp constraints,
có khác host. Khóa dependency/image để build tái lập thuộc Ngày 79/M6.

## Kiểm tra và bằng chứng

| Kiểm tra | Kết quả | Bằng chứng |
| --- | --- | --- |
| Cold build | Ba service build thành công, dependency cài mới | [build.log](build.log) |
| Build image cuối | Reuse dependency layer, cập nhật source/probe | [build-final.log](build-final.log) |
| Project cuối chưa có dữ liệu | Không có container/volume trước startup | [clean-preflight.json](clean-preflight.json) |
| Startup trên volume mới | PostgreSQL healthy → migration exit 0 → app/worker healthy | [clean-startup.log](clean-startup.log), [clean-containers.json](clean-containers.json) |
| Migration mới | Vector extension, 22 Alembic revision và LangGraph schema | [clean-migration.log](clean-migration.log) |
| Vector/worker/MCP/graph/checkpoint | Distance √2, worker thực thi job, MCP 2×3=6, checkpoint lưu PostgreSQL | [clean-runtime-probe.json](clean-runtime-probe.json) |
| HTTP E2E | Health, registration/auth, conversation, tenant isolation, cleanup đạt | [clean-e2e.json](clean-e2e.json) |
| Memory API | List/update/pin/delete và tenant isolation đạt; fixture manual memory | [clean-memory-api.json](clean-memory-api.json), [probe](memory_probe.py) |
| Checkpoint failure/resume | Resume từ state đã lưu, kết quả 42 | [checkpoint.log](checkpoint.log) |
| Alembic drift | Không có upgrade operation mới | [alembic-check.log](alembic-check.log) |
| Dependency consistency | `pip check` đạt | [pip-check.log](pip-check.log) |
| Hai volume độc lập | PG volume chỉ mount postgres; document volume chia sẻ app/worker | [containers.json](containers.json), [shared-volume.log](shared-volume.log) |
| Persistence qua down/up | Cùng job UUID/payload và file tài liệu sau tạo lại mọi container | [before](persistence-before.json), [after](persistence-after.json), [probe](persistence_probe.py) |
| Migration chạy lại | Extension/schema đã có, upgrade head idempotent | [migration-idempotent.log](migration-idempotent.log) |
| Migration thất bại | Cố ý exit 17; app/worker ở trạng thái `created`, không chạy | [override](migration-failure.compose.yaml), [state](migration-failure-state.json) |
| Regression host | 420 offline + 1 MCP ngoài Windows sandbox = **421/421** | [unit.log](unit.log), [unit.xml](unit.xml), [mcp.xml](mcp.xml) |
| Ruff | Lint toàn project và format 9 file Python mới/sửa đạt | Lệnh bên dưới |
| Cleanup | 0 user/0 job; dọn file probe; stack dừng, volume giữ lại | [clean-db-cleanup.json](clean-db-cleanup.json), [cleanup.json](cleanup.json), [persistence-cleanup.json](persistence-cleanup.json) |

Healthcheck ban đầu import dependency nặng và đôi lúc vượt timeout 5 giây. Probe cuối chỉ dùng
stdlib và module heartbeat nhẹ; snapshot cuối ghi nhận app/worker healthy, failing streak 0,
năm probe gần nhất đều exit 0. Heartbeat cập nhật từ worker loop kể cả trong lúc chạy job.
Pytest MCP cần chạy ngoài Windows sandbox do quyền named pipe; 420 test còn lại chạy offline.
Log PowerShell có thể bao dòng stderr Docker trong `NativeCommandError` dù exit code 0;
kết quả nghiệm thu dựa trên native exit code và assertion runtime.

## Cách chạy lại

Quickstart và hướng dẫn xử lý volume: [runbook Docker dev](../../../infra/docker/app/README.md).
Thay project name mỗi lần cần xác minh database mới, kiểm tra trước project chưa có volume.

```powershell
docker compose --env-file .env.docker.example --project-name agent-day78-new --progress plain build --pull --no-cache
docker compose --env-file .env.docker.example --project-name agent-day78-new up -d --no-build --wait --wait-timeout 180
docker compose --env-file .env.docker.example --project-name agent-day78-new exec -T app python -m scripts.check_compose_runtime
docker compose --env-file .env.docker.example --project-name agent-day78-new exec -T app python -m scripts.run_e2e_smoke --base-url http://app:8000
docker compose --env-file .env.docker.example --project-name agent-day78-new exec -T app alembic check
docker compose --env-file .env.docker.example --project-name agent-day78-new down
```

```powershell
.\.venv\Scripts\python.exe -m pytest -q --ignore=tests/test_mcp.py
.\.venv\Scripts\python.exe -m pytest -q tests/test_mcp.py
.\.venv\Scripts\python.exe -m ruff check agent_core backend migrations scripts tests
.\.venv\Scripts\python.exe -m ruff format --check backend/core/config.py backend/core/worker_health.py backend/background/worker.py backend/services/langgraph_checkpoints.py scripts/migrate_database.py scripts/run_background_worker.py scripts/check_container_health.py scripts/check_compose_runtime.py tests/test_container_health.py
```

## Phạm vi và trạng thái sau kiểm tra

- Đã dừng các project thử `agent-day78-20261018`, `agent-day78-20261018-clean`,
  `agent-day78-20261018-negative`; giữ named volume, image và Docker Desktop.
- Cổng 8000 được giải phóng. Stack dev mặc định chưa được tạo; người dùng chạy quickstart để mở.
- Không dùng API key thật, không chạy lại live LLM quality eval hoặc sandbox runner trong profile này.
  Python sandbox và các feature V2 đều tắt. Docker runner riêng thuộc M6; upload/RAG thuộc M3.
- Không thay `.env` hay dữ liệu PostgreSQL host. Run cũ gây critical ở baseline Ngày 76 vẫn cần
  đối soát trước release. 68 artifact baseline Ngày 76 được đối chiếu SHA-256 và giữ nguyên.
- Workspace vẫn chưa là Git repository; cấu hình repo đích/CI và lockfile tiếp tục ở Ngày 79.

Quyết định Compose được đối chiếu với [Docker startup order](https://docs.docker.com/compose/how-tos/startup-order/),
[Compose service reference](https://docs.docker.com/reference/compose-file/services/) và
[pgvector Docker](https://github.com/pgvector/pgvector#docker).
