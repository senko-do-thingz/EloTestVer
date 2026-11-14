# EloBot - Bot xếp hạng Elo cho Discord

**Tác giả:** Senko

**Ngôn ngữ tài liệu:** [English](../README.md) | [Tiếng Việt](README.vi.md) | [日本語](README.ja.md)

## Giới thiệu

EloBot là bot Discord viết bằng Python để đăng ký người chơi, báo cáo kết quả trận đấu, tính điểm Elo và xem bảng xếp hạng, thống kê, lịch sử. Dữ liệu được lưu trong cơ sở dữ liệu SQLite riêng cho từng server Discord. Tiền tố lệnh mặc định là `-`.

Tài liệu có ba ngôn ngữ: tiếng Anh, tiếng Việt và tiếng Nhật. Hiện tại, phần lớn thông báo bên trong bot vẫn bằng tiếng Anh. Chọn ngôn ngữ tài liệu không tự động thay đổi ngôn ngữ giao diện Discord. Cơ sở dữ liệu có trường `language` nhưng hệ thống dịch toàn bộ giao diện bot sang ba ngôn ngữ chưa được triển khai.

## Tính năng chính

- Đăng ký, đổi tên và quản lý người chơi
- Báo cáo trận đấu có xác nhận, cập nhật Elo và lưu lịch sử
- Bảng xếp hạng theo Elo, số trận thắng, thua, tỷ lệ thắng, chuỗi trận và hoạt động trong tháng
- Thống kê chi tiết, so sánh người chơi, ảnh lịch sử và xuất dữ liệu Excel
- Bảng xếp hạng theo mùa, lưu trữ từng tháng và tổng kết cả mùa
- Decay hàng tháng tự động, sao lưu database, chuyển mùa và xóa đăng ký khi kết thúc mùa
- Quản lý League và role trên Discord
- Counting, hỗ trợ tin nhắn riêng và công cụ dành cho quản trị viên

## Yêu cầu hệ thống

- Python 3.11 hoặc 3.12, là hai phiên bản đang được kiểm thử trong CI
- Các thư viện trong `requirements.txt`, bao gồm discord.py 2.7.1
- Discord bot token cùng quyền cấu hình bot trên server
- SQLite có sẵn trong thư viện chuẩn Python

## Cài đặt

1. Clone repository:

   ~~~bash
   git clone https://github.com/senko-do-thingz/EloTestVer.git
   cd EloTestVer
   ~~~

2. Tạo môi trường ảo Python:

   ~~~bash
   python -m venv .venv
   ~~~

   Windows: `.venv\Scripts\activate`

   macOS hoặc Linux: `source .venv/bin/activate`

3. Cài đặt các thư viện:

   ~~~bash
   python -m pip install -r requirements.txt
   ~~~

4. Tạo file `.env` tại thư mục gốc:

   ~~~dotenv
   DISCORD_TOKEN=replace_with_your_bot_token
   ~~~

   Không commit token hoặc file `.env` lên GitHub. File `config.py` sẽ đọc token từ biến môi trường. Có thể đặt `IS_PRODUCTION=true` để sử dụng cấu hình reaction cho môi trường production và tùy chỉnh `YES_EMOJI`, `NO_EMOJI` nếu cần.

5. Trong Discord Developer Portal, bật Message Content intent để bot nhận lệnh có tiền tố. Cấp quyền gửi tin nhắn, embed, reaction và quyền quản lý role nếu sử dụng chức năng League.
6. Khởi chạy bot:

   ~~~bash
   python elobot.py
   ~~~

Các bài test tự động không cần bot token và không kết nối tới Discord thật.

## Lệnh dành cho người dùng

Bảng dưới đây liệt kê các lệnh phổ biến. Một số lệnh chỉ dùng với tiền tố `-`, số khác hỗ trợ slash command. Sử dụng `-help`, `-adhelp` hoặc `/help` để xem cú pháp và quyền hạn đầy đủ.

| Chức năng | Lệnh | Ghi chú |
| --- | --- | --- |
| Đăng ký | `-register <name>` | Elo khởi tạo là 500 |
| Đổi tên | `-rename <new_name>` | Cooldown thông thường là 7 ngày |
| Báo cáo trận đấu | `-report @player1 3 1 @player2` | Có bí danh `-rp` |
| Báo cáo tỷ số rút gọn | `-report @winner 31 @loser` | Hỗ trợ cả `3-1` và `3:1` |
| Bảng xếp hạng hiện tại | `-leaderboard` hoặc `-lb` | Chọn loại bảng xếp hạng và chuyển trang |
| Bảng xếp hạng đã lưu | `-lbs` | Chọn mùa và tháng |
| Thống kê người chơi | `-stats` | Xem help để tra cứu người chơi khác |
| Lịch sử trận đấu | `-history` | Bản slash có thêm tùy chọn |
| So sánh người chơi | `-compare` hoặc `-cp` | So sánh các người chơi đã đăng ký |
| League | `-leagues` | Xem thông tin League |
| Trợ giúp | `-help` hoặc `/help` | Danh sách và hướng dẫn lệnh |

## Lệnh quản trị

Các lệnh quản trị thường yêu cầu role `Elo Bot Manager`, trừ khi hướng dẫn của từng lệnh có quy định khác.

| Chức năng | Lệnh | Ghi chú |
| --- | --- | --- |
| Đăng ký hộ | `-register_o`, `-register_oid` | Theo mention hoặc Discord ID |
| Đổi tên hộ | `-rename_o`, `-rename_oid` | Theo mention hoặc Discord ID |
| Báo cáo trận theo ID | `-ad_rp` | Quản trị viên thực hiện |
| Điều chỉnh Elo và thống kê | `-set_e`, `-set_w`, `-set_l`, `-set_stats` | Xem `-adhelp` để biết tham số |
| Reset Elo | `-reset_e` | Cần xác nhận |
| Xóa toàn bộ đăng ký | `-c_reg` | Thao tác phá hủy dữ liệu, cần xác nhận |
| Xuất bảng xếp hạng | `-get_lb` | Tạo các file archive |
| Tổng hợp bảng xếp hạng mùa | `-sum_lb` | Tổng kết dữ liệu đã lưu |
| Thiết lập số tháng trong mùa | `/months` | Giới hạn số tháng cho mỗi mùa |
| Chọn mùa và tháng hiện tại | `/season` | Cập nhật vị trí mùa |
| Kiểm tra hoặc chạy Decay | `-decay` | Chỉnh sửa thủ công cần dev mode và xác nhận |
| Cấu hình kênh log Decay | `-decay_setup` | Đặt hoặc xóa kênh log |

Ngoài ra còn có các lệnh về quản lý League, support, moderation và các lệnh riêng của server quản trị. Những lệnh nội bộ không dành cho người dùng thông thường.

## Quy tắc đặt tên

Tên người chơi dài từ 2 đến 20 ký tự, cho phép chữ cái Latin, chữ số, khoảng trắng, dấu gạch dưới, dấu chấm và các ký tự Latin có dấu, bao gồm tiếng Việt. Tên chỉ có khoảng trắng, tên chứa `elobot` (không phân biệt chữ hoa hay chữ thường) hoặc chứa ký tự không hỗ trợ sẽ bị từ chối. Không nên giả định rằng tên bằng chữ Nhật có thể sử dụng được.

## Elo, báo cáo trận và Decay

- Người chơi mới bắt đầu với 500 Elo. Công thức tính trong `utils/elo.py` có điều chỉnh theo tỷ số và các hệ số thưởng.
- Báo cáo trận đấu có thể yêu cầu người chơi xác nhận bằng reaction trước khi dữ liệu được ghi.
- `/months` đặt số tháng tối đa trong một mùa. `/season` đặt mùa và tháng hiện tại. `-decay` chỉ cho phép cập nhật dữ liệu thủ công khi dev mode được bật và thao tác đã được xác nhận.
- Job Decay tự động chạy kiểm tra vào ngày đầu tiên của mỗi tháng lúc 00:10 UTC. Ở tháng thông thường, bot áp dụng Decay và đặt lại thống kê trong tháng. Vào cuối mùa, bot lưu archive, gọi cùng logic xóa đăng ký như `-c_reg` rồi chuyển sang mùa tiếp theo.
- `-c_reg` xóa người chơi đã đăng ký và dữ liệu trận đấu liên quan, đồng thời cố gắng gỡ các League role. Dữ liệu daily streak vẫn được giữ lại theo thiết kế. Hãy sao lưu database trước khi thực hiện.
- Các bảng xếp hạng cũ được lưu trong `lb/<guild_id>/Season <n>/Month <m>/` và thư mục `Overall/` khi có dữ liệu tổng kết.

**Lỗi đã biết:** `-lbs` có thể phân tích sai tên người chơi chứa dấu cách trong file archive. Hiện đã có test ghi nhận nhưng logic parser chưa được sửa.

## Cấu trúc repository

~~~text
EloTestVer/
  elobot.py                 Khởi chạy bot và nạp các cog
  config.py                 Biến môi trường và cấu hình reaction
  requirements.txt          Các thư viện cần thiết
  cogs/                     Lệnh người dùng và quản trị
    admin_elo/              Decay, dọn đăng ký và cập nhật thống kê
    roles/                  Quản lý League role
    seasonal_lb/            Giao diện bảng xếp hạng theo mùa
    private/                Lệnh quản trị hạn chế
  db/                       Schema và xử lý SQLite
  utils/                    Elo, rendering, archive, cache và tiện ích
  tests/                    Bộ kiểm thử tự động
  .github/workflows/        Cấu hình GitHub Actions
  assets/                   Font dùng để vẽ ảnh
  databases/                SQLite khi chạy bot (không commit)
  lb/                       File archive khi chạy bot (không commit)
~~~

## Chạy test và CI

~~~bash
python -m pip install -r requirements.txt "pytest>=8,<9"
python -m compileall -q cogs db utils tests elobot.py
python -m pytest tests -q
~~~

GitHub Actions kiểm thử trên Python 3.11 và 3.12 khi có push hoặc pull request vào `main`. Bộ test sử dụng SQLite tạm và các đối tượng Discord giả lập, không cần token thật. Vẫn cần kiểm thử trên Discord thật để xác nhận quyền, reaction, thứ tự role và khả năng gửi tin nhắn.

Các trường hợp `xfail` của `-lbs` là lỗi đã biết, không phải bằng chứng rằng chức năng hoạt động đúng.

## Xử lý sự cố

- Thiếu `DISCORD_TOKEN`: tạo file `.env` hoặc khai báo biến môi trường.
- Lệnh có tiền tố không hoạt động: kiểm tra Message Content intent, quyền của bot và log khi nạp extension.
- Thông báo `Permission Denied`: kiểm tra role `Elo Bot Manager` và giới hạn theo server.
- Không tìm thấy archive: kiểm tra quá trình xuất dữ liệu và thư mục `lb`.
- Lỗi reaction hoặc role: kiểm tra Discord permissions, thứ tự role và giới hạn API.
- Test bị fail: mở log GitHub Actions để tìm nguyên nhân, không bỏ qua assertion.

**Phát triển bởi Senko.**
