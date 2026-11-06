# Plan03 — gộp local vào main, 2026-11-06

Người dùng yêu cầu gộp vào `main` rồi cung cấp lệnh push, không yêu cầu tự push.
Đã fetch `origin`: main local và origin/main cùng ở `11b9632`. Fast-forward tám commit
từ `feat/workspace-google` vào main, source commit `b1c7aa2`; không có conflict hoặc thay đổi code
so với bản đã review/kiểm thử. Commit ghi biên bản này chỉ cập nhật tài liệu.

## Hậu kiểm

- Đồng bộ dependency theo lock bằng `uv sync --locked --extra dev --inexact`; giữ các gói có sẵn,
  bổ sung Google OAuth SDK và các dependency cần thiết. Không sửa `.env` hay DB đang dùng.
- Chạy toàn suite bằng `python -m pytest -q -ra` trên main, Google fixtures dùng PostgreSQL
  QA riêng: **1.208 passed, 59 skipped**, 142,38 giây. Google/model/search là fixtures.
- 59 skips thuộc các gate cần môi trường Docker/Artifacts và điều kiện host. Lượt hậu kiểm này
  không bật lại các gate đó. Bản source giống hệt đã đạt **1.266 passed, 1 skipped** với Docker,
  broker và benchmark Excel trước gộp; xem [hồ sơ Plan03](2026-11-06-google-connectors.md).
- Ruff toàn repo và cú pháp JavaScript Connectors đạt.

## Lượt kiểm tra đầu và giới hạn

Lượt đầu đặt `--basetemp` trong thư mục QA riêng: 1.207 passed, 59 skipped, 1 failed (421,15 giây).
Test lỗi là `test_long_scratch_path_supports_binary_unpack_and_reconciliation`: reaper trả 0
thay vì 1 ngay sau hard-kill. Một lần chạy riêng với basetemp tùy chỉnh cũng tái hiện.

Kiểm tra sau đó cho thấy thư mục đúng owner/scope, nội dung nhận diện được, lease đã khóa được,
và dry-run đủ điều kiện thu hồi. Probe có chẩn đoán sâu cũng đạt. Chưa xác định chắc chắn nguyên nhân
của khác biệt thời điểm/đường dẫn; không sửa production code hoặc làm yếu assertion để bỏ qua lỗi.
Cả bảy test quản lý scratch đạt với thư mục tạm mặc định (2,21 giây), rồi toàn suite với cấu hình
mặc định đạt như trên. Lưu lại độ nhạy này để điều tra nếu tái diễn.

## Trạng thái bàn giao

Google thật vẫn **NOT RUN** theo lựa chọn trước đó; feature chưa được bật, chưa migrate DB đang dùng,
chưa chuyển Plan04. Chưa push GitHub. Lệnh người dùng chạy từ repo:

```powershell
git push origin main
```
