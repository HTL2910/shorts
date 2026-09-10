# Bộ dữ liệu mẫu: Giới thiệu Áo Sơ Mi Học Sinh

Thư mục này chứa đầy đủ tư liệu thực tế để bạn test:

## 1. Các file có trong thư mục:
- `ao_hoc_sinh_product.jpg`: Ảnh chụp sản phẩm áo sơ mi trắng học sinh phẳng phiu, ánh sáng tự nhiên.
- `nu_sinh_model.jpg`: Ảnh chân dung nữ sinh trung học Việt Nam duyên dáng mặc đồng phục sân trường.
- `input_video_generator.json`: File kịch bản JSON hoàn chỉnh (kịch bản tiếng Việt, voiceover, phụ đề SRT).

---

## 2. Cách test trên Flowboard Canvas (Khuyên dùng - Sử dụng Google Flow)
1. Mở trình duyệt vào **http://localhost:5173**.
2. **Tạo Visual Asset node**:
   - Bấm `+ Visual asset` trên thanh công cụ.
   - Bấm upload và chọn file: `sample_ao_hoc_sinh/ao_hoc_sinh_product.jpg`.
3. **Tạo Character node**:
   - Bấm `+ Character` trên thanh công cụ.
   - Bấm upload và chọn file: `sample_ao_hoc_sinh/nu_sinh_model.jpg`.
4. **Tạo Image node**:
   - Bấm `+ Image`. Nối 2 dây từ `Visual asset` và `Character` vào `Image`.
   - Bấm nút **Auto-Prompt** (Gemini sẽ tự viết prompt ghép mẫu với áo sơ mi).
   - Bấm **▶ Generate** để Google Flow sinh 4 biến thể ảnh studio. Click chọn ảnh đẹp nhất.
5. **Tạo Video node (Veo 3.1)**:
   - Bấm `+ Video`. Nối dây từ `Image` sang `Video`.
   - Bấm **Auto-Prompt** để tạo chuyển động (quay nhẹ người, nhìn camera cười, gió thổi nhẹ).
   - Bấm **▶ Generate** để Veo 3.1 tạo video clip 8 giây.

---

## 3. Cách test bằng dòng lệnh CLI (Nếu muốn render video hoàn chỉnh có tiếng + phụ đề):
Chạy lệnh sau tại thư mục gốc:
```bash
python video_generator.py sample_ao_hoc_sinh/input_video_generator.json video_ao_hoc_sinh
```
Video hoàn chỉnh kèm tiếng thuyết minh và phụ đề sẽ xuất hiện trong thư mục `output/`.
