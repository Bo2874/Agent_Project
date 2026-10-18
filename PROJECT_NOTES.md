# Project Notes

## Chuẩn bị đưa source lên GitHub — 2026-10-18

- Đã mở rộng `.gitignore` cho `.env.*` (giữ hai file example), credential/private key,
  môi trường Python/tool local, cache/build output, dữ liệu runtime/upload/backup/database,
  log tạm, file editor/OS và PDF tham khảo tại root.
- Giữ `uv.lock`, `.python-version`, source/test/fixture/dataset, tài liệu và báo cáo regression
  đã redact để repo đủ dữ liệu tái lập/đối chiếu. Không ignore toàn bộ PDF hoặc JSON vì cần
  hỗ trợ fixture kiểm thử về sau.
- Đây là bước chuẩn bị Git; Ngày 79 vẫn đang dở, chưa có kết quả CI và chưa được tick hoàn thành.
- Kiểm tra bằng repository Git tạm: 36 đường dẫn private/generated bị loại đúng, 19 đường dẫn
  cần version vẫn được giữ. Rà 389 file ứng viên không thấy giá trị secret hiện có trong `.env`;
  đây là đối chiếu secret đã biết, không thay thế việc review nội dung trước khi push.

## Ngày 78 — Docker dev app/worker, pgvector và startup sạch — 2026-10-18

- Đã thêm `infra/docker/app/Dockerfile`: dependency stage và runtime chung, hai target app/worker,
  Python 3.11, UID/GID 10001; source/assets giữ dưới `/app` để đường dẫn hiện tại hoạt động.
- `compose.yaml` gồm postgres/migrate/app/worker; PostgreSQL 17 có pgvector 0.8.7, không publish DB port.
  App chỉ bind localhost. Hai named volume riêng: `postgres_data` và `document_data` (app/worker chia sẻ).
  Root filesystem read-only, `/tmp` tmpfs 64 MiB, drop capabilities, không mount Docker socket.
- `.dockerignore` allow-list không gửi `.env`, `.venv`, backup, PDF hay báo cáo vào build context.
  `.env.docker.example` dùng biến `DEV_*` riêng, không tự dùng credentials/API key PostgreSQL host.
  Khởi động offline không cần API key; memory extraction, tracing, sandbox và V2 đều tắt.
- One-shot `scripts.migrate_database` tạo vector extension, upgrade Alembic head và setup schema
  LangGraph; app/worker chỉ khởi động sau migration exit 0. Runtime Docker tắt checkpoint auto-setup,
  luồng host giữ mặc định true. Chạy lại migration không tạo schema drift.
- App health gọi readiness có kiểm tra DB; worker dùng heartbeat atomically từ execution loop,
  cập nhật cả khi đang xử lý job. Probe chỉ dùng stdlib để tránh timeout do import dependency nặng.
  Worker nhận SIGTERM/SIGINT để dừng nhận job, drain job hiện tại và đóng pool trong stop grace.
- Đã cold build `--pull --no-cache`, sau đó kiểm tra image cuối bằng project/volume mới hoàn toàn:
  Python 3.11.17, PostgreSQL 17.11, đủ 22 Alembic revision và checkpoint schema; app/worker healthy.
  Đây là mô phỏng môi trường máy sạch bằng Docker trên Windows hiện tại, chưa thử mọi OS/CPU.
- HTTP E2E/auth/conversation/isolation, memory list/update/pin/delete/isolation, worker job thật,
  vector SQL, graph → MCP calculate → final và PostgreSQL checkpoint/resume đều đạt.
  Graph dùng model fixture, không gọi provider; không có live LLM quality eval trong lượt này.
- Kiểm tra persistence: cùng job UUID/payload và file tài liệu qua `down`/`up`; worker đọc được
  volume chung. Cố ý migration exit 17 trên project riêng khiến app/worker không khởi động.
- Ruff lint/format đạt; 13 test mới về heartbeat/checkpoint ownership/config; regression offline
  420/420 (19,71 giây), MCP ngoài Windows sandbox 1/1 (7,48 giây), tổng 421/421.
  `pip check` và `alembic check` trong image đạt. 68 artifact baseline Ngày 76 giữ nguyên SHA-256.
- Đã dọn user/job/file thử, dừng các stack nghiệm thu, giữ image/volume; cổng 8000 được giải phóng.
  Không thay `.env` hoặc dữ liệu PostgreSQL host. Lockfile/CI thuộc Ngày 79; production runner thuộc M6.
- Hướng dẫn: [Docker dev](infra/docker/app/README.md). Bằng chứng, image ID và package Linux:
  [nghiệm thu Ngày 78](reports/checks/2026-10-18-day78/STARTUP.md).
  Đã tick Ngày 78 trong kế hoạch và chuyển task tiếp theo sang Ngày 79.

## Ngày 77 — Contract V2, feature flags và ADR — 2026-10-18

- Đã tạo `agent_core/contracts/`: `CapabilityDescriptor`, `RoutingDecision`, `RetrievalResult`,
  `EvidenceReference`, `TaskSpec`, task budget/input reference và V2 event envelope.
- Contract dùng Pydantic, `schema_version="1.0"`, forbid extra field, reject NaN/Infinity,
  strict counter, frozen attributes, revalidate nested model; hỗ trợ JSON round-trip và JSON Schema.
- Routing kiểm tra tool thuộc shortlist, ID không trùng và probability distribution hợp lệ.
  Contract không tự tra catalog/quyền; backend và ToolExecutor vẫn là nơi kiểm tra thực thi.
- Evidence giữ nguyên quote, ID document/version/chunk, page/section/offset và `trust=untrusted`.
  Task validate dependency cục bộ, output JSON Schema, acceptance criteria và budget; full-DAG
  validation/scheduler/quota reservation thuộc M4, chưa triển khai ở lượt này.
- Năm V2 event kind: routing_decided, retrieval_completed, plan_created, task_status, run_status;
  discriminated union, root/task/plan/attempt correlation, sequence dương, timestamp UTC.
  Event contract chưa thay thế AgentLoopEvent/AgentGraphStreamEvent hoặc SSE hiện tại.
- `Settings.v2_features` cung cấp snapshot FeatureFlags: master `V2_ENABLED` và 7 feature đều
  mặc định false, khai báo trong `.env.example`; không sửa `.env` chứa secret của người triển khai.
  Consumer dùng `is_enabled`; planning cần routing, parallel cần planning; cấu hình sai fail fast.
- Đã chốt [ADR 0001](docs/adr/0001-v2-contracts-and-layer-ownership.md) về ownership giữa
  API/auth/catalog/router/LangGraph/RAG/planner/executor/PostgreSQL/Redis/recorder/UI;
  có [tài liệu field, ví dụ và cách tích hợp](docs/contracts/README.md).
- Kiểm tra: Ruff toàn project đạt, format 11 file mới/sửa đạt; 65 test contract/config mới đạt;
  regression offline 407/407 đạt (10,83 giây), MCP ngoài sandbox 1/1 đạt (4,92 giây), tổng 408/408.
  Các test kiểm tra JSON/schema, invalid selection/provenance/budget/event và default/dependency/kill switch.
- Không cần DB migration hoặc live API eval cho thay đổi contract/config; baseline Ngày 76 giữ nguyên.
  Đã tick Ngày 77 và chuyển task tiếp theo sang Ngày 78. Cảnh báo run cũ của baseline vẫn cần xử lý trước release.

## Ngày 76 — Baseline trước V2 — 2026-10-18

- Đã chạy regression theo `OPERATIONS.md`; lưu báo cáo tại
  [Baseline Ngày 76](reports/baselines/2026-10-18-day76/BASELINE.md), kèm JSON, JUnit và log đã redact.
- Pytest: 342 test offline + 1 test MCP = 343/343 pass, không skip. MCP ban đầu bị Windows sandbox
  chặn named pipe (`WinError 5`); chạy lại ngoài sandbox đạt, giữ cả bằng chứng lỗi và lần pass.
- Skill eval: selection 13/13, adherence 2/2. Dataset schema: 20 case, 38 critical criteria hợp lệ.
- E2E HTTP/DeepSeek: health, đăng ký/đăng nhập, conversation, cô lập user, SSE và cleanup đều đạt.
- Eval smoke: 2/2 pass, mỗi case 100/100; model `deepseek-flash`, prompt `personal-agent-system-v2`.
- Latency hai case: P50 1.442,50 ms, P95 1.613,05 ms; agent 1.941 token, judge 1.032 token,
  tổng 2.973 token. Đây là số đo hai case chat/safety, chưa phải benchmark toàn dataset/tải production;
  chưa gồm token HTTP E2E. Run ID và fingerprint dataset/prompt nằm trong báo cáo.
- PostgreSQL 17.5, Alembic `20261004_0022` head; upgrade/check không schema drift.
  Backup/restore trên DB tạm và PostgreSQL checkpoint/resume đều đạt, dữ liệu thử được dọn.
- Docker Desktop đã được khởi động; Engine 28.3.0/Linux. Isolation, approval/tool result,
  resource quota, artifact và attack probes đều đạt khi chạy Docker ngoài sandbox Windows.
- Dependency: 26/26 dependency trực tiếp đúng `pyproject.toml`; `pip check` không có xung đột.
  Chưa có lockfile; đã lưu version từng distribution và SHA-256 mã nguồn để đối chiếu ở Ngày 79.
- Git CLI đã cài nhưng `C:\Agent_Project` chưa phải repository; chưa có commit SHA cho baseline.
- Admin còn cảnh báo critical `stuck_agent_runs`: một run tạo ngày 01/10, vẫn `running`, step 0.
  Chỉ đọc metadata và ghi nhận; chưa sửa/hủy run. Regression tự động đạt, release checklist chưa sạch.
- Server HTTP test đã dừng, cổng 8000 đã đóng; Docker Desktop vẫn chạy.
  Mã nguồn runtime ứng dụng được giữ nguyên; đã tick Ngày 76 và chuyển task tiếp theo sang Ngày 77.

## Lượt lập kế hoạch V2 — 2026-10-18 (trước Ngày 76)

- Đã rà soát `PROJECT_PLAN.md`, notes, kiến trúc, vận hành và các module MCP/tool/Skill/graph/queue.
- Đã đối chiếu các phần liên quan trong `Hiểu sâu về AI Agent.pdf` và tài liệu chính thức trên web.
- Đã bổ sung lộ trình V2, ngày 76–133, trong [PROJECT_PLAN.md](PROJECT_PLAN.md): phụ thuộc, contract,
  thiết kế kỹ thuật, bài đo, tiêu chí nghiệm thu và mapping phần RAG ngày 69–73 đang thiếu.
- Đây là cập nhật kế hoạch; plugin platform, Jev, RAG tài liệu, multi-agent, Redis cache và CI/CD V2
  chưa được triển khai trong lượt này. Chưa chạy lại release gate hoặc benchmark hiệu năng.
- Khi hoàn thành task tiếp theo, bổ sung kỹ thuật đã dùng cùng kiểm tra/kết quả và giới hạn còn lại;
  đồng thời cập nhật checkbox và mục Task tiếp theo trong kế hoạch.

## Công nghệ và kỹ thuật đã triển khai

- Kiểm thử release: Ruff, E2E HTTP/DeepSeek, safety regression và eval versioned
- Tài liệu: `ARCHITECTURE.md`, `OPERATIONS.md`, `INTERVIEW_GUIDE.md`

- Ngôn ngữ: Python
- Database: PostgreSQL
- API: FastAPI
- ORM: SQLAlchemy
- PostgreSQL driver: asyncpg và psycopg
- Migration database: Alembic
- Băm mật khẩu: Argon2 (pwdlib)
- Đăng nhập: JWT lưu trong cookie HttpOnly
- Giao diện: Jinja2, HTML, CSS, JavaScript
- Hiển thị câu trả lời: Markdown an toàn cho heading, list, table, quote, link và code block
- Phân quyền: user và admin
- Model LLM: DeepSeek (`deepseek-flash`)
- Tích hợp model: LangChain `ChatDeepSeek`
- Message model: LangChain System/Human/AI/Tool message
- Streaming câu trả lời: LangGraph custom stream → SSE `delta`
- Dữ liệu chat: conversations, messages, model_calls
- Context: system, Skill, summary/history gần đây, memory, status và tool schema; giới hạn theo token budget
- Theo dõi LLM: token, cache token, thời gian và trạng thái gọi
- Tối ưu thời gian trả lời: streaming và tắt thinking mode
- Kiến trúc tool: LangChain `StructuredTool`, Pydantic schema, ToolContext và Tool Registry
- Theo dõi agent: agent_runs, run_steps và tool_calls(lưu lại các thông số chi tiết đã chạy để hiển thị cho user, thống kê admin, debug...)
- Vòng lặp agent: LangGraph conditional edge model → tool → model; không có tool call → final
- LangGraph state: message, context, tool call, observation, usage và final answer
- LangGraph node: context, model, tool và final
- LangGraph retry: model lỗi provider được retry 1 lần nếu chưa stream nội dung
- LangGraph timeout: `TimeoutPolicy` giới hạn thời gian async node theo budget agent
- LangGraph node error: `error_handler` lưu node, mã lỗi và trạng thái failed vào checkpoint
- LangGraph cancel: hủy task truyền qua graph; checkpoint giữ super-step cuối để resume
- LangSmith tracing: opt-in cho cây LangGraph, model và tool; project cấu hình qua env
- LangSmith eval: đồng bộ dataset/rubric theo version và lưu experiment + feedback; PostgreSQL vẫn giữ audit nội bộ
- Trace tag: `run` và `user` dùng HMAC, không gửi UUID gốc
- Trace privacy: ẩn input/output; redact metadata, error, secret, email và identifier
- Trạng thái agent realtime: LangGraph custom stream → SSE `agent_status`
- Timeline agent: step/tool realtime và tải lại từ PostgreSQL
- Kết nối tool ngoài: MCP client qua stdio
- MCP server thử nghiệm: tool `demo__get_server_time`
- MCP tool nhỏ: tính toán, thống kê văn bản và đổi nhiệt độ
- Validation input tool: Pydantic; MCP JSON Schema được bọc thành Pydantic model
- Timeout/retry tool: 10 giây, retry 1 lần cho lỗi tạm thời của tool idempotent
- Idempotency tool: tái sử dụng kết quả trùng trong cùng agent run
- Tool allowlist: cấu hình qua env
- Phân quyền tool: user và admin
- User approval: LangGraph interrupt/resume, Cho phép/Từ chối trên timeline và lưu audit PostgreSQL
- Agent checkpoint: LangGraph PostgreSQL, `thread_id = agent-run:{run_id}`
- Resume agent: chỉ dùng LangGraph PostgreSQL checkpoint theo cùng `thread_id`; 
- Prompt injection: system rules, tool output gắn nhãn untrusted và quyền được chặn bằng code
- Safety test: vòng lặp, lỗi tool, từ chối quyền và direct/indirect injection
- Context assembly: LangChain `AgentMiddleware` và `System/Human/AI/ToolMessage`
- Memory context: LangGraph Store đọc PostgreSQL và lưu lựa chọn trong AgentState
- RAG context: đã có contract và budget, chưa có nguồn dữ liệu
- Context token: ước tính theo system, memory, history, tools và RAG; token thật lấy từ DeepSeek usage
- Context budget: 32.000 input token (system 2.000, memory 4.000, history 16.000, tools 6.000, RAG 4.000)
- Context trimming: bỏ turn cũ nhất, luôn giữ turn user hiện tại
- KV cache prefix: system prompt cố định, history append-only và tool schema sắp xếp ổn định
- Prompt cache: lưu hit token, miss token và hit rate theo model call
- Cache isolation: gửi UUID user vào DeepSeek `user_id`
- Tool selection: allowlist → role → relevance
- Lazy-load tool: trên 4 tool chỉ nạp tối đa 4 schema phù hợp
- Agent status block: mục tiêu, tiến độ, việc còn lại và budget step/token/thời gian
- Status safety: chỉ lưu trạng thái thực thi, không đưa suy luận nội bộ ra UI
- History compression: giữ 20 message gần nhất, mỗi 10 message cũ tạo summary phân tầng
- Summary version: `extractive-history-v2`, ưu tiên giữ tên, số, ngày, cấu hình và ràng buộc
- Test nén history: đo tỷ lệ giữ dữ kiện ở raw, summary tầng 1 và summary tầng 2
- Dữ liệu gốc: message cũ không bị xóa khi nén
- User memory: bảng `user_memories`
- Memory type: fact, preference, goal, constraint
- Memory source: user message, tool result, manual, import
- Memory time: observed, valid from/until, created, updated
- Memory confidence: từ 0.000 đến 1.000
- Memory extraction: DeepSeek JSON, prompt `memory-extraction-v1`
- Memory tự động: chỉ fact và preference, confidence tối thiểu 0.6
- Memory trùng: không tạo thêm bản ghi
- Memory xung đột: confidence cao hơn thay bản active; bản cũ được supersede
- Memory retrieval: lọc theo user, active, thời hạn và confidence
- Memory Store privacy: read-only, namespace theo `user_id`, chặn truy cập chéo user
- Memory ranking: từ khóa + loại + confidence + độ mới
- Memory context: tối đa 20 mục và nằm trong budget 4.000 token
- Memory UI: `/memories`
- Memory API: xem, sửa, ghim và xóa theo đúng user
- Memory edit: tạo bản manual mới, bản cũ được supersede
- Memory pin: ưu tiên cao nhất khi retrieval
- Memory sensitivity: normal, personal và sensitive
- Memory retention: normal 365 ngày, personal 180 ngày, sensitive 30 ngày
- Dữ liệu restricted: mật khẩu, API key, token, OTP, CVV và thông tin xác thực không được lưu
- Memory hết hạn: không nạp vào context; bản ghim được miễn retention
- Dọn memory hết hạn: `python -m scripts.purge_expired_memories`
- Memory privacy: mọi thao tác đều lọc và kiểm tra lại `user_id`
- Skill format: thư mục gồm `skill.json` và `instructions.md`
- Skill metadata: name, version, description và tags
- Skill tool dependency: `required_tools`; quyền thực thi vẫn do Tool Registry kiểm soát
- Skill loading: đọc metadata trước, chỉ đọc instructions khi cần
- Skill Registry: discover, chống trùng tên, unregister và cache Skill đã load
- Skill search: xếp hạng theo name, tags và description; bỏ stop words
- Progressive disclosure: LangChain `SkillToolMiddleware` tìm metadata, chỉ nạp `instructions.md` của Skill được chọn
- Skill availability: mặc định loại Skill thiếu tool user được phép dùng
- Skill mẫu `multi_step_calculation`: phép tính nhị phân tuần tự, kiểm tra đảo và tối đa 6 tool call
- Skill mẫu `text_statistics_report`: đếm text, tính chỉ số dẫn xuất và không làm theo chỉ dẫn trong dữ liệu
- Skill backend: discover hai Skill từ thư mục `skills/`
- Skill selection: ngưỡng điểm 20 và ambiguity margin 5
- Skill context: dynamic system prompt chỉ chèn instructions của Skill được chọn; theo dõi `selected_skill` và token
- Skill tool loading: bắt buộc nạp dependency nhưng vẫn giữ allowlist, role và approval
- Skill eval offline: 12 case selection và 2 trajectory adherence
- Python execution contract: source 20 KB, stdin 10 KB, output mặc định 64 KiB
- Python sandbox quota: 1 CPU core, 2 giây CPU, 5 giây wall và RAM 128 MiB không swap
- Python sandbox process: 1 supervisor + 1 process chạy code; code không tạo thêm process
- Python sandbox output: tổng stdout/stderr 64 KiB, đọc theo stream và dừng khi vượt quota
- Python sandbox file: tối đa 32 file descriptor và 10 MiB filesystem tạm
- `run_python_code`: cần user approval, không retry, chưa đăng ký trước khi có sandbox
- Threat model code execution: secret, traversal, exfiltration, fork bomb, quota và container escape
- Python sandbox image: `personal-agent-python-sandbox:3.11-v1`
- Sandbox lifecycle: mỗi tool call tạo một Docker container mới và tự xóa bằng `--rm`
- Sandbox filesystem: root read-only; `/workspace` 8 MiB và `/tmp` 2 MiB dùng tmpfs tạm
- Sandbox network: mặc định `none`, không mount host và không truyền secret
- Sandbox process: UID/GID 65532, drop mọi capability, bật `no-new-privileges`
- Sandbox check: `python -m scripts.check_python_sandbox`
- Sandbox runtime check: đạt `ROOT_READ_ONLY`, `NETWORK_BLOCKED` và `WORKSPACE_CLEAN`
- Sandbox quota check: `python -m scripts.check_python_sandbox_limits`
- Sandbox quota runtime: wall, CPU, RAM, process và output đều đạt
- Python tool result: stdout, stderr và artifact đều được đánh dấu `untrusted`
- Python artifact: chỉ `.txt`, `.md`, `.csv`, `.json` UTF-8; tối đa 3 file
- Artifact quota: 8 KiB mỗi file, 16 KiB tổng; có size và SHA-256
- Artifact safety: từ chối path lạ, symlink, binary, HTML, JSON lỗi và artifact dở dang
- Artifact directory: code ghi file vào biến môi trường `AGENT_ARTIFACT_DIR`
- Sandbox artifact check: `python -m scripts.check_python_sandbox_artifacts`
- Python tool result check: `python -m scripts.check_python_tool_result`
- Sandbox attack check: `python -m scripts.check_python_sandbox_attacks`
- Attack probes: host secret canary, root/capability/namespace escape, fork và external network
- Sandbox security: Docker seccomp mặc định, drop toàn bộ capability và `no-new-privileges`
- Attack runtime: secret read, container escape, fork bomb và network đều bị chặn
- Python tool bridge: `RunPythonCodeHandler` đã đăng ký production
- Python tool gate: allowlist + role user/admin + user approval; không retry
- Python sandbox config: có thể tắt bằng `PYTHON_SANDBOX_ENABLED=false`
- Background queue: PostgreSQL, bảng `background_jobs`
- Queue claim: `FOR UPDATE SKIP LOCKED`, ưu tiên priority rồi FIFO
- Worker: process riêng `python -m scripts.run_background_worker`
- Queue smoke test: enqueue, lưu result và single-consumer claim đều đạt
- Background handler đầu tiên: `maintenance.purge_expired_memories`
- Redis queue: chưa dùng; đánh giá lại khi có số liệu latency/throughput hoặc cần pub/sub
- Queue payload/result: JSON object tối đa 64 KiB
- Background cancel: job chờ hủy ngay; job chạy nhận cờ hủy hợp tác
- Background retry: chỉ lỗi `RetryableJobError`, exponential backoff, tối đa 5 lần chạy
- Queue idempotency: scope theo user/system, SHA-256 fingerprint và unique index
- Worker lease: heartbeat và tự đưa job mất lease về queue nếu còn lượt thử
- LangGraph checkpoint: PostgreSQL lưu state tự động sau mỗi graph super-step
- Agent resume: giữ message, tiến độ, token và node đang chờ theo cùng `run_id`
- Resume audit: giữ các lần chạy model bằng `attempt_number`, không ghi đè lần lỗi
- Resume UI: timeline lỗi có nút tiếp tục từ checkpoint
- Rate limit agent: PostgreSQL fixed window, mặc định 10 request/phút/user
- Quota ngày: 100 agent run, 1.000.000 LLM token và 500 tool call/user
- Token quota: tính cả model call của LangGraph và memory extraction
- Quota response: HTTP 429 kèm `Retry-After`; counter cập nhật nguyên tử
- Chống lặp agent: LangGraph router dừng khi cùng tool call hoặc observation lặp quá 3 lần
- Checkpoint chống lặp: lưu fingerprint và số lần lặp để resume không reset giới hạn
- Eval dataset v1: 20 case cho chat, tool, RAG và safety
- Eval rubric: 100 điểm/case, pass 80; tiêu chí critical bắt buộc đạt
- Eval runner: chạy agent thật và chấm deterministic + LLM judge
- Eval storage: PostgreSQL lưu dataset, model, system prompt và judge prompt version/fingerprint
- Eval metrics: pass rate toàn run và theo chat/tool/RAG/safety
- Tool metrics: success/execution; call bị quyền hoặc approval chặn tính riêng
- Failure category: ưu tiên lỗi execution hoặc criterion critical bị trượt
- Admin UI: `/admin`, lọc 24 giờ, 7 ngày hoặc 30 ngày
- Admin metrics: token, model turn, agent run, tool call, latency và lỗi
- Admin API: `/api/admin/overview`; chỉ role admin, không trả nội dung user/tool
- Admin eval: xem pass rate tổng, theo category, tool success rate và failure category
- So sánh eval: candidate trừ baseline; cảnh báo khác dataset, case hoặc run chưa hoàn tất
- Chi phí LLM: ước tính USD theo cache hit, cache miss và output; giá cấu hình qua env
- Cache dashboard: hit token, miss token và cache hit rate
- Latency dashboard: average, P50 và P95 cho model/tool
- Agent run drill-down: step, model call, tool call; không trả arguments, output hoặc nội dung chat
- Log redaction: che dữ liệu nhạy cảm trong database log, console message và traceback
- Audit log: bảng `audit_logs`; ghi xem run, so sánh eval và chạy retention
- Retention: xóa payload/error/run/job/audit cũ; hỗ trợ `--dry-run`
- Admin alert: failure rate, model P95, cache hit thấp và agent run bị kẹt
- E2E smoke: health, auth, conversation, cô lập user, cleanup và tùy chọn DeepSeek streaming
- Database backup: PostgreSQL custom dump kèm manifest SHA-256
- Restore verification: database tạm, đối chiếu record/Alembic revision và tự cleanup
- Tài liệu vận hành: `OPERATIONS.md`
