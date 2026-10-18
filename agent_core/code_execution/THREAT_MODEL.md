# Threat model cho `run_python_code`

## Mục tiêu bảo vệ

- Secret, source code, database, filesystem, environment và network identity của host.
- Dữ liệu và tiến trình của user khác.
- Tính sẵn sàng của API, MCP server và worker.
- Tính toàn vẹn của tool result, audit log và bước user approval.

## Giả định và ranh giới tin cậy

- User input, code, stdin, stdout, stderr và file do code tạo ra đều **không tin cậy**.
- LLM có thể bị prompt injection và không phải thành phần kiểm soát bảo mật.
- Backend, Tool Registry, policy và image sandbox đã ký/kiểm soát là thành phần tin cậy.
- Skill không cấp quyền; user approval không biến code thành đáng tin cậy.
- Không nhận secret vào sandbox. Nếu tác vụ cần secret hoặc network thì contract v1 từ chối.

```text
User/LLM (untrusted)
        │ code + stdin
        ▼
Backend validation + approval (trusted control plane)
        │ bounded request, no secret
        ▼
Disposable sandbox (hostile workload, network off)
        │ bounded stdout/stderr
        ▼
Backend observation wrapper (untrusted data) → Agent
```

## Threats và biện pháp bắt buộc

| Threat | Ví dụ | Biện pháp bắt buộc |
|---|---|---|
| Đọc secret/host | `/etc`, `.env`, token cloud | Không mount host; env allowlist rỗng; user không phải root |
| Path traversal | `../../`, symlink | Working directory riêng; resolve path; cấm thoát root |
| Network exfiltration | HTTP, DNS, raw socket | Network namespace không interface; chặn syscall socket |
| Fork bomb/process escape | `fork`, subprocess hàng loạt | PID namespace; `pids_limit=1`; kill toàn bộ sandbox |
| CPU loop | `while True` | CPU quota và wall timeout độc lập |
| RAM bomb | cấp phát list/bytes lớn | Memory cgroup hard limit; tắt swap nếu có thể |
| Disk fill | ghi file vô hạn | tmpfs/ephemeral volume có quota và inode limit |
| Output flood | in vòng lặp vô hạn | Đọc stream có cap; cắt tại 64 KiB; tiếp tục kill đúng hạn |
| Container escape | syscall/kernel exploit | Rootless, drop capabilities, no-new-privileges, seccomp/AppArmor |
| Dependency attack | `pip install`, package độc | Image bất biến; không package install; network tắt |
| Cross-tenant leak | tái sử dụng thư mục/process | Một sandbox và workspace mới cho mỗi call; xóa sau cùng |
| Approval bypass | model tự nói “đã duyệt” | Quyết định gắn user/run/tool-call và do backend xác minh |
| Prompt injection qua output | code in chỉ dẫn giả | Bọc output là untrusted observation; không cấp authorization |
| Log injection/data retention | ANSI, dữ liệu nhạy cảm | Giới hạn, sanitize khi hiển thị, không log toàn bộ output mặc định |

## Trình tự kiểm soát

1. Validate schema và giới hạn byte trước approval.
2. Kiểm tra allowlist, role và approval trong control plane.
3. Tạo sandbox mới với network off và không có host mount/secret.
4. Áp quota trước khi process user bắt đầu.
5. Giám sát timeout/output từ bên ngoài sandbox.
6. Kill toàn bộ sandbox, sau đó mới trả observation.
7. Audit metadata tối thiểu: user, run, thời gian, status, quota hit; không log source/output mặc định.

## Không được dùng làm biện pháp bảo mật chính

- Tìm từ khóa nguy hiểm như `import os`, `eval`, `subprocess`.
- Yêu cầu LLM tự đánh giá code có an toàn hay không.
- Chỉ dùng Python timeout trong cùng process backend.
- Chỉ dùng user approval mà không sandbox.
- Docker mặc định mà không drop capability, quota và tắt network.

## Rủi ro còn lại

- Lỗ hổng kernel/container runtime và side-channel tài nguyên vẫn có thể tồn tại.
- Output hợp lệ có thể chứa nội dung độc hại hoặc dữ liệu user tự đưa vào.
- Tính đúng đắn của kết quả Python không được bảo đảm bởi sandbox.

Tool chỉ được đăng ký sau khi sandbox Ngày 38–40 và test tấn công Ngày 41 đạt.

## Trạng thái triển khai

- Ngày 38 đã có: container dùng một lần, non-root, root filesystem read-only, tmpfs riêng,
  không host mount, network `none`, drop capabilities và `no-new-privileges`.
- Ngày 39 đã có: cgroup CPU/RAM/PID, rlimit CPU/address-space/file/file-descriptor,
  wall timeout độc lập và chặn output theo stream ở cả runner lẫn backend.
- Ngày 40 đã có: stdout/stderr bọc dưới dạng observation không tin cậy; artifact text có
  allowlist định dạng, quota, chống symlink/path traversal và kiểm tra SHA-256 hai lớp.
- Ngày 41 đã đạt: host secret canary không rò rỉ; root/capability/namespace escape, fork
  vượt PID quota và kết nối mạng ngoài đều bị chặn.
- `run_python_code` đã được đăng ký sau khi toàn bộ kiểm tra trên đạt; approval vẫn bắt buộc.
