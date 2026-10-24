# Capability catalog và tải schema theo budget

Ngày 84 bổ sung catalog metadata của runtime đang hoạt động, index tìm kiếm và discovery tường minh.
Không thêm dependency hoặc migration; head vẫn `20261018_0024`.

## Catalog nào dùng để làm gì

- `PluginCatalog`/PostgreSQL lưu manifest, release và trạng thái control plane. Plugin có manifest
  chưa có nghĩa là server đang kết nối hoặc user được thực thi.
- `CapabilityCatalog` nhận `CapabilityDescriptor`, binding name và routing hints, tạo snapshot
  metadata cùng index từ/ngữ, kind, plugin và tag. Không chứa input/output schema, secret hoặc handler.
  Có thể index cả tool/skill/retriever/workflow; runtime Ngày 84 mới publish tool.
- `ToolRegistry.capability_catalog(role)` chỉ publish tool đang đăng ký, thuộc allowlist và role.
  View được cache theo revision/role trong process. Canonical plugin binding dùng descriptor của
  manifest host; tool legacy dùng descriptor `runtime1`, side effect unknown/approval bảo thủ.
  Đây là metadata, execution vẫn kiểm tra policy thật trong `ToolExecutor`.

Version `cc1:<registry revision>:<metadata SHA-256>` tách biệt version manifest/control plane.
Register, unregister hoặc replace batch thành công tăng revision và bỏ view cũ. Snapshot đã trả
về giữ nguyên. Mỗi process có catalog riêng, chưa có distributed invalidation hay Redis.
Không sửa trực tiếp đối tượng tool sau đăng ký; thay registration bằng batch mới.

## Discovery và run đang chạy

1. Startup discovery dùng các giới hạn page/tool, release/protocol pin và manifest binding sẵn có.
2. Admin gọi `POST /api/admin/mcp-runtime/{server_name}/discover` khi cần cập nhật metadata/schema.
   Cần đăng nhập admin và cả `V2_ENABLED=true`, `V2_PLUGINS_ENABLED=true`; thao tác thành công có audit.
3. Manager khóa lifecycle; adapter khóa RPC, gọi toàn bộ trang `tools/list` với SDK cache bypass,
   validate toàn bộ rồi thay registrations của đúng owner trong một lần publish. Không mở/đóng
   SDK context từ task gọi API. Discovery cùng nội dung không tạo generation mới.
4. Discovery bị reject/timeout sẽ withdraw riêng server đó và mở circuit; server khác tiếp tục.
   Không auto reconnect, không thực thi lại tool, không tự nhận tool ngoài manifest allowlist.
5. Run mới lấy view/schema mới. Run đang chạy giữ definitions đã chọn; executor pin identity của
   registration, báo `tool_catalog_changed` trước validation/cache/execute nếu registration bị thay.
   Tool đã bị gỡ trả `tool_not_found`. Handler MCP cũ cũng kiểm tra generation trước RPC.
   Call đang thực thi hoàn tất trước refresh vì cùng khóa RPC; không đổi schema âm thầm giữa call.

Không có RPC discovery trên mỗi query/model turn, không tự subscribe `tools/list_changed`.
Refresh là thao tác admin tường minh; thay manifest/profile vẫn cần luồng control/restart đã có.
Resume dựng lại selection từ runtime hiện tại; chưa persist schema/catalog snapshot vào checkpoint.
Ngày 85 đã nối Skill plugin và thu hồi grant/connection trong graph qua runtime riêng;
xem [Skill runtime](PLUGIN_SKILLS.md). Host metadata API ở đây vẫn giữ scope riêng.

## Schema lazy loading và token budget

`ToolSelector` dùng metadata để xếp hạng trước khi gọi `ToolRegistry.model_definition(name)`.
Catalog nhỏ vẫn ưu tiên mọi tool hợp lệ; catalog lớn chỉ chọn ứng viên liên quan, cộng `always_load`.
Index chuẩn hóa dấu tiếng Việt, kể cả đ/Đ, và sắp thứ tự ổn định khi bằng điểm.

Chỉ ứng viên cần xét budget mới được convert schema. Schema JSON được cache theo registration,
copy khi trả cho caller và invalidated khi thay/gỡ registration. Runtime vẫn nhận/validate schema
từ MCP lúc discovery; lazy loading ở đây là chuyển schema sang definitions cho model, không phải
trì hoãn việc nhận schema trên wire hoặc tạo Pydantic adapter.

- `ToolSelectionConfig.schema_token_budget` mặc định 6000; graph lấy giá trị nhỏ hơn với
  `ContextTokenBudget.tool_tokens` thực tế của run (`CONTEXT_TOOL_TOKEN_BUDGET` trong backend).
- Đo toàn bộ danh sách definitions đã sắp thứ tự bằng estimator `utf8_bytes_v1`, cùng estimator
  với context middleware. Không chỉ cộng ước lượng từng schema và bỏ qua list overhead.
- Optional tool không vừa budget bị bỏ, vẫn xét tool tiếp theo. Không cắt description/required/
  enum/$defs/type/constraint để làm schema nhỏ giả tạo. `always_load` không vượt budget.
- Tool bắt buộc của Skill được xét trước, phải vừa trọn nhóm; thiếu quyền/schema budget làm fail
  với context limit trước model call, không thực thi Skill thiếu dependency.
- Model chỉ thấy definitions đã chọn. Không đính kèm cả catalog vào prompt. Execution vẫn chặn
  tool không được load; kết quả memoization gắn registration để không tái dùng kết quả thế hệ cũ.

Estimation không phải provider tokenizer hay số token billing thực. Chưa benchmark chất lượng
routing bằng model; index hiện là lexical, không phải Jev/embedding/semantic search.

## API metadata

```http
GET /api/capabilities?q=tim%20kiem&kind=tool&plugin_id=test.docs&tag=retrieval&limit=20&offset=0
```

Cần user đăng nhập và master/plugins flag. Query tối đa 4096 ký tự, limit 1–100, tối đa 20 tag;
response có `catalog_version`, `scope=host_runtime`, `total`, `limit`, `offset`, `capabilities`.
Mỗi item chỉ có `binding_name` và descriptor; `input_schema_ref`/`output_schema_ref` là opaque
metadata, không có API resolve URL/path tùy ý. Không trả handler, full schema, credential hoặc profile.
Trang tiếp theo phải đọc lại version nếu catalog đổi; đây không phải transaction xuyên nhiều HTTP request.

Scope chỉ gồm shared host runtime đã được host review. Không đưa credential/grant/connection riêng
vào catalog chung, không khẳng định connection DB đã nối graph (`runtime_connected=false`).

## Kiểm tra

```powershell
python -m pytest -q tests/test_capability_catalog.py
python -m scripts.check_capability_catalog
python -m scripts.ci_compose_gate --output-dir reports/ci/compose
```

Probe dựng 200 tool synthetic, đo definitions eager/selected và số schema convert; chạy real stdio
đổi schema, xóa tool, discovery no-op, reject schema và server isolation. HTTP compatibility probe
kiểm tra refresh no-op cả modern/legacy JSON/SSE qua TLS. Compose chạy thêm probe catalog trong app
image cùng migration/auth/credential gates, không dùng provider hay DB host.
