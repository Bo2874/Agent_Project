# ADR 0006: Capability metadata và schema budget theo run

- Status: accepted, 2026-10-18 (Ngày 84).
- Phạm vi: runtime host catalog/discovery, schema definitions cho graph và Skill middleware.

## Bối cảnh

Plugin catalog manifest và registry executable đã tồn tại. Selector cũ duyệt metadata mỗi request,
giới hạn số tool nhưng chưa giới hạn kích thước schema; Skill có thể thêm required tools vượt budget.
Discovery chỉ xảy ra khi connect. Refresh schema mà không pin registration có thể làm model gửi args
theo schema cũ vào tool mới; cache idempotency cũng có thể trả kết quả từ generation trước.

## Quyết định và ownership

`agent_core.capability_catalog` sở hữu immutable descriptor records, lexical/filter index và version
metadata; không kết nối server, resolve credential, đọc DB, convert schema hay cấp quyền.
`ToolRegistry` sở hữu catalog role/allowlist-scoped và cache serialized schema theo identity registration.
Batch replace kiểm tra owner, duplicate alias/capability và giới hạn 4096 tool trước publish.
Descriptor manifest do host cung cấp; annotations remote không nới execution policy.

`ToolSelector` rank metadata rồi lazy convert nguyên schema và fit cả danh sách definitions theo
estimator context hiện có. Required Skill schemas là nhóm bắt buộc, optional schemas có thể bỏ;
không sửa schema để ép vừa budget. Graph truyền budget thực và giữ definitions ổn định trong run.
Dynamic Pydantic MCP schema phải dùng `args_schema.model_json_schema()` gốc: subset model của
`StructuredTool.tool_call_schema` có thể làm mất constraints khi field được tạo kiểu Any.

`MCPClientAdapter` sở hữu bounded cache-bypass discovery và generation handlers; `PluginManager`
serialize lifecycle, withdraw server có discovery không hợp lệ, giữ fault isolation. Context SDK
vẫn do task owner đóng. Refresh no-op giữ identity/version; không có per-query discovery RPC.
Backend sở hữu auth/feature gate/audit cho refresh API và auth gate cho metadata API.

`ToolExecutor` pin selected registration khi graph bắt đầu; kiểm tra pin trước schema validation và
memoization, kiểm tra lại trước mỗi attempt. Cached result giữ registration owner. Call đã bắt đầu
không bị thay schema giữa chừng; call tiếp theo báo catalog mismatch, cần run mới.

## Hệ quả và giới hạn

- Tái sử dụng contract `CapabilityDescriptor`, không thêm migration/dependency, không đổi default flags.
- Metadata search không trả full schema; không đưa whole catalog vào prompt. Chuẩn hóa chỉ phục vụ
  lexical lookup, không thay query gốc gửi model.
- Budget là ước lượng UTF-8; provider usage vẫn là nguồn billing. Chưa có quality/SLO benchmark.
- Catalog/cache là per-process; không đồng bộ pub/sub/Redis, không persist run snapshot khi resume.
- Refresh do admin yêu cầu, không tự nhận notifications hay hot-reload manifest/profile.
- Catalog generic hỗ trợ bốn kind, runtime hiện chỉ tool. Bridge Skill/plugin private runtime,
  distributed refresh/version và grant revoke trong graph tiếp tục ở Ngày 85.

Kiểm chứng: [catalog guide](../plugins/CAPABILITY_CATALOG.md),
[báo cáo Ngày 84](../../reports/checks/2026-10-18-day84/VALIDATION.md).
