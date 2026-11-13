# Google qua Python — kế hoạch triển khai

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans để triển khai inline, TDD từng phần và một lượt review toàn nhánh ở cuối. Người dùng đã chọn hướng và yêu cầu thực hiện.

**Goal:** Dùng duy nhất `run_python_code` cho Google file/Sheets/Docs, tự ghi theo quyền từng file, dừng khi phát hiện xung đột và loại bỏ tool cũ không còn dùng.

**Architecture:** Snapshot Google được backend lấy và lưu bền vững rồi stage như input Python. Python tạo file hoặc JSON request Google API. Backend kiểm quyền, snapshot, journal rồi tự upload/batchUpdate; sandbox không có mạng/credentials.

**Tech Stack:** Python 3.11, FastAPI, PostgreSQL/SQLAlchemy, Docker runtime hiện có, HTTPX/Google OAuth hiện có; không thêm thư viện hoặc image production mới.

**Spec:** `docs/superpowers/specs/2026-11-09-google-python-bridge-design.md`.

## Global Constraints

- Giữ input/output 20 MiB/file, 40 MiB tổng; tổng input local+Google tối đa 3, output tối đa 3, Google action tối đa 3; snapshot JSON tối đa 2 MiB.
- Không Google approval trong run mới; quyền từng file và Automation revision vẫn bắt buộc. Không tự mở rộng grant cũ.
- Native edits dùng Google API request JSON, không ép chuyển toàn bộ sang Office. User không cần biết tọa độ.
- Dừng khi phát hiện xung đột và chặn Google trong cùng run; không tự merge/retry, không hứa loại bỏ race Sheets check/write.
- Receipt bền vững trước/sau code và từng remote write; sent/unknown không chạy lại; báo đúng phần hoàn tất.
- Không sửa `.env`, dùng DB/model/Google thật, restart user process, merge/push hoặc đổi image tag đang chạy trong QA.
- Các trường mới omit khi rỗng để giữ wire/fingerprint cũ; Python thường, stream và primary giữ hành vi đã nghiệm thu.

## Review Focus

1. Snapshot đọc dở rồi worker chết: resume dùng chính snapshot trước đó, không đọc bản mới và vô tình đổi fingerprint — T2/T4.
2. Google API request lồng file ID, URL hoặc công thức ngoài phạm vi: chặn trước bất kỳ write nào — T3.
3. Source thứ hai thay đổi khi source thứ nhất chuẩn bị ghi: preflight cả bundle và báo phần đã thực hiện nếu đổi sau đó — T3/T4.
4. Google ghi xong nhưng receipt commit mất phản hồi: unknown chặn call mới, không tự upload/append lần hai — T3/T4.
5. Automation revision cũ chỉ cho một range nhưng generic edit rộng hơn: fail closed, không approval tự động biến thành tăng quyền — T5.

### Task 1: Hợp đồng Google/Python và control output

**Files:** tạo `artifact_runtime/google_types.py`; sửa `artifact_runtime/protocol.py`, `python_execution.py`; test `tests/test_google_python_protocol.py`.

**Interfaces:** `GoogleFileRef(connection_id,file_id,context_id)`, `PythonGoogleInput(ref,mode,range_a1=None,export_mime=None)`, `PythonGoogleUpload(kind='upload',connection_id,output_path,mode,input_index=None)`, `PythonGoogleUpdate(kind='batch_update',input_index,request_path)`. `PythonCodeInput.google_inputs/google_actions=()`, `PythonOutput.publish=True` omit defaults. Ref ở backend tái sử dụng shape runtime, không import backend vào image.

- [x] RED: test khai báo native input, upload, request control publish=false, primary tính trên file public; từ chối control không tham chiếu, sai index/path, quá trần, mixed input>3 và output trùng; wire call cũ giữ nguyên.
- [x] Chạy `python -m pytest tests/test_google_python_protocol.py tests/test_unified_python_runtime.py -q`, xác nhận FAIL ở schema thiếu.
- [x] Cài schema/validator và runtime collection tương thích; không Google network trong runtime.
- [x] GREEN cùng lệnh; commit `feat: extend Python contract with scoped Google inputs and actions`.

### Task 2: Google snapshots và truyền file

**Files:** tạo `backend/services/google/snapshots.py`; sửa `client.py`, `access.py`; test `tests/test_google_python_snapshots.py`.

**Interfaces:** `GoogleSnapshots(access,sessions,storage).capture(context,source)->dict` trả StoredArtifact metadata, ref, version/revision, snapshot hash, mode; `.validate(context,snapshot)` kiểm nguồn vẫn đúng. `.stage` hoặc capture tạo JSON/binary trong ArtifactStorage với owner key. `.http.download_bytes(url,token,max_bytes,params)` không redirect, không đưa header/token vào result.

- [x] RED: fake HTTP kiểm selected refs, owner, Google native metadata+range/full Doc, UTF-16 indices, binary download/export; version đổi giữa đọc → conflict; sai MIME/JSON lớn/revoke → fail; không trả token.
- [x] Chạy file test, xác nhận FAIL thiếu snapshots.
- [x] Cài native capture bounded, metadata version trước/sau, store snapshot và request snapshot authority do host ghim. Google file metadata dùng Drive version. Không nhầm JSON lỗi là snapshot rỗng.
- [x] GREEN; commit `feat: stage immutable selected Google sources for Python`.

### Task 3: Native Google API requests và automatic journal

**Files:** tạo `backend/services/google/requests.py`, `bridge.py`; sửa `mutations.py`, `client.py`, `drive.py` khi cần; tests `test_google_python_requests.py`, `test_google_python_bridge.py`.

**Interfaces:** `validate_requests(mime,body)->dict` bounded Google batch body; `GooglePythonBridge(access,sessions,storage).validate_actions(context,request,snapshots,files)` parse control; `.execute(context,request,snapshots,files,published,job_key)->list[dict]`; `.check_conflict` ghi marker `google.conflict` khi cần. Journal automatic là host-only flag, không là model arg; replay old compact receipts được chuẩn hóa.

- [x] RED: Sheets giá trị/công thức, format, sort/chart và Docs text/style/table/paragraph request hợp lệ; chặn HTTP passthrough/file ngoài phạm vi, external formula/data source, auth keys, target thay đổi, invalid/mixed action trước write.
- [x] RED: auto write không AgentApproval, same action replay một RPC, sent/unknown chặn, callback commit lỗi giữ unknown, Docs requiredRevisionId host-owned, conflict marker chặn Google call tiếp theo.
- [x] Chạy tests mới, xác nhận fail; cài native action validation, preflight toàn bộ nguồn, immutable operation key/payload và upload create/convert/binary update. Dùng lại GoogleMutations unresolved/receipt thay vì journal thứ hai.
- [x] GREEN toàn nhóm snapshots/requests/bridge; commit `feat: execute scoped Google changes from Python outputs without approval`.

### Task 4: Python orchestration, receipt và kết quả

**Files:** sửa `backend/services/artifacts/python.py`, `maintenance.py`, `agent_core/tools/python.py`, `backend/services/agent_context.py`; test `tests/test_google_python_service.py`.

**Interfaces:** `PythonToolService(...,google_bridge=None)`. Prepared receipt lưu snapshot metadata trước execute; executed receipt giữ files+snapshots+report. Native input stage sau local input. JSON control không publish; `data.google_results` là list receipt đã kiểm, giữ artifact_refs/primary cũ. Completed receipt replay không gọi API/code nữa.

- [x] RED: calculation thường không chạm Google; native read chỉ stdout; update không file public; create file+convert, mixed local/native input, readback, partial remote success, crash trước execute/sau remote/receipt recovery, sources không bị orphan sweep khi còn resume.
- [x] Chạy file test và unified service regression, xác nhận fail; cài orchestration/fingerprint/staged state và error code rõ `google_conflict`/`google_unknown`.
- [x] GREEN unit/DB/fake runner; Docker gate thật test protocol qua broker; commit `feat: orchestrate Google work through the unified Python tool`.

### Task 5: Registry, Automation, UI và nghỉ tool cũ

**Files:** sửa `backend/dependencies_tools.py`, `routes/chat.py`, `services/agent_jobs.py`, `services/automations/authority.py`, `service.py`, `schemas/automation.py`, `frontend/static/automations.js`, `core/config.py`, `.env.example`, `.env.docker.example`; thêm bridge compatibility nếu cần; dọn `agent_core/tools/google.py`, `services/google/tools.py` và schema/helper cũ sau inventory.

**Interfaces:** registry run mới chỉ có Python cho Google work; metadata selected_google_files/google_connections vẫn đưa cho LLM. Context pinning/authority nằm trong reusable Google context service, không mất do xóa invoke cũ. Thêm action revision `edit_sheet` cho native edit cả file; grant range cũ không tự broaden. `edit_doc`/`upload_artifact` vẫn exact target/connection. Backend vẫn nhận DTO cũ để đọc lịch sử.

- [x] RED: registry không google_* khi connector bật; old checkpoint chưa ghi không thực thi và không đợi approval mới; completed journal replay được; remove forced Google approvals cho đường Python.
- [x] RED: Automation chỉ đọc/ghi với revision grant, tạo file mới đúng connection, revoke/pause thắng trước commit, old range grant không thành file-wide edit; UI có lựa chọn quyền mới và vẫn thể hiện grant cũ đúng nghĩa.
- [x] Inventory toàn repo import/tool names trước xóa. Cài wiring/compatibility, cập nhật context/prompt để phân biệt local file, JSON snapshot và control action. Dọn code/tests chỉ thuộc tool cũ, giữ parsers cần cho compatibility/fixtures nếu còn hoạt động.
- [x] GREEN registry/automation/legacy/stream/UI gates; commit `refactor: retire Google-specific tools in favor of Python execution`.

### Task 6: Nghiệm thu và bàn giao

**Files:** `docs/manual-google-connectors.md`, `manual-python-runtime.md`, `PROJECT_NOTES.md`, acceptance mới, script/check fixtures hiện có và tests Docker/PG fake Google.

- [x] Chuyển fixture/model/script sang Python request files và native snapshots; không giữ test tool cũ đang chạy bằng đường công khai khác.
- [x] Full pytest env giả, Ruff, format file thay đổi, Node streaming/browser, Compose config, real Docker image QA riêng và PostgreSQL test riêng. Ghi rõ skip, lỗi/lượt chạy lại và giới hạn live.
- [x] Một review toàn nhánh độc lập theo executing-plans; tự xác minh và sửa Critical/Important bằng RED→GREEN trong một lượt rồi chạy lại suite, không re-review.
- [x] Ghi inventory xóa/giữ, quy trình OAuth một lần/refresh, conflict/unknown, tạo/sửa/readback và caveat conversion/concurrency. Không tự đổi user env hoặc bật dịch vụ.
- [x] Commit, cleanup đúng QA resources; bàn giao branch/worktree và các phần user cần test live. Không tự merge/push.
