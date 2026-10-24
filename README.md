# Personal AI Agent

Phiên bản hiện tại cung cấp đăng ký, đăng nhập, phân quyền và chat nhiều lượt với DeepSeek. Hội thoại, tin nhắn và usage token được lưu trong PostgreSQL.

Tài liệu chính:

- Kiến trúc và ranh giới hệ thống: [`ARCHITECTURE.md`](ARCHITECTURE.md).
- Runbook khởi động, E2E, backup/restore và xử lý sự cố: [`OPERATIONS.md`](OPERATIONS.md).
- Kịch bản demo và trình bày phỏng vấn: [`INTERVIEW_GUIDE.md`](INTERVIEW_GUIDE.md).
- Cài dependency từ lockfile và chạy CI: [`docs/DEPENDENCIES_AND_CI.md`](docs/DEPENDENCIES_AND_CI.md).
- Manifest, namespace/policy và catalog plugin: [`docs/plugins/README.md`](docs/plugins/README.md).
- Plugin API, grant/connection và credential reference theo user: [`docs/plugins/API.md`](docs/plugins/API.md).
- PluginManager, stdio profile, timeout và health: [`docs/plugins/STDIO_RUNTIME.md`](docs/plugins/STDIO_RUNTIME.md).
- HTTP MCP, SSRF và credential/OAuth theo owner: [`docs/plugins/HTTP_AND_OAUTH.md`](docs/plugins/HTTP_AND_OAUTH.md).
- Capability catalog, metadata search, discovery và schema budget: [`docs/plugins/CAPABILITY_CATALOG.md`](docs/plugins/CAPABILITY_CATALOG.md).
- Skill plugin, package pin và thu hồi quyền trong run: [`docs/plugins/PLUGIN_SKILLS.md`](docs/plugins/PLUGIN_SKILLS.md).
- UI Plugins/Connections theo user/admin: [`docs/plugins/PLUGINS_UI.md`](docs/plugins/PLUGINS_UI.md).
- DecisionProvider và Jev adapter chưa cấu hình: [`docs/routing/README.md`](docs/routing/README.md).
- Taxonomy 5 route và corpus Việt/Anh: [`docs/routing/TAXONOMY.md`](docs/routing/TAXONOMY.md).
- Query router rules trước graph (mặc định tắt): [`docs/routing/QUERY_ROUTER.md`](docs/routing/QUERY_ROUTER.md).
- Shortlist Skill/tool qua provider inject: [`docs/routing/CAPABILITY_SHORTLIST.md`](docs/routing/CAPABILITY_SHORTLIST.md).

## Chạy bằng Docker dev

```powershell
Copy-Item .env.docker.example .env.docker
docker compose --env-file .env.docker up -d --build --wait --wait-timeout 180
```

Chỉ copy config nếu `.env.docker` chưa tồn tại. Mở `http://127.0.0.1:8000`.
Stack gồm PostgreSQL/pgvector, migration one-shot, app và worker; dữ liệu dùng named volume riêng,
không nối tới PostgreSQL trên Windows. Không cần Python/.venv/API key trên host để khởi động.
Đặt `DEV_DEEPSEEK_API_KEY` trong config Docker nếu muốn chat với DeepSeek.
Hướng dẫn healthcheck, smoke test, restart và volume: [Docker dev](infra/docker/app/README.md).

## Chạy lần đầu trên Windows

1. Cài uv 0.13.1, Python và dependency theo lock:

   ```powershell
   uv python install
   uv sync --locked --extra dev --no-install-project --no-build
   ```

2. Copy `.env.example` thành `.env` nếu chưa có, điền `DB_PASSWORD` và key nếu cần chat DeepSeek.
3. Chạy migration:

   ```powershell
   .\.venv\Scripts\alembic.exe upgrade head
   ```

4. Khởi động ứng dụng:

   ```powershell
   .\.venv\Scripts\uvicorn.exe backend.main:app --reload
   ```

5. Mở `http://127.0.0.1:8000/register` để đăng ký, sau đó chọn **Mở cuộc trò chuyện**.

Nếu chưa điền `DEEPSEEK_API_KEY`, các trang và API nội bộ vẫn chạy nhưng khi gửi tin nhắn, giao diện sẽ báo chưa cấu hình API key.

## Luồng chat hiện tại

1. Frontend tạo hoặc chọn conversation thuộc user đang đăng nhập; backend kiểm tra quyền và quota.
2. Tin nhắn được lưu, sau đó context middleware ghép system, memory, history, Skill và tool theo token budget.
3. LangGraph chạy context → model → tool → model; không có tool call thì đi tới final.
4. Tool đi qua allowlist, role, approval, timeout/retry/idempotency; interrupt được resume từ PostgreSQL checkpoint.
5. Event LangGraph được map sang SSE để giao diện hiển thị nội dung và timeline theo thời gian thực.
6. Câu trả lời, token, cache hit, step, tool call, latency và lỗi đã redact được lưu để user/admin theo dõi.

## Tạo tài khoản admin

Sau khi migration đã chạy:

```powershell
.\.venv\Scripts\python.exe scripts\create_admin.py --email admin@example.com --name "Admin"
```

Script sẽ yêu cầu nhập mật khẩu mà không hiển thị trên màn hình. Sau khi đăng nhập bằng tài khoản đó, mở `http://127.0.0.1:8000/admin`.

## Kiểm tra mã nguồn

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m scripts.check_langgraph_checkpoint
```

Lệnh cuối tạo một run thử, gây lỗi có chủ đích rồi resume từ LangGraph PostgreSQL checkpoint.
Các bảng checkpoint chuẩn được tạo tự động khi ứng dụng khởi động.

## Dọn memory hết hạn

Memory hết hạn không được nạp vào context. Để xóa vật lý các bản hết hạn và không ghim:

```powershell
.\.venv\Scripts\python.exe -m scripts.purge_expired_memories
```

## Đánh giá Skill

```powershell
.\.venv\Scripts\python.exe -m scripts.evaluate_skills
```

Lệnh này đo tỷ lệ chọn đúng Skill và kiểm tra trajectory mẫu có tuân thủ tool,
giới hạn bước cùng output contract hay không. Benchmark chạy offline, không gọi DeepSeek.

## Trạng thái chạy Python

`run_python_code` đã có contract, sandbox và được đăng ký vào Tool Registry.
Để build image và kiểm tra network/filesystem isolation:

```powershell
docker build --tag personal-agent-python-sandbox:3.11-v1 infra/docker/python-sandbox
.\.venv\Scripts\python.exe -m scripts.check_python_sandbox
.\.venv\Scripts\python.exe -m scripts.check_python_sandbox_limits
.\.venv\Scripts\python.exe -m scripts.check_python_sandbox_artifacts
.\.venv\Scripts\python.exe -m scripts.check_python_tool_result
.\.venv\Scripts\python.exe -m scripts.check_python_sandbox_attacks
```

Xem `agent_core/code_execution/THREAT_MODEL.md` và `agent_core/code_execution/CONTRACT.md`.
Tool đã vượt qua attack test và được bật trong production registry. Mỗi lần chạy vẫn bắt buộc
user approval. Có thể tắt bằng `PYTHON_SANDBOX_ENABLED=false`.

## Background worker

Queue v1 dùng PostgreSQL và bảng `background_jobs`. Sau khi cập nhật code, chạy migration:

```powershell
.\.venv\Scripts\alembic.exe upgrade head
```

Khởi động worker ở terminal riêng:

```powershell
.\.venv\Scripts\python.exe -m scripts.run_background_worker
```

Enqueue maintenance job mẫu:

```powershell
.\.venv\Scripts\python.exe -m scripts.enqueue_background_job maintenance.purge_expired_memories
.\.venv\Scripts\python.exe -m scripts.enqueue_background_job maintenance.purge_expired_memories --idempotency-key purge-2026-09-28 --max-attempts 3
```

Hủy job bằng ID (job đang chạy sẽ dừng hợp tác ở lần kiểm tra gần nhất):

```powershell
.\.venv\Scripts\python.exe -m scripts.cancel_background_job <JOB_ID>
```

Kiểm tra queue mà không để lại job test:

```powershell
.\.venv\Scripts\python.exe -m scripts.check_background_queue
.\.venv\Scripts\python.exe -m scripts.check_background_queue_reliability
```

Retry chỉ xảy ra khi handler chủ động ném `RetryableJobError`; lỗi khác dừng ngay. Worker dùng
lease/heartbeat để khôi phục job nếu process chết giữa chừng. Cùng idempotency key và cùng nội dung
trả lại job cũ; cùng key nhưng khác nội dung bị từ chối.

Quyết định PostgreSQL/Redis được ghi tại `backend/background/DECISION.md`.

## Agent checkpoint và resume

LangGraph tự lưu `AgentState` sau từng super-step vào PostgreSQL checkpointer. Nếu model/provider lỗi,
timeline sẽ hiện nút **Tiếp tục từ checkpoint**. Resume dùng lại `thread_id` của cùng `agent_run`;
mỗi lần gọi model được lưu bằng `attempt_number` riêng. Checkpoint không thay thế idempotency của
tool: tool có side effect vẫn phải dùng approval và idempotency key riêng.

Kiểm tra checkpoint với PostgreSQL:

```powershell
.\.venv\Scripts\python.exe -m scripts.check_langgraph_checkpoint
```

## Rate limit, quota và chống lặp

Rate limit/quota dùng bảng PostgreSQL `usage_counters`, scope theo user:

- `AGENT_REQUESTS_PER_MINUTE=10`
- `AGENT_RUNS_PER_DAY=100`
- `AGENT_TOKENS_PER_DAY=1000000`
- `AGENT_TOOL_CALLS_PER_DAY=500`
- `AGENT_MAX_IDENTICAL_TOOL_CALLS=3`
- `AGENT_MAX_REPEATED_OBSERVATIONS=3`

Khi vượt request/run quota, API trả HTTP `429` và header `Retry-After`. Agent cũng dừng nếu
cùng tool call hoặc cùng observation lặp quá giới hạn cấu hình. Kiểm tra PostgreSQL:

```powershell
.\.venv\Scripts\python.exe -m scripts.check_usage_limits
```

## Eval dataset

Bộ eval version `1.0.0` gồm các case chat, tool, RAG và safety. Kiểm tra schema, ID và
rubric offline (không gọi DeepSeek):

```powershell
.\.venv\Scripts\python.exe -m scripts.validate_eval_dataset
```

Chạy một case qua agent thật và lưu kết quả vào PostgreSQL:

```powershell
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\python.exe -m scripts.run_evals --case-id chat_recall_conversation_context
```

Chạy toàn bộ bằng `python -m scripts.run_evals`. Lệnh eval thật gọi DeepSeek và sử dụng token API.
Kết quả nằm trong `eval_runs` và `eval_case_results`; xem thêm tại `evals/README.md`.

Xem pass rate, tool success rate và failure category của run mới nhất:

```powershell
.\.venv\Scripts\python.exe -m scripts.show_eval_metrics
```

Truyền thêm `<RUN_ID>` để xem một run cụ thể.

## Admin vận hành

Trang `/admin` dành riêng cho role `admin`, hiển thị token, model turn, agent run, tool call,
latency trung bình, lỗi gần đây và pass rate của các eval run. Có thể chọn hai eval run để so
sánh chênh lệch pass rate tổng, tool success rate, từng category và failure category.

Dashboard cũng hiển thị chi phí token ước tính, cache hit rate, latency P50/P95 và danh sách
agent run gần đây. Nhấn **Chi tiết** để xem từng step, model call và tool call của run. Drill-down
không trả nội dung chat, arguments hoặc output của tool.

Khu vực cảnh báo theo dõi failure rate, model P95, cache hit và agent run bị kẹt. Audit log ghi
thao tác xem chi tiết run, so sánh eval và chạy retention; dữ liệu nhạy cảm được redact trong
database log, console message và traceback.

Giá token được cấu hình theo USD/1 triệu token. Giá mặc định là mức peak của `deepseek-flash`
tại thời điểm triển khai; cần cập nhật `.env` khi nhà cung cấp đổi giá:

```text
DEEPSEEK_CACHE_HIT_PRICE_PER_MILLION_USD=0.006
DEEPSEEK_CACHE_MISS_PRICE_PER_MILLION_USD=0.30
DEEPSEEK_OUTPUT_PRICE_PER_MILLION_USD=1.20
```

Chỉ nên kết luận phiên bản tốt hơn khi hai run dùng cùng dataset fingerprint và cùng danh sách
case. Giao diện sẽ cảnh báo nếu dữ liệu so sánh không tương đương hoặc run chưa hoàn tất. Cần có
ít nhất hai lần chạy `scripts.run_evals` để dùng phần so sánh.

API dữ liệu:

```text
GET /api/admin/overview?days=7
GET /api/admin/agent-runs?days=7&limit=20
GET /api/admin/agent-runs/<RUN_ID>
GET /api/admin/audit-logs?limit=20
GET /api/admin/evals?limit=20
GET /api/admin/evals/compare?baseline_id=<UUID>&candidate_id=<UUID>
```

API không trả nội dung chat, arguments hoặc output của tool.

Kiểm tra retention mà không thay đổi dữ liệu:

```powershell
.\.venv\Scripts\python.exe -m scripts.purge_operational_data --dry-run
```

Áp dụng retention:

```powershell
.\.venv\Scripts\python.exe -m scripts.purge_operational_data
```

Mặc định: xóa nội dung lỗi và payload tool sau 30 ngày, agent run sau 90 ngày, background job
sau 30 ngày và audit log sau 180 ngày. Chat, message và eval dataset không bị lệnh này xóa.
