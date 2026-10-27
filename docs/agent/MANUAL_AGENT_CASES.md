# Kiểm thử agent ReAct và M4

## Đợt 1 — Vòng chung cho hội thoại chưa chọn tài liệu

Bật `AGENT_REACT_ENABLED=true` trong `.env`, restart web. Windows chạy một worker,
không `--reload`. Docker dùng `DEV_AGENT_REACT_ENABLED=true` rồi recreate app.
Key/model/tool giữ cấu hình hiện có. Mở hội thoại mới **chưa chọn file** cho R01–R04;
R05 gửi tiếp trong hội thoại R04 để kiểm tra history.

Khi bật, model nhận toàn bộ tool được phép nếu schema nằm trong budget. Không gọi JEV
để phân bốn route hoặc chọn một tool. Executor vẫn kiểm quyền, tham số và approval.
Chế độ này chưa tự nạp Skill; kho lớn vượt schema budget báo lỗi thay vì giấu bớt tool.
Tắt flag chỉ áp dụng run mới; checkpoint giữ chế độ đã lưu.

| Case | Câu hỏi | Kỳ vọng |
| --- | --- | --- |
| R01 | Chỉ trả lời đúng chuỗi AGENT_REACT_OK, không gọi công cụ. | Đúng chuỗi, không tool |
| R02 | Tôi muốn gửi email nhưng chưa biết gửi cho ai và chưa có nội dung. Bạn cần tôi bổ sung gì? Chưa được gửi email hoặc tự điền thông tin. | Hỏi rõ, không gửi email |
| R03 | Dùng demo__count_text đếm số ký tự của chuỗi "abc def". Chỉ sau khi nhận kết quả, dùng demo__calculate nhân số ký tự đó với 17. Không tự đếm hay tính thay tool. Trả lời kết quả cuối. | Count 7 → model đọc observation → multiply 17 → kết quả 119 |
| R04 | Tìm trên website Python.org ngày phát hành chính thức Python 3.14.0. Chỉ cần một lần gọi search_web với câu hỏi này; trả lời bằng tiếng Việt kèm link nguồn. | Tìm thật, model đọc kết quả rồi trả 7/10/2025, link Python.org |
| R05 | Từ câu trả lời vừa rồi, Python 3.14.0 được phát hành năm nào? Không cần tìm lại. | Dùng history, trả 2025, không gọi lại tool |

Timeline của run ReAct hiển thị “Chuẩn bị Agent”, không gắn nhãn “Tìm kiếm tài liệu: Fast”.
Reload giữ câu trả lời, tool, token và chi phí search. Lượt tìm web không kết thúc ngay tại tool:
cần có lượt model tổng hợp sau observation.

## Runner gọi LLM thật

Xem câu hỏi mà không gọi API:

```powershell
.venv\Scripts\python.exe -X utf8 -m scripts.test_agent_live
```

Chạy bộ live có phát sinh phí:

```powershell
.venv\Scripts\python.exe -X utf8 -m scripts.test_agent_live --live
```

Runner tạo tài khoản/hội thoại fixture riêng, đi qua auth, message, SSE, history/run API
và PostgreSQL thật. DeepSeek/MCP/OpenRouter chạy thật; không gửi email. Dừng lô khi một
case lỗi. Report ở `data/agent-testing/<thời gian>-<id>/REPORT.json` (Git ignore), có prompt,
kết quả, run ID, thời gian, timeline và native search usage/cost. Không lưu key/password/cookie.
Đây là test API/ASGI cùng luồng backend của web, không phải thao tác browser thật.

Kết quả 2026-10-27: R01–R05 đạt, report
`data/agent-testing/20261027-123819-f08639e7/REPORT.md`.
Search một lượt, phí native ghi nhận 0.0153904 USD; chưa cộng DeepSeek/memory.

## Các đợt tiếp theo — Chưa nghiệm thu

- Đợt 2: nối capability tìm tài liệu vào vòng chung. Mọi case retrieval dùng bản
  **Hiểu sâu về AI Agent.pdf** đã sẵn sàng trong thư viện `user@personalagent.vn`;
  không dùng bộ Atlas/Boreal thay cho file này. Hiện chọn file vẫn chạy pipeline M3 cũ.
- Câu hỏi chuẩn bị: “Tài liệu giới thiệu những chiến lược chunking nào? Nêu ưu và nhược
  điểm”; “Theo bảng 3-2, so sánh ANNOY và HNSW về bộ nhớ, cập nhật gia tăng và tình huống
  sử dụng”; “Tại sao gọi là khóa lạc quan?”. Đối chiếu đoạn nguồn/trang thực tế, không
  coi kiến thức nền của model là bằng chứng đã đọc tài liệu.
- Đợt 3: lịch sử tool giữa các lượt, ngân sách và kết quả một phần. R05 hiện chỉ kiểm
  history câu trả lời; chưa chứng minh agent đọc được audit tool từ lượt trước. Ngân sách
  hiện vẫn 20.000 token/run; nhiều paid search có thể chạm giới hạn như trước.
- Đợt 4–6: planner/DAG tuần tự, worker/song song, background/resume và task UI.

Không chạy lại paid suite chỉ để kiểm tra một thay đổi tài liệu hoặc định dạng report.
