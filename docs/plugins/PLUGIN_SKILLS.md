# Skill plugin và quyền trong run

Ngày 85 nối package Skill vào graph/chat/SSE khi cả `V2_ENABLED` và `V2_PLUGINS_ENABLED` bật.
Mặc định vẫn tắt. Không thêm dependency hoặc migration; head giữ `20261018_0024`.

## Package do host duyệt

Đặt package trong `plugins/packages/<relative_root>/`, rồi khai báo `plugins/packages.json`:

```json
{
  "schema_version": "1.0",
  "packages": [{
    "plugin_id": "example.research",
    "manifest_fingerprint": "<64 lowercase hex characters>",
    "relative_root": "research_v1",
    "instruction_hashes": {"brief": "<SHA-256 of instructions.md bytes>"}
  }]
}
```

Đây là ví dụ shape; thay placeholder bằng digest thật trước khi dùng. File mặc định có `packages: []`.
`PLUGIN_PACKAGES_CONFIG_PATH` chọn file host khác; Compose dùng `DEV_PLUGIN_PACKAGES_CONFIG_PATH`.
Không nhận path/instructions qua chat hoặc API đăng ký plugin, không tự cài/download package.

Mỗi `relative_directory` của Skill trong manifest phải chứa `skill.json` và `instructions.md` theo
format Skill hiện có. Metadata name/version/description/tags và required tools phải khớp manifest.
`required_tools` trong package dùng alias canonical `namespace__server__tool`; không tự suy ra tool
từ văn bản instructions. Description/tags phải đồng thời hợp lệ theo format Skill hiện tại.

Package chỉ nằm trong root đã review; lexical traversal, realpath escape và symlink bị reject.
File đọc bounded, regular-file check; instructions phải UTF-8, đúng SHA-256 và giới hạn Skill.
Fingerprint manifest chốt toàn bộ release/policy; hash instructions chốt bytes cụ thể. Không dùng
hash như bằng chứng quyền: admin vẫn phải cấp grant, user vẫn phải bật connection.

## Dependency và progressive disclosure

Runtime đổi tên Skill thành `namespace__skill__name`, tránh trùng builtin/Skill plugin khác.
Core `SkillRegistry` có loader/validator cho package được review; không ghép instructions vào
registry builtin dùng chung. Mỗi run có Skill registry riêng, không trộn cache giữa user.

- Grant Skill phải kèm toàn bộ capability ID của required tools; control API trả 422 nếu thiếu.
- Required tools phải đang có runtime, nằm trong allowlist host, được role và grant cho phép.
  Thiếu dependency vì server lỗi, pin sai hoặc tool bị chặn thì không advertise Skill đó trong run.
- Discovery đọc metadata của Skill được grant. Chỉ Skill được chọn mới đọc instructions, giữ
  behavior lazy-loading và schema token budget của Ngày 84.
- Full instruction cache không bỏ qua kiểm tra: selected Skill được kiểm tra lại package hash,
  manifest, quyền và registration dependencies trước model call, model retry và tool dispatch.
- `requires_approval` của Skill làm tool calls trong workflow phải được phê duyệt; không giảm
  policy tool. Required tools vượt schema budget vẫn fail cả nhóm trước model call.

## Runtime riêng theo user/run

Chat/SSE và resume dùng `plugin_runtime(...)`. Feature off trả nguyên V1 registry/Skill/context,
không truy vấn DB plugin hay khởi tạo transport. Legacy demo alias V1 tiếp tục theo policy cũ.

Khi bật, runtime fork host registry nhưng loại canonical plugin bindings chưa được authorize.
Đọc connection/grant/plugin/role hiện tại từ DB và giữ nguyên allowlist host; grant không vượt
`TOOL_ALLOWLIST`. Tối đa 32 enabled connections và 64 private server mỗi run.

- **Stdio:** chỉ tái sử dụng server host đang chạy, credential-free, descriptor và fingerprint
  toàn bộ manifest khớp DB release. Đổi host manifest/profile cần refresh/restart theo vận hành cũ;
  DB manifest không được launch command/cwd tùy ý. Runtime giữ upstream registration identity.
- **HTTP:** tạo manager/connection riêng cho run; credential resolver chốt owner/grant/reference và
  dùng SSRF/TLS guard của Ngày 83. Hỗ trợ single credential slot đã có hoặc profile không credential;
  chưa có Vault resolver. Không đưa token hay private handler vào registry chung.
- Startup lỗi từng server vẫn bị cô lập; Skill phụ thuộc tool thiếu sẽ không được chọn.
  Context manager đóng private manager khi hoàn tất, lỗi hoặc hủy SSE. Stdio shared không bị đóng
  theo run của một user. Concurrency limit của private manager là theo run, chưa phải quota tổng
  nhiều process/user; chưa benchmark chi phí startup cho workload lớn.

Control API vẫn trả `runtime_connected=false`: nó mô tả connection metadata, không quản lý một
phiên MCP bền vững. Ngày 85 có transport tạm trong run; chưa có UI/live session inventory.
`GET /api/capabilities` hiện vẫn là scope host runtime của Ngày 84, chưa liệt kê private Skill view;
graph/selector dùng registry riêng đã authorize. [UI Plugins/Connections](PLUGINS_UI.md) có từ
Ngày 86; connector GitHub/web search và Skill mẫu research/report được hoãn theo yêu cầu.

## Thu hồi, refresh và checkpoint

Authority pin gồm owner, role/user updated_at, manifest fingerprint, plugin/connection/grant
generation, capability scope và active credential-reference identities. Mỗi boundary đọc DB mới,
không dựa vào cached JWT role hay global auth counter của user khác. DB/feature check lỗi thì deny.

Host profile fingerprint cũng được chốt vào runtime/checkpoint. HTTP guard đọc lại reviewed profile
trước dispatch và chặn profile thay đổi, kể cả server không có credential; stdio profile đổi có hiệu
lực khi host restart/refresh registration. Không resume pending call sang profile host mới âm thầm.

1. Trước model và mỗi model retry: kiểm tra toàn bộ tool definitions đã chọn và selected Skill.
2. Trước validate/cache hit/retry tool: registry giữ owner guard độc lập với callback trong context.
3. Sau khi chờ plugin semaphore/call lock, ngay trước RPC: kiểm tra lại để revoke không lọt qua queue.
4. Sau RPC, trước observation/cache: kiểm tra lại; nếu quyền đổi thì bỏ kết quả của call đang chạy.

Grant revoke/regrant, plugin disable/re-enable, đổi role/user generation, connection disable, rotate
reference, đổi manifest/package hoặc discovery thay registration làm run cũ fail với code rõ ràng.
Không tự retry authorization errors, không tự replan hoặc replay tool ghi; bắt đầu run mới sau khi
cấu hình/consent được cập nhật. Thay file package cùng version nhưng chưa repin hash cũng bị reject.

Checkpoint lưu digest `plugin_runtime_version` gồm authority view, selected schemas và selected
Skill content/policy. Resume so digest với runtime hiện tại trước pending tool/approval/model; lệch
trả `plugin_runtime_changed`. Checkpoint V1 không có field vẫn resume theo V1; chuyển runtime có
plugin vào checkpoint cũ sẽ yêu cầu run mới. Không thêm bảng checkpoint mới.

Boundary là thời điểm đọc quyền đã commit, không phải transaction phân tán với MCP server. Revoke
không thể hoàn tác side effect RPC đã gửi hoặc thu hồi token/text đã stream trước đó. Kết quả call
đang chạy có thể bị giữ lại, nhưng server vẫn có thể đã thực thi; không tự replay call đó.
Không có poller cưỡng bức cancel một request LLM/MCP đang chạy hoặc distributed pub/sub.

## Kiểm tra

```powershell
python -m pytest -q tests/test_plugin_skills.py
python -m scripts.ci_compose_gate --output-dir reports/ci/compose
```

Compose chạy `scripts.check_plugin_skills` với PostgreSQL thật, cookie auth thật, stdio process thật
và model fixture, kiểm tra graph/chat/SSE, dependency, owner isolation, revoke, regrant/resume,
package tamper và release refresh. Credential probe thêm private HTTP run bridge qua TLS fixture.
Không chạy hai DB mutation probe này trên DB host/production, không gọi model/provider thật.
