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

Full offline sau sửa review ngày 2026-11-13: **1.238 passed, 188 skipped**, 105,02 giây;
hai warning pytest import rewrite. Node streaming chạy lại: **11 passed**.
Compose `config --quiet`, Ruff toàn repo và `git diff --check` đạt.

## Review độc lập và sửa lỗi

Review toàn nhánh `dd1f3c1..0964cf5` tìm thấy 7 Important, không có Critical/Minor.
Đã tự sửa trong một lượt, không gửi review vòng hai:

1. Kiểm công thức ở mọi vị trí JSON, chặn alias snake_case, hàm tùy chỉnh và phép biến đổi
   sinh công thức gián tiếp. Danh mục hàm chuẩn được ghim theo tài liệu Google.
2. Chuyển XLSX→Sheets cần kết quả kiểm công thức từ inspector trong container,
   đúng policy/hash của file; không parse Office trên backend host.
3. Đọc Sheet theo vùng kiểm `sheetId` trên chính dữ liệu được trả về, tránh đổi tab trùng tên.
4. Lưu từng snapshot đầu vào trước khi đọc đầu vào tiếp theo, giữ nguồn đã ghim khi phục hồi.
5. Cho replay receipt đã hoàn tất sau conflict; không bỏ kết quả thành công một phần.
6. Ưu tiên trạng thái `sent`/`unknown` trước kiểm phiên bản nguồn; không báo nhầm conflict
   sau một write thực sự đã tới Google. Fixture mất response nay áp dụng write trước khi lỗi.
7. Cho đọc Doc chỉ có quyền xem dù thiếu revision ID; chỉ thao tác sửa mới cần revision.

Các ca regression đã thấy RED trước sửa. Bổ sung kiểm hàm tùy chỉnh Unicode và khai báo
shared-drive support cho media download/resumable create/update (HTTP giả lập).
Toàn bộ suite offline sau sửa đạt như trên. Gate tích hợp sau sửa: **50 passed**, 539,53 giây,
gồm native Google/Automation authority, graph thật, hard-kill worker và phục hồi,
tạo/upload/chuyển file, chặn XLSX chứa IMPORTDATA trước upload nhưng giữ file local,
Excel/DOCX/PDF/runtime và publication regression. Google/model/search là fixture;
container Python, PostgreSQL, worker và checkpoint chạy thật. Không có ca lỗi trong gate cuối.

Các quyết định giữ nguyên: sandbox không có SDK Google trực tiếp; đọc lại sau sửa cần tool call mới;
quyền vùng cũ không tự nâng thành quyền toàn file; chỉ giữ DTO/adapter cần đọc checkpoint cũ;
native batch được Google xác nhận từ chối có thể sửa bằng call mới. Không tự merge/push hay đổi `.env`.
Không có Minor được hoãn. Live OAuth/Picker/conversion fidelity tiếp tục do người dùng nghiệm thu.

Quyết định trong quá trình triển khai và hệ quả:

- Tiếp tục từ thiết kế đã được người dùng duyệt, không hỏi duyệt thiết kế lần nữa;
  chi tiết kỹ thuật của tác giả vẫn cần nghiệm thu qua hành vi thực tế.
- Làm trực tiếp trong worktree riêng; giữ nhánh để thử trước khi tích hợp, nên `main` chưa có tính năng mới.
- Dùng snapshot/control JSON thay SDK có mạng trong sandbox; muốn đọc lại sau sửa cần một call mới.
- Quyền đọc/sửa file áp dụng cho Office/PDF đã chọn; quyền sửa Sheet toàn file phải cấp rõ,
  nên automation cũ có thể cần lưu revision mới.
- Xóa DTO/executor không có consumer, giữ adapter checkpoint không thực thi;
  run cũ chưa hoàn tất phải bắt đầu lại thay vì tiếp tục dùng tool đã nghỉ.
- Chỉ batch được xác nhận từ chối mới cho sửa qua call mới; mất phản hồi vẫn chặn,
  có thể cần người vận hành đối soát dù thay đổi thực tế chưa xảy ra.
- Giữ giới hạn race Sheets; một thay đổi đồng thời không quan sát được có thể lọt qua.
- Hoãn live theo lựa chọn người dùng; OAuth/Picker và độ giữ định dạng chưa được chứng minh bằng tài khoản thật.
- Chặn công thức mới không được hỗ trợ, không cách ly script/công thức sẵn có trong Google;
  chúng có thể tính lại khi file thay đổi.
- Sửa các tham số shared-drive theo API chính thức thay vì bỏ qua;
  quyền thực tế theo tenant/Drive vẫn cần thử live.

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
File đã có công thức/script bên ngoài có thể được Google tính lại sau một thay đổi hợp lệ;
bộ kiểm chỉ kiểm nội dung mới gửi, không cách ly hành vi có sẵn trong tài liệu Google.
Shared drives đã kiểm tham số API bằng fixture, chưa nghiệm thu quyền tài khoản/Drive thật.
Ảnh/OCR, toàn bộ Drive, Apps Script, quyền chia sẻ/chủ sở hữu/xóa file và workflow builder nằm ngoài phạm vi.
HTTP response/timeout không rõ vẫn giữ unknown; không xóa journal để “thử lại”.

Nguồn chính thức cho việc phân biệt batch bị từ chối và kết quả không rõ:
[Sheets atomic batch](https://developers.google.com/workspace/sheets/api/guides/batch?hl=en),
[Docs batch](https://developers.google.com/workspace/docs/api/how-tos/batch).
