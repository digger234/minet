---
trigger: always_on
---

# QUY TẮC BẮT BUỘC

1. **KHÔNG thêm comments**
2. **LUÔN dùng tiếng Việt trong giao tiếp với user** - Đơn giản, dễ hiểu, không phức tạp
3. **CẤM tạo hàm hỗn hợp** - Code chức năng A phải vào hàm A, CẤM lẫn lộn
4. **Hạn chế newlines** - Theo bố cục hiện tại, CẤM dùng parameter/pattern/v index/elif phân chia
5. **CẤM đặt tên hàm và biến quá dài** - Cấm có dấu gạch "\_" ở giữa tên biến hay hàm, Cấm có prefix trong tên, Chỉ được đặt từ đơn, Cấm đặt 2 từ có nghĩa ghép dính liền lại để lách luật, Cấm có chữ "visit" trong tên, Cấm viết hoa lung tung ( chỉ được phép đặt tên hoa nếu python yêu cầu bắt buộc ).
6. **Sửa lỗi ngay** - CẤM rollback khi chưa thử sửa, CẤM để lần sau
7. **Tận dụng hết mọi hàm** - Không để unused functions
8. **Suy nghĩ CỰC KÌ cẩn thận** - Cấm phạm sai lầm khi chỉnh sửa
9. **Xóa file temp** - Khi chỉnh sửa xong phải xóa file temp
10. **KHÔNG tự ý hành động** - Mọi thứ phải qua ý user
11. **KHÔNG chạy python** - Dùng path C:/Users/XZ/AppData/Local/Python/pythoncore-3.12-64/python.exe thay vì python vì environment của user đang bị trục trặc
12. **Cách xử lý khi có unused/duplicate functions** - Nếu có unused thì hãy tận dụng triệt để tất cả hàm unused đó, chỉ được xóa khi hàm đó không thể tận dụng vào đâu khác. Nếu có hàm duplicate thì hãy gộp những hàm duplicate lại, "gộp" không phải "chọn" cái mạnh nhất hay "thay thế". Lưu ý : Dù làm gì thì mục đích tối thượng vẫn là biến tool trở nên mạnh hơn chứ không phải yếu đi.
    **LƯU Ý:** User không biết code - giải thích đơn giản!\*\*
