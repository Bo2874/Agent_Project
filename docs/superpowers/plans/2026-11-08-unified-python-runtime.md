# Kế hoạch hợp nhất Python runtime

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans để thực hiện lần lượt. Người dùng đã yêu cầu bắt đầu triển khai thiết kế được duyệt.

**Goal:** Thay các công cụ tạo/sửa file bằng một `run_python_code` tổng quát, dùng một image và giữ nguyên dữ liệu, streaming và khả năng khôi phục.

**Architecture:** Tái sử dụng broker cách ly và Artifact Store. Hợp đồng Python có input version, output khai báo, stdout/stderr và primary output; receipt không phụ thuộc việc tạo file. Container mới mỗi lần chạy; file bền vững theo phiên bản.

**Tech Stack:** Python, Pydantic, FastAPI, PostgreSQL/SQLAlchemy, Docker, LibreOffice/UNO, JavaScript.

**Spec:** `docs/superpowers/specs/2026-11-08-unified-python-runtime-design.md`

## Global Constraints

- Một public tool `run_python_code`; `requires_approval=False`.
- Một image `personal-agent-python-runtime:1`; không network, không secrets/socket trong container chạy code.
- Không giữ biến Python giữa các lần chạy; input chỉ là các phiên bản được cấp quyền.
- Mỗi lần chạy tối đa 1 CPU, 1024 MiB, 90 giây; 20 MiB/file, 40 MiB tổng file.
- Giữ streaming thật và một file cuối cùng sau phần trả lời; lượt đọc lại không xóa primary đã chọn.
- Không sửa `.env` thật, không xóa dữ liệu hay Docker resource không thuộc phạm vi này.
- Lịch sử cũ giữ nguyên; compatibility ẩn cho checkpoint cũ, không quảng bá tool cũ cho run mới.

## Review Focus

1. Worker chết sau khi container chạy nhưng trước khi publish: không chạy lại code hay tạo phiên bản trùng.
2. Nhiều output rồi lượt chỉ đọc: final card vẫn là output primary được chọn rõ ràng.
3. Input PDF không phải parent vẫn phải được kiểm tra text coverage.
4. Workbook có biểu đồ/công thức nâng cao: preview không làm mất byte của output.
5. Automation không có user trực tiếp: Python chạy không xin duyệt nhưng vẫn kiểm tra file grants.

### Task 1: Hợp đồng Python và kết quả runtime

**Files:** `artifact_runtime/protocol.py`, `artifact_runtime/python_execution.py`, `artifact_runtime/execute.py`, `tests/test_unified_python_runtime.py`.

**Interfaces:** `PythonCodeInput(code, stdin, input_version_ids, outputs, primary_output)`; `PythonOutput(path,name,parent_version_id)`; `GeneratedArtifact` thêm execution report. `execute_python(request,input_dir,output_dir)` trả report và chỉ thu thập output được khai báo.

- [x] Viết test tính toán không file, stdin, lỗi có stderr, output khai báo/primary, từ chối path traversal/symlink và giới hạn log.
- [x] Chạy test: kỳ vọng FAIL vì hợp đồng mới chưa có.
- [x] Cài schema, subprocess cách ly, log hữu hạn và zero-output bundle; giữ nguyên wire shape của operation cũ.
- [x] Chạy `pytest tests/test_unified_python_runtime.py tests/test_artifact_protocol.py`: kỳ vọng PASS (điều chỉnh tên file theo inventory).
- [x] Commit `feat: define general Python execution and output contract`.

### Task 2: Một image và một profile thực thi

**Files:** `infra/docker/python-runtime/`, `artifact_runtime/containers.py`, `compose.yaml`, script build/doctor hiện hành, test broker/runtime.

**Interfaces:** `RUNTIME_IMAGE=personal-agent-python-runtime:1`; broker và executor dùng cùng image với entrypoint riêng. `ArtifactRunRequest` từ Task 1 giữ replay theo job fingerprint.

- [x] Viết test broker dùng một image, 90 giây/1 CPU/1024 MiB, zero-output hợp lệ.
- [x] Chạy test mới: kỳ vọng FAIL.
- [x] Hợp nhất Dockerfile/lockfile, thư viện phổ biến và BLAS/Agg/cache env; đồng bộ timeout ngoài và trong.
- [x] Build image; chạy import tất cả thư viện, tính toán, stdin, Excel nâng cao, DOCX/PDF/PNG tiếng Việt, kiểm tra isolation: kỳ vọng PASS.
- [x] Commit `feat: consolidate isolated Python and document runtime image`.

### Task 3: Scope, receipt, publication và primary

**Files:** `backend/services/artifacts/python.py`, storage/validation/publication, runtime receipt/model/migration nếu cần; test mới `tests/test_unified_python_service.py`.

**Interfaces:** `PythonToolService.execute(context, request: PythonCodeInput) -> ToolResult`; fingerprint bao gồm code/stdin/input hash/output/primary/contract. Lưu receipt độc lập ArtifactVersion, khóa publication theo output index, primary lưu bền.

- [x] Test zero-output replay, nhiều output, đọc lại giữ primary, sai input/parent, PDF không đọc được và resume sau publish dở; chạy và xác nhận FAIL.
- [x] Cài scoped staging, kiểm tra mỗi PDF, receipt/reconcile và immutable publication; text/PNG preview; giữ nguyên output nâng cao và báo calculation trung thực.
- [x] Chạy test service/publication/storage/validation: kỳ vọng PASS.
- [x] Commit `feat: publish scoped Python outputs with durable execution receipts`.

### Task 4: Registry, prompt, automation và compatibility

**Files:** `backend/dependencies_tools.py`, `agent_core/tools/python.py`, routing/config, automation authority/execution, recovery, chat final refs.

**Interfaces:** registry run mới chỉ đăng ký Python mới theo `PYTHON_SANDBOX_ENABLED`; file publication vẫn theo `WORKSPACE_ARTIFACTS_ENABLED`. Tool schema dùng Task 1, service dùng Task 3. Legacy checkpoint được nhận diện riêng.

- [x] Test registry chỉ có một Python tool, không approval, prompt tổng quát và automation file grants, pending approval cũ không treo; chạy xác nhận FAIL.
- [x] Cài wiring, prompt hướng dẫn tạo/đọc/sửa file bằng Python; sửa artifact-ready và persisted primary; giới hạn compatibility cho run cũ.
- [x] Chạy routing/registry/automation/recovery/streaming tests và Node final-card tests: kỳ vọng PASS.
- [x] Commit `refactor: route chat and automations through unified Python tool`.

### Task 5: Di chuyển fixtures, bỏ phần thừa và hướng dẫn vận hành

**Files:** scripts/tests/config/docs đang hoạt động, old Python sandbox và Dockerfile cũ.

**Interfaces:** build/doctor/acceptance chỉ tham chiếu image/tool mới; tài liệu lịch sử không bị viết lại.

- [x] Viết acceptance kiểm tra thư viện thiếu báo lỗi rõ, PDF không cần DOCX companion, không giữ RAM, hỗ trợ file upload/sửa lại và selected primary; chạy xác nhận FAIL.
- [x] Di chuyển fixtures và script; xóa executor/contract cũ sau khi không còn active import; giữ helper parsing/rendering/legacy cần thiết.
- [x] Cập nhật `.env.example`/Compose và hướng dẫn tiếng Việt với lệnh build/start/test, inventory giữ/xóa. Không đổi `.env` thật.
- [x] Chạy toàn bộ pytest, Ruff, Node và acceptance Docker/DB riêng: kỳ vọng PASS; ghi rõ mọi skip và phần cần user nghiệm thu.
- [x] Commit `refactor: retire redundant Python runtimes and document rollout`.

### Task 6: Review toàn nhánh và bàn giao

- [x] Tạo review package gồm diff, spec, plan, evidence và các quyết định.
- [x] Một reviewer độc lập kiểm tra cả nhánh, nhất là năm Review Focus.
- [x] Sửa Critical/Important bằng test RED→GREEN, chạy regression liên quan và full suite; không tự merge/push.
- [x] Xác minh image cũ không còn dependent; chỉ xóa đúng tag dư thuộc phạm vi nếu an toàn, liệt kê tag chưa thể xóa và lý do.
- [x] Bàn giao kết quả thực tế, lệnh user cần chạy và giới hạn còn lại.
