# Nghiệm thu Python runtime thống nhất — 2026-11-08

Triển khai trên `refactor/unified-python-runtime`, chưa merge hoặc push. Chưa dùng model trả phí,
Google thật hoặc database ứng dụng để nghiệm thu. `.env` thật được giữ nguyên.

## Bằng chứng kiểm thử

| Nhóm | Kết quả |
|---|---|
| Toàn bộ pytest, env giả và không bật integration flags | 1167 passed, 208 skipped; 2 warning pytest import rewrite |
| Lệnh `python -m scripts.check_python_runtime`, image thực | 43 passed, 1 skipped (symlink privilege trên Windows); 1 warning cache_dir do tắt plugin cache |
| Service/storage/publication với Docker và PostgreSQL QA riêng | 17 passed |
| Worker graph thực + service, tạo file rồi Python đọc lại | 8 passed |
| Header DOCX và preview/render, giữ original bytes | 1 passed |
| PDF thiếu nội dung, ghim mọi source/version/hash/pages và lời xác nhận text-only | 1 passed |
| Graph recovery từ receipt Python không tạo file | 1 passed |
| Regression Automation authority/API/execution, PostgreSQL schema riêng | 22 passed |
| Automation Python tạo/đọc lại file không approval và case từ chối grant; reaper regression | 3 passed |
| JavaScript streaming/final-card | 11 passed |
| Ruff check toàn repo | Passed |
| Ruff format các file Python đã thay đổi | 40 file passed ở lần kiểm tra; kiểm lại sau review nếu có sửa |
| `git diff --check`, Compose profile artifacts config | Passed |

Các nhóm có test trùng nhau, không cộng số lượng thành một tổng. Integration skip trong suite thường được chạy
riêng ở các gate nêu trên. Chưa nghiệm thu model/provider thật, OAuth Google thật hoặc UI bằng tài khoản người dùng.
Global format check còn các khác biệt format cũ ngoài phạm vi ở credential helper, migration, tài liệu và test;
các file thay đổi của nhánh được kiểm riêng, không format lại những file không liên quan.

## Đã kiểm tra hành vi

- Tính toán không file, stdin, stdout/stderr hữu hạn, lỗi Python là observation cho Agent sửa tiếp.
- Import đầy đủ thư viện phổ biến và UNO; thiếu thư viện báo lỗi; mỗi lần chạy không giữ RAM.
- Excel có biểu đồ tròn/công thức ngoài whitelist cũ; preview không ghi lại hoặc thay byte file.
- DOCX, PDF trực tiếp không DOCX companion, text/CSV/JSON và PNG; font tiếng Việt.
- File upload và file Agent tạo đều có thể làm input; sửa giữ original và tạo version mới.
- Nhiều output phải chọn primary; chỉ một thẻ cuối sau streaming; lượt đọc không output giữ primary.
- Receipt zero-output replay không chạy lại; gián đoạn sau publication không tạo version trùng.
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
- Review độc lập toàn nhánh: đang thực hiện; kết quả được bổ sung sau review.
