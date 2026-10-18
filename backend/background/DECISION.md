# Background queue v1

Quyết định Ngày 42: dùng PostgreSQL thay vì thêm Redis ngay.

- Job cần bền vững, audit được và liên kết trực tiếp với user/agent run.
- Project đã có PostgreSQL; chưa cần thêm service, dependency và chiến lược persistence mới.
- Worker dùng `FOR UPDATE SKIP LOCKED` để nhiều consumer không nhận cùng một job.
- Smoke test dùng hai consumer đồng thời và xác nhận chỉ một consumer claim được job.
- Worker chạy ở process riêng; web server không tự chạy job dài.
- Job có lease/heartbeat; worker khác khôi phục job khi lease hết hạn.
- Retry chỉ dành cho lỗi tạm thời được handler đánh dấu rõ và dùng exponential backoff.
- Idempotency key có scope theo user/system và fingerprint để chặn tái dùng sai payload.

Redis được hoãn, không bị loại bỏ. Đánh giá lại khi đo được queue latency/throughput không đạt,
hoặc khi cần pub/sub realtime, cache và rate limit. Nếu Redis trở thành queue chính thì phải cấu
hình AOF/replication và kiểm thử phục hồi trước khi chuyển.
