# Hướng dẫn Đảo Rồng Auto

DaoRong Tool chạy trên Windows và điều khiển Đảo Rồng trong giả lập Android. Tool dùng ảnh mẫu OpenCV, OCR tên và số sao, cùng model ONNX để hỗ trợ nhận diện một số mốc trong game.

## Cài đặt và cập nhật

- Bản đóng gói trên GitHub Releases: giải nén và mở `DaoRong_tool.exe`.
- Bản mã nguồn: cài Python 3.10 trở lên rồi chạy `install_and_run.bat`.
- Khi có bản mới, bấm **Cập nhật ngay** trong app. Trình cập nhật giữ cấu hình, profile và mẫu rồng riêng.

## Chọn rồng và chạy

1. Mở bảng Lai Rồng trong game. Trong **Điều Khiển**, bấm **+ Chụp** để lưu ảnh mẫu hai thẻ bố mẹ. Khoanh sát đặc trưng của từng thẻ và tránh lấy vùng nền động.
2. Chọn hai mẫu ở ô **Bố/Mẹ**. Mở **Luật Rồng (AI)** để đặt luật Cất, Bán hoặc Chúc phúc theo tên và số sao. Nút **Thử đọc rồng** cho xem kết quả nhận diện ở bảng kết quả lai.
3. Chọn **Chỉ Auto Lai** hoặc **Lai + Hóa Bụi**, và chọn cách xử lý **Rồng lạ**. Bấm **Kiểm tra** để rà các mẫu và điều kiện khởi động.
4. Bấm **Bắt đầu** hoặc `Ctrl+Enter`. Dùng phím đó để tạm dừng hoặc tiếp tục. Bấm **Chạy lại** để quay về Đảo Trứng hoặc `Esc` để dừng toàn bộ.

## Chạy nền qua ADB

Trong LDPlayer, mở kết nối gỡ lỗi ADB cục bộ. Trong app, chọn **Chạy Ngầm ADB** rồi bấm **Quét ADB**. Nếu app chưa tìm được `adb.exe`, chọn đường dẫn trong **Cài Đặt → Treo 24/7 & Thông Báo**. Bạn có thể chọn profile riêng cho từng thiết bị.

## Hóa bụi và bảo vệ rồng

Trong **Cài Đặt → Cơ Bản**, chọn các độ hiếm được phép hóa bụi và giới hạn số sao. Bật **Sổ Tay Rồng Vừa Lai** để lọc theo phiên, hoặc tắt để xét toàn bộ thẻ đủ điều kiện trong túi. Khóa các rồng cần giữ trong game và có thể bổ sung ảnh thẻ vào nhóm **Cấm Bụi**.

Nếu bot bỏ qua thẻ, xem dòng `[HÓA BỤI]` trong nhật ký để biết bước bị chặn. Nếu bot tạm dừng sau khi gửi Xác nhận, hãy kiểm tra kết quả trong game trước khi tiếp tục.
