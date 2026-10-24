# Dependency lock và kiểm tra thủ công

Repo đích: [Bo2874/Agent_Project](https://github.com/Bo2874/Agent_Project).
Workflow Foundation CI đã gỡ theo yêu cầu user ngày 2026-10-24. Sau khi commit/push thay đổi này,
push/pull request không tự chạy lint/unit/integration. Các lệnh trong tài liệu là công cụ chạy
thủ công khi cần; phần lịch sử CI bên dưới không mô tả workflow đang hoạt động.

## Môi trường được hỗ trợ

- CPython **3.11.17**, khai báo trong `.python-version`; project giới hạn Python `>=3.11,<3.12`.
- Windows x64 và Linux x86_64/glibc, tương ứng máy dev và Docker/runner Ubuntu hiện tại.
  ARM/macOS hoặc Python minor khác cần mở rộng resolver markers và thêm gate kiểm chứng.
- **uv 0.13.1**, được kiểm tra bằng `tool.uv.required-version`.
- `uv.lock` khóa cả dependency trực tiếp/transitive, runtime và extra `dev`, cùng artifact hash.
  `pyproject.toml` vẫn mô tả constraints; cài dependency từ lock.

## Cài mới trên Windows

Cài uv 0.13.1 bằng công cụ quản lý Python của bạn. Nếu đang có Python/pip:

```powershell
python -m pip install uv==0.13.1
uv python install
uv sync --locked --extra dev --no-install-project --no-build
uv lock --check --offline
uv pip check
```

`uv sync` đồng bộ `.venv` theo lock, có thể thay package trong môi trường đó. Nếu muốn giữ `.venv`
cũ để đối chiếu, đặt biến sau **trước** khi sync/run:

```powershell
$env:UV_PROJECT_ENVIRONMENT = "$PWD\.venv-locked"
```

Chạy từ root source bằng `uv run --no-sync python -m ...`; không cài editable project vì
runtime hiện dùng đường dẫn `/app`/root để tìm frontend, skills và migrations.
Không chạy `pip install -e .` hoặc `pip install -U ...` rồi coi đó là môi trường đã khóa.

## Cập nhật dependency có chủ đích

```powershell
uv lock --upgrade-package fastapi
uv sync --locked --extra dev --no-install-project --no-build
uv lock --check --offline
uv pip check
```

Review diff `pyproject.toml`/`uv.lock`, chạy lại lint, unit cả hai OS và migration/integration.
Nếu nâng Python hoặc uv, cập nhật cả `.python-version`, `tool.uv.required-version`,
Dockerfile. Docker Python/uv/PostgreSQL được pin theo digest; cập nhật digest là
một thay đổi có review. Không dùng `--frozen` để bỏ qua kiểm tra freshness trong gate này.

Lock tái lập dependency closure và kiểm tra hash; không cam kết image byte-for-byte giống nhau
hoặc tải được khi registry/network không sẵn sàng. Dev/runtime khác nhau ở extra `dev` và
dependency marker theo OS, nhưng cùng xuất phát từ một lock. Docker runtime không có pytest/ruff.

## Lịch sử workflow CI đã gỡ

Workflow cũ được kích hoạt bởi `push` vào `main`, `pull_request` và `workflow_dispatch`, có các
job dưới đây. Hiện không còn workflow này trong repo.

1. `workflow-lint`: actionlint 1.7.12 với image digest cố định; kiểm tra syntax/expression/action input.
2. `lint-unit`: matrix Ubuntu 24.04 + Windows 2025, Python 3.11.17, sync `--locked`, lock freshness,
   `uv pip check`, Ruff, dataset core/V2, manifest/catalog plugin, Skill fixtures và toàn bộ pytest bao gồm MCP stdio.
3. `migration-integration`: đợi các job trên đạt rồi chạy `scripts.ci_compose_gate` trên Ubuntu.
   Build app/worker từ lock, DB/volume mới, upgrade/drift/idempotence, vector SQL, job worker,
   MCP graph/checkpoint, checkpoint resume, queue reliability, HTTP/memory isolation, persistence
   qua down/up và migration exit 17 chặn runtime. Manifest được validate offline trong app image,
   không activate plugin. Lưu log/JUnit/summary bằng artifact, kể cả khi lỗi.

Unit tests giữ đăng ký sandbox legacy để kiểm tra approval bằng mock; không chạy Docker sandbox
thật trong unit. Compose integration tắt sandbox và V2 như profile Ngày 78. Sandbox runner/live LLM
vẫn thuộc release gate riêng; `stage=scaffold` của dataset không được hiểu là runtime V2 đã đạt quality.

## Chạy gate tại máy dev

Unit/lint/dataset sau sync:

```powershell
uv run --no-sync python -m ruff check .
uv run --no-sync python -m scripts.evaluate_skills
uv run --no-sync python -m pytest -q
```

Nếu thiếu config ứng dụng, dùng các giá trị synthetic trong `infra/ci/offline.env` cho process test;
không ghi đè `.env` thật. Workflow cũ đặt sẵn các giá trị này trong job environment.

Integration chỉ cần Python stdlib trên host và Docker Linux containers/Compose:

```powershell
python -m scripts.ci_compose_gate --cold-build --output-dir reports/ci/compose
```

Mỗi lần mặc định có project UUID mới. Gate từ chối project đã có container/volume; loại toàn bộ
biến `DEV_*` kế thừa để không dùng key/DB/image config cá nhân. Dữ liệu thử tự dọn, container/network
được `down` trong finally, named volume giữ lại để đối chiếu. Dùng project name mới nếu chạy lại.

Linux unit tại máy Windows có thể chạy bằng target `test` riêng:

```powershell
docker build --target test -f infra/docker/app/Dockerfile -t personal-agent-test:local .
docker run --rm --network none --env-file infra/ci/offline.env personal-agent-test:local
```

Target này chứa tests/evals/CI fixtures; runtime app/worker vẫn chỉ copy các thư mục ứng dụng cần thiết.

Ngày 81 bổ sung hai probe vào Compose gate: migration `22 → 23 → 22 → 23` trong DB tạm
(giữ user cũ, Alembic check), và plugin API với auth cookie/owner FK/CAS đồng thời/revoke/role
demotion/rollback release. Plugin flag chỉ bật trong subprocess của probe; runtime chính vẫn tắt.
Không cần thêm dependency hoặc provider key. DB tạm tự xóa, volume của gate vẫn giữ theo quy ước.

Ngày 82 bổ sung `scripts.check_plugin_manager` trong Compose runtime: hai stdio server thật, process
startup treo/exit, request timeout/crash isolation, restart, host policy và cleanup. Unit Windows/Linux
cũng chạy fixture này; runtime probe xác minh manager nạp demo từ file và graph vẫn gọi alias cũ.

Ngày 83 khai báo trực tiếp cryptography 50.0.2 và pin httpx2/httpcore2 2.13.1, vốn đã có trong
transitive closure của lock; không đổi phiên bản distribution, lock metadata được cập nhật.
Compose thêm TLS HTTP modern/legacy JSON/SSE và encrypted credential/OAuth probe: PKCE, issuer/resource,
state replay, concurrent refresh, invalid grant và local/provider revoke. Key/owner/token đều synthetic,
chỉ tồn tại trong probe; default runtime giữ V2 tắt. Migration round-trip bao gồm head 0024 và rollback 0023.

Ngày 85 không thêm dependency/migration. Compose chạy `scripts.check_plugin_skills` với master/plugins
chỉ bật trong process probe: auth/DB/stdio thật, fake model, graph/chat-SSE, revoke/regrant/checkpoint
và package refresh; credential probe kiểm tra thêm owner HTTP run bridge. Không gọi provider ngoài.

Ngày 84 không đổi dependency/lock hoặc migration. Unit job tự thu thêm test catalog/index/schema
budget; Compose gate chạy `scripts.check_capability_catalog` và lưu số schema convert, ước lượng
token eager/selected cùng real MCP discovery giữa run. HTTP probe thêm refresh no-op cho matrix
TLS JSON/SSE modern/legacy; plugin API probe kiểm tra metadata auth và quyền admin discovery.

## Lịch sử mở rộng CI

Ngày 89 thêm gate `python -m scripts.validate_routing_dataset` trong matrix Windows/Linux:
300 query Việt/Anh, grouping dev/test và consistency với taxonomy. Không gọi provider; không
đòi SDK/key Jev hoặc thay lock/migration. Gate chỉ validate dữ liệu, không phải benchmark routing.

Ngày 86 không thêm dependency/migration. Unit job thu test page/auth/feature gate/OAuth completion
mới; Compose probe thêm page bằng cookie thật và browser callback single-use/owner checks.
UI Chrome/Edge acceptance chạy riêng bằng `python -m scripts.check_plugins_ui`; chưa có browser
job trong workflow. Không dùng kết quả API fixture của UI để xác nhận connector live.

Ngày 91 thêm unit policy/graph shortlist và Compose probe `scripts.check_capability_shortlist`:
cookie/SSE/DI/PostgreSQL, chuyển tool/schema sau observation, Skill/none. Fixture chỉ thay Protocol
DecisionProvider của project; không gọi hoặc mock SDK Jev. Dependency/lock/migration giữ nguyên,
hosted workflow chưa được xác minh bởi việc chạy gate local.

Các ghi chép Ngày 79–91 ở trên là lịch sử. Hiện việc push code không kích hoạt Foundation CI;
các script/gate local chỉ được chạy khi có yêu cầu kiểm tra thủ công.

Nguồn: [uv locking/sync](https://docs.astral.sh/uv/concepts/projects/sync/),
[uv Docker](https://docs.astral.sh/uv/guides/integration/docker/),
[uv GitHub Actions](https://docs.astral.sh/uv/guides/integration/github/),
[actionlint](https://github.com/rhysd/actionlint).
