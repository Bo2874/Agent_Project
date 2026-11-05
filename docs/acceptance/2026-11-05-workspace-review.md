# Plan01: review và nghiệm thu cuối — 2026-11-05

UI A và Excel đã hoàn thành trên `feat/workspace-excel`, worktree `.worktrees/workspace-excel`.
Base `02f41e7`; review độc lập xem đến `a8dcba4`. Sáu Important đã được xử lý trong một lượt
sửa bằng RED→GREEN; thêm một lỗi cleanup khi mất lease do tác giả tái hiện và sửa cùng lượt.
Không có lượt review độc lập thứ hai; kết luận sau sửa dựa trên regression và toàn suite mới.
Chưa merge/push, không sửa `.env` thật, không migrate DB người dùng hoặc restart dịch vụ.
Flag tính năng mặc định vẫn tắt. Word/PDF editing, Google và Automations chưa triển khai.

## Bằng chứng cuối

- `pytest -q -p no:cacheprovider`: **1141 passed, 2 skipped**, 415,57s. Skips: fixture lớn chạy
  riêng; quyền symlink Windows không có. Probe symlink Linux thật đạt. Warning `cache_dir`
  là cấu hình pytest khi tắt cacheprovider, không phải lỗi test.
- File 10.000×12 chạy riêng trên mã/image cuối: hai sheet, chart, công thức SUM=50.005.000,
  hai child từ cùng parent, original hash giữ nguyên. [Số đo](2026-11-05-artifact-excel.json).
- Chrome thật: `node tests/artifact_panel_browser.cjs` đạt race lúc mở/refresh/đổi trang,
  nhiều event liên tiếp, đóng và đổi hội thoại. QA tổng trước đó dùng worker/queue/checkpoint
  PostgreSQL thật + model fixture ở 1440/390; [desktop](2026-11-04-artifact-desktop.png),
  [mobile](2026-11-04-artifact-mobile.png).
- Ba Docker image cuối build đạt. Signed HTTP qua broker đóng gói thật: create→Calc→inspect,
  SUM=535000, hai sheet/chart và không sót scratch.
- Hard-kill scratch Windows và Linux: thu hồi workspace hết lock; giữ workspace đang chạy,
  file mới, đường dẫn lạ và symlink. Reaper có regression lỗi OSError/SQLite rồi tiếp tục sweep.
- Ruff check toàn repo đạt; format **62 file Python thay đổi** đạt; JS/CJS syntax, Compose
  với env mẫu và `git diff --check` đạt. Whole-repo Ruff format vẫn có **10 file baseline**
  không đổi: contracts README, test_agent_models và các migration 0009/0010/0011/0012/0017/0018/0019/0021.
- Live model/provider/Google không chạy; dữ liệu, credentials và services người dùng không được dùng.

## Các sửa lỗi được xác minh

- Final: fixed positional ToolContext guard bypass — test_legacy_positional_context_still_enforces_authorization RED→GREEN; whole suite 1141 passed/2 skipped.
- Final: fixed disabled-feature legacy catalog/policy compatibility — test_legacy_catalog_fingerprint_is_stable_without_artifact_capabilities, test_resume_preserves_pre_artifact_jev_policy, test_legacy_catalog_pending_approval_resumes_without_rerouting RED→GREEN; whole suite 1141 passed/2 skipped; changed authority rejection remains covered.
- Final: fixed preview loading event loss — tests/artifact_panel_browser.cjs actual Chrome RED→GREEN for initial load/multiple events/automatic refresh/pagination/close/context switch; whole suite 1141 passed/2 skipped.
- Final: fixed silent rich-text loss by explicit preflight refusal — test_rich_text_is_refused_before_silent_format_loss RED→GREEN; whole suite 1141 passed/2 skipped.
- Final: fixed permanent reaper termination — test_reaper_retries_after_transient_failure[OSError/OperationalError] RED→GREEN; whole suite 1141 passed/2 skipped.
- Final: fixed hard-kill temporary directory lifecycle — test_hard_kill_scratch_recovery_retains_active_recent_and_unknown (three phases), test_orphan_reconciliation_reclaims_hard_killed_unpack_workspace, test_reaper_reclaims_hard_killed_upload_and_validation_workspaces RED→GREEN; whole suite 1141 passed/2 skipped; separate Linux image probe PASS for flock/hard-kill/symlink refusal.
- Final: fixed lost-lease cleanup handoff — test_lost_lease_before_publication_cleans_all_staged_files RED→GREEN; whole suite 1141 passed/2 skipped.

## Rulings I made — đầy đủ, theo thứ tự thực hiện

- Ruling: shared runner protocol/operation DTOs live in artifact_runtime.protocol; backend schemas re-export/extend for HTTP views — prevents backend/config imports inside isolated containers — cost if wrong: broader shared DTO maintenance.
- Ruling: plan headings normalized from X1–X8 to Task 1–8 retaining labels — required by bundled task-start parser — no product scope change.
- Ruling: .worktrees ignore is copied into the feature branch and committed with planning baseline rather than making a setup commit on main — preserves user's main branch — cost if wrong: ignore change remains uncommitted in parent until integration.
- Task 1: Ruling: pre-change snapshot is planning baseline commit 8d67b0e rather than another copied frontend tree — Git preserves the full original files — cost if wrong: baseline preview requires rendering that commit.
- Task 2: Ruling: published rows are immutable at database level except source_run_id may become NULL on retention delete — preserves file/version metadata while allowing existing run retention — cost if wrong: metadata repair requires an explicit maintenance migration.
- Task 2: Ruling: ambiguous commit failures retain staging if reference lookup is unavailable — avoids deleting a version whose commit succeeded — cost if wrong: temporary disk use until X8 reconciliation.
- Task 2: Ruling: service ownership test asserts HTTPException 404 until HTTP routes exist in X5 — same owner boundary without inventing an API early — cost if wrong: API mapping must be verified separately in X5.
- Task 3: Ruling: bounded length-prefixed manifest + concatenated binary streams replaces output ZIP archive — eliminates extraction and archive amplification in transport; each descriptor carries size/hash — cost if wrong: maintain this internal framing protocol on both ends.
- Task 3: Ruling: standalone broker uses persistent SQLite journal with one broker process — durable job/nonce state without app database credentials or another queue — cost if wrong: scaling broker replicas needs a shared coordinator.
- Task 3: Ruling: completed result bundles retained one hour, job tombstones retained after cache expiry — bounded binary cache and no late duplicate execution — cost if wrong: unpublished results retried after an hour require a new attempt ID; journal requires operational monitoring.
- Task 3: Ruling: initial XLSX envelope/preflight and hashed openpyxl dependency land with X3 because arbitrary-code inputs must pass the same isolated preflight — establishes X4 shared boundary before exposing tools — cost if wrong: X4 rebuilds code image after implementing operations.
- Task 4: Ruling: unsafe or unsupported Office features (macro/external reference/image/unsupported drawing) are refused rather than implementing lossy-edit approval in this milestone — spec permits refusal and image processing is deferred — cost if wrong: user must provide a simpler workbook until support expands.
- Task 4: Ruling: existing unsupported safe formulas are preserved with unsupported state and skip Calc; newly requested formulas use the five-function allowlist — prevents fabricated freshness or silent formula loss — cost if wrong: some results need an external spreadsheet application to calculate.
- Task 4: Ruling: preview caps 512 cells, 512KiB metadata and 100 columns, truncating displayed text to 256 characters and whole rows to fit — keeps transport bounded without changing the downloadable workbook — cost if wrong: long/wide data needs download for a complete view.
- Task 4: Ruling: openpyxl is an application dev dependency, runtime installs only inside sandbox images; this worktree uses isolated test-extras instead of modifying the shared venv — enforces Office parsing boundary — cost if wrong: local tests require dev sync or the documented PYTHONPATH.
- Task 5: Ruling: private storage accepts DOCX/PDF/TXT for byte-for-byte imports from existing RAG library; direct upload/edit remains XLSX in this milestone — RAG currently has no XLSX type, so a working import path must preserve legacy source types without prematurely implementing their processors — cost if wrong: copied non-XLSX files offer management/download until plan 02 adds editing.
- Task 5: Ruling: new migration 0032 clears only source document/version FK fields when RAG source is deleted; immutable companion_sources keeps provenance IDs/hash — preserves independent copy and owner FK without editing applied 0031 — cost if wrong: PostgreSQL column-specific SET NULL needs to stay supported (current project PG17).
- Task 6: Ruling: scope includes explicitly pinned inputs plus owner-validated outputs created in this conversation; outputs do not replace selected parents — permits follow-up editing of Agent-created files without changing user input selection — cost if wrong: clearing input selection does not revoke access to conversation outputs. Task 6: Ruling: host capability descriptors gain immutable_output for reversible local writes without approval; plugin descriptors cannot use this exemption — existing contract otherwise prohibits the approved structured-tool UX — cost if wrong: host registration authors must declare output semantics correctly.
- Task 6: RED missing tool bridge, then 47/47 tools/document/approval/budget GREEN. Actual runner creation/recalculation/edit/replay produces two immutable versions; uploaded owner file denied until explicitly selected. Invalid Python output never published; cancellation leaves no version. Privacy boundary RED lost selected_artifacts; now forwards only scrubbed name/type (no IDs). Task 6: Ruling: MessageArtifact publication helper lands in X7 alongside its durable projection rather than a placeholder in X6 — tool pipeline already publishes validated immutable versions — cost if wrong: X7 owns binding integration tests.
- Task 7: Publication RED missing helper then 3/3 GREEN against real DB/Docker. Chart preview RED missing samples, then 12 spreadsheet tests GREEN; code image rebuilt. Browser real tool/Calc pipeline GREEN desktop/mobile: create/edit, closed stays closed, open updates, duplicate SSE deduped, immutable old preview/download, DB reload, delayed close/context switch discarded, escaped HTML-like filenames, Escape restores trigger focus and standalone library. Initial composer race RED dropped a submission before conversation loaded; composer now waits for initial load. Screenshots visually inspected. Task 7: Ruling: basic chart preview data (max 4 charts, 6 series, 100 points) is parsed in sandbox and drawn with local SVG; full charts remain in download — satisfies preview requirement without Office parsing in web process — cost if wrong: large/advanced charts need download for complete rendering.
- Task 8: Offline full gate PASS: 55 passed/1 Windows symlink skip in 303.21s. Hard broker process kill/restart replays identical bytes. Hard worker exit after publish/before receipt RED exposed two different fingerprint serializers; recovery now reuses graph canonical fingerprint, then actual subprocess restart GREEN with one version. Unknown external actions remain blocked. Benchmark exact 10,000x12, 2 sheets, chart, SUM=50,005,000, 701,675 bytes, create+2 concurrent siblings 90.69s, 52 Docker stats samples, sampled max 258.3 MiB/101.1% CPU; no sandbox profile increase. Retention scope RED then GREEN via durable MessageArtifact. Task 8: Ruling: automatic recovery is limited to saved Artifact calls with matching graph fingerprint and deterministic broker IDs, Python requires saved approval; other unknown operations stay blocked — extends existing conservative journal without replaying external writes — cost if wrong: interrupted/expired broker results still need a new user run. Task 8: Ruling: whole-repository Ruff format reports 10 pre-existing unchanged files (8 applied migrations, old model test, contracts README); format all changed Python files only — avoid unrelated edits/applied migration changes — cost if wrong: repository-wide format gate remains red on that inherited set.
- Final: Ruling: Word/PDF editing, Google and Automations remain Plans02-04 — explicitly accepted milestone boundary; current byte-copy imports retain owner checks — cost if wrong: those workflows wait for subsequent plans.
- Final: Ruling: live-model and Google quality remain unverified — offline acceptance proves infrastructure only and no paid/provider calls were authorized — cost if wrong: provider-specific failures may appear in later live acceptance.
- Final: Ruling: broker remains one process with SQLite — matches documented deployment and accepted plan — cost if wrong: multiple replicas can race without a separate coordinator.
- Final: Ruling: arbitrary Excel fidelity is not promised; refuse mixed-run rich text before editing rather than silently flattening — same refusal policy as unsupported Office features and safe for original files — cost if wrong: otherwise ordinary mixed-format workbooks cannot be edited until preservation is implemented.
- Final: Ruling: user-facing quota reclamation/deletion is deferred to a separately designed retention change; document that version deletion is unavailable and orphan cleanup cannot reclaim referenced files — immutable versions have no approved deletion semantics — cost if wrong: a user at quota needs an operator-configured quota increase or storage provisioning until retention UI exists.
- Final: Ruling: web configuration limits and fixed runner ceilings are independent; document effective minimum explicitly — raising a setting cannot expand sandbox capacity — cost if wrong: users can still encounter a tighter downstream limit.
- Final: Ruling: baseline format failures and dirty parent planning documents stay untouched — they predate this feature branch and parent work must be preserved — cost if wrong: whole-repository formatting remains red and integration must account for parent planning changes.
- Final: Ruling: scratch recovery uses generated, flat, explicitly named workspaces with OS-held leases and age checks; unknown legacy tmp names stay untouched — makes hard-kill leftovers reclaimable without guessing which arbitrary paths belong to the app — cost if wrong: unknown leftovers require operator inspection, recognized worker scratch can remain until the scheduled grace expires.
- Final: Ruling: retain pre-Artifact catalog bytes when immutable_output is false and accept pinned jev_query2.1.3 only under an unchanged authorized catalog — fixes disabled-feature deployments while preserving genuine capability-change rejection — cost if wrong: future routing semantic changes must review this compatibility allowlist.
- Final: Ruling: sweep exceptions are logged and retried every 30 seconds while cancellation propagates — transient Docker/SQLite/filesystem errors must not permanently terminate maintenance — cost if wrong: persistent failures still require operator action and logs; /health/live remains a process-liveness check.

## Deferred minors

- Final: minor (deferred): malformed Origin URL may return HTTP 500 instead of a clean 4xx; no authorization bypass established, stricter bad-header handling is future robustness work.

## Review độc lập trước lượt sửa

Bản ghi dưới đây giữ nguyên phát hiện và verdict **trước** các sửa ở trên.

# Plan01 final code review

Reviewed base `02f41e7dce3946ba83dfbbb0982c9892dc220c41` through `a8dcba4`, branch `feat/workspace-excel`. One reviewer; no delegated review. Scope: Plan01 UI A and Excel. Read the reviewer instructions, plan, design and all ledger rulings. Inspected the branch in passes, focusing on runtime/storage, authorization and compatibility, workbook processing, API/publication, and frontend. No implementation or Git state changed.

## Strengths

- Ownership is enforced both in queries and composite foreign keys. Publication uses owner locking for quota/idempotency and immutable versions with explicit parent lineage.
- Office parsing is isolated; signed bounded binary transport avoids checkpoint blobs and archive extraction. External relationships, macros, unsupported drawings and output symlinks are explicitly rejected.
- Deterministic operation/job identities, conservative unknown-outcome handling, and durable message bindings address replay and retention carefully.
- Generation/context checks prevent a closed or switched preview from displaying a late response. File names and cell content use text nodes.
- Supplied acceptance evidence includes real PostgreSQL, Docker/Calc, worker/checkpoint and browser runs rather than mocks alone.

## Issues

### Important — P1: preserve the positional authorization-guard contract

**Location:** `agent_core/tools/types.py:28` (new fields preceding `authorization_guard` at line 30).

An existing `ToolContext(user, conversation, role, run, guard)` now puts `guard` in `artifact_version_ids` and leaves `authorization_guard=None`. This silently removes the caller's authorization check. A read-only, in-memory probe using the actual ToolExecutor executed the handler successfully and never called a guard that would have denied execution. No in-repository fifth-positional caller was found, so this is an exported-contract/integration regression rather than evidence of a currently exploited application endpoint; Plan01 explicitly requires backward compatibility.

**Fix direction:** append the new fields after the old positional fields, or make only the new fields keyword-only. Add a regression asserting that a legacy positional guard still denies dispatch.

### Important — P1: deploying with the feature disabled invalidates existing routing checkpoints

**Locations:** `agent_core/contracts/capabilities.py:30`; `agent_core/routing/query_router.py:19`. Related unchanged consumers: `agent_core/capability_catalog.py:63`, `agent_core/graph/runner.py:455` and `:460`.

The default `immutable_output=False` is serialized into every existing capability descriptor and therefore changes catalog hashes even when no Artifact tool is registered. Resume rejects that hash mismatch. Independently, changing the query-policy constant to `jev_query2.1.4` makes the resume allowlist reject saved `jev_query2.1.3` decisions. Existing routed runs waiting for approval or interrupted before deployment consequently cannot continue even with Workspace Artifacts disabled. The descriptor hash difference was reproduced in memory using the actual serializer/catalog.

**Fix direction:** preserve legacy hash serialization for the false/default extension and explicitly handle compatible old routing policy snapshots. Keep genuine authority/catalog-change rejection. Test resume of a checkpoint serialized by the base revision with the new feature disabled, including a pending approval; fixing only one of these two causes is insufficient.

### Important — P2: new-version events are lost while preview is loading

**Locations:** `frontend/static/artifact-panel.js:121`, `:148`.

Every `open()` clears `current`, including sheet/page navigation and an automatic version refresh. `onVersionReady` requires `current`, so a newer version arriving before that request resolves is discarded. The older response then becomes the displayed preview/download and no later update is queued. An in-memory Node DOM harness reproduced opening v1, receiving v2, and resolving v1: only v1 was requested. The same race can leave v2 showing after v3 completed during v2's fetch.

**Fix direction:** retain the intended Artifact identity separately from loaded data and queue/reconcile ready events for the active context. Preserve the current close/context invalidation and explicit-opening behavior. Test multiple ready events while loading, plus close and conversation switching.

### Important — P2: accepted workbooks can silently lose rich text formatting

**Location:** `artifact_runtime/spreadsheet.py:115`; subsequent save at `:289`.

`load_workbook(..., keep_links=False)` uses the default `rich_text=False`. An ordinary cell containing mixed plain and bold text passes preflight, loads as a plain string without a warning, and saves with its rich-text runs removed. An in-memory workbook probe reproduced `CellRichText` becoming `str` and remaining plain after save/reload with `rich_text=True`. The inventory comparison cannot catch this: it starts after the lossy load and tracks cell values, not rich-text runs. Even an unrelated edit can therefore publish an unexpectedly degraded workbook. The immutable original survives, which limits severity, but this violates the refusal-before-loss requirement.

**Fix direction:** preserve rich text throughout the processing/preview path and verify round-trip support, or explicitly reject such workbooks before edits. Add a fixture where another cell is edited while mixed-format text must survive, or publication must be refused.

### Important — P2: one transient cleanup error permanently stops the reaper

**Location:** `artifact_runtime/app.py:59`–`:67`.

The background sweep awaits `reap_stale()` without handling transient Docker/SQLite/filesystem failures or supervising its task. One exception terminates the task for the rest of the broker process lifetime while the server and liveness endpoint continue running. This disables stale resource cleanup and the advertised one-hour bundle expiry. An in-memory lifespan probe whose fake broker failed once recorded exactly one sweep call despite further scheduling; it never retried.

**Fix direction:** contain/log recoverable sweep errors and retry on the next bounded interval, preserving cancellation. Expose or supervise unexpected task termination. Add a failure-then-success lifecycle test.

### Important — P2: hard-kill temporary directories have no reconciliation path

**Locations:** `backend/services/artifacts/runner_client.py:91`; `artifact_runtime/app.py:126`; `artifact_runtime/containers.py:179`. Related cleanup exclusion: `backend/services/artifacts/maintenance.py:97`.

A worker killed during output unpack/copy leaves `tmp*/output-*.xlsx` under Artifact storage. The reconciler only accepts owner-UUID/opaque content keys, so it permanently skips those files. A broker killed during request upload similarly leaves `upload-*` directories, and a kill during validation can leave `<job>/validated/` bytes. Broker expiry removes only four named bundle/log files, not those directories. These paths are private (no HTTP exposure was found), but repeated interrupted jobs retain potentially large, unquotaed copies indefinitely and can exhaust the shared disk. Context-manager cleanup covers exceptions, not process termination. This finding is from code inspection; no additional crash test was run during review.

**Fix direction:** give temporary storage a recognizable, isolated lifecycle and add conservative stale-directory recovery with age/active-job and symlink checks. Cover worker unpack and broker upload/validation kill points, retaining referenced immutable files and in-flight work.

## Review focus coverage

1. Response preview muộn sau chuyển hội thoại — X7 kiểm request generation và owner/context.
   - Close invalidates generation, aborts the request and clears context; conversation selection closes the panel. Owner checks are server-side. No cross-conversation content leak found. The separate loading/update race is reported above.
2. Hai sửa đồng thời cùng parent — X2 kiểm cả hai version còn nguyên, parent đúng.
   - Owner/Artifact locking, unique version/operation keys and same-Artifact parent validation preserve siblings. Supplied real concurrent-child evidence is consistent with the inspected implementation.
3. Workbook có external links/shape không giữ được — X4 yêu cầu duyệt hoặc từ chối trước publish.
   - Explicit refusal of external links/shapes/macros is appropriate for the accepted ruling. Shared preflight also gates Python inputs. Rich-text loss is an uncovered preservation case, reported above.
4. Runner bị kill sau output nhưng trước publish — X3/X8 không duplicate version hoặc lộ file tạm.
   - Deterministic IDs, durable tombstones and publication recovery prevent silent duplicate execution/publication in the reviewed paths; private staging is not served directly. Crash leftovers have incomplete cleanup, reported above. Interrupted jobs may require a new attempt as documented.
5. Input lớn/zip bomb/symlink output — X2/X3 chặn trước parse/publish, không mất bản gốc.
   - Streaming limits, bounded envelope/ZIP checks, isolated parse quotas, generated names and regular-file checks provide layered protection. Originals are create-only and remain separate from outputs. Supplied Linux symlink/large-workbook gate evidence was considered; no bypass was established in this review.

## Ledger decisions

Shared protocol DTOs, length-prefixed streams, a single-process SQLite broker, refusal of unsupported Office features, bounded preview, byte-preserving non-XLSX RAG imports, nullable provenance retention, and exact saved-call Artifact recovery are reasonable Plan01 choices. Keeping generated conversation outputs accessible without changing pinned input selection is explicit and consistent with the design. The host-only immutable-output approval exception is reasonable in principle; its serialization compatibility is not. The one-hour retention and orphan-cleanup rulings are incomplete at the failure boundaries above. Formatting only changed Python while documenting untouched baseline failures is reasonable.

## Declined to judge

- Word/PDF editing/rendering, Google OAuth/Sheets/Docs, Automations and their live-service acceptance: explicitly Plans02–04, not Plan01. Non-XLSX import ownership and byte-preservation remain in this review's scope.
- Paid/live-model quality and real Google behavior: not authorized or exercised; offline model fixtures cannot establish model/provider quality.
- Multi-replica broker coordination: the accepted deployment is explicitly one broker process with SQLite; horizontal scaling needs a separate design.
- Full Excel fidelity for arbitrary features: not promised; explicit refusal is acceptable. This does not excuse silent rich-text loss in accepted workbooks.
- User-facing deletion/retention controls to reclaim the 200 MiB quota: considered because users eventually need quota recovery, but Plan01 does not define deletion semantics or authorize removing immutable versions. Executor should explicitly rule whether a later milestone or current operational procedure is sufficient.
- Raising configurable web limits beyond fixed sandbox/transport limits: treated as independent documented safety ceilings, not a promise that increasing one setting increases all stages. Executor should rule whether configuration documentation needs clarification.
- Ten untouched global-format failures and dirty planning documents in the parent checkout: baseline/outside this branch's implementation; no reformatting or parent cleanup requested.

## Verification and limitations

Author-supplied evidence: 1127 passed / 2 skipped full suite; separate real offline gate 55 passed / 1 Windows symlink skip with Linux symlink and large benchmark actually exercised; browser 1440/390 against actual PostgreSQL queue/worker/graph/checkpoints and an offline model; whole-repository Ruff check and formatting of 56 changed Python files. These long suites were not rerun by this reviewer.

Reviewer independently ran only read-only/in-memory probes for positional authorization, legacy catalog serialization, preview event loss, rich-text conversion, and reaper failure lifecycle. No production DB, paid model, Google, or .env access. No fresh browser session or full crash integration run. No merge/push or implementation changes. A source-line printing command encountered a console Unicode encoding error after printing its earlier sections; it did not affect source or the successful probes.

## Assessment

**Ready to merge? No — fix the issues above first.**

The core isolation/version architecture and real acceptance coverage are strong, but the branch silently breaks legacy authorization/checkpoint contracts and has reproducible preview/preservation bugs plus incomplete cleanup after failures. One targeted fix pass with regressions should retain the current architecture and accepted scope.
