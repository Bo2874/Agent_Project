# Workspace 01 — Giao diện A và Excel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Người dùng tải/tạo/sửa Excel qua chat, giữ phiên bản và mở preview theo yêu cầu, tải `.xlsx` thật.

**Architecture:** Shell A trên frontend hiện có; Artifact Manager giữ file bất biến và ownership. Agent gọi structured tools hoặc Python được duyệt qua runner cách ly; không chạy openpyxl trên file ngoài trong web process. Không enqueue rồi chờ một artifact job khác trong cùng pool Agent worker, tránh deadlock khi pool đầy.

**Tech Stack:** Stack hiện có, openpyxl 3.1.x, Docker runner, LibreOffice Calc/Writer, Jinja2/JavaScript.

**Spec:** `docs/superpowers/specs/2026-11-04-agent-workspace-design.md`; quy ước chung: `2026-11-04-agent-workspace-roadmap.md` cùng thư mục.

## Global Constraints

- “Chỉ bấm thẻ hoặc chọn file trong thư viện mới mở preview.”
- “Nhận file mới hoặc bản sửa không tự mở panel đang đóng”.
- “Tạo và chỉnh sửa qua chat; chưa có trình biên tập trực tiếp”.
- “Sandbox contract cần mở rộng để truyền file nhị phân qua kênh có giới hạn tới storage, không nhét base64 file lớn vào tool observations.”
- Giữ upload tại thư viện, tách file làm việc khỏi điều kiện RAG-ready. Chưa OCR/ảnh/macro.

## Review Focus

1. Response preview muộn sau chuyển hội thoại — X7 kiểm request generation và owner/context.
2. Hai sửa đồng thời cùng parent — X2 kiểm cả hai version còn nguyên, parent đúng.
3. Workbook có external links/shape không giữ được — X4 yêu cầu duyệt hoặc từ chối trước publish.
4. Runner bị kill sau output nhưng trước publish — X3/X8 không duplicate version hoặc lộ file tạm.
5. Input lớn/zip bomb/symlink output — X2/X3 chặn trước parse/publish, không mất bản gốc.

---

## File map và contract chung

Mới: `backend/models/artifact.py`, `backend/schemas/artifact.py`, `backend/services/artifacts/{storage,service,validation,runner_client,publication,tools}.py`, `backend/routes/artifacts.py`; các package có `__init__.py`. Runner riêng: `artifact_runtime/{app,protocol,containers,spreadsheet,recalculation}.py`, `infra/docker/artifact-runner/{Dockerfile,requirements.lock}`, `infra/docker/artifact-sandbox/{Dockerfile,runner.py,requirements.lock}`.

`Artifact`, `ArtifactVersion`, `ConversationArtifact`, `MessageArtifact` nằm trong migration `20261104_0031_create_artifacts.py`. `ArtifactVersion` có immutable storage key, hash, MIME, size, parent, source document version, source run, `calculation_status`, typed JSON `preview_manifest` và `companion_sources`; hai JSON ban đầu empty để Word/PDF mở rộng không sửa migration đã áp dụng. Không lưu bytes. Model fields/types được định nghĩa trong X2 và tái sử dụng, không tạo DTO khác cùng tên.

`ArtifactOperation` Pydantic: `kind: Literal['create_xlsx','edit_xlsx','python']`, `parent_version_id: UUID|None`, `payload: dict`, `code: str|None`; extra forbidden, validator theo kind. Structured payload có discriminated operations `write_cells`, `add_sheet`, `set_formula`, `format_range`, `add_chart`, không có HTTP/path/shell. `PublishedArtifact` gồm `artifact_id`, `version_id`, `parent_version_id`, `name`, `mime_type`, `size_bytes`, `sha256`, `calculation_status`.

### Task 1: X1 — Shell A và typography

**Files:** tạo `frontend/templates/workspace.html`, `frontend/static/workspace.{css,js}`, `frontend/static/fonts/` và giấy phép font; sửa `base.html`, `chat.html`, `dashboard.html`, `documents.html`, `memories.html`, `plugins.html`, `admin.html`, `styles.css`, `backend/routes/pages.py`, `backend/core/config.py` và env mẫu. Không sửa `.env` thật.

**Interfaces:** `initWorkspaceShell(root: HTMLElement): void` trong `workspace.js`; `data-workspace-shell`, `data-sidebar-toggle`. Giữ các hook `data-chat-app`, `data-message-form`, `data-new-chat`, document picker, logout, SSE và approval đang có. Flags `WORKSPACE_ARTIFACTS_ENABLED`, `GOOGLE_CONNECTOR_ENABLED`, `AUTOMATIONS_ENABLED` mặc định false; nav chỉ hiện module đã bật, không có link giả.

- [x] Giữ snapshot trước sửa; đưa shell/sidebar A và Inter/Lora đã chốt vào các trang đăng nhập sau auth, giữ admin link theo role và trạng thái empty/loading/error.
- [x] Chạy `node --check frontend/static/workspace.js`, render Jinja các trang với user/admin fixture; kiểm không thiếu hook JavaScript hiện có. Không viết unit test màu/font.
- [x] Browser QA chat mới, chat đang stream, approval, stop/resume, RAG picker, logout và sidebar ở 1440px/390px. Xác nhận network font không cần Google Fonts.
- [x] Review diff và commit `feat: add approved workspace shell and typography`.

### Task 2: X2 — Metadata, private storage và phiên bản

**Files:** file model/schema/storage/validation/service ở file map; `backend/models/__init__.py`, `backend/core/config.py`; migration 0031; mới `tests/test_artifact_storage.py`, `tests/test_artifact_versions.py`.

**Interfaces:** `ArtifactStorage.put(source: BinaryIO, key: str, *, max_bytes: int) -> StoredArtifact`; `open_owned_version(session, owner: UUID, version_id: UUID) -> ArtifactVersion`; `publish_version(session, owner: UUID, *, artifact_id: UUID|None, parent_version_id: UUID|None, staged: ValidatedArtifact, operation_key: str) -> PublishedArtifact`. `ValidatedArtifact`/`StoredArtifact` dataclasses được định nghĩa trong validation/storage, có key/hash/size/MIME. Lần upload đầu `origin='upload'`; output `origin='agent'`.

- [x] Red tests: `test_other_owner_cannot_open`: `assert response.status_code == 404`; `test_concurrent_children_preserve_parent`: `assert child_a.id != child_b.id`, `assert child_a.parent_version_id == child_b.parent_version_id == original.id`, `assert sha256(original_bytes_after) == original.sha256`; `test_publish_replay_same_operation`: `assert version_count == 1`; `test_traversal_and_zip_bomb_rejected`: `assert outside_files == []`.
- [x] Chạy `python -m pytest tests/test_artifact_storage.py tests/test_artifact_versions.py -q`; kỳ vọng fail vì module/interface chưa có.
- [x] Tạo schema với composite ownership FK/unique constraints; parent phải thuộc cùng Artifact, run/source owner được kiểm lại. Cấp version number bằng row lock ngắn. Stage file rồi commit metadata; publication failure giữ bản gốc và cleanup file chưa tham chiếu.
- [x] Khởi tạo `ARTIFACT_MAX_FILE_BYTES=20971520`, `ARTIFACT_MAX_USER_BYTES=209715200`, archive unpacked tối đa `104857600` bytes/2000 entries; giới hạn upload streaming trước đọc toàn file. Lưu tại `ARTIFACT_STORAGE_PATH`, deny symlink/HTML/executable; checksum tính theo stream. Đây là mặc định ban đầu có cấu hình, X8 phải đo trước nghiệm thu.
- [x] Green các test trên và chạy migration trong schema thử riêng; ghi nhận conflict concurrent và rollback. Commit `feat: add private artifact storage and immutable versions`.

### Task 3: X3 — Runner và kênh file nhị phân cách ly

**Files:** runner/client/protocol/containers/Dockerfiles ở file map; mới `tests/test_artifact_runner.py`, `scripts/check_artifact_runner.py`; sửa `compose.yaml`, `.env.example`, `.env.docker.example`, `infra/docker/app/README.md`, `pyproject.toml` (package discovery thêm `artifact_runtime*`).

**Interfaces:** `ArtifactRunnerClient.execute(request: ArtifactRunRequest, inputs: tuple[ArtifactInput,...], *, cancel: Callable[[], Awaitable[None]]) -> StagedArtifactResult`; DTO trong `artifact_runtime/protocol.py`. `ArtifactInput` chỉ có generated name, MIME, size, checksum và nguồn stream do backend cấp; không nhận host path từ model. Runner `POST /v1/jobs`/`DELETE /v1/jobs/{job_id}`; request manifest được HMAC theo job ID/nonce/expiry/hash các blob; chỉ backend giữ auth key. Output archive + bounded manifest stream tới staging, không vào checkpoint.

- [x] Red `test_runner_refuses_arbitrary_image_mount_and_environment` asserts schema reject; `test_output_symlink_or_oversize_rejected` asserts không publish; `test_cancel_reaps_child_and_volume` asserts không còn resource có label đúng job; `test_input_is_readonly_and_network_blocked` asserts thay input/mạng đều fail.
- [x] Chạy `python -m pytest tests/test_artifact_runner.py -q`; fail trước implementation.
- [x] Implement broker nội bộ với fixed image/profile, deadline request và idempotent job IDs; duplicate started job không chạy lại. Dùng volume input riêng được trusted intake container nạp, mount read-only vào execution container; output tmpfs truyền archive theo stream. Validate từng member, không `extractall` không kiểm soát. Cleanup/reaper chỉ xóa resource mang đúng label runner/job đã xác minh.
- [x] Profile code: 1 CPU, 512 MiB RAM, 15s CPU/30s wall, tối đa 3 output/20 MiB mỗi file/40 MiB tổng. Profile LibreOffice: 1 CPU, 1024 MiB RAM, 60s CPU/90s wall, pids 64. Root read-only, nonroot, drop caps, no-new-privileges, network none; không có secret/socket trong execution container. Tool Python nhỏ cũ giữ nguyên limits và approval.
- [x] Compose chỉ broker có Docker access; không public port, app/worker không mount socket. Cấu hình host Windows bind loopback khi chạy local. Chạy green unit, `python -m scripts.check_artifact_runner` với Docker thật và `docker compose config --quiet`; commit `feat: add isolated artifact runner and bounded file transport`.

### Task 4: X4 — Excel structured operations, recalc và kiểm tra kết quả

**Files:** `artifact_runtime/spreadsheet.py`, `artifact_runtime/recalculation.py`, sandbox runner/image/lock, `pyproject.toml`, `uv.lock`; mới `tests/test_spreadsheet_artifacts.py`, `tests/test_spreadsheet_recalculation.py`.

**Interfaces:** `apply_spreadsheet(operation: ArtifactOperation, *, input_path: Path|None, output_dir: Path) -> GeneratedArtifact`; `recalculate_xlsx(source: Path, *, output_dir: Path) -> CalculationReport`. Những Path này chỉ do trusted runner tạo trong sandbox. `GeneratedArtifact`/`CalculationReport` trong protocol, chứa manifest và calculation state `fresh|unsupported|failed`.

- [x] Red `test_create_and_edit_preserve_formula_and_original`: data `[120000,65000,150000,200000]`, `assert ws['C6'].value == '=SUM(C2:C5)'`, `assert recalculated['C6'].value == 535000`, `assert len(wb.sheetnames) == 2`, `assert input_hash_after == input_hash_before`; kiểm inventory chart sau recalc. `test_stale_cache_never_reported_fresh`: `assert preview.calculation_status != 'fresh'` khi chưa recalc; `test_external_link_or_loss_requires_approval`: `assert published_versions == []` trước duyệt.
- [x] Chạy hai file test trên để xác nhận đỏ.
- [x] Implement với openpyxl 3.1.x được lock; typed operations không eval code. Hỗ trợ ban đầu SUM/SUMIF/AVERAGE/COUNT/IF, column/bar/line chart; inspect workbook package trước load. Detect macros/external references/unsupported drawings, báo phạm vi mất nội dung để approval hoặc từ chối; Python fallback không vượt preflight này.
- [x] Cài Calc và python-uno vào profile render riêng, dùng user profile/UNO pipe tạm cho từng job, disable macro/link update, no network. Gọi UNO `calculateAll()` rồi store XLSX ra file mới; không chỉ đổi định dạng hoặc đặt cờ recalc-on-open rồi coi cache đã fresh. So formula/sheet/drawing inventory trước-sau, không âm thầm làm mất chart. JSON preview tách formula và cached value, escape qua text nodes; unsupported formula hiện trạng thái chưa tính, không số giả.
- [x] Green unit + Calc thật bằng fixtures; mở workbook bằng openpyxl `data_only` xác minh `535000`, QA chart và sheet. Nếu Calc không giữ round-trip trong fixture, sửa engine/gate trước nghiệm thu. Commit `feat: create edit and recalculate spreadsheet artifacts`.

### Task 5: X5 — Upload tại thư viện, chọn file và API theo owner

**Files:** `backend/routes/artifacts.py`, service/schema; sửa `backend/main.py`, `backend/routes/pages.py`, `backend/services/agent_context.py`, `frontend/templates/documents.html`, `frontend/static/documents.js`; mới `tests/test_artifact_api.py`.

**Interfaces:** `POST /api/artifacts` multipart upload; `POST /api/artifacts/import-document` `{document_version_id}`; `GET /api/artifacts`, `GET /api/artifacts/{id}/versions`, `GET /api/artifact-versions/{id}/download`; `GET/PUT /api/conversations/{id}/artifacts` `{version_ids: list[UUID]}`. `set_conversation_artifacts(session, owner, conversation_id, version_ids) -> tuple[PublishedArtifact,...]`; selectors check owner, không RAG status.

- [x] Red `test_upload_xlsx_without_rag_index`: uploaded version được chọn ngay, không tạo ingestion job; `test_guess_version_and_import_foreign_document`: 404; `test_unknown_length_upload_stops_at_limit`: stream bị chặn; `test_selected_version_is_pinned`: phiên bản mới không tự đổi input user đã chọn.
- [x] Chạy `python -m pytest tests/test_artifact_api.py -q` để thấy fail.
- [x] Implement tab “File làm việc” tại thư viện, upload xlsx vào Artifact storage; RAG file cũ dùng action import đã kiểm quyền và copy immutable. Nút thêm file trong chat mở picker có link tới thư viện. Đăng ký middleware streaming cho endpoint mới, auth/cache/download filename headers an toàn.
- [x] Green API và kiểm thư viện RAG cũ vẫn upload/QA được. Commit `feat: select uploaded artifacts independently of rag indexing`.

### Task 6: X6 — Tool Registry, pipeline xử lý và Python fallback

**Files:** `backend/services/artifacts/tools.py`, `publication.py`, mới `agent_core/tools/artifacts.py`; sửa `backend/dependencies_tools.py`, `backend/services/agent_context.py`, `agent_core/tools/types.py`, routing metadata nếu cần; mới `tests/test_artifact_tools.py`.

**Interfaces:** `register_artifact_tools(registry: ToolRegistry, service: ArtifactToolService) -> None`; `ArtifactToolService.execute(context: ToolContext, request: ArtifactOperation) -> ToolResult`. Tools `artifact_inspect`, `artifact_create`, `artifact_edit`, `run_artifact_python`; kết quả chỉ có `artifact_refs: list[PublishedArtifact]`, text summary/warnings. `ToolContext` thêm `artifact_version_ids: tuple[UUID,...]=()` backwards compatible.

- [x] Red `test_create_edit_tools_publish_once_after_validation`, `test_file_task_routes_to_tools_not_rag_only`, `test_python_fallback_requires_approval`, `test_tool_cannot_open_unselected_version`; assert tool side effect, selection và scope đúng, không bytes/token/path trong output.
- [x] Chạy `python -m pytest tests/test_artifact_tools.py -q`; fail trước triển khai.
- [x] Backend kiểm quyền, input pin, operation key/run journal, gọi runner và validator, publish version. Structured operations không xin duyệt mỗi sửa có thể phục hồi; raw Python bắt buộc approval hiện có. Không chờ artifact job thứ hai trên cùng queue worker pool. Failure/cancel không publish bản hỏng; output chưa rõ đối soát job ID.
- [x] Green các test tool và regression document tools/approval/budget: `python -m pytest tests/test_artifact_tools.py tests/test_document_tools.py tests/test_tool_approvals.py tests/test_agent_budgeting.py -q`. Commit `feat: expose scoped artifact tools to the agent`.

### Task 7: X7 — Thẻ file, preview và phiên bản theo bố cục A

**Files:** mới `frontend/templates/artifacts.html`, `frontend/static/artifacts.js`, `frontend/static/artifact-panel.js`; sửa `chat.html`, `chat.js`, `workspace.css`, `backend/routes/pages.py`, `backend/routes/chat.py`, `backend/services/agent_runs.py`, `backend/services/artifacts/publication.py`; mới `tests/test_artifact_publication.py`.

**Interfaces:** `GET /api/artifact-versions/{id}/preview` trả DTO paginated có cells/sheet/calculation state hoặc preview asset refs; `createArtifactPanel(root, {requestJson}): {open(versionId, contextId), close(), onVersionReady(ref, contextId)}`. `artifact_ready` run event chứa validated IDs; `MessageArtifact` cho reload, unique `(message_id,version_id)`. Không tự parse ID/URL từ prose của model làm quyền file.

- [ ] Red publication test: replay cùng event không nhân card; reload tìm đúng version; owner khác không lấy được metadata. Dùng version fixture đã publish trong X2, không file nhị phân trong SSE.
- [ ] Implement thẻ qua DOM text nodes, panel chỉ mở bằng explicit click, close trả full chat width. Event bản mới chỉ update panel đang mở của cùng Artifact; đóng/đổi chat phải invalid request generation và bỏ response cũ. Version selector/parent lineage và download đều dùng immutable version ID.
- [ ] Green publication test; `node --check` các JS thay đổi. Browser QA: tạo/sửa lúc panel đóng vẫn đóng; mở → sửa cập nhật; đóng khi fetch chậm; chuyển chat A→B không hiện file A; chọn phiên bản cũ; narrow viewport/back/Escape/focus. Kiểm reload thật từ DB.
- [ ] Commit `feat: show artifact versions on demand beside chat`.

### Task 8: X8 — Nghiệm thu Excel xuyên suốt và vận hành

**Files:** mới `scripts/check_artifact_workflow.py`, `docs/manual-artifacts.md`; sửa `OPERATIONS.md`, `PROJECT_NOTES.md`, `PROJECT_PLAN.md` và config/image khi số đo cần điều chỉnh.

**Interfaces:** `python -m scripts.check_artifact_workflow --offline` dùng model fixture, runner/DB thật; `--live-model` chỉ dùng khi được user yêu cầu riêng. Fixture 2 owners, file 10.000 hàng × 12 cột, 2 sheet, công thức và chart; script chỉ cleanup IDs/schema/resource của chính nó.

- [ ] Kiểm pipeline create/edit/import/select/replay/download thật; hai sửa cùng parent; cancel giữa runner và publish; kill runner/worker rồi resume không nhân version. Chạy vượt quota file, zip bomb và output symlink probes.
- [ ] Đo CPU/RAM/time và kích thước; profile mặc định X3 phải hoàn thành fixture hoặc được điều chỉnh công khai có regression. Không nới legacy Python profile. Xác nhận không credential/network/socket trong execution container.
- [ ] Chạy toàn pytest, Ruff check/format, Node syntax, Compose config và diff check. Chạy browser với worker thật và model fixture; ghi riêng live model chưa chạy. Feature chỉ bật khi gate đạt.
- [ ] Ghi hướng dẫn startup/health/cleanup/rollback flag; cập nhật tài liệu theo bằng chứng, commit `docs: record spreadsheet artifact acceptance and operations`.

## Điều kiện chuyển plan 02

User thực sự tải được `.xlsx` đúng version, công thức/biểu đồ fixture đã kiểm, isolation/cancel/replay đạt và UI A không tự mở preview. CSV mock, unit tests xanh hoặc sandbox chỉ tạo được file không đủ để tick hoàn tất.
