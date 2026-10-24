# ADR 0003 — Plugin persistence, grant và connection theo owner

- Trạng thái: Accepted.
- Ngày: 2026-10-18.
- Phạm vi: Ngày 81; tiếp nối [ADR 0002](0002-plugin-manifests-namespace-and-catalog.md).

## Quyết định

1. PostgreSQL là nguồn dữ liệu chính cho control API. `plugins` giữ manifest đang được chọn;
   `plugin_manifest_releases` và `plugin_policy_releases` giữ fingerprint các release đã đăng ký.
   API không sửa nội dung của một version đã xuất bản, kể cả sau restart hoặc quay về release cũ.
   Catalog trong memory của Ngày 80 tiếp tục là thư viện metadata; không phải nguồn quyền của API.
2. Admin đăng ký/bật/tắt plugin và cấp grant với danh sách capability cụ thể. User chỉ tạo, xem và
   bật/tắt connection của chính mình. Owner lấy từ session đăng nhập, không nhận từ request body.
   Grant phải hợp role hiện tại; Skill cần grant cả tool dependency. Việc kiểm tra role đọc lại user
   từ DB mỗi request, không tin role lưu trong token hay grant cũ.
3. Mỗi `(user, plugin)` có một grant và một connection. Composite foreign key bắt connection thuộc
   đúng grant, credential reference thuộc đúng `(connection, user, plugin)`. Query luôn scope owner;
   ID của user khác trả 404. API không cho user tự cấp grant hoặc tự gắn credential store ID.
4. Credential reference chứa `provider` và UUID opaque của secret store, cùng slot/server/version
   và owner. Không nhận secret value, URI hay path. Chỉ admin gắn reference; user có thể thu hồi
   reference của mình. Response/audit không chứa `provider`, `secret_id` hay giá trị secret.
   Cặp `(provider, secret_id)` không được tái sử dụng trong các binding còn được lưu, kể cả đã revoke.
   Chưa có resolver/encryption/OAuth; UUID không chứng minh secret tồn tại hoặc còn hiệu lực.
5. Mọi mutation và audit commit trong cùng transaction. Row singleton `plugin_catalog_state` khóa
   `FOR UPDATE` cho mutation, `FOR SHARE` cho GET để snapshot và version nhất quán giữa process.
   Catalog dùng `pc1:revision:hash`; grant/connection/reference dùng counter `auth_version` riêng.
   Client gửi expected version; stale trả 409 và phải GET lại. No-op không tăng counter.
6. Thay manifest đang active, kể cả quay lại bản cũ, tắt plugin, grant, connection và revoke mọi
   reference của plugin. Cần cấp lại quyền, gắn reference mới và user bật lại. Thu hồi/đổi scope grant
   hoặc rotate/revoke reference cũng tắt connection; cấp lại grant không tự bật connection.
   Global disable là công tắc tạm thời: giữ lựa chọn connection nhưng chặn `effective_enabled`.
7. `V2_ENABLED` và `V2_PLUGINS_ENABLED` đều phải bật; mặc định false, route trả 404.
   `effective_enabled` chỉ biểu thị cấu hình đủ điều kiện. `runtime_connected` luôn false trong
   Ngày 81; việc đổi DB không tạo MCP transport hay thay registry V1 đang chạy.

## Ownership giữa các lớp

| Lớp | Sở hữu |
| --- | --- |
| `agent_core/plugins` | Manifest, namespace, policy validation, fingerprint và snapshot thuần dữ liệu |
| `backend/models`, migration 0023 | Dữ liệu bền vững, owner FK, uniqueness và index |
| `backend/services/plugins` | Transaction, scope, trạng thái hiệu lực, CAS, lịch sử release và audit |
| `backend/routes/plugins`, schemas | Auth, feature gate, DTO, pagination và HTTP error |
| PluginManager Ngày 82 / credential service Ngày 83 | Kết nối thật, resolve secret, lifecycle, timeout và health |
| Executor / Skill bridge Ngày 85 | Kiểm tra quyền ngay trước thực thi, dependency và xử lý revoke khi đang chạy |

## Hệ quả và kiểm chứng

- Khóa singleton ưu tiên tính đúng ở quy mô hiện tại; ghi metadata bị tuần tự hóa. GET catalog tính
  hash trên toàn catalog, dù response phân trang. Cần đo trước khi chuyển sang version theo owner/plugin;
  chưa đưa Redis hay cache vào đường kiểm tra quyền này.
- Đăng ký file manifest qua CLI không tự import vào DB. API đăng ký mới mặc định disabled;
  migration chỉ seed singleton, không cấp quyền hay gắn credential tự động.
- Soft revoke chỉ ngắt binding của ứng dụng. Không xóa/rotate secret tại provider; yêu cầu đó thuộc
  credential service. Raw SQL với quyền DB cao vẫn có thể sửa lịch sử; đây không phải kho chống sửa đổi.
- Gate PostgreSQL kiểm tra owner FK, IDOR, CAS đồng thời, revoke, role demotion và rollback release.
  Migration được chạy `22 → 23 → 22 → 23` trên DB tạm, giữ user cũ và kiểm tra drift.
- Composite FK theo [PostgreSQL constraints](https://www.postgresql.org/docs/17/ddl-constraints.html);
  row lock qua [SQLAlchemy with_for_update](https://docs.sqlalchemy.org/en/20/core/selectable.html#sqlalchemy.sql.expression.Select.with_for_update).
