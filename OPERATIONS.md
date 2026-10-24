# Vận hành Personal AI Agent

Tài liệu này là runbook tối thiểu cho môi trường development/staging. Lệnh được chạy tại
`C:\Agent_Project` bằng PowerShell.

## 1. Khởi động

Docker dev (app/worker/PostgreSQL có pgvector, không cần Python/DB/API key trên host):

```powershell
Copy-Item .env.docker.example .env.docker
docker compose --env-file .env.docker up -d --build --wait --wait-timeout 180
docker compose --env-file .env.docker ps -a
```

Chỉ copy config khi `.env.docker` chưa tồn tại. Migration one-shot phải exit 0 trước khi
app/worker khởi động; V2 và Python sandbox mặc định tắt trong profile dev này.
Hai volume riêng giữ PostgreSQL và tài liệu; `docker compose --env-file .env.docker down`
giữ dữ liệu. Lệnh E2E/probe, cách restart và xử lý migration lỗi nằm tại
[runbook Docker dev](infra/docker/app/README.md). Bằng chứng nghiệm thu:
[Ngày 78](reports/checks/2026-10-18-day78/STARTUP.md).

Chạy trực tiếp trên Windows như trước:

```powershell
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\uvicorn.exe backend.main:app --host 127.0.0.1 --port 8000
```

Worker chạy ở terminal riêng:

```powershell
.\.venv\Scripts\python.exe -m scripts.run_background_worker
```

Kiểm tra:

```text
GET http://127.0.0.1:8000/health/live
GET http://127.0.0.1:8000/health/ready
```

`live` kiểm tra process; `ready` kiểm tra kết nối PostgreSQL.

## 2. E2E smoke test

Khởi động web trước, sau đó chạy:

```powershell
.\.venv\Scripts\python.exe -m scripts.run_e2e_smoke
```

Luồng kiểm tra: health → đăng ký hai user tạm → cookie auth → tạo conversation → chặn truy
cập chéo user → logout/login → dọn toàn bộ user tạm. Mặc định không gọi DeepSeek.

Kiểm tra thêm chat streaming bằng DeepSeek thật:

```powershell
.\.venv\Scripts\python.exe -m scripts.run_e2e_smoke --with-llm
```

Lệnh này sử dụng token API.

## 3. Backup PostgreSQL

```powershell
.\.venv\Scripts\python.exe -m scripts.backup_database --output-dir backups
```

Mỗi backup gồm file `.dump` custom format và manifest `.dump.json` chứa SHA-256. Thư mục
`backups/` bị loại khỏi Git. Backup vẫn chứa dữ liệu thật: chỉ lưu ở nơi được mã hóa, giới hạn
quyền đọc và không gửi qua chat/email.

Khuyến nghị: backup hằng ngày, giữ 7 bản ngày và 4 bản tuần; kiểm tra restore ít nhất mỗi tháng.

## 4. Kiểm tra backup và restore

Test đầy đủ trên database tạm, tự đối chiếu số record và tự xóa database test:

```powershell
.\.venv\Scripts\python.exe -m scripts.verify_database_backup
```

Restore thủ công vào database mới:

```powershell
.\.venv\Scripts\python.exe -m scripts.restore_database `
  --backup backups\personal_agent_db_YYYYMMDDTHHMMSSZ.dump `
  --target-db personal_agent_restore `
  --confirm-target personal_agent_restore
```

Nếu target đã tồn tại, chỉ thêm `--replace-target` sau khi xác nhận không còn dữ liệu cần giữ.
Restore vào database chính mặc định bị chặn. Quy trình khôi phục sự cố:

1. Dừng web và worker.
2. Tạo thêm một bản backup của database hiện tại nếu còn truy cập được.
3. Xác minh đúng file, manifest và tên database.
4. Chạy restore với `--replace-target --allow-primary` và `--confirm-target` đúng tuyệt đối.
5. Chạy `alembic current`, `/health/ready` và E2E smoke test trước khi mở lại dịch vụ.

## 5. Retention

Xem trước, không thay đổi dữ liệu:

```powershell
.\.venv\Scripts\python.exe -m scripts.purge_operational_data --dry-run
```

Áp dụng:

```powershell
.\.venv\Scripts\python.exe -m scripts.purge_operational_data
```

Hoặc enqueue để worker xử lý:

```powershell
.\.venv\Scripts\python.exe -m scripts.enqueue_background_job maintenance.purge_operational_data
```

## 6. Theo dõi và xử lý sự cố

- `/admin`: token, chi phí, cache hit, P50/P95, lỗi, cảnh báo, eval và audit log.
- DeepSeek lỗi: kiểm tra `DEEPSEEK_API_KEY`, quota, model và model P95.
- Agent bị kẹt: kiểm tra agent run detail, checkpoint, tool đang chờ approval và worker.
- Tool lỗi: kiểm tra allowlist, role, timeout, sandbox Docker và error code.
- Queue ngừng chạy: kiểm tra worker process, lease và background job failed.
- Database lỗi: kiểm tra `/health/ready`, dung lượng đĩa, connection và migration hiện tại.

Log và audit metadata được redact, nhưng không nên ghi prompt, tool arguments hoặc secret vào
message log mới. Sau sự cố, ghi lại thời gian, tác động, nguyên nhân, cách khôi phục và test hồi quy.

## 7. Checklist trước khi phát hành

- `alembic upgrade head` và `alembic check` đạt.
- `ruff check backend scripts tests` đạt.
- `pytest -q` đạt.
- E2E smoke test đạt; chạy `--with-llm` ở staging.
- Backup/restore verification đạt.
- `AUTH_SECRET_KEY`, DB password và API key không dùng giá trị development.
- `AUTH_COOKIE_SECURE=true` khi chạy HTTPS.
- Docker sandbox, network none, quota và attack checks đạt.
- Kiểm tra `/admin` không có cảnh báo critical chưa xử lý.

## 8. Regression gate sau migration framework

Từ Ngày 79, cài dependency bằng uv 0.13.1 và lockfile trước khi chạy gate:

```powershell
uv sync --locked --extra dev --no-install-project --no-build
uv lock --check --offline
uv pip check
```

Workflow [Foundation CI](.github/workflows/ci.yml) chạy lint/unit trên Windows/Linux và
migration/integration trên Compose với database mới; không cần model key. Hướng dẫn cài mới,
nâng dependency, gate local và kích hoạt GitHub Actions:
[Dependency và CI](docs/DEPENDENCIES_AND_CI.md).

Chạy phần offline trước:

```powershell
.\.venv\Scripts\ruff.exe check agent_core backend migrations scripts tests
.\.venv\Scripts\python.exe -m pytest -q --ignore=tests/test_mcp.py
.\.venv\Scripts\python.exe -m scripts.validate_eval_dataset
.\.venv\Scripts\python.exe -m scripts.validate_foundation_datasets
.\.venv\Scripts\python.exe -m scripts.validate_plugin_manifests
.\.venv\Scripts\python.exe -m scripts.evaluate_skills
```

MCP subprocess trên Windows và E2E HTTP:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\test_mcp.py
.\.venv\Scripts\python.exe -m scripts.run_e2e_smoke --with-llm
```

Eval smoke thật trước release:

```powershell
.\.venv\Scripts\python.exe -m scripts.run_evals `
  --case-id chat_recall_conversation_context `
  --case-id safety_refuse_system_prompt_extraction
```

`--with-llm` và `run_evals` gọi DeepSeek thật. Chỉ đánh dấu release gate hoàn tất khi Docker Desktop
đang chạy và `python -m scripts.check_python_sandbox` cùng `python -m scripts.check_python_tool_result`
đều đạt.

Gate Compose nền tảng có thể chạy độc lập, không gọi provider:

```powershell
python -m scripts.ci_compose_gate --cold-build --output-dir reports/ci/compose
```

Gate dùng project/volume mới, từ chối namespace có sẵn, giữ volume và dừng container trong
finally. Dataset V2 là scaffold; validation không phải quality eval của router/retriever/planner.

## 9. Baseline trước V2

Mốc Ngày 76: [báo cáo baseline 2026-10-18](reports/baselines/2026-10-18-day76/BASELINE.md).
Thư mục cùng tên lưu `baseline.json`, `environment-snapshot.json`, `live-metrics.json`,
JUnit, log từng gate và manifest SHA-256; timestamp JSON dùng UTC, báo cáo dùng Asia/Saigon.

Kết quả: 343/343 pytest, Skill selection 13/13, adherence 2/2 và live eval smoke 2/2 đạt;
E2E DeepSeek, migration/checkpoint, backup/restore và sandbox checks đạt. MCP stdio và Docker
named pipe bị Windows sandbox chặn thì chạy lại đúng kiểm tra đó ngoài sandbox, giữ cả log lỗi
và log lần chạy hợp lệ. Không sửa code ứng dụng chỉ để né lỗi quyền của môi trường kiểm thử.

Eval smoke chỉ chạy hai case tại mục 8: latency/token không đại diện toàn dataset hoặc SLO.
Token agent và judge được báo riêng; không suy ra tổng token của mọi lệnh từ mẫu eval này.
Snapshot package version là bằng chứng môi trường đã đo, chưa thay thế dependency lockfile.

Baseline còn cảnh báo admin critical `stuck_agent_runs` từ run cũ ngày 01/10/2026.
Cần đối soát và xử lý run này trước khi nghiệm thu checklist phát hành mục 7;
không tự đổi trạng thái run của user trong quá trình chụp baseline. Khi chạy baseline mới,
lưu vào thư mục mới để giữ nguyên mốc đối chiếu và ghi rõ case selection/model/prompt version.

## 10. Cấu hình feature flags V2

Các biến `V2_ENABLED`, `V2_PLUGINS_ENABLED`, `V2_ROUTING_ENABLED`, `V2_RAG_ENABLED`,
`V2_PLANNING_ENABLED`, `V2_PARALLEL_ENABLED`, `V2_REDIS_CACHE_ENABLED`, `V2_EVENTS_ENABLED`
đều mặc định `false`; mẫu nằm trong `.env.example`. Consumer đọc
`get_settings().v2_features.is_enabled(feature)` để áp dụng cả master switch.
Planning cần routing và parallel cần planning; cấu hình thiếu dependency làm startup báo lỗi
validation ngay cả khi master false. Thay env cần restart vì Settings được cache theo process.

Từ Ngày 80 có manifest/catalog metadata tại `agent_core/plugins/`; hướng dẫn field, policy,
namespace, version và trust boundary ở [Plugin guide](docs/plugins/README.md).
`python -m scripts.validate_plugin_manifests` kiểm tra offline, xây catalog tạm disabled và
không start server. Bật flag hoặc đặt catalog entry enabled vẫn chưa tạo connection/grant/runtime.

Ngày 81 thêm API và bảng PostgreSQL cho plugin/grant/connection/credential reference, head
`20261018_0023`. Backup trước khi upgrade DB có dữ liệu; không downgrade 0023 nếu cần giữ dữ liệu
plugin mới. Migration chỉ seed catalog state, không import manifest hay cấp quyền tự động.
Bật cả master/plugins mở các API có auth; admin đăng ký/cấp grant, user bật connection của mình.
Compose nhận `DEV_V2_ENABLED` và `DEV_V2_PLUGINS_ENABLED`, đều mặc định false.
Hướng dẫn request/version/revoke ở [Plugin API](docs/plugins/API.md), ownership ở
[ADR 0003](docs/adr/0003-plugin-persistence-and-user-ownership.md).
Gate `scripts.ci_compose_gate` kiểm tra migration round-trip trên DB tạm và API thật với user synthetic;
không chạy probe này trên DB host/production. `runtime_connected` vẫn false cho connection V2.

Ngày 82: stdio host profile nằm ở `plugins/stdio/servers.json`, chọn bằng `MCP_STDIO_CONFIG_PATH`
(Compose: `DEV_MCP_STDIO_CONFIG_PATH`). Restart process sau khi đổi cấu hình; demo giữ alias cũ.
Manager startup từng server độc lập, có timeout/health/circuit breaker; admin xem
`GET /api/admin/mcp-runtime`. Server lỗi không làm readiness DB fail, nhưng health báo degraded.
Không tự reconnect/replay; sửa config rồi restart app hoặc dùng API restart nội bộ sau cooldown.
Kiểm tra độc lập: `python -m scripts.check_plugin_manager`; xem [runtime guide](docs/plugins/STDIO_RUNTIME.md).

Ngày 83: head `20261018_0024` thêm encrypted credential/OAuth state. Backup DB và giữ encryption key
riêng trước khi upgrade; downgrade 0024 sẽ mất các bảng credential/state mới. Host HTTP profile ở
`plugins/http/servers.json`, chọn bằng `MCP_HTTP_CONFIG_PATH`; mặc định rỗng. Key ring riêng
`MCP_CREDENTIAL_ENCRYPTION_KEYS` không có default/fallback JWT; Compose dùng các biến `DEV_*` tương ứng.
Thiếu key chỉ làm thao tác cần mã hóa trả 503. Không đưa token hoặc key ring vào manifest/image/Git.
HTTP chỉ đi URL host đã review, DNS public được ghim IP, TLS verify, không redirect/proxy. Revoke
local commit trước provider; remote fail không tự retry. Khi invalid grant hoặc đổi grant/role/reference,
hãy reauthorize/reconnect; không replay tool ghi. Chi tiết [HTTP/OAuth](docs/plugins/HTTP_AND_OAUTH.md).
Probe `python -m scripts.check_http_plugins` chạy TLS localhost; credential probe nằm trong Compose gate,
không chạy trên PostgreSQL host/production. Tại mốc Ngày 83, connection chưa được nối vào graph;
runtime riêng theo run được bổ sung ở Ngày 85 bên dưới.

Ngày 84: `GET /api/capabilities` trả metadata host runtime đã lọc role/allowlist, không trả schema.
Admin bật master/plugins có thể gọi `POST /api/admin/mcp-runtime/{server_name}/discover` để refresh
toàn bộ trang tools/list với cache bypass. No-op giữ version; discovery lỗi withdraw riêng server đó.
Run mới dùng schema mới, run đang chạy báo `tool_catalog_changed` trước call tiếp theo nếu binding đổi.
Schema được lazy convert/cache theo registration và fit `CONTEXT_TOOL_TOKEN_BUDGET`; required Skill
không vừa budget sẽ fail trước model call. Probe: `python -m scripts.check_capability_catalog`,
đã tích hợp Compose gate. Xem [catalog guide](docs/plugins/CAPABILITY_CATALOG.md).

Ngày 85: package bindings ở `plugins/packages.json`, root `plugins/packages/`, chọn bằng
`PLUGIN_PACKAGES_CONFIG_PATH` (`DEV_PLUGIN_PACKAGES_CONFIG_PATH` trong Compose). Mặc định rỗng.
Pin manifest fingerprint và từng instructions SHA-256; metadata/required canonical tool alias phải
khớp manifest. Không chỉnh file package trực tiếp khi đang phục vụ run; review release/hash mới và
host stdio binding tương ứng trước khi user bật lại connection. Giữ `TOOL_ALLOWLIST` cho alias mới.
Chat khi bật master/plugins dùng runtime riêng, fresh authority check trước model/cache/retry/RPC
và sau RPC. Revoke/version đổi yêu cầu run mới, không replay pending write; checkpoint lệch pin bị
từ chối. Private MCP session chỉ sống trong run, control `runtime_connected=false` không phải inventory.
Probe PostgreSQL `scripts.check_plugin_skills` chỉ chạy trong Compose gate synthetic. Chi tiết:
[Skill runtime](docs/plugins/PLUGIN_SKILLS.md), [ADR 0007](docs/adr/0007-plugin-skills-and-run-authorization.md).

Ngày 86 (UI): `/plugins` cho user, `/admin/plugins` cho admin. Default flags vẫn tắt; bật master/plugins
trong cấu hình private rồi restart app để dùng catalog/grant/connection. Không tự setup connector.
OAuth UI cần reviewed redirect URI `<app-origin>/plugins/oauth/callback`; callback cũ trả JSON vẫn dùng
được. Thao tác bị 409 cần tải lại, không tự replay; trạng thái configured không xác nhận server online.
Guide và cách test Chrome offline: [Plugins UI](docs/plugins/PLUGINS_UI.md).

Ngày 77 chốt contract/config; plugin runtime đã được bổ sung đến Ngày 85, các phần
RAG/planner/cache chưa triển khai vẫn cần task tương ứng. Flag không thay quyền user/tool,
quota hoặc approval. Khi tắt master/plugins, MCP demo/sandbox và SSE giữ luồng V1.
Chi tiết và ví dụ tại [contract V2](docs/contracts/README.md) và
[ADR ownership](docs/adr/0001-v2-contracts-and-layer-ownership.md).

Ngày 90: rules query router opt-in cần cả master/routing; Compose nhận `DEV_V2_ROUTING_ENABLED`
(mặc định false). Router chỉ thấy runtime catalog theo quyền; chat không nạp tool/Skill, tool_task
giữ executor/approval cũ, clarify hoàn tất message/run mà không gọi model. RAG/planner chưa có
handler sẽ hỏi lại, không tự khả dụng khi bật flag. Jev config/metadata/mock contract tiếp tục hoãn.
Restart app sau đổi env. Tắt switch đưa run mới về V1; resume giữ route đã pin. Catalog/policy đổi
báo `routing_checkpoint_changed`, cần run mới; checkpoint lỗi báo `routing_checkpoint_invalid`.
SSE thêm `routing` khi bật, UI cũ tiếp tục dùng event hiện có. Compose gate đã thêm probe cookie/
SSE/PostgreSQL offline; không chạy `scripts.check_query_routing` trên DB host/production.
Chi tiết: [Query router](docs/routing/QUERY_ROUTER.md), [ADR 0008](docs/adr/0008-query-routing-before-graph.md).

Ngày 91: `get_decision_provider` mặc định None; hai flag routing chưa đủ để gọi Jev. Khi host
inject provider đã cấu hình, context node chọn capability trên shortlist và chọn lại sau mỗi batch
observation; none/exhaustion nạp 0 tool. Search_more chỉ mở trang metadata, không discovery server.
Choice/schema/Skill/policy limits pin vào checkpoint; tháo provider khỏi run đang pin báo
`selection_provider_unavailable`, không fallback V1. Catalog/policy/budget đổi cần run mới.
Rules tăng 1.1.0 nên checkpoint rules 1.0.0 cũng cần run mới; không replay pending write để migrate.
Config/usage/model version/deadline/mock SDK Jev vẫn hoãn, privacy/LLM fallback thuộc Ngày 92.
Compose gate thêm `scripts.check_capability_shortlist` với Protocol fixture, cookie/SSE/PostgreSQL;
không gọi Jev/DeepSeek và không chạy probe trên DB host/production.
[Shortlist guide](docs/routing/CAPABILITY_SHORTLIST.md), [ADR 0009](docs/adr/0009-capability-shortlist-and-observation-routing.md).

Kiểm tra riêng contract/config, không gọi provider hoặc cần DB:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_v2_contracts.py tests/test_v2_features.py
```

## Cập nhật Jev runtime — 2026-10-19

Phần config/SDK/usage của Ngày 88 và chức năng Ngày 92/94 đã bổ sung. Khi hai flag V2/routing
bật, dependency tạo Jev client thật; fallback dùng DeepSeek JSON, có deadline/circuit/budget/privacy.
Mặc định trong code tắt; cấu hình local đặt trong `.env`. Schema cần head `20261019_0025`.
Admin xem `/admin` hoặc `/api/admin/routing`; shadow ghi choice, giữ nguyên baseline thực thi.
Chi tiết cấu hình và các bước người dùng tự test: [Jev runtime](docs/routing/PROVIDER_RUNTIME.md).

Regression SDK/mock HTTP, timeout/cancellation/circuit, privacy, budget/resume, auth và shadow
chạy bằng unit. Compose CI gọi thêm `scripts.check_routing_runtime` trên scratch database;
probe kiểm tra migration/drift/ownership/quota/cascade và không gọi model. Không chạy downgrade
trên database đang dùng. Kết quả ở `reports/checks/2026-10-19-m2-runtime/`.
