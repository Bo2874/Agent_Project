# Review độc lập Plan02

Phạm vi review: `34fd73c..c416701`, toàn bộ D1–D3 trên `feat/workspace-documents`.
Một reviewer mới, model `gpt-6-astra`, chỉ đọc checkout; không có reviewer phụ hoặc vòng review lại.

Reviewer đánh giá owner/version, companion source, quota/publication/replay/recovery, approval,
DOCX/PDF runtime, UI và ba ảnh fixture tiếng Việt. Không có lỗi Critical hoặc Minor.
Hai lỗi Important được giữ nguyên mức độ và sửa trong một lượt theo TDD:

1. `artifact_runtime/pdf.py`: inline image trong Form XObject lồng nhau bị bỏ qua, khiến coverage
   thành `complete` và bypass duyệt text-only. Test dựng PDF có text cùng hai tầng Form, kiểm tra
   `partial`, đúng trang thiếu và từ chối rebuild thông thường: RED rồi GREEN. Giờ kiểm tra cả
   content stream của Form và tính stream lồng nhau vào giới hạn trước khi trích text.
2. `artifact_runtime/document.py`: gán `run.text` làm mất `w:sym` không thuộc phần chữ cần sửa.
   Test kiểm tra symbol, hyphen đặc biệt và các dạng break tương đương: chín ca RED rồi GREEN.
   Preflight chỉ chấp nhận các run children có thể roundtrip thành text; cấu trúc khác bị từ chối,
   không publish và không sửa bytes đầu vào.

Lượt test tập trung: **10 passed, 23 deselected**, 12,03 giây. Đã build lại code/render images;
Ruff đạt. Hồi quy toàn suite sau sửa: **1.194 passed, 2 skipped**, 1.042,54 giây.
Hai skips là kiểm tra symlink trên Windows. Không còn lỗi Critical/Important chưa xử lý;
nhánh sẵn sàng tích hợp. Kết luận sau sửa dựa trên test hồi quy, không có vòng review thứ hai.

Những hành vi reviewer để executor quyết định đều được ghi thành Ruling trong ledger và hồ sơ
quyết định: OCR/ảnh; full Office fidelity; giữ bố cục PDF gốc; file vượt giới hạn; outline DOCX quá
dài; preview backfill; chất lượng live model; Google/Automations; vận hành production.
Không có minor trì hoãn. Không chạy provider trả phí, không thay `.env`/DB/dịch vụ người dùng.
