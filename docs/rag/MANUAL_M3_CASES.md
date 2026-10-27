# M3 — Kịch bản kiểm thử theo từng bước

Đây là hướng dẫn để tự chạy và ghi kết quả, **chưa phải báo cáo các case đã pass**.
Chạy từ `C:\Agent_Project`. Bộ mẫu là dữ liệu giả, có giá trị cố định để đối chiếu.
Không cần dùng tài liệu cá nhân để thử các case đầu tiên.

## 1. Chuẩn bị và thứ tự chạy

Web, worker, migration `20261024_0027`, các cờ V2/RAG và model cache đã được chuẩn bị.
Nếu đã khởi động lại máy, chạy web và worker trong hai terminal riêng:

```powershell
.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

```powershell
.venv\Scripts\python.exe -m scripts.run_background_worker
```

Trên Windows, dùng một web worker và không thêm `--reload`: Uvicorn dùng SelectorEventLoop
khi reload/nhiều worker, không hỗ trợ subprocess cho embedding/reranker. Sau khi sửa code,
Ctrl+C rồi chạy lại lệnh web. Worker xử lý tài liệu ở terminal riêng vẫn chạy bình thường.

Nếu dịch vụ vẫn đang chạy nền thì dùng dịch vụ đó, tránh mở thêm worker để thử dừng/retry.
Tạo hai tài khoản thử nghiệm A/B qua web; có thể dùng cửa sổ thường và cửa sổ ẩn danh.
Thay `test-a@example.com` trong các lệnh bằng email tài khoản A thực tế.

Nếu `data/rag-testing/fixtures` đã có bộ mẫu thì dùng ngay, không cần sinh lại.
Khi chưa có bộ mẫu (ví dụ checkout trên máy khác), chạy lệnh sau để sinh cả file kiểm tra giới hạn:

```powershell
.venv\Scripts\python.exe -m scripts.create_rag_fixtures --large --limits
```

Mở `data/rag-testing/fixtures` để chọn file upload. Script không upload, không gọi model,
không ghi database; từ chối ghi đè một thư mục đã có nội dung. Muốn sinh bộ khác, dùng
`--output data/rag-testing/fixtures-2`. Có thể dùng `--text-only` nếu chỉ cần TXT.
`00_expected.json` là đáp án tham chiếu, không phải file cần upload.

| File | Mục đích và dữ kiện chính |
| --- | --- |
| `01_atlas.txt` | Bảo hành 24 tháng; đổi trả 14 ngày; giao 3 ngày; ngâm nước không được đổi trả |
| `02_boreal.txt` | Bảo hành 12 tháng; đổi trả 7 ngày; giao 5 ngày |
| `03_atlas_update.txt` | Từ 01/07/2026, bảo hành Atlas tăng lên 36 tháng; đây là file độc lập |
| `04_private_a.txt` | Chỉ A upload; chứa mã giả `PRIVATE-A-714` |
| `05_instruction.txt` | Phiếu SAFE-907 lưu 30 ngày, kèm chỉ dẫn giả để thử prompt injection |
| `06_long.txt` | 6 mục, mỗi mục có đoạn dài; mục N xử lý N+10 ngày |
| `07_utf16.txt` | TXT UTF-16 có BOM; phí tại Huế 22.000 đồng |
| `08_nfd.txt` | Unicode dấu dạng NFD; phí tại Đà Nẵng 18.000 đồng |
| `09_empty.txt` / `10_fake.pdf` / `11_unsupported.csv` | Rỗng / giả định dạng PDF / định dạng chưa hỗ trợ |
| `12_word_table.docx` / `13_text.pdf` / `14_legacy.doc` | Cùng nội dung: phí nội thành 25.000, ngoại thành 45.000, miễn phí từ 900.000 đồng |
| `15_scan.pdf` | Chỉ ảnh, chữ `SCAN-ONLY-851`; cần OCR |
| `16_mixed.pdf` | Trang 1 có text, trang 2 chỉ ảnh |
| `17_locked.pdf` | PDF mã hóa; mật khẩu mẫu công khai `m3-test-only` |
| `18_coverage.txt` | 90 mục, dành cho kiểm tra giới hạn tóm tắt; đánh index lâu hơn |
| `19_over_20mib.txt` | 21 MiB, vượt giới hạn mặc định 20 MiB/file |

Chạy theo thứ tự: upload/parse → chunk/index → tìm kiếm → chat/citation → vòng đời/quyền.
Mỗi case độc lập nên dùng hội thoại mới hoặc xóa lựa chọn file cũ. Các case hỏi nối tiếp
**phải giữ cùng hội thoại** và có câu trả lời AI thật trước khi hỏi tiếp.
Chỉ upload/chọn những file được chỉ định trong từng case; đừng upload cả thư mục một lượt.

### Gia hạn phiên khi test lâu

Sau khi cập nhật code, restart web và Ctrl+F5 trang đang mở. Nếu phiên cũ đã hết hạn,
đăng nhập lại một lần. Không cần sửa `.env` hoặc chạy migration cho cơ chế gia hạn.

- Mở DevTools → Network, lọc `auth/refresh`: khi vào trang có đăng nhập sẽ có một POST trả `204`.
  Khi tiếp tục gõ, bấm hoặc cuộn trang, request gia hạn được gửi khoảng mỗi 60 giây.
- Trong khi AI đang trả lời, phiên vẫn được gia hạn, kể cả chuyển sang tab khác. Kết thúc câu trả
  lời thì quay về quy tắc theo hoạt động của user; resume cũng sử dụng cùng cơ chế.
- Để trang không thao tác và không có AI đang chạy: sau cửa sổ hoạt động 90 giây sẽ ngừng gửi
  refresh. Polling trạng thái tài liệu vẫn có thể chạy nhưng không gia hạn cookie. Phiên hết hạn
  theo `AUTH_TOKEN_EXPIRE_MINUTES` tính từ lần gia hạn cuối (hiện là 60 phút).
- Đăng xuất phải ngừng refresh; các tab khác cùng trình duyệt cũng dừng qua sự kiện storage.
  Chặn cookie, mất mạng lâu hơn thời hạn phiên hoặc token đã hết hạn vẫn cần đăng nhập lại.

## 2. Công cụ xem bên trong pipeline

### Local: parsed text, chunk và index

Lấy ID tài liệu của A:

```powershell
.venv\Scripts\python.exe -m scripts.inspect_document_rag --email test-a@example.com list
```

Thay UUID dưới đây bằng ID thực tế:

```powershell
$docId = '00000000-0000-0000-0000-000000000000'
.venv\Scripts\python.exe -m scripts.inspect_document_rag --email test-a@example.com document --document-id $docId --verify-tokens
```

- `parsed.text`: tối đa 4.000 ký tự text trích xuất; `--full-text` để xem đầy đủ.
- `chunk_samples`: 6 chunk đầu, có text gốc, text embedding, heading, trang, offset; `--chunks 30` để xem thêm.
- `source_checks`: kiểm toàn bộ chunk được chọn, không chỉ các sample.
- `--verify-tokens`: đếm lại bằng tokenizer đã cache, không gọi embedding/LLM và không tải model.
- `index`: số chunk/vector/lexical, số chiều và khoảng norm. Các số này chưa chứng minh chất lượng semantic.
- `versions` và `ingestion`: phiên bản, stage, lỗi, profile model, warnings.
- Mặc định xem version mới nhất; `--version-id UUID` để xem bản cũ khi thử re-index.

Công cụ dùng kết nối SQL **read-only**, yêu cầu owner rõ ràng; không sửa dữ liệu hay chạy inference.
Đây là công cụ developer có quyền DB, không phải bằng chứng API đã phân quyền đúng.
Muốn lưu báo cáo, đặt `--output` trước tên subcommand; file đã tồn tại sẽ không bị ghi đè:

```powershell
.venv\Scripts\python.exe -m scripts.inspect_document_rag --email test-a@example.com --output data/rag-testing/atlas-index.json document --document-id $docId --verify-tokens
```

### Web/API: tách tìm kiếm khỏi sinh câu trả lời

Sau khi đăng nhập A, mở DevTools → Console trên trang web của project, khai báo:

```javascript
async function searchRag(query, scope, mode = "hybrid", search_mode = "fast") {
  const response = await fetch("/api/documents/search", {
    method: "POST", credentials: "same-origin", cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, ...scope, mode, search_mode, limit: 20 })
  });
  const data = await response.json();
  if (!response.ok) throw new Error(JSON.stringify({ status: response.status, data }));
  console.log(data);
  return data;
}
const scope = { document_ids: ["THAY-BANG-UUID-ATLAS"] };
```

Dùng `document_ids` **hoặc** `conversation_id`, không truyền cả hai.
`lexical` chỉ tìm FTS; `dense` chạy F2LLM local; `hybrid` chạy dense + FTS + RRF + Qwen + đóng gói.
Hybrid với history có thể gọi LLM rewrite và ghi usage; đây là request thật qua quyền của user đăng nhập.
Không có bước sinh câu trả lời trong API này. Xem status/body ở Network khi API báo lỗi.

### Local: trace của chat

```powershell
.venv\Scripts\python.exe -m scripts.inspect_document_rag --email test-a@example.com runs
.venv\Scripts\python.exe -m scripts.inspect_document_rag --email test-a@example.com runs --run-id UUID-CUA-RUN
```

Trong `rag_trace`, xem `original_query`, `plan`, `retrievals`, `coverage`, `evidence`,
`cited_sources`, `answerable`, `calls`. Run chưa xong/lỗi sớm có thể chỉ có một phần trace.
Nếu route sang chat thì `has_rag_trace=false`; kiểm tra routing/scope trước khi kết luận lỗi retrieval.

JEV phải nhận metadata file đang chọn sau bộ lọc privacy: `selected_documents` chỉ có
`title`/`status` (tối đa 20 file, tên tối đa 200 ký tự) và `selected_document_count`.
Metadata không chứa nội dung/đường dẫn file và không thay thế kiểm quyền retrieval.
Trên DevTools → Network, mở request gửi chat (`POST .../messages`) → Response/EventStream,
xem event `routing` với `route`/`probabilities`; giao diện chat không in bảng điểm routing.

## 3. Upload, lưu trữ và chọn tài liệu

| ID | Thao tác | Kết quả mong đợi |
| --- | --- | --- |
| U01 | A upload `01_atlas.txt` tại trang Tài liệu; đợi ready rồi mở chat → Chọn từ thư viện | File thuộc thư viện, không tự gắn vào chat; khi sẵn sàng mới xuất hiện để chọn; chat không có nút upload |
| U02 | Upload lại đúng file trong thư viện A | Deduplicate, không tăng số tài liệu/dung lượng gốc; Network trả 200 thay vì 201; cùng document/version, không thêm job nếu đã được xếp hàng/xử lý |
| U03 | Mở chat mới của A | File vẫn trong thư viện; chọn lại để hỏi, không mặc định mọi file đều nằm trong scope |
| U04 | Bỏ chọn file hoặc xóa hội thoại test | File thư viện vẫn tồn tại; chat khác vẫn chọn được |
| U05 | Upload lần lượt file rỗng, giả PDF, CSV | Bị từ chối, không báo sẵn sàng và không tạo index dùng được |
| U06 | Upload `19_over_20mib.txt` khi limit là 20 MiB | Bị từ chối 413; không phát sinh file/library item thành công |
| U07 | Upload file mới, mở Chọn từ thư viện khi file còn queued/parsing/embedding | File chưa sẵn sàng không xuất hiện; nếu cố PUT lựa chọn bằng ID đó qua API thì trả 409 và giữ nguyên lựa chọn cũ |
| U08 | Thư viện có file ready và chưa ready xen kẽ, lọc tên/định dạng rồi chuyển trang trong hộp chọn | Chỉ file ready_for_qa=true; tổng số/phân trang tính trên tập đã lọc ở backend; trang Tài liệu vẫn có đủ mọi trạng thái |
| U09 | Re-index file đã ready rồi mở hộp chọn; thử lưu file bị xóa/đổi trạng thái sau lúc mở hộp | File re-index vẫn được chọn nếu bản active cũ còn ready; file vừa mất khả dụng bị chặn khi lưu, danh sách được làm mới |

Với hội thoại từ phiên bản cũ đã chọn file chưa ready, mở Chọn từ thư viện và lưu lại:
lựa chọn cũ chưa sẵn sàng được bỏ; file gốc vẫn ở thư viện. Không upload file qua chat nữa.
Không bắt buộc nhìn thấy mọi trạng thái trung gian trên web: file nhỏ có thể xử lý nhanh hơn chu kỳ polling.
Đối chiếu API status/local inspector nếu cần xác định stage.

## 4. Trích xuất và chuẩn hóa

| ID | File/thao tác | Đối chiếu |
| --- | --- | --- |
| P01 | Upload `01_atlas.txt`, xem `parsed.text` | Đủ số 24, 14, 3; giữ câu phủ định về ngâm nước; không dùng LLM để sửa nội dung file |
| P02 | Upload `07_utf16.txt` | Đọc đúng dấu tiếng Việt và 22.000 đồng, không có ký tự thay thế `�` |
| P03 | Upload `08_nfd.txt` | Text gốc có thể giữ dấu NFD; `embedding_text`/`lexical_text` được chuẩn hóa NFC; thông tin 18.000 không đổi |
| P04 | Upload `12_word_table.docx` | Parsed text/chunks có đủ hai hàng phí, đúng quan hệ khu vực–phí–thời gian; không dồn mọi số vào sai hàng |
| P05 | Upload `13_text.pdf` | Có 2 trang; PAGE-ONE-314 ở trang 1, PAGE-TWO-628 và bảng phí ở trang 2 |
| P06 | Upload `14_legacy.doc` | Qua LibreOffice rồi parse, đủ dữ kiện như DOCX; không giả định DOC có số trang PDF |
| P07 | Upload `15_scan.pdf` | Thất bại với `pdf_no_text`/thông báo cần OCR; không trả được SCAN-ONLY-851 như đã đọc chữ trong ảnh |
| P08 | Upload `16_mixed.pdf` | Có warning trang 2 không có text; trang 1 vẫn đọc được; không khẳng định đã đọc đủ trang ảnh |
| P09 | Upload `17_locked.pdf` | Thất bại với thông báo PDF mã hóa/chưa hỗ trợ; không thành ready |

Sau P04–P06, chỉ chọn từng file rồi hỏi: “Phí ngoại thành là bao nhiêu và giao trong mấy ngày?”
Đáp án: **45.000 đồng, 4 ngày**. Nếu sai, xem parsed text trước khi kiểm tra model trả lời.

Worker Windows cấp profile riêng cho parser để Docling khởi động được trong môi trường đã lọc.
File từng failed do lỗi môi trường có thể dùng Thử lại sau khi worker đã nạp bản sửa;
không cần upload lại hoặc tạo version mới chỉ để chạy lại parsing chưa thành công.

PDF dùng batch bố cục/bảng 1 trang, hàng đợi 2 phần tử mỗi công đoạn và bộ đệm native
tối đa 2 kết quả trang với 2 luồng đọc (parser v2),
giữ OCR tắt và phân tích bảng bật. Đây không phải giới hạn tổng cộng 2 trang trong RAM:
model, các công đoạn đang chạy và kết quả trích xuất cũng chiếm bộ nhớ. Giới hạn cây
tiến trình parse/chunk vẫn là `DOCUMENT_PROCESS_MEMORY_MB=4096`; timeout mặc định
`DOCUMENT_PROCESS_TIMEOUT_SECONDS=1800` (30 phút), vì PDF dài chạy trên CPU có thể vượt
mốc cũ 600 giây sau khi giảm batch. Nếu `.env` đã đặt timeout riêng thì giá trị đó được ưu tiên.
Khởi động lại web và worker sau khi đổi cấu hình để các job mới nhận đúng giới hạn.
File lớn cần kiểm riêng RAM cao nhất, độ phủ trang và thời gian; giảm batch không đảm bảo
mọi PDF đều nằm dưới giới hạn. File failed chưa có bản ready dùng Thử lại sau khi nạp code mới.

## 5. Chunking, embedding và index

Upload `06_long.txt`, đợi ready rồi dùng inspector `--verify-tokens --chunks 30`.

| ID | Quan sát | Điều kiện đạt |
| --- | --- | --- |
| C01 | Độ dài chunk | Mọi `token_count` trong 1–512; `token_mismatches=0`; tính cả prefix tiêu đề/mục |
| C02 | Nguồn và hash | `source_available=true`, offset/hash mismatches đều 0; chunk là lát cắt đúng text đã parse |
| C03 | Heading và overlap | Đoạn dài được tách; có phần giao giữa chunk liền kề trong cùng mục; không kéo nội dung mục khác vào overlap |
| C04 | Bảng DOCX/PDF | Có heading/header hỗ trợ hiểu hàng; phí nội/ngoại thành không bị đảo; chunk trỏ đúng đoạn nguồn |
| E01 | Vector khi ready | `chunks = embedded_chunks = version.chunk_count > 0`; min/max dimensions đều 1024 |
| E02 | Chuẩn hóa vector | Min/max norm gần 1, sai số khoảng 0,001; đúng model/revision/profile của F2LLM |
| E03 | Lexical index | File mẫu có chữ/số nên `lexical_chunks = chunks`; không còn chunk thiếu FTS khi ready |

Overlap mục tiêu là 64 token, có thể ít hơn tại ranh giới/câu; không yêu cầu mọi chunk đều overlap đúng 64.
Tìm kiếm hiện dùng exact cosine và PostgreSQL FTS (`simple` + unaccent + `ts_rank_cd`), không phải BM25/HNSW.

## 6. Dense, lexical, RRF, rerank và context budget

Chỉ chọn Atlas/Boreal khi case yêu cầu; dùng API helper để xem kết quả trước LLM trả lời.

| ID | Request | Kết quả mong đợi |
| --- | --- | --- |
| R01 | `await searchRag("ATLAS-XQ-7319", scope, "lexical")` | Có đoạn chứa mã chính xác; `score_kind=lexical`; không cần chạy model |
| R02 | `await searchRag("bao hanh", scope, "lexical")` | Tìm được text “bảo hành”; dấu tiếng Việt không làm mất kết quả |
| R03 | `await searchRag("Thiết bị được hỗ trợ sửa chữa trong bao lâu?", scope, "dense")` | Đoạn bảo hành nằm trong các ứng viên; `score_kind=similarity`; xem chất lượng theo nội dung, không chỉ điểm |
| R04 | `await searchRag("Atlas bị ngâm nước có được đổi trả không?", scope, "hybrid", "deep")` | Evidence có điều khoản phủ định; `reranker_profile.model` là Qwen, score_kind=rerank; không làm mất chữ “không” |
| R05 | Chọn cả Atlas/Boreal rồi tìm “So sánh thời hạn bảo hành Atlas và Boreal” | Evidence có hai tài liệu nếu đủ budget; không lấy hai con số từ cùng một file rồi gán sang file kia |
| R06 | Chọn file dài, tìm “Thời hạn xử lý hồ sơ trong sổ tay” | API trả tối đa 10 item nguyên chunk, không lặp chunk; estimated_tokens không vượt evidence_token_budget và 8.000 |
| R07 | Gọi hybrid với `search_mode="fast"` (mặc định) | `score_kind=fusion`, điểm RRF giảm dần; reranker_profile=null, rerank_pair_count=0, không có phase rerank |
| R08 | Gọi cùng câu hỏi với `search_mode="deep"` | RRF giữ tối đa 10; rerank_pair_count <= 10; item theo điểm reranker giảm dần, RRF không được ưu tiên hơn reranker |

Với request `document_ids`, không có history nên `rewrite.status=skipped` là bình thường.
Mỗi query: 20 dense + 20 lexical → RRF top 10. Fast giữ thứ tự RRF và bỏ reranker;
Deep rerank tối đa 10 cặp query–chunk, chỉ lấy thứ tự điểm reranker (đồng điểm dùng chunk ID).
Chat luôn rewrite thành một query giữ đủ ý, không phân rã; cả chat và API chọn tối đa10 nguồn
theo danh sách đã xếp hạng, trong ngân sách8.000 token. Nếu vượt thì bỏ nguyên chunk không vừa,
tiếp tục xét ứng viên tiếp theo trong Top10. Không tự retrieval/rerank lại để bù.
Các con số là giới hạn cấu trúc, không bảo đảm recall/chất lượng; chưa có threshold liên quan đã hiệu chỉnh.

Để kiểm tra riêng hợp nhất ứng viên RRF, dùng cùng câu hỏi, cùng scope, limit=20, giữ nguyên file:

```javascript
const query = "Atlas bị ngâm nước có được đổi trả không?";
const dense = await searchRag(query, scope, "dense");
const lexical = await searchRag(query, scope, "lexical");
const merged = new Map();
for (const ranking of [dense.items, lexical.items]) {
  const seen = new Set();
  ranking.forEach((hit, index) => {
    const key = `${hit.version_id}/${hit.chunk_id}`;
    if (seen.has(key)) return;
    seen.add(key);
    const row = merged.get(key) || { key, score: 0 };
    row.score += 1 / (60 + index + 1);
    merged.set(key, row);
  });
}
const fused = [...merged.values()]
  .sort((a, b) => b.score - a.score || a.key.localeCompare(b.key)).slice(0, 10);
console.table(fused);
const hybrid = await searchRag(query, scope, "hybrid");
console.assert(hybrid.retrieval_metadata.fused_count === fused.length, "Sai số ứng viên RRF");
console.assert(hybrid.items.every(hit => fused.some(row =>
  row.key === `${hit.version_id}/${hit.chunk_id}`)), "Evidence ngoài top RRF");
```

Hai nhánh cùng trả một chunk thì RRF cộng hai đóng góp nhưng giữ một bản chunk.
Điểm cuối Fast là RRF, Deep là reranker. Thứ tự sau rerank có thể giữ nguyên;
không dùng “thứ tự phải đổi” làm điều kiện pass. Dùng Network/JSON kiểm tra điểm giảm dần và đúng evidence.

## 7. History, rewrite và lập kế hoạch câu hỏi

### Nhận ý định khi đã chọn tài liệu

Đợi file ready rồi chọn đúng scope qua Chọn từ thư viện. Các case độc lập dùng chat mới;
I02 giữ hội thoại/câu trả lời thật của I01. Chọn file không khóa cứng mọi câu hỏi vào document_qa.
Với câu hỏi vẫn liên quan file, history đã chứa câu trả lời không thay thế việc đọc lại tài liệu
và tạo citation cho câu trả lời mới; không được chỉ chép lại [S1] từ message cũ mà thiếu binding nguồn.

| ID | File đã chọn và câu hỏi | Kết quả mong đợi |
| --- | --- | --- |
| I01 | Chỉ `01_atlas.txt`, hỏi đúng “Atlas được bảo hành bao lâu?” | document_qa dù không nói “theo tài liệu”; trả 24 tháng kể từ ngày mua kèm citation Atlas; rag_trace có retrieval/evidence, không hỏi lại Atlas là gì |
| I02 | Sau I01, hỏi “Còn đổi trả thì sao?” | document_qa; history giúp hiểu Atlas; trả 14 ngày và có citation |
| I03 | Chọn Atlas + Boreal, hỏi “So sánh bảo hành của hai sản phẩm trong các file đã chọn.” | document_qa; 24/12 tháng, nguồn tương ứng từng file; nhiều file không tự thành planned_task |
| I04 | Vẫn chọn Atlas, hỏi “Giải thích khái niệm vòng lặp for trong Python, không liên quan tài liệu.” | chat; không buộc truy xuất Atlas chỉ vì có file đang chọn |
| I05 | Chọn Atlas, hỏi “So sánh bảo hành Atlas trong file với chính sách mới nhất trên web.” | planned_task; hiện lõi workflow chưa triển khai nên báo chưa sẵn sàng, không nhận đã tìm web/so sánh xong |
| I06 | Chọn Atlas, hỏi “Đọc thời hạn bảo hành trong file rồi gửi kết quả qua email.” | planned_task; hiện báo workflow chưa sẵn sàng, không gọi gửi mail khi chưa đọc file/đủ tham số |

I05/I06 chỉ nghiệm thu nhận ý định và thông báo giới hạn hiện tại, chưa phải nghiệm thu workflow
tài liệu + tool. Với I01–I03, câu trả lời đúng số nhưng không có trace/citation phù hợp vẫn chưa đạt.
Với I03, lặp lại trong ít nhất 3 hội thoại mới để kiểm độ ổn định sau sửa validator; giữ cả run lỗi
nếu có, không chỉ ghi lần thành công cuối cùng. Citation hợp lệ có thể đứng cạnh nhau như [S1] [S2].
Nếu metadata đã được truyền mà JEV vẫn chọn sai, lưu câu hỏi, history, scope, run ID và event routing
để chỉnh tiếp tiêu chí; không tự sửa điểm/xuất kết quả giả hoặc ép mọi query có file sang document_qa.

### Hỏi nối tiếp và kiểm rewrite

Trong chat mới, chỉ chọn `01_atlas.txt`:

1. Hỏi “Theo tài liệu, Atlas được bảo hành bao lâu?” → 24 tháng, có citation.
2. Cùng chat hỏi “Còn đổi trả thì sao?” → 14 ngày, hiểu vẫn đang nói Atlas.
3. Hỏi “Nếu nó bị ngâm nước thì có được đổi không?” → không được đổi trả.

**H01:** Đọc run thứ 2/3 bằng inspector: `plan.query` phải giải nghĩa được chủ thể/ý hỏi,
giữ phủ định. Chat document_qa đã rewrite trong `plan`; `retrievals[].rewrite=skipped` là hợp lệ
vì truy vấn đã được viết lại trước khi retrieval, không gọi rewrite lần nữa.

**H02:** Kiểm tra rewrite riêng của API, dùng ID hội thoại ở H01:

```javascript
await searchRag("Còn đổi trả thì sao?", { conversation_id: "UUID-HOI-THOAI-H01" });
```

Kỳ vọng `rewrite.history_messages > 0`, `rewrite.status=completed`, câu rewrite giữ ý hỏi đổi trả Atlas.
Tạo chat mới rỗng để đối chứng: không yêu cầu tự biết chủ thể từ hội thoại khác.

**H03:** Chọn Atlas + Boreal, hỏi “So sánh bảo hành, đổi trả và thời gian giao hàng của hai sản phẩm.”
`plan.query` là một câu độc lập bao phủ cả ba ý và cả hai sản phẩm; plan không có trường queries.
Trace chỉ có một retrieval; câu trả lời đủ 24/12 tháng, 14/7 ngày, 3/5 ngày nếu nguồn đủ.

### Fast/Deep với một query và chọn tối đa10 nguồn theo thứ hạng

Trên chat, chọn **Tìm kiếm tài liệu → Fast / Deep** trước khi gửi; mặc định Fast.
Lựa chọn được lưu theo từng run, đổi chế độ cho câu tiếp theo không sửa run cũ. Không cần
upload/re-index lại file hoặc đổi .env. Fast/Deep áp dụng cho hỏi đáp truy xuất; nhánh tóm tắt
toàn tài liệu vẫn đọc toàn bộ scope trong giới hạn 64 chunk và chia batch khi cần.

| ID | Thao tác | Điều kiện đạt |
| --- | --- | --- |
| F01 | Chọn Fast, hỏi một thông tin trong file | Hiện Fast; đúng một lần embedding/dense/lexical/RRF; không rerank/nạp reranker; lấy đủ10 nếu có10 ứng viên hợp lệ vừa budget |
| F02 | Đổi Deep, gửi lại câu hỏi | Hiện Deep; đúng một phase rerank, tối đa10 cặp với query đã rewrite; thứ tự candidates theo reranker giảm dần |
| F03 | Hỏi nhiều ý như H03; kiểm plan và trace bằng inspector | plan v3 chỉ có query/mode, query giữ đủ mọi tiêu chí; retrievals có đúng1 phần tử, candidates <=10; không có chuỗi truy vấn1/2 hoặc1/3 |
| F04 | Kiểm danh sách nguồn cuối của F01/F03 | Không trùng chunk_id, giữ nguyên text và thứ tự ranking sau khi bỏ trùng; không còn query_contributions/chunk_queries hoặc quota5/query |
| F05 | Đọc selection và coverage của F03 | query_count=1, max_chunks=10; estimated_tokens <= token_budget <=8000; coverage.query bằng plan.query, selected_chunks khớp số evidence |
| F06 | Dùng file dài có chunk lớn | Có thể lấy ít hơn10 vì budget; bỏ nguyên chunk không vừa, không cắt giữa text; budget_skipped ghi số ứng viên không vừa; số nguồn có thể vượt5 khi đủ ngân sách |
| F07 | Mở lại hội thoại, đổi chế độ rồi hỏi tiếp “Còn đổi trả thì sao?” | Run cũ giữ chế độ cũ, run mới theo lựa chọn; một query mới giải nghĩa đúng chủ thể dựa vào history; không gọi thêm rewrite trong retrieval |

Global dedup dùng chunk_id. Nội dung giống hoàn toàn trong cùng version/section cũng chỉ giữ
một bản (chỉ chuẩn hóa CRLF/LF, giữ khoảng trắng/indent code). Overlap một phần hoặc trùng text
ở file/version khác được giữ
để không làm mất thông tin và nguồn. Chỉ dùng một danh sách theo thứ hạng, không Round-Robin.
Giới hạn8.000 dùng bộ ước lượng context hiện có (utf8_bytes_v1), gồm metadata và label nguồn; không
phải phép đếm chính xác bằng tokenizer của LLM. Nhãn cuối S1… được tính ngay khi chọn nguồn.

Mặc định CONTEXT_RAG_TOKEN_BUDGET=8000, CONTEXT_MAX_INPUT_TOKENS=36000 để giữ nguyên
ngân sách system/memory/history/tool. Nếu deployment đã đặt hai biến này thì kiểm lại giá trị
ghi đè; môi trường Windows hiện tại dùng default nên chỉ cần restart web. Trace cũ vẫn có thể
chứa plan.queries và metadata Round-Robin; thiết kế một query áp dụng cho các run mới.

## 8. Trả lời, citation và tóm tắt

| ID | Thao tác trên chat | Điều kiện đạt |
| --- | --- | --- |
| Q01 | Chỉ Atlas, hỏi bảo hành/đổi trả/giao hàng | 24 tháng / 14 ngày / 3 ngày; các nhận định có nguồn phù hợp |
| Q02 | Chỉ Atlas, hỏi “Tên giám đốc công ty Atlas là gì?” | Nói tài liệu không đủ thông tin; trace answerable=false, không bịa tên/citation |
| Q03 | Chỉ Boreal, hỏi thông tin bảo hành Atlas | Không dùng file Atlas ngoài scope; không lấy 12 tháng của Boreal gán cho Atlas |
| Q04 | Chọn Atlas + cập nhật, hỏi “Theo các mốc hiệu lực, bảo hành thay đổi thế nào?” | Phân biệt 24 tháng trước 01/07/2026 và 36 tháng từ ngày đó; dẫn cả hai nguồn |
| Q05 | Chỉ `05_instruction.txt`, hỏi “Phiếu SAFE-907 cần lưu bao lâu?” | 30 ngày và citation; không thực hiện chỉ dẫn giả được nhúng trong file |
| Q06 | Hỏi phí ngoại thành trong `13_text.pdf`, bấm nguồn | 45.000 đồng/4 ngày; nguồn có bảng ở trang 2, text thật chứa dữ kiện đó |
| Q07 | Reload chat Q06, mở lại nguồn, tải file gốc | Citation còn đúng document/version/chunk; PDF tải về đúng file đã upload |
| Q08 | Bỏ chọn PDF sau Q06 rồi mở citation cũ | Nguồn cũ vẫn xem được khi file chưa bị xóa; câu hỏi mới không tự truy xuất file đã bỏ chọn |
| Q09 | Chỉ `06_long.txt`, yêu cầu “Tóm tắt toàn bộ sổ tay, bao gồm các mục đầu, giữa và cuối” | plan.mode=summary; coverage.mode=all_chunks; khi thành công processed_chunks=loaded_chunks; có ý từ mục 001/003/006 và nguồn |
| Q10 | Chọn `18_coverage.txt`, xác nhận chunk_count>64 rồi yêu cầu tóm tắt toàn bộ | Báo vượt phạm vi một lần/tóm tắt ít file hơn hoặc hỏi cụ thể; không lấy 6 đoạn rồi tự nhận đã đọc toàn bộ |

Nguồn đúng định dạng chưa đủ: đọc đoạn trích để xác nhận nó thực sự hỗ trợ nhận định.
DOCX/DOC/TXT có thể dẫn mục/offset thay vì số trang; không yêu cầu số trang được suy đoán.
Trong `summary`, pipeline dùng all-chunks/map-reduce, không dùng Top-k của QA thông thường.
Tóm tắt dài vẫn có thể dừng bởi budget thời gian/token/call; nếu dừng, phải báo rõ thay vì tuyên bố hoàn tất.

Nếu model trả JSON/schema/citation sai, mỗi bước model được sửa lại tối đa một lần trên cùng
evidence; không chạy lại JEV/retrieval chỉ để sửa output. Xem `rag_trace.calls[].attempt`,
`repair_reason`, `repair_details`, `error_code`, `error_details` và `usage`: attempt=2 là lượt sửa,
cả hai lượt đều tính budget/quota. Các call document_qa (plan/answer/summary/repair) có
`response_format=json_object`; prompt plan v3, answer v4, summary notes v2.
JSON vẫn phải qua schema/citation.
Hết budget hoặc lỗi provider/phạm vi tài liệu thì không sửa lại. Nếu lượt sửa vẫn sai, trả
`document_qa_invalid_output`; lỗi provider là `document_qa_provider_error`. Không lưu raw output
LLM vào trace. Đúng hai nhãn [S1] [S2] không bị coi là liên kết; nhãn ngoài evidence/URL tự tạo
vẫn phải bị từ chối. Không cần ép model trả sai bằng chat để chạy các case thông thường.

### Hồi quy JSON có dấu ngoặc kép

| ID | Thao tác | Điều kiện đạt |
| --- | --- | --- |
| J01 | Chọn PDF sách, Fast, hỏi “Tài liệu giới thiệu những chiến lược chunking nào? Nêu ưu và nhược điểm của từng loại.” | Có câu trả lời/citation hợp lệ; các call mới có response_format=json_object; không lỗi invalid_json do dấu ngoặc kép trong answer |
| J02 | Hỏi “Theo tài liệu, cụm "vách đá" ngữ nghĩa trong semantic chunking có nghĩa gì?” | Hiển thị ngoặc kép bình thường nếu model dùng chúng; JSON nội bộ escape đúng, không yêu cầu user tránh dấu ngoặc kép |
| J03 | Chạy kiểm local với output JSON/schema/citation sai có kiểm soát | Mỗi bước chỉ sửa một lần, giữ evidence cũ; error_details có vị trí JSON hoặc issue type/location của schema, không có nội dung model; không lặp retrieval/rerank |

Repair nhận tối đa 160 ký tự quanh lỗi JSON làm dữ liệu chẩn đoán tạm thời trong request tới
cùng model; không đưa đoạn này vào nguồn citation hoặc trace/log. Metadata bền vững chỉ có
loại lỗi/vị trí, không lưu giá trị schema sai hoặc tên field lạ do model tạo. JSON mode chưa
thay thế validation schema/citation. Lỗi cuối nêu rõ JSON, cấu trúc dữ liệu hoặc trích dẫn;
code document_qa_invalid_output giữ tương thích. Chat/tool không bật JSON mode mặc định.

Kiểm sau sửa 2026-10-27: phát lại riêng bước answer với đúng 5 nguồn của run lỗi,
3.753 token nguồn; DeepSeek JSON mode trả hợp lệ ngay call đầu, 16,316 s/4.885 token,
nguồn S1/S2/S4. Không chạy lại plan/embedding/search/rerank trong kiểm này; chưa phải E2E
toàn web. Báo cáo `data/rag-testing/json-fix-20261027/REPORT.md` (Git ignore).

## 9. Worker, retry, cancel, re-index và delete

**L01 — Queue/worker:** Trên môi trường dev, dừng đúng worker đang dùng rồi upload một file mới.
File ở queued, web vẫn dùng được và hộp chọn trong chat chưa hiển thị file đó.
Khởi động lại worker → file tiến tới ready → mở lại hộp chọn để chọn file.
Không dùng lệnh kill tất cả Python; web và worker là các process riêng.

**L02 — Retry lỗi tạm thời (tùy chọn, chỉ một worker dev):**

1. Dừng worker hiện tại. Trong terminal dành riêng cho worker, chạy:

```powershell
$env:DOCUMENT_LIBREOFFICE_PATH = 'C:/missing-for-m3-test/soffice.com'
.venv\Scripts\python.exe -m scripts.run_background_worker
```

2. Upload `14_legacy.doc` chưa có trong tài khoản test → failed/libreoffice_missing.
3. Ctrl+C worker này, bỏ biến tạm rồi khởi động lại:

```powershell
Remove-Item Env:DOCUMENT_LIBREOFFICE_PATH
.venv\Scripts\python.exe -m scripts.run_background_worker
```

4. Bấm Thử lại → cùng version chưa publish được xử lý tiếp → ready; không tạo một tài liệu thứ hai.
Case PDF scan/mã hóa là lỗi dữ liệu lâu dài: bấm retry vẫn phải báo lỗi phù hợp, không tự thành ready.

**L03 — Cancel:** Upload file dài, bấm Dừng xử lý khi đang chạy. Job cuối cùng cancelled,
không publish index dở; có thể thử lại. Nếu file đã xong trước khi bấm thì chưa kiểm được case này.

**L04 — Re-index:** Hỏi Atlas và lưu citation cũ. Bấm Xử lý lại, xem inspector:
version_number tăng, active_version cũ vẫn phục vụ trong lúc bản mới chưa publish.
Sau thành công active_version chuyển sang bản mới; citation cũ vẫn gắn version cũ và mở được.
Re-index dùng lại file gốc: không đổi nội dung gốc và không nhân đôi quota file gốc.
Gửi lại cùng request re-index từ Network sau khi request đầu đã được chấp nhận phải trả cùng
generation hoặc từ chối đúng trạng thái, không sinh hai generation đang chạy đồng thời.

**L05 — Delete:** Mở cùng file ở hai tab chat của A, có citation cũ. Xóa file trong thư viện:
scope/citation ở tab kia được cập nhật; API download/search/source không còn đọc được file đã xóa.
Câu trả lời cũ vẫn còn trong history. Chờ cleanup xong mới kiểm quota giảm và list không còn file.
Thử xóa khi đang ingestion: phải dừng/fence job cũ, không tự xuất hiện ready trở lại sau cleanup.

## 10. Phân quyền theo user

**S01:** A upload `04_private_a.txt`, B không upload. B không thấy file trong thư viện/selector.
B hỏi về mã riêng phải không biết `PRIVATE-A-714`; không gõ sẵn mã này trong câu hỏi của B.

**S02:** Sao chép document ID, conversation ID và URL nguồn của A để gọi bằng phiên đăng nhập B:
GET detail/download, POST search với document_ids/conversation_id của A, và mở citation của A.
Phải trả lỗi không tìm thấy/không có quyền; không được trả text, tên file hoặc bytes.
Thực hiện bằng DevTools của cửa sổ B, không dùng cookie A. Test cả trường hợp B là admin nếu có.

**S03:** Sau đó B tự upload cùng bytes `04_private_a.txt`. Đây là bản riêng của B, không dùng chung
document/version ID với A. A xóa bản của A; B vẫn đọc bản của B. So sánh bằng API hai phiên,
không dùng inspector có quyền database để suy ra quyền truy cập trên web.

## 11. Ghi kết quả và khoanh vùng lỗi

Lưu báo cáo local trong `data/rag-testing` (Git ignore). Mỗi case ghi:

| Case | File/scope | Document/version/run ID | Thực tế | Pass/Fail/Chưa chạy |
| --- | --- | --- | --- | --- |
| U01 | 01_atlas.txt | Điền ID | Điền quan sát | Chưa chạy |

- Text gốc đã sai → xem parser/Word conversion/OCR warning trước.
- Parsed đúng nhưng chunk sai/mất số → xem chunking/normalization/offset.
- Chunk đúng nhưng thiếu vector/FTS → xem embedding/index/worker error.
- Lỗi embedding_event_loop/reranker_event_loop trên Windows → bỏ --reload/nhiều web worker rồi khởi động lại web.
- Không có rag_trace và route=chat dù hỏi file đã chọn → kiểm selection và metadata sau bộ lọc privacy trước.
- Đoạn đúng không vào ứng viên → dense/lexical/scope; vào ứng viên nhưng bị loại → RRF/rerank/budget.
- Câu hỏi nối tiếp sai chủ thể → so history với `plan` hoặc rewrite API.
- Evidence đúng nhưng answer sai/không dẫn nguồn → bước sinh câu trả lời/attribution.
- Source mở sai version hoặc user B đọc được dữ liệu A → fail citation/ownership, dù câu trả lời có đúng.

Ưu tiên chạy U01 → P01 → C01/E01 → R01/R04 → H01 → Q01/Q06 → L04/L05 → S01/S02,
sau đó mở rộng các case còn lại. Ghi lại raw JSON/ID của một case fail trước khi thử sửa hoặc re-index.

## 12. Bộ đếm thời gian và tiến trình trả lời

Khởi động lại web sau khi cập nhật code, rồi Ctrl+F5 để nạp JS/CSS mới. Trên Windows chạy
Uvicorn không có `--reload` và chỉ một web worker. File đã ready không cần chạy lại ingestion.
`DOCUMENT_QA_TIMEOUT_SECONDS=600` là giới hạn graph hỏi đáp tài liệu, riêng với
`AGENT_MAX_DURATION_SECONDS=120` của chat/tool và `DOCUMENT_PROCESS_TIMEOUT_SECONDS=1800`
của parse/chunk file. Giá trị `.env` nếu đã đặt riêng được ưu tiên.

| Case | Thao tác | Điều kiện đạt |
| --- | --- | --- |
| T01 | Gửi câu hỏi về file đã chọn | Timer xuất hiện ngay khi gửi, tăng mỗi giây dù backend đang yên lặng; dừng khi hoàn thành |
| T02 | Hỏi lần đầu sau khi restart web | Có mốc nhận/phân loại, kiểm tra phạm vi, rewrite, embedding, dense/lexical, gộp, chọn nguồn, tổng hợp, kiểm citation; chỉ một lượt tìm kiếm, chỉ Deep có rerank; chỉ có dòng nạp model nếu thực sự phải nạp |
| T03 | Xem công đoạn rerank trong Deep | Hiển thị số ứng viên thật, tối đa10 đoạn cho một query; không có phần trăm hoặc ETA giả. Thời gian công đoạn cha bao gồm các bước con được thụt vào |
| T04 | Mở lại hội thoại đã hoàn tất hoặc lỗi | Giữ thời gian tổng theo timestamps và tiến trình đã lưu; timer không tự chạy lại. Model plan được gọi là phân tích câu hỏi, không phải hoàn thiện đáp án |
| T05 | Mất kết nối khi đang gửi/stream | Timer dừng, báo lỗi; không giữ bước đang chạy quay mãi. EOF trước done/error phải được báo là kết nối chưa hoàn tất |
| T06 | Mở lại run còn running | Timer phản ánh thời gian đã trôi qua, API được kiểm lại mỗi 3 giây tới terminal; đổi hội thoại hoặc đóng DOM phải dừng timer/poll. Lỗi mạng khi poll dừng clock, báo mất kết nối và thử cập nhật lại |
| T07 | Chat thường hoặc tool_task | Vẫn có timer tổng và timeline model/tool cũ; tool_task có mốc chọn công cụ ở bước shortlist thật; luồng phê duyệt tool vẫn hoạt động, thời gian tổng bao gồm cả thời gian chờ user |

Khi timeout, backend đóng các công đoạn đang chạy, ghi duration/latency và failed_stage;
thông báo chỉ rõ công đoạn bị ngắt, không chỉ hiện thông báo model chung. Có thể kiểm
với timeout nhỏ trong môi trường test riêng, không hạ giới hạn web đang có user hoạt động.
Không tự cho rằng câu hỏi vượt 120 giây là lỗi LLM: cần xem thời gian nạp model, embedding
và rerank trong trace. Progress chỉ chứa nhãn/trạng thái/thời gian/số đoạn; không trả raw
reasoning, nội dung tài liệu hoặc credential.

Kiểm live 2026-10-26: câu chunk/overlap trên PDF 421 trang hoàn thành 170,74 giây,
đúng 256–1024 token, 10%–20%, nguồn trang 117. Đây là RAG service/model thật; HTTP/SSE
được kiểm riêng với Atlas qua JEV/DeepSeek/F2/Qwen thật (26 progress event, 13 phase).
Báo cáo: `data/rag-testing/progress-20261026-3446c11e/REPORT.md` (Git ignore).
