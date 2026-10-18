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

Chạy phần offline trước:

```powershell
.\.venv\Scripts\ruff.exe check agent_core backend migrations scripts tests
.\.venv\Scripts\python.exe -m pytest -q --ignore=tests/test_mcp.py
.\.venv\Scripts\python.exe -m scripts.validate_eval_dataset
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

Ngày 77 mới chốt contract/config; bật flag không tự tạo runtime plugin/RAG/planner/cache chưa
triển khai. Flag không thay quyền user/tool, quota hoặc approval. MCP demo/sandbox và SSE hiện tại
vẫn dùng luồng V1. Chi tiết và ví dụ tại [contract V2](docs/contracts/README.md) và
[ADR ownership](docs/adr/0001-v2-contracts-and-layer-ownership.md).

Kiểm tra riêng contract/config, không gọi provider hoặc cần DB:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_v2_contracts.py tests/test_v2_features.py
```
