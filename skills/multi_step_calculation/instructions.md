# Multi-step calculation

## Khi nào dùng

- Dùng khi yêu cầu có từ hai phép toán số học phụ thuộc nhau trở lên.
- Dùng khi user muốn xem các bước tính có thể kiểm chứng bằng tool.
- Không dùng cho chứng minh toán học, đại số ký hiệu, thống kê nâng cao hoặc bài toán cần tool khác.

## Quy trình

1. Xác định biểu thức cuối cùng, các số đầu vào, đơn vị và thứ tự ưu tiên. Nếu thiếu dữ liệu làm thay đổi kết quả, hỏi lại user trước khi gọi tool.
2. Tách bài toán thành tối đa 6 phép toán nhị phân thuộc `add`, `subtract`, `multiply`, `divide`. Tôn trọng dấu ngoặc và thứ tự nhân/chia trước cộng/trừ.
3. Gọi `demo__calculate` cho từng phép toán. Từ bước thứ hai, dùng chính xác trường `result` của observation trước làm đầu vào; không tự nhập lại kết quả đã làm tròn.
4. Nếu chia cho 0, tool báo lỗi hoặc kết quả không hữu hạn, dừng và giải thích bước không thể tiếp tục. Không đoán kết quả.
5. Kiểm tra kết quả bằng một phép toán đảo nếu có thể và vẫn nằm trong giới hạn 6 lần gọi tool. Nếu kiểm tra không khớp, báo không thể xác minh thay vì chọn một kết quả tùy ý.
6. Trả lời bằng biểu thức ban đầu, danh sách bước ngắn gọn và kết quả cuối kèm đơn vị. Chỉ trình bày phép tính và observation cần kiểm chứng; không xuất suy luận nội bộ.

## Output contract

- `Các bước`: mỗi dòng ghi phép toán đã gọi và kết quả tool trả về.
- `Kết quả`: một giá trị cuối cùng, kèm đơn vị nếu user cung cấp.
- `Kiểm tra`: ghi phép kiểm tra đảo hoặc lý do không thể kiểm tra.

## Điều kiện dừng

- Đã có kết quả được tool xác nhận và hoàn thành bước kiểm tra; hoặc
- Thiếu dữ liệu quan trọng, vượt 6 phép toán, chia cho 0 hay tool lỗi không phục hồi được.
