# Project Notes

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
- Audit log: bảng `audit_logs`; ghi xem run, so sánh eval và chạy retention
- Retention: xóa payload/error/run/job/audit cũ; hỗ trợ `--dry-run`
- Admin alert: failure rate, model P95, cache hit thấp và agent run bị kẹt
- E2E smoke: health, auth, conversation, cô lập user, cleanup và tùy chọn DeepSeek streaming
- V2 event: routing, retrieval, plan, task status và run status; correlation ID và timestamp UTC
- Feature flag V2: master switch và từng chức năng, mặc định tắt; kiểm tra dependency giữa flag
- Docker app/worker: multi-stage image, UID 10001, root read-only và healthcheck
- Docker volume: dữ liệu PostgreSQL và tài liệu lưu riêng, giữ qua recreate container
- GitHub Actions: đã gỡ workflow ngày 2026-10-24 theo yêu cầu user; kiểm thử chạy thủ công khi cần
- Plugin manifest: schema 1.0, exact SemVer, server/tool/Skill và credential slot logical
- Plugin namespace: canonical capability ID, alias riêng và giữ nguyên remote tool name
- Plugin policy: role, approval, side effect, timeout/concurrency và retry có điều kiện
- Plugin Skill validation: required tools tồn tại, policy không nới quyền của dependency
- Plugin admin API: đăng ký/bật/tắt plugin, cấp/thu hồi grant và gắn credential reference
- Plugin user API: tạo/xem/bật/tắt connection và thu hồi reference của chính mình
- Plugin grant: scope capability cụ thể, kiểm tra role hiện tại và required tools của Skill
- Connection ownership: owner từ auth session, composite foreign key chặn gắn sai user
- Credential reference: provider + UUID opaque; không nhận key/token thô hoặc trả secret ID
- PluginManager: quản lý nhiều stdio server, owner task riêng và startup song song
- Stdio timeout: startup/discovery, request, health và shutdown theo từng server
- Stdio concurrency: khóa call riêng từng server, semaphore chung theo plugin
- Discovery guard: giới hạn page/tool, chặn cursor lặp, trùng tên và thiếu tool khai báo
- Stdio isolation: server startup treo, timeout hoặc crash không làm hỏng server khác
- Stdio cleanup: đóng SDK đúng task sở hữu, thu hồi child process và owner task
- MCP HTTP adapter: Streamable HTTP qua SDK, tái sử dụng lifecycle và registry policy
- HTTP host profile: `plugins/http/servers.json`; mặc định rỗng, không chứa token/secret
- SSRF guard: exact URL, chặn redirect/proxy, kiểm tra mọi DNS answer và dial IP đã duyệt
- TLS: giữ Host/SNI gốc và certificate verification khi ghim IP
- OAuth public client: preregistered profile, authorization code, state TTL 10 phút và PKCE S256
- OAuth validation: issuer/resource/scope/expiry, state single-use và pin consent generation
- OAuth refresh: row lock chống refresh trùng, rotate refresh token; invalid grant yêu cầu reconnect
- Private HTTP cache: tắt memoization/retry của executor V1 để revoke không bị vượt qua bởi cached result
- Capability catalog: snapshot metadata không chứa schema/handler, revision và SHA-256 theo registry
- Metadata index: từ/phrase, kind/plugin/tag và chuẩn hóa dấu tiếng Việt, gồm đ/Đ
- Schema lazy loading: convert ứng viên được chọn, cache JSON theo registration và copy khi trả
- MCP schema: giữ nguyên type/required/enum/$defs/constraint khi tạo definitions cho model
- Schema token budget: fit cả danh sách definitions theo budget context; không cắt nội dung schema
- Skill schema budget: required tools phải vừa trọn nhóm, vượt budget fail trước model call
- Package validation: metadata/dependency khớp manifest, bounded UTF-8; chặn traversal và symlink escape
- Checkpoint pin: digest authority/schema/Skill; regrant không hồi sinh pending call của run cũn
- Plugins UI: `/plugins` theo owner, `/admin/plugins` theo admin; link từ dashboard/chat/bộ nhớ/quản trị
- User UI: tìm plugin được cấp, tạo/bật/tắt connection và trạng thái quyền/credential/version rõ ràng
- Admin UI: đăng ký/cập nhật manifest, bật/tắt toàn hệ thống và cấp/thu hồi grant theo capability
- Grant UI: chọn Skill kèm tool bắt buộc; API vẫn validate role/dependency và version snapshot
- UI states: loading/empty/feature-off/error; configured không xác nhận dịch vụ bên ngoài online
- Jev adapter: inject client async, chuyển Choice question và await `system_one`; caller quản lý lifecycle
- Decision validation: exact option IDs, distribution hữu hạn/tổng 1, selected có xác suất cực đại
- Query router rules Việt/Anh: sau runtime catalog theo quyền, trước chọn Skill/tool và chạy graph
- Route execution: chat không nạp tool/Skill; tool_task giữ executor/approval; clarify hoàn tất run không gọi model
- Jev OpenRouter live: SDK 0.7.5 và dependency backend trong `.venv` đều PASS; model `typesafe/jev-1.14-20261014` chọn calculator
- Jev runtime: lưu usage/token và model version thực; fallback LLM JSON, deadline tổng và circuit breaker dùng chung theo process
- Jev query preview (2026-10-22): một Choice bốn phương án từ taxonomy 2.0.0, query mới nhất và tối đa 20 message
  gần nhất gồm cả query (`CHAT_HISTORY_LIMIT`); giữ role/thứ tự, không gửi query hai lần.
- Query policy 2.0.0: argmax không ngưỡng; đồng hạng cao nhất chính xác chuyển chat bằng policy,
  giữ phân phối gốc và tied_routes; các xác suất gần nhau vẫn chọn phương án cao hơn.
- Preview web: `ROUTING_PREVIEW_ONLY=true` rẽ trước compression/plugin runtime/memory/graph;
  không khởi tạo DeepSeek, không LLM fallback hoặc tool call; lỗi Jev được trả riêng, không giả route.
- Preview giữ đủ bốn ý định kể cả RAG/planner chưa có lõi; tắt cờ phục hồi luồng thực thi cũ,
  chưa tự nối query router mới với agent. Chặn resume agent trong lúc bật preview.
- Query privacy: allowlist query/history user-assistant riêng; redact như capability, có giới hạn
  state/payload riêng, không cắt âm thầm input; trace không lưu query/history hoặc secret.
- Query trace: phase=query ghi model/usage/latency đo được, raw choice/phân phối và tie policy;
  token query được tính vào quota/admin, usage thiếu giữ null. Không thêm migration.
- Kiểm tra preview: 216 regression routing hiện có đạt; probe inline không tạo file xác minh
  SDK HTTP fixture, năm nhánh, argmax/tie, privacy, trace/usage và API chat/SSE không chạy agent.
  Consumer SSE thực trong `frontend/static/chat.js` nhận response preview đến hết `done` và render
  route/xác suất không lỗi JavaScript; giữ contract usage model-turn của UI hiện có.
  Chưa đo chất lượng/live mới; bộ 30 case để người dùng kiểm tra ở `docs/routing/MANUAL_JEV_CASES.md`.
- Điều chỉnh theo phản hồi: bỏ clarify khỏi Choice hiện tại, chat nhận yêu cầu thiếu dữ kiện/tham chiếu
  mơ hồ sau khi xét history; giữ mô tả tool_task/planned_task. Contract giữ đọc quyết định cũ năm route.
  Manual đổi sáu case hỏi rõ sang chat và thêm hai kịch bản nhiều lượt cùng hội thoại; không tạo file mới.
  Kiểm tra lại: lint/format và 216 regression đạt; probe inline xác minh bốn phương án,
  argmax thấp, đồng hạng về chat, gần đồng hạng, history, trace và SSE/usage. Chưa gọi Jev live.
- Người dùng xác nhận đã kiểm tra Jev routing ổn (2026-10-22); đây là nghiệm thu thủ công preview,
  chưa phải số liệu benchmark hoặc việc bật query router mới cho thực thi thật.
- Chat sidebar: grid/flex giới hạn chiều cao, hàng không co, danh sách có scrollbar riêng;
  tiêu đề dài rút gọn, giữ vị trí cuộn khi refresh và giữ phần tài khoản ngoài vùng cuộn.
- Xóa hội thoại: nút riêng có tên truy cập/xác nhận; DELETE `/api/conversations/{id}` theo owner.
  Khóa conversation/run/job khi kiểm tra, trả 409 nếu còn queued/running/waiting approval;
  dọn LangGraph checkpoint và job liên quan, PostgreSQL cascade lịch sử/trace/memory có nguồn.
- Sau xóa hội thoại đang mở, chọn hội thoại còn lại; xóa cuối cùng tạo hội thoại trống.
  Khóa thao tác hội thoại trong lúc stream/xóa/tạo để tránh thay nhầm phiên đang xử lý.
- Kiểm tra UI/xóa: lint/format, JavaScript syntax và 77 regression đạt. Chrome headless chạy
  template/CSS/JavaScript thật với 70 hội thoại, kiểm tra cuộn/hàng không chèn, tiêu đề dài,
  hủy xác nhận, API lỗi, xóa phiên khác/đang mở/cuối cùng và mở lại composer qua API fixture.
  Probe PostgreSQL/API kiểm tra 401/404/409/204, owner, cascade và checkpoint; fixture DB đã rollback.
  Không thêm file test/tài liệu hoặc migration; không xóa hội thoại thật của người dùng.
- Dòng nhắc dưới composer giữ cố định “AI có thể mắc lỗi. Hãy kiểm tra thông tin quan trọng.”;
  tách data-chat-status sang header (aria-live) để trạng thái xóa/stream/lỗi không ghi đè dòng nhắc.
- Sau nghiệm thu: gỡ stream/bảng route-xác suất và cờ preview khỏi code/config/.env;
  Jev phân query trước graph bằng query mới + tối đa CHAT_HISTORY_LIMIT message gần nhất.
  Chat dùng model/graph có sẵn, tool_task giữ selector/Skill/tool hiện có; hai nhánh chưa có lõi
  trả thông báo tương ứng. Chưa thay đổi Choice/policy chọn tool trong đợt này.
- Query production: budget/privacy/timeout riêng theo phase, giữ argmax/tie-to-chat và không LLM
  fallback cho phân query. Capability giữ budget/privacy/threshold/fallback hiện có.
- Pin route Jev cùng catalog và provider policy cho chat/tool checkpoint; resume dùng pin,
  chặn policy đổi trước model/tool. Query trace có usage thật, không ghi thêm bản summary trùng.
- Kiểm tra gỡ preview: lint/format và 216 regression đạt; probe inline kiểm tra bốn nhánh,
  chat không nạp tool, tool_task thực thi qua selector hiện có, RAG/planner chờ lõi,
  checkpoint/resume/policy pin và SSE trả câu trả lời không kèm bảng debug. Không gọi Jev/LLM live.
  Cập nhật manual hiện có, không tạo thêm file nguồn/test/tài liệu.

## M3 — Các phần triển khai sau khi chốt thiết kế (2026-10-24)

- M3.1 (đợt trước): thêm model/migration `20261024_0026` cho documents, document_versions,
  document_chunks, document_ingestion_jobs, conversation_documents và document_citations.
  Cột vector 1.024 chiều, GIN lexical, metadata nguồn/version và khóa ngoại ghép theo owner;
  thêm khóa ghép conversation/message. `pgvector` được khai báo trực tiếp và cập nhật lock.
  Chỉ kiểm tra tĩnh/SQL offline, chưa áp dụng migration hoặc nghiệm thu runtime trong đợt đó.
- M3.2: thêm private storage tại `data/documents/{owner}/{document}/{version}/original.ext`;
  UUID do server sinh, chặn đường dẫn ngoài root, quyền file riêng, không public static storage.
  Dọn file lỗi/hủy trước commit; file đã có khả năng commit được giữ khi lỗi kết nối làm kết quả
  transaction chưa rõ, tránh xóa nhầm file được tham chiếu. Đối soát orphan sau lỗi commit để sau.
- API có đăng nhập ở `/api/documents`: upload, list có phân trang/lọc tên/type/status, usage,
  detail/status, download theo version và delete. Mọi truy vấn dùng user đăng nhập, không nhận owner
  từ client và không có quyền đọc chéo user cho admin. API không trả storage key/đường dẫn nội bộ.
- Upload nhận PDF, DOC, DOCX, TXT; kiểm tra tên, MIME và nội dung container, từ chối file rỗng/quá lớn.
  DOC dùng olefile xác minh WordDocument; DOCX kiểm tra ZIP/OOXML, số entry, kích thước sau giải nén,
  tỉ lệ nén, XML và đường dẫn thành viên; defusedxml chặn DTD/entity/external reference. Không giải nén
  lên disk hoặc thực thi macro. TXT nhận UTF-8/UTF-16 có BOM. PDF mới kiểm tra header/EOF; kiểm tra
  page/encryption/scan và parse đầy đủ sẽ làm trong ingestion, chưa tích hợp OCR.
- Quota cấu hình: 20 MiB/file, 200 MiB/user, 1.000 tài liệu, DOCX tối đa 100 MiB sau giải nén và
  2.000 entry. Giới hạn request multipart áp dụng trước khi spool, kể cả không có Content-Length.
  Khóa user ở database khi tạo/xóa/cleanup để các upload đồng thời không vượt quota; dữ liệu đang
  chờ dọn vẫn tính dung lượng. SHA-256/type chỉ dedup trong thư viện của chính owner, trả 200 khi
  dùng lại bản mới nhất đã có; file mới trả 201. Có thể gắn vào hội thoại cùng owner khi upload.
- Delete trả 202 và đánh dấu deleting, bỏ active version/liên kết chat để ngừng truy cập ngay;
  ghi job `documents.cleanup` cùng transaction. Worker hiện có xử lý hủy ingestion, chờ job đang
  chạy dừng, dọn file và metadata cascade; retry I/O, xóa lại không tạo nhiều job đang hoạt động.
  Chạy worker để hoàn tất dọn và giải phóng quota; khi worker chưa chạy, tài liệu vẫn bị chặn truy cập.
- Upload còn trạng thái uploaded, ready_for_qa=false, chưa enqueue ingestion chưa có handler.
  Chưa thêm UI, parser RAG, embedding, rerank hoặc gọi model. Các bước tiếp theo giữ kế hoạch đã chốt:
  Docling/LibreOffice, LlamaIndex, F2LLM-v2-0.6B, Qwen3-Reranker-0.6B và PostgreSQL/pgvector.
- Thêm 4 file nguồn: services/document_storage.py, services/documents.py, schemas/document.py,
  routes/documents.py trong backend; cập nhật main/config, handler worker, env mẫu/Compose và lock.
  Cài olefile 0.47 theo uv.lock, khai báo trực tiếp defusedxml 0.7.1; không tải model ở bước này.
  Kiểm tra cú pháp/import/OpenAPI/lint/lock; chưa chạy test chức năng, gọi API/DB, áp dụng migration
  hoặc tạo test/fixture/báo cáo. User sẽ kiểm tra tay sau; tiếp theo làm M3.3 UI thư viện/upload/chọn file.
- M3.3 (2026-10-24): thêm trang thư viện riêng `/documents` có đăng nhập, liên kết từ dashboard/chat;
  tạo `frontend/templates/documents.html` và `frontend/static/documents.js`, cập nhật template chat,
  dashboard, CSS và chat.js. Không tạo tài liệu hoặc file kiểm thử riêng cho đợt này.
- Thư viện hỗ trợ upload nhiều file lần lượt bằng API M3.2, tiến độ truyền file thực qua XHR,
  quota/giới hạn mỗi file lấy từ server, tìm tên/lọc định dạng/trạng thái, phân trang và tải xuống.
  Hiển thị trung thực `uploaded`: file đã lưu nhưng chưa sẵn sàng hỏi đáp; chưa enqueue ingestion.
  Sau upload/delete làm mới danh sách và dung lượng; chỉ tự làm mới định kỳ khi có trạng thái đang
  xử lý/đang xóa. File đang xóa vẫn hiển thị và tính quota cho đến khi worker cleanup dọn xong.
- Upload trong chat đồng thời lưu vào thư viện và gắn vào hội thoại. Hộp chọn thư viện có tìm tên,
  lọc type và phân trang; giữ lựa chọn nháp qua các trang, chỉ áp dụng khi bấm lưu. Hủy hộp chọn không
  đổi lựa chọn; bỏ chọn file khỏi chat không xóa file thư viện. Chip file có thanh cuộn giới hạn chiều
  cao; trạng thái upload/lỗi có vùng riêng, không ghi đè dòng nhắc cố định bên dưới ô nhập chat.
- Bổ sung GET/PUT `/api/conversations/{conversation_id}/documents` trong routes/chat.py, schema
  ConversationDocumentSelection/ConversationDocumentsResponse và service đọc/lưu lựa chọn.
  Scope được lưu bền trong conversation_documents và tải lại khi chuyển hoặc mở lại hội thoại.
  API không nhận owner từ client, kiểm tra hội thoại và tất cả file thuộc user, bỏ file đã thu hồi;
  nếu có file không khả dụng thì không thay đổi một phần danh sách. Giới hạn 1.000 ID, từ chối ID trùng.
- Lưu lựa chọn/upload gắn hội thoại dùng khóa user rồi conversation, kiểm tra AgentRun đang hoạt động
  và trả 409 nếu hội thoại bận. UI khóa gửi tin/chuyển/tạo/xóa hội thoại khi upload/lưu hoặc mở hộp chọn;
  khóa thao tác tài liệu trong lúc stream. Xóa file thư viện thu hồi liên kết ở mọi hội thoại theo API
  M3.2; xóa hội thoại chỉ cascade liên kết, giữ tài liệu thư viện. Lỗi/mất phản hồi không tự gửi lại
  upload/PUT/delete; làm mới để đọc trạng thái server, tránh nhân đôi tác vụ do retry mù.
- Kiểm tra cú pháp JS bằng node --check, Python/Jinja, Ruff lint/format, import/OpenAPI và biên dịch
  truy vấn PostgreSQL offline. Chưa chạy test chức năng, mở web, gọi API/DB, áp dụng migration hoặc
  tải model; không sửa .env và không thêm dependency. Chưa nối file được chọn vào JEV/LLM/retrieval.
  Tiếp theo M3.4: worker ingestion, parser/chuẩn hóa/chunk có nguồn; người dùng tự test trực quan sau.
- M3.4 (2026-10-24): thêm 6 file nguồn: agent_core/rag/__init__.py, documents.py, parsing.py,
  chunking.py; backend/services/document_ingestion.py; scripts/process_document.py. Đây là pipeline
  nội bộ, không đăng ký parser/chunker làm tool và không thay đổi Choice/JEV ở đợt này.
- Upload mới tạo job documents.prepare và DocumentIngestionJob cùng transaction với document/version;
  tái upload file uploaded cũ hoặc POST /api/documents/{id}/retry cũng có thể enqueue. Dùng queue
  PostgreSQL và worker hiện có, không tạo scheduler/queue thứ hai; tối đa 3 lần thử cho lỗi tạm thời.
  Lỗi nội dung/thiếu dependency/giới hạn được báo để user chủ động thử lại sau khi sửa nguyên nhân.
- Worker gọi parsing rồi chunking bằng subprocess Python, kiểm tra checksum/size file gốc; có timeout
  theo phase, theo dõi tổng RSS của cây process và dừng process con khi hủy/quá hạn/vượt bộ nhớ.
  Watchdog trong child dừng cả các tiến trình Office con khi worker cha chết. Chỉ truyền môi trường
  tối thiểu, không đưa secret DB/SMTP/LLM/JEV vào parser; vẫn không coi process riêng là OS sandbox.
- Vùng tạm nằm trong private storage theo owner/document/version/attempt; scratch/profile Office
  nằm trong attempt, cache model nằm riêng. Dọn attempt sau hoàn tất/lỗi/hủy; delete tài liệu dọn
  cả cây staging của owner/document sau khi job chạy đã dừng. Resolve/kiểm tra đích trước recursive
  cleanup; không đụng file user khác. parsed.json được publish nguyên tử, chế độ file riêng.
  Cleanup cũng dọn parsed.json có thể đã được ghi trước COMMIT chưa rõ kết quả.
- PDF dùng pypdf preflight số trang, mã hóa và lớp text rồi Docling slim đọc layout/table; OCR,
  remote service và external plugin tắt. PDF không lớp text/mã hóa báo rõ; trang không/ít text, ảnh
  và trang không có text sau parse có cảnh báo. Không chấp nhận Docling partial/failed như đọc đủ.
  Kiểm tra ít text là cảnh báo coverage, không phải phương pháp phát hiện ảnh scan hoàn hảo.
- DOC dùng LibreOffice headless, profile riêng với macro security cao và link update tắt, chuyển
  sang DOCX; xác minh output/giới hạn archive rồi Docling đọc. DOCX bật giới hạn unpack/member và
  tắt remote/local resource fetch. TXT nhận UTF-8/UTF-16; giữ text/offset, heading dạng # nếu có.
  Chưa hỗ trợ OCR; không đưa ảnh hoặc nội dung tài liệu lên dịch vụ parse/model từ xa.
- Bản trích giữ original_text, block/heading path, PDF page, source ref và offset tính theo text
  trong parsed.json. Bảng biểu diễn TSV theo hàng/cột, giữ header/row metadata; rich cell serialize
  trước khi đưa vào bảng, không lặp subtree lần nữa. Header/footer vẫn giữ ở artifact, bỏ khỏi chunk.
- Chunker dùng LlamaIndex SentenceSplitter, tokenizer thực của codefuse-ai/F2LLM-v2-0.6B;
  resolve revision thành commit trước khi load tokenizer, trust_remote_code=false. Giới hạn 512 token
  gồm title/heading/table header và special token; overlap tối đa 64 token cho mục dài, không qua
  heading/bảng không liên quan. Bảng nhóm hàng và lặp header ở context; hàng quá dài tách có cờ fragment.
  Header/title quá dài rút gọn context và cảnh báo, metadata/bản trích vẫn giữ đầy đủ.
- Chuẩn hóa Unicode NFC/khoảng trắng cho embedding_text/lexical_text, không sửa đoạn trích làm nguồn.
  Kiểm tra từng chunk khớp substring/checksum của original_text; offset không tính trên bản chuẩn hóa.
  Chunk ID UUID5 ổn định theo version/pipeline/index/hash; thay chunk trong transaction, không nhân đôi
  khi retry. Lưu parser/chunker version, tokenizer commit và pipeline hash gồm owner/file/config.
- Thêm POST retry/cancel có đăng nhập và ownership; UI thư viện có Xử lý/Thử lại/Dừng xử lý,
  hiển thị số chunk/cảnh báo và tự làm mới trạng thái trong chat. Job bị mất lease/failed/cancelled
  được đồng bộ sang trạng thái tài liệu khi đọc API; background worker bỏ ghi kết quả khi mất quyền.
  Các lần ghi ingestion kiểm tra worker/attempt/started_at/lease và lấy khóa theo owner/document/job.
- Preparation hoàn tất ở stage chunking: DocumentIngestionJob succeeded, document/version embedding,
  awaiting_embedding=true, vector còn NULL, active_version/ready_at chưa đặt, ready_for_qa=false.
  UI hiện đã trích/chia xong nhưng chưa sẵn sàng hỏi đáp; chưa chạy embedding, lexical index hoặc retrieval.
- Thêm cấu hình giới hạn: 500 trang, 5 triệu ký tự text, 20.000 chunk, 128 MiB/artifact, 600 giây/phase,
  4 GiB RSS/cây process; có thể chỉnh trong env mẫu. Thêm đường dẫn LibreOffice/cache model/revision.
  Khóa extra ingestion với docling-slim 2.142.1 (PDF/Word/local models), pypdf, transformers và PyTorch
  CPU từ index riêng; không kéo CUDA/OCR. Base thêm psutil 7.2.2; môi trường local chỉ cài base/dev,
  chưa cài extra ingestion/LibreOffice hoặc tải model weights. Tokenizer và Docling layout/table model
  sẽ được tải vào cache khi chạy xử lý lần đầu; hướng dẫn Windows đã thêm vào README Docker hiện có.
- Docker worker cài extra ingestion/LibreOffice Writer, có volume document_model_cache riêng;
  app/migrate giữ dependency nhẹ. Cập nhật Dockerfile/Compose/env mẫu, không sửa .env thật.
- Kiểm tra cú pháp Python/JS, Ruff lint/format, import ứng dụng/registry, OpenAPI, YAML/TOML/lock
  và SQL compile offline; không tạo/chạy test chức năng, fixture/eval, xử lý tài liệu thật, gọi API/DB,
  build/chạy Docker hay áp dụng migration. User tự nghiệm thu tay sau. Tiếp theo M3.5 embedding/index.
  API Docling/LibreOffice và tokenizer đã đối chiếu nguồn chính thức:
  [Docling SDK](https://github.com/docling-project/docling/blob/v2.142.1/docling/document_converter.py),
  [LibreOffice CLI](https://help.libreoffice.org/latest/en-US/text/shared/guide/start_parameters.html),
  [F2LLM](https://huggingface.co/codefuse-ai/F2LLM-v2-0.6B).
- M3.5 (2026-10-24): thêm 4 file nguồn: agent_core/rag/embeddings.py,
  scripts/run_embedding_model.py, backend/services/document_indexing.py và document_retrieval.py.
  Không tạo test/fixture/tài liệu mới; không sửa .env thật hoặc tạo migration mới.
- Provider F2LLM local qua JSON-line IPC, subprocess CPU float32 load lazily và giữ model giữa các
  batch/query trong mỗi app/worker process; cache weights dùng chung trên disk. Chỉ giữ một revision
  trong RAM mỗi provider; đổi revision thì dừng model cũ. Không tải model vào process web chính.
  Shutdown app/worker đóng model; child dùng watchdog cha hiện có, không nhận secret DB/mail/provider.
- Dùng SentenceTransformer encode_query với instruction chính thức của F2LLM và encode_document
  với prompt rỗng; kiểm tra last-token pooling, 1.024 chiều, normalize và vector finite/norm hợp lệ.
  Pin commit từ tokenizer M3.4, trust_remote_code=false, safetensors và token=False cho repo public.
  Profile lưu model/revision/recipe/dimension/dtype/normalized và phiên bản Sentence Transformers.
  Không giảm chiều hoặc âm thầm truncate: document tối đa 512 token, query tối đa 1.024 gồm prompt.
- Giới hạn mặc định batch 4 (cấu hình 1–32), 600 giây/call, 4 GiB RSS/cây process; IPC tối đa 2 MiB,
  tối đa 8 caller đang encode/chờ trong mỗi provider. Serialize inference bằng lock, hủy/timeout/lỗi
  dừng model để không nhận nhầm response cũ; lần gọi sau có thể load lại. Không cache nội dung/query
  hoặc ghi text/vector ra log/file IPC. Chưa có giới hạn RAM/inference toàn cụm khi chạy nhiều process.
- Nối tiếp documents.prepare trên cùng background job/lease: parsing → chunking → embedding → indexing.
  Sau chunking không dừng ở awaiting_embedding nữa. Embedding lưu theo batch và cập nhật progress;
  retry/cancel rồi retry giữ bản trích/chunk/vector hợp lệ, chỉ chạy các vector còn thiếu. Profile khác
  khiến toàn bộ vector của version chưa công bố bị reset để dựng nhất quán, không trộn implementation.
- UI thư viện có Tiếp tục xử lý cho file awaiting_embedding từ M3.4; retry enqueue lại cùng version.
  Ready hiển thị Đã xử lý. ready_for_qa chỉ true khi đúng version ready đang active; nhánh trả lời
  document_qa vẫn chưa nối. Re-index ready không mở ở API retry đợt này, sẽ làm trong M3.8.
- FTS dùng pg_catalog.simple + public.unaccent/lower; title/heading weight A, lexical body B.
  Ghi tsvector tường minh vào document_chunks.search_tsv, tận dụng GIN đã có từ migration 0026.
  Kiểm tra đủ vector không NULL/norm đúng và số row FTS đã ghi trước khi công bố active version/ready;
  commit publication nguyên tử, ghi ready_at/profile/lexical config/pipeline hash và tăng doc revision.
  Exact cosine là baseline, chưa tạo hoặc tuning HNSW. Không đổi schema/migration.
- API POST /api/documents/search có auth: query, document_ids hoặc conversation_id (chỉ một scope),
  mode dense/lexical, limit mặc định 20/tối đa 50. Không có scope/file chưa ready thì báo rõ, không
  tự tìm cả thư viện. Không nhận owner từ client; kiểm tra hội thoại và toàn bộ ID trước khi gọi model.
  SQL lọc owner ở document/version/chunk, document còn tồn tại/ready và đúng active version.
- Dense dùng cosine distance pgvector, trả similarity = 1 - distance, giữ scope/version/profile sau
  model; giải phóng connection trong lúc inference rồi xác minh lại. Scope thay đổi thì báo gửi lại,
  không trả kết quả trộn version. Các file khác profile/revision/library không tìm dense chung.
- Lexical giữ tối đa 16 term, ưu tiên từ trong ngoặc kép/mã số, bỏ một số từ chức năng và nối OR;
  term được rút chỉ từ chữ/số, không nhận toán tử tsquery từ user. unaccent nhất quán lúc index/query,
  rank bằng ts_rank_cd normalization 32, không gọi là BM25. Đây là heuristic baseline để user test sau.
- Retrieval trả RetrievalResult với chunk/document/version ID, citation ID nguồn ổn định, title,
  text gốc, page/section/offset, score_kind và retrieval_config_version; trust=untrusted.
  Chưa fusion/hybrid, rerank, rewrite, đóng evidence context, sinh câu trả lời hay lưu citation message.
- Thêm extra embedding và Sentence Transformers vào ingestion, khóa sentence-transformers 5.7.0
  cùng dependency trong uv.lock; giữ PyTorch CPU. Docker app cài embedding, worker cài ingestion,
  migrate chuyển sang target migrate chỉ base; app/worker cùng mount document_model_cache.
  Cập nhật env mẫu, Dockerfile/Compose/README hiện có. Chưa cài extra nặng hoặc tải model weights.
- Kiểm tra Python AST/JS syntax, Ruff lint/format, import app/worker registry/OpenAPI, TOML/YAML/lock
  và compile SQL index/dense/lexical offline. Không chạy test chức năng, gọi DB/API/UI, xử lý file
  thật, build Docker hoặc áp dụng migration; người dùng tự test trực quan sau. Tiếp theo M3.6.
  Nguồn đối chiếu:
  [F2LLM usage/prompt](https://huggingface.co/codefuse-ai/F2LLM-v2-0.6B),
  [F2LLM pooling](https://huggingface.co/codefuse-ai/F2LLM-v2-0.6B/blob/main/1_Pooling/config.json),
  [PostgreSQL FTS](https://www.postgresql.org/docs/17/textsearch-controls.html),
  [pgvector](https://github.com/pgvector/pgvector#querying).

## 2026-10-24 — M3.6: hybrid/RRF, Qwen rerank, history rewrite và evidence budget

- Tiếp tục từng phần nhỏ theo yêu cầu; giữ các thay đổi M3.5 đang có trong working tree,
  không commit/push thay user. Không tạo file test hoặc tài liệu hướng dẫn mới.
- Thêm đúng 5 file: `agent_core/rag/local_models.py`, `agent_core/rag/reranking.py`,
  `agent_core/rag/retrieval.py`, `scripts/run_reranker_model.py`,
  `backend/services/document_query.py`. Sửa embedding client để dùng transport chung,
  search service/schema/API, config/dependency factory/lifespan, env mẫu, Compose,
  README Docker và PROJECT_PLAN.md. Không sửa bí mật trong .env hoặc thêm thư viện.
- Transport LocalModelClient tách từ F2: process lazy/reuse, model revision/profile,
  JSON-line IPC 2 MiB, serialized request, tối đa8 caller gồm cả đang chạy/chờ,
  timeout/RSS, kill khi hủy/lỗi và parent watcher. Child chỉ có env runtime/cache tối thiểu,
  không nhận DB/mail/DeepSeek credential. App shutdown đóng cả embedding và reranker.
- Qwen/Qwen3-Reranker-0.6B dùng Transformers/PyTorch CPU float32, safetensors,
  trust_remote_code=false; mặc định batch1, 4 GiB RSS, 600 giây/request.
  Revision nhánh resolve về commit lúc load, cố định trong process/profile; revision SHA
  dùng trực tiếp để có thể tận dụng cache offline. Hai model có thể cùng ở RAM;
  giới hạn mỗi process không phải RAM tổng hệ thống. Chưa đo RAM/latency thực tế.
- Scoring giữ template system/user/assistant + think rỗng của Qwen; instruction cho document QA,
  logits yes/no rồi softmax lấy yes. Dùng API logits_to_keep=1 để chỉ tạo logits token cuối,
  use_cache=false. Giới hạn mỗi cặp 2.048 token, không cắt ngầm nội dung đầu vào.
  Rerank nhận embedding_text (có title/heading, chuẩn hóa từ M3.4); đoạn nguồn trả về vẫn
  là text gốc, page/section/offset không đổi. Điểm chỉ dùng xếp hạng, không phải confidence đã hiệu chuẩn.
- Hybrid: dense top20 và lexical top20 độc lập, không dùng lexical lọc ứng viên dense;
  RRF cùng trọng số với k60, gộp trùng theo version/chunk ID, lấy tối đa20 ứng viên Qwen.
  Khi điểm rerank bằng nhau giữ thứ tự fusion. Đây là pipeline cố định, không thêm agent loop
  hoặc fallback bỏ rerank nếu model lỗi. PostgreSQL FTS hiện tại vẫn là lexical, không gọi là BM25.
- Evidence nhắm6, tối đa8 đoạn theo CONTEXT_RAG_TOKEN_BUDGET hiện có (mặc định4.000);
  slot7–8 ưu tiên file chưa được đại diện. Đo cả metadata/JSON/wrapper bằng estimator context
  utf8_bytes_v1. Bỏ qua whole chunk không vừa budget, không cắt text rồi giữ offset sai.
  Nếu có ứng viên nhưng không đoạn nào vừa thì trả document_evidence_budget rõ ràng.
  Đây chưa phải tóm tắt có độ phủ toàn tài liệu hoặc truy vấn nhiều ý — làm ở M3.7.
- Rewrite: không history thì không gọi LLM. Có history hội thoại đã xác thực owner thì gọi
  model chat hiện có đúng một lần; prompt JSON yêu cầu standalone query, giữ ngôn ngữ/số liệu/
  phủ định/các ý, giữ nguyên nếu đã rõ, không trả lời hoặc gọi tool.
  History tối đa8 message, budget2.000 token ước lượng; message dài rút gọn có marker,
  giữ chuỗi gần nhất và cờ history_trimmed. Metadata file cũng bounded/có cờ rút gọn,
  không gửi toàn bộ tài liệu vào rewrite. Timeout mặc định30 giây, output bounded/validate JSON;
  lỗi trả document_rewrite_failed, không âm thầm đổi về query khác.
- Caller document_qa ở M3.7 có thể truyền current_message_id để loại câu hiện tại khỏi history
  và callback nhận trace/status. API search không tạo message/run/citation giả; callback chỉ
  ghi trace, không cộng token lần hai. Token provider đã báo được cộng UsageCounter hiện có
  kể cả khi JSON lỗi/hủy; kiểm quota trước call. Chưa có token usage thì không tự ước lượng phí.
- POST /api/documents/search mặc định mode=hybrid; vẫn hỗ trợ dense/lexical với limit riêng.
  Hybrid cố định20+20/top20 rồi budget6–8 nên không dùng limit của baseline; schema mô tả rõ.
  API search áp dụng rate limit agent_request hiện có. Response có query gốc/rewrite,
  prompt version/model/usage/latency, profile Qwen, candidate counts, evidence budget và
  fingerprint retrieval config. Việc lưu toàn bộ trace/citation vào run/message làm ở M3.7.
- Scope vẫn chỉ file của user/đang chọn/active version ready; snapshot và kiểm tra lại sau
  rewrite/embedding/rerank, giải phóng DB transaction trong lúc chạy model. Tài liệu bị xóa,
  đổi scope/version/config thì không trả evidence cũ. Không mở quyền đọc file giữa user.
- Thêm config/env mẫu cho revision/batch/RSS/timeout Qwen và history/timeout rewrite,
  truyền qua Compose; dùng lại extra embedding/ingestion có Transformers và PyTorch CPU.
  Chưa cài extra nặng, tải model, chạy database/migration/API/web hoặc build Docker.
- Kiểm tra tĩnh đã đạt: Ruff; AST 17 file Python đang thay đổi/thêm, TOML/Compose YAML;
  import app/OpenAPI không chạy lifespan; biên dịch SQL owner-scoped chunk/history offline;
  uv lock --check --offline. Không tạo/chạy test chức năng hoặc benchmark/eval.
  Người dùng tự kiểm thử trực quan sau; bước tiếp theo M3.7 nối document_qa và citation UI.
  Nguồn đối chiếu:
  [Qwen template/scoring](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B),
  [Transformers Qwen3 logits_to_keep](https://github.com/huggingface/transformers/blob/v4.57.6/src/transformers/models/qwen3/modeling_qwen3.py).

## 2026-10-24 — M3.7: document_qa, hỏi nối tiếp/nhiều ý/tóm tắt và citation trên web

- Tiếp tục phần kế tiếp sau M3.6, giữ yêu cầu triển khai từng phần để user review/commit/push.
  Không tạo/chạy test chức năng, fixture/eval hoặc browser test; không đọc/sửa secret .env.
- Thêm 3 file: agent_core/rag/qa.py, backend/services/document_qa.py và migration
  migrations/versions/20261024_0027_document_qa_trace.py. Migration thêm agent_runs.rag_trace JSONB
  nullable với check object; chưa áp dụng DB. Sửa contract score_kind để có coverage cho full-scope
  summary; không gán điểm coverage thành điểm cosine/rerank giả.
- Sửa graph runner bằng handler tùy chọn cho document_qa; handler chạy LangGraph plan → retrieve
  → answer, không có orchestration framework thứ hai. Chat/tool_task giữ workflow thực thi cũ,
  planned_task vẫn stub. RAG chỉ hoạt động khi master/routing/rag flags bật và JEV active;
  Compose đã nhận DEV_V2_RAG_ENABLED thay vì hard-code false, vẫn mặc định tắt.
- JEV query input bổ sung title/status của tối đa20 file đang chọn và tổng số file, không truyền
  nội dung tài liệu. Instruction nhắc có file không làm mọi query thành document_qa.
  Query policy 2.1; giữ khả năng resume chat/tool checkpoint đã pin policy 2.0.
- QA có call lập truy vấn JSON: lấy history owner-scoped/bounded từ M3.6, loại message hiện tại,
  resolve tham chiếu khi có history, giữ query chính nguyên văn khi không history; tách tối đa3 ý
  và chọn qa hoặc summary. Đây là call lập truy vấn ngay cả khi không history, khác API search
  M3.6 vẫn bỏ call rewrite nếu không history. File metadata bounded20/title200 ký tự/cờ rút gọn.
- QA thường chạy hybrid M3.6 cho từng ý với explicit document_ids; không đưa history vào lại
  để tránh gọi rewrite lần hai. Xen kẽ kết quả của các ý, khử trùng chunk, đóng context theo budget;
  lưu số evidence của từng ý để model nói rõ ý nào thiếu dữ liệu. Không dùng history làm nguồn
  factual cho nội dung file và không trích memory từ nội dung tài liệu.
- Summary đọc mọi chunk thuộc các active version trong scope, tối đa64; vượt giới hạn báo rõ
  chọn ít file hơn/hỏi chủ đề cụ thể. Vừa RAG budget thì trả lời trực tiếp; không vừa thì chia batch,
  model tạo notes có label nguồn, gộp/thu gọn nhiều tầng đến khi vừa rồi sinh câu trả lời cuối.
  Giữ source labels của từng batch/note; coverage trace có loaded/processed chunk count và số tầng.
  Không dùng top-k thay cho toàn file hoặc tự nhận tóm tắt đủ khi budget chặn xử lý.
- Dùng budget/quota hiện có: max_steps (số call QA), max_total_tokens, max_duration_seconds;
  mặc định8 call/20.000 token/120 giây. Vượt giới hạn trả lỗi rõ, usage thực đã báo vẫn được ghi.
  Chưa đo độ trễ cold/warm cache, RAM, giới hạn tài liệu hay chất lượng đa ý/tóm tắt trên dữ liệu thật.
- Model QA không nhận tool/Skill. Output JSON gồm answer/sources/answerable; validate schema
  strict và nhãn [S<number>] phải khớp sources, nằm trong evidence đã cấp. Không cho nhãn citation
  tự gắn URL. Thiếu dữ liệu thì không suy đoán, answerable=false/sources rỗng.
  Đây là kiểm tra cấu trúc/binding, không phải evaluator chứng minh từng claim đúng ngữ nghĩa.
  Chỉ phát answer sau validation; timeline SSE vẫn phát model started/completed/failed, không
  đưa JSON chưa kiểm tra lên bubble. Chưa có durable QA checkpoint/resume; lỗi thì gửi lại câu hỏi.
- Dùng AgentRunRecorder/ModelCall/RunStep và UsageCounter hiện có cho từng call, gồm token đã báo
  khi output lỗi/hủy; cập nhật recorder để giữ usage ở model_failed và trạng thái cancelled.
  Callback QA tự charge đúng một lần, không đi qua quota handler của vòng tool để tránh cộng đôi.
  rag_trace lưu query/plan/history count, retrieval config/profile, evidence IDs/metadata, coverage,
  usage/status/latency; không nhân bản text chunk đầy đủ vào trace. Không tạo billing store mới.
- complete_agent_run mở rộng tùy chọn cho QA: khóa owner → conversation → document trước khi
  tạo message, kiểm scope/active version/deleted; message, citation bindings, trace và trạng thái
  completed cùng transaction. Nếu scope đổi thì rollback, không lưu answer/citation trộn version.
- Citation ID S1/S2 là label riêng từng answer, binding lưu document/version/chunk/config version
  cụ thể. API history trả metadata citation, source API đọc đúng binding và lọc owner cả conversation/
  document/chunk; private/no-store. Query metadata không load vectors/text, chỉ source detail lấy text.
  File bỏ chọn vẫn xem nguồn cũ, file thư viện bị xóa thì source 404; admin không có nhánh đọc chéo owner.
- Web sửa chat.js/styles.css: inline citation button, danh sách nguồn, dialog text gốc/trang/mục,
  tải file gốc đúng version; dùng DOM/textContent, không render HTML từ tên file/đoạn trích.
  Reload hội thoại phục hồi citation metadata; nguồn bị xóa hiển thị lỗi mở nguồn rõ ràng.
  Footer AI có thể mắc lỗi giữ nguyên; không in route/điểm debug. QA error không hiện nút resume giả.
- Cập nhật PROJECT_PLAN.md/README Docker/env mẫu Compose. Chưa bật cờ runtime hoặc áp dụng
  migration; chưa chạy LLM/JEV/embedding/Qwen/DB/API/UI, build Docker hoặc xử lý tài liệu thật.
  Kiểm tra tĩnh: Ruff/Python AST/JS syntax/Compose YAML/import app/OpenAPI, migration chain head0027,
  biên dịch SQL citation ownership/run trace offline. Người dùng tự test trực quan sau; tiếp theo M3.8.

## 2026-10-24 — M3.8 (code): re-index theo version, delete cleanup và invalidation

- Tiếp tục phần code theo yêu cầu; không tạo file/migration/test mới, không sửa .env private.
  Sửa các file hiện có: backend/services/document_ingestion.py, document_indexing.py, documents.py,
  backend/schemas/document.py, backend/routes/documents.py, frontend/static/documents.js,
  frontend/static/chat.js, README Docker, PROJECT_PLAN.md và ghi cuối PROJECT_NOTES.md.
- Thêm POST /api/documents/{document_id}/reindex, body expected_version_id, dùng auth/ownership
  và queue documents.prepare hiện có. Khóa owner → document; version cũ không khớp thì 409,
  gửi lại cùng request sau khi mất response trả generation đã tạo, không tạo trùng job/version.
  Chặn tạo generation khác khi tài liệu còn job queued/running hoặc chưa có bản active ready.
- Re-index là parse/chunk/embed/FTS mới theo snapshot cấu hình hiện tại, tạo version_number kế tiếp;
  retry tiếp tục version chưa publish (có thể resume embedding đã có), không mutate bản đã publish.
  Có nút Xử lý lại, Thử lại, Dừng xử lý trong library. Không thêm LLM/tool/plugin choice cho thao tác này.
- Active document giữ ready khi generation thay thế queued/parsing/chunking/embedding/indexing;
  retry/cancel/failure mới không làm mất active version cũ. Chỉ đổi active_version sau khi đủ vector/
  lexical index trong transaction publish. Library status/filter phản ánh generation mới nhất;
  ready_for_qa phản ánh active version, reindexing cho UI biết bản cũ vẫn sẵn sàng.
- Bảo vệ version published trong enqueue/index/cancel/interruption; retry worker đọc lại ready
  là idempotent kể cả version đã cũ. Worker kiểm generation mới nhất trước khi ghi bản chưa ready.
  Content pipeline hash thêm version_id, chunk UUID vẫn cố định trong mỗi version/retry; rebuild
  cùng nội dung/cùng model không va unique(document_id,pipeline_hash) hoặc sửa citation cũ.
- Generation cùng document dùng chung storage_key của file gốc immutable, không sao chép bytes
  upload hoặc chia sẻ giữa owner/document. document_usage group theo storage_key để chỉ tính file
  gốc một lần. Parsed artifact/chunk/vector các version cũ giữ đến khi xóa file thư viện, nhằm mở
  nguồn lịch sử đúng version; quota file gốc không phải giới hạn toàn bộ dung lượng DB/model cache.
- Delete vẫn soft-revoke active version và xóa conversation_documents trước khi commit; mọi
  retrieval/download/citation kiểm owner/nondeleted. Câu trả lời đã lưu trong hội thoại được giữ;
  citation không tự chuyển sang version mới hoặc tiếp tục đọc file đã xóa.
- Cleanup đợi ingestion dừng ngoài khóa owner/document, recover lease hết hạn trong lúc đợi;
  tránh hết năm retry quá nhanh khi worker cũ mất lease. Sau đó khóa owner/document, kiểm job type/
  payload/owner/worker/attempt/started_at/lease/cancel trước thao tác và khóa job trước commit metadata.
  Không giữ job row lock trong lúc I/O để heartbeat có thể gia hạn lease. I/O dọn tombstone idempotent;
  sau khi mất lease không commit xóa metadata. Nhiều generation dùng chung original key chỉ unlink
  một lần; parsed keys và staging cũng được dọn, path vẫn qua kiểm tra thư mục riêng hiện có.
- Library đọc cleanup_status của job mới nhất theo owner/document, hiển thị rõ cleanup lỗi/hủy
  và Yêu cầu xóa lại; không polling liên tục job terminal lỗi. Quota chỉ giải phóng sau khi file/
  metadata được dọn xong. Request xóa lại không sinh thêm job nếu đã có cleanup queued/running.
- Retrieval không có cache kết quả riêng để xóa: snapshot active version/hash/profile và kiểm lại
  sau inference/ở transaction lưu answer đã có từ M3.6–M3.7. Khi publish version mới, query đang
  dùng version cũ phải gửi lại nếu scope thay đổi; khi re-index còn chạy, query dùng bản active cũ.
  Citation binding giữ version/chunk ban đầu; model weights cache không bị xóa theo file của user.
- Frontend dùng no-store, thông báo mutation giữa tab bằng BroadcastChannel chỉ chứa UUID/type;
  có fallback scope polling/visibility refresh. Chat refresh scope cả khi file ready, đóng dialog/
  bỏ nút citation khi nhận delete event, chặn response nguồn đang chờ và metadata SSE cũ của file
  đã xóa. API nguồn vẫn kiểm owner/nondeleted mỗi lần mở, không dựa vào sự tin cậy của event client.
- Chỉ kiểm tra tĩnh Ruff/Python AST/JS syntax/import app/OpenAPI/worker registry và compile SQL
  quota shared-original/latest cleanup/library owner filters offline. Không chạy model, migration,
  database/API/browser, test chức năng hoặc race/failure injection. Không cài/tải thêm dependency/model.
  Đánh dấu M3.8 phần code hoàn thành; nghiệm thu tay vẫn để mở. Tiếp theo chuẩn bị runtime,
  áp dụng migration/model cache khi đến bước chạy để người dùng test trực quan và báo lỗi.

## 2026-10-25 — Chuẩn bị runtime M3 trên Windows

- Kiểm tra cấu hình thực tế: PostgreSQL native 17.5, migration còn `20261019_0025`,
  chưa có extension vector, chưa có worker; Docker engine không chạy. Giữ database native
  hiện có. Đã sao lưu bằng pg_dump vào `.tools/runtime-setup/pre-m3-20261025.dump` trước migration.
- Cập nhật `.env` private: V2_ENABLED/V2_ROUTING_ENABLED/V2_RAG_ENABLED=true,
  ROUTING_MODE=active; giữ nguyên thông tin đăng nhập và API key.
- Tải LibreOffice 26.8.1 từ nguồn chính thức, xác minh SHA256 và giải nén tại
  `.tools/libreoffice-26.8.1`; chạy `soffice.com --headless --version` thành công.
  DOCUMENT_LIBREOFFICE_PATH trỏ tới bản này, chưa thử chuyển tài liệu thật.
- Clone pgvector tag v0.8.7 từ repo chính thức, biên dịch DLL bằng MSVC x64 cho PostgreSQL 17
  thành công. Windows từ chối chép vào Program Files do thiếu quyền Administrator;
  chuẩn bị `.tools/install-pgvector-admin.ps1` để user chạy nâng quyền, chỉ cài file extension.
  User đã cài thành công; đã đối chiếu hash DLL với bản build, áp dụng migration 0026–0027.
  Database hiện ở `20261024_0027`, có vector 0.8.7/unaccent, đủ 6 bảng tài liệu và `agent_runs.rag_trace`.
- `uv sync` gặp WinError 32 khi rename cache; không thay lockfile hoặc tắt bảo vệ Windows.
  Export dependency dev+ingestion từ `uv.lock`, cài qua pip bằng `--require-hashes --no-deps`
  và index CPU chính thức của PyTorch. Cài xong torch CPU/Docling/ST/transformers và `pip check`
  báo không thiếu dependency. Import Docling PDF/Word, sentence-transformers, transformers,
  torch CPU thành công; không load model để inference hoặc đọc tài liệu thử.
- Đã tải đủ trọng số/tokenizer F2LLM, Qwen reranker và Docling layout/table vào `data/models`.
  F2LLM revision `8786315a8711c242ee03ec67c74dd9ad0a61e2cf`, Qwen revision
  `e61197ed45024b0ed8a2d74b80b4d909f1255473`. Xác nhận cấu hình F2LLM 1.024 chiều/last-token pooling.
  CDN ngắt nhiều lần nên Qwen dùng helper HTTP Range tiếp tục phần đã tải; kiểm SHA256
  `27cd75a405b9c1b46b59abfd88aaa209e6fed2a1972cde9b70e7659537c5e65b` khớp metadata chính thức
  trước khi đưa vào cache Hugging Face. Không tải/bật OCR hoặc gọi dịch vụ LLM trả phí.
- Restart đúng cây tiến trình uvicorn của project, giữ nguyên terminal/tiến trình khác;
  web chạy lại ở `http://127.0.0.1:8000`, health live/ready đều 200.
  Khởi động worker native ở cửa sổ ẩn, có heartbeat mới và không có startup traceback.
  Log/heartbeat local trong `.tools/runtime-setup/`; không tạo job hay upload tài liệu thử.
- File tải về, source/compiled extension, helper setup, model cache, backup và cấu hình private
  nằm trong đường dẫn đã được Git ignore. Chưa thực hiện kiểm thử chức năng/LLM/JEV/browser;
  nghiệm thu tay M3 vẫn do user thực hiện sau khi runtime sẵn sàng.

## 2026-10-25 — Bộ kịch bản kiểm thử M3 và dữ liệu mẫu local

- Theo yêu cầu mới, bổ sung `docs/rag/MANUAL_M3_CASES.md`: kịch bản theo upload/scope,
  parse/chuẩn hóa, chunk/provenance, embedding/FTS, dense/lexical/RRF/rerank/budget,
  history/plan/rewrite, QA/citation/summary, worker/retry/cancel/re-index/delete và ownership.
  Mỗi case có thao tác, nguồn quan sát và điều kiện đạt; có thứ tự test nhanh và cách ghi case fail.
- Thêm `scripts/create_rag_fixtures.py`: sinh dữ liệu giả có đáp án cố định bằng Python,
  python-docx, LibreOffice và Pillow/pypdf; không upload, ghi DB hoặc gọi model.
  Đã tạo 19 file và answer key tại `data/rag-testing/fixtures` (Git ignore), gồm
  TXT UTF-8/UTF-16/NFD, tài liệu dài/nhiều mục, Word DOC/DOCX, PDF text/ảnh/trộn/mã hóa,
  file rỗng/giả PDF/CSV và 21 MiB để kiểm giới hạn. Không ghi đè thư mục mẫu đã có nội dung.
  Lượt tạo mẫu trong sandbox bị lỗi quyền profile LibreOffice; giữ bản dở tại
  `.tools/runtime-setup/m3-fixtures-incomplete`, tạo lại thành công với quyền chạy phù hợp.
- Thêm `scripts/inspect_document_rag.py`: kết nối SQL read-only, yêu cầu email owner và lọc
  document/version/run theo owner; list ID, đọc parsed artifact, sample chunk, toàn bộ offset/hash,
  số chiều/norm/số vector/FTS, ingestion stage và rag_trace. Có tùy chọn đếm lại token bằng
  tokenizer đã cache; không gọi embedding/reranker/LLM, không bổ sung endpoint debug vào web.
- Hướng dẫn dùng POST /api/documents/search bằng phiên đăng nhập hiện tại để tách dense,
  lexical và hybrid khỏi sinh câu trả lời; xem RRF candidate membership/reranker profile/budget.
  Phân biệt rewrite riêng API với bước plan đã rewrite của chat document_qa, tránh hiểu nhầm
  retrieval rewrite skipped là thiếu history. Kiểm quyền thật bằng hai phiên A/B, không dùng CLI
  có quyền DB làm bằng chứng isolation API.
- Xác minh Ruff/CLI help, EXPLAIN 9 SELECT bằng kết nối read-only/UUID không tồn tại,
  dữ kiện/encoding/bảng, 2 trang PDF/text marker, OLE DOC, scan/mixed PDF và PDF mã hóa.
  Không tạo hàng test trong database, không gọi LLM/model inference, chưa chạy bộ case trên web;
  checkbox nghiệm thu M3 vẫn để mở. File code mới chỉ có hai script; sửa thêm PROJECT_PLAN.md
  và ghi cuối PROJECT_NOTES.md, không thay pipeline hoặc giao diện thực thi.

## 2026-10-25 — Upload chỉ tại thư viện, chat chỉ chọn tài liệu sẵn sàng

- Theo yêu cầu mới, bỏ nút/input/progress upload và nút refresh thủ công trong chat;
  giữ Chọn từ thư viện, danh sách file đã chọn và cập nhật scope tự động.
  Trang Tài liệu tiếp tục upload/hiển thị mọi trạng thái xử lý. Upload API/service chỉ lưu
  vào thư viện, bỏ tham số conversation_id và helper tự gắn file khi upload/deduplicate.
- Hộp chọn gọi GET /api/documents?ready_for_qa=true. Backend lọc theo document còn tồn tại,
  trạng thái ready và active version cùng owner còn ready trước khi tính total/phân trang.
  Không lọc đơn thuần latest version.status=ready: file re-index đang chạy hoặc thất bại
  vẫn chọn được nếu bản active cũ còn dùng được.
- PUT lựa chọn kiểm lại quyền và readiness dưới khóa owner/conversation hiện có. File chưa
  sẵn sàng trả 409/documents_not_ready; file không khả dụng trả 404; lựa chọn cũ chưa bị thay
  khi kiểm tra thất bại. Frontend làm mới hộp chọn/lựa chọn đã lưu khi gặp lỗi lưu.
- Hội thoại cũ đã chọn file chưa ready vẫn có thể bỏ chọn. Khi mở hộp chọn/lưu lại, draft chỉ
  giữ các file ready; không xóa file gốc hoặc sửa history. Không tự thay selection bằng GET.
- Sửa frontend/templates/chat.html, frontend/static/documents.js, backend/routes/documents.py,
  backend/services/documents.py; cập nhật PROJECT_PLAN.md và MANUAL_M3_CASES.md (U01/U07,
  thêm U08/U09 kiểm phân trang/readiness/re-index, chỉnh luồng queue). Không tạo file code mới.
- Kiểm tra Ruff, Node syntax, render Jinja/selector DOM, OpenAPI upload chỉ có file và query
  ready_for_qa. Chạy SELECT với CTE dữ liệu giả trên kết nối read-only để xác nhận loại queued/
  failed/deleted/khác owner, giữ active ready khi re-index và total/pagination; không tạo dữ liệu DB.
  Kiểm branch lưu lựa chọn bằng mock session: ready/bỏ chọn thành công, 409/404 không commit
  hoặc thay lựa chọn trước đó. Chưa test giao diện tương tác hoặc gọi LLM/model inference;
  user tiếp tục nghiệm thu tay theo hướng dẫn cập nhật.
- Đã khởi động lại web của đúng project để nạp thay đổi, xác nhận HTTP OpenAPI có filter mới
  và static JavaScript dùng ready_for_qa=true; worker cũ vẫn chạy. Browser cần Ctrl+F5 để tải UI mới.

## 2026-10-26 — Sửa kiểm tra pooling khi khởi động F2LLM

- User test `01_atlas.txt`: parse/chunk đã có 1 đoạn nhưng embedding thất bại sau 3 lần thử,
  mã `embedding_model_unavailable`. Tải trực tiếp từ cache thành công, xác định nguyên nhân
  là SentenceTransformers 5.7 chuyển boolean `pooling_mode_lasttoken` sang
  `pooling_mode="lasttoken"`; code kiểm tra thuộc tính cũ nên từ chối model hợp lệ.
- Sửa `scripts/run_embedding_model.py` để nhận cấu hình mới và vẫn hỗ trợ thuộc tính cũ.
  Giữ nguyên model/revision, last-token pooling, số chiều, chuẩn hóa, giới hạn tài nguyên
  và recipe; không cần tải lại weights hoặc thay schema database.
- Chạy thật qua F2EmbeddingClient/subprocess cho nội dung Atlas và một query: đều trả
  1 vector đủ 1.024 chiều, norm 1. Chạy subprocess bằng quyền thực thi phù hợp vì sandbox
  cản tạo tiến trình IPC. Ruff check/format check đều đạt; không tạo file kiểm thử mới.
- Gọi retry_preparation cho đúng file Atlas đã upload qua service hiện có, không sửa trạng
  thái/vector bằng SQL hoặc upload bản mới. Worker xử lý thành công ngay lần 1; SELECT xác
  nhận document/version ready, active version đã publish, ingestion/job succeeded,
  1 chunk có vector 1.024 chiều (norm khoảng 1) và search_tsv không rỗng, error_code đã xóa.
  Worker đang chạy khởi tạo subprocess từ script đã sửa nên không cần restart web/worker.
- Cập nhật PROJECT_PLAN.md và ghi cuối PROJECT_NOTES.md. Không gọi LLM/dịch vụ trả phí;
  user tiếp tục chọn Atlas từ thư viện và nghiệm thu hỏi đáp/citation trên web.

## 2026-10-26 — Sửa giới hạn instructions của JEV và nút resume khi chưa có checkpoint

- Đọc đúng run lỗi của câu hỏi Atlas: stop_reason=llm_error, error_message là validation
  error của DecisionRequest.instructions vượt ShortText 1.000 ký tự. Chưa phân route,
  chưa gọi DeepSeek, chưa chạy LangGraph; thông báo lỗi DeepSeek trên UI gây hiểu nhầm.
- Rút QUERY_INSTRUCTIONS trong agent_core/routing/query_router.py còn 844 ký tự, giữ
  4 route, history, selected_documents và ranh giới chat/document_qa/planned_task cùng
  hướng dẫn coi input là dữ liệu không đáng tin. Tăng QUERY_POLICY_VERSION lên jev_query2.1.1;
  không tăng giới hạn schema hay cắt chuỗi tùy ý khi gửi request.
- frontend/static/chat.js dùng cùng điều kiện canResumeRun cho timeline lưu và lỗi SSE.
  Ngay sau lỗi, lấy timeline từ API hiện có và chỉ thêm nút resume cho đúng run failed
  có checkpoint_revision dạng số nguyên không âm. Thiếu checkpoint, sai run hoặc lỗi
  truy vấn API thì không hiện nút chưa xác minh; giữ backend guard resume hiện có.
- backend/routes/chat.py ghi exception qua logger có redaction hiện có; lỗi ngoài các
  nhánh đã biết trả thông báo chung Không thể hoàn tất yêu cầu thay vì đổ lỗi DeepSeek.
  Giữ nguyên nhánh lỗi provider/authorization/quota và không sửa dữ liệu run lỗi cũ.
- Kiểm request thật qua schema với 0/1/20 metadata file, đủ 4 options và giữ query/history;
  kiểm adapter/route bằng phản hồi JEV giả, không gọi endpoint JEV/DeepSeek. Kiểm JavaScript
  với Node cho thiếu checkpoint, run khác, checkpoint hợp lệ (kể cả revision 0), thiếu run ID
  và lỗi mạng. Ruff check/format, Node syntax và git diff --check đạt; không tạo file test mới.
- Dừng hai cây uvicorn của đúng workspace đang chạy song song, khởi động một web server
  local ẩn để nạp code mới, không đổi worker/database/model cache. User tải lại trình duyệt
  và gửi lại câu hỏi Atlas; run chưa có checkpoint không thể dùng nút tiếp tục để khôi phục.

## 2026-10-26 — Truyền metadata tài liệu qua bộ lọc trước khi JEV phân route

- Theo user phản ánh, lượt hỏi Atlas hoàn thành nhưng trả lời không biết sản phẩm. Đọc run
  thấy JEV chọn chat=0,8, document_qa=0,18 và không có rag_trace. Tái hiện request xác nhận
  backend đã gắn metadata nhưng PrivacyPolicy.prepare bỏ selected_documents/count; đây là
  lỗi đường truyền trước provider, không phải bằng chứng index thiếu nội dung Atlas.
- Sửa agent_core/routing/policy.py: query chỉ cho phép title/status của tối đa 20 metadata
  file, title tối đa 200 ký tự, status là chuỗi không rỗng tối đa 20 ký tự. Bỏ entry sai kiểu,
  bỏ mọi trường khác như ID/path/text, giữ selected_document_count dạng int phù hợp hoặc
  suy ra từ danh sách hợp lệ. Scrub secret trước cắt title và tiếp tục scrub PII/secret toàn
  payload; giữ giới hạn state/payload và bộ lọc capability/history hiện có. Policy lên 1.2.1.
- Sửa agent_core/routing/query_router.py: query policy jev_query2.1.2, instructions 917 ký tự.
  Hỏi file/chủ thể trong tên file/hỏi nối tiếp chọn document_qa kể cả không nói theo tài liệu;
  tài liệu + tìm web/gửi mail/phối hợp bước chọn planned_task. Chat hoặc tool độc lập vẫn
  được chọn khi có file. Giữ đủ 4 choices, argmax và tie về chat; không ép route bằng selection.
- Kiểm inline đủ chuỗi build_query_request -> ManagedDecisionProvider -> PrivacyPolicy ->
  JevDecisionProvider -> route với phản hồi JEV giả; xác nhận metadata thực sự tới SDK boundary,
  không chỉ kiểm request đầu vào. Kiểm không chọn file, history/query, entry/count sai kiểu,
  giới hạn 20/200, secret cắt ngang ranh giới title, PII, loại trường ngoài allow-list và payload
  budget; capability không nhận metadata và vẫn giữ history. Không tạo file kiểm thử mới.
- Kiểm dispatch graph bằng handler giả: document_qa gọi QA handler, không chạy model chat
  thường. Trên transaction SQL read-only, scope của hội thoại hiện có trả bản active/ready và
  chunk đúng owner/file đã chọn. Đã kiểm đường nối QA hiện có, không thay service retrieval,
  embedding/rerank, quyền truy cập hoặc lõi planned_task. Không gọi JEV/DeepSeek thật.
- Cập nhật docs/rag/MANUAL_M3_CASES.md với cách quan sát SSE routing/trace và I01–I06:
  Atlas không nói theo tài liệu, history, nhiều file, chat không liên quan, tài liệu + web/mail.
  Hai case phối hợp chỉ kiểm ý định/thông báo chưa sẵn sàng; chưa triển khai workflow tool.
  Cập nhật PROJECT_PLAN.md và ghi cuối PROJECT_NOTES.md; tổng cộng sửa 5 file, không tạo file mới.
- Ruff check/format và git diff --check đạt. Khởi động lại đúng web của workspace để nạp code;
  health live/ready 200. Không restart worker hoặc đổi dữ liệu/index. User tiếp tục Ctrl+F5,
  chọn Atlas và gửi lại câu hỏi để nghiệm thu phân loại thật, retrieval và citation trên web.

## 2026-10-26 — Chạy RAG Windows với event loop hỗ trợ subprocess

- Run Atlas mới có rag_trace/plan đúng, DeepSeek hoàn tất bước lập câu hỏi; lỗi sau đó là
  embedding_unavailable. Đọc source Uvicorn đã cài: Windows với --reload/nhiều worker dùng
  SelectorEventLoop, không hỗ trợ asyncio.create_subprocess_exec cho tiến trình model.
  Model cache/file/index không phải nguyên nhân của lần lỗi này.
- Tái hiện bằng chính loop factory của Uvicorn: reload sinh NotImplementedError khi mở
  tiến trình Python in số; bỏ reload dùng ProactorEventLoop, mở/đọc tiến trình thành công.
  Tài liệu Python xác nhận giới hạn subprocess trên Windows:
  https://docs.python.org/3.11/library/asyncio-platforms.html#windows
- Sửa agent_core/rag/local_models.py: bắt NotImplementedError tại spawn, trả mã riêng
  embedding_event_loop/reranker_event_loop, không retry, kèm chỉ dẫn chạy Uvicorn một worker
  không reload. Không đổi sang subprocess không giới hạn hoặc bỏ giới hạn RSS/timeout/IPC.
- Cập nhật docs/rag/MANUAL_M3_CASES.md và infra/docker/app/README.md: lệnh web Windows
  bỏ --reload, dùng host 127.0.0.1/cổng 8000; sửa code thì Ctrl+C và khởi động lại thủ công.
  Worker tài liệu vẫn là terminal/tiến trình riêng; không thay runtime Docker/Linux.
- Kiểm LocalModelClient trên Selector loop trả đúng mã embedding_event_loop và retryable=false.
  Chạy search_documents hybrid thật bằng loop factory tương ứng một worker không reload,
  trên đúng Atlas đã upload: F2LLM query embedding, dense/FTS mỗi nhánh 1 ứng viên, RRF gộp 1,
  Qwen rerank và đóng gói trả 1 evidence đúng document chứa 24 tháng, score_kind=rerank.
  Lượt nạp model và retrieval mất 67,64 giây (rerank khoảng 36,28 giây); đã đóng các model client
  thử sau khi xong. Request dùng document_ids, không có history nên rewrite skipped; không
  gọi JEV/DeepSeek, tạo chat/run/citation mới hay thay file/index trong database.
- Ruff check/format và git diff --check đạt. Sửa thêm PROJECT_PLAN.md, ghi cuối PROJECT_NOTES.md;
  tổng cộng 5 file, không tạo file test mới. Giữ web/worker user đang chạy, không mở web nền
  hoặc chiếm thêm cổng; user cần dừng web --reload trong terminal rồi chạy lệnh đã sửa.
  Chưa nghiệm thu sinh câu trả lời/citation trên web; user tiếp tục test tay.

## 2026-10-26 — Chạy thật bộ case M3 phần 5, 6, 7

- Theo yêu cầu mới của user, chạy API HTTP/SSE thật trên web local một worker không reload.
  Tạo tài khoản test riêng m3-live-a8f29c71@example.com bằng register/login cookie thật;
  không tạo JWT thay user, override dependency hoặc mock JEV/DeepSeek/embedding/reranker.
  Upload 5 fixture qua API: Atlas, Boreal, sổ tay dài, DOCX bảng và PDF chữ. Không thay
  history/selection/file của tài khoản user đang sử dụng; giữ dữ liệu test để đối chiếu.
- Viết helper local tại .tools/runtime-setup/m3_live_probe.py và summarize_m3_live.py,
  lưu request/response/SSE/trace/inspect/citation và báo cáo trong
  data/rag-testing/live-20261026-a8f29c71 (đều Git ignore). credentials.json chỉ chứa tài khoản
  test mới, không in password/cookie/provider key vào report. Ghi environment.json với git HEAD,
  danh sách file đang sửa, version package, config không bí mật và SHA256 fixture.
- Phát hiện setup: cả 5 upload trả HTTP 500 nhưng file/job đã commit và tiếp tục được worker
  xử lý. Ba TXT ready; DOCX/PDF failed tại parsing/document_processing_failed, 0 chunk.
  Ghi rõ upload không pass; không gửi lại upload để che lỗi hoặc giả trạng thái ready.
  Chưa xác định exception parser/vendor chi tiết, chưa sửa sản phẩm trong đợt chạy test này.
- Dừng đúng worker nền cũ do assistant mở (root 24964, worker 15704) sau khi xác nhận không
  có job running thuộc worker đó. Giữ worker user, tránh hai model ingestion trùng trong RAM;
  không stop/restart web đang được user chạy ở terminal. Inspector đọc artifact bằng quyền
  thực thi phù hợp vì sandbox Windows chặn ACL; dùng transaction SQL read-only và lọc owner.
- Phần 5: C01/C02/C03/E01/E02/E03 đạt. Sổ tay dài có 19 chunk, 30–475 token, đếm lại
  tokenizer/offset/hash đều 0 mismatch; 12 cặp overlap trong mục, không overlap xuyên mục.
  19/19 vector và FTS, 1024 chiều, norm 0,9999999617–1,0000000965; profile F2LLM đúng.
  C04 BLOCKED vì chưa qua parse DOCX/PDF; không suy ra chunking bảng đã đúng hoặc sai.
- Phần 6: R01–R06 đạt qua API search thật. Mã chính xác/không dấu/diễn đạt lại tìm đúng
  đoạn Atlas; R04 đối chiếu dense/lexical và tính lại RRF=2/61, Qwen rerank giữ phủ định.
  R05 đủ hai file/24 và 12 tháng; R06 có 19 ứng viên, đóng 5 whole chunk không trùng,
  3921/4000 token evidence. Thời gian R01/R02 0,16/0,06 giây; R03 15,20; R04 29,16;
  R05 8,97; R06 95,38 giây. Đây là số đo một đợt máy CPU, không phải benchmark tổng quát.
- Phần 7: I01/I02/I04/I05/I06/H01/H02/H03 đạt. Atlas 24 tháng có citation (8,64 giây),
  nối tiếp đổi trả 14 ngày (13,30 giây). Câu Python không liên quan vẫn route chat.
  Hai yêu cầu file + web/mail route planned_task và báo chưa sẵn sàng, không gọi tool ghi;
  đây chỉ là kiểm ý định, không nghiệm thu lõi workflow. Không gửi email trong phép thử.
- I03 FLAKY: lần đầu route document_qa đúng và 3 retrieval có đủ Atlas/Boreal, nhưng step
  trả lời báo document_qa_model_failed (26,84 giây). Thử lại đúng 1 lần ở chat test mới:
  12/24 tháng, 2 citation, 21,91 giây. Giữ cả hai run, không đổi lỗi lần đầu thành pass;
  chưa biết điều kiện output/validation lỗi cụ thể vì raw LLM output không được lưu.
- H01 dùng 3 lượt hỏi/câu trả lời thật: history=0/2/4, plan giải nghĩa Atlas và ngâm nước,
  trả đúng phủ định; retrieval rewrite skipped đúng vì plan đã rewrite. H02 API dùng 6 message,
  DeepSeek rewrite thành Chính sách đổi trả của Atlas là gì? (499 token); chat rỗng đối chứng
  history=0/rewrite skipped. H03 chia 3 truy vấn, trả đủ 24/12 tháng, 14/7 ngày, 3/5 ngày
  và hai nguồn (27,36 giây). Mở 9 citation qua endpoint nguồn đều HTTP 200, đúng document/
  version/chunk và có text; đối chiếu dữ kiện với fixture, không chỉ kiểm nhãn nguồn.
- REPORT.md/results.json ghi đủ 22 case: 20 PASS, 1 BLOCKED (C04), 1 FLAKY (I03),
  cùng thời gian, conversation/run ID và artifact. Chưa test click/render browser hoặc chất
  lượng trên corpus lớn; chưa sửa 3 lỗi mới (upload HTTP 500, parser Word/PDF, I03 không ổn định).
  Cập nhật PROJECT_PLAN.md và ghi cuối PROJECT_NOTES.md, không thêm file code/test tracked.

## 2026-10-26 — Sửa upload, parser Windows/PDF, citation và kiểm lại model thật

- Theo user duyệt sửa các lỗi ở báo cáo phần 5/6/7. Giữ các thay đổi chưa commit từ bước
  event-loop trước, không reset/stage/commit thay user. Sửa thêm các file hiện có, không thêm
  file code/test tracked; helper/báo cáo mới ở .tools/runtime-setup và data/rag-testing (Git ignore).
- backend/services/documents.py: upload mới và dedup đều flush/refresh Document/Version,
  dựng response DTO khi fields đã nạp rõ ràng rồi chỉ trả sau commit thành công. Khắc phục
  updated_at server-generated bị expire gây MissingGreenlet khi _view đọc ngầm. Giữ điều kiện
  cleanup file khi transaction lỗi/commit chưa chắc chắn và giữ atomic enqueue hiện có.
- backend/services/document_ingestion.py: Windows cấp USERPROFILE riêng dưới scratch/profile,
  không chuyển toàn bộ env hoặc credential DB/mail/provider vào parser. Tái hiện trước sửa:
  Docling import Path.home() lỗi Could not determine home directory trong env đã lọc; cùng
  DOCX đọc được khi có profile. Parser mới đã qua Word, sau đó PDF lộ dependency cv2 thiếu.
- pyproject.toml/uv.lock: thêm opencv-python-headless>=4.10,<5 vào extra ingestion cho
  Docling TableFormer, lock 4.14.0.94; resolver chỉ thêm một package, không đổi phiên bản cũ.
  Cài đúng wheel đã kiểm hash trong lock bằng uv pip --no-deps/--require-hashes; uv pip check
  và uv lock --check --offline đạt. Không bật OCR hoặc thay model/layout pipeline để né bảng.
- agent_core/rag/qa.py: answer version document-qa-answer-v2; DocumentOutputError có mã cố
  định cho invalid_json/invalid_schema/citation_link/citation_sources/citation_syntax. Cho phép
  [S1] [S2] và [S1][S2]; vẫn từ chối link inline/reference, định nghĩa link mang nhãn nguồn,
  nguồn ngoài allowed, source trùng/mismatch và cú pháp nguồn lạ. Lỗi validator tái hiện được;
  không kết luận tuyệt đối đó là exception của I03 cũ vì lần cũ không lưu raw output.
- backend/services/document_qa.py: tách mỗi attempt và wrapper sửa output tối đa một lần.
  Giữ nguyên question/evidence, chỉ thêm chỉ dẫn sửa cùng mã lỗi đã lọc; không gửi lại output
  lỗi nguyên văn hoặc chạy lại routing/retrieval. Mỗi attempt đi qua guard budget/quota/scope
  và dùng chung timeout graph; finally ghi usage của cả attempt lỗi, recorder và trace với
  attempt/repair_reason/error_code. Provider/configuration/scope/budget/cancel không bị retry;
  hết hai attempt trả document_qa_invalid_output, provider trả document_qa_provider_error.
- Kiểm callback điều khiển riêng: tối đa hai lần gọi, reason chính xác, lỗi provider/scope/
  budget không lặp; hết budget trước lượt sửa vẫn dừng. Kiểm JSON/schema, nhãn nguồn cạnh
  nhau, URL/Markdown reference/nguồn lạ. Đây là kiểm giới hạn, không tính là lượt LLM thật.
- Khởi động lại đúng service project để nạp code. Guard ban đầu gặp một marker run running;
  kiểm read-only xác nhận là run ngày 01/10, 0 step, chưa resume, tồn tại trước service hiện
  tại và không có job/run thực mới. Giữ nguyên bản ghi cũ. Đã dọn helper mở trùng trong lúc
  kiểm tra, sau đó giữ đúng một web và một worker chạy nền. Không dùng --reload trên Windows.
- Đợt live sau sửa dùng tài khoản test mới m3-live-b71c9a04@example.com qua register/login
  cookie và API HTTP/SSE thật, JEV/DeepSeek/F2LLM/Qwen thật. 5 file mới đều HTTP 201 và ready;
  duplicate Atlas HTTP 200, cùng document/version, tổng 5 file. SELECT read-only xác nhận
  chỉ 1 prepare job cho version Atlas. Ba TXT vẫn khớp token/offset/hash và đủ vector/FTS.
- DOCX/PDF mỗi file có 4 chunk/vector/FTS, offset/hash không lệch; PDF 2 trang. Nội dung bảng
  giữ đúng nội thành 25.000/2 ngày, ngoại thành 45.000/4 ngày. Cả hai câu hỏi qua chat thật
  trả đúng 45.000 đồng và 4 ngày kèm citation (21,12/24,70 giây). Retry DOCX/PDF của tài
  khoản test trước qua API đều ready/cùng document/version; không upload bản mới để che lỗi.
- I03 lặp 3 hội thoại mới đều hoàn thành, đủ 12/24 tháng và 2 nguồn, model thực sự trả các
  citation liền nhau và validator chấp nhận. Thời gian 99,27/24,58/23,49 giây (lượt đầu sau
  restart model). H03 đủ ba tiêu chí/2 nguồn trong 34,12 giây. Atlas 24 tháng và history đổi
  trả 14 ngày cũng đạt. Không thấy attempt=2 phát sinh trong các lượt live, ghi rõ giới hạn này.
- Regression thêm phát hiện Negation-fixed route chat=0,68 và lặp [S1] từ history nhưng
  không có citation binding mới. Không đánh dấu pass hoặc xóa run này. Chỉnh taxonomy.py
  lên 2.0.1: chat xử lý văn bản user dán trực tiếp, không coi câu trả lời tài liệu trước là
  pasted text; câu hỏi nối tiếp/xác nhận file vẫn document_qa dù history đã có đáp án.
  Query policy jev_query2.1.3, instructions 970 ký tự, giữ 4 choices/argmax/tie về chat.
- Kiểm lại đúng history thật: câu ngâm nước từng sai và câu hỏi lại đều document_qa (0,80/
  0,91), trả không đổi và có nguồn mới (79,20/12,14 giây). Đối chứng Python không liên quan
  vẫn chat=0,98; file + web vẫn planned_task=0,75 và báo chưa sẵn sàng. Không gọi tool ghi.
- Cập nhật MANUAL_M3_CASES.md và MANUAL_JEV_CASES.md với retry/output trace, hỏi nối tiếp
  cần citation mới, lõi document_qa đã có, lệnh Windows không reload. Cập nhật PROJECT_PLAN.md.
  Báo cáo data/rag-testing/fix-20261026-b71c9a04/REPORT.md/results.json lưu 12 kiểm tra chat
  live đạt cùng run/answer/trace, baseline sai route được giữ riêng; mở 14 citation qua API
  đều đúng document/version/chunk và có text. Giữ báo cáo trước sửa để đối chiếu.
- Ruff check/format, git diff --check, lock check và dependency check đạt. Chưa chạy lại toàn
  bộ 22 case/bộ M3, không khẳng định độ đúng JEV/LLM tuyệt đối từ số mẫu hữu hạn. Web/worker
  hiện đã nạp bản sửa, chạy nền một bản mỗi service tại cổng 8000 để user tiếp tục kiểm thử.

## 2026-10-26 — Giảm RAM parser PDF cho tài liệu dài

- User duyệt giảm batch/queue và đo lại PDF Hiểu sâu về AI Agent.pdf (11 MiB, 421 trang).
  Kiểm read-only job lỗi: stage parsing, document_memory_limit, limit 4096 MiB, dừng sau
  khoảng 68 giây; chưa có parsed artifact/chunk. Đây là guard theo RSS cây tiến trình,
  không phải bằng chứng toàn máy đã hết RAM. Không thay đổi trạng thái tài liệu của user.
- agent_core/rag/parsing.py: PDF recipe v2 đặt layout/table/OCR batch 1 (OCR vẫn tắt),
  queue_max_size=2 mỗi công đoạn; thêm ThreadedDoclingParseBackendOptions với parser_threads=2,
  max_concurrent_results=2 để chặn cả bộ đệm decode/render native bên dưới pipeline.
  Giữ phân tích bảng và recipe DOC/DOCX v1, CPU 2 threads, không thêm dependency/migration.
- Phép đo đầu chỉ giảm batch/queue, dùng đúng subprocess/guard sản phẩm: peak 1825,12 MiB,
  không gặp memory limit nhưng dừng document_timeout sau 600,62 giây. Không gọi lần này là
  parse thành công. Phép đo tiếp có cả native bound, timeout riêng 1800 giây và wrapper đếm
  progress qua callback chính thức; callback chỉ lưu số trang/phase, không lưu nội dung/log vendor.
- backend/core/config.py và .env.example: timeout parse/chunk mặc định 600 -> 1800 giây
  để PDF dài có thời gian xử lý với batch nhỏ trên CPU; vẫn cấu hình được (30–3600 giây),
  giá trị .env có ưu tiên. Không tăng DOCUMENT_PROCESS_MEMORY_MB=4096 hoặc sửa .env riêng.
- PDF mẫu 13_text.pdf qua parser thật trong 30,89 giây, peak 1047,84 MiB: 2 trang, 245 ký tự,
  5 block; marker/trang đúng, hai hàng phí và ngưỡng 900.000 đồng còn nguyên. Qua chunking thật
  thành 4 chunk, lớn nhất 52 token; _load_artifacts xác nhận offset/hash và nguồn trang 1–2.
- Không thêm file test/code tracked. Helper đo nằm .tools/runtime-setup; artifact/report ở
  data/rag-testing/memory-20261026 (Git ignore). Giữ nguyên chỉnh sửa PROJECT_PLAN.md có sẵn
  của user, chỉ thêm phần điều chỉnh PDF; không stage/commit/push thay user.
- Kiểm môi trường thấy hai worker root: bản nền cũ do agent tạo PID 18936 và bản terminal
  user PID 26164. Xác minh create_time/module/CWD, queue không có job queued/running rồi
  dừng riêng cây worker cũ (kể cả model/helper con); giữ worker terminal và web đang chạy.
  Không dừng parser đo RAM độc lập, không sửa marker agent run cũ trong DB.
- Kết quả cuối PDF 421 trang: parse SUCCESS trong 1170,27 giây (19 phút 30 giây), peak RSS
  1573,17 MiB = 1,54 GiB, dưới limit 4096 MiB. 1.308.288 ký tự, 4.640 block; callback đủ
  421 page_completed success, cả 421 trang có text ngoài header/footer, ParsedDocument kiểm
  offset/page hợp lệ. Có warning 827 header/footer và 111 ảnh chưa OCR; không coi nội dung
  trong ảnh là đã đọc. Không cần thêm chia nhóm trang vì cấu hình bounded đã qua toàn bộ PDF.
- Báo cáo REPORT.md/results.json trong data/rag-testing/memory-20261026 lưu kết quả cả lần
  timeout và lần thành công. Các kiểm độ phủ trang/RAM/bảng/source đều đạt; chúng không thay
  thế nghiệm thu chất lượng mọi chữ/bảng, embedding hay hỏi đáp toàn bộ sách. Không gọi LLM,
  không ghi DB hoặc chuyển artifact local vào tài liệu của user. Web/worker trong terminal
  user được giữ nguyên; user cần restart để nạp timeout mới rồi Thử lại file cũ trên web.
- Ruff check/format hai file Python và git diff --check đạt. Các file tracked sửa trong bước:
  agent_core/rag/parsing.py, backend/core/config.py, .env.example, docs/rag/MANUAL_M3_CASES.md,
  PROJECT_PLAN.md, PROJECT_NOTES.md. Không thêm file tracked, không đổi dependency/schema.

## 2026-10-26 — Timer trả lời, tiến trình RAG và timeout riêng

- User duyệt hướng làm trước khi sửa. Giữ toàn bộ thay đổi chưa commit của phần RAM PDF,
  không khôi phục đoạn PROJECT_PLAN.md user đã xóa; không stage/commit/push thay user.
- Đối chiếu run lỗi 731e06f4-d4f6-477a-952a-1c72ed5d29e3 theo owner user@personalagent.vn:
  hết document_qa_timeout sau khoảng 120 giây; model plan 1963 ms/349 token đã xong, nhưng
  retrieval chưa trả kết quả. Trace cũ chưa đủ để kết luận chính xác bước con nào bị chậm.
- backend/core/config.py/.env.example: thêm DOCUMENT_QA_TIMEOUT_SECONDS=600 (5–3600 giây).
  document_qa.py thay time budget của graph RAG, giữ nguyên AgentLimits chat/tool 120 giây
  và timeout parse/chunk 1800 giây. Không thay .env riêng, dependency, model hoặc schema DB.
- agent_core/graph/runtime.py: thêm event pipeline_progress và payload metadata. Routes chat
  phát start có timestamp, progress nhận/phân loại và chuyển các event công đoạn thành SSE
  riêng. Nhãn model plan giờ là phân tích câu hỏi; completed event RAG giữ prompt context
  để mô tả đúng công việc vừa xong, không gọi plan là hoàn thiện đáp án.
- document_qa.py, document_query.py, document_retrieval.py phát mốc scope/plan/embedding/
  dense/lexical/fusion/rerank/evidence/answer/citation và sửa output nếu phát sinh. Metadata
  số đoạn dùng giá trị thật, không đoán phần trăm/ETA. local_models.py và hai adapter hiện có
  embeddings.py/reranking.py thêm callback on_load để chỉ hiện nạp model khi phải khởi động
  tiến trình/revision mới. Callbacks mặc định None giữ đường gọi ingestion/search cũ.
- Ghi snapshot vào AgentRun.rag_trace.progress, không thêm bảng/migration. agent_runs.py và
  schemas/chat.py trả progress whitelist cùng started_at; phase RAG ghi trace tại các mốc,
  phase routing dùng recorder. Timeout ghi failed_stage, đóng mọi phase running, lưu latency
  cả khi lỗi; fail_agent_run cũng đóng phase còn chạy khi lỗi/hủy ở lớp ngoài. Không trả raw
  reasoning, đoạn tài liệu, tham số tool hay credential qua field progress.
- frontend/static/chat.js/styles.css: tạo timer ngay trước fetch, dùng performance clock,
  cập nhật mỗi giây; dừng khi done/error/network/EOF hoặc DOM bị tháo. Các công đoạn có trạng
  thái, duration/số đoạn, bước nạp model/citation thụt vào; summary bố trí timer và metadata
  riêng, công đoạn trên màn nhỏ xuống dòng. Mở lại run terminal hiển thị timestamps đã lưu;
  run active poll 3 giây tới terminal, lỗi mạng poll dừng clock/báo trạng thái rồi thử lại.
  Không đổi luồng phê duyệt tool hoặc dòng nhắc AI có thể mắc lỗi.
- Kiểm 25 test hiện có về recorder, graph runtime/streaming và timeline schema đạt. Node
  chạy functions JS sản phẩm với DOM host kiểm clock live/stopped/stored/detached, phase
  count/state, fetch lỗi, SSE done/error, EOF sớm, polling active->terminal/offline/detach.
  Đây là kiểm logic DOM, không phải kiểm ảnh giao diện browser. Browser inventory rỗng và
  IAB unavailable nên chưa nghiệm thu trực quan layout/mobile; user cần restart web/Ctrl+F5.
- Kiểm RAG service + LangGraph/DB/DeepSeek/F2/Qwen thật, dùng đúng PDF đã ready thuộc user,
  tạo hội thoại mới Kiểm tra tiến trình RAG (Codex), không giả JWT/đổi ownership/copy index.
  Run 65bafe27-de67-49ce-b9f0-1895d8cb773b hoàn thành 170,74 giây/4267 token, trả đúng
  256–1024 token và 10%–20%; mở S1 qua source service đúng owner xác nhận trang 117.
  Đo plan 2,784 s; embedding 39,280 s gồm load 38,619 s; dense 83 ms; lexical 156 ms;
  rerank 125,210 s gồm load 31,437 s; answer 2,808 s. Đây là bằng chứng lần kiểm mới cần
  hơn 120 giây, không giả định trace cũ đã xác định chính xác rerank là bước lỗi.
- Kiểm timeout điều khiển riêng trong tài khoản test: model/retrieval giả, graph và DB thật,
  deadline 1 giây chỉ trong tiến trình kiểm. Lỗi chỉ đúng Nạp model embedding, failed_stage
  query.1.embedding_model, mọi phase terminal, giữ routing/latency/timestamps sau mở lại.
- Mở web thử riêng cổng 8011, tài khoản test có sẵn và Atlas ready qua HTTP/SSE thật:
  JEV chọn document_qa=1.0, DeepSeek/F2/Qwen thật, hoàn thành 81,48 giây; 26 progress event,
  API /runs trả 13 phase completed, nhãn model plan đúng. Không đổi web 8000/worker user;
  đã dừng riêng cây web thử PID 18856 và mọi model/helper con sau khi kiểm xong.
- Báo cáo, source/checkpoint metadata và raw event ở data/rag-testing/progress-20261026-3446c11e
  (Git ignore); helper dưới .tools/runtime-setup. Không thêm file code/test tracked. Cập nhật
  PROJECT_PLAN.md và MANUAL_M3_CASES.md với T01–T07; Ruff/format, JS syntax, diff checks đạt.
  Chưa chạy full suite/M3 hoặc mọi query, không khẳng định model/JEV đúng tuyệt đối.
- Rà soát tool_task bổ sung agent_core/graph/runner.py: publish progress bắt đầu/kết thúc
  ngay quanh lựa chọn/resolve shortlist thật theo step, qua event handler/recorder hiện có;
  không phát lại lựa chọn đã commit khi resume. Không đổi choice, quyền tool hay approval.
  Thêm 50 test shortlist/status SSE đạt, nâng tổng kiểm hiện có trong bước này lên 75.

## 2026-10-27 — Fast/Deep và Ranked Round-Robin cho hỏi đáp tài liệu

- User duyệt thiết kế trước khi làm: từng query dense20 + lexical20 → RRF Top10; Fast bỏ hẳn
  reranker, Deep rerank tối đa10 cặp/query và chỉ sắp theo reranker giảm dần. Nếu đồng điểm
  dùng chunk ID ổn định, không dùng lại thứ hạng RRF. Không tự đặt ngưỡng liên quan chưa hiệu chỉnh.
- agent_core/rag/retrieval.py đổi fingerprint RRF/selection recipe. Final nhận 1–3 danh sách
  riêng, Ranked Round-Robin theo query, tối đa5 đóng góp/query,15 chunk tổng,4000 token theo
  estimator utf8_bytes_v1 hiện có (gồm metadata/wrapper). Cùng chunk_id chỉ cấp vào context một lần;
  gặp trùng/quá dài xét tiếp ngay trong lượt đó. Không ép đủ số lượng, không cắt text để vừa budget.
- Khử trùng text hoàn toàn chỉ trong cùng version/section, chỉ chuẩn hóa CRLF/LF, giữ indent code;
  giữ nguyên các đoạn overlap một phần và text ở file/version khác để bảo toàn thông tin/nguồn. Không thêm
  thuật toán similarity hoặc threshold có thể làm mất nguồn mà chưa được hiệu chỉnh.
- backend/services/document_query.py có đường trả candidates_only cho QA: giữ đủ Top10 chưa
  đóng budget. query_is_planned bỏ rewrite lặp, vẫn revalidate owner/scope/version sau inference.
  Search API độc lập vẫn rewrite có history và chọn tối đa5 nguồn, mặc định Fast; Deep lỗi trả lỗi,
  không fallback Fast. API giữ citation ID opaque đúng contract; QA tính budget với label S1… cuối.
- backend/services/document_qa.py gọi retrieval đúng một lần/subquery rồi chọn chung; không so
  score giữa query. Trace lưu candidates theo query không có text, selection budget/estimated_tokens,
  query_contributions, chunk_queries và số ứng viên trùng/vượt budget. coverage tách số đóng góp
  với số nguồn chung được tìm thấy. Phase ghi truy vấn i/N và chế độ, không còn hai dòng giống hệt
  khiến user không biết đang xử lý query nào. Output repair vẫn chỉ sửa câu trả lời trên evidence cũ.
- schemas/chat.py, schemas/document.py thêm chế độ được validate Fast/Deep. Routes chat truyền
  MessageCreate.rag_search_mode (mặc định fast) xuống QA; services/agent_runs.py lưu trong JSONB
  trace hiện có và trả qua API timeline; resume lấy chế độ từ run, không từ lựa chọn mới trên web.
- frontend/templates/chat.html, static/chat.js/styles.css thêm chọn Fast/Deep cùng mô tả ở ô nhập,
  khóa khi đang gửi, gửi theo từng message; timeline hiển thị chế độ đã lưu cả khi mở lại. Không
  đổi footer AI có thể mắc lỗi, tool approval hoặc routing. Không sửa .env, ingestion/model/index,
  dependency/schema DB; không cần upload/re-index lại. Nhánh tóm tắt toàn scope giữ giới hạn64 chunk.
- Kiểm local không gọi provider/model/DB: selector đạt 15 nguồn/[5,5,5] với chunk ngắn, đúng thứ tự
  vòng, dedup không mất lượt, chunk lớn bị skip, biên budget/nhãn cuối, text trùng và overlap;
  hybrid Fast không gọi client reranker, Deep tối đa10 và score/tie không phụ thuộc RRF, API max5.
  Graph thật với model/retrieval/persistence giả kiểm 3 query, chỉ 2 call plan+answer, repair không
  lặp retrieval và lỗi retrieval không tự retry; route nhận/truyền/lưu đúng cả hai mode. Helper
  .tools/runtime-setup/check_rag_modes.py nằm Git ignore, không thêm test/code tracked.
- 13 test hiện có về recorder/timeline/streaming/status đạt; DOM host timer/poll/SSE cũ và kiểm
  gửi Fast/Deep theo message, khôi phục chế độ timeline đạt. Ruff check/format 7 file Python,
  JS syntax, Jinja compile và git diff --check đạt.
  MANUAL_M3_CASES.md cập nhật R04/R06–R08, RRF Top10 và F01–F07 cho user test web; PROJECT_PLAN.md
  ghi cấu hình mới. Chưa chạy benchmark LLM/model thật hoặc kiểm trực quan browser của bản Fast/Deep,
  không dùng số đo CPU cũ làm bằng chứng tốc độ mới. User restart web không --reload rồi Ctrl+F5.

## 2026-10-27 — Chẩn đoán lỗi JSON khi hỏi các chiến lược chunking

- Đọc DB read-only, lọc đúng owner user@personalagent.vn và câu hỏi user cung cấp. Run
  d504077d-f54e-4134-8611-81135abbb925 lúc 00:46:43 +07, Fast, hai query; dense/lexical/RRF
  và final selection hoàn thành. Chọn5 chunk, 3753/4000 token nguồn, đóng góp [3,2].
- Plan thành công; call2 (689 completion token) và call3 sửa output (777 completion token)
  đều error_code=invalid_json. Stop reason cuối document_qa_invalid_output. Chưa tới bước
  validate citation, nên thông báo chung JSON/citation chưa nêu đúng loại lỗi cụ thể.
- Parser agent_core/rag/qa.py gọi json.loads trực tiếp trên toàn bộ output. Adapter DeepSeek
  hiện yêu cầu JSON qua prompt, chưa bind response_format/structured output cho RAG. Lượt
  repair là regenerate trên cùng evidence và error code, không nhận raw output lỗi để sửa.
  Đây là các điểm cần cải thiện; chưa kết luận lỗi thực tế là Markdown fence, escape hay ký tự nào.
- Trace/ModelCall không lưu raw output hoặc chi tiết vị trí JSONDecodeError. Helper chẩn đoán
  answer-only dưới .tools/runtime-setup (Git ignore) đã dựng lại scope owner và kiểm budget3753;
  thử mạng mặc định không kết nối được. Auto-review từ chối escalation vì gửi lại5 đoạn riêng tư
  tới DeepSeek và phát sinh phí chưa được user duyệt riêng. Đã hỏi quyền replay; chưa tái hiện
  được raw output, không bypass rejection. Không chạy lại embedding/retrieval/rerank hoặc sửa code.

- Cập nhật sau khi user cho phép replay: gọi đúng2 lần DeepSeek với cùng5 nguồn, đúng scope
  owner/active version và context3753 token; không chạy lại plan/search/rerank. Call answer
  4,515s/4698 token, call repair 4,172s/4879 token (tổng9577), finish_reason=stop cả hai;
  usage thật được cộng quota. Không tạo message/run hoặc thay đổi tài liệu/index.
- Tái hiện được invalid_json cả hai call mới: dấu ngoặc kép không escape quanh cụm “vách đá”
  bên trong trường answer, JSONDecodeError Expecting ',' delimiter tại dòng1 cột914 và900.
  Repair còn có lỗi cùng loại ở “công ty” và tên phần “Truy xuất nhận biết theo ngữ cảnh”.
  Đây là bằng chứng từ phản hồi tái hiện; raw output gốc vẫn không có để xác định từng ký tự.
- Kiểm offline chỉ escape các cụm lỗi quan sát trong bản sao: cả hai qua JSON/schema/citation,
  cùng nguồn S1/S2/S4. Không đưa thay thế cụm từ vào code sản phẩm và không nới validator.
  Nguyên nhân tái hiện là định dạng chuỗi JSON; cần cải thiện ràng buộc output/repair RAG.
  Báo cáo cùng raw replay ở data/rag-testing/output-diagnostic-20261027 (Git ignore).

## 2026-10-27 — Sửa JSON Output và repair trong document_qa

- User duyệt kế hoạch sửa và kiểm lại DeepSeek tối đa2 call với đúng5 nguồn. Giữ các ghi chú
  chẩn đoán chưa commit, không stage/commit/push thay user. Chỉ sửa file tracked hiện có.
- agent_core/llm/types.py/deepseek.py thêm json_mode keyword tùy chọn, mặc địnhFalse. Adapter
  chỉ truyền response_format={type:json_object} khi bật; không thay cấu hình model toàn cục
  hoặc mang cờ sang request sau. Các call plan/answer/summary/repair của document_qa bậtTrue;
  chat/tool và API rewrite độc lập giữ hành vi cũ. Tài liệu DeepSeek đã đối chiếu:
  https://api-docs.deepseek.com/guides/json_mode/ . Không fallback âm thầm nếu provider báo lỗi.
- agent_core/rag/qa.py bổ sung quy tắc escape bằng ví dụ sinh từ json.dumps; tăng plan v2,
  answer v3, summary notes v2. Giữ parser json.loads strict và mọi kiểm schema/citation. DocumentOutputError có
  vị trí JSON/message tĩnh hoặc schema issue type/location (tối đa8 issue, không có input/context,
  tên field lạ thay bằng unknown). Excerpt tối đa160 ký tự giữ trong bộ nhớ cho repair, str(error)
  chỉ là mã lỗi. Không thêm json-repair/regex tự sửa output hoặc thay thế cụm từ cụ thể.
- backend/services/document_qa.py chuyển error object qua đúng một lượt repair, kèm metadata
  và excerpt tạm thời trong user payload, prompt đánh dấu dữ liệu chẩn đoán untrusted. Dùng cùng
  evidence và allowed_sources; không gọi lại JEV/embedding/search/rerank. Trace calls ghi thêm
  response_format/error_details/repair_details, không ghi excerpt/raw output. Thông báo cuối
  phân biệt JSON/schema/citation nhưng giữ mã document_qa_invalid_output cho tương thích.
- backend/routes/chat.py và frontend/static/chat.js nhận dạng prefix document-qa-plan-v để
  nhãn plan đúng cho cả v1/v2, bao gồm timeline đã lưu. Không đổi UI Fast/Deep, nguồn hoặc footer.
- tests/test_deepseek.py bổ sung transport option theo call, stream escape bị chia giữa chunk,
  parser strict/ngoặc kép/metadata privacy, schema và citation sai, repair một lượt và provider
  không retry. 40 test gồm deepseek/context_middleware/agent_status/graph_streaming đạt.
  Helper regression graph Fast/Deep trong .tools (Git ignore) kiểm json_mode mọi call, repair
  JSON/schema/citation, metadata không chứa marker private, hết lượt lỗi đóng phase và không
  lặp retrieval. Mock summary12 chunk chạy notes theo batch cũng xác nhận mọi call JSON mode,
  đọc đủ12 chunk, không chuyển sang Top-k. DOM host timer/poll/SSE/Fast–Deep đạt;
  không phải nghiệm thu ảnh giao diện.
- Sau kiểm local, gọi DeepSeek thật bằng adapter mới với đúng5 nguồn owner-scoped/active version
  từ run d504077d-f54e-4134-8611-81135abbb925, source budget3753. Dùng prompt/repair/parser
  sản phẩm, nhận response_format=json_object. Thành công ngay attempt1, finish_reason=stop,
  16,316s, prompt4116/completion769/tổng4885 token; schema/citation hợp lệ, nguồn S1/S2/S4.
  Dùng1/2 call được duyệt, không cần call thứ2. Usage cộng quota thật. Không chạy lại plan,
  embedding/search/rerank, không tạo message/run hoặc đổi tài liệu/index. Báo cáo và output
  kiểm riêng tại data/rag-testing/json-fix-20261027 (Git ignore); không phải E2E toàn web.
- Cập nhật PROJECT_PLAN.md và MANUAL_M3_CASES.md với J01–J03 để user chạy lại. Không thêm
  file tracked/dependency/migration/.env; user restart web không --reload rồi Ctrl+F5 để nạp code.
  Ruff check/format6 file Python, JS syntax và git diff --check đạt; đã kiểm lại18 test adapter/RAG
  sau khi hoàn thiện giới hạn excerpt, đều đạt. Không cộng lượt chạy lặp vào tổng40 test khác nhau.

## 2026-10-27 — Chẩn đoán lỗi citation khi hỏi bảng ANNOY/HNSW

- Đọc trace theo owner user@personalagent.vn, run37453adf-0e66-4c10-86f3-9da06b62a0ef
  lúc11:26:40 +07. Fast/plan v2/answer v3; JSON/schema đã qua, answer và repair đều
  citation_sources. error_details rỗng, không raw output: chưa biết điều kiện con/nhãn nào sai.
- Kiểm private active-version chunks: bảng3-2 ANNOY/HNSW đã được trích xuất/index ở trang119,
  chunk a9fe3bbd-eee5-5623-aed8-a41d1fcf1e90. Bộ5 evidence cuối ở109/131/136–137/106/111
  đều không có ANNOY/HNSW trong text. Chunk giới thiệu bảng hạng6/4/3; chính bảng hạng5 của
  query3, chưa vào Top10 query1/2. RRF đang ưu tiên nhiều đoạn chung về bộ nhớ/cập nhật.
- Phát lại offline đúng30 candidates/text đã lưu: trùng chính xác bộ5 nguồn/3971 token,
  đóng góp [3,1,1]. Lúc xét bảng context3606, nếu thêm bảng lên4071 >4000 nên skip; bảng đứng
  riêng513 token. Ranking đầu vào chưa tốt làm budget dành cho nguồn chung, selector đang làm
  đúng quy tắc Round-Robin/Fast đã chốt. Không cần thay đổi giới hạn để kết luận vấn đề này.
- Lexical đang OR cả tên thuật toán và từ chung/số3/2; thiếu trace từng nhánh nên chưa định
  lượng được dense/lexical gây lệch bao nhiêu. Cần cải thiện ranking thực thể/tham chiếu bảng,
  đồng thời chi tiết hóa citation_sources và repair/no-answer; không nới kiểm source binding.
- Không gọi LLM/embedding/reranker hoặc ghi DB, không sửa code sản phẩm trong lượt chẩn đoán.
  Helper Git ignore .tools/runtime-setup/diagnose_annoy_citations.py; kết quả/report tại
  data/rag-testing/annoy-citation-diagnostic-20261027. Thiếu nguồn bảng là kết quả đã xác minh;
  chưa khẳng định nó gây điều kiện citation_sources nào vì output gốc không được lưu.

## 2026-10-27 — Một query sau rewrite, tối đa10 nguồn và ngân sách8000

- User chốt bỏ phân rã và Ranked Round-Robin; giữ rewrite/history, Fast/Deep, mục tiêu10 chunk,
  nếu vượt8000 token thì bỏ bớt nguyên chunk theo thứ hạng. Giữ các ghi chú chẩn đoán chưa commit.
- agent_core/rag/qa.py: QueryPlan chỉ có query/mode, plan v3 yêu cầu đúng một câu độc lập giữ
  mọi ý/tên/số/phủ định. Schema extra=forbid từ chối trường queries cũ; không tự chọn một ý rồi
  làm mất các ý khác. Không history giữ nguyên query gốc; có history dùng query đã resolve.
  Answer v4 nhận câu hỏi đầy đủ/coverage/allowed_sources, không còn danh sách aspects.
- backend/services/document_qa.py: bỏ vòng lặp subquery, chỉ gọi retrieve_document_evidence một
  lần với plan.query. Giữ JSON mode và tối đa1 repair, không rewrite/search lại trong retrieval
  hoặc repair. Progress giữ ID query.1 để ổn định, bỏ nhãn truy vấn i/N vì chỉ còn một query.
  Trace có đúng1 retrieval, coverage.query/selected_chunks và selection.query_count=1/max_chunks=10;
  bỏ query_contributions/chunk_queries/quota5/query. Run/trace cũ không bị sửa hoặc xóa.
- agent_core/rag/retrieval.py: xóa select_ranked_evidence/thuật toán Round-Robin; pack_evidence
  duyệt một danh sách Top10 theo thứ tự đã xếp hạng, không tự sort. Fast dùng RRF, Deep dùng
  reranker; giữ dedup chunk_id và text giống hoàn toàn trong cùng version/section, giữ indent/
  overlap một phần/khác version. Hard cap8000 gồm metadata/wrapper, label S1… được tính ngay
  trong QA. Bỏ nguyên chunk không vừa và xét tiếp trong Top10; không cắt text hoặc lấy ứng viên
  ngoài Top10 để bù. Search API giữ ID opaque, QA dùng nhãn S1…; đổi packing recipe v3.
- backend/services/document_query.py, schemas/document.py: standalone hybrid API cũng tối đa10
  nguồn trong8000 token; dense20/lexical20 và RRF Top10/Deep10 không đổi. Rewrite độc lập của
  search API vẫn một query. Scope owner/version và kiểm citation/JSON mode giữ nguyên.
- backend/core/config.py, agent_core/context_budget.py, .env.example: RAG default/max8000,
  tổng context đầu vào default36000 thay32000 để giữ system2000/memory4000/history16000/tool6000.
  Đọc riêng các giá trị budget xác nhận .env hiện không có override; get_settings thực tế ra
  8000/36000 và tổng section36000 hợp lệ. Không sửa .env riêng hoặc in config/credential khác.
- Summary giữ all-chunks tối đa64 và map-reduce theo batch (budget nguồn8000), không biến tóm
  tắt toàn bộ thành Top10. Không đổi ingestion, chunk size512/tokenizer F2, embedding/index,
  model/device, timeout hoặc budget tổng call/token của run; không cần upload/re-index lại.
- tests/test_deepseek.py bổ sung plan một query giữ đủ tiêu chí và từ chối queries/NUL/rỗng.
  tests/test_context_middleware.py kiểm10 nguồn vượt4000 nhưng trong8000, cắt nguyên chunk khi
  vượt8000/hardcap khi caller xin16000, thứ tự/dedup/biên label+metadata và defaults context.
  77 test deepseek/context/status/streaming/recorder/runtime/safety đạt. Test config mới ban đầu
  thiếu credential bắt buộc trong fixture; đã bổ sung giá trị giả, không đọc secret máy user.
- Helper Git ignore .tools/runtime-setup/check_single_query_rag.py chạy graph và hybrid thật
  với provider/search/reranker/DB giả: Fast/Deep một query/retrieval, history, sửa output vẫn
  không lặp retrieval,10 nguồn/8000, API giữ opaque ID, summary đủ12 chunk theo batch đều đạt.
  Không gọi LLM/model thật, không ghi DB hoặc chạy lại câu ANNOY/HNSW trong bước này; chưa
  khẳng định thay thiết kế đã giải quyết chất lượng ranking hoặc lỗi citation_sources trước đó.
- Cập nhật PROJECT_PLAN.md, MANUAL_M3_CASES.md (H01/H03/F01–F07/T02/T03) và README Docker để
  phản ánh một query/8000. Giữ ghi chép và báo cáo lịch sử có multi-query/4000, không sửa dữ liệu cũ.
  Không thêm file tracked/dependency/migration; user restart web rồi test lại, không --reload trên Windows.
  Ruff check/format9 file Python và git diff --check đạt; không stage/commit/push thay user.

## 2026-10-27 — Tự gia hạn phiên đăng nhập khi đang sử dụng

- Bổ sung POST /api/auth/refresh: yêu cầu cookie còn hợp lệ và header X-Session-Activity=1;
  kiểm tra user hiện tại trong DB, dùng role hiện tại để tạo JWT mới. Giữ HttpOnly, SameSite=Lax,
  Path=/, Secure theo config và Max-Age theo AUTH_TOKEN_EXPIRE_MINUTES (hiện 60 phút).
  Response 204/no-store, không trả JWT cho JavaScript. Token hết hạn/sai, user bị khóa/xóa đều 401.
- base.html đánh dấu trang có đăng nhập; app.js chỉ khởi tạo heartbeat trên các trang này.
  Gia hạn ngay khi mở trang đang hiển thị và tối đa một lần/phút khi có thao tác trong 90 giây gần
  nhất; tick kiểm tra mỗi 15 giây. Gõ/bấm/cuộn, quay lại tab và focus được tính là hoạt động;
  event DOM giả, polling tài liệu và sự kiện mạng trở lại không tự mở cửa sổ hoạt động mới.
- chat.js nối setBusy với session manager: chờ AI/stream/resume vẫn gia hạn cả khi tab ẩn.
  Khi AI xong thì trở lại quy tắc hoạt động/idle. Không sửa pipeline RAG, không gửi lại message
  hoặc retry tool để gia hạn; lỗi mạng tạm thời ở heartbeat không làm gián đoạn câu trả lời.
- Request gia hạn có timeout 10 giây, không chạy song song trong một tab. Đăng xuất dừng heartbeat,
  chờ refresh đang chạy trước khi xóa cookie; Web Locks tuần tự hóa thay đổi cookie giữa các tab
  khi trình duyệt hỗ trợ. Cờ localStorage không chứa token báo đăng xuất cho tab khác và chặn
  refresh còn trong hàng đợi; đăng nhập/đăng ký thành công xóa cờ. Storage bị chặn vẫn dùng được
  gia hạn trong tab, nhưng không có phối hợp logout bằng storage giữa các tab.
- pagehide dừng timer, pageshow persisted khôi phục khi quay lại bằng back/forward cache.
  Phiên đã hết hạn hoặc mất mạng quá thời hạn vẫn phải đăng nhập lại; đây là gia hạn bằng phiên
  đang hợp lệ, chưa thêm refresh token dài hạn hoặc bảng session/revocation phía server.
- 27 test security/public pages/agent status/streaming đạt: gia hạn expiry và cookie ở cả Secure
  true/false, role từ DB, từ chối phiên không hợp lệ/thiếu header, polling không Set-Cookie,
  logout xóa cookie, marker/script đúng trang và regression SSE. Không dùng DB/LLM thật.
- Helper Git ignore .tools/runtime-setup/check_session_renewal.cjs chạy app.js thật trong Node VM
  với DOM/fetch/timer giả: active/idle, event giả, AI tab ẩn, phục hồi mạng/401, BFCache, logout
  đang refresh/queued Web Lock và mô phỏng 65 phút chờ AI đều đạt. Đây không phải kiểm tra tương
  tác bằng browser thật hoặc chờ thực tế 65 phút. Node syntax, Ruff check/format và diff check đạt.
- Cập nhật PROJECT_PLAN.md và hướng dẫn gia hạn trong docs/rag/MANUAL_M3_CASES.md. Không thêm file
  tracked, dependency, migration hay sửa .env; không restart dịch vụ, stage/commit/push thay user.
  User restart web (Windows không --reload), Ctrl+F5; phiên cũ hết hạn thì đăng nhập lại một lần.

## 2026-10-27 — Chẩn đoán citation khi hỏi vì sao gọi là khóa lạc quan

- Đọc run c4e2802f-473c-4845-b98d-289232aae095 đúng owner user@personalagent.vn, lúc12:58:40 +07.
  Plan v3 lần đầu thiếu mode (invalid_schema); repair thành công, giữ một query và 8 message history.
- Fast chạy một retrieval dense20/lexical20/RRF10; pack9 chunk/7975 token, budget_skip1 đúng cap8000.
  S1 trang406/mục10.5.1 có đoạn Optimistic Locking mô tả kiểm phiên bản lúc ghi và thử lại khi xung
  đột. Xác nhận nguồn trực tiếp về cơ chế đã vào context; chưa đánh giá đủ mọi ý nghĩa câu hỏi.
- Answer v4 và repair đều citation_sources, JSON/schema đã qua; error_details={} nên không biết
  vi phạm sources ngoài allowed, mismatch inline/list, duplicate, answerable/citation hay answer
  rỗng. Raw output không được lưu; không khẳng định lỗi nằm ở điều kiện con nào hoặc giống hệt
  lượt ANNOY/HNSW. Repair hiện chỉ nhận mã chung, thiếu hướng dẫn sửa theo lỗi cụ thể.
- Chưa sửa code sản phẩm. Đề xuất bổ sung chẩn đoán citation an toàn/chi tiết cho trace và repair,
  làm rõ prompt về inline/sources/answerable, giữ kiểm tra nguồn và một lần repair; không thay
  retrieval để chữa một lỗi đầu ra chưa rõ. Không gọi LLM/model, không ghi DB.
- Helper Git ignore .tools/runtime-setup/diagnose_optimistic_citations.py; report/inspection ở
  data/rag-testing/optimistic-citation-diagnostic-20261027. Không tạo thêm file tracked.

## 2026-10-27 — Backend quản lý nguồn, LLM chỉ trả văn bản

- User chốt liệt kê mọi chunk đã cung cấp cho AI ở cuối câu trả lời. QA có9 chunk trong context
  thì có9 mục nguồn, không yêu cầu LLM khai báo chunk thực sự dùng hoặc gắn nguồn vào từng câu.
  Đây là danh sách đầu vào cho AI, không phải chứng minh attribution/ngữ nghĩa từng nhận định.
- agent_core/rag/qa.py: answer v5 và summary notes v3 yêu cầu text/Markdown thường, không JSON,
  không inline label/sources/answerable. Xóa GroundedAnswer và các validator citation_sources/
  citation_link/citation_syntax từ output model. Plan v3 vẫn QueryPlan JSON strict, một query.
  Text rỗng/NUL/quá24000 ký tự có mã/details riêng, không lưu nội dung vào diagnostics.
- backend/services/document_qa.py: chỉ plan/plan repair bật JSON mode; answer/notes/text repair
  ghi response_format=text. Giữ tối đa1 repair trên cùng input, budget/quota/provider/scope/
  timeout và một retrieval; không đọc nguồn từ output model. Backend trace reference_policy=
  supplied_context và reference_sources; có evidence thì không tự suy ra answerable. Không tìm
  được evidence thì trả thông báo rõ và không có nguồn; model tự nói thiếu dữ kiện khi có context
  vẫn giữ nguồn đã cấp. Lỗi quote trong answer không còn phải parse JSON.
- Summary giữ mọi chunk trong all-chunks scope (tối đa64), propagates lineage từ metadata qua
  các batch/nhiều tầng, không dùng nhãn trong notes model. Link cuối vẫn trỏ chunk gốc/version.
  Progress thay kiểm citation output bằng “Chuẩn bị nguồn tham khảo” do backend thực hiện.
- frontend/static/chat.js: footer “Nguồn tài liệu được cung cấp”, một mục/chunk có nhãn/tên/trang
  hoặc khoảng trang và link mở nguồn. Sắp nhãn theo số để reload giữ S1…S10 đúng thứ tự; giữ
  nguyên từng chunk dù cùng file/trang, không đoán trang khi TXT/DOCX không có. Tooltip giải thích
  AI có thể không dùng hết từng đoạn. Giữ inline rendering cho message cũ, filter nguồn đã xóa.
- Binding message/owner/document/version/chunk, commit cùng câu trả lời, revalidate/lock scope,
  API history/source/download và kiểm quyền giữ nguyên. Không sửa migration, .env, model/index,
  Fast/Deep, giới hạn10 chunk/8000 token của QA hoặc phần ingestion; không cần upload/re-index lại.
- tests/test_deepseek.py cập nhật protocol/repair, bổ sung kiểm graph thật với provider/DB/search
  giả:9 chunk→9 bindings, text có quote, answer không đủ dữ kiện, model tự ghi S99 không thêm
  nguồn, empty evidence, repair rỗng một lần, summary12 chunk qua batch vẫn giữ12 nguồn gốc.
  106 test adapter/context/status/streaming/recorder/runtime/safety/security/public pages đạt.
- Helper Git ignore check_single_query_rag.py cập nhật và đạt Fast/Deep ordering, history,
  budget10/8000, một retrieval, repair, API opaque IDs và summary. check_reference_footer.cjs
  đạt9 link, sort số, trang/khoảng trang/no-page, title text an toàn, target nguồn, reload/delete;
  check_progress_ui.cjs và check_session_renewal.cjs tiếp tục đạt. Đây là Node VM/DOM host giả,
  chưa kiểm browser thật hoặc gọi LLM/model thật ở lượt này; không ghi DB dữ liệu user.
- Cập nhật PROJECT_PLAN.md và MANUAL_M3_CASES.md (Q02/J01–J04/T02/trace) theo thiết kế mới,
  giữ ghi chú/report/run lịch sử. Không tạo thêm file tracked, dependency; không restart dịch vụ
  hoặc stage/commit/push thay user. User restart web (Windows không --reload) và Ctrl+F5 để test.

## 2026-10-27 — Tool search_web dùng OpenRouter key và OpenAI native search

- User duyệt triển khai và dùng key thật test. Tạo đúng một file code tracked mới:
  agent_core/tools/web_search.py; dùng httpx hiện có, không thêm SDK/dependency/migration.
  Tool nội bộ read_only, user/admin dùng được khi bật và allowlist, không cần approval gửi mail.
  SearchWebInput chỉ có query; mô tả yêu cầu query độc lập, giải nghĩa history, giữ ngôn ngữ user.
- WebSearchConfig dùng SecretStr, model riêng mặc định openai/gpt-5.4-mini, timeout60 giây,
  max_output_tokens2000. URL API cố định https://openrouter.ai/api/v1, không lấy endpoint/key
  từ tool input; trust_env=false, không follow redirect. Giữ key ngoài prompt/output/trace.
- Trước paid request kiểm endpoint tag=openai có native_tools.openrouter:web_search. Request
  pin provider.only=[openai], allow_fallbacks=false, engine=native, tool_choice=required;
  max_tool_calls2 là giới hạn server-tool yêu cầu, không khẳng định native luôn tôn trọng hard cap.
  Chỉ trả kết quả khi provider=OpenAI, có web_search_requests, output hoàn chỉnh và URL annotations
  hợp lệ. Chuẩn hóa sources URL/title, số token/cache/cost/latency/số search; giữ citation Markdown
  của provider, không tạo lại S1/sources bằng model chat. Nguồn web không nhập vào document_qa.
- Lỗi timeout/key/credit/quyền/rate limit/provider/thiếu search hoặc nguồn có mã và thông báo an
  toàn. Không lộ response body/credential, không retry request có phí. Timeout/connection ghi
  billing_status=unknown khi chưa có usage. Không coi chỉ thêm engine=native là bằng chứng đủ.
- Bổ sung return_direct tùy chọn cho create_context_tool và direct_answer trong AgentState.
  Với đúng một call của host tool được đánh dấu này, graph chuyển tool→final và phát nguyên
  câu trả lời/citation; lỗi cũng trả thông báo an toàn, không chạy thêm LLM để lặp paid search.
  Trường hợp nhiều call giữ luồng thường. Tool/approval/authorization và kiểm catalog vẫn chạy;
  checkpoint cũ thiếu direct_answer mặc định false. Các tool hiện có mặc định không return_direct.
- ToolResult có usage tùy chọn; runner cộng token tool vào budget/stream tổng, quota ngày tính
  event tool_completed, kể cả output đã dùng token rồi lỗi. Recorder tạo ModelCall riêng cho
  model trong tool và giữ cost/usage/source trong ToolCall.result_data. API/history timeline cộng
  native tokens vào tổng, hiển thị token/cost USD/search count ở live và reload; metadata dài
  xuống dòng riêng để tránh chèn nội dung. Không trộn native token vào model DeepSeek cũ.
- Executor khi thu hồi quyền sau RPC vẫn loại nội dung/nguồn private nhưng giữ known usage và
  metadata billing cần thiết; không xóa dấu vết phí đã phát sinh. Có test kiểm nội dung không lộ.
- Settings/dependencies_tools/.env.example đăng ký tool theo cấu hình. Đã bật WEB_SEARCH_ENABLED
  trong .env host và thêm search_web nếu có TOOL_ALLOWLIST override; giữ nguyên API key hiện có.
  .env vẫn Git ignore. .env.docker.example/compose.yaml/README có DEV_WEB_SEARCH_* default-off;
  kiểm compose config đạt, chưa build/test live Docker. Không thay config JEV/DeepSeek/RAG.
- Probe key thật: HTTP200, native OpenAI, một search, đúng Python3.14.0 ngày7/10/2025/link chính
  thức Python.org;4,548s/0,0152929 USD. Hai lượt backend đầu đã giữ link/history nhưng câu trả lời
  tiếng Anh do args dịch sang Anh. Sửa mô tả/schema giữ ngôn ngữ rồi kiểm hai lượt thật mới:
  3.14.0 ngày7/10/2025 (9,378s/0,0156739 USD/8959 native token); câu nối tiếp3.13.0 ngày7/10/2024
  (7,817s/0,01539415 USD/8756 native token). Cả hai trả tiếng Việt, đúng link và một tool call.
- Live test qua FastAPI auth/message/SSE/history/run với DB/JEV/DeepSeek/OpenRouter thật trên
  tài khoản fixture riêng, không chọn/đọc tài liệu hoặc chat của user. DB xác nhận query và
  capability đều provider=jev, selection policy bind search_web, không fallback; history giải
  “ngôn ngữ đó” thành Python. Run cuối fca228bc-2202-48ae-bd9a-930788384cb6 và
  0556e09d-2d79-4be1-aa93-8ab84d03c98c. Nguồn/message/model usage đều đọc lại được.
- Tổng5 native searches/probe: OpenRouter báo0,07718375 USD, gồm inference/search của những
  request này, chưa gồm JEV/DeepSeek. Không gọi thêm model khi xác minh DB cuối. Sandbox proxy
  từng làm SDK JEV không kết nối; test thật ngoài sandbox đạt, không sửa transport JEV sản phẩm.
  Harness từng nhầm provider của selection row phải là jev; sửa assertion theo đúng phase rồi
  xác minh chỉ đọc, không chạy lại paid calls để chữa assertion.
- 343 test graph/tool/quota/recorder/SSE/RAG/auth/routing/plugin/MCP/checkpoint đạt. Ba fixture
  MCP ban đầu bị chặn subprocess/temp/TLS bởi sandbox, rerun ngoài sandbox và bộ cuối đều đạt.
  Node DOM host kiểm cost/token/search count live/reload, timer, session renewal, nguồn RAG cũ;
  Ruff format/check, JS syntax, diff check và Compose config đạt. Chưa thao tác browser thật.
- PROJECT_PLAN.md và MANUAL_JEV_CASES.md có W01–W06. Report/artifacts Git ignore ở
  data/web-search-testing/20261027/REPORT.md. Helper Git ignore trong .tools/runtime-setup gồm
  probe_web_search_live.py, enable_web_search.py, test_web_search_e2e.py,
  diagnose_jev_web_search.py, verify_web_search_saved.py; không lưu mật khẩu fixture/key/cookie.
  Không restart dịch vụ hoặc stage/commit/push thay user; user restart web và Ctrl+F5 để dùng.
