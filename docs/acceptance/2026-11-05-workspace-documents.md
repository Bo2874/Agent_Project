# Nghiệm thu Plan02: Word và PDF văn bản

Trạng thái: Plan02 hoàn tất; gate offline, review độc lập và hồi quy sau sửa đã qua.
Đã gộp local vào `main` tại commit `22def91` theo lựa chọn của người dùng; chưa push hoặc bật runtime.
[Hồ sơ sau gộp](2026-11-05-workspace-documents-main-merge.md).

Phạm vi triển khai: tạo/sửa DOCX; đọc và dựng lại PDF text; giữ nguồn DOCX của từng phiên bản PDF;
render preview PNG theo đúng owner/version. Không có OCR, xử lý ảnh, Google Connectors hay Automations.

## Bằng chứng đã kiểm tra

- D1: sửa heading/style/cell, giữ run không sửa, từ chối node ID cũ và cấu trúc Word chưa hỗ trợ.
  Tạo/sửa/tải Word qua Tool Registry và runner thật đạt 2/2; unit tài liệu đạt 15/15 sau bổ sung validation.
- D2: PDF tiếng Việt nhiều trang, bảng text, source của parent cũ sau các nhánh sửa; scan/partial
  coverage và duyệt đúng danh sách phần bị bỏ; quota/replay/ambiguous commit/orphan của bộ file.
  Lượt runtime và bundle đạt 12/12; completion gate PDF đạt 8/8.
- D3: các ca owner/version, từ chối SVG giả PNG và lỗi render không publish đạt 3/3.
  Chrome component kiểm tra page image, chọn Word/PDF, race, chuyển hội thoại và panel đóng đạt.
- Trình duyệt với queue/worker/checkpoint PostgreSQL thật, model fixture: tạo/sửa Word, tải Word
  lên, chọn input rồi sửa, giữ input đã ghim và bản gốc, đổi dropdown về bản cũ, tạo/sửa PDF từ nguồn
  đúng phiên bản, desktop/mobile và không tự mở panel. Đạt trong 241,62 giây.
- Bộ định tuyến/fixture đạt 69 ca; checkpoint JEV v2.1.3/v2.1.4 chỉ tiếp tục nếu catalog không đổi.
- Ruff, format các Python file thay đổi, toàn bộ JS syntax, Compose profile `artifacts`,
  `uv lock --check --offline` và `git diff --check` đã qua.
- `python -m scripts.check_artifact_workflow --offline --formats docx,pdf`: **34 passed**,
  752,09 giây; có một warning cấu hình `cache_dir` vì gate chủ động tắt cache provider của pytest.
  [Báo cáo browser của gate](2026-11-05-documents/browser-gate.json).
- Full suite: **1.184 passed, 2 skipped**, 1.196,32 giây; một ca thiếu quyền symlink trên Windows,
  một benchmark Excel lớn chỉ chạy trong gate offline riêng.
- Review độc lập phát hiện hai lỗi Important về ảnh trong Form PDF và ký hiệu trong Word;
  đã sửa trong một lượt với 10 ca RED→GREEN. Hồi quy sau sửa: **1.194 passed, 2 skipped**,
  1.042,54 giây. [Hồ sơ review](2026-11-05-documents-review.md),
  [quyết định triển khai](2026-11-05-documents-decisions.md),
  [kết quả kiểm thử](2026-11-05-documents-verification.txt).

## Kiểm tra bố cục và giới hạn renderer

[Mẫu DOCX](2026-11-05-documents/vietnamese-three-pages.docx),
[PDF](2026-11-05-documents/vietnamese-three-pages.pdf),
[trang 1](2026-11-05-documents/vietnamese-page-1.png),
[trang 2](2026-11-05-documents/vietnamese-page-2.png),
[trang 3](2026-11-05-documents/vietnamese-page-3.png).

Đã xem cả ba ảnh: tiêu đề dài xuống dòng trong lề, dấu tiếng Việt hiển thị đủ, bảng lặp header
ở trang 2/3, không cắt hàng qua trang, đủ hàng 01–75 và đoạn kết. Kiểm tra text extraction cũng
xác nhận có hàng đầu/cuối và đoạn kết; không dùng riêng extraction để kết luận bố cục.

[Số đo](2026-11-05-documents/renderer.json): mẫu ba trang, 75 hàng; roundtrip Docker 18,344 giây;
Writer 4,050 giây, memory.peak của container 195.710.976 byte; Poppler ba trang 2,792 giây,
memory.peak 43.601.920 byte. Đây là một mẫu kiểm tra, không phải chứng minh mọi file 20 MiB
đều hoàn thành trong giới hạn. Renderer vẫn giới hạn 1 CPU, RAM 1 GiB, CPU 60 giây, wall 90 giây;
PNG cạnh dài tối đa 1.600 pixel và tối đa 20 trang. File vượt giới hạn hoặc render lỗi bị từ chối.

[Word desktop](2026-11-05-documents/word-desktop.png),
[PDF desktop](2026-11-05-documents/pdf-desktop.png),
[PDF mobile](2026-11-05-documents/pdf-mobile.png). UUID/email trong ảnh thuộc tài khoản fixture.

## Phần chưa kiểm tra hoặc chưa triển khai

- Không gọi provider/model trả phí thật; fixture xác nhận wiring, không xác nhận chất lượng tool calling của model.
- Không sửa `.env`, chạy migration vào DB đang dùng, restart dịch vụ người dùng hoặc push.
  Đã gộp local theo lựa chọn của người dùng; xem hồ sơ sau gộp ở đầu tài liệu.
- Không OCR/ảnh, full Office fidelity, PDF mã hóa/form/annotation/script/attachment.
- Các bản sao RAG giữ nguyên bytes; bản cũ không có page preview vẫn tải được và có thể dùng làm
  input cho phiên bản chỉnh sửa mới nếu cấu trúc hỗ trợ. Không sửa ngầm metadata phiên bản cũ.
- Google và lịch tự động thuộc Plan03/04, chưa được triển khai trong nhánh này.
