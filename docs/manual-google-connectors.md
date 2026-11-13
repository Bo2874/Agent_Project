# Google Connectors — vận hành và nghiệm thu

Từ thiết kế 2026-11-09, Google Sheets/Docs/Drive dùng chung **`run_python_code`** với file trong ứng dụng.
Backend giữ OAuth và giao tiếp Google; Python tạo nội dung/file hoặc các lệnh sửa nội dung/bố cục.
Không còn tool `google_*` cho lượt chạy mới và không cần duyệt từng thay đổi. OAuth chỉ cần kết nối
ban đầu trong điều kiện bình thường; backend tự refresh. Thu hồi/hết hiệu lực thì phải kết nối lại.
Kiểm thử offline không thay cho nghiệm thu OAuth/API và chuyển đổi định dạng trên Google thật.

## Cấu hình

Đồng bộ dependencies theo lockfile (`uv sync --extra dev` cho máy phát triển/QA) trước khi chạy
backend hoặc tests. Google auth SDK là dependency ứng dụng; không cài chúng vào Python sandbox.

1. Trên Google Cloud, tạo project và OAuth consent screen; khai báo tài khoản thử nếu ứng dụng
   đang ở chế độ testing. Bật Google Drive API, Google Sheets API, Google Docs API và Google Picker API.
2. Tạo OAuth Client loại **Web application**. Đăng ký chính xác redirect URI
   `http://127.0.0.1:8000/connectors/google/callback` cho local; khi deploy dùng URL HTTPS của web.
   Không dùng URL của worker/container làm callback. `localhost` và `127.0.0.1` là hai địa chỉ khác nhau.
   Reverse proxy phải chuyển đúng public scheme/host; cookie đăng nhập phải tới được callback.
3. Khai báo backend bằng các biến trong `.env.example`:
   `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`,
   `GOOGLE_CREDENTIAL_ENCRYPTION_KEYS`, `GOOGLE_PICKER_API_KEY`, `GOOGLE_CLOUD_PROJECT_NUMBER`.
   Picker API key là browser key: hạn chế HTTP referrer đúng origin của ứng dụng và API phù hợp.
   Project number là số project, không phải project ID hay client ID.
4. Tạo Fernet key riêng cho Google bằng
   `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
   Cất key/client secret trong cấu hình riêng; không đưa vào Git, chat hay sandbox. Không dùng lại key plugin.
5. Chạy `alembic upgrade head` trên môi trường đích sau khi chuẩn bị backup theo quy trình vận hành.
   Migration `20261105_0033` nối sau `0032`; thêm năm bảng Google, không sửa plugin credential.
6. Bật `GOOGLE_CONNECTOR_ENABLED=true`, restart cả web và worker với cùng cấu hình/keyring.
   Compose dùng các biến `DEV_GOOGLE_*` trong `.env.docker.example`; mặc định vẫn tắt.
   Bật `PYTHON_SANDBOX_ENABLED=true`, giữ `run_python_code` trong `TOOL_ALLOWLIST` nếu có danh sách riêng.
   Bật `WORKSPACE_ARTIFACTS_ENABLED=true` để tải/lưu file trong ứng dụng. Không thêm tool Google cũ.
   Build lại image `personal-agent-python-runtime:1`, restart broker/web/worker theo
   [hướng dẫn Python](manual-python-runtime.md). Compose dùng các biến `DEV_*` tương ứng.

Ứng dụng chỉ xin `https://www.googleapis.com/auth/drive.file`. Không xin quyền tìm toàn bộ Drive.
OAuth có state dùng một lần, TTL 10 phút, PKCE S256 và offline access; thiếu refresh token thì
báo reconnect, chưa sẵn sàng chạy vắng mặt. Không dùng thông tin email do token response tự khai báo;
identity lấy từ Drive `about.get`.

## Dùng trong chat

Vào **Google Connectors → Kết nối Google → Chọn file từ Google**. Sau đó mở chat, bấm **File Google**
và chọn đúng file cho cuộc trò chuyện. Chọn global chưa cho mọi chat quyền đọc file. Thu hồi một file
hoặc ngắt kết nối chặn call tiếp theo, kể cả khi run giữ schema/checkpoint cũ.
Mỗi lần reconnect đều thu hồi grant cũ; chọn lại file và bắt đầu lượt chat mới.
Ngắt kết nối là thu hồi trong ứng dụng; có thể thu hồi consent hoàn toàn tại tài khoản Google.

Một yêu cầu bình thường có thể là: “Đọc bảng doanh thu này, thêm cột lợi nhuận và sắp xếp lại cho dễ xem”.
Agent đọc dữ liệu rồi tự xác định ô/phạm vi, công thức và bố cục; người dùng không cần chỉ từng tọa độ.

| Yêu cầu | Luồng thực hiện |
|---|---|
| Tạo Sheet/Doc mới | Python tạo XLSX/DOCX → lưu file → backend upload và chuyển thành Google Sheets/Docs → trả URL |
| Sửa Google Sheets/Docs đang có | Backend lấy bản chụp JSON → Python phân tích và tạo batchUpdate → backend ghi vào đúng file cũ |
| File XLSX/DOCX/PDF trên Drive | Tải bytes vào Python → đọc/sửa bằng thư viện → upload bản mới hoặc cập nhật đúng file cùng định dạng |
| Kiểm tra sau khi ghi | Một lần gọi Python tiếp theo đọc lại Google để xác minh dữ liệu thực tế |

Google Sheets/Docs là tài liệu trực tuyến của Google; XLSX/DOCX trên Drive vẫn là file Office.
Sửa tài liệu trực tuyến không buộc phải xuất ra Office rồi dựng lại toàn bộ. BatchUpdate có thể sửa
giá trị/công thức, định dạng, thứ tự dữ liệu, biểu đồ, đoạn văn, bảng và kiểu chữ trong phạm vi API hỗ trợ.
Không xử lý ảnh ở giai đoạn này. Không tự chia sẻ, đổi chủ, xóa file, chạy Apps Script hoặc gọi URL tùy ý.

Python không có mạng, token Google hoặc kết nối sống tới Google. Mỗi lần chạy dùng container mới.
Bản chụp nguồn được lưu trước khi chạy code. Kết quả trung gian và lệnh sửa là file; không giữ biến RAM.
JSON chứa lệnh sửa được đánh dấu `publish=false`, không hiện thành file tải xuống cho người dùng.
Kết quả Google có URL đã được backend kiểm tra; file local vẫn theo cơ chế một primary output cuối chat.

Giới hạn hiện tại: tối đa 3 input tổng cộng local+Google, 3 output và 3 Google action/lần gọi;
20 MiB/file, 40 MiB input tổng; JSON nguồn/lệnh Google tối đa 2 MiB, 100 API request/batch.
Sheets mặc định đọc `A1:AX1000` của tab đầu, có thể chỉ rõ vùng/tab khác để đọc tiếp. Docs giữ JSON
gốc và chỉ số UTF-16. Đây là giới hạn quan sát của ứng dụng, không phải giới hạn dung lượng file trên Google.
Không hỗ trợ công thức lấy dữ liệu bên ngoài, ảnh hoặc external data source. Khi cần chuỗi bắt đầu `=`,
Agent dùng `stringValue`; công thức thật dùng `formulaValue`.

Công thức mới chỉ dùng hàm Google có trong danh mục đã kiểm tra; hàm tùy chỉnh, named function,
Apps Script và hàm truy cập mạng không được gửi lên. Nếu cần, Python tính kết quả rồi ghi giá trị.
Khi chuyển XLSX thành Sheets, inspector trong container kiểm cả công thức ô, named range,
validation, conditional formatting và bảng; kết quả kiểm được ghim theo hash của file.
Image cũ thiếu kết quả kiểm sẽ yêu cầu build lại trước khi chuyển đổi.
Không dùng `findReplace`, `pasteData` hoặc `textToColumns` để sinh công thức gián tiếp;
Python có thể xử lý dữ liệu và tạo `updateCells`/`appendCells` rõ ràng thay thế.
File Google đang có vẫn có thể chứa công thức hoặc script từ trước; ứng dụng không cách ly
cách Google tính lại chúng sau khi sửa. Chỉ dùng file đã chọn mà người dùng tin cậy.

Doc chỉ có quyền xem vẫn đọc được nếu Google không trả revision ID; để sửa, backend cần revision
hợp lệ và quyền ghi. Bản chụp từng đầu vào được lưu riêng, nên phục hồi không đọc lại nguồn đã ghim.

Tạo mới mặc định ở My Drive, chưa chọn thư mục đích. `keep_format` giữ XLSX/DOCX/PDF;
`google_native` chỉ chuyển XLSX→Sheets, DOCX→Docs. Chuyển đổi có thể mất một số đặc tính và cần xem lại.
Chỉ thay bytes file cũ nếu nguồn được tải bằng `download`, cùng connection/MIME và mode `keep_format`.
Không dùng cơ chế upload chuyển đổi để âm thầm thay toàn bộ nội dung tài liệu Google đang có.

**Xung đột:** Google Docs trực tuyến kiểm revision và hash JSON nguồn, gắn `requiredRevisionId`
từ backend khi ghi. Không coi Drive version tăng riêng lẻ là xung đột Docs: metadata Drive có thể
cập nhật chậm sau chính lần ghi của Agent. Sheets và file download/export vẫn kiểm Drive version
trước/sau đọc và trước ghi. Revision/nội dung Docs thay đổi thật hoặc quyền thay đổi vẫn bị chặn.
Nếu phát hiện người khác sửa nguồn, dừng và báo `google_conflict`; chặn Google trong cùng lượt chạy,
không tự đọc lại/merge/ghi đè. Người dùng phải gửi yêu cầu mới. Sheets vẫn có khe thời gian giữa kiểm tra
và ghi; không hứa khóa nguyên tử hay exactly-once xuyên Google và database.

**Automation:** chọn đúng từng file và quyền. `edit_sheet` cho phép đọc/sửa nội dung, công thức và bố cục
toàn file Sheet; `edit_doc` tương tự cho Doc. `read_file`/`edit_file` dành cho Office/PDF trên Drive.
`upload_artifact` cho phép tạo file trên connection đã chọn. Quyền vùng cũ như `append_rows` không tự
nâng thành quyền sửa toàn file; cần lưu revision mới với quyền phù hợp. Đọc vùng cũ vẫn ghim sheetId/tên tab.
Không xuất hiện bước duyệt từng thay đổi đã được grant cho phép; dừng/thu hồi quyền vẫn chặn thao tác tiếp theo.

## Credential và sự cố

Nếu Picker báo “The API developer key is invalid”, kiểm API đã bật, key/project và
Website restrictions phải có origin của ứng dụng cùng `https://docs.google.com/*`.
Trang `/connectors` cần `Referrer-Policy: strict-origin` để Google nhận origin khi kiểm key;
không đặt `no-referrer` cho trang này qua reverse proxy hoặc extension. Callback OAuth/API credential
vẫn dùng `no-referrer`. Sau khi cập nhật code, restart web và tải lại trang; không cần build Python image.

Access/refresh token được mã hóa theo owner + connection; refresh tuần tự bằng row lock, giữ refresh
token cũ nếu provider không trả bản mới. `invalid_grant` chuyển reconnect. Token không vào LLM,
sandbox, journal payload hoặc tool result. Browser Picker chỉ nhận access token trong bộ nhớ,
không nhận refresh token, không ghi local/session storage; Picker đóng/hết hạn thì bỏ tham chiếu.

Keyring nhận 1–3 Fernet keys phân cách dấu phẩy, key mới ở đầu. Cập nhật web/worker đồng thời,
giữ key cũ cho đến khi credential đã được ghi lại bằng key mới qua refresh/reconnect và kiểm chứng.
Không có lệnh re-encrypt hàng loạt trong Plan03; không bỏ key cũ chỉ vì vừa restart. Mất key cần reconnect.
Google OAuth testing/consent có thể làm refresh token hết hạn; theo dõi trạng thái, không xin scope rộng hơn.

Journal ghi `sent` trước RPC và thả DB locks trước mạng. Nếu mất response, worker bị dừng hoặc không
lưu được receipt, trạng thái là `unknown` (hoặc còn `sent` sau crash), **không tự retry**.
Mã gọi mới cũng bị chặn trên cùng đích; thao tác tạo file chưa rõ chặn tạo/upload mới ở connection đó.
Dừng run không hoàn tác request đã gửi Google. Chỉ đọc để đối soát; không yêu cầu Agent thử ghi lại.

Đối soát do người vận hành tin cậy thực hiện: lấy operation ID ở timeline/receipt, xem journal theo
đúng owner/run, kiểm nội dung/range/file mới trực tiếp trong tài khoản Google đã dùng. Giữ bằng chứng.
Chỉ đánh dấu `completed` kèm receipt đã xác minh hoặc `rejected` khi đã chứng minh chưa áp dụng,
trong transaction được kiểm tra owner/connection. Không có API/tool cho LLM tự gỡ trạng thái này.
Nếu vẫn chưa rõ, giữ nguyên và hỏi người dùng; không xóa journal để mở khóa.

## Nghiệm thu

Sau khi build lại image và restart broker/web/worker, kết nối Google, chọn một Sheet và một Doc thử riêng.
Không dùng file quan trọng để thử lần đầu.

1. “Đọc file Google đã chọn, tóm tắt nội dung hiện có.” Kiểm dữ liệu thật, không hỏi duyệt thao tác.
2. “Thêm cột lợi nhuận, công thức và định dạng tiêu đề cho dễ nhìn.” URL và file ID Sheet phải giữ nguyên.
3. “Sửa báo cáo này thành bản ngắn gọn, có tiêu đề và bảng tổng hợp.” Doc giữ file ID; kiểm Unicode/bảng/kiểu chữ.
4. “Tạo bảng quản lý chi tiêu rồi lưu thành Google Sheets mới.” Mở URL và đối chiếu file XLSX local.
5. “Tạo báo cáo và lưu thành Google Docs mới.” Kiểm nội dung và bố cục sau chuyển đổi.
6. Với file Office trên Drive: yêu cầu đọc/sửa và cập nhật cùng file; kiểm đúng định dạng và file ID.
7. Yêu cầu Agent đọc lại sau khi sửa. Thử một nguồn lớn để kiểm thông báo giới hạn và đọc theo vùng.
8. Thử sửa ngoài Google trong lúc Agent đang xử lý. Nếu phát hiện xung đột, Agent phải dừng; không tự gộp lại.
9. Thu hồi file/kết nối rồi gửi yêu cầu tiếp; quyền cũ không được dùng lại. Kết nối lại và chọn lại file khi cần.
10. Automation: chọn quyền mới, Run now, tắt lịch/dừng run và kiểm lịch sử/URL kết quả. Chỉ bật lịch sau khi thử đạt.

Nếu lỗi “unknown outcome”, không yêu cầu thử ghi lại: kiểm trực tiếp trên Google và đối soát journal như trên.
Thao tác thành công trước một lỗi sau đó vẫn được báo lại; không hứa tự hoàn tác các bước đã ghi.

QA offline dùng PostgreSQL scratch có tên kết thúc `_test`; không dùng database production:

```powershell
$env:GOOGLE_TEST_DATABASE_URL = 'postgresql+asyncpg://test_user:test_password@127.0.0.1:5432/google_test'
$env:ARTIFACT_RUNNER_DOCKER = '1'
python -m scripts.check_google_connector --offline --report reports/google-offline.xml
```

Google/model/search là fixture; worker/graph/journal/checkpoint và container Python chạy thật ở các gate tương ứng.
Script `--live` chỉ in checklist và trả mã 2, không tự đổi tài liệu cloud. Người dùng tự quyết định giữ/xóa file thử.
Rollback bằng tắt connector rồi restart web/worker; không xóa ciphertext, grant hoặc journal để mở khóa.

## Tài liệu gốc đã đối chiếu

- [OAuth web server/offline access](https://developers.google.com/identity/protocols/oauth2/web-server)
- [Google Picker](https://developers.google.com/workspace/drive/picker/guides/overview)
- [Sheets append](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/append)
- [Docs batchUpdate và revision control](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/batchUpdate)
- [Docs tabs](https://developers.google.com/workspace/docs/api/how-tos/tabs)
- [Docs TextRun và suggestions](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#TextRun)
- [Sheets A1, sheetId và tab hiển thị](https://developers.google.com/workspace/sheets/api/guides/concepts)
- [Sheets bounded grid data](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/get)
- [Drive resumable upload/import](https://developers.google.com/workspace/drive/api/guides/manage-uploads)
- [Danh mục hàm Google Sheets](https://support.google.com/docs/table/25273?hl=en)
- [Apps Script custom functions](https://developers.google.com/apps-script/guides/sheets/functions)
- [Drive files.get và shared drives](https://developers.google.com/workspace/drive/api/reference/rest/v3/files/get)
- [Drive files.update và resumable upload](https://developers.google.com/workspace/drive/api/reference/rest/v3/files/update)
