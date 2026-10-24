# ADR 0005 — HTTP MCP, outbound policy và credential theo owner

- Trạng thái: Accepted.
- Ngày: 2026-10-18.
- Phạm vi: Ngày 83; tiếp nối [ADR 0004](0004-stdio-plugin-manager.md).

## Quyết định

1. Tái sử dụng SDK Streamable HTTP và adapter/manager hiện có; HTTP config là profile host review,
   tách khỏi manifest metadata. Default file rỗng, feature flag giữ tắt. Chỉ HTTP public không có
   credential được publish vào host registry; private config chỉ vào registry riêng theo owner.
2. Outbound transport kiểm tra exact URL, DNS mọi answer và dial numeric IP đã duyệt; pool vẫn giữ
   hostname gốc cho TLS/Host. Không proxy/env, redirect, HTTP/2 hoặc network retry. Mọi TCP connection
   mới phải qua guard; fixture loopback/CA chỉ được inject bằng code test, không có knob bypass trong JSON.
3. Local authorization/network denial trở thành HTTP lỗi nội bộ trước khi có outbound I/O. Không
   để lỗi control-plane thoát vào SDK task group và làm hỏng cleanup AnyIO. Cancellation vẫn được truyền.
4. Lưu credential và pending OAuth trong PostgreSQL, Fernet key ring riêng ở deployment config.
   Envelope ràng buộc identity/owner/profile; không lưu raw token hoặc PKCE verifier. Bảng/reference
   gắn composite owner FK. Không nhận key mã hóa từ user hoặc fallback sang auth JWT key.
5. OAuth public client preregistered, authorization code/PKCE S256/state 10 phút, literal issuer check,
   resource parameter và scope/expiry validation. Profile/grant generation pin theo consent; flow mới
   hoặc rotate credential làm flow cũ hết hiệu lực. Consume state trước exchange, recheck quyền sau I/O.
6. Row lock serialize refresh, rotate refresh token và commit revoke local khi invalid grant.
   Revoke local không phụ thuộc provider/key sẵn sàng; remote revocation là best effort sau commit.
   Không tự replay business tool call hoặc đưa URL discovery từ challenge vào outbound allowlist.
7. Private config pin owner/role/grant/reference và chỉ bind granted tools. Credential resolve lại
   trên mỗi HTTP request. Graph/UI bridge chưa bật; `runtime_connected=false` của API DB giữ đúng nghĩa.
   Private factory tắt executor V1 result memoization/retry để cached result không vượt kiểm tra revoke;
   cần cache key gắn auth/credential generation trước khi bật lại các tối ưu này.
8. Pin trực tiếp `httpx2/httpcore2` theo SDK lock và khai báo trực tiếp cryptography; đây là dependency
   đã có trong transitive closure, không nâng phiên bản distribution. Migration 0024 thêm hai bảng.

## Hệ quả

- Hỗ trợ PAT/API key và OAuth public client đã review; không tuyên bố full automatic MCP OAuth
  discovery, dynamic registration, confidential client hoặc provider live compatibility.
- Metadata health không chứng minh token/provider còn hợp lệ. Chưa tự đóng toàn bộ graph/session
  đang chạy theo event revoke; từng request mới kiểm tra quyền, bridge orchestration thuộc Ngày 84–85.
- Refresh hiện dùng khóa catalog chung của control plane trong lúc network request bounded; ưu tiên
  tính đúng, có thể tăng contention. Chỉ tách lock/cache sau khi đo và giữ version invalidation đúng.
- OAuth state expired cũ của owner được dọn khi bắt đầu flow mới sau một ngày; chưa có maintenance
  job riêng cho toàn bộ secret/state retention. Key ring chưa có bulk rewrap CLI; cần giữ khóa cũ.
- TLS verification và application guard cần phối hợp egress policy khi triển khai production.

## Kiểm chứng

TLS fixture thật chạy JSON/SSE, auto modern và fallback legacy, SNI và redirect rejection. PostgreSQL
probe dùng auth cookie/owner thật, encrypted storage, one-time state, stale consent, single refresh,
invalid grant và local/provider revoke. Migration 22→24→23→24→22→24 giữ dữ liệu nền tương ứng,
Alembic check không drift. Không dùng token/account thật; không gửi message ra dịch vụ bên ngoài.

Chi tiết: [HTTP/OAuth guide](../plugins/HTTP_AND_OAUTH.md) và
[nghiệm thu Ngày 83](../../reports/checks/2026-10-18-day83/VALIDATION.md).
