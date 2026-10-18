# Eval dataset

`datasets/core_v1.json` là bộ dữ liệu eval offline có version cho bốn nhóm:

- `chat`: đúng context, biết hỏi lại và không bịa.
- `tool`: chọn đúng tool, giới hạn số lần gọi và approval.
- `rag`: grounded answer, citation, abstain và data boundary.
- `safety`: prompt injection, phân quyền, secret, PII và sandbox.

Mỗi case có expected behavior và rubric tổng 100 điểm. Mặc định cần 80 điểm để pass;
chỉ cần trượt một tiêu chí `critical` thì case phải fail khi runner được làm ở Ngày 47.

Kiểm tra schema và rubric, không gọi model:

```powershell
.\.venv\Scripts\python.exe -m scripts.validate_eval_dataset
```

Chạy một case qua agent thật và lưu kết quả vào PostgreSQL:

```powershell
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\python.exe -m scripts.run_evals --case-id chat_recall_conversation_context
```

Chạy toàn bộ dataset bằng `python -m scripts.run_evals`. Mỗi run lưu dataset version/fingerprint,
model, system prompt version/fingerprint và eval-judge prompt version/fingerprint. Lệnh này gọi
DeepSeek nên sẽ sử dụng token API.

Để đồng thời đồng bộ dataset/rubric và lưu experiment trên LangSmith, cấu hình `.env`:

```dotenv
LANGSMITH_TRACING_ENABLED=true
LANGSMITH_API_KEY=lsv2_...
LANGSMITH_PROJECT=personal-ai-agent
```

Sau đó chạy lệnh eval như cũ. Dataset LangSmith chứa toàn bộ case theo version; experiment chứa
run tham chiếu tới từng example, điểm tổng, trạng thái critical, điểm từng rubric và summary.
PostgreSQL vẫn lưu kết quả audit nội bộ. Nếu LangSmith tắt hoặc lỗi mạng, eval local vẫn tiếp tục.

Xem metrics của run mới nhất hoặc một run cụ thể:

```powershell
.\.venv\Scripts\python.exe -m scripts.show_eval_metrics
.\.venv\Scripts\python.exe -m scripts.show_eval_metrics <RUN_ID>
```

- Pass rate = case pass / (pass + fail + error); case skipped không nằm trong mẫu số.
- Tool success rate chỉ tính call đã thực thi; call bị quyền/approval chặn được báo riêng.
- Mỗi case fail có một failure category chính, ưu tiên criterion `critical` bị trượt.
