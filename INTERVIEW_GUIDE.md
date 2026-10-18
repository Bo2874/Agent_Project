# Trình bày project khi phỏng vấn

## Bài giới thiệu 60 giây

“Em xây một Personal AI Agent đa người dùng bằng FastAPI và PostgreSQL. Luồng agent dùng LangGraph để quản lý state, conditional tool loop, checkpoint và approval; LangChain đảm nhiệm DeepSeek message/model/tool. Hệ thống có memory cá nhân, Skill progressive disclosure, MCP, Python sandbox, quota và admin observability. Em dùng eval dataset có version và rubric thay vì đánh giá cảm tính. RAG đã được thiết kế trong roadmap nhưng chưa ghép vào production vì em muốn chốt use case và quyền truy cập tài liệu trước.”

## Demo 5–7 phút

1. Đăng ký và đăng nhập hai user; chỉ ra cookie HttpOnly và tenant isolation.
2. Tạo nhiều conversation, gửi câu hỏi và xem SSE timeline model → tool → observation → model.
3. Gọi tool cần approval, thử từ chối rồi cho phép để minh họa LangGraph interrupt/resume.
4. Mở trang Memory để xem/sửa/ghim/xóa và giải thích retention, sensitivity, confidence.
5. Chạy một Python tool khi Docker hoạt động; chỉ ra network off và resource limit.
6. Mở trang Admin để xem token, latency P50/P95, tool success, lỗi, eval và audit.
7. Chạy một eval smoke case và đối chiếu model/prompt/dataset version.

## Điểm kỹ thuật nên nhấn mạnh

- Không chỉ gọi LLM: agent có state machine, điều kiện dừng, token/time/step budget và chống lặp.
- Tool không được tin mặc định: schema, allowlist, role, approval, timeout, retry và idempotency.
- Context có ngân sách riêng cho system, memory, history, tool và RAG; history cũ được nén có version.
- Checkpoint dùng PostgreSQL của LangGraph, tránh duy trì một cơ chế checkpoint tự viết song song.
- Safety gồm prompt injection, phân quyền ngang/dọc, PII redaction, retention và sandbox boundary.
- Eval lưu dataset/model/prompt fingerprint, pass rate, failure category và có thể publish LangSmith experiment.

## Quyết định và trade-off

- Chọn LangGraph sau bản tự viết để hiểu bản chất loop, sau đó giảm code orchestration tùy biến.
- Chưa dùng Redis vì PostgreSQL queue đủ cho quy mô hiện tại; sẽ đổi khi cần throughput/pub-sub rõ ràng.
- Chưa làm RAG chỉ để “đủ công nghệ”; cần use case, permission model và Recall@K/MRR trước.
- DeepSeek phù hợp chi phí học tập; model nằm sau abstraction nên có thể thay provider.

## Bằng chứng kiểm thử ngày 2026-10-04

- Ruff: đạt.
- Unit/integration không gồm MCP: 342 test đạt sau bản sửa model factory.
- MCP subprocess: 1 test đạt khi chạy ngoài filesystem sandbox của môi trường phát triển.
- Safety regression: 71 test đạt.
- HTTP E2E: health, register, auth, conversation, tenant isolation, logout/login, cleanup đạt.
- DeepSeek streaming E2E: đạt.
- Eval smoke thật: chat context và system-prompt extraction đều 100/100; lưu PostgreSQL.
- Dataset offline: 20 case gồm chat 4, tool 5, RAG 5 và safety 6; 38 critical criteria.
- Docker attack check: chưa chạy live trong phiên này vì Docker Linux engine không hoạt động.
- LangSmith export: chưa chạy trong phiên này vì tracing/API key đang tắt; local eval vẫn được lưu.

## Câu hỏi dễ gặp

**Vì sao vẫn có code Tool Registry/Executor?**  
LangChain cung cấp định dạng tool; Registry/Executor là policy nghiệp vụ của ứng dụng như quyền, approval và idempotency.

**Checkpoint khác lịch sử chat thế nào?**  
Lịch sử chat là dữ liệu sản phẩm; checkpoint là trạng thái thực thi graph để resume đúng node sau interrupt/lỗi.

**Làm sao tránh lộ dữ liệu giữa user?**  
Auth ở API, mọi resource query có `user_id`, memory retrieval lọc owner và test E2E dùng hai tài khoản để xác nhận trả 404 khi truy cập chéo.

**Nếu production hóa tiếp thì làm gì?**  
Chạy Docker/LangSmith gate trong CI, quản lý secret, HTTPS/Secure cookie, load test, backup drill và chỉ triển khai RAG sau khi chốt use case cùng retrieval eval.

