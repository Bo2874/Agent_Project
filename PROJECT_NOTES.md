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
