# Skill format v1

Mỗi Skill là một thư mục có đúng hai file bắt buộc:

```text
skill_name/
├── skill.json
└── instructions.md
```

`skill.json` chứa metadata nhỏ để tìm kiếm trước khi nạp hướng dẫn:

```json
{
  "schema_version": 1,
  "metadata": {
    "name": "research_summary",
    "version": "1.0.0",
    "description": "Tìm nguồn và viết bản tóm tắt có dẫn chứng.",
    "tags": ["research", "summary"]
  },
  "required_tools": ["web_search", "document_reader"]
}
```

`instructions.md` chứa quy trình Agent phải làm khi Skill được chọn. Hướng dẫn nên nêu rõ:

- Khi nào dùng và khi nào không dùng Skill.
- Các bước thực hiện theo thứ tự.
- Điều kiện kiểm tra đầu ra và điều kiện dừng.
- Cách xử lý khi tool lỗi hoặc thiếu dữ liệu.

Quy ước:

- `name` và tên tool dùng chữ thường, số và dấu gạch dưới.
- `version` dùng Semantic Versioning, ví dụ `1.0.0`.
- `description` ngắn, đủ để tìm Skill mà chưa cần đọc `instructions.md`.
- `required_tools` chỉ khai báo dependency; Tool Registry vẫn quyết định allowlist, role và approval.
- Không đặt API key, mật khẩu hoặc secret trong hai file Skill.
- Skill là cấu hình tin cậy do ứng dụng quản lý; không nạp trực tiếp file do user upload.

## Luồng Registry và progressive disclosure

```python
from agent_core.skills import SkillRegistry

registry = SkillRegistry()
registry.discover("skills")  # Chỉ đọc skill.json

available_tools = {spec.name for spec in tool_registry.list_accessible_specs(user_role)}
matches = registry.search(query, available_tools=available_tools)
selected = registry.load(matches[0].metadata.name, available_tools=available_tools)
# instructions.md chỉ được đọc ở bước load phía trên.
```

Registry chỉ tìm Skill có điểm khớp lớn hơn 0 và mặc định loại Skill đang thiếu tool.
`include_unavailable=True` chỉ dùng để chẩn đoán. Skill đã load được cache trong process;
khi file thay đổi cần đăng ký lại Registry.

## Đánh giá Skill

Chạy benchmark xác định, không gọi LLM:

```powershell
.\.venv\Scripts\python.exe -m scripts.evaluate_skills
```

- Selection eval so sánh Skill được chọn với nhãn mong đợi; ngưỡng điểm và khoảng
  cách chống mơ hồ ngăn Skill yếu hoặc hai Skill gần bằng nhau tự động được nạp.
- Adherence eval kiểm tra Skill đã chọn, thứ tự tool, tool ngoài dependency, giới hạn
  tool call, lỗi tool và các mục bắt buộc/cấm trong câu trả lời.
- Khi chạy thật, Agent chỉ nhận Skill sau khi danh sách `required_tools` đã qua
  allowlist và role của Tool Registry.
