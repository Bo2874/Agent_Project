# Plugin manifest và catalog — Ngày 80

Định dạng của ứng dụng tại `agent_core/plugins/`, wire `schema_version="1.0"`.
Đây là metadata/policy của host; không phải chuẩn đóng gói chung của MCP.
Validation/catalog không khởi động process, cài package, mở HTTP connection hoặc cấp quyền user.
Luồng demo/MCP/Skill hiện có và feature flag V2 mặc định tắt vẫn giữ nguyên.
Ngày 81 bổ sung [API và persistence theo user](API.md); phần dưới mô tả thư viện metadata Ngày 80.
Ngày 82 bổ sung [PluginManager nhiều stdio server](STDIO_RUNTIME.md) từ profile host được review.
Ngày 83 bổ sung [HTTP adapter, SSRF và encrypted credential/OAuth](HTTP_AND_OAUTH.md).

## Manifest

Ví dụ được kiểm chứng:

- [Demo](../../plugins/manifests/demo.json): 1 stdio server, 6 tool và 1 Skill khai báo.

| Field | Ý nghĩa |
| --- | --- |
| `id` / `namespace` | ID plugin ổn định và namespace do host cấp, duy nhất trong catalog |
| `version` | Exact SemVer của manifest đã review; đổi nội dung phải dùng version mới |
| `policy_version` | Exact SemVer cho cấu hình policy/binding/pin đã duyệt |
| `name`, `description`, `tags` | Metadata; không phải instructions thực thi |
| `max_concurrency` | Trần concurrency toàn plugin; runtime manager sẽ thực thi ở Ngày 82 |
| `servers` | 1–16 server; ID local, transport, release pin, profile reference, credential slots, tools |
| `skills` | 0–64 Skill metadata với version, relative directory và dependency tới tool trong plugin |

Server dùng `transport=stdio|streamable_http`, exact SemVer `release`,
`connection_profile_ref` là logical ID. Manifest không nhận `command`, `args`, `env`, URL,
HTTP headers hoặc secret thô. Host/backend sau này resolve profile do admin quản lý thành
executable/args/cwd hoặc endpoint đã kiểm tra; profile phải khớp artifact/release được duyệt.
Release pin ở metadata chưa chứng minh server thực tế đã được pin hoặc protocol tương thích.

`credentials` chỉ khai báo `slot` và loại `api_key|bearer_token|oauth2`. Không chứa giá trị key,
token, user ID hay secret ID của một user. Connection/credential reference theo user được triển
khai ở Ngày 81. Không dùng một profile reference như một URL/path tùy ý.

`PluginSkill.relative_directory` là đường dẫn portable tương đối với package root. Validator
chặn absolute path, drive, backslash, traversal và segment rỗng. Ngày 85 vẫn phải resolve realpath,
chặn symlink thoát package, kiểm tra tồn tại/version trước khi nạp instructions.
Ngày 80 chưa nạp Skill qua đường dẫn này; cũng chưa hỗ trợ dependency chéo plugin.

## Namespace và remote name

Host quy định component ASCII lowercase, không tự normalize tên gây collision:

| Thành phần | Giới hạn |
| --- | --- |
| Namespace plugin | 1–20 ký tự, bắt đầu chữ, sau đó `a-z0-9_` |
| Server ID | 1–16 ký tự, cùng quy tắc |
| Tool/Skill local name | 1–24 ký tự, cùng quy tắc |
| Component | Không chứa separator `__` |
| Remote MCP tool name | Giữ case, 1–128 ký tự `A-Za-z0-9_.-` |

- Canonical tool capability: `research.tool.github.search`.
- Canonical Skill capability: `demo.skill.multi_step_calculation`.
- Alias tool cho adapter/model: `research__github__search`, tối đa 64 ký tự.
- Remote tool name `search` ở server web thành `research__web__search`, không va chạm.
- `remote_name` giữ đúng tên server công bố, ví dụ `Remote.Search-API_v2`; local name là alias host chọn.
- Không dựa vào tên trong MCP `serverInfo` để cấp namespace. Các ID `CapabilityDescriptor` được sinh
  từ manifest đã validate; full input/output schema chỉ là logical reference, không tải qua mạng.

Tên V1 như `demo__calculate` vẫn giữ nguyên trong registry đang chạy. Canonical ID/alias V2 ở đây
chưa được đăng ký vào ToolRegistry; Ngày 82 cần bridge tương thích, không đăng ký cả hai bộ một cách ngầm định.

## Policy do host sở hữu

`AccessPolicy` mặc định: chỉ admin, `side_effect=unknown`, bắt buộc approval.
`ToolPolicy` thêm timeout, retries/backoff, idempotence và concurrency; mặc định không retry.

- Write/destructive/unknown luôn cần approval. Read-only cũng có thể yêu cầu approval.
- Tự retry chỉ hợp lệ khi host đánh dấu **read-only và idempotent**; tối đa 3 retry.
- Timeout tool không vượt server timeout, concurrency tool ≤ server ≤ plugin.
  Đây là validation cấu hình, chưa có semaphore hoặc enforcement runtime mới.
- Skill không được rộng role hơn tool bắt buộc, bỏ approval của tool, hoặc khai side effect nhẹ hơn
  dependency. Thứ tự bảo thủ: read_only → write → destructive → unknown.
- Không nhập MCP annotation vào policy và không cho payload `annotations` ghi đè policy.
  Metadata server chỉ là thông tin không tin cậy; executor kiểm tra host policy khi thực thi.

Manifest valid chưa phải là gói trusted. Admin review nguồn/package/profile/policy trước khi đăng ký;
backend vẫn cần auth, grant/connection state, allowlist, quota và kiểm tra tại thời điểm gọi tool.
Validation không thể phát hiện mọi secret bị nhét vào description; review/redaction vẫn cần thiết.

## Catalog snapshot và version

```python
from agent_core.plugins import PluginCatalog, load_plugin_manifest

catalog = PluginCatalog()
manifest = load_plugin_manifest("plugins/manifests/demo.json")
registered = catalog.put(manifest)  # disabled
assert registered.capabilities() == ()

enabled = catalog.set_enabled(
    manifest.id, True, expected_catalog_version=registered.catalog_version
)
print(enabled.catalog_version)
```

`enabled` là metadata của catalog host, không phải grant của user và không bật feature flag.
`capabilities()` chỉ trả metadata của entry enabled; backend vẫn phải lọc theo quyền/connection.
`include_disabled=True` chỉ phục vụ kiểm tra/admin, không được coi là shortlist đã cấp quyền.

Snapshot có `revision` và `catalog_version=pc1:<revision>:<SHA-256>`, vừa giới hạn 80 ký tự của
contract routing. Hash tính trên manifest canonical + trạng thái enabled. Revision tăng sau mỗi
thay đổi thật, nên disable/enable trở lại nội dung cũ vẫn có version khác, chặn stale update/ABA.
Sắp xếp declaration, roles, tags và thứ tự đăng ký không làm thay đổi fingerprint có cùng revision.
Đăng ký lại cùng nội dung/trạng thái là no-op.

Writers dùng lock, build/validate toàn snapshot trước khi thay con trỏ. Snapshot cũ bất biến và vẫn
đọc được. Namespace collision, version mismatch hoặc validation lỗi không publish một phần.
`expected_catalog_version` là compare-and-swap; hai writer cùng version chỉ có một writer thắng.

Một `(plugin id, manifest version)` đã đăng ký không được đổi nội dung. Cấu hình policy thay đổi
cần cả manifest version và `policy_version` mới; đổi description đơn thuần chỉ cần manifest version.
Policy fingerprint cũng bao gồm binding/profile, release pin, schema refs, credential slots,
limits và Skill dependencies/path/version. Không tự diễn giải SemVer range hoặc chọn version mới nhất.

JSON snapshot đọc lại sẽ được kiểm tra nested model và checksum. Hash không phải chữ ký hoặc bằng
chứng quyền; dữ liệu phải đến từ storage/host trusted. Release history đang giữ trong memory của
một `PluginCatalog`; restore snapshot chỉ phục hồi history của các release đang có trong snapshot.
Ngày 81 đã bổ sung persistence/uniqueness/transaction/auth version nhiều process qua migration 0023
và service backend; xem [API guide](API.md). API dùng lịch sử release bền vững trong PostgreSQL,
không khôi phục quyền từ snapshot memory.

## Validation, schema và CI

Runtime metadata/index/discovery và schema budget có tại
[Capability catalog](CAPABILITY_CATALOG.md), quyết định ownership ở
[ADR 0006](../adr/0006-capability-discovery-and-schema-budget.md).
Catalog host runtime đã lọc role/allowlist tách biệt catalog manifest ở trên.

Ngày 85 nối Skill từ package host-reviewed vào graph/chat runtime riêng theo user; dependency,
package/version invalidation, checkpoint pin và revoke guard ở [Skill runtime](PLUGIN_SKILLS.md).

Ngày 86 thêm [UI Plugins/Connections](PLUGINS_UI.md): `/plugins` theo owner và `/admin/plugins`
cho manifest/grant. Connector GitHub đọc, web search và Skill research/report được hoãn theo yêu cầu;
không setup hoặc gọi provider live trong mốc UI này.

```powershell
uv run --no-sync python -m scripts.validate_plugin_manifests
uv run --no-sync python -m scripts.validate_plugin_manifests --schema-dir docs/plugins/schemas
uv run --no-sync python -m pytest -q tests/test_plugin_manifest.py tests/test_plugin_catalog.py
```

- Loader đọc tối đa 256 KiB, reject duplicate JSON key, NaN/Infinity, version không hỗ trợ,
  field lạ, namespace/resource/policy sai. Không đọc instructions hay resolve profile/schema.
- CLI xây catalog tạm tất cả entry disabled, in fingerprint/count/version, không khởi tạo runtime.
  Payload bị reject không được in nguyên văn để tránh log secret.
- [Plugin manifest schema](schemas/plugin-manifest.schema.json) và
  [catalog snapshot schema](schemas/catalog-snapshot.schema.json) được sinh trực tiếp từ Pydantic.
  JSON Schema kiểm tra shape; semantic/cross-reference/policy checks vẫn do model validator thực hiện.
- Workflow CI có bước validate manifest trên Windows/Linux; Compose gate kiểm tra manifest được
  đóng gói và validate trong app container mà không activate plugin. Runtime V2 vẫn tắt.

Nguồn thiết kế: [MCP tool names, annotations và collision](https://modelcontextprotocol.io/specification/2026-07-28/server/tools),
[MCP transports](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports),
[SemVer 2.0.0](https://semver.org/). Manifest/namespace/policy ở trên là quyết định của project,
không phải cơ chế policy chung do MCP cung cấp.
