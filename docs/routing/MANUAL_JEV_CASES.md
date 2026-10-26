# Các tình huống JEV phân route trên web

JEV chọn một trong `chat`, `tool_task`, `document_qa`, `planned_task`.
Việc cần hỏi rõ được gộp vào `chat`, không còn Choice `clarify`.
Người dùng đã nghiệm thu phân loại; phần in route/xác suất và cờ preview đã được gỡ.
Hiện quyết định JEV nối vào luồng thực tế: chat dùng model hiện có, tool_task dùng
selector/agent hiện có; document_qa dùng pipeline M3 khi bật V2_RAG_ENABLED và chọn file ready;
planned_task còn trả thông báo chưa có lõi workflow.
Các yêu cầu tool trong bảng dưới có thể thực sự gọi tool, theo quyền và approval hiện có.

## Cách chạy

1. Trong `.env`, bật `V2_ENABLED=true`, `V2_ROUTING_ENABLED=true`,
   `ROUTING_MODE=active` và cấu hình `OPENROUTER_API_KEY` cùng model chat.
   Cờ preview đã bỏ; ngưỡng chọn tool hiện có giữ nguyên.
2. Restart backend, ví dụ:
   `.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000`.
   Windows dùng một web worker, không thêm --reload để hỗ trợ tiến trình model cục bộ.
3. Đăng nhập web và kiểm tra các kịch bản nhiều lượt ở cuối tài liệu: mỗi kịch bản dùng
   một hội thoại, gửi các lượt liên tiếp để kiểm tra history. Với bảng 30 case độc lập,
   mở hội thoại mới khi cần đúng điều kiện “chưa có context”; case L1/L2 gửi cùng hội thoại.
   Nếu gửi các case độc lập liên tiếp, đáp án có thể thay đổi khi history đã giải quyết
   thông tin mà câu mới đang thiếu.
4. Đối chiếu route trong trace admin với cột mong đợi. Chat chỉ hiện câu trả lời nghiệp vụ;
   không in tên route hoặc bảng xác suất. Các nhãn là đáp án tham khảo để đối chiếu.
5. Trong admin, trace `phase=query`, `provider=jev` có model/usage/latency thực.
   Model-turn/tool call có thể phát sinh theo nhánh đã chọn; token JEV vẫn được đo riêng.

Đầu vào là tối đa `CHAT_HISTORY_LIMIT` message gần nhất (mặc định 20), bao gồm query
mới nhất. Query được tách khỏi history, giữ nguyên role user/assistant và thứ tự;
system prompt, memory, tool schema/output không được đưa vào state phân loại.
Khi bật RAG, metadata tên/trạng thái file đang chọn được gửi qua bộ lọc privacy cùng số file;
không gửi nội dung/đường dẫn file cho JEV chỉ để phân route.
Các báo cáo preview cũ đã lưu vẫn là chẩn đoán trong history; không tự xóa dữ liệu đó.
Nên mở hội thoại mới khi chuyển sang dùng luồng thực tế để có history gồm câu trả lời thật.

## Quy tắc chọn

- Một Choice gồm bốn lựa chọn; nội dung chuẩn nằm trong
  `agent_core/routing/taxonomy.py`, chỉ dẫn nằm trong `query_router.py`.
- `chat` gồm trò chuyện, giải thích, soạn thảo, xử lý văn bản user dán trực tiếp và hỏi lại khi
  thiếu dữ kiện thiết yếu/tham chiếu chưa rõ sau khi đã xét history. Không hỏi lại
  dữ kiện đã có. Nhánh chat dùng model hiện có để trả lời hoặc hỏi rõ.
- Hỏi nối tiếp, giải thích hoặc xác nhận thông tin trong file đã chọn là document_qa, kể cả
  câu trả lời đã có trong history. Câu trả lời tài liệu trước đó không được coi là văn bản user
  dán trực tiếp để chuyển sang chat. Câu hỏi không liên quan file vẫn có thể chọn chat/tool_task;
  yêu cầu kết hợp tài liệu với web/mail/phối hợp bước là planned_task.
- Chọn xác suất cao nhất, kể cả khi thấp; không dùng confidence floor hoặc margin.
- Hai hay nhiều lựa chọn **bằng nhau chính xác** ở xác suất cao nhất → `chat` để hỏi rõ.
  Trace vẫn giữ xác suất gốc; không sửa xác suất của `chat` thành 1.
- Hai xác suất gần nhau nhưng khác nhau vẫn chọn phương án cao hơn.
- Timeout, lỗi API, dữ liệu quá dài hoặc hết budget hiển thị lỗi; không giả route,
  không chuyển sang LLM fallback. Không có câu query bảo đảm model sẽ trả đồng hạng.

Giới hạn riêng của phân query: `ROUTING_QUERY_MAX_STATE_CHARS=32768`,
`ROUTING_QUERY_MAX_PAYLOAD_BYTES=131072`; budget call/token vẫn dùng cấu hình routing
hiện có. Vượt giới hạn sẽ báo lỗi, không cắt mất query/history. Cấu hình privacy/redact
credential và email tiếp tục áp dụng cho cả query và history.

Trong nhánh `tool_task`, bước chọn tool cũng nhận cùng history user/assistant gần nhất
với node phân query, giữ thứ tự và tách query mới nhất vào `goal`. History được lưu trong
checkpoint để tiếp tục sau phê duyệt không lấy nhầm ngữ cảnh mới. System prompt, tool
arguments/output và memory không được thêm vào history gửi provider; observations chỉ có
trạng thái tool. Giới hạn bước chọn tool: `ROUTING_MAX_STATE_CHARS=32768`,
`ROUTING_MAX_PAYLOAD_BYTES=131072`; vượt giới hạn không tự cắt nội dung.

Để kiểm tra chọn tool theo history trong cùng hội thoại:

1. Gửi: “Soạn email đến địa chỉ nhận của tôi, tiêu đề Kiểm tra, nội dung Xin chào.
   Chỉ soạn, chưa gửi.” Thay “địa chỉ nhận của tôi” bằng địa chỉ bạn muốn dùng để test.
2. Sau khi agent trả bản nháp, gửi: “Gửi email đó đi.” Mong đợi chọn `send_email`,
   tạo tham số từ nội dung trước và hiện thẻ phê duyệt. Chỉ gửi thật khi bạn cho phép.

## 30 tình huống

L1/L2 là hai message user. Route trong ngoặc ở L1 là kết quả mong đợi cho lượt đầu;
cột mong đợi áp dụng cho lượt cuối cùng của case.

| ID | Message gửi trên web | Route mong đợi | Điểm cần kiểm tra |
| --- | --- | --- | --- |
| C01 | Giải thích dependency injection là gì, kèm một ví dụ đơn giản. | `chat` | Giải thích kiến thức, không thực thi. |
| C02 | Viết giúp tôi một email xin nghỉ phép ngày mai. Chỉ soạn nội dung, chưa gửi. | `chat` | Soạn email không phải gửi email. |
| C03 | Lập kế hoạch học Python trong bốn tuần. Chỉ viết kế hoạch, không thực hiện thao tác nào. | `chat` | Từ “kế hoạch” không tự thành planner. |
| C04 | Tóm tắt đoạn sau: “Nhóm đã hoàn thành chức năng đăng nhập. Phần upload tài liệu sẽ được xây dựng trong giai đoạn tiếp theo.” | `chat` | Văn bản có sẵn không cần retrieval. |
| C05 | Không tìm trên web và không gọi công cụ. Hãy giải thích cách một công cụ tìm kiếm hoạt động. | `chat` | Phủ định hành động; chỉ giải thích. |
| C06 | L1: Tìm trên web các bài viết mới về Python (`tool_task`). L2: Không tìm nữa, chỉ giải thích list comprehension bằng kiến thức sẵn có. | `chat` | Query mới hủy ý định tool trước đó. |
| T01 | Dùng công cụ tính chính xác 125 nhân 48. | `tool_task` | Một phép tính qua tool. |
| T02 | Tìm trên web thông báo phát hành Python mới nhất. | `tool_task` | Tra dữ liệu hiện tại. |
| T03 | Dùng công cụ đọc trạng thái issue số 42 trong repository example/project. | `tool_task` | Một lookup với đối tượng rõ. |
| T04 | Liệt kê tên các file trong thư mục Reports bằng công cụ, không đọc nội dung file. | `tool_task` | Metadata file khác document QA. |
| T05 | Gửi email cho an@example.test, tiêu đề “Họp nhóm”, nội dung “Họp lúc 9 giờ sáng mai”. | `tool_task` | Đủ thông tin để phân loại hành động; tool ghi phải qua approval. |
| T06 | L1: Chỉ giải thích phép nhân, không cần công cụ (`chat`). L2: Bây giờ dùng công cụ tính 327 nhân 19. | `tool_task` | Query mới thay đổi yêu cầu không dùng tool trước đó. |
| D01 | Tóm tắt phần kết luận trong tài liệu BaoCaoQ3.pdf. | `document_qa` | Tên tài liệu rõ; không đòi lõi retrieval đã có. |
| D02 | Trong tài liệu HopDongA.pdf, điều khoản thanh toán quy định những gì? Trích nguồn giúp tôi. | `document_qa` | Hỏi nội dung cần tìm trong tài liệu. |
| D03 | So sánh điều khoản bảo hành của HopDongA.pdf và HopDongB.pdf, kèm đoạn nguồn. | `document_qa` | Hai tài liệu không tự thành planner. |
| D04 | Tìm các đoạn nói về thời gian thử việc trong SoTayNhanVien.pdf. | `document_qa` | Tìm bằng chứng trong tài liệu. |
| D05 | Dựa vào BaoCaoQ3.pdf, doanh thu quý ba là bao nhiêu và số đó nằm ở trang nào? | `document_qa` | Đáp án dựa trên nguồn tài liệu. |
| D06 | L1: Tài liệu tôi muốn hỏi là HuongDanSuDung.pdf; hãy tóm tắt nó (`document_qa`). L2: Chỉ tóm tắt chương hai của tài liệu đó. | `document_qa` | History giải quyết tham chiếu “tài liệu đó”. |
| P01 | Đọc BaoCaoQ3.pdf, tìm số liệu thị trường mới trên web, đối chiếu hai nguồn rồi tạo báo cáo tổng hợp. | `planned_task` | Nhiều nguồn và bước phụ thuộc. |
| P02 | Tìm giá sản phẩm A trên ba website, chuẩn hóa đơn vị tiền tệ, so sánh rồi tạo file báo cáo. | `planned_task` | Lookup, xử lý dữ liệu và artifact. |
| P03 | Đọc danh sách khách hàng trong KhachHang.csv, tra trạng thái từng đơn hàng rồi tổng hợp các đơn bị chậm vào một báo cáo. | `planned_task` | Fan-out theo khách hàng rồi tổng hợp. |
| P04 | Kiểm tra hai repository example/api và example/web song song, thu thập issue đang mở rồi tạo bảng so sánh mức độ ưu tiên. | `planned_task` | Điều phối nhánh độc lập và tổng hợp. |
| P05 | Tìm các báo cáo quý của công ty A, trích doanh thu từng quý, tính mức tăng trưởng rồi tạo biểu đồ và báo cáo. | `planned_task` | Chuỗi retrieval, phân tích và xuất kết quả. |
| P06 | L1: Viết kế hoạch đối chiếu BaoCaoA.pdf với BaoCaoB.pdf và số liệu web (`chat`). L2: Bây giờ thực hiện: đọc hai tài liệu đó, tìm số liệu mới trên web, đối chiếu và tạo báo cáo. | `planned_task` | Chuyển từ viết kế hoạch sang thực thi. |
| Q01 | Gửi cái đó cho anh ấy. | `chat` | Cần hỏi rõ nội dung và người nhận. |
| Q02 | Đọc file đó giúp tôi. | `chat` | Chat mới chưa xác định file, cần hỏi rõ. |
| Q03 | Tìm kiếm giúp tôi. | `chat` | Chưa có chủ đề tìm kiếm. |
| Q04 | Làm tiếp như lúc nãy nhé. | `chat` | Chat mới không có tác vụ trước đó. |
| Q05 | Xóa bản ghi bị nhầm giúp tôi. | `chat` | Cần hỏi rõ bản ghi trước khi thực hiện. |
| Q06 | L1: Tôi có hai tài liệu BaoCaoA.pdf và BaoCaoB.pdf, hãy so sánh chúng (`document_qa`). L2: Chỉ đọc và tóm tắt một tài liệu thôi. | `chat` | History có hai đối tượng, cần hỏi user chọn tài liệu nào. |

## Kịch bản nhiều lượt trong cùng hội thoại

Mở hội thoại mới khi bắt đầu mỗi kịch bản. Gửi các lượt trong một bảng liên tiếp;
không mở chat mới giữa các lượt. Đây là đáp án để bạn đối chiếu, không dán cả bảng
hoặc nhãn mong đợi vào ô chat. Gửi lượt bổ sung thông tin như bên dưới;
nhánh chat hiện có thể hỏi rõ trực tiếp dựa trên hội thoại.

### Kịch bản A: thiếu thông tin → có thông tin → đổi ý

| Lượt | User gửi | Route mong đợi | Điểm cần kiểm tra |
| --- | --- | --- | --- |
| 1 | Đọc file đó giúp tôi. | `chat` | Chưa có file nào trong history, cần hỏi rõ. |
| 2 | Tôi muốn hỏi tài liệu BaoCaoA.pdf; hãy tóm tắt phần kết luận. | `document_qa` | User đã xác định đối tượng. |
| 3 | Chỉ lấy kết luận ở chương hai của tài liệu đó. | `document_qa` | History xác định BaoCaoA.pdf, không hỏi lại tên file. |
| 4 | Không đọc nữa. Chỉ giải thích kết luận của một báo cáo thường gồm những gì. | `chat` | Query mới hủy yêu cầu đọc tài liệu. |
| 5 | Bây giờ tìm trên web các hướng dẫn viết báo cáo mới nhất. | `tool_task` | Đổi sang tra cứu nguồn ngoài. |

### Kịch bản B: bổ sung đầu vào và chuyển sang workflow

| Lượt | User gửi | Route mong đợi | Điểm cần kiểm tra |
| --- | --- | --- | --- |
| 1 | Tìm kiếm giúp tôi. | `chat` | Thiếu chủ đề, cần hỏi rõ. |
| 2 | Tôi muốn tìm trên web thông báo phát hành Python mới nhất. | `tool_task` | Bổ sung đủ chủ đề tìm kiếm. |
| 3 | Không tìm nữa; viết kế hoạch đối chiếu BaoCaoA.pdf, BaoCaoB.pdf và dữ liệu web. Chỉ viết kế hoạch. | `chat` | Viết kế hoạch không phải thực thi workflow. |
| 4 | Bây giờ thực hiện: đọc hai tài liệu đó, tìm số liệu mới trên web, đối chiếu rồi tạo báo cáo. | `planned_task` | Thực thi các bước phối hợp nguồn; hiểu tên tài liệu từ history. |
| 5 | Thu hẹp lại: chỉ so sánh phần kết luận trong hai tài liệu đó, kèm nguồn; không dùng web. | `document_qa` | Query mới thu hẹp sang QA tài liệu. |

## Kết thúc đợt kiểm tra

Đợt kiểm tra preview đã hoàn tất theo xác nhận của người dùng. Code sinh bảng route/xác suất
và cờ preview đã gỡ. Chat dùng nhánh hiện có; tool_task giữ bộ chọn hiện có để tiếp tục bàn
thiết kế Choice động/một hoặc nhiều tool. Hai nhánh chưa có lõi trả thông báo tương ứng.
Trace/admin vẫn lưu kết quả routing; không xóa hội thoại hoặc trace đã tạo.
