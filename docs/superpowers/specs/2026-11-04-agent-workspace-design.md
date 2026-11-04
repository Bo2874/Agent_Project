# Thiết kế mở rộng Personal Agent

Ngày: 2026-11-04. Trạng thái: người dùng đã duyệt ngày 2026-11-04; đang lập kế hoạch, chưa triển khai sản phẩm.

## 1. Mục tiêu và cách đọc

Mở rộng project hiện có thành không gian làm việc: giao tiếp qua chat, tạo/sửa tài liệu, thao tác trên tài liệu Google được chọn và thực hiện nhiệm vụ theo lịch. Giữ hệ thống đa người dùng, Agent runtime, Tool Registry, RAG, memory và audit đã có.

Các mục ghi **đã thống nhất** là quyết định trực tiếp trong cuộc thảo luận. Các mục ghi **đề xuất** là lựa chọn của người thiết kế được người dùng chấp thuận khi duyệt toàn bộ tài liệu. Bản phác thảo dùng dữ liệu mẫu, không chứng minh chức năng backend đã triển khai.

## 2. Những quyết định đã thống nhất

| Chủ đề | Quyết định |
| --- | --- |
| Thứ tự module | Artifacts → Connectors → Automations |
| Thứ tự Artifacts | Excel → Word → PDF |
| Tương tác tài liệu | Tạo và chỉnh sửa qua chat; chưa có trình biên tập trực tiếp |
| Nguồn tài liệu | Cả file người dùng đưa vào và file Agent tạo |
| PDF | Có thể dàn lại trang, miễn đáp ứng yêu cầu nội dung/định dạng |
| Ảnh | Xử lý văn bản/dữ liệu trước; OCR, PDF scan và xử lý nội dung ảnh để sau |
| Bộ xử lý Artifacts | Kết hợp công cụ chuyên biệt và Python trong sandbox |
| Google đầu tiên | Sheets: đọc/phân tích, thêm dữ liệu, chỉnh sửa bảng đang có |
| Quyền Google | Người dùng chọn từng file; không tìm trên toàn Drive |
| Cách tích hợp Google | Google API trực tiếp, đăng ký thao tác vào Tool Registry |
| Automation | Theo lịch trước; chạy thử, bật/tắt và lịch sử |
| Chính sách chạy | Chưa đặt ngân sách token hay thời gian tổng riêng; chạy đến khi hoàn thành, gặp lỗi hoặc người dùng dừng |
| Dừng và tắt lịch | Hai thao tác riêng; khi tắt lịch có lượt đang chạy, cho chọn giữ chạy hoặc dừng luôn |
| Bật lại lịch | Chờ mốc tiếp theo, không tự chạy bù khoảng đã tắt |
| Bố cục | A: chat chiếm vùng chính, bấm file mới mở preview bên cạnh |
| Preview | Đóng thì chat rộng lại; tạo file/phiên bản mới không tự mở panel đang đóng |
| Typography | Cách phối đã xem: Inter cho menu/nút, Lora cho tiêu đề/nội dung |

## 3. Các hướng đã cân nhắc

### Artifacts

1. Chỉ công cụ cố định: kiểm soát rõ, nhưng phải thêm nhiều thao tác để xử lý yêu cầu mới.
2. Chỉ Python sinh tự do: linh hoạt, nhưng phụ thuộc nhiều vào kiểm tra code/kết quả.
3. Kết hợp: công cụ cấu trúc cho thao tác phổ biến, code trong sandbox cho trường hợp phức tạp. **Người dùng chọn hướng 3.**

### Google Connectors

1. Google API trực tiếp trong backend: chủ động quản lý file được chọn, thao tác đọc/ghi và audit.
2. MCP server: tận dụng transport có sẵn, nhưng phải chọn/tự xây server đáp ứng cùng yêu cầu quyền và thao tác.

**Người dùng chọn API trực tiếp.** Các dịch vụ khác vẫn có thể dùng MCP; lựa chọn này không thay thế plugin platform hiện có.

### Bố cục

A ưu tiên chat, preview theo yêu cầu; B giữ tài liệu mở rộng cạnh chat. **Người dùng chọn A**, đồng ý cách phối font đã xem. Tham khảo Claude về cảm giác đọc và vị trí thao tác, giữ thương hiệu Personal Agent.

## 4. Nền tảng hiện có và phần phải mở rộng

- Frontend Jinja2/HTML/CSS/JavaScript; đề xuất tiếp tục dùng, chưa cần chuyển framework.
- PostgreSQL queue có claim, lease, heartbeat, retry và cancel; worker riêng có Agent root jobs.
- AgentRun, checkpoint, operation journal, approval và replay SSE đã có. Kết quả ngoài chưa rõ không được xem là đã thành công hoặc tự phát lại.
- AgentRun hiện gắn với Conversation. Nhiệm vụ theo lịch cần lớp tạo input/run độc lập với kết nối HTTP và vòng đời tab.
- Plugin có owner-scoped connection, credential mã hóa và OAuth. Các bảng/flow này đang gắn với plugin/MCP; không thể coi đã có Google OAuth dùng trực tiếp.
- Thư viện RAG có local storage, kiểm tra file và download theo owner. Đây là nền tảng dùng lại, chưa phải Artifact Manager.
- Python sandbox hiện chỉ trả file text nhỏ, chưa hỗ trợ nhập/xuất Office/PDF nhị phân và chưa có thư viện tạo tài liệu.
- Runtime hiện vẫn có trần token toàn run: `AgentLimits.max_total_tokens` là số nguyên. Chính sách Automation không ngân sách tổng cần thay đổi có chủ đích ở các chỗ cấp phát/reserve/checkpoint; không chỉ bỏ một trường UI.
- Docker ingestion hiện cài LibreOffice Writer; không được giả định đã có Calc để tính lại Excel.

Các nhận định trên dựa vào đọc code; không phải nghiệm thu runtime live hay tải phân tán.

## 5. Trải nghiệm giao diện

### Điều hướng và chat

Đề xuất dùng shell chung: sidebar có tạo chat, tìm kiếm, trò chuyện, Artifacts, Connectors, Automations và hội thoại gần đây. Thư viện RAG, Bộ nhớ và tài khoản nằm ở vùng phụ. Giữ khả năng truy cập quản trị theo role.

Màn hình chat mới có khung nhập ở trung tâm; trong hội thoại khung nhập ở dưới. Nút thêm file/công cụ ở trái, model và gửi/dừng ở phải. Sidebar thu gọn được. Nền sáng ấm và icon nét mảnh theo bản mẫu; đề xuất giữ tùy chọn tối và bảo đảm tương phản, focus, bàn phím.

Một file xuất hiện thành thẻ trong chat. Chỉ bấm thẻ hoặc chọn file trong thư viện mới mở preview. Nhận file mới hoặc bản sửa không tự mở panel đang đóng; panel đang mở có thể cập nhật bản vừa hoàn tất. Đóng panel không dừng Agent. Trên màn hình hẹp, preview chiếm vùng nội dung và có nút quay lại chat.

Font Inter/Lora là font công khai dùng để mô phỏng, không tuyên bố là font gốc của Claude. Đề xuất đóng gói font cùng ứng dụng để giao diện không phụ thuộc tải Google Fonts mỗi lần mở.

### Thư viện và nguồn file

Đề xuất Artifacts có tìm kiếm, lọc loại file, nguồn tạo, phiên bản và link về cuộc trò chuyện/run. Thư viện RAG tiếp tục quản lý nguồn hỏi đáp; một file chỉ được index RAG khi có hành động rõ ràng, không tự index mọi output.

**Đề xuất luồng thêm file:** giữ upload tại thư viện theo quy ước hiện có của project; nút thêm file trong chat mở bộ chọn tài liệu sẵn có và lối đi tới upload. Bản mẫu mới có nút thêm file minh họa, chưa phải quyết định thay quy ước upload hiện hành. Chọn file cho xử lý phải phân biệt với trạng thái sẵn sàng RAG: không bắt file Excel chờ index RAG mới được chỉnh sửa.

## 6. Artifacts

### Phạm vi đầu tiên — đề xuất chi tiết

- `.xlsx`: dữ liệu, công thức phổ biến, nhiều sheet, định dạng bảng và biểu đồ cơ bản từ dữ liệu.
- `.docx`: nội dung, heading, danh sách, bảng, style và bố cục văn bản.
- `.pdf`: PDF có lớp văn bản; đọc/sửa nội dung rồi xuất PDF có thể dàn lại.
- Định dạng cũ `.xls`/`.doc`, macro/VBA, embedded objects, công thức liên kết ngoài và xử lý ảnh chưa thuộc cam kết đầu tiên. Phải báo rõ khi file/yêu cầu nằm ngoài khả năng, không âm thầm chuyển thành kết quả thiếu nội dung.
- Với ảnh xen kẽ: đề xuất không đọc/sửa ảnh; giữ khi đường xử lý cho phép. Nếu không giữ được và ảnh có thể mất, báo trước và cho người dùng chọn trước khi xuất bản sửa. PDF scan thuần ảnh chưa được nhận là đã trích xuất đủ.

### Vòng đời và phiên bản — đề xuất

`Artifact` là tài liệu logic; `ArtifactVersion` là file bất biến. Giữ bản gốc, mỗi sửa thành công tạo bản mới cùng lineage, có parent version, run, yêu cầu và tóm tắt thay đổi. Cho sửa tiếp từ phiên bản cụ thể, không chỉ bản cuối. Hai yêu cầu đồng thời từ cùng parent tạo hai nhánh phiên bản rõ ràng, không âm thầm ghi đè.

File nhị phân nằm trong private storage; DB/checkpoint giữ ID, metadata, checksum và trạng thái, không chứa toàn bộ bytes hoặc đường dẫn host do model cung cấp. Download/preview luôn xác minh owner. Ban đầu đề xuất local storage qua interface, mở đường cho S3/MinIO sau; chưa bắt buộc thêm service lưu trữ.

Quy trình: nhận file/ID đã xác thực → kiểm tra định dạng và giới hạn tài nguyên → xử lý → kiểm tra kết quả → lưu file → công bố version và event. Kết quả hỏng không được công bố như bản hoàn tất. Dọn file mồ côi có quy trình; không xóa bản gốc khi sửa thất bại.

### Bộ xử lý

Tool thao tác phổ biến nhận dữ liệu cấu trúc: đọc vùng, sửa ô, thêm sheet/công thức, cập nhật đoạn/bảng. Python linh hoạt chỉ dùng khi cần. Cả hai đi qua cùng quản lý input/output, quyền và audit.

Đề xuất dùng openpyxl cho sửa/tạo workbook, python-docx cho Word; bộ xuất PDF/render được chọn trong kế hoạch. File đang có cần kiểm tra khả năng giữ thành phần trước khi sửa, không hứa round-trip mọi tính năng Office.

Python sinh tự do chạy trong container cách ly, không credential hoặc mạng. Input là file được cấp cho đúng lần chạy, bản input chỉ đọc; output vào vùng riêng. Giữ giới hạn CPU/RAM/thời gian **mỗi sandbox call**, điều chỉnh profile cho tài liệu theo phép đo. Chính sách không hạn token/thời gian tổng của Agent không xóa giới hạn tài nguyên của sandbox.

Sandbox contract cần mở rộng để truyền file nhị phân qua kênh có giới hạn tới storage, không nhét base64 file lớn vào tool observations. Model chỉ đọc metadata và nội dung/vùng đã trích cần thiết. Renderer/parser xử lý tài liệu ngoài tiến trình web, có giới hạn riêng.

### Chất lượng và preview

Excel phải phân biệt công thức và giá trị cache. openpyxl không tự tính công thức. Đề xuất có bước tính lại bằng engine riêng cho tập công thức được hỗ trợ; chốt engine ở kế hoạch sau kiểm tra tương thích và bổ sung Calc nếu dùng LibreOffice. Không hiển thị cache cũ như kết quả vừa tính. Công thức chưa hỗ trợ phải có trạng thái rõ, không bịa số.

Preview Excel là bảng/vùng dữ liệu, sheet và biểu đồ cơ bản; không phải Excel editor. Word/PDF có preview được render từ đúng version đã lưu. Với PDF tạo mới, đề xuất lưu thêm nguồn có thể sửa. Cập nhật phiên bản phải đổi cả preview và file download đúng ID.

Sửa thành phiên bản riêng là thao tác có thể phục hồi; đề xuất không xin duyệt từng thay đổi thông thường. Giữ approval của Python tùy ý hiện có; các thao tác file ngoài phạm vi hoặc làm mất thành phần cần duyệt rõ. Không cho tool chuyên biệt trở thành đường vượt quyền sandbox.

## 7. Google Connectors

### Phạm vi

Làm Sheets trước, có Drive Picker để chọn file. Ba luồng: đọc/phân tích; nghiên cứu rồi thêm hàng; đọc bảng và cập nhật vùng xác định. Đề xuất ưu tiên ô, hàng, công thức và định dạng cơ bản; biểu đồ nâng cao sau. Google Docs đọc/tạo/sửa văn bản và đưa Artifact lên Drive là các bước tiếp theo trong cùng giai đoạn Connectors.

Luồng Sheets trực tiếp làm việc với Google Sheets gốc; `.xlsx` nằm trên Drive không tự được coi là Sheet gốc. File Office trên Drive dùng luồng Artifact hoặc chuyển đổi có chủ đích khi bước upload/import được triển khai. Backend kiểm MIME/type của file đích, không suy luận chỉ từ tên hoặc URL.

OAuth theo user; đề xuất scope `drive.file`, Picker và allowlist file ở backend. Không bổ sung quyền đọc toàn Drive để tiện tìm file. Mỗi file dùng cho chat hoặc automation cần nằm trong tập được user chọn cho công việc đó, hoặc được tạo và liên kết bởi chính công việc. Quyền theo vùng ô là policy backend, không tuyên bố OAuth giới hạn được từng tab/vùng.

Chọn file không tự cho phép mọi thao tác trên file. Ghi đè/xóa dữ liệu cần preview phạm vi và xác nhận; thêm hàng cần có yêu cầu rõ về file đích/dữ liệu. Chia sẻ công khai, chuyển sở hữu, xóa file Drive và chạy Apps Script chưa nằm trong phạm vi bản đầu.

### Credential và thực thi

Adapter Google dùng OAuth web-server/offline access phù hợp và refresh khi backend chạy vắng mặt. Client secret thuộc cấu hình ứng dụng; token mã hóa theo owner. LLM, sandbox, logs và tool results không nhận token. Flow MCP public client hiện có phải được tách/phát triển phần dùng chung phù hợp, không giả định tương thích Google nguyên trạng. Chi tiết mapping schema do người triển khai chốt, giữ ownership/revocation/audit và kết nối plugin cũ.

Thu hồi lựa chọn file chặn công việc mới/lời gọi tiếp theo; ngắt connection chặn toàn bộ công việc dùng kết nối đó. Không thể hoàn tác request ngoài đã gửi bằng việc thay cờ DB. Lượt chạy gặp quyền bị thu hồi báo reconnect/thiếu quyền, không xin scope rộng hơn tự động.

Tools có schema riêng: đọc, thêm hàng, cập nhật vùng; về sau Docs và Drive upload. Backend xác minh file/owner/thao tác trước mỗi call; model không tự gửi HTTP tùy ý. Tool input có version hoặc fingerprint cần thiết để preview approval không bị dùng cho nội dung khác. Nếu dữ liệu đổi sau preview, kiểm tra lại và yêu cầu duyệt lại thay vì ghi dựa trên bản cũ; không hứa transaction đồng bộ giữa người sửa trên Google và DB của ứng dụng.

Google append không được coi là exactly-once. Journal ghi operation trước/sau call; nếu đã gửi mà chưa biết kết quả, dừng chờ đối soát, không append lại mù. Dedup nội dung bằng khóa nghiệp vụ như URL. Cơ chế marker/cột ID chỉ dùng khi user đồng ý; đọc trước rồi append chưa đủ bảo đảm không trùng khi retry hoặc cạnh tranh.

## 8. Automations

### Định nghĩa và lưu trữ

Đề xuất mỗi Automation lưu owner, tên, instruction, schedule có múi giờ, enabled, revision và tập quyền/file đích đã duyệt. Schedule ban đầu: một lần, hằng ngày, hằng tuần. Hiển thị giờ địa phương; tính và lưu thời điểm chạy theo UTC.

Một lần chạy có `automation_run_id`, thời điểm trigger, revision cấu hình, run/job liên kết và kết quả. Scheduler chỉ phát hành job; worker chạy Agent. Đề xuất scheduler dùng PostgreSQL và queue hiện có; chưa thêm Redis/Celery hoặc workflow builder. Thuật toán lập lịch/thư viện do kế hoạch chọn sau khi xác minh phiên bản.

Tạo job và ghi tick cần nguyên tử, có uniqueness theo automation + thời điểm đã định. Manual run có idempotency key riêng. Worker kiểm tra trạng thái hiện tại và quyền trước khi bắt đầu. Sửa cấu hình áp dụng cho lần sau; lượt đang chạy giữ snapshot đã được duyệt, nhưng thu hồi quyền vẫn có hiệu lực tức thời cho call tiếp theo.

Đề xuất dùng execution conversation nội bộ cho từng lượt để tái sử dụng AgentRun hiện có, có đánh dấu source và không làm đầy danh sách chat thông thường. Context bắt đầu từ instruction, input và nguồn đã chọn; không tự nạp toàn bộ lịch sử các lượt trước. Kết quả liên kết từ Automation vẫn mở và theo dõi được. Schema cụ thể do builder chọn.

### Ngừng chạy

| Thao tác | Hành vi |
| --- | --- |
| Dừng lượt chạy | Hủy job/run hiện tại, giữ lịch; không tự retry lượt bị user hủy |
| Tắt lịch | Không tạo lượt tự động mới; đề xuất hủy cả lượt tự động đang queued/chưa bắt đầu |
| Tắt khi có lượt đang chạy | Hỏi giữ lượt hiện tại hay tắt lịch và dừng luôn |
| Bật lại | Chờ mốc kế tiếp, không chạy bù khoảng đã tắt |
| Chạy ngay | Đề xuất cho phép ngay cả khi lịch tắt, nếu không có lượt đang hoạt động |

Nút dừng có trạng thái “đang yêu cầu dừng”, sau đó terminal state từ backend. Giữ checkpoint/audit và phần kết quả đã có; không hoàn tác dữ liệu Google đã ghi. Tab đóng không hủy; mở lại đọc trạng thái bền vững. Không báo cancelled khi chưa xác nhận xử lý hủy/đối soát request đang bay.

### Vận hành — đề xuất để duyệt

- Mỗi Automation tối đa một lượt active, tính cả đang chờ duyệt. Mốc lịch mới khi lượt trước còn active được ghi “bỏ qua vì đang chạy”; không xếp backlog ngày này qua ngày khác.
- Mất thời điểm chạy do dịch vụ offline: ghi mốc bị lỡ, chuyển mốc tương lai, không chạy bù mặc định. Không hứa chạy đúng giờ khi dịch vụ tắt.
- Không có trần token/bước/thời gian tổng cho Automation theo lựa chọn user; vẫn ghi usage/chi phí. Giữ giới hạn context/output mỗi model call, timeout RPC, quyền, journal và tài nguyên sandbox. Không tạo số hữu hạn rất lớn để giả làm unlimited.
- Sửa hỗ trợ token tổng nullable xuyên qua root/task budgets, reservation, planner, checkpoint và UI. Run chat hiện tại giữ policy đã có; không tự chuyển checkpoint lịch sử sang unlimited.
- Không chạy vô hạn retry mạng: lỗi tạm thời chỉ retry có điều kiện, lỗi quyền hoặc unknown outcome cần can thiệp. Quy tắc này không phải ngân sách token tổng.
- Quyền automation được duyệt theo thao tác và file đích lúc cấu hình; các thao tác ghi trong phạm vi đó có thể chạy vắng mặt. Nếu phát sinh hành động vượt phạm vi, chuyển waiting_approval và thông báo. Approval runtime không tự mở rộng cấu hình lâu dài.
- Python tùy ý giữ approval từng call trong bản đầu, nên Automation có thể chờ user. Automation tạo file vắng mặt ưu tiên công cụ tài liệu chuyên biệt có phạm vi đã duyệt; không tự đổi approval của run_python_code.
- Thông báo ban đầu: inbox trong ứng dụng, badge trạng thái chưa đọc và link tới run/file/Google URL. Email/push ngoài ứng dụng chưa thuộc phạm vi.

## 9. Thứ tự triển khai đề xuất

1. Shell giao diện theo A và typography đã chốt; giữ luồng đăng nhập/chat/RAG/memory/approval/cancel đang dùng.
2. Artifact foundation và Excel xuyên suốt: nhận file hoặc tạo mới → xử lý → kiểm tra → lưu version → thẻ chat → preview theo yêu cầu → download thật.
3. Word, sau đó PDF có văn bản; dùng lại quản lý file/version và bổ sung kiểm tra layout.
4. Google OAuth + chọn file + Sheets ba luồng; tiếp theo Docs và Artifact lên Drive.
5. Automations theo lịch: quản lý, scheduler, worker, quyền vắng mặt, cancel/pause, lịch sử và inbox.
6. Sau khi nghiệm thu: condition triggers, xử lý ảnh/OCR, dịch vụ ngoài khác. Workflow builder chưa bắt buộc.

Không viết lại các phần đang hoạt động chỉ để khớp cây thư mục trong tài liệu tham khảo. Bản kế hoạch sẽ chia milestone nhỏ theo nghiệm thu; bản thiết kế này chưa phải giao việc triển khai toàn bộ cùng lúc.

## 10. Tiêu chí nghiệm thu

- Chat không tự mở preview khi tạo/sửa file; bấm thẻ mở cạnh chat, đóng phục hồi chiều rộng. Bàn phím và cửa sổ hẹp dùng được.
- User A không đọc, preview, tải hay sửa file/connection/run của user B qua ID trực tiếp hoặc đường dẫn giả.
- Excel tạo mới/sửa file đưa vào, có công thức/biểu đồ trong tập đã công bố; bản gốc còn nguyên, download khớp version, giá trị tính lại không dùng cache cũ giả làm mới.
- Word/PDF giữ nội dung theo yêu cầu; PDF có thể reflow; file scan hoặc thành phần ngoài phạm vi được báo rõ. Có QA render bản kết quả, không chỉ kiểm file tồn tại.
- File lớn/hỏng, archive bất thường, quá quota sandbox và hủy giữa chừng không công bố version hỏng hoặc làm mất bản gốc.
- Google thật: chọn từng file; đọc, append, update; không đụng file chưa chọn; reconnect/revoke có hiệu lực; approval gắn đúng dữ liệu. Retry/unknown outcome không âm thầm ghi lặp.
- Theo lịch thật: tắt tab vẫn chạy; worker/scheduler restart không nhân tick; dừng lượt khác tắt lịch; bật lại không chạy bù; mốc trùng/lượt overlap được xử lý theo policy công bố.
- Có integration check PostgreSQL/worker/checkpoint, Google trên file thử nghiệm được user chọn và kiểm hủy/khôi phục. Mock/unit tests không thay nghiệm thu live.

## 11. Quyết định để người triển khai chốt

Tên module/API/schema cụ thể; dùng lại hoặc tách interface storage/credential ra sao; thư viện scheduler và engine render/recalc; profile CPU/RAM/dung lượng/filecount theo benchmark; pagination và giới hạn payload; chiến lược dọn file tạm; chi tiết accessibility/responsive.

Các lựa chọn này phải giữ hành vi và ranh giới trong thiết kế. Nếu benchmark hoặc khả năng thư viện buộc thay đổi phạm vi người dùng thấy, đưa lại để bàn thay vì âm thầm hạ chất lượng.

## 12. Nguồn và bản phác thảo

- Bản đã chọn: `.superpowers/brainstorm/20261104-claude-layout/content/claude-layout-v5.html`; trạng thái server ở thư mục `state` cùng session. Thư mục này bị Git ignore vì có session key và dữ liệu thử.
- [Claude: Appearance](https://support.claude.com/en/articles/8887527-customizing-your-appearance-settings).
- [Claude: Artifacts](https://support.claude.com/en/articles/17153992-what-are-artifacts-and-how-do-i-use-them).
- [Ảnh giao diện tham khảo công khai, 03/2026](https://www.cool3c.com/article/246833); không coi là ảnh live của mọi tài khoản hiện nay.
- [Google Sheets: đọc/ghi](https://developers.google.com/workspace/sheets/api/guides/values), [batchUpdate](https://developers.google.com/workspace/sheets/api/guides/batchupdate), [chart limits](https://developers.google.com/workspace/sheets/api/samples/charts).
- [Google Drive: per-file scopes](https://developers.google.com/workspace/drive/api/guides/api-specific-auth), [Google OAuth web-server/offline access](https://developers.google.com/identity/protocols/oauth2/web-server).
- [openpyxl: formula không được tự tính](https://openpyxl.readthedocs.io/en/stable/simple_formulae.html), [giới hạn đọc/lưu thành phần workbook](https://openpyxl.readthedocs.io/en/3.1/tutorial.html).

Các nguồn web được đối chiếu trong cuộc thảo luận ngày 2026-11-04; phiên bản dependency cụ thể cần xác minh lúc lập kế hoạch và khóa khi triển khai.

## 13. Rà soát bản thiết kế

Đã có một lượt builder check độc lập ngày 2026-11-04 theo góc nhìn người sẽ lập kế hoạch. Kết quả: không còn câu hỏi quan trọng cần trả lời để lập kế hoạch trong phạm vi tài liệu. Sau đó người dùng duyệt toàn bộ tài liệu, gồm giữ upload tại thư viện, một lượt active cho mỗi Automation và thông báo trong ứng dụng. Đây không phải review code hay bằng chứng sản phẩm đã hoàn tất.
