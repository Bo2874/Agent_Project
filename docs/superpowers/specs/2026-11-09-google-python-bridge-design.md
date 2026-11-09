# Google qua Python — thiết kế hợp nhất

## Mục tiêu và quyết định của người dùng

Người dùng đã nghiệm thu Excel, DOCX và PDF qua `run_python_code`, muốn dùng cùng giao diện để làm việc với Google. Ngày 2026-11-09 đã chọn triển khai Sheets và Docs cùng đợt, bỏ duyệt từng thay đổi, chỉ dùng từng file được chọn và file mới do Agent tạo. Yêu cầu bằng ngôn ngữ tự nhiên có thể nói về nội dung/bố cục tổng thể; Agent tự xác định dữ liệu và vị trí cần xử lý. Phát hiện nguồn bị người khác sửa thì dừng, báo xung đột, không tự đọc bản mới để ghi lại.

Người dùng đã yêu cầu thực hiện theo các quyết định đã bàn. Tài liệu này ghi lại yêu cầu và các lựa chọn kỹ thuật của người triển khai, không mở thêm vòng xin phép triển khai. Không tự đổi `.env`, gọi Google/model thật, thay database ứng dụng, restart dịch vụ người dùng, merge main hoặc push trong đợt này.

## Phương án

1. Chỉ download/export → Python sửa file → upload/import: phù hợp file mới và Office/PDF nhưng import lại tài liệu Google có thể thay toàn bộ nội dung; không đáp ứng tốt sửa từng phần.
2. Python dùng lớp Google chung cho file và cập nhật trực tiếp: chọn phương án này. OAuth, quyền, journal nằm ở backend. Python nhận snapshot/file và tạo file hoặc các yêu cầu Google API; backend tự thực hiện trong quyền đã cấp.

Không đưa token, socket hoặc mạng vào sandbox; không tạo Google tool công khai riêng cho từng thao tác. Một image production `personal-agent-python-runtime:1` được giữ; image/tag QA tạm có thể dùng riêng để không thay image web đang chạy.

## Giao diện thực thi

Mở rộng hợp đồng `run_python_code` theo hướng trao đổi file, không thêm RPC sống hoặc phiên RAM:

- Giữ code/stdin/input_version_ids/outputs/primary_output và hành vi cũ.
- `google_inputs`: tối đa 3 nguồn đã chọn, mỗi nguồn có Google ref (`connection_id`, `file_id`, `context_id`), mode `native`, `download` hoặc `export`, định dạng export rõ ràng; Sheets có thể đọc range do Agent xác định. Metadata có thể đọc trước để Agent tự chọn range, không đòi user biết địa chỉ ô.
- Backend lấy nguồn trước khi chạy Python, lưu snapshot bất biến và stage file theo thứ tự sau input Artifact. JSON nguồn native có ref, metadata, dữ liệu và revision/version; không có credentials. Trả đường dẫn input trong observation và mô tả quy ước để code sử dụng.
- Tổng input Artifact + Google không quá 3; 20 MiB/file, 40 MiB tổng, giữ trần code/log/tài nguyên hiện có. JSON Google mặc định giới hạn 2 MiB như HTTP client hiện tại; dữ liệu lớn đọc từng range/lượt.
- `google_actions`: tối đa 3 đầu ra Google. `upload` liên kết `output_path` với output Artifact, connection và mode `keep_format`/`google_native`; tạo file mới mặc định, `input_index` tùy chọn cho phép thay bytes đúng file binary đã download, giữ nguyên MIME và link, không dùng conversion để thay toàn bộ Google native ngầm. `batch_update` liên kết snapshot nguồn bằng chỉ số input và đường dẫn JSON request do Python tạo.
- `PythonOutput.publish` mặc định true; chỉ cho false đối với JSON control được một action tham chiếu. File control không thành thẻ tải xuống hoặc file thư viện. Primary chỉ tính output được publish. Kiểm tra đường dẫn/size/hash như mọi output khác.
- JSON control là payload Google API, không phải danh sách thao tác Excel/Word tự chế. Docs dùng mảng `requests` của documents.batchUpdate; Sheets dùng spreadsheets.batchUpdate. Backend chỉ gọi endpoint tương ứng file nguồn, tự thêm revision control. Không cho URL, OAuth header, host path, file target ngoài quyền hoặc yêu cầu chia sẻ/xóa file/Apps Script/nguồn dữ liệu ngoài.
- JSON snapshot có thể được Python đọc và code sinh request cho nội dung, công thức, kiểu chữ/đoạn/bảng, cấu trúc sheet và biểu đồ trong phạm vi Google API được hỗ trợ. Dùng bộ kiểm tra từng nhóm request để phân biệt cập nhật nội dung với mở rộng quyền/truy cập nguồn ngoài; không passthrough HTTP tùy ý.
- Với file mới, Python tạo XLSX/DOCX/PDF như hiện tại. Backend lấy bytes theo version và upload; XLSX→Sheets, DOCX→Docs khi chọn chuyển đổi. Không hứa mọi đặc tính Office giữ nguyên sau conversion.

Đây là lựa chọn kỹ thuật của người triển khai: giao tiếp bằng snapshot/control file giúp giữ sandbox không mạng và receipt phát lại được; không cần SDK OAuth trong container. Agent có thể dùng nhiều lần gọi Python để đọc cấu trúc, xử lý, tự kiểm tra và sửa. File mới từ Google được đăng ký cho owner/hội thoại; chỉ cấp tiếp cho cùng run nếu journal chứng minh đã tạo thành công.

## Backend và dữ liệu bền vững

Tận dụng OAuth/credentials/access/client và bảng Google hiện có. Thêm bridge nội bộ cho snapshot, payload validation và execute; không xóa kết nối, grant, ciphertext hoặc journal cũ.

Python receipt phải lưu snapshot/StoredArtifact trước khi chạy code; resume không đọc bản mới rồi đổi dữ liệu của một operation cũ. Fingerprint ghim arguments, local input hash và identity hợp đồng. Broker job fingerprint ghim thêm các input snapshot đã lưu. Sau code chạy, receipt `executed` lưu tất cả file output (cả control) trước remote write. Google operation key ổn định theo run + Python call + action index.

Mọi action được parse/kiểm quyền trước lần ghi đầu tiên. Kết quả có `google_results` gồm operation ID, file ref/URL, trạng thái. Phần đã ghi thành công không được thực hiện lại khi retry. Python tự kiểm tra bằng lần gọi đọc mới; không giả vờ kiểm tra Google trước khi backend đã commit.

Giữ journal `prepared/approved/sent/completed/unknown/rejected`; với bridge mới `approved` chỉ là trạng thái nội bộ của quyền đã cấp, không tạo AgentApproval. `sent/unknown` không retry mù, kể cả operation key mới trên cùng đích. Lỗi/mất phản hồi sau RPC phải nói rõ kết quả chưa xác định. Cancel không hoàn tác ghi đã gửi. Báo cáo phần thành công trước khi dừng; không hứa transaction xuyên nhiều tài liệu.

Quyền kiểm tra lại khi lấy input, trước mỗi write và trước khi trả receipt; revoke/reconnect làm pin cũ hết hiệu lực. OAuth offline tự refresh, chỉ yêu cầu kết nối lại nếu credentials không còn hợp lệ; không hứa đăng nhập một lần vĩnh viễn.

## Xung đột

Lấy metadata phiên bản trước/sau snapshot để phát hiện thay đổi trong lúc đọc. Trước lần ghi đầu, kiểm tra tất cả nguồn đã dùng; trước mỗi action kiểm tra đích. Một call không ghi cùng file hai action riêng: gộp vào một batch để tránh tự xung đột.

Docs dùng `requiredRevisionId` từ snapshot, không nhận revision tùy ý do code thay thế. Sheets/file binary so sánh Drive version + snapshot ngay trước RPC. Google Sheets không cung cấp trong API đang dùng một điều kiện ghi nguyên tử tương đương Docs: có khoảng đua giữa check và write. Không tuyên bố loại bỏ được mọi thay đổi đồng thời. Khi quan sát được xung đột thì chặn tuyệt đối, không tự merge/retry.

Lưu marker xung đột theo AgentRun, chặn các Google read/write tiếp theo trong cùng run để LLM không lách bằng call mới. Người dùng bắt đầu yêu cầu mới khi muốn tiếp tục. Nếu action trước đã hoàn tất, giữ receipt và báo rõ; không rollback ngầm.

## Automations và tương thích

Không thêm approval Python hoặc Google trong run mới. Automation vẫn dùng revision và grant cụ thể; không suy từ OAuth ra quyền cho toàn bộ Drive. Thêm quyền generic native edit rõ ràng cho revision mới; không tự mở rộng quyền range/action của revision cũ. Grant cũ chỉ được dùng nếu bridge chứng minh từng request nằm trong quyền cũ; nếu không thì từ chối và hướng dẫn cập nhật cấu hình automation.

Ẩn/xóa đăng ký các tool `google_*` cho run mới sau wiring Python. Checkpoint cũ chỉ có adapter hẹp: kết quả đã hoàn tất replay từ journal, lời gọi cũ chưa rõ không tự chạy; thao tác chưa thực hiện trả hướng dẫn bắt đầu run mới. Không xóa sent/unknown để mở khóa.

Retire từng phần sau khi có coverage: schemas prepare/commit dùng riêng cho tool cũ, `GoogleToolService.invoke`, adapters cũ chỉ còn chỗ cần compatibility, approval prompt/allowlist/schema tuyến mới, tests chỉ phản ánh hợp đồng cũ. Giữ reader/validator phục vụ snapshot, OAuth, Picker, grants, client, journal và lịch sử. Không xóa API/response DB đang phục vụ UI/receipt. Cập nhật script nghiệm thu, tài liệu và UI Google results; không nhân đôi thẻ Artifact.

## Nghiệm thu

- Một tool Python chung; không Google approval trong chat hoặc Automation đã có grant hợp lệ.
- Native Sheets/Docs read → code tạo request → write → readback; nội dung và bố cục do prompt tổng thể, user không cần chỉ tọa độ.
- XLSX/DOCX tạo mới → chuyển thành Google file; binary download/export → Python → output; upload đúng owner/version/hash.
- Phạm vi từng file; chống ref/connection/context giả, control JSON sửa target, request lồng tham chiếu ngoài phạm vi, reconnect/revoke giữa các bước.
- Xung đột snapshot/revision → không ghi, marker chặn call mới; ghi một phần báo đúng phần thành công. Kiểm riêng race giới hạn Sheets.
- Crash trước/sau Python, trước/sau remote send, sau remote success trước receipt; không code/ghi lặp; unknown giữ chặn.
- Giữ nguyên stream, primary, file gốc/phiên bản, Excel/DOCX/PDF đã nghiệm thu; mặc định wire của call cũ không đổi.
- Docker thật + PostgreSQL QA riêng, fake Google/model; không Google thật hoặc database ứng dụng khi chưa được user yêu cầu nghiệm thu live.

## Tài liệu chính thức

- https://developers.google.com/workspace/drive/api/guides/manage-downloads
- https://developers.google.com/workspace/drive/api/guides/manage-uploads
- https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/batchUpdate
- https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/batchUpdate
- https://developers.google.com/identity/protocols/oauth2/web-server
