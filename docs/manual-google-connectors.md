# Google Connectors — vận hành và nghiệm thu

Plan03 có OAuth, chọn từng file theo chat, Sheets, Docs text và upload Artifact. Bằng chứng
offline không chứng minh quyền/API Google thật. Chưa có OAuth client của người dùng nên **live NOT RUN**.
Theo lựa chọn người dùng ngày 2026-11-07, Plan04 đã được triển khai và kiểm thử offline trước.
Vẫn cần nghiệm thu ba luồng Sheets trên file thử đã chọn trước khi bật automation ghi Google thật.

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
   Thêm những tool Google cần dùng từ bảng dưới vào `TOOL_ALLOWLIST`, đồng thời giữ
   các tool hiện có. Danh sách mặc định chưa chứa tool Google, kể cả khi đã bật connector.
   Với Compose, đặt `DEV_TOOL_ALLOWLIST` rồi tạo lại cả web và worker.

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

| Nhóm | Tool và cách dùng |
| --- | --- |
| Sheets | `google_sheets_read`; `google_sheets_prepare` tạo đề xuất local; `google_sheets_append` / `google_sheets_update` commit sau duyệt |
| Docs | `google_docs_read`; `google_docs_prepare` với `kind=edit` hoặc `create`; `google_docs_edit` / `google_docs_create` commit sau duyệt |
| Drive | `google_drive_prepare` nhận `connection_id`, `version_id`, **mode bắt buộc**; `google_drive_upload` commit sau duyệt |

Tool ghi chỉ nhận nguyên `commit_arguments` từ proposal, không nhận `approved: true` từ model.
Preview ghim đích, dữ liệu, fingerprint, generation, snapshot và hạn 10 phút; thay đổi thì phải chuẩn bị lại.
Tất cả ghi Google hiện cần duyệt, kể cả append. Receipt có operation ID, URL đã kiểm tra và số ô/hàng khi phù hợp.
File mới được đăng ký owner/chat trong cùng transaction lưu receipt và dùng tiếp được ngay trong run đó.
Receipt ghim cả generation của file mới; thu hồi rồi cấp lại không làm sống lại quyền của run cũ.
Kết quả đã lưu trước sự cố và kết quả đọc đang chạy cũng bị chặn nếu quyền đã bị thu hồi.

Sheets chỉ nhận native Google Sheet. `.xlsx` trên Drive chưa phải Sheet; không tự chuyển đổi.
Range A1 phải có hàng cụ thể, tối đa 1.000 hàng/50 cột; đọc từng trang tối đa 100 hàng,
ghi tối đa 100 hàng/lần. Append/update dùng RAW để chuỗi từ web bắt đầu `=` vẫn là text.
Chỉ `set_formulas` chủ đích mới dùng công thức. Dedup theo cột được chỉ định, không thêm marker/cột ẩn.
Không hứa exactly-once hoặc transaction xuyên Google/DB; vẫn còn race sau lần kiểm snapshot cuối.
Đề xuất ghi ghim tab bằng sheetId và tên tab có quote. Range không ghi tên tab được giải quyết
thành tab hiển thị đầu tiên ngay khi chuẩn bị; đổi thứ tự tab không đổi đích, đổi tên phải duyệt lại.
Formatting phải có ít nhất một thuộc tính; bản xem trước và snapshot gồm đúng định dạng sẽ thay đổi,
để phát hiện người khác sửa định dạng trước lúc ghi. Phản hồi Google hỏng không được coi là bảng rỗng
hay receipt thành công; phản hồi ghi không hợp lệ giữ trạng thái unknown để đối soát.

Docs đọc text runs và tab ID, sửa bằng chỉ số UTF-16 trên revision vừa đọc. Các thao tác không được
chồng lấn hoặc cắt đôi ký tự Unicode; phải nằm trong một text run, giữ dấu kết thúc đoạn.
Ảnh/bảng/footnote/suggestion không được sửa hay full-replace ngầm. Văn bản dài đọc tiếp bằng offset.
Tạo Doc hiện tạo file trắng trước; thêm nội dung bằng một đề xuất sửa riêng. `requiredRevisionId`
chặn batchUpdate nếu có người sửa Google sau lần kiểm tra cuối.

Upload chỉ lấy bytes của ArtifactVersion đã sở hữu và được chọn/tạo trong chat, kiểm hash/size trước
khi gửi; không nhận URL/path từ model. Tối đa 20 MiB, một session resumable và một PUT hoàn tất.
Session URL chỉ ở trusted adapter, không đưa vào tool result; query `upload_id` được che trong log.
`keep_format` giữ XLSX/DOCX/PDF; `google_native` chỉ chuyển XLSX→Sheet hoặc DOCX→Doc,
có preview cảnh báo mất format/công thức/biểu đồ/bố cục. File mặc định tạo ở My Drive.
Chưa chọn folder đích, không tự chia sẻ công khai, đổi chủ, xóa file hoặc chạy Apps Script.

## Credential và sự cố

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

Đối soát do người vận hành tin cậy thực hiện: lấy operation ID ở timeline/proposal, xem journal theo
đúng owner/run, kiểm nội dung/range/file mới trực tiếp trong tài khoản Google đã dùng. Giữ bằng chứng.
Chỉ đánh dấu `completed` kèm receipt đã xác minh hoặc `rejected` khi đã chứng minh chưa áp dụng,
trong transaction được kiểm tra owner/connection. Không có API/tool cho LLM tự gỡ trạng thái này.
Nếu vẫn chưa rõ, giữ nguyên và hỏi người dùng; không xóa journal để mở khóa.

## Nghiệm thu

Offline cần dev dependencies, Chrome/Node và PostgreSQL scratch đã migrate, tên DB kết thúc `_test`:

```powershell
$env:GOOGLE_TEST_DATABASE_URL = 'postgresql+asyncpg://test_user:test_password@127.0.0.1:5432/google_test'
python -m scripts.check_google_connector --offline --report reports/google-offline.xml
```

Đây là fixture model/Google/web responses; queue, worker, graph, approval, DB và checkpoint chạy thật.
Các test tự tạo schema riêng rồi dọn schema đó. Không dùng database production. Script không mở
OAuth thật hoặc gọi model trả phí. JUnit report không phải báo cáo chất lượng model/live conversion.

`python -m scripts.check_google_connector --live --connection-id <id> --file-id <selected-test-file>`
chỉ in checklist và trả mã 2 (**NOT RUN**); thao tác thật đi qua chat để giữ nguyên xác thực/duyệt.
Trên Sheet thử riêng: đọc/phân tích A1:B10; tìm nguồn rồi append các dòng mẫu; đọc B2 rồi preview,
duyệt và update; đọc lại để so dữ liệu. Thử revoke/reconnect trước call tiếp theo. Docs thử sửa Unicode,
sửa ngoài Google giữa preview/duyệt; upload XLSX/DOCX ở cả hai mode và mở/đọc lại kết quả.
Mọi conversion giữ đầy đủ nội dung/format phải được đánh giá riêng bằng dữ liệu thật.
Người dùng quyết định giữ hay xóa file thử cloud; script không xóa Sheet/Doc hoặc dữ liệu không xác định.

Rollback bằng `GOOGLE_CONNECTOR_ENABLED=false` và restart web/worker; không downgrade DB hoặc
xóa ciphertext/journal để ẩn UI. Giữ backup keyring an toàn cùng DB backup theo quy trình vận hành.

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
