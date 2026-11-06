# Workspace 03 — Google Connectors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Agent làm việc với từng Google file user chọn: Sheets trước, sau đó Docs và upload Artifact lên Drive.

**Architecture:** Google adapter backend qua Tool Registry, dùng OAuth web-server/offline access theo owner. Reuse lớp cipher và approval/journal; không tái sử dụng nguyên trạng MCP public-client flow. Google file grant theo connection và context công việc được kiểm mỗi call.

**Tech Stack:** HTTPX async sẵn có, google-auth/google-auth-oauthlib, Google Picker/Drive/Sheets/Docs APIs, PostgreSQL.

**Spec:** `docs/superpowers/specs/2026-11-04-agent-workspace-design.md`; phụ thuộc plan 01–02; quy ước roadmap.

**Nghiệm thu được user chọn (2026-11-06):** triển khai và kiểm thử offline trước, chưa có
OAuth Web Client hoặc file Google thử. Live Google **NOT RUN** cho cả Sheets/Docs/upload;
checkbox triển khai không thay thế gate live để chuyển Plan04. CLI `--live` in checklist,
không tự ghi ngoài cơ chế chat/approval. [Hồ sơ](../../acceptance/2026-11-06-google-connectors.md).

**Chốt offline 2026-11-06:** G1–G6 hoàn tất; review độc lập phát hiện bảy Important đã sửa bằng
regression RED → GREEN trong một lượt. Gate Google 71 passed; full Docker 1.266 passed/1 Windows
symlink skip. Một Minor UX hoãn, toàn bộ quyết định/đánh đổi đã lưu trong hồ sơ; chưa gộp main/push.

## Global Constraints

- “Người dùng chọn từng file; không tìm trên toàn Drive”.
- “Google API trực tiếp, đăng ký thao tác vào Tool Registry”.
- “LLM, sandbox, logs và tool results không nhận token.”
- “Ghi đè/xóa dữ liệu cần preview phạm vi và xác nhận”.
- “Google append không được coi là exactly-once.”

## Review Focus

1. Callback đổi owner, state replay hoặc refresh đồng thời — G1.
2. Picker trả ID mà tài khoản khác/quyền cũ đã mất — G2/G4.
3. Append remote thành công nhưng response bị mất — G3.
4. Bảng thay đổi sau preview/chuỗi bắt đầu `=` từ web — G3.
5. `.xlsx` trên Drive không phải native Sheet, conversion mất thành phần — G2/G6.

---

## File map và hợp đồng liên module

Tạo `backend/models/google_connection.py`, `backend/schemas/google.py`, `backend/services/google/{oauth,credentials,access,client,sheets,mutations,docs,drive,tools}.py`, `backend/routes/connections.py`, `agent_core/tools/google.py`, `frontend/templates/connectors.html`, `frontend/static/connectors.js`, migration `20261105_0033_create_google_connections.py`. Sửa model exports, main/pages/Tool Registry/config/env mẫu. Mỗi package có `__init__.py`.

Models: `GoogleConnection` (unique owner/provider cho một tài khoản đầu tiên), `GoogleOAuthState` (hash/state expiry/single-use/owner binding), `GoogleFileGrant` (connection, owner, file, MIME, generation, revoked), `GoogleWorkFile` (conversation + grant, composite owner FK), `GoogleMutation` (idempotency/run/digest/status/receipt). Automation context sau này dùng `AutomationGrant` tham chiếu `GoogleFileGrant`, không tạo FK tới bảng chưa có ở migration 0032. Không lưu plaintext token trong ORM JSON.

DTO `GoogleFileRef(connection_id: UUID, file_id: str, context_id: UUID)`; `GoogleWriteProposal(id: UUID, digest: str, snapshot_hash: str, expires_at: datetime)`; mutation states `prepared|approved|sent|completed|unknown|rejected`. Không có `approved: bool` từ model để vượt approval.

### Task 1: G1 — OAuth và credential theo owner

**Files:** model/schema/migration/oauth/credentials trên; `backend/core/credential_cipher.py`, config/env mẫu; mới `tests/test_google_oauth.py`, `tests/test_google_credentials.py`.

**Interfaces:** `begin_google_oauth(session, owner: UUID) -> OAuthStart`; `complete_google_oauth(session, owner: UUID, *, state: str, code: str) -> ConnectionView`; `get_google_access_token(session, owner, connection_id) -> str` chỉ cho trusted adapter. Routes `/api/connections/google`, `/connectors/google/callback`, `DELETE /api/connections/google/{id}`.

- [x] Red tests state single-use/TTL 10 phút/owner mismatch; refresh hai caller chỉ một request; invalid_grant → reconnect; connection khác owner 404; ciphertext không mở được với binding owner khác. Assert token/code không có trong view/log fixture.
- [x] Run `python -m pytest tests/test_google_oauth.py tests/test_google_credentials.py -q`, kỳ vọng fail.
- [x] Implement flow authorization code với state, PKCE S256, exact redirect URI, `access_type=offline`; client secret cấu hình backend, scope `drive.file`. SDK calls blocking chạy ngoài event loop; HTTPX REST calls async. Fixed Google endpoints/no redirect không kiểm soát. Token refresh row lock, rotate và giữ refresh cũ nếu provider không trả mới.
- [x] Tách cipher error trung lập nếu cần, map về PluginServiceError ở adapter cũ; thêm keyring Google riêng, không đổi token/binding plugin cũ. Kiểm scope được cấp; thiếu offline token báo cần reconnect, không coi ready cho Automation. Không suy luận account email từ token response; lấy identity metadata bằng API có quyền phù hợp.
- [x] Green tests và credential/plugin regressions hiện có; migration scratch schema; commit `feat: add owner-scoped google oauth credentials`.

### Task 2: G2 — Picker, file allowlist và UI Connectors

**Files:** access/client/routes/frontend trên; sửa `agent_core/tools/types.py`, `backend/services/agent_context.py`; mới `tests/test_google_file_access.py`.

**Interfaces:** `authorize_google_file(session, context: ToolContext, ref: GoogleFileRef, action: str) -> AuthorizedGoogleFile`; `PUT /api/connections/google/{id}/files` `{file_ids}`; `PUT /api/conversations/{id}/google-files` `{file_refs}`. `/picker-config` cấp token ngắn hạn cho đúng browser session, no-store, không log/persist JS; refresh token không ra browser. `ToolContext.google_file_refs` mặc định empty để checkpoint cũ đọc được.

- [x] Red `test_unselected_file_rejected_even_if_google_allows`, `test_revoked_grant_or_other_connection_rejected`, `test_xlsx_is_not_native_sheet`, `test_selected_file_not_implicitly_shared_with_other_conversation`.
- [x] Implement Picker theo file, backend kiểm lại provider metadata/type/permission trước lưu grant; không nhận nguyên thư mục thành quyền đọc mọi con. Native Sheet/Doc phân biệt Office upload. Chọn file theo từng cuộc trò chuyện; grant global không tự cấp mọi chat quyền dùng file.
- [x] UI state configured/disconnected/reconnect/no files/loading/error và nút thu hồi từng file. Browser token Picker chỉ ở memory, xóa sau picker; kiểm Origin/CSRF của mutation routes theo cơ chế app. Không xin scope Drive rộng hơn.
- [x] Green access tests; browser fixture chọn/bỏ file, chuyển account không hồi sinh grant cũ; commit `feat: select scoped google files for agent work`.

### Task 3: G3 — Sheets đọc, đề xuất ghi và journal chống phát lại

**Files:** sheets/mutations/client; mới `tests/test_google_sheets.py`, `tests/test_google_mutations.py`.

**Interfaces:** `read_sheet(context, ref, *, range_a1: str) -> SheetSnapshot`; `prepare_sheet_write(context, ref, change: SheetChange) -> GoogleWriteProposal`; `execute_sheet_write(context, proposal_id: UUID) -> GoogleWriteReceipt`. SheetChange union `append_rows`, `update_range`, `set_formulas`, `format_range`; proposal pin input, target, grant generation, snapshot/digest và approval record. Receipt có updated range, counts, operation ID; không raw auth headers.

- [x] Red `test_append_raw_preserves_equal_sign_as_text`: dữ liệu web `=...` không tự thành formula; `test_changed_snapshot_requires_new_approval`; `test_sent_but_lost_response_is_unknown_no_retry`: provider append call_count bằng 1 sau resume; `test_replayed_completed_write_reuses_receipt`.
- [x] Chạy `python -m pytest tests/test_google_sheets.py tests/test_google_mutations.py -q` thấy đỏ.
- [x] Implement values get/append/update và mapped batchUpdate formatting. Append mặc định RAW, formula write chỉ explicit operation. Read/range payload bounded và phân trang. Read-only/transient lỗi có retry có giới hạn; ghi không tự retry sau sent/unknown. Dedup URL theo cột được user chỉ định, không âm thầm thêm marker/cột phụ.
- [x] Recheck snapshot ngay trước update; nếu khác, invalidate approval. Ghi nhận race còn lại với sửa ngoài Google: không hứa transaction xuyên Google/DB. Approval payload fingerprint phải khớp proposal đã pin; ngoài thời hạn cần proposal mới.
- [x] Green tests gồm concurrent same-key và scope revoke giữa prepare/execute. Commit `feat: add scoped sheets reads and journaled writes`.

### Task 4: G4 — Tool Calling và nghiệm thu ba luồng Sheets

**Files:** tools/agent bridge; dependency registry; mới `tests/test_google_tools.py`, `scripts/check_google_connector.py`; cập nhật `OPERATIONS.md`.

**Interfaces:** `register_google_tools(registry: ToolRegistry, service: GoogleToolService) -> None`; tools `google_sheets_read`, `google_sheets_append`, `google_sheets_update`; mapping định dạng vào update schema. `GoogleToolService` lấy authority từ run DB, không tin authority model gửi. Proposal/approval nội bộ trả interrupt timeline hiện có; tool result chỉ summary/receipt/validated URL.

- [x] Red tests đọc/phân tích, research→append, read→update với model fixture; assert search tool thực sự được gọi, file ID đúng, approval ghi đè phải được duyệt; quyền bị revoke chặn lời gọi tiếp theo cả khi schema/cached result cũ còn.
- [x] Implement adapter vào registry, routing metadata và context selected files; không tool `google_workspace` nhận arbitrary HTTP. Chủ động giới hạn/nhóm schema theo catalog sẵn có.
- [x] Green offline fixtures; chạy `python -m scripts.check_google_connector --offline`. Sau đó OAuth thủ công và `--live --connection-id <id> --file-id <selected-test-sheet>` trên file thử user chọn; script hiện kế hoạch thay đổi, không chạm tài liệu khác. Chỉ dọn dữ liệu fixture đã xác định, không xóa Sheet.
- [x] Nghiệm thu đọc, thêm hàng, cập nhật vùng, revoke/reconnect và unknown outcome; ghi rõ live chưa chạy nếu thiếu account. Commit `feat: integrate google sheets tools with chat approvals`.

### Task 5: G5 — Google Docs văn bản

**Files:** docs/tools/schema; mới `tests/test_google_docs.py`.

**Interfaces:** `read_google_doc(context, ref) -> GoogleDocumentSnapshot`; `prepare_doc_edit(context, ref, edit: DocumentEdit) -> GoogleWriteProposal`; hai DTO `GoogleDocumentSnapshot` và `DocumentEdit` định nghĩa trong `backend/schemas/google.py`, gồm revision ID, text blocks/indices và typed edit operations. Execute qua mutation journal chung. Tools `google_docs_read`, `google_docs_create`, `google_docs_edit` có schema đóng.

- [x] Red tests read/edit text, batch index đúng sau nhiều thay đổi, revision đổi → không ghi approval cũ, ảnh/bảng ngoài phạm vi không bị full-replace ngầm, owner/type mismatch 404/unsupported.
- [x] Implement Docs batchUpdate với writeControl revision được API hỗ trợ; giữ component không sửa, reject edit không biểu diễn được. File mới do ứng dụng tạo đăng ký owner/work-context trước cho Agent dùng; tạo mới cũng journal unknown outcome.
- [x] Green unit và live trên Doc thử được chọn; đọc lại nội dung so yêu cầu, URL trả về validated. Commit `feat: add selected google docs text tools`.

### Task 6: G6 — Artifact lên Drive và gate Connectors

**Files:** drive/tools/routes; mới `tests/test_google_drive_artifacts.py`; mở rộng check script/manual/notes/plan.

**Interfaces:** `upload_artifact(context, version_id: UUID, *, mode: Literal['keep_format','google_native']) -> GoogleFileRef`; input lấy bytes từ owned immutable storage, không từ URL/path user gửi. Tool `google_drive_upload` chỉ nhận ArtifactVersion và explicit mode.

- [x] Red tests foreign version rejected, replay upload không tạo file thứ hai khi receipt đã có, sent/unknown không tự upload lại; conversion phải có proposal cảnh báo thành phần mất và user chọn rõ mode.
- [x] Implement Drive resumable upload có giới hạn kích thước, register file được tạo trong đúng work context; không tự chia sẻ công khai, chuyển owner hoặc xóa file. Mặc định tạo ở My Drive; chọn thư mục đích là bước riêng có grant, không suy ra quyền các file con.
- [x] Kiểm live `.xlsx`/`.docx` giữ định dạng và conversion Sheet/Doc bằng file fixture, đọc lại kết quả. Người dùng quyết định giữ/xóa file cloud thử; không tự permanently delete.
- [x] Chạy unit/regression/QA và record Google scopes, OAuth configuration, token reconnect/rotation, callback deployment requirements. Cập nhật notes/plan theo bằng chứng và commit `feat: export artifact versions to selected google workspace`.

## Điều kiện chuyển plan 04

Google Sheets ba luồng đã nghiệm thu trên file thử được chọn, credential refresh/revoke và journal ghi ngoài đã được kiểm. Docs/upload có kết quả riêng; không gọi toàn bộ Google Connector hoàn tất nếu mới pass mock.
