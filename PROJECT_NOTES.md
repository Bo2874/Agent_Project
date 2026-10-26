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
