# UI Plugins / Connections

Ngày 86 chỉ triển khai UI theo phạm vi đã chốt. Hai connector GitHub đọc + web search và Skill mẫu
research/report được hoãn để người dùng tự setup/test sau. Không thêm tài khoản, profile, token hoặc
plugin live vào cấu hình mặc định.

## Mở giao diện

- `/plugins`: tài khoản đã đăng nhập; plugin được cấp và connections của chính mình.
- `/admin/plugins`: chỉ admin; catalog, manifest và grant theo tài khoản.
- Link Plugins nằm ở dashboard, chat, bộ nhớ và trang quản trị.
- Khi `V2_ENABLED` hoặc `V2_PLUGINS_ENABLED` tắt, trang giải thích tính năng đang tắt, không tải API
  plugin hay cho thao tác. Giá trị mặc định vẫn false. Compose dùng các biến `DEV_V2_*` tương ứng.

Catalog rỗng hoặc tài khoản chưa có grant sẽ hiện hướng dẫn, không chèn plugin demo tự động.
Nút Tải lại cập nhật toàn bộ snapshot; tìm kiếm lọc theo tên/id/mô tả trong danh sách đã tải.
UI đọc từng trang 100 mục, giới hạn mỗi danh sách dưới 5.000 mục; vượt giới hạn hoặc snapshot đổi
giữa các trang thì ngừng cho thao tác và yêu cầu tải lại. Chưa có UI phân trang cho catalog lớn hơn.

## Quản trị

1. Đăng ký manifest JSON đã review. Manifest chỉ là metadata; command, endpoint, OAuth client và
   package instructions vẫn do host cấu hình theo các guide stdio/HTTP/Skill.
2. Bật plugin toàn hệ thống. Cấp quyền bằng UUID tài khoản và checkbox capability cụ thể.
   UUID lấy từ dữ liệu tài khoản do admin quản lý; trang này không liệt kê email của mọi user.
3. Chọn Skill tự chọn tool bắt buộc; bỏ tool sẽ bỏ Skill phụ thuộc. API vẫn validate role/dependency,
   không tin checkbox hay `data-admin` từ trình duyệt.
4. Chọn grant hiện có để sửa hoặc bỏ “Cho phép sử dụng plugin” để thu hồi. Thay đổi grant tắt
   connection của user đó; user cần kiểm tra và bật lại.
5. Cập nhật manifest cần release/policy version đúng quy tắc immutable. Release đổi sẽ tắt plugin,
   grant/connection và revoke credential cũ; giao diện hiển thị tác động trước khi lưu.

Tắt plugin toàn hệ thống có bước xác nhận. Không có UI sửa command/cwd/endpoint host, tự cài package,
provision Vault reference hoặc thay role tài khoản.

## User và thông tin xác thực

User tạo connection riêng, mặc định tắt; bật/tắt có xác nhận. Trạng thái “Đã cấu hình · Đang bật”
là trạng thái quyền/cấu hình, không phải kiểm tra dịch vụ bên ngoài online. Server chỉ kết nối theo
runtime Ngày 85 trong run; UI không mở phiên MCP để health check.

API user thêm `credential_requirements`: `server_id`, `slot`, `kind`, `transport` từ manifest;
không trả endpoint, profile ref, command, secret/store ID hay toàn manifest admin. Trong hộp xác thực:

- HTTP API key/bearer token: nhập qua password field, gửi POST tới API owner. UI xóa ô ngay khi gửi,
  xóa nội dung dialog khi đóng, không dùng local/session storage và không ghi giá trị vào log.
- OAuth: dùng authorization URL do host-reviewed profile sinh. Phải có encryption key ring,
  profile/client/redirect URI đã review; UI không nhận endpoint tùy ý từ user.
- Reference hiện có chỉ hiển thị slot/server/version. User có thể thu hồi qua API credential hiện có.
- Slot stdio yêu cầu quản trị viên cấu hình tại host. Không có resolver Vault mới.

Credential mới hoặc revoke tắt connection. Sau khi hoàn tất, user chủ động bật lại.

## OAuth callback của UI

Đăng ký redirect URI chính xác `<app-origin>/plugins/oauth/callback` với provider và reviewed profile.
Cookie đăng nhập phải còn hiệu lực. Route trả HTML `Cache-Control: no-store`, `Referrer-Policy:
no-referrer`, không phản chiếu code/state vào HTML. JS xóa query khỏi URL bằng history replace trước
khi gửi `POST /api/plugins/oauth/complete` với body `state`, `code`, `issuer`.

Endpoint mới gọi cùng `complete_state` đã kiểm tra owner/TTL/single-use/PKCE/issuer/consent;
không lấy owner từ body. Callback cũ `GET /api/plugins/oauth/callback` và endpoint complete theo
connection vẫn tương thích. Callback lỗi/hủy/phiên hết hạn cần bắt đầu lại; không tự retry exchange.
UI không thể xóa lịch sử/log đã được provider, proxy hoặc trình duyệt lưu trước khi HTML được tải;
giữ redaction query và cấu hình log đúng ở reverse proxy khi deploy.

## Lỗi và kiểm tra

401 chuyển về đăng nhập; 403/404/409/422/503 có thông báo. Mutation dùng version snapshot và khóa
nút khi đang gửi. Khi mutation lỗi hoặc không biết kết quả do mạng, UI không tự gửi lại: tải lại
trạng thái rồi user quyết định thao tác tiếp. Không ghép HTML từ tên/mô tả manifest; dùng text nodes.

```powershell
python -m pytest -q tests/test_plugins_ui.py
python -m scripts.check_plugins_ui --output-dir reports/ci/plugins-ui
python -m scripts.ci_compose_gate --output-dir reports/ci/compose
```

UI acceptance dùng Chrome/Edge Chromium đã cài, profile tạm, server chỉ bind loopback, template/asset
thật và API replies giả. Mọi request provider/font bên ngoài bị chặn. Nó không xác minh connector live.
Script này độc lập với app production, không mount production API và không dùng DB host.
Compose bổ sung kiểm tra trang bằng cookie thật, phân quyền admin và browser OAuth completion trên
PostgreSQL + TLS fixture. Model/provider thật: 0 call.
