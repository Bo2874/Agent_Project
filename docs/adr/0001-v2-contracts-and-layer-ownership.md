# ADR 0001 — Contract V2 và ownership giữa các lớp

- Trạng thái: Accepted.
- Ngày: 2026-10-18.
- Phạm vi: Ngày 77, nền tảng cho M1–M6 trong `PROJECT_PLAN.md`.
- Contract triển khai: `agent_core/contracts/`; hướng dẫn field và ví dụ ở
  [tài liệu contract](../contracts/README.md).

## Bối cảnh

Ứng dụng hiện chạy FastAPI → LangGraph → LangChain model/tool, MCP stdio, ToolExecutor,
PostgreSQL checkpoint và SSE. Các phần sắp thêm gồm plugin catalog, Jev routing,
LlamaIndex RAG, task DAG/sub-agent, Redis cache và event replay. Nếu mỗi adapter tự định nghĩa
payload/ownership, quyền truy cập và budget dễ bị bỏ qua khi đổi framework hoặc chạy nhánh con.

## Quyết định

Dùng Pydantic model có version trong package `agent_core/contracts`. Package chỉ phụ thuộc
Python, Pydantic và JSON Schema validation, không import backend, SQLAlchemy, LangGraph,
LangChain, LlamaIndex, Redis hay provider SDK. Đây là boundary data; không tạo schema DB,
client/network connection, graph node, scheduler hoặc API endpoint từ các model này.

Mọi model dùng `schema_version="1.0"`, `extra="forbid"`, validate default, reject NaN/Infinity,
frozen attributes và revalidate nested model khi đưa qua boundary mới. Các tuple ID không trùng.
JSON Schema có thể lấy trực tiếp từ model/TypeAdapter. Lưu và đọc lại bằng JSON để không đưa
đối tượng framework/session/credential vào state hoặc checkpoint.

`frozen` không làm dict lồng bên trong bất biến hoàn toàn. Producer/consumer không được sửa
mapping sau validation hoặc dùng `model_construct`/`model_copy(update=...)` để bỏ validation ở
boundary. Sau đọc DB/cache/queue phải validate lại. Validation đúng schema chưa chứng minh dữ
liệu được phép truy cập, schema output đúng chưa chứng minh task hoàn thành.

### Phân định ownership

| Thành phần | Sở hữu | Input/output qua boundary | Không được tự quyết |
| --- | --- | --- | --- |
| FastAPI và backend services | User identity, conversation/run ownership, credential, document ACL, quota DB, flags từ deploy | Trusted runtime context; request đã xác thực | Không nhận `user_id`, grant hoặc secret từ model để đổi quyền |
| PluginManager/catalog (M1) | Manifest/version, connection lifecycle, canonical capability ID, host policy | `CapabilityDescriptor` sau lọc user/role/grant | MCP annotation không phải quyền thực thi; không chạy command do chat cung cấp |
| Router và provider adapter (M2) | Chuẩn hóa output rules/Jev/LLM, shortlist, fallback, usage của quyết định | `RoutingDecision` | Không cấp quyền, không gọi tool, không tự lập plan hoặc sửa budget |
| LangGraph/orchestrator | Workflow control, ready-set, root budget, child scope, cancel/approval/resume | Routing decision, task proposal, evidence và event của node | Không tạo quyền mới cho child; không làm thêm scheduler có state machine cạnh tranh |
| LlamaIndex/RAG adapter (M3) | Parse/chunk/embed/retrieve/rerank và giữ provenance | `RetrievalResult`/`EvidenceReference` sau filter do backend cấp | Không tự chọn owner, tự tạo policy hoặc làm bộ điều phối thứ hai |
| Planner (M4) | Task proposal và acceptance criteria | `TaskSpec` | Không reserve quota, không bảo đảm dependency tồn tại/acyclic hoặc quyền được cấp |
| Plan validator/scheduler (M4) | Kiểm tra toàn DAG, readiness, resource conflict, allocation và plan version | Task đã kiểm tra theo trusted context | Không coi field `allowed_capability_ids` trong plan là grant thật |
| ToolExecutor | Schema, allowlist/role/grant hiện hành, approval, timeout/retry/idempotency | ToolContext hiện có + tool ID/arguments | Không bỏ gate vì router confidence cao, cache hit hoặc parent đã chạy tool khác |
| PostgreSQL services | Record nguồn, transaction, task projection, job lease/fencing, quota và audit | Contract JSON đã validate + runtime identity | Không chia sẻ AsyncSession giữa nhánh song song |
| Redis adapter (M5) | Cache và TTL/version key | Cùng contract đã validate, có user/ACL/corpus/catalog scope | Không làm nguồn quyền, approval, quota hay checkpoint chính |
| Recorder/event service | Kiểm tra run ownership, cấp event UUID/sequence, ghi/replay, redact/project public data | Discriminated `RunEvent` | Không lấy identity/sequence do model hoặc browser tự gán |
| SSE/frontend | Hiển thị trạng thái/kế hoạch/evidence được phép, dedup theo event ID khi có V2 | Event/public projection do backend trả | Không thực thi tool, suy ra quyền từ event, hoặc hiển thị suy luận nội bộ |

Các thành phần M1–M6 trong bảng là ownership đã chốt cho triển khai sau; Ngày 77 chỉ cung cấp
contract/config và không tự khởi động các thành phần đó.

### Ranh giới tin cậy

1. Backend lấy user/grant/conversation/run scope từ auth và DB, không từ JSON của model.
2. Catalog lọc capability theo scope trước khi gửi shortlist cho router.
3. `RoutingDecision` kiểm tra ID đã chọn là tập con candidate, nhưng adapter vẫn phải kiểm tra
   candidate thuộc catalog snapshot hợp lệ. Contract không tra catalog/DB.
4. Host xác định risk/approval của `CapabilityDescriptor`; unknown/write/destructive phải yêu cầu
   approval. Annotation remote chỉ là metadata tham khảo, không được dùng để hạ policy.
5. Backend/retriever kiểm tra document/version/chunk ownership trước khi đưa evidence vào model;
   kiểm tra lại khi mở citation hoặc sau khi đọc cache. `trust="untrusted"` không đổi bằng prompt.
6. Scheduler lấy giao giữa capability task đề xuất và quyền thực của root/worker; reserve tổng
   quota trước khi phát nhánh. ToolExecutor kiểm tra lại ngay trước side effect.
7. Model output và tài liệu không được tạo trực tiếp event envelope có quyền ghi DB.
   Recorder gắn envelope; dữ liệu private chỉ được serialize cho user đã xác thực.

### Schema và định danh

- `schema_version` là version wire contract; khác `model_version`, capability/Skill version,
  retrieval config version, catalog version và routing policy version.
- UUID dùng cho root run, attempt và document/version/chunk; task ID là logical ID như `read_a`,
  có namespace `(root_run_id, plan_version, task_id)`.
- Capability ID canonical do host cấp, có thể giữ tên tool hiện tại như `demo__calculate`.
  JSON Schema reference là logical ID, không phải URL để model yêu cầu tải schema/network.
- Route probability trong contract mô tả quyết định cuối của provider, không trộn probability của
  một quyết định Jev cũ với route fallback do rules/LLM chọn. Raw provider response được adapter
  xử lý riêng. Rules/LLM không có đo xác suất có thể để distribution rỗng, confidence `null`.
- `confidence` là metadata của provider có version, không phải quyền hoặc bảo đảm tỷ lệ đúng.
- Thay field/type/enum hoặc thay meaning phải có schema version và migration/consumer rõ ràng.
  Với `extra=forbid`, thêm field vào wire payload cũng cần kế hoạch tương thích; không âm thầm
  chấp nhận schema mới. Ngày 77 chỉ hỗ trợ version `1.0`.

### Events và tương thích hiện tại

V2 có `routing_decided`, `retrieval_completed`, `plan_created`, `task_status`, `run_status`.
Mỗi kind có model riêng; union phân biệt theo `kind`, không có `payload: Any` tự do.
Envelope có UUID, root run, sequence dương, timestamp timezone-aware chuẩn hóa UTC, optional
task/plan/attempt correlation. Event của task cần plan version; event execution cần attempt ID.

Sequence do recorder cấp theo root run, không reset theo task hoặc attempt. Retry/replay giữ
event ID/sequence của record đã persist; adapter frontend không tạo ID mới. UUID/timestamp không
thay thế sequence. Global uniqueness, delivery ordering, transition hợp lệ và ownership cần DB/service
kiểm tra khi làm event persistence; validator của một event không thể bảo đảm các thuộc tính đó.

Ngày 77 giữ nguyên `AgentLoopEvent`, `AgentGraphStreamEvent`, recorder hiện tại và giao thức
SSE `agent_status`/`delta`. V2 envelope chưa được gắn vào production stream. Khi tới M2/M4,
thêm adapter/public projection và event persistence có flag, test tương thích trước khi chuyển UI.
Evidence event chỉ mang reference; endpoint nguồn có auth mới trả nội dung. Không thêm field
suy luận nội bộ, raw credential hoặc arbitrary tool output vào V2 event.

### Feature flags

`Settings.v2_features` là snapshot `FeatureFlags` từ env deploy. Master `V2_ENABLED=false`
và bảy sub-flag đều mặc định `false`. Consumer dùng `is_enabled(feature)` để áp dụng master;
không đọc riêng sub-flag để quyết định chạy. Dependency flag: planning cần routing;
parallel cần planning. Cấu hình thiếu dependency bị từ chối cả khi master đang tắt để tránh
cấu hình tiềm ẩn sai lúc bật master.

Flags là ý định bật tính năng, không chứng minh implementation đã sẵn sàng và không thay grant
của user. Tại Ngày 77 các implementation V2 chưa nối vào runtime nên bật flag chưa tạo thêm
chức năng. Khi thêm adapter phải kiểm tra cả flag, availability và quyền. `get_settings()` đang
cache theo process; thay env cần restart, không phải cơ chế đổi flag trực tiếp qua chat.

## Hệ quả và nghiệm thu

- Adapter có thể thay SDK/framework mà vẫn giữ payload; JSON Schema giúp review cùng contract.
- Boundary validation có thêm chi phí và có thể reject payload từng được Python ép kiểu;
  số đếm là integer thật, boolean không được làm token/page/sequence, timestamp phải có timezone.
- Các cap như 8 tool được chọn/8 task ID trong event là giới hạn wire bản đầu; tăng giới hạn phải
  review contract và quota cùng nhau. Không coi cap này là scheduler hoặc reservation đã triển khai.
- RAG result là **một passage**, không phải toàn bộ batch/query response; retrieval layer quản lý
  uniqueness/corpus version của batch. Citation ID phải phân biệt các evidence trong cùng response.
- TaskSpec chỉ validate cấu trúc, self-dependency và input-output dependency cục bộ; cycle xuyên
  task, missing node, root quota, grant và side effect cần full plan validator của Ngày 110.
- Unit tests kiểm tra JSON round-trip/JSON Schema, selection/provenance/budget/scope sai, flag
  default/dependency/master switch. Regression graph/SSE xác minh luồng hiện tại vẫn tương thích.
- Không cần migration database hoặc gọi provider trả phí cho thay đổi contract/config này.

## Các phương án đã cân nhắc

- Dùng raw dict/Any xuyên mọi lớp: dễ prototype nhưng sai field/correlation chỉ lộ khi thực thi.
- Dùng LangChain/LlamaIndex object làm contract chung: gắn persistence và API vào SDK/version.
- Thay ngay state/SSE cũ: tăng phạm vi migration trước khi có plugin/RAG/worker thực tế.

Chọn package contract riêng và tích hợp từng mốc có kiểm thử để kiểm soát các chi phí trên.
