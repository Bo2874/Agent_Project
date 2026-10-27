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

## Đợt 2 — Tài liệu trong vòng ReAct

Giữ `AGENT_REACT_ENABLED=true`, `V2_ENABLED=true`, `V2_RAG_ENABLED=true`. Trong chat chọn
**Hiểu sâu về AI Agent.pdf** của `user@personalagent.vn` (421 trang, 1.389 chunk đã ready).
Không upload/re-index lại. Agent nhận `search_documents` và `summarize_documents` chỉ trong
registry riêng của run; backend giữ owner, file/version và Fast/Deep, model chỉ truyền query.
Nếu có override `TOOL_ALLOWLIST`, thêm hai tên này. Tắt ReAct vẫn dùng nhánh M3 cũ.

`search_documents`: một query độc lập do Agent viết theo history, không thêm lượt LLM rewrite.
Dense20 + keyword20 → RRF top10; Deep rerank10. Evidence và metadata thư viện nằm trong
vùng RAG tối đa8.000 token; observation không chép lại text chunk. Một lần tìm cụ thể hơn
thay vùng evidence hiện tại, giữ audit cũ trong ToolCall và khử trùng lặp nguồn. Footer lưu
các chunk ở context cuối (tối đa10) cộng lineage của summary nếu có, không lấy nhãn nguồn từ LLM.

Run có file dùng `AGENT_DOCUMENT_MAX_TOTAL_TOKENS=60000` và deadline ít nhất bằng
`DOCUMENT_QA_TIMEOUT_SECONDS` (host hiện600 giây); run không file giữ20.000/120 giây.
Đây là hạn mức, không phải số token chắc chắn tiêu thụ. Dự trù/settle quota toàn run sẽ làm ở đợt3.

| Case | Câu hỏi trên web | Kỳ vọng |
| --- | --- | --- |
| D01 · Fast | Theo bảng 3-2, so sánh ANNOY và HNSW về bộ nhớ, cập nhật gia tăng và tình huống sử dụng. Chỉ dựa trên tài liệu đã chọn. | Search thật, ANNOY thấp/không cập nhật gia tăng; HNSW cao/có hỗ trợ, nguồn tr.119 |
| D02 · cùng hội thoại D01 | Vì sao loại thứ hai phù hợp hơn khi cần cập nhật gia tăng? Chỉ dựa trên tài liệu, hãy tìm lại bằng chứng cho câu hỏi nối tiếp này. | Resolve thành HNSW, gọi retrieval mới, không dùng lời chat cũ làm bằng chứng |
| D03 · Fast | Theo tài liệu, HNSW và ANNOY khác nhau thế nào về cập nhật gia tăng? Sau đó tìm tài liệu chính thức hnswlib trên GitHub để đối chiếu khả năng thêm phần tử. Nêu rõ phần từ sách và phần từ web. | Có doc + web trong cùng run, link GitHub và nguồn PDF; có thể tìm cụ thể hơn nếu thiếu bảng, không tự bù nội dung thiếu |
| D04 · Deep | Tài liệu giới thiệu những chiến lược chunking nào? Nêu ưu và nhược điểm của từng loại, chỉ theo nội dung tài liệu. | Rerank tối đa10 cặp, nguồn tr.116–117; cố định/đệ quy/ngữ nghĩa; thiếu ưu/nhược điểm nào phải nêu rõ |
| D05 | Tóm tắt toàn bộ file đã chọn bằng luồng tóm tắt toàn văn. Nếu vượt giới hạn, chỉ thông báo giới hạn đó; không thay bằng tìm Top-k rồi tóm tắt. | `summarize_documents` báo `document_summary_limit`; file1389 chunk vượt cap64, không giả tóm tắt toàn bộ |
| D06 | Theo tài liệu đã chọn, số tài khoản ngân hàng cá nhân của tác giả là gì? Nếu các đoạn tìm được không nêu, hãy nói không có đủ thông tin, không suy đoán. | Retrieval thật nhưng không bịa số tài khoản từ các chunk không liên quan |

R01–R04/D01/D03–D06 dùng hội thoại mới; D02 giữ hội thoại D01. Nguồn cuối mở đúng chunk/trang,
reload giữ được nguồn. Một user khác không được chọn file, đọc hội thoại hoặc mở citation này.

Xem case, không gọi API:

```powershell
.venv\Scripts\python.exe -X utf8 -m scripts.test_agent_live --suite documents
```

Chạy thật trên file đã upload, tạo **hội thoại thử mới trong chính tài khoản owner**:

```powershell
.venv\Scripts\python.exe -X utf8 -m scripts.test_agent_live --suite documents --owner-email user@personalagent.vn --document-id bf811f8e-bce3-4119-86a9-f72e75de6002 --live
```

Runner dùng cookie ký bởi backend và kiểm user thật trong DB; không đổi mật khẩu/override auth,
không sửa hội thoại có sẵn. Thêm `--case D03` để chỉ chạy một case; D02 riêng dùng câu độc lập,
nên muốn kiểm history phải chạy cả bộ. Report chứa dữ liệu PDF riêng ở `data/agent-testing`
được Git ignore; không chia sẻ report ngoài phạm vi bạn cho phép.

Kết quả ngày2026-10-27:6/6 case luồng đạt, có Fast/Deep, nguồn/SSE/history và kiểm isolation.
Deep trên CPU khoảng91 giây. Đây không phải benchmark Recall/độ đúng của mọi câu trả lời:
Fast có lần chỉ lấy đoạn giới thiệu bảng và model nêu rõ thiếu ô bảng; query cụ thể hơn đã
lấy được nội dung đúng. Xem report tổng hợp `data/agent-testing/stage2-20261027/REPORT.md`.
Tóm tắt các file nhỏ vẫn dùng map/reduce/cap64 cũ; kiểm graph tự động, không tạo file retrieval khác.

Trong ReAct, timeline/tool/progress tiếp tục phát realtime. Text model được giữ tới khi xác định
lượt đó trả lời cuối hay gọi tool, tránh lời dẫn hành động lẫn vào đáp án; chữ đáp án xuất sau
khi model hoàn tất lượt cuối. Chưa có UI riêng để stream provisional/final text theo hai kênh.

## Các đợt tiếp theo — Chưa nghiệm thu

- Đợt 3: lịch sử tool giữa các lượt, ngân sách và kết quả một phần. R05 hiện chỉ kiểm
  history câu trả lời; chưa chứng minh agent đọc được audit tool từ lượt trước. Nhiều paid
  search vẫn có thể chạm giới hạn; tăng hạn mức cho run tài liệu không thay reservation/partial result.
- Đợt 4–6: planner/DAG tuần tự, worker/song song, background/resume và task UI.

Không chạy lại paid suite chỉ để kiểm tra một thay đổi tài liệu hoặc định dạng report.
