# Plugin API — Ngày 81

API quản lý đăng ký plugin, grant, connection theo user và credential reference trong PostgreSQL.
Manifest/policy theo [plugin guide](README.md); quyết định ownership ở [ADR 0003](../adr/0003-plugin-persistence-and-user-ownership.md).
Ngày 82 có [runtime stdio theo cấu hình host](STDIO_RUNTIME.md); các connection DB theo user vẫn
chưa được nối vào registry dùng chung và tiếp tục báo `runtime_connected=false`.
Ngày 83 bổ sung [API lưu credential mã hóa và OAuth](HTTP_AND_OAUTH.md), cùng factory runtime riêng theo owner.

## Bật trên môi trường dev

Backup trước khi migration DB có dữ liệu. Compose dùng migration one-shot; head hiện tại `20261018_0024`
(catalog/grant ở 0023, encrypted credential/OAuth ở 0024).
Các bảng mới độc lập với runtime V1. Migration không đăng ký plugin từ file và không cấp quyền user.

Trong shell PowerShell dành cho Compose, bật cả hai flag rồi tạo lại app/worker:

```powershell
$env:DEV_V2_ENABLED = 'true'
$env:DEV_V2_PLUGINS_ENABLED = 'true'
docker compose --env-file .env.docker.example up -d --build --wait
```

Mặc định cả hai false; API trả 404 khi gate đóng. Auth dùng cookie `access_token` của luồng
`/api/auth/login` hiện có. Role admin phải do cơ chế quản trị hiện có cấp; API không có self-promotion.
Không sửa `.env` chứa thông tin cá nhân để chạy các gate tự động.

## Endpoint

Mọi response có `catalog_version` và `auth_version`. Danh sách dùng `limit=1..100` (mặc định 50),
`offset=0..100000`. Các mutation yêu cầu expected version kể cả khi muốn lặp lại một thao tác.

| Quyền | Method / path | Tác dụng / body chính |
| --- | --- | --- |
| Admin | GET `/api/admin/plugins` | Danh sách manifest, global enabled và version context |
| Admin | PUT `/api/admin/plugins/{plugin_id}` | `manifest`, `expected_catalog_version`; ID khớp URL |
| Admin | PATCH `/api/admin/plugins/{plugin_id}/enabled` | `enabled`, `expected_catalog_version` |
| Admin | GET `/api/admin/plugins/{plugin_id}/grants` | Các grant của plugin |
| Admin | PUT `/api/admin/plugins/{plugin_id}/grants/{user_id}` | `enabled`, `capability_ids`, `expected_auth_version` |
| Admin | POST `/api/admin/plugin-connections/{id}/credential-references` | `server_id`, `slot`, `provider`, `secret_id`, `expected_auth_version` |
| User | GET `/api/plugins` | Plugin được grant cho mình; scope lọc theo role/version hiện tại |
| User | GET `/api/plugin-connections` | Connection của mình |
| User | GET `/api/plugin-connections/{id}` | Connection của mình và trạng thái hiệu lực |
| User | POST `/api/plugin-connections` | `plugin_id`, `expected_auth_version`; tạo disabled, 201 |
| User | PATCH `/api/plugin-connections/{id}/enabled` | `enabled`, `expected_auth_version` |
| User | POST `/api/plugin-connections/{id}/credential-references/{ref_id}/revoke` | `expected_auth_version`; soft revoke binding của mình |

Admin cũng có các quyền user nhưng user endpoints vẫn chỉ thấy connection của chính actor.
Anonymous trả 401, user gọi admin API trả 403, connection/reference của người khác trả 404.
Body có field lạ, bool dạng chuỗi, auth counter âm hoặc grant scope không hợp lệ trả 422.
CAS stale, namespace collision, sửa release bất biến hoặc enable thiếu điều kiện trả 409.
Cần migrate lên head trước khi mở gate. Nếu bảng state đã có nhưng thiếu singleton, API trả 503;
lỗi SQL không được trả nguyên văn cho client.

## Luồng sử dụng

1. Admin GET context, PUT manifest đã review, ví dụ nội dung `plugins/manifests/demo.json`.
   Plugin mới disabled. PUT cùng nội dung là no-op; đổi nội dung cần manifest version mới;
   đổi policy/binding/pin cần policy version mới.
2. Admin GET context mới rồi PATCH global enabled. Với demo, cấp grant cho user bằng body:

   ```json
   {"enabled": true, "capability_ids": ["demo.tool.main.calculate"], "expected_auth_version": 2}
   ```

   `2` chỉ là ví dụ: lấy counter thực tế từ GET gần nhất. Grant Skill phải chứa cả required tool.
   Grant mặc định rỗng/disabled, không có wildcard hay tự kế thừa mọi tool của plugin.
3. User GET `/api/plugins`, POST connection với plugin ID và auth counter vừa nhận.
   Server tự gán owner từ session, không nhận `user_id` trong body. Mỗi user có tối đa một connection/plugin.
4. Nếu manifest có credential slot, admin gắn UUID opaque từ provider `encrypted_store|vault`.
   Ngày 81 chỉ lưu binding metadata; không upload key/token, không kiểm tra provider thật.
   Response chỉ có ID của reference trong ứng dụng, không trả UUID secret store. Demo hiện tại không có slot.
5. User PATCH connection enabled sau khi global plugin, grant, role, version và reference đều hợp lệ.
   Response `status=configured`, `effective_enabled=true`, **`runtime_connected=false`**.
   Chưa có MCP transport mới; triển khai nhiều stdio server tiếp ở Ngày 82.
6. Khi nhận 409, GET lại context và trạng thái trước khi quyết định gửi lại; không retry mù.
   Global auth counter thay đổi khi user khác sửa quyền/connection cũng có thể gây 409.

## Trạng thái và thu hồi

Điều kiện kiểm tra theo thứ tự: `plugin_disabled`, `grant_revoked`, `version_mismatch`,
`credentials_missing`, `connection_disabled`; đủ điều kiện mới `configured`.
`enabled` là lựa chọn của connection, `effective_enabled` còn phụ thuộc toàn bộ các lớp trên.
API đọc lại role DB: grant admin cũ không đủ quyền sau khi hạ role.

- Global disable chặn hiệu lực ngay ở API; re-enable có thể phục hồi connection đã được user bật.
- Đổi/revoke grant tắt connection; regrant cần user bật lại.
- Rotate/revoke reference tắt connection. Revoke idempotent; binding UUID cũ không được tái sử dụng.
- Đổi manifest active, kể cả downgrade về release cũ: tắt global plugin, mọi grant/connection,
  revoke mọi reference. Cần regrant, reference mới và consent bật lại để tránh khôi phục quyền cũ.
- Đây là control metadata; ngắt phiên MCP đang chạy và kiểm tra quyền trước tool call nối ở Ngày 82–85.

## Kiểm thử tái lập

Ngày 86 có [UI theo user/admin](PLUGINS_UI.md), route `/plugins` và `/admin/plugins`.
`GET /api/plugins` thêm `credential_requirements` chỉ gồm server/slot/kind/transport để render form;
không tiết lộ host profile/secret. Browser OAuth callback dùng `POST /api/plugins/oauth/complete`,
auth và owner-bound state validation giữ nguyên. Không thêm API cấp quyền hoặc transport mới.

Ngày 85 nối chat/graph với run-local plugin runtime khi master/plugins bật. `runtime_connected=false`
trong connection response vẫn mô tả control metadata; nó không báo phiên MCP tạm đang sống trong run.
Skill/private connection không publish vào host metadata endpoint; xem [Skill runtime](PLUGIN_SKILLS.md).

Ngày 84 thêm `GET /api/capabilities` (user, metadata scope host runtime) và
`POST /api/admin/mcp-runtime/{server_name}/discover` (admin, bounded refresh + audit).
Cả hai cần master/plugins flag; không publish private connection/grant vào registry chung.
Chi tiết query, response, version và run đang chạy: [Capability catalog](CAPABILITY_CATALOG.md).

```powershell
python -m pytest -q tests/test_plugin_api.py tests/test_plugin_manifest.py tests/test_plugin_catalog.py
python -m scripts.ci_compose_gate --output-dir reports/ci/compose
```

Gate dùng project/volume mới, PostgreSQL thật, auth cookie thật và user synthetic. Plugin API probe
chỉ bật V2/plugins trong subprocess riêng, không đổi flag app/worker chính. Migration round-trip tạo
và xóa DB tạm `plugin_migration_<uuid>`; cần quyền CREATEDB trong PostgreSQL dev của gate.
Probe không resolve secret hay gọi model/provider. Kết quả nghiệm thu lưu tại
[Ngày 81](../../reports/checks/2026-10-18-day81/VALIDATION.md).
