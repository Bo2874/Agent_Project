# Agent Workspace Implementation Plan — Roadmap

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Triển khai thiết kế đã duyệt thành các phần dùng được, lần lượt Artifacts, Google Connectors và Automations.

**Architecture:** Giữ Jinja2/FastAPI/PostgreSQL, Tool Registry và durable Agent runtime. Artifact Manager quản lý file/phiên bản; Google adapter giữ credential; scheduler chỉ tạo job cho worker hiện có. Thực thi code/tài liệu chạy trong sandbox ngoài web process.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy/Alembic, PostgreSQL, LangGraph, Jinja2/JavaScript, Docker, openpyxl, python-docx, LibreOffice, Google OAuth/API, zoneinfo.

**Spec:** `docs/superpowers/specs/2026-11-04-agent-workspace-design.md` — được người dùng duyệt ngày 2026-11-04.

## Global Constraints

- “Artifacts → Connectors → Automations”; “Excel → Word → PDF”.
- “Tạo và chỉnh sửa qua chat; chưa có trình biên tập trực tiếp”.
- “Chỉ bấm thẻ hoặc chọn file trong thư viện mới mở preview.”
- “Nhận file mới hoặc bản sửa không tự mở panel đang đóng”.
- “Người dùng chọn từng file; không tìm trên toàn Drive”.
- “Không có trần token/bước/thời gian tổng cho Automation theo lựa chọn user; vẫn ghi usage/chi phí.”
- “Bật lại: Chờ mốc kế tiếp, không chạy bù khoảng đã tắt”.
- Giữ upload tại thư viện; không buộc file làm việc chờ RAG index. Giữ approval hiện tại cho Python tùy ý.
- Không sửa `.env`, gọi LLM trả phí, ghi Google thật hoặc restart dịch vụ đang dùng trong bước lập kế hoạch. Các bước nghiệm thu Google dùng file thử do user chọn khi đến giai đoạn đó.
- Bản mẫu HTML là tài liệu tham khảo; không copy tài khoản, dữ liệu giả, nút mô phỏng hoặc nội dung script mẫu vào sản phẩm.

## Review Focus

1. Chuyển hội thoại trong lúc preview đang tải: không hiện file của hội thoại trước — plan 01/X7.
2. Hai lượt sửa cùng parent: không ghi đè hoặc làm mất một nhánh — 01/X2.
3. Công thức cache cũ/mất ảnh: không công bố số liệu giả hoặc âm thầm làm mất nội dung — 01/X4 và 02/D1.
4. Google ghi xong nhưng mất response: không append lại tự động — 03/G3.
5. Tắt lịch đồng thời scheduler/worker claim: không bắt đầu lượt queued đã bị tắt — 04/A3–A5.

---

## Các kế hoạch thực thi

| Thứ tự | Kế hoạch | Kết quả được nghiệm thu độc lập |
| --- | --- | --- |
| 1 | [01 — Giao diện A và Excel](2026-11-04-workspace-01-excel.md) | Upload/tạo/sửa Excel → version → preview khi chọn → download `.xlsx` thật |
| 2 | [02 — Word và PDF](2026-11-04-workspace-02-documents.md) | Sửa DOCX/PDF có văn bản, render và tải kết quả |
| 3 | [03 — Google](2026-11-04-workspace-03-google.md) | OAuth, chọn từng file, Sheets ba luồng; tiếp theo Docs và upload |
| 4 | [04 — Automations](2026-11-04-workspace-04-automations.md) | Lịch bền vững, chạy nền, stop/pause, lịch sử và inbox |

Đây là bốn sub-project nối tiếp, không triển khai tất cả trong một lượt. Bắt đầu bằng plan 01; chỉ chuyển phần sau khi gate phần trước đạt. Không phải phần nào cũng cần nghiệm thu trả phí: unit và integration dùng model/provider fixture; live cần ghi rõ dữ liệu và môi trường được dùng.

## Quy ước dùng chung trong các plan

- `owner: UUID` luôn lấy từ auth/run đã xác thực, không lấy từ payload LLM. Service kiểm tra lại quyền ở mỗi lần gọi.
- DTO mới là Pydantic `extra="forbid"`; ID là UUID, enum đóng; trả lỗi 404 cho ID không thuộc owner.
- `session: AsyncSession` truyền tường minh; service giao dịch công khai tự commit, helper trong transaction không tự commit. Không giữ DB lock suốt thời gian sandbox/model chạy; chỉ refresh OAuth cần lock tuần tự credential.
- Migration tên dưới đây dành cho head hiện tại `0030`; nếu head đổi, cấp revision tiếp theo và giữ thứ tự, không ghi đè migration của người khác.
- Các bước “commit” chỉ stage file của task đã kiểm; không stage `.env`, dữ liệu thử hoặc sửa đổi ngoài task. Sandbox `.git` read-only thì báo rõ thay vì tự thay quyền.
- Unit test theo phong cách pytest hiện có, không tạo global conftest làm thay đổi toàn bộ suite. Integration PostgreSQL dùng schema thử riêng `test_workspace_<uuid>`, cleanup đúng schema đó; không `drop_all` trên schema đang dùng.
- Câu lệnh Python: `& .\.venv\Scripts\python.exe -m pytest <files> -q -p no:cacheprovider`; các lệnh dưới đây dùng viết tắt `python -m pytest`. Chạy trong venv, không đọc/in secrets. Nếu chưa có runtime phụ trợ phải ghi NOT RUN, không đánh dấu gate đạt.
- Mỗi task: red test cho hành vi mới → triển khai nhỏ nhất → green kiểm tra đích → review diff → commit file task. Không viết unit test chỉ để kiểm màu/font hoặc lặp lại HTML; phần diện mạo dùng screenshot/browser QA.

## Lựa chọn kỹ thuật được chốt trong kế hoạch

- Storage đầu tiên là private local storage qua interface; chưa thêm S3/Redis/Celery.
- Giữ sandbox Python nhỏ hiện tại. Thêm profile Artifacts riêng, không tăng trần hay nới quyền của tool cũ để xử lý Office.
- Artifact runner nội bộ có quyền gọi Docker; app và Agent worker gọi runner bằng giao diện giới hạn, không được nhận quyền Docker host. Runner không giữ Google/LLM credentials. Container xử lý không nhận Docker socket, mạng hoặc secret.
- Structured transforms dùng openpyxl/python-docx; tính lại và render bằng LibreOffice Calc/Writer trong profile riêng. Dùng font Inter/Lora đóng gói kèm giấy phép phân phối; xác minh giấy phép khi tải font.
- Google OAuth dùng `google-auth-oauthlib` cho code flow/refresh; Google REST qua HTTPX async sẵn có. Tham số OAuth và endpoint được backend cố định; không dùng MCP profile nguyên trạng.
- Schedule giới hạn một lần/hằng ngày/hằng tuần, tính bằng `datetime` + `zoneinfo`; thêm `tzdata` cho Windows, không thêm cron engine cho ba kiểu lịch này.
- Exact dependency versions resolve/lock trong task sở hữu dependency, xác minh tài liệu chính thức tại thời điểm thực hiện; chưa cài thêm gì trong bước lập kế hoạch.

## Gate cuối mỗi phần

- Unit tests đích + regression liên quan xanh; Ruff check/format và `git diff --check` đạt.
- Migration thử trên schema sạch có dữ liệu fixture; failure/restart/cancel có bằng chứng phù hợp phần đó.
- Browser QA trên desktop và viewport hẹp; không xem Node syntax check là browser E2E.
- Download/preview/Google links thực sự trỏ đúng kết quả đã kiểm. Screenshot mock không thay nghiệm thu.
- Cập nhật `PROJECT_NOTES.md`, `PROJECT_PLAN.md`, `OPERATIONS.md` theo kết quả thực tế, ghi rõ phần chưa chạy.

## Tự rà soát kế hoạch

- Coverage: spec §5 → 01/X1,X7; §6 → 01/X2–X8 và 02; §7 → 03; §8 → 04; §9–10 → gate từng plan.
- Không thay runtime token của chat ngầm; plan 04/A1 bổ sung policy nullable cho Automation và giữ checkpoint cũ.
- Plan01 đã triển khai, nghiệm thu offline và gộp vào `main` tại máy ngày 2026-11-05.
  Plan02 hoàn tất Word/PDF/preview và đã gộp local vào `main` tại `22def91`;
  gate offline 34 passed, hồi quy sau gộp 1.194 passed/2 skipped (1.106,74 giây).
  Chưa push hoặc bật runtime. Plan03–04 chưa triển khai.
  Bật runner/flag và áp dụng migration trên môi trường người dùng là bước vận hành riêng.

## Tài liệu kỹ thuật dùng để chốt kế hoạch

- [Google OAuth web server](https://developers.google.com/identity/protocols/oauth2/web-server): flow cho backend và offline access; không thay bằng MCP public-client profile.
- [LibreOffice conversion filters](https://help.libreoffice.org/latest/en-US/text/shared/guide/convertfilters.html) và [XCalculatable.calculateAll](https://api.libreoffice.org/docs/idl/ref/interfacecom_1_1sun_1_1star_1_1sheet_1_1XCalculatable.html): xuất tài liệu và tính lại workbook là hai trách nhiệm riêng cần kiểm bằng fixture.
- [Python 3.11 zoneinfo](https://docs.python.org/3.11/library/zoneinfo.html): IANA timezone và tzdata fallback cho Windows. Schedule policy missed/overlap vẫn do ứng dụng quyết định.
