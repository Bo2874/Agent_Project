# HTTP MCP, credential và OAuth — Ngày 83

`MCPHTTPAdapter` dùng Streamable HTTP của SDK đã khóa trong `uv.lock`, cùng policy/registry batch
và lifecycle của PluginManager. HTTP profile được operator review; endpoint không lấy từ chat hay
request body của user. Cấu hình mặc định `plugins/http/servers.json` rỗng, không kết nối mạng mới.

## HTTP profile

`MCP_HTTP_CONFIG_PATH` chọn file cấu hình; Compose dùng `DEV_MCP_HTTP_CONFIG_PATH`.
Profile chỉ chứa metadata/endpoint, không chứa token hay client secret:

```json
{
  "schema_version": "1.0",
  "profiles": [{
    "id": "research.remote.http",
    "release": "1.0.0",
    "endpoint": "https://mcp.example.com/mcp",
    "protocol_mode": "auto",
    "allowed_protocol_versions": ["2025-11-25", "2026-07-28"],
    "credential_header": "authorization",
    "oauth": {
      "issuer": "https://identity.example.com",
      "authorization_endpoint": "https://identity.example.com/authorize",
      "token_endpoint": "https://identity.example.com/token",
      "revocation_endpoint": "https://identity.example.com/revoke",
      "client_id": "preregistered-public-client-id",
      "redirect_uri": "https://agent.example.com/api/plugins/oauth/callback",
      "scopes": ["tools:read"],
      "require_response_issuer": true
    }
  }],
  "servers": []
}
```

Các domain/version trên là ví dụ, không phải connector đã cấu hình. Manifest admin đăng ký phải
có server `transport=streamable_http`, `connection_profile_ref` trùng ID profile và release khớp pin.
Một HTTP server hỗ trợ một credential slot `api_key|bearer_token|oauth2` trong luồng riêng theo user.
API key dùng profile `credential_header=x-api-key`; bearer/OAuth dùng Authorization Bearer.
Profile PAT/API key bỏ phần `oauth`.

HTTP public không có credential có thể thêm vào `servers` như stdio: `manifest`, `server_id`,
`enabled=true`; cần cả V2/plugins flag và tool allowlist. Profile có OAuth hoặc manifest có credential
không được publish vào registry host dùng chung. Cần restart sau khi thay file host profile.

## SSRF và transport

- Chỉ HTTPS và URL được host duyệt chính xác; không nhận userinfo, query, fragment, backslash,
  control character hoặc port không hợp lệ. MCP, token và revocation dùng các URL đã cấu hình riêng.
- Tắt proxy/environment và redirect. Kể cả redirect cùng origin cũng không tự đi theo.
- Mỗi TCP connection resolve DNS với deadline, kiểm tra **mọi** câu trả lời, rồi dial IP numeric đã
  kiểm tra. Chặn private/loopback/link-local/metadata/CGNAT/multicast và các IPv6 transition phổ biến.
  Không resolve lại hostname khi dial, tránh khoảng hở DNS rebinding giữa kiểm tra và kết nối.
- HTTP Host và TLS SNI vẫn là hostname gốc, TLS certificate verification bật. Không có tùy chọn
  `verify=false` hay allow-private trong file profile. Test harness chỉ inject đúng loopback IP/CA của fixture.
- Không retry HTTP ở lớp network. Tool retry vẫn theo host policy; không tự replay call sau 401 hoặc refresh.
- Từ chối credential/endpoint ở local transport được chuyển thành response HTTP lỗi nội bộ để SDK
  kết thúc request có kiểm soát; không gửi request ra mạng hoặc ném lỗi quyền vào SDK task group.

Guard này là lớp ứng dụng; egress firewall/proxy do hạ tầng kiểm soát vẫn phù hợp khi deploy.
Chưa bật HTTP/2, SSE resumability nâng cao hoặc URL tự discovery từ challenge không tin cậy.

## Storage mã hóa

Migration head `20261018_0024` thêm `plugin_secrets` và `plugin_oauth_states`, với composite FK owner
tới connection. Token/refresh token/PKCE verifier chỉ nằm trong ciphertext Fernet; state lưu SHA-256
để lookup. Envelope còn ràng buộc record ID, owner/connection/plugin/server/slot/version và profile
để phát hiện ciphertext bị gắn sang binding khác.

Cấu hình `MCP_CREDENTIAL_ENCRYPTION_KEYS` bằng Fernet key ring, khóa mới nhất đứng đầu, tối đa 3 khóa.
Compose dùng `DEV_MCP_CREDENTIAL_ENCRYPTION_KEYS` từ cấu hình private. Không có khóa mặc định hoặc
fallback sang JWT/auth secret. Thiếu khóa trả 503 ở thao tác cần mã hóa, không cản startup V2 đang tắt.
Giữ key ngoài Git/image; backup database cần giữ key riêng để có thể giải mã sau restore.

Key ring đọc được ciphertext cũ và dùng khóa đầu cho write mới. Chưa có CLI rewrap toàn database;
không bỏ khóa cũ khi vẫn còn record mã hóa bằng khóa đó. Provider Vault bên ngoài chưa có resolver.

Response/audit chỉ có credential reference ID của ứng dụng và version context; không trả token,
refresh token, PKCE verifier hoặc secret-store ID. Secret value đầu vào dùng `SecretStr`, giới hạn
16 KiB và chặn ký tự gây header injection. OAuth code/state trong URL callback được redact khỏi log,
kể cả tên query parameter được percent-encode.

## API theo owner

Mọi endpoint dưới đây cần đăng nhập, cả master/plugins flag và owner đúng connection.
Quyền thao tác còn phụ thuộc grant hiện tại, role, manifest version và capability của server.

| Method / path | Body / kết quả |
| --- | --- |
| POST `/api/plugin-connections/{id}/credentials` | `server_id`, `slot`, `value`, `expected_auth_version`; lưu/rotate API key hoặc bearer token |
| POST `/api/plugin-connections/{id}/oauth/begin` | `server_id`, `slot`, `expected_auth_version`; trả authorization URL và TTL 600 giây |
| GET `/api/plugins/oauth/callback` | Provider redirect `code`, `state`, `iss`; lấy connection từ state/owner, trả reference metadata |
| POST `/api/plugins/oauth/complete` | Browser callback gửi `state`, `code`, `issuer` bằng body; dùng cùng owner-bound state checks |
| POST `/api/plugin-connections/{id}/oauth/complete` | `state`, `code`, `issuer`; thay callback GET cho frontend/client riêng |
| POST `/api/plugin-connections/{id}/credentials/{ref_id}/revoke` | `expected_auth_version`; local revoke rồi provider revoke nếu được cấu hình |

Luồng thực tế:

UI Ngày 86 ở `/plugins`; redirect HTML dùng `/plugins/oauth/callback`, khác callback API trả JSON
ở bảng trên. Đăng ký đúng URI này trong reviewed profile/provider khi muốn quay về UI.
JS xóa query khỏi URL trước POST exchange; xem [UI guide](PLUGINS_UI.md).

1. Operator cấu hình profile, encryption key và client ID đã đăng ký với provider.
   Admin đăng ký manifest, bật plugin và cấp grant; user tạo connection theo [API guide](API.md).
2. Với PAT/API key, user gọi endpoint credentials. Với OAuth, user gọi begin, mở authorization URL
   để consent và provider redirect về callback đã đăng ký chính xác. Cookie đăng nhập phải còn hiệu lực.
3. Backend kiểm tra state/TTL/owner, PKCE S256, issuer và profile/grant generation, gửi authorization
   code cùng `resource` tới token endpoint đã review. State được consume trước network I/O;
   hai callback cùng state chỉ một lần exchange. Code thất bại không retry lại, cần bắt đầu flow mới.
4. Backend validate token type, expiry, resource nếu được trả và scope không vượt cấu hình; mã hóa
   token, tạo reference và tắt connection. User bật lại qua API enabled để xác nhận binding mới.
5. Refresh diễn ra trước expiry 30 giây, serialize bằng transaction/row lock giữa process. Refresh
   token mới thay token cũ; nếu provider không trả refresh token mới thì giữ token hiện có.
   Invalid grant/refresh failure tắt connection và revoke binding, yêu cầu reauthorization.
6. Revoke commit local trước khi gọi provider; lỗi/mất key/profile không làm mất khả năng thu hồi local.
   `remote_revocation=succeeded|failed|not_configured`; không có retry nền nếu provider revoke thất bại.

OAuth bản này là **authorization code + PKCE cho public client đăng ký trước**. Issuer/endpoints/scopes
được operator duyệt, không tự DCR, tự khám phá metadata hoặc hỗ trợ confidential client secret/step-up
scope. `require_response_issuer` mặc định true; chỉ tắt nếu provider đã review không trả `iss`.
Resource parameter luôn gửi ở authorize, exchange và refresh. Token opaque không được client tự
coi là JWT đã xác minh; resource server phải kiểm tra audience. PAT expiry do provider quản lý,
không có auto-refresh PAT; khi hết hạn user rotate token.

## Nối runtime riêng

`backend.services.plugin_credentials.private_http_config` tạo config cho **registry riêng theo owner**.
Chỉ chứa binding của tool được grant, pin role/grant generation/reference. Mỗi HTTP request resolve
credential qua DB lại; thay grant/role/reference làm config cũ yêu cầu reconnect. Refresh giữ nguyên
binding thì SDK nhận token mới ở request kế tiếp. Handler cũng kiểm tra `ToolContext.user_id`.

Ngày 85 đã nối factory vào graph/chat qua runtime riêng theo user/run; UI/live session inventory
chưa triển khai. Xem [Skill runtime](PLUGIN_SKILLS.md). Connection API vẫn báo
`runtime_connected=false`; không lấy registry host làm bằng chứng connection user đang live.
Bridge graph/discovery/Skill và guard call đang chạy đã có ở Ngày 84–85; UI ở Ngày 86.

Private factory tạm tắt result memoization và automatic retry của executor V1, kể cả manifest khai
idempotent. Nhờ đó gọi lại cùng arguments vẫn kiểm tra quyền mới, không trả cached data sau revoke.
Host public/stdio giữ policy cũ; chỉ bật lại cache/retry riêng khi có authorization-aware cache key.

## Kiểm thử và bằng chứng

```powershell
python -m pytest -q tests/test_mcp_http.py tests/test_plugin_manager.py
python -m scripts.check_http_plugins
python -m scripts.ci_compose_gate --output-dir reports/ci/compose
```

TLS fixture kiểm tra modern/legacy, JSON/SSE, SNI, redirect và cleanup sau revoke. Probe PostgreSQL
kiểm tra auth/owner, mã hóa, PKCE/issuer/resource, state replay/concurrency, stale consent,
refresh rotation, invalid grant và revoke local/provider. Không gọi provider thật hoặc cần account/key.
Đây chưa là compatibility certificate cho mọi MCP server/OAuth provider.

Xem [nghiệm thu Ngày 83](../../reports/checks/2026-10-18-day83/VALIDATION.md) và
[ADR 0005](../adr/0005-http-mcp-credentials-and-oauth.md).
Nguồn đối chiếu: [MCP authorization](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization),
[OWASP SSRF prevention](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html).
