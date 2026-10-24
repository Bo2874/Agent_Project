# ADR 0008: Query routing trước graph và giữ policy khi resume

- Status: accepted, 2026-10-19 (Ngày 90).

## Bối cảnh

Taxonomy có 5 route nhưng runtime mới có chat/tool execution. Jev chưa được cấu hình; nối provider
vào production lúc này sẽ bỏ qua phần metadata/lifecycle đã được người dùng yêu cầu hoãn.
Một lời nhắc “không gọi tool” cũng chưa đủ ngăn model phát tool call hoặc Skill nạp dependency.

## Quyết định

Core rules chỉ đọc messages và metadata catalog đã lọc quyền, trả contract `RoutingDecision`.
Backend sở hữu flags/runtime/quota/persistence/SSE; graph runner quyết định policy trước middleware
Skill/tool và graph. Rules không import DB/SDK, cấp quyền hoặc thực thi tool. Quyết định rules có
version/latency thực và 0 inference tokens; không dùng dữ liệu đó thay cho telemetry Jev đang hoãn.

Chat nạp 0 tool/Skill; executor tiếp tục enforce loaded names. Tool task dùng selection, schema
budget và authorization/approval cũ. Clarification kết thúc tại backend persistence với 0 model call.
RAG/planner chưa có handler không được xem là available chỉ vì flag bật.

Pin route/rules/policy/catalog vào checkpoint cho run đi graph. Resume dùng pin thay vì phân loại lại;
legacy checkpoint giữ V1. Catalog/policy đổi hoặc checkpoint không hợp lệ thì dừng trước approval/
model/tool, yêu cầu run mới. Tắt flag chỉ thay policy của run mới, không nới giới hạn run đang resume.

## Hệ quả

- Default off giữ V1; có thể thử/rollback rules riêng mà không cần key Jev.
- Candidate IDs là hint, chưa thay selector bằng Jev hay coi router là authorization boundary.
- Metadata-only router giữ lazy schema loading; quyền và approval vẫn do các lớp hiện có thực thi.
- Rules là baseline có giới hạn ngôn ngữ, chưa chứng minh mục tiêu quality/cost M2.
- Pin catalog chặt có thể từ chối resume khi thay đổi capability không liên quan; ưu tiên run mới.
- SSE routing/checkpoint phục vụ kiểm tra hiện tại; admin trace, metric, shadow còn ở Ngày 94.

Chi tiết: [Query router](../routing/QUERY_ROUTER.md).
