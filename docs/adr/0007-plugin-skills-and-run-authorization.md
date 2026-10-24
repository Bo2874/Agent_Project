# ADR 0007: Plugin Skill và authority pin trong graph

- Status: accepted, 2026-10-19 (Ngày 85; bắt đầu 2026-10-18).

## Bối cảnh

Manifest đã mô tả Skill và dependency nhưng runtime chỉ dùng builtin Skill. Plugin grant/reference
chỉ được kiểm tra ở control API/private HTTP factory. Nếu nối vào graph chung mà thiếu guard, cache,
retry, queue hoặc checkpoint approval có thể sử dụng quyền/version đã bị thay thế.

## Quyết định

Host cấu hình root package, exact manifest fingerprint và instruction hash; API/chat không có quyền
chọn đường dẫn hoặc cung cấp trusted instructions. Core package source validate path/size/content/
metadata, tạo alias theo namespace và giữ lazy instruction loading. Core SkillRegistry giữ loader/
validator; backend chịu trách nhiệm scope theo DB user/grant/connection.

Mỗi chat run/resume có runtime riêng. Không publish private credential hoặc Skill cache vào global
registry. Stdio chỉ tái sử dụng host binding đúng whole-manifest pin; HTTP có owner manager riêng.
Allowlist host, role và grant cùng áp dụng. Feature off giữ nguyên V1 và không phát sinh DB/transport.

Registry gắn owner/authority guard, executor kiểm tra trước cache/retry và sau response, adapter
kiểm tra lại sau queue locks trước RPC. Graph kiểm tra selected capabilities/Skill trước model và
model retry. Skill approval chỉ làm policy chặt hơn. Authorization failures không retry/replay.

Pin dùng User/plugin/connection/grant generation và content/reference fingerprints; fresh DB read
ở mỗi boundary nên thay đổi từ process khác vẫn được phát hiện mà chưa cần Redis/pub/sub.
Checkpoint lưu authority/schema/Skill digest; mismatch khi resume phải tạo run mới.

Host profile fingerprint nằm trong pin/checkpoint; HTTP kiểm tra lại profile trước dispatch, stdio
pin profile đã được host khởi chạy. Không để pending call resume sang endpoint/process profile mới.

## Hệ quả

- Thu hồi chặn call mới, cache hit, retry, call đang chờ, model tiếp theo và resume generation cũ.
  Kết quả RPC in flight bị giữ lại nếu post-call check thấy quyền đã thay đổi.
- Không giữ DB lock suốt remote execution. Không thể rollback RPC đã gửi hoặc thu hồi output đã
  stream; không có guarantee transaction phân tán, poller cancel hay automatic compensation.
- Có thêm DB reads/content hash checks và chi phí private HTTP startup mỗi run; chưa có SLO/load
  benchmark hoặc persistent private connection pool. Private concurrency limit là per-run.
- Không thêm migration/dependency; default package catalog rỗng và master/plugins vẫn false.
- Control-plane `runtime_connected` và host metadata API không phải live inventory của private run.
  UI/connector live của Ngày 86 và hardening/eval Ngày 87 chưa được nghiệm thu bởi ADR này.

Chi tiết: [Skill runtime guide](../plugins/PLUGIN_SKILLS.md),
[bằng chứng Ngày 85](../../reports/checks/2026-10-18-day85/VALIDATION.md).
