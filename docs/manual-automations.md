# Scheduled Automations — hướng dẫn vận hành

Plan04 dùng scheduler riêng, queue/worker PostgreSQL và Agent hiện có. Đóng tab không
dừng tác vụ. Bản đầu hỗ trợ một lần, hằng ngày, hằng tuần; chưa có webhook/điều kiện.
Google thật và model trả phí chưa được nghiệm thu trong quá trình phát triển offline.

## Chuẩn bị

1. Sao lưu database; đọc kết quả nghiệm thu trước khi bật trên dữ liệu thật.
2. Chạy migration `python -m alembic upgrade head` trên database triển khai đã chọn.
   Các revision Plan04 là 0034–0036. Chạy migration một lần trước web/worker/scheduler.
3. Cấu hình riêng `AUTOMATIONS_ENABLED=true`, `AGENT_REACT_ENABLED=true` cho cả ba process.
   Context, output mỗi call, rate limit, RPC timeout và sandbox vẫn được áp dụng.
   Automation không có trần token/bước/thời gian tổng; usage vẫn được ghi.
4. Dùng Artifact thì bật `WORKSPACE_ARTIFACTS_ENABLED` và runner/storage theo hướng dẫn
   Plan01–02. Dùng Google thì hoàn thành [hướng dẫn connector](manual-google-connectors.md):
   OAuth, encryption key bền vững dùng chung web/worker, Picker, chọn từng file.
5. Giữ tool hiện có trong `TOOL_ALLOWLIST` và bổ sung đúng tool Google cần dùng. Ví dụ
   luồng đọc/thêm Sheets cần `search_web,google_sheets_read,google_sheets_prepare,google_sheets_append`.
   Bật connector không tự mở allowlist. Không gửi token/secret vào chat.

Khởi chạy native trong ba terminal với cùng cấu hình/database:

```powershell
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
python -m scripts.run_background_worker
python -m scripts.run_automation_scheduler
```

Không khởi tạo scheduler trong web lifespan. Có thể chạy nhiều scheduler/worker;
locking và uniqueness bảo vệ việc phát hành, nhưng đây chưa phải nghiệm thu tải lớn.

Với Compose, đặt các biến riêng `DEV_AUTOMATIONS_ENABLED=true`,
`DEV_AGENT_REACT_ENABLED=true`, `DEV_GOOGLE_*`, `DEV_TOOL_ALLOWLIST` và
`DEV_WORKSPACE_ARTIFACTS_ENABLED` nếu dùng. Sau migration, tạo lại web/worker và bật
profile `automations` để chạy service `scheduler`. Kiểm tra tên service thực tế bằng
`docker compose --profile automations config --services`. Feature mặc định vẫn tắt.

## Tạo và quản lý

1. Mở **Automations → Tạo mới**, nhập nhiệm vụ, lịch và múi giờ IANA
   (Việt Nam: `Asia/Ho_Chi_Minh`). Lịch một lần diễn giải giờ theo múi giờ đã chọn.
2. Chọn từng phiên bản Artifact hoặc từng file Google/tab/vùng/quyền. Sheet phải là
   Google Sheets native, Doc phải là Google Docs native. Không tự chuyển file Office.
3. Xem bản rà soát rồi lưu. Bản nháp luôn tắt lịch. **Chạy ngay** thử đúng revision
   đã lưu; chạy thử được cả khi lịch đang tắt. Sửa tạo revision mới, không đổi lượt cũ.
4. Xem kết quả, usage, file/link ở lịch sử và Inbox; preview file chỉ mở khi bấm.
   Inbox lưu DB, cập nhật badge khoảng 10 giây. Giao diện hiển thị 100 bản ghi gần nhất.
5. Bật lịch khi đã kiểm tra nhiệm vụ và phạm vi được phép.

**Dừng lượt chạy** hủy lượt hiện tại, giữ lịch. Việc đã ghi ra Google không được hoàn tác.
**Tắt lịch** có hai lựa chọn: giữ lượt đang chạy hoặc dừng luôn lượt đó. Bật lại chờ mốc
kế tiếp, không chạy bù thời gian tắt. Lượt bị người dùng dừng không tự retry.

Mỗi automation có tối đa một lượt active, gồm cả chờ duyệt/đang hủy/chờ đối soát.
Mốc tới khi lượt trước còn active ghi `skipped_overlap`. Scheduler ngừng lâu ghi các mốc
lỡ lịch (`missed`) thay vì chạy dồn; grace mặc định 60 giây. DST giờ lặp chạy fold đầu,
giờ không tồn tại bị bỏ qua; form lịch một lần từ chối giờ không tồn tại.

## Chờ duyệt, kết nối và kết quả chưa xác định

Python tùy ý và tool chưa có quyền tự động vẫn cần duyệt từng call. Yêu cầu duyệt
automation không tự hết hạn sau 5 phút; người dùng có thể quay lại lịch sử để duyệt/từ chối
hoặc dừng. Nó chiếm active slot và một worker trong khi chờ. Chat giữ timeout hữu hạn cũ.
Proposal Google vẫn có TTL riêng: proposal quá hạn phải chuẩn bị và duyệt lại, không nới TTL.

Khi revoke/reconnect/chọn lại file làm generation thay đổi, lượt cũ không tự dùng quyền mới.
Kết nối lại trong Connectors, kiểm tra/chọn lại file, sửa và rà soát revision rồi chạy mới.

`needs_reconciliation` nghĩa là chưa biết kết quả thực tế, không phải chưa thực hiện.
Mở file đích và kiểm tra hàng/nội dung; kiểm tra usage/provider nếu là model mất phản hồi.
Chỉ dùng **Kết thúc, không phát lại** sau đối soát. Thao tác này giải phóng slot, giữ journal
và receipt chưa xác định; không append lại, không sửa receipt thành thành công.
Các kết quả đã hoàn tất vẫn được giữ khi dừng hoặc lỗi ở bước sau.

## Kiểm thử offline và nghiệm thu thật

Dùng PostgreSQL thử riêng có tên kết thúc `_test`, đã migrate current head:

```powershell
$env:GOOGLE_TEST_DATABASE_URL='postgresql+asyncpg://USER:PASSWORD@127.0.0.1:PORT/automation_test'
python -m scripts.check_automation_workflow --offline --report reports/automation-offline.xml
```

Lệnh tạo schema ngẫu nhiên của riêng từng test, dùng queue/checkpointer/worker thật và
provider fixture. Có test tắt hẳn child worker sau receipt rồi restart, hai scheduler/worker,
response mất sau ghi, waiting qua hai ngày, pause race và browser đóng/reload.
Không suy ra kết quả Google thật từ fixture. Chạy gate Artifact riêng theo Plan01–02.

Nghiệm thu thật sau khi cấu hình OAuth/model: chọn một Sheet thử riêng, tạo automation
chỉ ghi Sheet đó, chạy trong chat và kiểm tra đọc/ghi trước. Sau đó chạy thử ở UI hoặc:

```powershell
python -m scripts.check_automation_workflow --live --automation-id UUID_CUA_AUTOMATION_THU
```

Lệnh live thực sự enqueue một lượt của đúng draft được chọn, dùng quyền đã lưu; không bật lịch.
Cần worker đang chạy. Timeout chỉ dừng theo dõi CLI, không dừng lượt. Quay lại UI để dừng.
Kết quả CLI `succeeded` vẫn cần kiểm tra nội dung file thật. Sau đó đặt một mốc gần, bật lịch,
đóng browser, mở lại kiểm tra Inbox/file/usage; thử stop, pause, reconnect và đối soát.
Lưu riêng bằng chứng OAuth, Google, model, file trước/sau và kết quả từng bước.

## Health và rollback

Scheduler ghi heartbeat khi đặt `AUTOMATION_SCHEDULER_HEARTBEAT_PATH`; Compose đã đặt
`/tmp/automation-scheduler-health.json`. Chạy `python -m scripts.check_automation_scheduler_health`
trong cùng môi trường/path. Health worker theo cơ chế hiện có. Heartbeat chỉ chứng minh
dispatcher sống, không bảo đảm OAuth hay model đang dùng được. Kiểm tra queue tuổi cao,
run `waiting_approval`/`needs_reconciliation`, missed/overlap và log trước khi retry.

Rollback an toàn: tắt các lịch và dừng/drain lượt đang chạy qua UI; dừng scheduler, đặt
`AUTOMATIONS_ENABLED=false` trên web/worker rồi restart. Giữ database, file, khóa mã hóa và
journal để đối soát. Không drop bảng hay downgrade để xóa dữ liệu. Migration 0036 từ chối
downgrade khi còn approval không có expiry; giữ schema và tắt feature là cách rollback mặc định.
