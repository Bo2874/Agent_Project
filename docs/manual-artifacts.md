# File làm việc: Excel, Word và PDF văn bản

Giao diện A hỗ trợ file Excel, Word và PDF có văn bản. Google Workspace và lịch tự động
thuộc các kế hoạch tiếp theo. `.env` thật không được thay đổi trong quá trình triển khai.

## Sử dụng

1. Mở **File làm việc** hoặc tab **File làm việc** trong Thư viện, tải `.xlsx`, `.docx` hoặc PDF có văn bản lên.
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
Các bản sao nguồn giữ nguyên bytes và có thể tải xuống. Bản sao cũ chưa có preview trang;
yêu cầu sửa trong chat để tạo phiên bản mới có preview. Nội dung chưa hỗ trợ được báo lỗi rõ ràng.

## Phạm vi Excel

- Ghi ô, thêm sheet, SUM/SUMIF/AVERAGE/COUNT/IF, định dạng vùng, biểu đồ cột/ngang/đường.
- Chuỗi qua `write_cells` luôn là text; công thức đi qua `set_formula`.
- Python cho yêu cầu phức tạp chạy trong sandbox, bắt buộc approval; Python nhỏ cũ giữ nguyên.
- Macro, ảnh, external links, embedded objects, rich text (nhiều kiểu chữ trong một ô)
  và drawing không giữ được bị từ chối trước khi sửa, kể cả Python fallback.
  Công thức cũ an toàn ngoài danh sách được giữ nhưng đánh dấu chưa hỗ trợ tính lại.
- “Đã tính lại công thức” chỉ có sau UNO `calculateAll()` và kiểm inventory/cache.
  Upload đơn thuần không xác nhận cache còn mới.
- Preview tối đa 512 ô/512 KiB, 100 cột; text rút gọn 256 ký tự. Biểu đồ preview tối đa
  4 chart, 6 series/chart, 100 điểm/series. Download giữ dữ liệu đầy đủ.

## Phạm vi Word và PDF

- Tạo Word với heading, đoạn văn, danh sách, bảng; sửa text theo node ID gắn với đúng hash
  của bản đầu vào, chỉnh style/cỡ chữ/bold/italic/alignment và ô bảng, thêm các block cuối tài liệu.
  Các run/bảng/style không bị sửa được giữ lại; bản gốc và các nhánh sửa vẫn tải xuống riêng được.
- Word có ảnh/drawing, embedded objects, external relationships, hyperlink, footnote/endnote,
  comments, track changes, fields hoặc content controls chưa được hỗ trợ và bị từ chối trước sửa.
  Python cho Word cũng qua cùng preflight, chạy cách ly và cần duyệt như Excel.
- PDF được đọc theo trang và dựng lại từ nội dung mong muốn, không cam kết giữ bố cục trang gốc.
  PDF mới giữ kèm DOCX của chính phiên bản đó. Sửa theo node ID luôn dùng nguồn của parent đã chọn,
  không lấy nguồn của phiên bản mới nhất ở nhánh khác.
- Không OCR. PDF không có text trả `unsupported_scanned_pdf`. Với trang có ảnh hoặc không đọc được
  text, inspect báo coverage/omitted pages; dựng lại chỉ phần text phải qua tool
  `artifact_rebuild_pdf_text_only` và bước duyệt đúng file, nội dung, danh sách trang bị ảnh hưởng.
  PDF mã hóa, form, annotation, script và attachment chưa hỗ trợ. Bookmark/đích điều hướng trang
  thông thường do Writer tạo được chấp nhận.
- PDF tối đa 20 trang, 20 MiB; stream giải nén tối đa 8 MiB/trang, 32 MiB/tài liệu, text tối đa
  200.000 ký tự/trang và 400.000/tài liệu. Trang dài đọc tiếp bằng `limit=1`, page `offset`
  và `text_offset=next_text_offset`; cảnh báo cắt preview không có nghĩa đã đọc hết nguồn.
- Preview Word/PDF dùng Writer và Poppler trong renderer cách ly, tối đa 20 trang, PNG cạnh dài
  1.600 pixel. Tất cả trang phải hoàn thành trước khi công bố phiên bản; lỗi render không để lại
  một output được báo thành công thiếu preview. Tài liệu vượt giới hạn bị từ chối rõ ràng.
- Preview dùng đúng owner/version/asset UUID, chỉ phục vụ PNG, không HTML/SVG. Đóng panel vẫn giữ
  đóng khi có output mới. Không có trình sửa trực tiếp; người dùng yêu cầu sửa qua chat.
- PDF, nguồn DOCX và ảnh preview lưu nguyên tử, cùng tính quota; orphan cleanup giữ cả bộ đã commit,
  kể cả trường hợp mất phản hồi commit. Bật Artifacts dùng allowance schema tool 10.000 token
  (catalog tài liệu khoảng 6.200); tắt Artifacts giữ 6.000. Đây là phần ngữ cảnh schema, không phải
  một giới hạn ngân sách chạy tác vụ mới.

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
`artifact_edit`, `run_artifact_python`, `artifact_rebuild_pdf_text_only`. Restart cả web và worker sau khi đổi cấu hình/code.
ReAct hỗ trợ vòng làm việc nhiều bước; routing nhận metadata file làm việc riêng nguồn RAG.
Google/Automations và Workspace Artifacts mặc định tắt cho đến khi người vận hành bật.

## Giới hạn và phục hồi

Mặc định 20 MiB/file, 200 MiB/user (tính cả bản cũ, nguồn kèm và preview); binary transport tối đa 40 MiB/lượt.
Chưa có thao tác xóa phiên bản để giải phóng quota. Orphan cleanup chỉ dọn dữ liệu không còn
tham chiếu, không xóa version đã lưu. Khi chạm quota, cần người vận hành tăng quota/dung lượng
theo cấu hình; thiết kế retention/xóa có kiểm soát sẽ được làm riêng.
Giới hạn cấu hình web và giới hạn cố định trong protocol/sandbox là các trần độc lập:
giới hạn thực tế là trần nhỏ nhất. Tăng `ARTIFACT_MAX_FILE_BYTES` không nâng trần 20 MiB
của một file trong runner, cũng không nâng trần transport, CPU/RAM hoặc archive.
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
Startup thu hồi job đang chạy dở; reaper sweep mỗi 30s, chỉ xóa resource có label runner/job khớp.
Lỗi sweep được ghi log và thử lại ở chu kỳ kế tiếp; cancellation vẫn dừng task bình thường.
Thư mục upload/validation tạm có tên UUID riêng và OS file lock; broker thu hồi sau một giờ
nếu không còn lock. Thư mục unpack/copy của worker dùng cùng cơ chế, được đối soát theo lịch
orphan bên dưới. File mới, workspace còn hoạt động, symlink và cấu trúc lạ luôn được giữ.
Không dùng `docker system prune` để dọn Artifact.

COMMIT mất phản hồi có thể giữ staging để tránh xóa output đã commit. Dọn orphan sau 24 giờ
theo lịch vận hành, kiểm DB và giữ owner lock khi đối soát:

```powershell
python -m scripts.reconcile_artifacts
python -m scripts.reconcile_artifacts --apply
```

Lệnh đầu chỉ báo số lượng file/thư mục scratch. Lệnh hai xóa file cũ không có version tham chiếu
và workspace scratch cũ đã mất lock; giữ file mới,
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
python -m scripts.check_artifact_workflow --offline --formats docx,pdf --report document-acceptance.json
```

Gate hard-kill/restart broker, hard-crash worker sau publish, resume bằng queue/checkpoint
PostgreSQL thật, kiểm upload/select/import/replay/owner/cancel, quota/zip bomb/symlink/isolation,
workbook 10.000×12 có hai sheet/công thức/biểu đồ và hai child cùng parent. Report ghi CPU/RAM
lấy mẫu, thời gian và kích thước; không gọi là peak chính xác. Live model/provider quality
chưa được kiểm bởi gate này; chỉ chạy khi người dùng yêu cầu riêng. Browser QA kiểm UI A
cùng worker thật và model fixture.

`--formats docx,pdf` chạy kiểm tra Word/PDF, bundle/preview/owner, mẫu tiếng Việt ba trang và
trình duyệt với queue/worker/checkpoint thật. Cần Node và Chrome (`CHROME_PATH` nếu khác mặc định).
Không thay việc kiểm tra chất lượng với provider thật. Ảnh và số đo mẫu:
[nghiệm thu tài liệu](acceptance/2026-11-05-documents/renderer.json).

Kiểm thử hồi quy race preview bằng component thật trong Chrome headless:

```powershell
node tests/artifact_panel_browser.cjs
```

`CHROME_PATH` có thể chỉ định Chrome khác. Test dùng profile riêng, không mở tài khoản/dịch vụ
ngoài; kiểm event đến trong lúc mở file, đổi trang, refresh, đóng và chuyển hội thoại.
