# Gộp Plan02 vào main tại máy — 2026-11-05

- Người dùng chọn phương án 1: gộp local. Main fast-forward `34fd73c` → `22def91`, không có xung đột.
  Không pull/push remote. Phần cập nhật sau kiểm thử chỉ là tài liệu ghi nhận việc gộp.
- Kiểm thử lại commit `22def9159e629f5025226def6e40c44db456a4c0` trong worktree sạch, không có `.env`;
  Git tree trùng main: `6fa02e0cddb8345c76acdfe3d64bb6612f2985df`.
- Dùng DB tạm `workspace_documents_merge_test` ở loopback 65403 và broker QA 65404, dữ liệu tmpfs.
  Migration trên DB mới đạt `20261104_0032`. Không đọc/sửa `.env`, migrate DB hoặc restart dịch vụ đang dùng.
- **1.194 passed, 2 skipped**, 1.106,74 giây (18 phút 26 giây), mã thoát 0.
  [Log kiểm thử sau gộp](2026-11-05-documents-main-merge-verification.txt).
- Hai skips: `test_output_symlink_or_oversize_rejected` thiếu quyền tạo symlink trên Windows;
  `test_large_workbook_acceptance_benchmark` chỉ chạy khi bật gate offline riêng.
  Benchmark đã có bằng chứng nghiệm thu Plan01; không được tính là vừa chạy lại trong lượt này.
- Ruff check và `git diff --check` đạt. Các ca full suite bao gồm runtime Word/PDF, kiểm tra bundle/approval,
  preview owner/version và trình duyệt với queue/worker/checkpoint thật, model fixture.
  Không có lượt gọi model trả phí.
- Tài nguyên QA, nhánh `feat/workspace-documents` và worktree được dọn sau khi suite xanh.
  Không xóa stash sao lưu có sẵn hoặc worktree của kế hoạch khác.
- Tính năng chưa được bật trên môi trường người dùng. Kích hoạt runner/flag/migration production là bước
  vận hành riêng, theo [hướng dẫn Artifacts](../manual-artifacts.md).
- Tiếp theo [Plan03](../superpowers/plans/2026-11-04-workspace-03-google.md): OAuth, chọn từng file,
  Google Sheets → Docs → upload Artifact lên Drive. Cần dùng số migration mới sau `0032` đã áp dụng.
