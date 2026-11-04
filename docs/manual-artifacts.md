# File làm việc: Excel

Giai đoạn này triển khai giao diện A và Excel. Word/PDF, Google Workspace và lịch tự động
thuộc các kế hoạch tiếp theo. `.env` thật không được thay đổi trong quá trình triển khai.

## Sử dụng

1. Mở **File làm việc** hoặc tab **File làm việc** trong Thư viện, tải `.xlsx` lên.
   Không cần index RAG; file có thể được chọn ngay sau khi kiểm tra định dạng.
2. Trong chat, bấm **＋ File làm việc**, chọn phiên bản rồi lưu lựa chọn. Phiên bản được ghim;
   bản sửa mới không tự thay input. Mỗi hội thoại chọn tối đa 20 phiên bản.
3. Yêu cầu bằng chat, ví dụ: “Tạo Excel chi tiêu gồm tiền nhà, ăn uống, đi lại; tính tổng
   và thêm biểu đồ cột”, hoặc “Sửa file đã chọn: thêm sheet Tháng 2 và định dạng tiền tệ”.
4. Bấm thẻ file để mở preview cạnh chat. File mới/bản sửa không mở panel đang đóng.
   Có thể chọn sheet, trang dữ liệu, phiên bản và tải xuống. Màn hình hẹp có nút quay lại;
   Escape đóng panel và trả focus. Đóng preview không dừng Agent.

Agent có thể sửa tiếp output của chính hội thoại, không cần thay input đã ghim.
Chỉnh sửa tạo phiên bản mới và giữ bản gốc. Hai sửa từ cùng parent giữ hai nhánh.
Tên file/công thức hiển thị như văn bản; download luôn dùng ID phiên bản bất biến.

Thư viện RAG cũ tiếp tục dùng cho hỏi đáp nguồn. **Lưu bản sao làm việc** sao chép nguyên
bytes DOCX/PDF/TXT sang kho độc lập, kể cả nguồn chưa index. Xóa nguồn không xóa bản sao.
Các bản sao này hiện quản lý/tải xuống được; bộ xử lý Word/PDF thuộc kế hoạch 02.

## Phạm vi Excel

- Ghi ô, thêm sheet, SUM/SUMIF/AVERAGE/COUNT/IF, định dạng vùng, biểu đồ cột/ngang/đường.
- Chuỗi qua `write_cells` luôn là text; công thức đi qua `set_formula`.
- Python cho yêu cầu phức tạp chạy trong sandbox, bắt buộc approval; Python nhỏ cũ giữ nguyên.
- Macro, ảnh, external links, embedded objects và drawing không giữ được bị từ chối.
  Công thức cũ an toàn ngoài danh sách được giữ nhưng đánh dấu chưa hỗ trợ tính lại.
- “Đã tính lại công thức” chỉ có sau UNO `calculateAll()` và kiểm inventory/cache.
  Upload đơn thuần không xác nhận cache còn mới.
- Preview tối đa 512 ô/512 KiB, 100 cột; text rút gọn 256 ký tự. Biểu đồ preview tối đa
  4 chart, 6 series/chart, 100 điểm/series. Download giữ dữ liệu đầy đủ.

## Khởi động

Cài dev dependencies khi chạy test; web không cần import openpyxl để parse Office:

```powershell
uv sync --extra dev
docker build --target code -t personal-agent-artifact-code:1 -f infra/docker/artifact-sandbox/Dockerfile .
docker build --target render -t personal-agent-artifact-render:1 -f infra/docker/artifact-sandbox/Dockerfile .
```

Chạy `alembic upgrade head` trên môi trường đích trước khi bật flag. Migration 0031 tạo
metadata/version/binding; 0032 giữ bản sao khi nguồn RAG bị xóa. Dùng flag để rollback tính
năng, giữ nguyên database/volume chứa file.

Với Compose, dùng env mẫu Docker và profile `artifacts`; chuẩn bị các giá trị cho web/worker:

```text
DEV_WORKSPACE_ARTIFACTS_ENABLED=true
DEV_ARTIFACT_RUNNER_KEY=<khóa ngẫu nhiên riêng, tối thiểu 32 ký tự>
```

`docker compose --env-file .env.docker --profile artifacts up -d --build` dùng file cấu hình
do người vận hành chuẩn bị. Broker có Docker socket và volume journal; web/worker không có
socket. Image code/render phải có trên cùng daemon. Broker không publish port.
Local Windows chạy broker qua loopback; web/worker dùng chung private artifact storage root.

Host Python dùng `WORKSPACE_ARTIFACTS_ENABLED`, `ARTIFACT_RUNNER_URL`, `ARTIFACT_RUNNER_KEY`,
`ARTIFACT_STORAGE_PATH`. Với allowlist tùy chỉnh, thêm `artifact_inspect`, `artifact_create`,
`artifact_edit`, `run_artifact_python`. Restart cả web và worker sau khi đổi cấu hình/code.
ReAct hỗ trợ vòng làm việc nhiều bước; routing nhận metadata file làm việc riêng nguồn RAG.
Google/Automations và Workspace Artifacts mặc định tắt cho đến khi người vận hành bật.

## Giới hạn và phục hồi

Mặc định 20 MiB/file, 200 MiB/user (tính cả bản cũ); binary transport tối đa 40 MiB.
Code: 1 CPU/512 MiB, 15s CPU/30s wall. Calc: 1 CPU/1024 MiB, 60s CPU/90s wall, pids 64.
Hai job đồng thời/broker. Giới hạn sandbox độc lập giới hạn Agent/chat.
Input chỉ đọc, output tmpfs, root chỉ đọc, không mạng, không credentials/socket trong sandbox.

Operation key/job ID cố định cho retry. Output hoàn tất được replay trong một giờ; job đã
bắt đầu nhưng bị gián đoạn bị tombstone/reaper chặn chạy lại. Journal giữ tombstone sau khi
cache hết hạn; hủy trước start cũng được ghi nhận. Outcome chưa rõ/hết cache cần một lượt mới.
Không tự replay email hoặc hành động ngoài Artifact.

Worker đối soát output đã publish từ DB trước khi resume. Call phải khớp fingerprint đã lưu;
Python vẫn cần quyết định duyệt đã ghi nhận. Process chết trước receipt không nhân version.
Gián đoạn giữa các stage chỉ dùng lại job ID cũ, không chạy lại job đã bắt đầu chưa hoàn tất.

Broker `/health/live` kiểm tiến trình; gate offline mới kiểm Docker/Calc thực thi.
Reaper chạy mỗi 30s và khi startup, chỉ xóa resource có label runner/job khớp.
Không dùng `docker system prune` để dọn Artifact.

COMMIT mất phản hồi có thể giữ staging để tránh xóa output đã commit. Dọn orphan sau 24 giờ
theo lịch vận hành, kiểm DB và giữ owner lock khi đối soát:

```powershell
python -m scripts.reconcile_artifacts
python -m scripts.reconcile_artifacts --apply
```

Lệnh đầu chỉ báo số lượng. Lệnh hai xóa file cũ không có version tham chiếu; giữ file mới,
file được tham chiếu, đường dẫn lạ và symlink. DB không truy cập được thì dừng dọn.
Tắt `WORKSPACE_ARTIFACTS_ENABLED`/`DEV_WORKSPACE_ARTIFACTS_ENABLED` và restart web/worker để
ẩn/từ chối API; giữ binary volume và journal để bật lại.

## Cổng nghiệm thu offline

Chuẩn bị PostgreSQL scratch đã migrate, tên database kết thúc `_test`. Gate tạo user/job
riêng, không xóa schema/database hoặc dùng dữ liệu người dùng. Cần Docker thật và hai image;
thiếu hạ tầng phải báo NOT RUN, không coi mock là nghiệm thu.

```powershell
$env:ARTIFACT_TEST_DATABASE_URL='postgresql+asyncpg://test_user:test_password@127.0.0.1:5432/artifacts_test'
python -m scripts.check_artifact_workflow --offline --report artifact-acceptance.json
```

Gate hard-kill/restart broker, hard-crash worker sau publish, resume bằng queue/checkpoint
PostgreSQL thật, kiểm upload/select/import/replay/owner/cancel, quota/zip bomb/symlink/isolation,
workbook 10.000×12 có hai sheet/công thức/biểu đồ và hai child cùng parent. Report ghi CPU/RAM
lấy mẫu, thời gian và kích thước; không gọi là peak chính xác. Live model/provider quality
chưa được kiểm bởi gate này; chỉ chạy khi người dùng yêu cầu riêng. Browser QA kiểm UI A
cùng worker thật và model fixture.
