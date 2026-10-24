# ADR 0004 — PluginManager cho nhiều stdio server

- Trạng thái: Accepted.
- Ngày: 2026-10-18.
- Phạm vi: Ngày 82, tiếp nối [ADR 0003](0003-plugin-persistence-and-user-ownership.md).

## Quyết định

1. Tách executable/args/cwd khỏi `backend/dependencies_tools.py` thành file profile host được review.
   Manifest chỉ trỏ logical profile; API/chat không được cấu hình lệnh. Loader validate toàn config
   trước startup, chỉ nhận credential-free stdio. Profile release và server version phải khớp pin.
2. Một task sở hữu toàn bộ lifetime SDK của từng server. Các task startup độc lập, health riêng,
   timeout riêng và shutdown trong chính task đã enter SDK; không dùng một exit stack chung cho
   nhiều task/server. Tránh lỗi AnyIO cancel scope và lỗi một server kéo sập các server khác.
3. Registry publish batch theo server sau discovery; rollback lỗi trước publish. Cleanup so sánh
   identity registration để không xóa tool do connection khác vừa đăng ký. Circuit open detach
   handler và gỡ batch ngay; restart tạo adapter/generation mới.
4. Canonical tools dùng alias namespace/server/local name và host execution policy của manifest.
   Remote annotation không hạ role/approval/retry. Chỉ bundled demo dùng compatibility alias và
   policy annotation V1 để giữ hành vi hiện có. Không tự đăng ký cả hai bộ alias.
5. Server có khóa call riêng; plugin có semaphore chung. Discovery giới hạn page/tool và reject
   cursor/name trùng. Health thực hiện metadata RPC không dùng cache, bỏ qua khi server bận.
   Sau ngưỡng lỗi mở circuit; restart tường minh qua cooldown, không tự replay tool call.
6. Admin xem health qua `/api/admin/mcp-runtime`; không trả command/args/credential hoặc exception
   thô. Server lỗi là degraded ở lớp MCP; readiness nền tảng của app vẫn kiểm tra DB như trước.
7. Registry host không tự nối connection/grant/secret theo user trong DB. Entry canonical cần cả
   V2/plugins flag và allowlist. Credential/HTTP và bridge theo owner sẽ nối sau; API connection vẫn
   `runtime_connected=false` để tránh coi cấu hình đã lưu là quyền thực thi dùng chung.

## Hệ quả

- Đổi file profile cần restart. Không có HTTP API launch/restart executable trong Ngày 82.
- Stdio là process được host tin cậy, không phải sandbox. Cấu hình executable phải qua operator review;
  version server tự công bố không thay thế xác minh artifact trước deploy.
- Startup deadline có thể cộng cleanup bounded của SDK. Shutdown chờ budget profile, sau cancellation
  cho SDK thêm tối đa 8 giây để terminate process; lỗi cleanup được báo, vẫn đóng các server khác.
- `tools/list` đo khả năng đáp ứng metadata, không đảm bảo từng tool nghiệp vụ thành công.
- Không có DB migration hoặc dependency mới. Demo V1, graph, SSE, approval và allowlist được giữ.

## Bằng chứng

Test Windows/Linux bao gồm stdio process thật, startup hang/exit, request timeout, crash giữa call,
restart, stale handler, process/task cleanup, registry atomicity, role gate và plugin concurrency.
Compose chạy cùng probe trong runtime image, cùng regression graph/MCP/checkpoint/queue/PostgreSQL.
Chi tiết: [runtime guide](../plugins/STDIO_RUNTIME.md) và
[nghiệm thu Ngày 82](../../reports/checks/2026-10-18-day82/VALIDATION.md).
