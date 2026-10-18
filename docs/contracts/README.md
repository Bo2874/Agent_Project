# Contract V2 — schema 1.0

Public imports ở `agent_core.contracts`. Đây là DTO giữa các thành phần, không phải schema
request cho frontend hoặc response nguyên bản từ Jev/MCP. Các producer/consumer cần áp dụng
[ADR ownership](../adr/0001-v2-contracts-and-layer-ownership.md) cùng validation.

## Quy tắc chung

- Python 3.11+, Pydantic v2; mọi model có `schema_version="1.0"`, forbid field lạ, reject số
  NaN/Infinity và version chưa hỗ trợ. Numeric counter dùng strict integer, không nhận boolean.
- Logical ID: chữ thường, số, underscore và các segment phân cách bằng `.`/`:`, bắt đầu bằng chữ;
  tối đa 128 ký tự. UUID document/run/attempt được parse từ JSON string.
- Dùng `Model.model_validate_json(payload)`, `model_dump_json()` và `model_json_schema()`;
  event union dùng `RUN_EVENT_ADAPTER`. Validate lại khi nhận payload từ DB/queue/cache.
- Model không có owner/secret; `user_id`, credential, grant và session lấy từ trusted runtime context.
  Việc thiếu các field này trong DTO không thay thế kiểm tra quyền ở backend.
- Frozen attributes không bảo vệ hoàn toàn nested dict; không mutate DTO sau validation hoặc
  dùng constructor bỏ validation ở boundary.

## CapabilityDescriptor

Metadata để discover trước khi nạp schema/Skill instructions:

| Field | Ý nghĩa |
| --- | --- |
| `capability_id`, `kind` | ID canonical; `tool`, `skill`, `retriever`, `workflow` |
| `name`, `description`, `tags` | Metadata tìm kiếm, có giới hạn độ dài/số lượng |
| `version`, `plugin_id` | Version của capability; plugin null nếu built-in |
| `required_capability_ids` | Dependency không trùng, không trỏ chính nó |
| `input_schema_ref`, `output_schema_ref` | Logical reference; không mang instructions/schema lớn |
| `side_effect`, `requires_approval` | Policy metadata do host xác định; mặc định unknown/true |

Write/destructive/unknown không được khai báo `requires_approval=false`.
Descriptor không chứa handler hoặc credential và không tự grant quyền thực thi.

## RoutingDecision

- `route`: `chat`, `tool_task`, `document_qa`, `planned_task`, `clarify`.
- `skill_id`; `candidate_tool_ids` tối đa 64; `selected_tool_ids` tối đa 8, không trùng và là
  tập con candidate. Chat/clarify không chọn tool. Chọn `none`/`search_more` ở provider phải được
  adapter chuyển thành không chọn tool + fallback/reason phù hợp; không dùng ID tool giả.
- `probabilities`: map route → probability trong [0,1], tổng 1 trong sai số 1e-6;
  route đã chọn phải nằm trong map và có xác suất lớn nhất, có thể đồng hạng.
  Map rỗng cho quyết định không có phân phối; lúc đó `confidence` phải null.
- `confidence`: null hoặc [0,1]; giữ meaning theo provider/model version, không tự suy ra chất lượng.
- `provider`: rules/jev/llm. `model_version`, `policy_version`, `catalog_version`, `reason_code`
  bắt buộc để audit. Cache hit giữ provider gốc và đánh dấu `cache_hit=true`.
- `latency_ms`, `input_tokens`: số đo không âm; `fallback_reason` là enum mã lỗi/độ mơ hồ.
- Adapter phải kiểm tra catalog/grant/availability thật. Model chỉ validate quan hệ ID trong DTO;
  một shortlist giả nhưng tự nhất quán vẫn không được thực thi.

Ví dụ:

```python
from agent_core.contracts import RoutingDecision

decision = RoutingDecision(
    route="tool_task",
    candidate_tool_ids=("demo__calculate",),
    selected_tool_ids=("demo__calculate",),
    probabilities={"tool_task": 0.9, "chat": 0.1},
    confidence=0.8,
    provider="jev",
    model_version="jev-1.13.0",
    policy_version="routing-v1",
    catalog_version="catalog-v1",
    latency_ms=100.0,
    input_tokens=120,
    reason_code="tool_match",
)
assert RoutingDecision.model_validate_json(decision.model_dump_json()) == decision
```

## RetrievalResult và EvidenceReference

`RetrievalResult` là **một passage**: citation ID, UUID document/version/chunk, title, original
`text`, score + `score_kind` và retrieval config version. Score không giới hạn [0,1] vì distance,
lexical và rerank có thang khác nhau; phải là số hữu hạn. `trust` luôn `untrusted`.

Ít nhất có page range, section hoặc offset range. Page bắt đầu từ 1, start/end đi cùng nhau,
end không trước start. Offset là khoảng nửa mở `[start_offset, end_offset)` trong text tài liệu
đã parse, offset bắt đầu từ 0; không suy ra offset từ độ dài snippet. Section có thể dùng riêng.
Text giữ nguyên whitespace để citation/quote có thể đối chiếu chính xác.

`result.reference()` lấy `EvidenceReference` chỉ chứa identity, không nội dung passage. Event
dùng reference để tránh đưa raw tài liệu vào status stream. Backend vẫn kiểm tra ownership,
active version và nguồn trước khi dùng evidence hoặc mở citation.

## TaskSpec

| Field | Ý nghĩa |
| --- | --- |
| `task_id`, `goal` | Logical ID có scope root run/plan version và task brief ngắn |
| `agent_type` | document_researcher, web_researcher hoặc data_analyst |
| `depends_on` | Tối đa 8 ID không trùng; không phụ thuộc chính mình |
| `input_refs` | document_version/artifact/task_output, reference ID là opaque reference |
| `evidence_refs` | Reference đến passage có version; citation ID không trùng |
| `allowed_capability_ids` | Giới hạn đề xuất; scheduler lấy giao với grant của parent/worker |
| `output_schema` | JSON Schema hợp lệ có type object; chưa validate output của worker tại Ngày 77 |
| `acceptance_criteria` | 1–16 tiêu chí không rỗng để verifier kiểm tra |
| `timeout_seconds` | Hữu hạn, lớn hơn 0, tối đa 3.600 giây |
| `budget` | TaskBudget: max_tokens/max_steps dương, max_tool_calls không âm |
| `side_effect` | read_only/write/destructive/unknown, mặc định unknown |

`task_output` input cần `source_task_id` thuộc `depends_on`; loại input khác không mang
source task. Opaque reference không phải quyền đọc file và không được giải thành arbitrary path.
Tool budget khác 0 cần capability proposal khác rỗng. Task không dùng tool đặt max_tool_calls=0.
Chưa có root budget reservation, cross-task cycle validation hoặc scheduler ở Ngày 77.

## RunEvent

Tất cả event bắt buộc có `event_id`, `root_run_id` (UUID), `sequence` dương và `occurred_at`
có timezone. Timestamp được chuẩn hóa UTC. Recorder cấp các field này sau kiểm tra ownership.

| `kind` | Payload typed ngoài envelope |
| --- | --- |
| routing_decided | `decision: RoutingDecision` |
| retrieval_completed | evidence_refs không trùng citation ID, retrieval config version, duration_ms |
| plan_created | plan_version dương, 1–8 task ID không trùng; chỉ ở root |
| task_status | task_id, plan_version, status, attempt_id khi đã execution, error_code khi failed/blocked |
| run_status | queued/running/waiting_approval/completed/failed/cancelled; error_code chỉ khi failed |

Status task: pending, ready, running, waiting_approval, succeeded, failed, blocked, cancelled.
Running/waiting_approval/succeeded/failed cần attempt ID; task bị block trước dispatch có thể chưa
có attempt. Event root run/plan không được mang task/attempt ID. Routing/retrieval có thể nằm ở
root hoặc child; child luôn cần plan version, attempt nếu đã chạy attempt.
Validator kiểm tra một event, không kiểm tra ordering/transition xuyên nhiều event.

```python
from datetime import UTC, datetime
from uuid import uuid4
from agent_core.contracts import RUN_EVENT_ADAPTER, RunStatusEvent

event = RunStatusEvent(
    event_id=uuid4(), root_run_id=uuid4(), sequence=1,
    occurred_at=datetime.now(UTC), status="running",
)
assert RUN_EVENT_ADAPTER.validate_json(event.model_dump_json()) == event
event_schema = RUN_EVENT_ADAPTER.json_schema()
```

Chưa thay thế `AgentLoopEvent`/SSE hiện tại. Sequence và replay persistence làm ở M4;
producer không được gửi CoT, secret hoặc generic arbitrary payload vào event.

## FeatureFlags và cấu hình deploy

| Env | Field trong FeatureFlags |
| --- | --- |
| V2_ENABLED | enabled, công tắc tổng |
| V2_PLUGINS_ENABLED | plugins |
| V2_ROUTING_ENABLED | routing |
| V2_RAG_ENABLED | rag |
| V2_PLANNING_ENABLED | planning |
| V2_PARALLEL_ENABLED | parallel |
| V2_REDIS_CACHE_ENABLED | redis_cache |
| V2_EVENTS_ENABLED | events |

Tất cả mặc định false trong Settings và `.env.example`; `.env` chứa secret của người triển khai
không cần sửa. `settings.v2_features.is_enabled("rag")` chỉ true nếu master + RAG đều true.
Planning cần routing; parallel cần planning; dependency được kiểm tra khi load Settings kể cả
master false. Bật flag không làm implementation chưa có tự xuất hiện. MCP demo, sandbox và các
chức năng V1 không bị các flag này tắt. Env thay đổi cần restart process vì Settings có cache.

## Kiểm tra offline

```powershell
.\.venv\Scripts\ruff.exe check agent_core backend migrations scripts tests
.\.venv\Scripts\python.exe -m pytest -q tests/test_v2_contracts.py tests/test_v2_features.py
```

Các test bao gồm JSON round-trip/schema hợp lệ; chặn owner/field/version giả; chặn selection ngoài
candidate, probability lỗi và NaN; giữ nguyên quote/provenance; kiểm tra task dependency/budget;
event union/correlation và feature flag default/dependency/master switch.
