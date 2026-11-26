# Hướng Dẫn Sử Dụng — Đảo Rồng Auto (Lai Rồng & Hóa Bụi cho LDPlayer)

Tool được thiết kế riêng cho **Windows + LDPlayer**, sử dụng **so khớp hình ảnh màu (OpenCV)** tốc độ cao (không dùng OCR nên không bị đọc sai chữ), tự động chống lệch DPI trên laptop và vuốt mượt chống trôi quán tính.

## 1. Cài đặt và khởi chạy nhanh
1. Cài **Python 3.10 trở lên** tại [python.org](https://www.python.org/downloads/) (nhớ tích chọn **Add Python to PATH**).
2. Giải nén toàn bộ thư mục `DaoRongAuto` ra máy (ví dụ: `D:\DaoRongAuto`).
3. Nhấp chuột phải vào `install_and_run.bat` -> chọn **Run as administrator** (khuyên chạy quyền Admin để phím tắt `F8` / `F9` và click chuột hoạt động mượt bên trong LDPlayer).
4. Lần đầu chạy, script sẽ tự tạo môi trường ảo `.venv`, cài thư viện và mở giao diện điều khiển.

## 2. Thiết lập ảnh mẫu lần đầu (chỉ cần làm 1 lần)
Trước khi bấm chạy, mở game **Đảo Rồng** trên **LDPlayer** và giữ cố định kích thước cửa sổ LDPlayer. Sau đó vào **Tab 2: Chụp Ảnh Mẫu (Setup)**:

### A. Ảnh mẫu nút giao diện (`templates/ui/`)
Bấm nút **Chụp** cạnh từng mục và kéo chuột khoanh gọn vào đúng nút/chữ trong game:
- `btn_egg.png`: Quả trứng ở giữa đảo lai.
- `btn_keep.png`, `btn_bless.png`, `btn_sell.png`: 3 nút **Cất rồng**, **Chúc phúc**, **Bán rồng** sau khi lai xong.
- `btn_sell_confirm.png`: Nút xác nhận bán trong giao diện bán.
- `msg_bag_full.png`: Khoanh gọn dòng chữ thông báo **"Túi rồng đã đầy"** (chỉ lấy phần chữ ổn định, không lấy nền động).
- `btn_close_x.png`: Dấu **X** tắt giao diện.
- `btn_arrow_right.png` và `btn_arrow_left.png`: Mũi tên chuyển đảo `>` và `<`.
- `btn_dust_bottle.png`: Cái bình bên cạnh trong giao diện tẩy tủy.
- `dragon_nguyen_lieu.png`: Khoanh gọn phần tên **"nguyên liệu"** trên danh sách rồng bên phải ở giao diện hóa bụi.
- `btn_dust.png` và `btn_dust_confirm.png`: Nút **Hóa bụi** và nút **Xác nhận** hóa bụi.

### B. Ảnh mẫu phân loại rồng
- **Rồng Bố/Mẹ (`templates/parents/`)**: Mở giao diện lai, khoanh đúng hình thẻ của từng rồng bạn muốn đem lai trên thanh rồng phía dưới.
- **Rồng muốn Cất (`templates/keep/`)**, **Rồng muốn Bán (`templates/sell/`)**, **Rồng muốn Chúc phúc (`templates/bless/`)**: Sau khi lai ra rồng kết quả, khoanh gọn phần hình rồng hoặc bảng tên đặc trưng của từng con vào đúng nhóm mong muốn.

> **Mẹo chụp ảnh mẫu đạt độ chính xác ~99%:**
> - Khoanh sát phần đặc trưng tĩnh (hình nút, chữ, đầu/thân rồng), **tránh khoanh dính nền trời/mây động** hoặc hiệu ứng lấp lánh xung quanh.
> - Giữ nguyên độ phân giải và kích thước cửa sổ LDPlayer giữa lúc chụp mẫu và lúc chạy tool.

## 3. Chạy vòng lặp Lai Rồng - Hóa Bụi
1. Quay lại **Tab 1: Auto Lai Rồng - Hóa Bụi**.
2. Chọn cố định **Rồng bố/mẹ 1** và **Rồng bố/mẹ 2** ở 2 ô chọn phía trên.
3. Chọn cách xử lý **Khi gặp rồng lạ**:
   - `keep` (khuyên dùng): Tự động cất an toàn, tránh bán nhầm rồng hiếm.
   - `pause`: Tự động tạm dừng để bạn xem và chụp bổ sung mẫu.
   - `sell`: Tự động bán.
4. Nhấn **▶ Bắt đầu / Tạm dừng (F8)** (hoặc bấm phím **F8**).
5. Bấm **F9** (hoặc rê nhanh chuột lên góc trên cùng bên trái màn hình) để dừng khẩn cấp bất cứ lúc nào.

## 4. Chế độ Auto Click Nhiều Nút (Tab 3)
Nếu bạn muốn treo click lặp lại một chuỗi nút bất kỳ (nhận quà, mở rương, cường hóa…), mở **Tab 3: Auto Click Nhiều Nút**, bấm **+ Thêm nút (Lấy tọa độ)** hoặc **+ Thêm nút (Chụp ảnh mẫu)**, đặt độ trễ rồi bấm **▶ Chạy Auto Click**.

## 5. Xử lý nhanh nếu gặp sự cố
- **Tool không click vào trong LDPlayer**: Chạy `install_and_run.bat` bằng **Run as administrator**.
- **Nhận diện bị bỏ sót (score trong Log khoảng 0.78–0.82)**: Vào **Tab 4: Cài Đặt Nâng Cao**, hạ nhẹ ngưỡng khớp xuống `0.78` rồi bấm **Lưu cài đặt**.
- **Kéo thanh rồng hoặc danh sách hóa bụi bị trôi quá nhanh/chậm**: Mở `config.json`, chỉnh `drag_duration` (tăng lên `0.6` để kéo chậm hơn) hoặc chỉnh tọa độ tương đối `bottom_bar_roi` / `dust_list_roi`.
