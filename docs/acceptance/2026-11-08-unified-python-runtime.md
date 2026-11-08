# Nghiệm thu Python runtime thống nhất — 2026-11-08

Triển khai trên `refactor/unified-python-runtime`, chưa merge hoặc push. Chưa dùng model trả phí,
Google thật hoặc database ứng dụng để nghiệm thu. `.env` thật được giữ nguyên.

## Bằng chứng kiểm thử

| Nhóm | Kết quả |
|---|---|
| Toàn bộ pytest, lượt cuối 2026-11-09, env giả và không bật integration flags | 1176 passed, 215 skipped; 2 warning pytest import rewrite |
| Lệnh `python -m scripts.check_python_runtime`, image thực | 51 passed, 1 skipped (symlink privilege trên Windows); 1 warning cache_dir do tắt plugin cache |
| Service/storage/publication với Docker và PostgreSQL QA riêng | 17 passed |
| Worker graph thực + service, tạo file rồi Python đọc lại | 8 passed |
| Header DOCX và preview/render, giữ original bytes | 1 passed |
| PDF thiếu nội dung, ghim mọi source/version/hash/pages và lời xác nhận text-only | 1 passed |
| Graph recovery từ receipt Python không tạo file | 1 passed |
| Journal consumer shape + hard worker crash/resume qua broker HTTP native | 2 passed sau sửa, Docker/PG QA |
| Kiểm tra source sau lỗi database, timeline không primary và PDF HTTPS qua pipeline | 2 + 1 passed, Docker/PG QA |
| Regression Automation authority/API/execution, PostgreSQL schema riêng | 22 passed |
| Automation Python tạo/đọc lại file không approval và case từ chối grant; reaper regression | 3 passed |
| JavaScript streaming/final-card | 11 passed |
| Ruff check toàn repo | Passed |
| Ruff format các file Python đã thay đổi | 44 file passed sau sửa review |
| `git diff --check`, Compose profile artifacts config | Passed |

Các nhóm có test trùng nhau, không cộng số lượng thành một tổng. Integration skip trong suite thường được chạy
riêng ở các gate nêu trên. Chưa nghiệm thu model/provider thật, OAuth Google thật hoặc UI bằng tài khoản người dùng.
Global format check còn các khác biệt format cũ ngoài phạm vi ở credential helper, migration, tài liệu và test;
các file thay đổi của nhánh được kiểm riêng, không format lại những file không liên quan.
Một lượt full suite trước đó có lỗi Windows EBUSY khi dọn profile trình duyệt tạm sau khi các assertion UI đã đạt.
Ca riêng chạy lại đạt, rồi lượt full suite cuối cũng đạt toàn bộ; không sửa mã UI không liên quan.

## Đã kiểm tra hành vi

- Tính toán không file, stdin, stdout/stderr hữu hạn, lỗi Python là observation cho Agent sửa tiếp.
- Import đầy đủ thư viện phổ biến và UNO; thiếu thư viện báo lỗi; mỗi lần chạy không giữ RAM.
- Excel có biểu đồ tròn/công thức ngoài whitelist cũ; preview không ghi lại hoặc thay byte file.
- DOCX, PDF trực tiếp không DOCX companion, text/CSV/JSON và PNG; font tiếng Việt.
- File upload và file Agent tạo đều có thể làm input; sửa giữ original và tạo version mới.
- Nhiều output phải chọn primary; chỉ một thẻ cuối sau streaming; lượt đọc không output giữ primary.
- Receipt zero-output replay không chạy lại; gián đoạn sau publication không tạo version trùng.
- Kết quả phục hồi vào graph journal được chuẩn hóa đủ `usage` và `context_blocks`, kể cả receipt compact đã lưu.
- Scope user/conversation, parent phải là input rõ ràng, checksum và file grants của Automation.
- Macro, active Word field, executable hyperlink, symlink/path traversal bị chặn; text UTF-8 dài không lỗi giữa ký tự.
- Không mạng, non-root, readonly root/input, không secrets/socket; cancellation/cleanup có kiểm tra nhãn tài nguyên.
- Race hai reaper cùng xóa volume đã có test RED→GREEN, không che lỗi Docker khác.

## Inventory chuyển đổi

- Giữ: `run_python_code`, image `personal-agent-python-runtime:1`, broker HTTP, Artifact Store/version/UI,
  parsers/renderers nội bộ, scheduler/worker/Google tools hiện có.
- Không quảng bá cho run mới: `artifact_create`, `artifact_edit`, `artifact_inspect`, `run_artifact_python`,
  `artifact_rebuild_pdf_text_only`. Chỉ có adapter ẩn cho checkpoint cũ.
- Xóa source: executor/contract/tool bridge `agent_core/code_execution/*.py`, Docker sandbox cũ,
  Dockerfile/lockfile artifact-code/render/runner trùng và các gate/test của contract lỗi thời.
- Thay bằng schema/runtime/registry/security tests, fixture Python sinh file → đọc lại → stream final,
  một Dockerfile/lockfile và `scripts.check_python_runtime`.
- Giữ tài liệu contract/threat-model cũ dưới `agent_core/code_execution/` như tài liệu lịch sử.

## Vận hành còn lại

- Người dùng bật `PYTHON_SANDBOX_ENABLED=true` trong `.env` khi chuyển bản chạy, build image và restart broker/web/worker.
- Có broker native cũ đang chạy (PID 11016 và launcher 21760 lúc kiểm tra). Không tự dừng tiến trình của người dùng;
  chưa xóa tag code/render/sandbox cũ khi chưa xác minh hết dependent sau restart.
- Hướng dẫn đầy đủ: [manual-python-runtime.md](../manual-python-runtime.md).
- Đã dừng đúng broker native QA (launcher 7892, child 26412) sau khi kiểm tra StartTime/command/port,
  và container PostgreSQL QA `agent-unified-python-qa-20261108` sau khi kiểm tra full ID/label.
  Broker/process và database của người dùng không bị dừng hoặc sửa.
- Đã xóa đúng tag `personal-agent-artifact-runner:dev` sau khi xác minh không có container phụ thuộc.
  Ba tag sandbox/code/render còn được giữ vì broker native cũ của người dùng vẫn đang chạy.

## Review độc lập và lượt sửa

Reviewer đánh giá range `983b985..2303bed`, kết luận chưa sẵn sàng merge tại HEAD đó: không có Critical,
có 8 Important. Đã thực hiện một lượt sửa của tác giả; không có lượt review độc lập thứ hai.

- Giữ source thuộc receipt khi publication rollback vì lỗi database; replay cùng call phục hồi và chỉ tạo một version.
- Staging của run completed/cancelled hết bảo vệ sau thời gian orphan grace; run failed giữ tối đa 7 ngày để resume.
  Run đang hoạt động và file đã publish vẫn được bảo vệ.
- Timeline không lấy file trung gian làm final khi unified Python có receipt nhưng chưa lưu primary.
- Render DOCX dùng kiểm tra cấu trúc/an toàn, không ép qua whitelist sửa tài liệu cũ. Có regression PAGE field,
  ảnh và hyperlink; source gốc không bị ghi lại.
- PDF cho phép Link/URI HTTP, HTTPS, mailto và điều hướng nội bộ; JavaScript/Launch/file URI vẫn bị chặn.
- Kiểm tra công thức ở defined names và các phần XML có công thức, không chỉ ô worksheet.
- Phân tích token công thức để giữ literal `"|"`, text `"WEBSERVICE("` và spill reference hợp lệ.
- Compose mặc định tắt tool khi chưa bật broker; hướng dẫn rõ computation-only vẫn cần profile broker.

Các lỗi được xác minh trước khi sửa bằng regression RED→GREEN. Test Word có ảnh/hyperlink là coverage bổ sung;
lỗi renderer được tái hiện bằng PAGE field, lỗi PDF bằng HTTPS Link annotation trực tiếp.
Reviewer không đánh giá provider/OAuth thật hoặc UI đăng nhập; các phần này vẫn cần người dùng nghiệm thu.

Gate service cuối chạy 17 ca đạt nhưng crash/resume worker phát hiện thêm `KeyError: usage` ngoài 8 finding của reviewer.
Nguyên nhân là chép receipt compact sang graph journal mà không chuẩn hóa full ToolResult. Đã thêm assertion consumer
shape và xác minh RED, rồi chuẩn hóa tại ranh giới recovery để giữ tương thích receipt đã có. Kết quả chạy lại gate
crash/resume được ghi trong bảng kiểm thử cuối.

## Các quyết định khi triển khai

1. Dùng nhánh feature riêng đang có trong checkout gốc, không tạo worktree mới. Người dùng đã yêu cầu triển khai
   sau khi duyệt thiết kế; không sửa main. Nếu checkout bị đổi bên ngoài thì cần đối chiếu trước khi tích hợp.
2. Ghi ledger bằng PowerShell thay script POSIX trên Windows; giữ đủ identity/evidence.
   Rủi ro là khác cách chạy harness, được bù bằng ledger và lệnh kiểm chứng thực tế.
3. Giới hạn bảo vệ staging của run failed ở 7 ngày; active run giữ đến khi kết thúc, completed/cancelled dùng grace 24 giờ.
   Resume quá hạn có thể cần yêu cầu mới; version đã publish không bị ảnh hưởng.
4. Dùng fixture offline thay model/provider/OAuth/UI tài khoản thật trong lần triển khai này.
   Hành vi trên tài khoản và provider thực vẫn cần nghiệm thu.
5. Giữ broker native cũ và tag sandbox/code/render đang có khả năng được dùng; chỉ xóa tag runner dev không có dependent.
   Đổi hành vi của dịch vụ đang chạy cần restart và các tag giữ lại tiếp tục chiếm đĩa.
6. Giữ thời hạn bundle broker một giờ và tombstone ngăn thực thi lại khi chưa rõ kết quả.
   Gián đoạn quá hạn mà chưa lưu source vào receipt có thể phải tạo yêu cầu mới; không chạy mù lại code cũ.
7. Giữ các đường Automation grants, legacy adapter, completed-primary/readback và byte-preservation sau review và regression.
   Reviewer không xác lập thêm lỗi cụ thể ở các phần này; tình huống ngoài coverage vẫn có thể tồn tại.

Không có Minor được reviewer nêu riêng hoặc hoãn lại.
