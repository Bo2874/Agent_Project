# PluginManager và stdio runtime — Ngày 82

`PluginManager` quản lý nhiều stdio server độc lập trong cùng process ứng dụng.
Demo mặc định đọc cấu hình từ `plugins/stdio/servers.json`, giữ sáu alias `demo__*` hiện có.
Không cần sửa graph khi thêm server vào cấu hình host đã review.
Ngày 83 manager hỗ trợ thêm [HTTP MCP và OAuth theo owner](HTTP_AND_OAUTH.md).

## Cấu hình host

`MCP_STDIO_CONFIG_PATH` chọn file JSON; mặc định `plugins/stdio/servers.json`.
Compose dùng `DEV_MCP_STDIO_CONFIG_PATH` tương ứng. File cần tồn tại trong image hoặc được mount
read-only vào container; đường dẫn host không tự xuất hiện trong container. Restart để nạp lại.

```json
{
  "schema_version": "1.0",
  "profiles": [{
    "id": "builtin.demo.stdio",
    "release": "0.1.0",
    "command": "@python",
    "args": ["-m", "agent_core.mcp.demo_server"],
    "cwd": "@project",
    "startup_timeout_seconds": 30,
    "health_interval_seconds": 30,
    "health_timeout_seconds": 5,
    "shutdown_timeout_seconds": 15,
    "failure_threshold": 3,
    "restart_cooldown_seconds": 10
  }],
  "servers": [{
    "manifest": "plugins/manifests/demo.json",
    "server_id": "main",
    "enabled": true,
    "legacy_aliases": true
  }]
}
```

`@python` resolve thành interpreter đang chạy;
`@project` là project root. Executable/cwd khác phải là absolute path đã được operator review.
Không nhận command từ chat, DB manifest hoặc Plugin API. Không nhận field `env`/raw credential;
bản này chỉ hỗ trợ server stdio không có credential slot. Child process dùng bộ biến môi trường
mặc định của SDK, không truyền toàn bộ environment ứng dụng.

Để thêm server:

1. Đặt manifest đã review trong `plugins/manifests/`; dùng namespace/server ID không trùng.
2. Thêm profile có `id` khớp `connection_profile_ref`, `release` khớp manifest pin và executable/args
   thực tế. Nhiều server có thể dùng cùng profile nếu cùng artifact/version.
3. Thêm entry trỏ tới manifest và `server_id`, đặt `enabled=true`. `legacy_aliases` mặc định false,
   chỉ dành cho bundled demo khi bật. Alias mới theo `namespace__server__tool`.
4. Bật cả `V2_ENABLED=true`, `V2_PLUGINS_ENABLED=true` cho entry dùng alias canonical;
   Compose dùng `DEV_V2_ENABLED`/`DEV_V2_PLUGINS_ENABLED`. Thêm các alias được phép vào `TOOL_ALLOWLIST`
   của process. Allowlist/role/approval vẫn được executor kiểm tra như trước.
5. Restart, xem health và thử tool. `MCP_DEMO_ENABLED=false` chỉ tắt entry demo compatibility.

File config tối đa 256 KiB, 64 profile và 64 entry; reject field lạ/JSON key trùng, duplicate identity,
profile/pin không khớp, manifest ngoài thư mục cho phép và HTTP/credential slot chưa hỗ trợ.
Toàn bộ config được validate trước khi launch. Config sai làm startup fail fast; một executable
không kết nối được chỉ làm entry đó `circuit_open`, các server khác vẫn được khởi động.

## Lifecycle, timeout và health

Mỗi server có một task sở hữu kết nối SDK từ lúc enter tới exit, để cleanup AnyIO đúng task.
Startup các server chạy song song; registry chỉ publish toàn bộ batch tool của một server sau
khi discovery/validation xong. Nếu schema/tên/collision sai, không publish nửa batch.
Discovery giới hạn mặc định 32 page/1024 tool, chặn cursor lặp và remote tool name trùng.
Chỉ tool đã khai báo trong manifest được đăng ký; remote tool phát sinh khác bị bỏ qua.

| Giới hạn | Mặc định / nguồn |
| --- | --- |
| Connect, initialize và discovery | Profile `startup_timeout_seconds=30` |
| RPC và thời gian chờ slot/lock | Manifest server `request_timeout_seconds=30` |
| Tool execution | Tool policy; demo compatibility giữ các biến `TOOL_*` cũ |
| Health interval / timeout | Profile 30 giây / 5 giây |
| Circuit threshold / restart cooldown | Profile 3 lỗi / 10 giây |
| Shutdown | Profile 15 giây; thêm tối đa 8 giây chờ SDK terminate sau cancellation |

Health dùng `tools/list` không lấy cache; server đang bận được bỏ qua lượt kiểm tra, không đánh lỗi.
Không dùng ping làm giả định chung cho mọi protocol revision. Call trên mỗi stdio server được
tuần tự hóa; semaphore chung giữ trần concurrency của plugin qua nhiều server.
Timeout khi chờ semaphore chưa gửi RPC không tính là lỗi transport của server.

Trạng thái: `stopped → starting → healthy`; lỗi có thể chuyển `degraded → circuit_open`.
Health thành công reset failure counter. Startup thất bại mở circuit ngay; đủ ngưỡng lỗi runtime
sẽ gỡ tool của server đó khỏi registry và đóng kết nối. Thông tin health chỉ có tên, trạng thái,
tool name, failure count, mã lỗi, protocol và thời điểm kiểm tra; không có command/args/secret.

Admin GET `/api/admin/mcp-runtime` để xem trạng thái process app đang phục vụ request.
Anonymous trả 401, user trả 403. Readiness của app vẫn kiểm tra dependency nền tảng; MCP lỗi
được phản ánh là degraded qua endpoint này để các chức năng khác tiếp tục hoạt động.

`PluginManager.stop(name)`, `restart(name)` và `close()` là API nội bộ cho host; chưa có HTTP
endpoint restart process. Restart circuit cần qua cooldown; manager không tự reconnect hoặc replay
tool call. Khi vận hành có thể restart app sau khi sửa config. Shutdown gỡ đúng registration mà
connection đó sở hữu, đóng child process và thu hồi owner task; handler của generation cũ bị chặn.
Deadline startup có thể cộng thêm thời gian SDK dọn process, không phải hard kill tức thời.

## Phạm vi quyền

Server trong file host là cài đặt dùng chung do operator chủ động cấu hình. Canonical binding lấy
role/approval/retry từ manifest host; annotation remote không nới policy. Demo compatibility giữ
cách xử lý annotation V1 và alias cũ, chỉ với tool được manifest demo khai báo.
`serverInfo.version` phải khớp pin; đó là kiểm tra version server tự công bố, chưa phải xác minh hash binary.

Các bảng connection/grant theo user ở Ngày 81 vẫn là control metadata, `runtime_connected=false`.
Manager không đọc credential reference, không tự đưa connection của user vào registry toàn cục.
Nối registry theo owner, credential resolver, quyền trước mỗi call và revoke đang chạy tiếp tục
ở Ngày 83–85. HTTP adapter/encrypted credential đã có ở Ngày 83; graph/Skill bridge và UI vẫn triển khai sau.

## Kiểm thử

```powershell
python -m pytest -q tests/test_mcp.py tests/test_plugin_manager.py
python -m scripts.check_plugin_manager
python -m scripts.ci_compose_gate --output-dir reports/ci/compose
```

Probe tạo hai stdio server synthetic cùng remote name, thêm một process exit và một process treo
khi startup; kiểm tra timeout/crash isolation, host policy, restart, stale handler và cleanup.
Không dùng DB hay provider key. Compose chạy lại probe dưới UID 10001/root read-only, rồi các gate
PostgreSQL/API hiện có. Bằng chứng tại [Ngày 82](../../reports/checks/2026-10-18-day82/VALIDATION.md).

Quyết định: [ADR 0004](../adr/0004-stdio-plugin-manager.md).
Nguồn đối chiếu: [MCP stdio transport và shutdown](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/stdio).
Lifecycle được kiểm tra với SDK trong `uv.lock`; compatibility cho HTTP/protocol khác thuộc Ngày 83.
