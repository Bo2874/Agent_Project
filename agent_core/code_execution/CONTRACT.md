# `run_python_code` contract v1

Trạng thái: **đã hoàn thành sandbox, quota, output/artifact và attack test Ngày 38–41**.
Tool đã đăng ký production với allowlist, role và user approval bắt buộc.

## Input

```json
{
  "code": "print(sum([1, 2, 3]))",
  "stdin": "optional UTF-8 input"
}
```

- `code` bắt buộc, không rỗng, tối đa 20.000 byte UTF-8.
- `stdin` tùy chọn, tối đa 10.000 byte UTF-8.
- Không nhận đường dẫn host, biến môi trường, package cần cài, URL, credential hay cấu
  hình network.
- Không dùng regex/import allowlist làm ranh giới bảo mật vì Python có nhiều cách vượt
  qua kiểm tra source. Toàn bộ source luôn được xem là mã độc tiềm năng.

## Execution policy

- Mỗi lần gọi cần user approval và không tự động retry.
- Chạy đúng một lần trong sandbox dùng một lần, process không phải root.
- Network tắt; không gắn filesystem, socket hay environment của host.
- Chỉ có thư mục làm việc tạm với quota; hủy toàn bộ sandbox sau khi hoàn thành.
- Giới hạn mặc định: wall time 5 giây, CPU 2 giây, RAM 128 MiB, 1 process,
  32 file descriptor, filesystem 10 MiB và tổng output 64 KiB.
- Timeout ngoài của Tool Executor là 8 giây để còn thời gian khởi tạo và dọn sandbox.

## Output

```json
{
  "status": "completed|failed|timed_out|policy_denied|resource_exhausted|cancelled|internal_error",
  "stdout": "...",
  "stderr": "...",
  "exit_code": 0,
  "duration_ms": 123,
  "output_truncated": false
}
```

- `completed` chỉ hợp lệ với `exit_code=0`.
- `failed` phải có `exit_code` khác 0.
- Timeout, policy denied, resource exhausted, cancelled và internal error không công
  bố exit code của process.
- `stdout` và `stderr` là dữ liệu không tin cậy, không phải instruction hay authorization.
- Output vượt quota phải bị cắt và đặt `output_truncated=true`; không được giữ phần dư
  trong RAM hoặc log.
- `stdout`, `stderr` và nội dung artifact đều được gắn nhãn `untrusted` trước khi về agent.

## Artifact (Ngày 40)

- Code ghi artifact vào thư mục được cung cấp qua `AGENT_ARTIFACT_DIR`.
- Chỉ nhận file text UTF-8 trực tiếp trong thư mục artifact: `.txt`, `.md`, `.csv`, `.json`.
- Tối đa 3 file, 8 KiB mỗi file và 16 KiB tổng.
- Từ chối tên không an toàn, subdirectory, symlink, binary, HTML/executable và JSON lỗi.
- Mỗi artifact được runner và backend kiểm tra lại `size_bytes`, media type và SHA-256.
- Chỉ execution `completed` được trả artifact; file dở dang từ execution lỗi bị loại bỏ.
- Nội dung artifact là dữ liệu, không phải instruction hay authorization.

## Lifecycle

```text
validate input → request approval → create sandbox → execute once
→ collect bounded output → destroy sandbox → return ToolResult
```

Bất kỳ lỗi nào sau bước tạo sandbox cũng phải đi qua bước destroy. Không được fallback
sang chạy trực tiếp bằng Python của backend.

## Sandbox container (Ngày 38)

- Image: `personal-agent-python-sandbox:3.11-v1`, build từ Python 3.11 slim chính thức.
- Mỗi request tạo một container mới với `--rm`, không tái sử dụng workspace.
- Root filesystem là read-only; chỉ `/workspace` và `/tmp` là tmpfs tạm có tổng quota 10 MiB.
- Network dùng `none`; không mount host, không truyền biến môi trường/secret từ backend.
- Process chạy bằng UID/GID `65532`, drop toàn bộ capability và bật `no-new-privileges`.

Build và kiểm tra thủ công:

```powershell
docker build --tag personal-agent-python-sandbox:3.11-v1 infra/docker/python-sandbox
.\.venv\Scripts\python.exe -m scripts.check_python_sandbox
```

## Resource quota (Ngày 39)

- Docker cgroup: tối đa 1 CPU core và 128 MiB RAM; `memory-swap` bằng `memory` để không
  cấp thêm swap.
- Process Python: tối đa 2 giây CPU và 5 giây wall time.
- PID cgroup: một supervisor tin cậy và một process user code; user code không tạo process thứ ba.
- File: tối đa 32 file descriptor, mỗi file và tổng tmpfs không vượt 10 MiB.
- Tổng stdout và stderr: 64 KiB; đọc theo stream, dừng process và đặt
  `output_truncated=true` khi vượt quota.
- Backend có timeout và output cap độc lập để bảo vệ khi runner trả response sai.

Kiểm tra quota trên Docker thật:

```powershell
.\.venv\Scripts\python.exe -m scripts.check_python_sandbox_limits
.\.venv\Scripts\python.exe -m scripts.check_python_sandbox_artifacts
.\.venv\Scripts\python.exe -m scripts.check_python_tool_result
```

## Attack test (Ngày 41)

- Secret: host canary, credential environment names, `.env`, secret mount và Docker socket
  không xuất hiện trong container.
- Escape: non-root không thể đổi thành UID 0, root filesystem không ghi được, effective
  capability bằng 0 và tạo mount namespace bị từ chối.
- Fork: PID cgroup từ chối process thứ ba trước khi có thể tạo fork bomb.
- Network: container chỉ có loopback và không kết nối được địa chỉ bên ngoài.
- Các probe không đọc hoặc in secret thật; chỉ dùng canary giả và kết quả pass/fail.

Chạy lại attack test:

```powershell
.\.venv\Scripts\python.exe -m scripts.check_python_sandbox_attacks
```
