# EloBot - Bot xep hang Elo cho Discord

**Tac gia:** Senko

**Ngon ngu tai lieu:** [English](../README.md) | [Tiếng Việt](README.vi.md) | [日本語](README.ja.md)

## Gioi thieu

EloBot la bot Discord viet bang Python, dung de dang ky nguoi choi, bao cao ket qua tran dau, tinh diem Elo, xem bang xep hang va lich su. Du lieu duoc luu trong SQLite rieng theo tung server Discord. Tien to lenh mac dinh la `-`.

Tai lieu co ba ngon ngu: tieng Anh, tieng Viet va tieng Nhat. Hien tai phan lon thong bao cua bot van bang tieng Anh. Chon ngon ngu tai lieu khong tu dong doi ngon ngu giao dien Discord. Database co truong language, nhung he thong dich giao dien bot day du sang ca ba ngon ngu chua duoc trien khai.

## Tinh nang chinh

- Dang ky, doi ten va quan ly nguoi choi
- Bao cao tran dau co xac nhan va cap nhat diem Elo
- Bang xep hang Elo, thang, thua, ty le thang, chuoi tran va so tran trong thang
- Thong ke, so sanh nguoi choi, lich su va xuat file Excel
- Bang xep hang theo mua, kho luu tru thang va tong ket mua
- Decay hang thang tu dong, backup, chuyen mua va xoa dang ky cuoi mua
- Quan ly league va role Discord
- Counting, ho tro tin nhan rieng va cong cu quan tri

## Yeu cau

- Python 3.11 hoac 3.12, hai phien ban dang duoc CI kiem thu
- Cac thu vien trong `requirements.txt`, gom discord.py 2.7.1
- Discord bot token va quyen cau hinh bot tren server
- SQLite co san trong thu vien chuan Python

## Cai dat

1. Clone repository:

   ~~~bash
   git clone https://github.com/senko-do-thingz/EloTestVer.git
   cd EloTestVer
   ~~~

2. Tao moi truong ao:

   ~~~bash
   python -m venv .venv
   ~~~

   Windows: `.venv\Scripts\activate`

   macOS / Linux: `source .venv/bin/activate`

3. Cai thu vien:

   ~~~bash
   python -m pip install -r requirements.txt
   ~~~

4. Tao file `.env` o thu muc goc:

   ~~~dotenv
   DISCORD_TOKEN=replace_with_your_bot_token
   ~~~

   Khong commit token hoac file `.env`. File `config.py` doc token tu bien moi truong. Co the dat `IS_PRODUCTION=true` de su dung cau hinh reaction production va tuy chinh `YES_EMOJI`, `NO_EMOJI` neu can.

5. Trong Discord Developer Portal, bat Message Content intent de bot nhan lenh prefix. Cap quyen gui tin nhan, embed, reaction va quan ly role neu can.
6. Chay bot:

   ~~~bash
   python elobot.py
   ~~~

Test tu dong khong yeu cau token va khong dang nhap Discord.

## Lenh thong dung

Danh sach duoi day gom cac lenh chinh. Xem `-help`, `-adhelp` hoac `/help` de biet cu phap va gioi han quyen cua tung lenh.

| Muc dich | Lenh | Ghi chu |
| --- | --- | --- |
| Dang ky | `-register <name>` | Elo khoi tao la 500 |
| Doi ten | `-rename <new_name>` | Cooldown 7 ngay |
| Bao cao tran | `-report @player1 3 1 @player2` | Co alias `-rp` |
| Bao cao rut gon | `-report @winner 31 @loser` | Ho tro `3-1` va `3:1` |
| Bang xep hang hien tai | `-leaderboard` hoac `-lb` | Chon loai bang xep hang |
| Bang xep hang luu tru | `-lbs` | Chon mua va thang |
| Thong ke | `-stats` | Xem huong dan khi can xem nguoi khac |
| Lich su | `-history` | Lenh slash co lua chon chi tiet hon |
| So sanh | `-compare` hoac `-cp` | So sanh nguoi choi |
| League | `-leagues` | Xem thong tin league |
| Tro giup | `-help` hoac `/help` | Huong dan cac lenh |

Lenh admin yeu cau role `Elo Bot Manager`, tru khi phan huong dan cua lenh ghi dieu kien rieng.

| Muc dich | Lenh |
| --- | --- |
| Dang ky ho | `-register_o`, `-register_oid` |
| Doi ten ho | `-rename_o`, `-rename_oid` |
| Bao cao tran theo ID | `-ad_rp` |
| Sua Elo va thong ke | `-set_e`, `-set_w`, `-set_l`, `-set_stats` |
| Reset Elo | `-reset_e` |
| Xoa toan bo dang ky | `-c_reg` |
| Xuat bang xep hang | `-get_lb` |
| Tong ket mua | `-sum_lb` |
| Dat so thang trong mua | `/months` |
| Dat mua va thang hien tai | `/season` |
| Xem hoac thuc hien decay | `-decay` |
| Dat kenh log decay | `-decay_setup` |

Cac lenh private nhu quan tri support, moderation va kiem tra server khac co rang buoc quyen rieng.

## Quy tac dat ten

Ten nguoi choi dai 2-20 ky tu. Cho phep chu Latin, chu so, khoang trang, dau gach duoi, dau cham va chu Latin co dau, bao gom tieng Viet. Cam ten chi co khoang trang, ten chua `elobot` khong phan biet hoa thuong, va ky tu khong ho tro. Khong mac dinh rang ten bang chu Nhat duoc chap nhan.

## Elo, bao cao tran va Decay

- Nguoi choi moi bat dau voi 500 Elo. Cong thuc nam trong `utils/elo.py`, co dieu chinh theo ty so va bonus.
- Bao cao tran co the yeu cau nguoi choi xac nhan bang reaction truoc khi ghi du lieu.
- `/months` dat so thang toi da cho mua. `/season` dat vi tri mua/thang. Lenh `-decay` thu cong chi thay doi du lieu khi dev mode cho phep.
- Job Decay tu dong kiem tra vao ngay dau tien moi thang luc 00:10 UTC. Thang thong thuong se decay va reset so tran trong thang. Cuoi mua se luu archive, chay cung logic xoa dang ky nhu `-c_reg` va chuyen sang mua tiep theo.
- `-c_reg` xoa nguoi choi va lich su tran lien quan, thu go bo league role. Du lieu daily streak duoc giu lai co chu dich. Nen backup truoc khi thao tac.
- Lich su bang xep hang nam trong `lb/<guild_id>/Season <n>/Month <m>/` va thu muc `Overall/`.

**Loi da biet:** `-lbs` co the doc sai ten nguoi choi co dau cach trong file archive. Hien da co test ghi nhan loi nay, nhung logic parser chua duoc sua.

## Cau truc repository

~~~text
EloTestVer/
  elobot.py                 Diem khoi chay bot va nap cogs
  config.py                 Bien moi truong va reaction
  requirements.txt          Danh sach thu vien
  cogs/                     Lenh nguoi dung va quan tri
    admin_elo/              Decay va cap nhat du lieu
    roles/                  Quan ly league role
    seasonal_lb/            Bang xep hang theo mua
    private/                Lenh quan tri gioi han
  db/                       SQLite va truy van
  utils/                    Elo, ve anh, cache va helpers
  tests/                    Test tu dong
  .github/workflows/        GitHub Actions
  assets/                   Font de ve anh
  databases/                Database khi chay (khong commit)
  lb/                       Archive khi chay (khong commit)
~~~

## Chay test va CI

~~~bash
python -m pip install -r requirements.txt "pytest>=8,<9"
python -m compileall -q cogs db utils tests elobot.py
python -m pytest tests -q
~~~

GitHub Actions chay tren Python 3.11 va 3.12 khi push hoac mo pull request vao `main`. Test su dung database tam va mock Discord, khong can token that. Van can kiem thu tren server Discord that cho quyen han, reaction, role va delivery. Cac truong hop `xfail` cua `-lbs` la loi da biet, khong phai chuc nang da hoat dong.

## Xu ly su co

- Thieu `DISCORD_TOKEN`: tao file `.env` hoac dat bien moi truong.
- Lenh prefix khong chay: kiem tra Message Content intent, quyen bot va log nap extension.
- Bao `Permission Denied`: kiem tra role `Elo Bot Manager` va gioi han server.
- Khong tim thay leaderboard archive: kiem tra export va thu muc `lb`.
- Loi reaction / role: kiem tra Discord permissions, thu tu role va rate limit.
- Test fail: xem log GitHub Actions va sua nguyen nhan, khong bo qua assertion.

**Phat trien boi Senko.**
