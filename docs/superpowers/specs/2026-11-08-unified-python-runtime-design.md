# Thiết kế hợp nhất công cụ Python và hệ thống file làm việc

**Trạng thái:** bản thiết kế đã cập nhật theo yêu cầu người dùng; chưa triển khai thay đổi vào sản phẩm.

**Các quyết định đã chốt:**

- Dùng một tool Python tổng quát tên `run_python_code`.
- Dùng một Docker image xử lý chung tên `personal-agent-python-runtime:1`.
- Mỗi lần chạy Python là một môi trường mới; không giữ biến trong RAM giữa các lần gọi tool.
- Giữ các file đã lưu để Agent đọc, kiểm tra và sửa tiếp ở lần chạy sau.
- Chạy `run_python_code` như tool bình thường, không yêu cầu người dùng chấp nhận từng lần chạy.
- Cài sẵn một bộ thư viện Python phổ biến. Nếu phát sinh thư viện còn thiếu, cập nhật bộ thư viện và build lại image sau; Agent không tự cài gói trong lúc thực thi.

## 1. Mục tiêu và hành vi

`run_python_code` là công cụ thực thi Python tổng quát, không phải công cụ chỉ để tạo Excel. LLM tự viết code để tính toán, phân tích và biến đổi dữ liệu, xử lý văn bản, tạo biểu đồ, đọc, tạo hoặc chỉnh sửa các định dạng file được hỗ trợ.

Không bắt buộc LLM phải diễn đạt yêu cầu thành các thao tác cố định như `write_cells`, `set_formula`, `format_range` hoặc `add_chart`. Ví dụ, khi người dùng yêu cầu tạo Excel có biểu đồ, LLM chọn thư viện phù hợp và tự viết Python để tạo file thực tế.

Mỗi lần gọi tool tạo một container thực thi độc lập. Biến Python, thư viện đã import, đối tượng đang mở và dữ liệu trong RAM không được giữ lại. Phiên bản file đầu vào và file kết quả đã công bố được giữ trong kho riêng của người dùng. Agent có thể truyền ID phiên bản kết quả vào lần chạy sau để đọc lại, tự kiểm tra hoặc chỉnh sửa.

Giữ nguyên luồng chat đã nghiệm thu: chữ được stream theo nội dung model trả về; khi trả lời xong mới hiện một thẻ file kết quả cuối cùng; panel preview đang đóng không tự mở; tải lại trang hoặc kết nối lại không nhân đôi thẻ file.

Phạm vi gồm Excel, DOCX, PDF văn bản, tính toán không tạo file, các file văn bản TXT/Markdown/CSV/JSON và ảnh biểu đồ PNG do Python tạo. Việc cài thư viện xử lý ảnh không đồng nghĩa bổ sung OCR hoặc khả năng hiểu ảnh cho LLM trong giai đoạn này.

## 2. Phương án được chọn

Hai hướng đã được đối chiếu:

1. **Nâng cấp sandbox Python cũ:** giữ bộ thực thi cũ nhưng phải xây thêm đường nhận/trả file nhị phân, lưu trữ và quản lý phiên bản. Hợp đồng cũ chỉ hỗ trợ file text nhỏ, thời gian mặc định 5 giây và RAM 128 MiB nên chưa đáp ứng việc xử lý tài liệu.
2. **Hợp nhất trên nền Artifacts hiện có:** tận dụng kho file riêng, quyền truy cập, truyền file nhị phân, phiên bản, dừng tác vụ và phục hồi công việc; đưa một giao diện Python tổng quát ra trước rồi bỏ các nhánh tool trùng chức năng.

Chọn phương án 2. Đây là thay đổi giao diện thực thi và môi trường xử lý; không thay thế database Artifacts hoặc xóa các file người dùng đã lưu.

## 3. Danh sách tool sau hợp nhất

### Tool giữ lại

Giữ **`run_python_code`** làm tool Python/xử lý file công khai duy nhất. Đầu vào gồm:

- Mã Python cần chạy.
- Chuỗi `stdin` tùy chọn.
- Danh sách ID phiên bản file được phép đưa vào container.
- Khai báo file đầu ra: đường dẫn tương đối, tên hiển thị và ID phiên bản cha nếu đang sửa file.
- Chỉ định file kết quả chính qua `primary_output` nếu tạo nhiều file.

Khai báo đầu ra chỉ dùng để xác định file cần thu và lưu; không chứa danh sách thao tác Excel/Word. Backend kiểm tra đường dẫn, quyền sở hữu và tự cấp khóa lưu trữ thực tế.

Kết quả trả về gồm trạng thái thực thi, `stdout`, `stderr` có giới hạn dung lượng, cảnh báo và các tham chiếu file đã công bố. Chạy phép tính không tạo file vẫn được xem là thành công. Không đưa nội dung file nhị phân lớn hoặc chuỗi base64 vào ngữ cảnh của model.

Nếu chỉ có một file đầu ra, mặc định đó là file chính. Nếu có nhiều file, lời gọi phải chỉ rõ file chính. Lời gọi chỉ đọc/kiểm tra và không tạo file không được xóa lựa chọn file kết quả trước đó của cùng tác vụ. Backend lưu lựa chọn cuối một cách bền vững để hoàn tất câu trả lời, tải lại lịch sử và phát lại sự kiện đều dùng đúng file; không suy đoán bằng thời gian tạo phiên bản mới nhất. Quy tắc chọn phiên bản mới nhất chỉ còn là phương án tương thích với lịch sử cũ.

### Tool bỏ khỏi danh sách dùng cho tác vụ mới

| Tool cũ | Cách thay thế |
| --- | --- |
| `artifact_create` | Python qua `run_python_code` |
| `artifact_edit` | Python cùng file đầu vào và phiên bản cha |
| `run_artifact_python` | Hợp nhất vào `run_python_code` |
| `artifact_inspect` | Python đọc file đã chọn hoặc file do Agent tạo; phần đọc phục vụ preview vẫn giữ nội bộ |
| `artifact_rebuild_pdf_text_only` | Python xử lý PDF; giữ kiểm tra độ phủ nội dung và thông tin phần bị bỏ, không duy trì tool riêng theo định dạng |

Registry hiện không có tool công khai tên `create_excel`; không tạo mục xóa giả với tên này. Các tool tìm web, Google Workspace, email, RAG, plugin và điều khiển automation vẫn giữ vai trò riêng.

## 4. Một Docker image dùng chung

Build **`personal-agent-python-runtime:1`**, gồm Python, mã thực thi/kiểm tra file, các thư viện phổ biến và phần mềm cần để tính công thức hoặc render tài liệu.

### Bộ thư viện cài sẵn ban đầu

| Nhóm | Thư viện/phần mềm | Mục đích |
| --- | --- | --- |
| Tính toán, dữ liệu bảng | `numpy`, `pandas` | Tính toán mảng, làm sạch, tổng hợp, biến đổi dữ liệu |
| Toán học, thống kê | `scipy`, `sympy`, `statsmodels` | Hàm toán học, biểu thức đại số và thống kê |
| Phân tích dữ liệu phổ biến | `scikit-learn` | Tiền xử lý, phân cụm, hồi quy và mô hình nhỏ phù hợp tài nguyên |
| Excel | `openpyxl`, `XlsxWriter` | Đọc/sửa workbook và tạo Excel với công thức, định dạng, biểu đồ |
| Word, mẫu tài liệu | `python-docx`, `docxtpl` | Tạo/sửa DOCX và điền dữ liệu vào mẫu |
| PDF | `pypdf`, `pdfplumber`, `reportlab` | Đọc, ghép, tách, trích văn bản/bảng và tạo PDF |
| Biểu đồ | `matplotlib`, `seaborn` | Tạo biểu đồ và xuất ảnh kết quả |
| Ảnh cơ bản | `Pillow` | Đọc/ghi, đổi kích thước hoặc kết hợp ảnh do code tạo; không cung cấp OCR |
| Văn bản, ngày tháng | `regex`, `rapidfuzz`, `python-dateutil` | Xử lý chuỗi, so khớp gần đúng và ngày tháng |
| Dữ liệu có cấu trúc, mẫu | `PyYAML`, `lxml`, `beautifulsoup4`, `Jinja2` | Đọc dữ liệu YAML/XML/HTML và dựng nội dung theo mẫu |
| Tiện ích HTTP phổ biến | `requests`, `httpx` | Cung cấp API thư viện quen thuộc; không mở quyền mạng cho container |
| Phần mềm hệ thống | LibreOffice Calc/Writer, UNO, Poppler, font Unicode phù hợp tiếng Việt | Tính công thức, chuyển đổi/render tài liệu và hỗ trợ văn bản tiếng Việt |
| Thư viện vận hành | `pydantic`, `defusedxml`, FastAPI, Uvicorn và các phụ thuộc cần thiết | Kiểm tra dữ liệu, vận hành Runner và giao thức nội bộ |

Các module chuẩn như `csv`, `json`, `math`, `statistics`, `datetime`, `decimal`, `sqlite3`, `pathlib`, `zipfile` đã có trong Python, không cần cài gói riêng.

Danh sách trên là bộ thư viện ban đầu được yêu cầu, không phải cam kết cài mọi thư viện Python. Không đưa các bộ nặng như PyTorch, TensorFlow, mô hình OCR hoặc trình duyệt tự động vào chỉ để dự phòng. Khi triển khai phải chọn phiên bản tương thích với Python/UNO, khóa phiên bản và hash, rồi kiểm tra import thực tế trong image. Không xem việc ghi tên thư viện vào tài liệu là đã cài xong.

Cấu hình các thư viện số học dùng số luồng phù hợp với 1 CPU, tránh vượt giới hạn tiến trình/luồng của container. Matplotlib dùng backend không cần màn hình; cache và file tạm đặt trong vùng được phép ghi. Kiểm tra font và xuất tài liệu/biểu đồ có dấu tiếng Việt.

### Quy trình bổ sung thư viện về sau

1. Lần chạy gặp `ModuleNotFoundError` hoặc `ImportError` trả lỗi dễ hiểu và tên module thiếu cho Agent.
2. Không tự chạy `pip install`, tải mã từ Internet hoặc thay đổi môi trường đang chạy để né lỗi.
3. Người phát triển đối chiếu module với đúng gói, bổ sung vào danh sách phụ thuộc, cập nhật lockfile, build và kiểm tra lại image.
4. Cập nhật hướng dẫn/danh mục thư viện để LLM biết môi trường mới có gì. Ghi nhận phiên bản hoặc digest image để truy vết được môi trường của từng lần chạy.

### Hai vai trò chạy từ cùng image

- **Thực thi:** container tạm, chạy dưới user không phải root, không có mạng, không nhận đường dẫn host, Docker socket hay thông tin đăng nhập. Các bước kiểm tra, tính công thức và render nội bộ cũng dùng image này.
- **Điều phối:** dịch vụ Runner hiện có, chạy từ cùng image khi dùng Compose. Chỉ vai trò này được mount Docker socket. Image có Docker CLI và thư viện HTTP cần thiết, nhưng container thực thi không được cấp socket hoặc khóa ký của Runner.

Trên Windows chạy Python trực tiếp, Runner có thể tiếp tục chạy bằng venv của project tại loopback cổng 8081 và tạo container con từ image chung. Không cần thêm image hoặc giữ phiên Python sống lâu.

### Image cũ sẽ loại bỏ

| Image cũ | Thay bằng |
| --- | --- |
| `personal-agent-python-sandbox:3.11-v1` | Image runtime hợp nhất |
| `personal-agent-artifact-code:1` | Image runtime hợp nhất |
| `personal-agent-artifact-render:1` | Image runtime hợp nhất |
| `personal-agent-artifact-runner:dev` và tag tương ứng theo cấu hình | Runner Compose dùng image runtime hợp nhất |

Chỉ xóa sau khi bản thay thế kiểm thử thành công và không còn container/tác vụ phụ thuộc. Kiểm tra đúng ID, tag và tham chiếu trước khi xóa. Không dùng Docker prune. Image app/worker, PostgreSQL/pgvector, Redis và dịch vụ không liên quan nằm ngoài phạm vi xóa.

## 5. Những phần backend giữ lại

Giữ metadata `Artifact`/`ArtifactVersion`, kho riêng, quyền sở hữu, phiên bản bất biến, giao diện chọn file, tải xuống, liên kết file với tin nhắn, bộ chọn phiên bản, preview, nhật ký thực thi, dừng tác vụ và phục hồi Worker.

Giữ giao thức HTTP/truyền file nhị phân của Runner, nhưng mở rộng từ một file Office sang thực thi Python tổng quát. Giữ các bộ đọc/render Office và PDF cần thiết ở nội bộ. Không bắt LLM phải chọn một tool riêng để gọi những bước này.

Backend chỉ đưa các phiên bản đã được cấp quyền vào thư mục input chỉ đọc. Ngữ cảnh tool cung cấp tên, ID và đường dẫn tương đối xác định trước. Viết một đường dẫn host trong code không cấp thêm quyền đọc file host. Việc lưu/công bố output do backend thực hiện, không để code LLM tự ghi SQL hoặc gọi trực tiếp hệ thống lưu trữ của ứng dụng.

## 6. Nhật ký thực thi, chống chạy lặp và file cuối

Mỗi lần thực thi có một bản ghi bền vững độc lập với phiên bản file; tận dụng `AgentOperation` khi đáp ứng được các bất biến cần thiết. Dấu vân tay đầu vào bao gồm code/stdin, ID và hash file đầu vào, khai báo output/phiên bản cha, lựa chọn file chính, phạm vi xử lý đã xác định và phiên bản hợp đồng runtime.

Lần chạy thành công không tạo file vẫn lưu và phát lại được trạng thái/stdout mà không chạy lại code. Mỗi file đầu ra có khóa thao tác xác định từ danh tính lần thực thi và khai báo file, tránh nhân phiên bản khi retry hoặc phục hồi.

Khi nhận lại cùng thao tác, đối chiếu biên nhận Runner và output đã lưu trước khi tiếp tục. Nếu kết quả thực thi trước chưa rõ, không âm thầm chạy lại code. Không dùng một `ArtifactVersion` làm đại diện duy nhất cho một lần chạy có nhiều output.

## 7. Xử lý file và Agent tự kiểm tra

Tách kiểm tra cấu trúc/an toàn khỏi giới hạn tính năng. Tiếp tục chặn archive lỗi, đường dẫn vượt phạm vi, symlink, output quá dung lượng, macro/mã thực thi nhúng và liên kết ngoài có khả năng thực thi/nguy hiểm. Không từ chối workbook hợp lệ chỉ vì chứa loại biểu đồ hoặc tính năng an toàn mà bộ sửa có cấu trúc cũ chưa giữ được.

Không ghi đè file chỉ để tạo preview. Tính công thức, render và tạo preview là dịch vụ nội bộ. Nếu một chuyển đổi không giữ được tính năng, giữ nguyên bytes của file kết quả và báo rõ hạn chế; không lặng lẽ thay bằng bản mất nội dung. Công thức mới an toàn không bị giới hạn trong năm hàm cũ. Chỉ báo kết quả tính còn mới khi đã xác minh thực sự.

PDF được kiểm tra độ phủ văn bản đối với mọi file đầu vào, không chỉ file được LLM khai báo là phiên bản cha. Nếu có trang không đọc được hoặc có phần ảnh ngoài phạm vi, trả thông tin cụ thể. Không được mặc nhiên coi bản dựng lại từ phần văn bản là đã bảo toàn toàn bộ tài liệu. Nếu yêu cầu người dùng chưa xác định rõ việc chỉ lấy phần text, Agent làm rõ nội dung yêu cầu trong chat; đây không phải hộp thoại xin phép chạy Python. Ghi nhận phạm vi xử lý gắn đúng phiên bản/hash/trang bị ảnh hưởng. Không thêm OCR trong lần hợp nhất này.

PDF mới có thể được tạo độc lập bằng ReportLab/pypdf, không bắt buộc có DOCX đi kèm. Nguồn DOCX chỉnh sửa được là thành phần tùy chọn, phải liên kết rõ ràng nếu có. Giữ các nguồn đi kèm và link tải của phiên bản cũ.

Để tự kiểm tra, Agent có thể chạy Python mở lại kết quả, kiểm tra ô/công thức, đọc nội dung tài liệu và in tóm tắt có giới hạn. Ảnh render có thể hiện cho người dùng. Việc LLM tự nhìn ảnh để đánh giá bố cục cần đường đọc ảnh/model hỗ trợ riêng; không tuyên bố đã làm việc đó chỉ vì có preview.

PNG được preview qua đường dẫn có xác thực. TXT/MD/CSV/JSON được xem như văn bản an toàn hoặc tải xuống. Giữ việc chọn file Office/PDF hiện có và cho phép tool dùng lại các output của chính nó được hỗ trợ. Không thêm tool theo từng đuôi file.

## 8. Prompt, lựa chọn tool và quyền thực thi

Mô tả tool phải nói rõ khả năng thực thi tổng quát, thư viện đã cài, tính độc lập giữa các lần chạy, quy ước input/output, cách nhận lỗi/stdout và khả năng tạo/sửa file. Có ví dụ Excel/Word/PDF/biểu đồ để LLM biết chọn tool, nhưng không biến mô tả thành chỉ dẫn dành riêng cho Excel.

Bỏ hướng dẫn cũ yêu cầu chỉ dùng Python khi không có tool hẹp hơn phù hợp; bỏ ưu tiên gọi những tool Artifacts đã loại bỏ. Cập nhật đồng bộ đăng ký, allowlist, routing/shortlist, ngữ cảnh thực thi, prompt Agent, quyền automation, công bố output và phục hồi thao tác. Prompt đúng chưa đủ nếu tool bị bộ lọc loại trước khi đến model.

### Không yêu cầu duyệt từng lần chạy Python

Theo yêu cầu trực tiếp của người dùng, cấu hình `run_python_code` với **`requires_approval=False`**. Không hiện nút chấp nhận/từ chối trước mỗi lần chạy, không tạo yêu cầu duyệt code và không chuyển tác vụ sang chờ duyệt chỉ vì gọi Python. Agent được chạy, đọc lỗi, sửa code và gọi lại có chủ đích để hoàn thành yêu cầu như với tool bình thường.

Chính sách này áp dụng cho chat và automation đã được phép sử dụng tool. Automation tạo/sửa file không bị dừng hằng ngày để xin duyệt code Python. Cập nhật bộ kiểm quyền automation để nhận tool hợp nhất và tiếp tục ràng buộc file đầu vào, phạm vi nhiệm vụ và kết quả theo cấu hình đã cấp. Việc bỏ bước duyệt Python không tự cấp quyền cho tool Google/email, truy cập file ngoài phạm vi hoặc thay đổi quyền của tool khác.

Vẫn giữ sandbox, kiểm tra đầu vào/đầu ra, giới hạn tài nguyên, ownership, cơ chế dừng, nhật ký và chống thực thi lặp. Các lần chạy bị hủy hoặc kết quả chưa rõ không được tự động chạy lại chỉ vì không còn bước duyệt.

## 9. Giới hạn và cấu hình

Dùng một cấu hình tài nguyên thực thi phù hợp cả tính toán và tài liệu, độc lập với ngân sách token tổng của Agent. Mức ban đầu: 1 CPU, RAM 1.024 MiB, tối đa 90 giây mỗi lần thực thi và không có mạng. Khi triển khai phải chỉnh đồng bộ timeout của tiến trình con, container và tool; không để sót giới hạn 8/25/30 giây của các nhánh cũ rồi tuyên bố được chạy 90 giây.

Giữ ban đầu 20 MiB/file, 40 MiB tổng cho một lượt truyền, giới hạn log và quota kho riêng. Lần chạy không tạo file là hợp lệ; bỏ trần file text 8 KiB của sandbox cũ. Cài thư viện thống kê/ML không bảo đảm mọi mô hình lớn chạy được trong mức tài nguyên này.

Dùng `PYTHON_SANDBOX_ENABLED` để bật tool hợp nhất. `WORKSPACE_ARTIFACTS_ENABLED` tiếp tục điều khiển UI/API file làm việc; công bố file cần phần này được bật. Cập nhật file mẫu và Compose để bật đúng chức năng; không ghi đè `.env` thật. Cấu hình Windows đang tắt Python và bật Artifacts cần hướng dẫn chuyển bật Python sau khi triển khai.

Giữ tương thích các biến URL/key/storage của Runner trừ khi có lý do triển khai phải đổi tên và được ghi rõ. Không thêm cờ cấu hình cho từng định dạng file. Không sửa thông tin đăng nhập hay thay nội dung database chỉ để đổi tên tool/image.

## 10. Tương thích và xóa thành phần cũ

File, hash, ID phiên bản, URL tải và lịch sử tin nhắn cũ phải tiếp tục đọc được. Không âm thầm đổi khóa thao tác, checkpoint hoặc nhật ký cũ sang nghĩa mới khiến code chạy hai lần.

Trước khi xóa bộ thực thi/image cũ, xác định các tác vụ chưa kết thúc. Hoàn tất chúng dưới phiên bản cũ hoặc có đường tương thích hẹp giữ nguyên dấu vân tay và dữ liệu thao tác. Đường tương thích chỉ phục vụ tác vụ tồn tại trước khi nâng cấp, không trở thành một tool Python công khai thứ hai. Các tác vụ Python cũ đang chờ duyệt cần có cách chuyển trạng thái/phục hồi rõ ràng theo chính sách mới, không để treo và không chạy lại thao tác đã thực thi. Hồ sơ duyệt cũ được giữ làm lịch sử; không phát sinh duyệt Python mới.

Loại bỏ bộ thực thi/hợp đồng độc lập cũ trong `agent_core/code_execution` và định nghĩa image `infra/docker/python-sandbox` sau khi hành vi tính toán tổng quát đã được bao phủ bằng bản hợp nhất. Chuyển script và test còn hoạt động sang hợp đồng mới; giữ tài liệu nghiệm thu lịch sử.

Hợp nhất định nghĩa image và lockfile của `infra/docker/artifact-sandbox` cùng `infra/docker/artifact-runner`. Giữ các bộ đọc/render/tính công thức và dịch vụ lưu trữ/phiên bản còn hữu ích; không xóa nguyên thư mục chỉ vì tên cũ có chữ artifact.

Xóa các image Docker cũ là bước cuối sau khi kiểm thử tạo/sửa/tự kiểm tra/tải file và tính toán bằng container thật đạt. Nếu image còn được container hoặc tác vụ chưa chuyển đổi dùng đến, báo đúng phụ thuộc đó thay vì ép xóa.

## 11. Tiêu chí nghiệm thu

1. Registry chỉ công khai một tool Python/xử lý file tên `run_python_code`; không đăng ký trùng khi bật đồng thời các cờ tính năng.
2. Tính toán và stdin hoạt động khi không tạo file. Stdout/stderr có giới hạn; Agent đọc được lỗi và có thể sửa code trong lần gọi mới.
3. Chạy Python không tạo yêu cầu duyệt hoặc hiện nút chấp nhận; chat và automation không chuyển sang chờ duyệt vì tool này. Các tool khác giữ chính sách riêng.
4. Tạo/sửa Excel có định dạng, ô gộp, công thức và biểu đồ ngoài bộ cũ; lần chạy độc lập tiếp theo mở lại được kết quả; bản gốc vẫn tải được.
5. Tạo/sửa DOCX; tạo/đọc PDF văn bản độc lập, giữ cảnh báo độ phủ. TXT/MD/CSV/JSON và PNG hoạt động qua kho file chung.
6. File đã chọn và output trước đó được đưa vào xử lý; file của người khác, file chưa được cấp quyền và đường dẫn host tùy ý bị chặn.
7. Hạn chế preview không làm hỏng hoặc mất nội dung file hợp lệ. Độ mới của kết quả công thức được xác minh riêng.
8. Mọi thư viện trong danh sách ban đầu import được trong image. Có bài chạy nhỏ đại diện cho tính toán, bảng dữ liệu, Excel, DOCX, PDF, biểu đồ và xử lý văn bản.
9. Module thiếu trả lỗi rõ ràng; không tự cài gói hoặc mở mạng. Cách bổ sung thư viện và build lại image được ghi trong hướng dẫn vận hành.
10. Biến RAM không tồn tại giữa các lần chạy. Mạng/secret/socket và giới hạn tài nguyên được kiểm tra trên container thật.
11. Một thẻ file cuối, streaming thật, dừng tác vụ, phát lại, phục hồi và ràng buộc quyền automation tiếp tục hoạt động.
12. Docker và Compose chỉ dùng image runtime hợp nhất cho phân hệ này. Runner Windows vẫn chạy được.
13. Test và dọn dẹp không sửa `.env`, file người dùng hoặc tài nguyên Docker không liên quan. Báo cáo cuối liệt kê chính xác code/tool/tag image đã xóa và phần tương thích còn giữ.

## 12. Rà soát thiết kế

Bản thiết kế gốc đã có một lượt rà soát độc lập. Ba điểm cần bổ sung được đưa vào tài liệu: kiểm tra PDF phải bao phủ mọi file đầu vào; PDF không bắt buộc có DOCX đi kèm; thực thi tổng quát cần nhật ký độc lập cho trường hợp không có/nhiều output và lựa chọn file cuối rõ ràng.

Bản tiếng Việt này cập nhật thêm hai quyết định trực tiếp của người dùng: bỏ duyệt từng lần chạy Python và cài sẵn bộ thư viện phổ biến, bổ sung thư viện thiếu vào image sau khi có nhu cầu thực tế. Các đoạn giữ chính sách duyệt Python ở bản cũ đã được thay thế bằng mục 8; không dùng chúng làm yêu cầu triển khai nữa.
