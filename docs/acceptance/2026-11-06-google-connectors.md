# Plan03 — Google Connectors, 2026-11-06

Triển khai trên `feat/workspace-google`, base `11b9632`, trong worktree riêng. Main, `.env`,
dịch vụ/database đang dùng và remote không thay đổi. Người dùng chọn triển khai/kiểm thử offline
trước vì chưa có OAuth Web Client hay file Google thử. **Live Google NOT RUN**; không coi fixture
là nghiệm thu live, chưa mở Plan04.

## Đã kiểm tra

- OAuth state/owner/TTL/replay, refresh đồng thời, invalid_grant/reconnect, độc lập keyring plugin.
- Picker/global grants/per-chat selection, ID/type/permission, revoke và account generations.
- Sheets RAW và công thức chủ đích, formatting, phân trang/range bounds, dedup cột chủ đích.
- Proposal bất biến/approval host, snapshot đổi, concurrent same-key, receipt replay,
  mất response và lỗi commit receipt; sent/unknown chặn cả mã gọi mới.
- Ba luồng qua queue/worker/graph/PostgreSQL checkpoints thật với fixture model: đọc/phân tích,
  search tool thật được gọi với nguồn fixture rồi append, đọc rồi update qua durable approval.
- Docs UTF-16/tab/revision, nhiều sửa không chồng lấn, bảo toàn bảng/phần không sửa, tạo Doc trắng,
  dùng tiếp file mới cùng run, không tạo lại khi mất response.
- Upload immutable XLSX/DOCX ở hai mode, byte hash/owner/work scope, receipt/unknown,
  Location giả không chuyển credentials, che upload session ID trong log.
- Catalog Google/Artifact và routing thực tế giữ prepare/search tools; Google schema allowance
  hữu hạn 16.000 tokens và tối thiểu 12 ứng viên khi bật flag, vẫn chịu trần context chung.
- `scripts.check_google_connector --offline`: **50 passed**, 74,01 giây. Không gọi Google thật,
  không tìm web thật, không gọi model trả phí. Tách schema DB theo test và dọn đúng schema đó.
- Ruff, syntax JS, `uv lock --check --offline`, Compose config và `alembic check` đạt;
  migration 0033 đã áp dụng riêng trên DB QA, không còn schema diff.

Hồi quy toàn project với broker loopback/Docker Artifacts: **1.245 passed, 1 skipped**,
1.006,81 giây. Skip duy nhất là quyền tạo symlink trên Windows; benchmark Excel và browser
Word/PDF đã chạy. Lượt đầu phát hiện ba lỗi ở cấu hình harness, đã sửa phạm vi format Word/PDF
và giữ MCP defaults rồi chạy lại toàn suite; không sửa production để làm test xanh.
Đánh giá độc lập toàn nhánh đang được thực hiện trước bàn giao.

## UI offline

Ảnh dùng template, CSS và Inter/Lora đi kèm project, account/file giả; không phải ảnh Google live.
Đã xem cả hai cỡ, không cắt tiêu đề hoặc tràn ngang; sidebar mobile giữ đóng mặc định.

![Connectors desktop](2026-11-06-google-connectors-desktop.png)

![Connectors mobile](2026-11-06-google-connectors-mobile.png)

## Giới hạn nghiệm thu

Live OAuth/Picker, refresh token thật, dữ liệu Sheets/Docs, fidelity chuyển đổi và deployment callback
chưa được chứng minh. CLI `--live` chỉ in kế hoạch trên file đích đã chọn, không vượt chat approvals.
Giữ định dạng upload là gửi đúng bytes trong fixture; khả năng mở/chuyển đổi của Google phải kiểm riêng.
Sheets vẫn có race sau snapshot; Docs dùng requiredRevisionId. Unknown cần đối soát tin cậy,
chưa có UI/API để Agent tự gỡ. Docs chưa sửa ảnh/bảng/field; upload chưa chọn folder hay chia sẻ/xóa cloud.

Hướng dẫn đầy đủ: [manual](../manual-google-connectors.md).

## Review và quyết định

Review độc lập toàn nhánh: đang chờ gate cuối. Rulings và deferred minors sẽ được ghi ở đây
trước khi dọn scratch của plan.
