# Plan04 — gộp local vào main, 2026-11-07

Người dùng yêu cầu gộp vào `main` và cung cấp lệnh push; không yêu cầu tự push.
Sau `git fetch origin main`, main local và origin/main cùng ở `df49358`.
Đã fast-forward chín commit Plan04 vào main tại source `4af8135`, không có conflict.
Git tree của main và checkout kiểm thử cùng là `4d2b23d5a6a251abafe499e7edf6fd0eb7a1be10`.
Commit bàn giao sau đó chỉ cập nhật tài liệu, không sửa code ứng dụng.

## Hậu kiểm sau gộp

- Đồng bộ dependency bằng `uv sync --locked --extra dev --inexact`; giữ package hiện có.
- PostgreSQL QA riêng: container `workspace-automations-merge-qa`, loopback `65407`, database
  `workspace_automation_test`. Nâng schema đến head 0036 và `alembic check` không có drift.
  Cấu hình migration hậu kiểm tắt dotenv, dùng thông số QA được truyền riêng.
- Chạy mới toàn suite trên checkout sạch có cùng Git tree với main sau gộp, không có `.env`;
  `PYTHONPATH` trỏ chính checkout này. Provider Google/model/search là fixture.
  Kết quả: **1.304 passed / 59 conditional skips**, 404,83 giây, exit 0.
- Có ba ca worker process chết/khôi phục và 17 hồi quy review. 59 skips gồm các gate Docker
  tùy điều kiện và quyền symlink Windows; lượt hậu kiểm này không bật lại toàn bộ Docker.
  Các đợt Docker có lỗi, chẩn đoán và lượt chạy lại được giữ trong
  [biên bản review](2026-11-07-automations-review.md), không gọi toàn Docker đã xanh.
- Ruff, cú pháp toàn bộ JavaScript và Compose với profile `artifacts`/`automations` đạt.
  Cảnh báo pytest `cache_dir` do tắt cache provider, không phải lỗi ứng dụng.

Không sửa `.env`, không migrate database đang sử dụng, không tự bật feature hoặc gọi
Google/model thật. Google OAuth/API và chất lượng model thật vẫn chờ nghiệm thu chung.
Một Minor phân trang lịch sử/Inbox trên 100 mục tiếp tục hoãn; lượt active vẫn được ghim.

## Bắt đầu nghiệm thu tại máy

1. Theo phần **Khởi động** trong [File làm việc](../manual-artifacts.md): chuẩn bị runner,
   image code/render, key/storage, cấu hình Artifacts và migration trên môi trường muốn thử;
   sao lưu database trước khi nâng schema, restart web/worker sau cấu hình.
2. Plan01: Excel — tạo mới, upload/chọn file, sửa bằng chat, preview khi bấm và download.
3. Plan02: Word rồi PDF văn bản — tạo/sửa, phiên bản gốc và preview/download. Chưa OCR/ảnh.
4. Plan03: cấu hình [Google Connectors](../manual-google-connectors.md), nghiệm thu đọc/thêm/sửa
   trên từng file thử được chọn. Hai plan đầu không cần Google.
5. Plan04: theo [Automations](../manual-automations.md), chạy thử trước khi bật lịch;
   kiểm Run now, stop lượt, pause lịch, Inbox và chạy khi đóng tab.

Lệnh người dùng chạy sau bàn giao:

```powershell
cd C:\Agent_Project
git push origin main
```
