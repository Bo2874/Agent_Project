# Python runtime thống nhất

## Giữ và bỏ những gì?

| Thành phần | Hiện tại |
|---|---|
| Tool cho Agent | **`run_python_code`**: tính toán, phân tích dữ liệu, tạo/đọc/sửa file bằng Python |
| Image cho subsystem này | **`personal-agent-python-runtime:1`** |
| Broker | HTTP service nhận job có chữ ký, tạo container mới và giữ receipt; có thể chạy bằng venv trên Windows hoặc cùng image trong Compose |
| File và preview | Giữ Artifact Store, immutable version, quyền theo user/hội thoại, tải xuống, preview và lịch sử |
| Tool cũ | Không đăng ký `artifact_create`, `artifact_edit`, `artifact_inspect`, `run_artifact_python`, `artifact_rebuild_pdf_text_only` cho run mới |
| Compatibility | Chỉ tải adapter trong run đang resume checkpoint cũ. Python sandbox cũ trả hướng dẫn bắt đầu yêu cầu mới, không tự chạy lại code chưa rõ kết quả |
| Runtime cũ | Đã bỏ executor/contract Python cũ, script gate cũ và ba bộ Dockerfile/lockfile trùng |

Các image ứng dụng web/worker, PostgreSQL, Redis hoặc project khác vẫn có vai trò riêng.
Một image ở đây nói về subsystem thực thi Python và render tài liệu.

## Chạy trên Windows

Mở terminal tại thư mục project. Docker Desktop cần hoạt động.

1. Build image duy nhất:

   ```powershell
   docker build -f infra/docker/python-runtime/Dockerfile -t personal-agent-python-runtime:1 .
   ```

2. Trong `.env`, bổ sung biến chưa có; giữ nguyên các khóa hiện có:

   ```dotenv
   PYTHON_SANDBOX_ENABLED=true
   WORKSPACE_ARTIFACTS_ENABLED=true
   AGENT_REACT_ENABLED=true
   ARTIFACT_RUNNER_URL=http://127.0.0.1:8081
   ARTIFACT_RUNNER_KEY=<khóa riêng tối thiểu 32 ký tự>
   ARTIFACT_RUNNER_STATE_PATH=data/artifact-runner
   ```

   Nếu đã có `ARTIFACT_RUNNER_KEY`, tiếp tục dùng khóa đó. Nếu `TOOL_ALLOWLIST` có cấu hình riêng,
   thêm `run_python_code`. Không cần thêm các tool artifact cũ. `PYTHON_SANDBOX_ENABLED` bật tool;
   `WORKSPACE_ARTIFACTS_ENABLED` bật file input/output và giao diện file. Tắt file vẫn cho phép tính toán.

3. Terminal broker (giữ chạy):

   ```powershell
   .\.venv\Scripts\python.exe -m uvicorn artifact_runtime.app:application_factory --factory --env-file .env --host 127.0.0.1 --port 8081 --no-access-log
   ```

   Broker cần cùng key với web/worker. Broker dùng Docker CLI để mở container, còn container chạy code
   không được mount Docker socket, `.env` hoặc thư mục máy chủ. Đây là HTTP service nên dùng port riêng.

4. Restart web và background worker bằng các lệnh đang dùng. Nếu cần lệnh tương ứng:

   ```powershell
   .\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
   ```

   Terminal khác:

   ```powershell
   .\.venv\Scripts\python.exe -m scripts.run_background_worker
   ```

   PostgreSQL và cấu hình chat/model của project vẫn cần hoạt động như trước. Scheduler chỉ cần khi test Automations.

Không cần cài numpy/openpyxl/LibreOffice vào Windows để Agent chạy Python; chúng đã nằm trong image.
Môi trường dev dùng `uv sync --extra dev` khi chạy kiểm thử Python của repository.

## Chạy bằng Compose

Trong file Docker env của bạn, đặt `DEV_PYTHON_SANDBOX_ENABLED=true`,
`DEV_WORKSPACE_ARTIFACTS_ENABLED=true`, `DEV_AGENT_REACT_ENABLED=true` và cùng
`DEV_ARTIFACT_RUNNER_KEY` tối thiểu 32 ký tự. Sau đó:

```powershell
docker compose --env-file .env.docker --profile artifacts up -d --build
```

Service `artifact-runner` dùng image `personal-agent-python-runtime:1` với command Uvicorn và Docker socket.
Mặc định Docker env tắt Python cùng với profile broker chưa bật. Nếu chỉ dùng Python tính toán và tắt UI file,
vẫn bật `DEV_PYTHON_SANDBOX_ENABLED=true` và chạy profile `artifacts` để có broker;
`DEV_WORKSPACE_ARTIFACTS_ENABLED` có thể tiếp tục là `false`.
Job thực thi dùng chính image đó với command executor, user không phải root, không mạng, không socket.
Web/worker gọi broker qua mạng riêng; broker không publish port ra host trong Compose.

## Agent đọc lại và sửa như thế nào?

1. Agent gọi `run_python_code`, truyền code và các `input_version_ids` cần dùng.
2. Backend xác minh quyền và hash, chỉ stage các input đó tại `/input/input-0.<đuôi>` theo thứ tự khai báo.
3. Container mới chạy code; `/input` chỉ đọc, `/output` là thư mục làm việc.
4. Chỉ các đường dẫn trong `outputs` được xuất ra. File tạm không khai báo không trở thành file tải xuống.
5. Backend kiểm tra, giữ nguyên byte output, lưu version mới và trả ref cùng stdout/stderr/cảnh báo.
6. Agent có thể gọi tiếp bằng ref vừa nhận, mở file bằng openpyxl/python-docx/pypdf… và `print` kết quả kiểm tra.
   Nó có thể sửa và xuất phiên bản khác hoặc kết thúc. Một lượt chỉ đọc không xóa file primary đã chọn.
7. Chat stream câu trả lời trước; sau `done` mới hiện một thẻ của file primary. Có nhiều output phải chọn
   `primary_output`; những file khác vẫn nằm trong thư viện. Preview không đồng nghĩa model đã nhìn thấy ảnh.

Python không giữ biến trong RAM giữa các lần gọi. Muốn dùng lại dữ liệu, lưu file JSON/CSV hoặc file phù hợp rồi
truyền version đó vào lần sau. Không có thao tác chấp nhận chạy Python trong chat hay Automation.
Automation vẫn chỉ được dùng file và các hành động trong grant của revision đã lưu.

## Thư viện có sẵn và giới hạn

- Dữ liệu/toán: numpy, pandas, scipy, sympy, statsmodels, scikit-learn.
- File: openpyxl, XlsxWriter, python-docx, docxtpl, pypdf, pdfplumber, reportlab.
- Biểu đồ/ảnh xuất: matplotlib, seaborn, Pillow.
- Parsing: regex, rapidfuzz, python-dateutil, PyYAML, lxml, beautifulsoup4, Jinja2, defusedxml.
- HTTP/types/service: requests, httpx, pydantic, FastAPI, Uvicorn; requests/httpx không mở quyền mạng.
- Hệ thống: LibreOffice Calc/Writer, Python + UNO cùng phiên bản Debian, Poppler và font DejaVu hỗ trợ tiếng Việt.

Phiên bản và hash nằm trong `infra/docker/python-runtime/requirements.lock`.
Thiếu thư viện trả `ModuleNotFoundError` cho Agent; developer thêm vào `requirements.in`, cập nhật lock và build lại.
Container không chạy pip và không tải package lúc xử lý yêu cầu.

- Mỗi job: 1 CPU, 1024 MiB, tối đa 90 giây; subprocess Python dừng ở 80 giây để trả lỗi trước deadline ngoài.
- Tối đa 3 input và 3 output/lần; 20 MiB/file, 40 MiB tổng mỗi chiều. Hội thoại có thể chọn nhiều file nhưng Agent chia lượt xử lý.
- stdout và stderr giữ tối đa 64 KiB mỗi luồng; có cờ `logs_truncated`. Scratch/tmp có giới hạn riêng.
- Mặc định quota file user 200 MiB, tính cả version và preview. Không xóa version đã lưu trong đợt chuyển đổi này.
- Preview Excel là bảng dữ liệu hữu hạn; công thức/biểu đồ nâng cao giữ trong file. Không tự lưu lại bằng LibreOffice
  chỉ để làm preview. `not_calculated` nghĩa kết quả công thức chưa được hệ thống xác minh, dù file có cached values.
- DOCX/PDF render preview riêng; PDF tạo bằng ReportLab/pypdf không bắt buộc có DOCX companion.
- TXT/MD/CSV/JSON xem dạng text an toàn; PNG Agent tạo dùng endpoint có kiểm tra owner. Không render HTML/SVG.
- PDF: tối đa 20 trang, chưa OCR. Kiểm tra mọi PDF input. Khi bỏ qua phần không đọc được, Agent báo nguồn và trang thiếu;
  user trả lời đúng **“Đồng ý chỉ xử lý văn bản”** trong chat, rồi tool ghim version/hash/pages. PDF scan không có text bị từ chối.
- Macro, executable embed, liên kết thực thi nguy hiểm, archive lỗi/path traversal/symlink bị từ chối; giới hạn tính năng preview
  không phải whitelist các thao tác Excel như trước.

## Tự kiểm tra

```powershell
.\.venv\Scripts\python.exe -m scripts.check_python_runtime
```

Lệnh này dùng Docker thực và fixture cục bộ, không gọi model trả phí hoặc database ứng dụng. Gate workflow đầy đủ cần database
QA riêng đã migrate, với `ARTIFACT_TEST_DATABASE_URL` có tên database kết thúc bằng `_test`.

Thử trong chat: “Tính trung vị các số 1, 5, 9 bằng Python”; “Tạo Excel có biểu đồ tròn”; “Đọc lại file vừa tạo,
kiểm tra công thức”; “Sửa file đã chọn”; “Tạo DOCX có header”; “Tạo PDF tiếng Việt”; “Vẽ biểu đồ PNG”.
Kiểm tra file chỉ hiện khi câu trả lời kết thúc và panel chỉ mở khi bạn bấm vào.

## Receipt và dọn dữ liệu

Receipt lưu riêng trong `agent_operations`, kể cả chạy không tạo file. Fingerprint ghim code/stdin/input hash/output/primary/contract.
Broker dùng job ID cố định để replay kết quả đã xong; job bị gián đoạn hoặc kết quả không rõ không tự chạy lại.
Publication có khóa riêng cho từng output; phục hồi sau gián đoạn không tạo version trùng. File staging của receipt chưa hoàn tất
được bảo vệ khỏi orphan sweep, kể cả khi database lỗi lúc publish. Khi run hoàn tất hoặc bị hủy, staging chưa publish
có thể được dọn sau thời gian chờ mặc định 24 giờ. Run lỗi giữ staging thêm tối đa 7 ngày kể từ lúc lỗi để có thể resume;
sau đó cần yêu cầu mới nếu file chưa được publish đã bị dọn. File/version đã publish luôn được giữ.
Run cũ và file/version/hash/history/download không bị viết lại.

Các tag cũ thuộc subsystem: `personal-agent-python-sandbox:3.11-v1`, `personal-agent-artifact-code:1`,
`personal-agent-artifact-render:1`, `personal-agent-artifact-runner:dev`. Chỉ gỡ tag sau khi kiểm tra không có container
phụ thuộc và đã chuyển broker/worker; không dùng `docker system prune`.
