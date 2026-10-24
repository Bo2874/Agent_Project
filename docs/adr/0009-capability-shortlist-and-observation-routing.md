# ADR 0009: Chọn capability bằng provider inject và reselect theo observation

- Status: accepted, 2026-10-19 (Ngày 91, phạm vi code/injection; Jev runtime chưa nghiệm thu).

## Quyết định

Core ShortlistRouter chỉ trao metadata đã lọc quyền cho DecisionProvider, chọn một tool/Skill
hoặc none/search_more; không sinh arguments hoặc gọi tool. Backend sở hữu dependency/lifecycle
và runtime scope. Dependency mặc định None; Jev config/telemetry/SDK contract vẫn hoãn theo user.
Không tạo RoutingDecision với model version/usage Jev giả để vượt qua phần hoãn.

Graph chọn ở context node trước model và sau mỗi batch observation, commit lựa chọn cùng loaded
schema/Skill. Chỉ lựa chọn được nạp schema; required Skill tools là nhóm atomic trong token budget.
Authorization/approval/quota/executor giữ nguyên quyền kiểm soát. Plugin Skill có metadata guard
riêng để kiểm tra grant/package/dependency mà không đọc instructions chưa chọn.

Search_more mở trang metadata của cùng snapshot, có giới hạn; không refresh quyền hoặc discovery
server. None/exhaustion cho phép model trả lời/hỏi lại với 0 tool, không mở lại toàn catalog.
Raw observation body/data không đi provider; chỉ gửi status projection cùng goal và metadata.
Privacy config cho goal và benchmark/fallback còn ở bước sau.

Shortlist policy `shortlist1.1.0` đưa metadata đã lọc quyền vào các trang theo thứ tự ổn định,
không tìm kiếm/xếp hạng bằng từ khóa trước khi provider chọn. Query vẫn được gửi trong goal
để provider đánh giá khả năng phù hợp từ mô tả. Choice đã lưu với policy cũ cần run mới.

Checkpoint pin choice/step/identity, catalog + policy limits + schema budget và runtime digest.
Resume không reselect pending action, không tự chuyển V1/provider. Thay pin hoặc thiếu provider
thì dừng. Tắt flags chỉ đổi chính sách run mới; run đã pin cần provider để tiếp tục đúng cơ chế.

## Hệ quả

- Có thể kiểm tra selection/graph bằng fixture Protocol mà chưa cấu hình/mock SDK Jev.
- Chưa có factory/key/deadline tổng/usage/model version/privacy đầy đủ: không mở Jev production.
- Reselect sau tool batch; không có observation-driven global planner/RAG hoặc reselect giữa
  các tool trong batch. Quyền đã thay đổi yêu cầu run mới, không tự mở rộng quyền.
- Rules 1.1.0 thêm đường Skill metadata khi inject; checkpoint rules 1.0.0 cần run mới.
- Trace/admin metrics/shadow và benchmark quality/cost chưa được nghiệm thu ở ADR này.

[Guide](../routing/CAPABILITY_SHORTLIST.md).
