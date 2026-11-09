# Nghiệm thu Google qua Python — 2026-11-09

Nhánh `feat/google-python-bridge`, worktree `.worktrees/google-python`, base `dd1f3c1`.
Không sửa `.env` thật, không dùng Google/model trả phí hoặc database ứng dụng; chưa merge/push.

## Phạm vi đã triển khai

- Một tool `run_python_code`, cùng image production `personal-agent-python-runtime:1`.
- Google input là bản chụp native JSON hoặc bytes download/export; lưu trước khi chạy Python.
- Tạo mới bằng XLSX/DOCX rồi upload/chuyển native; sửa native qua batchUpdate giữ file ID.
- Chỉ đọc/ghi file được chọn hoặc file do cùng run tạo; OAuth/token ở host, sandbox không mạng.
- Không duyệt từng thay đổi; Automation dùng revision grants, có `edit_sheet` riêng cho quyền toàn file.
- JSON lệnh sửa không publish, giữ primary local/streaming; receipt Google có URL và operation ID.
- Conflict chặn Google cùng run, unknown/sent không tự gửi lại; báo đúng phần đã hoàn tất.
- Google trả `INVALID_ARGUMENT` xác nhận từ chối cả native batch thì giữ `rejected`, cho sửa yêu cầu bằng call mới.

## Bằng chứng kiểm thử

Các nhóm có ca trùng nhau, không cộng thành một tổng. Log QA nằm trong
`.superpowers/verification/google-python/` của worktree (không vào Git).

| Gate | Kết quả |
|---|---|
| Baseline trước thay đổi | 1.176 passed, 215 skipped |
| Full offline sau bỏ tool cũ | 1.213 passed, 180 skipped; hai warning pytest import rewrite |
| Native/Automation/Docker/PG tích hợp và Python file regression | 66 passed, 424,48 giây |
| Worker graph thật + Docker: đọc, research/append, sửa/readback | 3 passed, không approval |
| Worker chết sau ghi/receipt, sau search-record, hoặc mất response | 3 passed, 203 giây; không ghi Google lần hai |
| Receipt commit không tới DB hoặc mất acknowledgement, partial success, orphan retention | 5 passed |
| Native invalid/revision policy, authority và browser UI | 15 passed, 3 Docker-upload cases deselected |
| Node streaming/final file card | 11 passed |
| Ruff / format file Python thay đổi / diff whitespace | Passed; 39 file đã kiểm format trước review |

Full offline sau các cập nhật cuối và review độc lập được ghi bổ sung khi hoàn tất.

QA dùng PostgreSQL container riêng `google-python-qa-pg`, port 55439, DB `google_python_test`;
mỗi fixture Google tạo schema riêng. DB scratch đã migrate tới `20261107_0036`.
Image build riêng `personal-agent-python-runtime:google-python-qa`; không đổi tag production.

Các lần chạy chưa đạt cũng được giữ trong log: schema RED ban đầu; tên tham số pytest `request`
phải đổi; import constant test thiếu; lỗi tự chờ job-row lock do hai fenced session lồng nhau
được sửa bằng chỉ giữ một phiên khóa ngắn ở ranh giới ghi. Test process zero-write dùng `.get`
vì worker chết trước mọi HTTP call. Native unknown-target URL được sửa theo MIME snapshot.
Node subprocess bị sandbox `EPERM` lần đầu, đã chạy lại có quyền tạo subprocess và đạt.
Một lệnh Ruff vô tình nhận glob chứa JavaScript; đã chạy lại Ruff trên file Python đúng phạm vi.

## Đã xóa và giữ

Xóa đăng ký/mã 10 tool Google, executor Sheets/Docs/Drive riêng và DTO structured không còn consumer.
Các test prepare/approve cũ được thay bằng native bridge, Automation và graph fixture Python;
giữ các regression HTTP, OAuth, credential, chọn file, quyền, journal và kết quả lịch sử.
Giữ `GoogleContextService` cho pinning/authority, `GoogleMutations` cho journal, Picker/OAuth,
Artifact Store/preview/version, API chọn file và các bảng dữ liệu hiện có. Không thêm migration/library/image production.

## Giới hạn còn phải nghiệm thu thực tế

Google OAuth/Picker/API thật, chất lượng code/model và độ giữ định dạng khi chuyển Office→Google:
**chưa chạy**. Người dùng cần làm theo [kịch bản trong manual](../manual-google-connectors.md).
Sheets còn race giữa lần kiểm phiên bản cuối và write; không hứa khóa nguyên tử liên dịch vụ.
Ảnh/OCR, toàn bộ Drive, Apps Script, quyền chia sẻ/chủ sở hữu/xóa file và workflow builder nằm ngoài phạm vi.
HTTP response/timeout không rõ vẫn giữ unknown; không xóa journal để “thử lại”.

Nguồn chính thức cho việc phân biệt batch bị từ chối và kết quả không rõ:
[Sheets atomic batch](https://developers.google.com/workspace/sheets/api/guides/batch?hl=en),
[Docs batch](https://developers.google.com/workspace/docs/api/how-tos/batch).
