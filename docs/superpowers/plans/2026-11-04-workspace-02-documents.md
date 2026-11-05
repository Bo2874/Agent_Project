# Workspace 02 — Word và PDF Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mở rộng Artifact foundation đã nghiệm thu để tạo/sửa DOCX và PDF có văn bản qua chat.

**Architecture:** Tái sử dụng owner/version/storage/publication/runner của plan 01. Word dùng structured edits trên DOCX; PDF được đọc thành nội dung có cấu trúc và dàn lại qua nguồn DOCX riêng, không sửa tọa độ trang gốc. Preview được render từ đúng immutable version.

**Tech Stack:** python-docx 1.2.x, pypdf hiện có, LibreOffice Writer và PDF renderer trong runner cách ly.

**Spec:** `docs/superpowers/specs/2026-11-04-agent-workspace-design.md`; phụ thuộc toàn bộ plan 01; quy ước `2026-11-04-agent-workspace-roadmap.md`.

## Global Constraints

- “Excel → Word → PDF”.
- “Có thể dàn lại trang, miễn đáp ứng yêu cầu nội dung/định dạng”.
- “Xử lý văn bản/dữ liệu trước; OCR, PDF scan và xử lý nội dung ảnh để sau”.
- “Với PDF tạo mới, đề xuất lưu thêm nguồn có thể sửa.”
- Chat-only editing và preview theo yêu cầu vẫn giữ nguyên.

## Review Focus

1. DOCX có drawing/footnote/track changes: không full-replace rồi làm mất ngầm — D1.
2. PDF scan hoặc trang hỗn hợp: báo phần không đọc được, không gọi bản trích là đầy đủ — D2.
3. Tiếng Việt, heading dài, bảng tràn trang: kiểm render, không chỉ text extraction — D3.
4. DOCX paragraph index thay đổi sau edit: dùng input version pin và stable node IDs — D1.
5. Preview/source/download nhầm phiên bản PDF: version refs cùng owner/lineage — D2/D3.

---

## File map và interfaces kế thừa

Tạo `artifact_runtime/document.py`, `artifact_runtime/pdf.py`, `artifact_runtime/rendering.py`; mở rộng `protocol.py`, sandbox image/lock và schemas trong plan 01. Dùng lại `ArtifactOperation`, `GeneratedArtifact`, `PublishedArtifact`, `publish_version`, `ArtifactToolService.execute`, API/library và panel; không tạo store/executor riêng cho Word/PDF.

Mở rộng operation kind bằng `create_docx`, `edit_docx`, `rebuild_pdf`; `payload` được thay bằng union Pydantic theo kind. `DocumentBlocks`, `DocumentOutline`, `PdfTextDocument`, `PreviewManifest` định nghĩa trong `artifact_runtime/protocol.py`. Blocks là heading/paragraph/list/table, text escaped, không HTML tùy ý. Output nguồn PDF là companion DOCX nội bộ gắn cùng version, không lộ host path; dùng trường `companion_sources` X2 đã tạo, không sửa migration 0031 đã áp dụng.

### Task 1: D1 — Word tạo mới, sửa bản đưa vào và giữ cấu trúc

**Files:** file document/protocol/image trên; `backend/schemas/artifact.py`, tools registration; mới `tests/test_document_artifacts.py`.

**Interfaces:** `inspect_docx(source: Path) -> DocumentOutline`; `apply_document(operation: ArtifactOperation, *, input_path: Path|None, output_dir: Path) -> GeneratedArtifact`. Outline gồm node ID theo input version + block position, loại/style/text/table cells; edits nhắm node ID, không replace toàn XML theo chuỗi.

- [ ] Red `test_docx_edit_heading_table_and_keep_original` asserts heading/style/bảng đã sửa, hash bản gốc giữ nguyên; `test_stale_node_id_rejected` asserts input version khác bị chặn; `test_unsupported_content_loss_requires_approval` asserts không publish khi đường xử lý làm mất ảnh/footnote.
- [ ] Chạy `python -m pytest tests/test_document_artifacts.py -q`; xác nhận fail trước implementation.
- [ ] Implement python-docx với paragraph/run/table helpers; giữ phần không sửa khi thư viện hỗ trợ. Inspect ảnh, embedded objects, revision markup trước edit; chặn hoặc tạo loss proposal để duyệt nếu không giữ được. Không có OCR/image manipulation. Thêm dependencies trong sandbox lock, không cài runtime nặng vào FastAPI process.
- [ ] Green tests; kiểm DOCX mở lại được, không chỉ ZIP hợp lệ. Chạy Artifact tools regression và commit `feat: add versioned word artifact editing`.

### Task 2: D2 — PDF văn bản, nguồn có thể chỉnh sửa và xuất lại

**Files:** `artifact_runtime/pdf.py`, `rendering.py`, mở rộng schema/service/storage để giữ companion source key; mới `tests/test_pdf_artifacts.py`.

**Interfaces:** `read_pdf_text(source: Path) -> PdfTextDocument`; `rebuild_pdf(content: DocumentBlocks, *, source_pdf: Path|None, output_dir: Path) -> GeneratedArtifact`. `PdfTextDocument` có pages text và coverage/warnings; PDF output manifest có `editable_source` là companion key do runner tạo.

- [ ] Red `test_pdf_text_edit_reflows_and_preserves_requested_content`: nội dung mới có trong PDF extract, không assert số trang cũ; `test_scan_and_mixed_pages_not_reported_complete`: coverage cảnh báo trang không text; `test_pdf_revision_uses_own_source`: parent/source/hash đúng, không lấy source bản cuối khác nhánh.
- [ ] Chạy `python -m pytest tests/test_pdf_artifacts.py -q` và thấy đỏ.
- [ ] Implement pypdf extraction trong runner có page/size/time bounds; PDF pure scan trả `unsupported_scanned_pdf`. File hỗn hợp nêu trang không đọc và cần user đồng ý phạm vi text trước chỉnh, không tự bỏ ảnh/trang. Tạo DOCX blocks làm nguồn rồi dùng Writer xuất PDF; source và PDF publish nguyên tử như một version bundle.
- [ ] Green test với PDF có tiếng Việt, nhiều trang và bảng văn bản fixture. Gửi result refs qua Tool Registry chung, không tạo PDF-only Agent. Commit `feat: rebuild text pdf artifacts with editable sources`.

### Task 3: D3 — Render preview, kiểm layout và nghiệm thu tài liệu

**Files:** rendering, `backend/routes/artifacts.py`, `frontend/static/artifact-panel.js`, `scripts/check_artifact_workflow.py`; mới `tests/test_artifact_preview_assets.py`; cập nhật manual/operations/notes/plan.

**Interfaces:** `render_version(source: Path, *, mime_type: str, output_dir: Path) -> PreviewManifest`; `GET /api/artifact-versions/{id}/preview-assets/{asset_id}` theo owner, không nhận arbitrary path. Manifest có page count, asset IDs, checksum/source version ID.

- [ ] Red `test_preview_assets_cannot_cross_owner_or_version` asserts 404; `test_render_failure_does_not_publish_incomplete_bundle` asserts không version “hoàn tất” với source/preview dở; `test_preview_content_is_safe` asserts HTML/SVG active không được phục vụ như inline user content.
- [ ] Implement DOCX→PDF preview bằng Writer, PDF→page PNG bằng Poppler trong renderer profile có giới hạn; cache theo version/hash, không tên file. Xóa preview qua cleanup chung chỉ khi không còn tham chiếu.
- [ ] Green tests và `python -m scripts.check_artifact_workflow --offline --formats docx,pdf`. Render fixture heading dài, bảng 3 trang, dấu tiếng Việt; xem ảnh từng trang để sửa overflow, font lỗi, bảng cắt và thiếu nội dung.
- [ ] Browser với worker thật/model fixture: tạo DOCX, sửa file đưa vào, xem bản cũ, xuất PDF mới/reflow; panel đóng vẫn đóng khi nhận output. Chạy regression toàn suite/Ruff/JS/Compose/diff theo roadmap.
- [ ] Ghi giới hạn định dạng/coverage, số đo renderer và bằng chứng QA; commit `feat: preview and verify word pdf artifact versions`.

## Điều kiện chuyển plan 03

Excel, DOCX và PDF có download thật, đúng owner/version; Word/PDF đã kiểm render. OCR/ảnh và full Office fidelity vẫn được ghi ngoài phạm vi. Chỉ khi ba định dạng đạt gate mới bắt đầu kết nối Google theo thứ tự đã chọn.
