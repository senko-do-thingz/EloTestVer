# 🏆 Elo Bot - Discord Rating System

**Author:** Senko | **Tác giả:** Senko | **作者:** Senko

---

## 🇻🇳 Tiếng Việt

Bot Discord chuyên dụng để quản lý hệ thống xếp hạng Elo cho các trận đấu game. Bot sử dụng công thức tính Elo của Autony và hỗ trợ nhiều chức năng quản lý người chơi, báo cáo trận đấu, và xem thống kê.

---

## 🇺🇸 English

A specialized Discord bot for managing Elo rating systems for gaming matches. The bot uses Autony's Elo calculation formula and supports various player management features, match reporting, and statistics viewing.

---

## 🇯🇵 日本語

ゲームマッチ用のEloレーティングシステムを管理するための専用Discordボット。ボットはAutonyのElo計算式を使用し、プレイヤー管理、マッチレポート、統計表示など様々な機能をサポートします。

### 🚀 Main Features | Tính năng chính | 主要機能

**🇺🇸 English:**
- ✅ **Player registration system** with custom names
- ✅ **Match reporting** with confirmation from both players
- ✅ **Automatic Elo calculation** using Autony's formula
- ✅ **Diverse leaderboards** (Elo, wins, losses, win rate, monthly games)
- ✅ **Seasonal leaderboards** with history storage
- ✅ **Detailed statistics** for each player
- ✅ **Admin system** with advanced management privileges
- ✅ **Multi-language support** (Vietnamese & English)

**🇻🇳 Tiếng Việt:**
- ✅ **Hệ thống đăng ký người chơi** với tên tùy chỉnh
- ✅ **Báo cáo trận đấu** với xác nhận từ cả hai người chơi
- ✅ **Tính toán Elo tự động** theo công thức Autony
- ✅ **Bảng xếp hạng đa dạng** (Elo, thắng, thua, tỷ lệ thắng, game tháng)
- ✅ **Bảng xếp hạng theo mùa** với lưu trữ lịch sử
- ✅ **Thống kê chi tiết** cho từng người chơi
- ✅ **Hệ thống admin** với quyền quản lý nâng cao
- ✅ **Hỗ trợ đa ngôn ngữ** (Tiếng Việt & English)

**🇯🇵 日本語:**
- ✅ **プレイヤー登録システム** カスタム名対応
- ✅ **マッチレポート** 両プレイヤーの確認付き
- ✅ **自動Elo計算** Autonyの式を使用
- ✅ **多様なリーダーボード** (Elo、勝利、敗北、勝率、月間ゲーム)
- ✅ **シーズンリーダーボード** 履歴保存機能
- ✅ **詳細統計** 各プレイヤー用
- ✅ **管理者システム** 高度な管理権限
- ✅ **多言語サポート** (ベトナム語 & 英語)

## 📋 System Requirements | Yêu cầu hệ thống | システム要件

**🇺🇸 English:**
- Python 3.8+
- Discord.py 2.5.0+
- SQLite3 (built-in)

**🇻🇳 Tiếng Việt:**
- Python 3.8+
- Discord.py 2.5.0+
- SQLite3 (tích hợp sẵn)

**🇯🇵 日本語:**
- Python 3.8+
- Discord.py 2.5.0+
- SQLite3 (内蔵)

## 🛠️ Installation | Cài đặt | インストール

### 1. Clone repository | Sao chép repository | リポジトリのクローン
```bash
git clone <repository-url>
cd elo-bot
```

### 2. Install dependencies | Cài đặt dependencies | 依存関係のインストール
```bash
pip install -r requirements.txt
```

### 3. Create .env file | Tạo file .env | .envファイルの作成
**🇺🇸 English:** Create `.env` file in root directory with content:
**🇻🇳 Tiếng Việt:** Tạo file `.env` trong thư mục gốc với nội dung:
**🇯🇵 日本語:** ルートディレクトリに`.env`ファイルを作成し、内容を記述:

```env
DISCORD_TOKEN=your_discord_bot_token_here
```

### 4. Run bot | Chạy bot | ボットの実行
```bash
python elobot.py
```

## 🎮 User Guide | Hướng dẫn sử dụng | ユーザーガイド

### Basic Commands | Lệnh cơ bản cho người dùng | 基本コマンド

#### Player Registration | Đăng ký người chơi | プレイヤー登録
```
-register <name>
```
**🇺🇸 Example:** `-register PlayerOne`
**🇻🇳 Ví dụ:** `-register PlayerOne`
**🇯🇵 例:** `-register PlayerOne`

#### Match Reporting | Báo cáo trận đấu | マッチレポート
**🇺🇸 English:** Bot supports 2 formats:
**🇻🇳 Tiếng Việt:** Bot hỗ trợ 2 định dạng:
**🇯🇵 日本語:** ボットは2つの形式をサポート:

**🇺🇸 Format 1:** `-report @winner score @loser`
**🇻🇳 Định dạng 1:** `-report @người_thắng điểm @người_thua`
**🇯🇵 形式1:** `-report @勝者 スコア @敗者`
```
-report @PlayerOne 31 @PlayerTwo
```

**🇺🇸 Format 2:** `-report @player1 score1 score2 @player2`
**🇻🇳 Định dạng 2:** `-report @người_chơi_1 điểm_1 điểm_2 @người_chơi_2`
**🇯🇵 形式2:** `-report @プレイヤー1 スコア1 スコア2 @プレイヤー2`
```
-report @PlayerOne 3 1 @PlayerTwo
```

#### Leaderboard | Bảng xếp hạng | リーダーボード
```
-leaderboard
-lb
```
**🇺🇸 English:** Use dropdown to select leaderboard type:
**🇻🇳 Tiếng Việt:** Sử dụng dropdown để chọn loại xếp hạng:
**🇯🇵 日本語:** ドロップダウンでリーダーボードタイプを選択:

- 🏆 Top Elo | Top Elo | トップElo
- 🥇 Top Wins | Top Wins | トップ勝利
- 💀 Top Losses | Top Losses | トップ敗北
- 📊 Top Win Rate | Top Win Rate | トップ勝率
- 🎮 Top Monthly Games Played | Top Monthly Games Played | 月間トップゲーム

#### Player Statistics | Thống kê cá nhân | プレイヤー統計
```
-stats                    # Your stats | Xem thống kê của bạn | あなたの統計
-stats @PlayerOne         # Others' stats | Xem thống kê người khác | 他人の統計
-stats_id <discord_id>    # Stats by Discord ID | Xem thống kê bằng Discord ID | Discord IDで統計
```

#### Seasonal Leaderboard | Bảng xếp hạng theo mùa | シーズンリーダーボード
```
-lbs
```
**🇺🇸 English:** Select season and month to view ranking history.
**🇻🇳 Tiếng Việt:** Chọn mùa và tháng để xem lịch sử xếp hạng.
**🇯🇵 日本語:** シーズンと月を選択してランキング履歴を表示。

#### Rename | Đổi tên | 名前変更
```
-rename <new_name>
```
**🇺🇸 English:** Note: 7-day cooldown, admins are not limited.
**🇻🇳 Tiếng Việt:** Lưu ý: Có cooldown 7 ngày, admin không bị giới hạn.
**🇯🇵 日本語:** 注意: 7日間のクールダウン、管理者は制限なし。

#### FAQ | Câu hỏi thường gặp | よくある質問
```
-qna
```

#### Help | Trợ giúp | ヘルプ
```
-help      # Basic commands | Lệnh cơ bản | 基本コマンド
-adhelp    # Admin commands (admin only) | Lệnh admin (chỉ admin) | 管理者コマンド（管理者のみ）
```

### Admin Commands | Lệnh dành cho Admin | 管理者コマンド

#### Registration Management | Quản lý đăng ký | 登録管理
```
-register_o <@player> <name>           # Register others | Đăng ký người khác | 他人を登録
-register_oid <discord_id> <name>      # Register by Discord ID | Đăng ký bằng Discord ID | Discord IDで登録
-rename_o <@player> <new_name>         # Rename others | Đổi tên người khác | 他人の名前変更
-rename_oid <discord_id> <new_name>    # Rename by Discord ID | Đổi tên bằng Discord ID | Discord IDで名前変更
-d_registration <@player>              # Delete registration | Xóa đăng ký | 登録削除
-did_registration <discord_id>         # Delete registration by ID | Xóa đăng ký bằng ID | IDで登録削除
-c_registration                        # Clear all registrations | Xóa tất cả đăng ký | 全登録削除
```

#### Statistics Management | Quản lý thống kê | 統計管理
```
-decay                                 # Auto increase/decrease Elo for all | Tự động tăng/giảm Elo cho tất cả | 全員のElo自動調整
-reset_e                               # Reset all Elo to 500 | Reset Elo tất cả về 500 | 全Eloを500にリセット
-set_e <@player> <elo>                 # Set player Elo | Set Elo người chơi | プレイヤーElo設定
-set_eid <discord_id> <elo>            # Set Elo by Discord ID | Set Elo bằng Discord ID | Discord IDでElo設定
-set_w <@player> <wins>                # Set win count | Set số trận thắng | 勝利数設定
-set_l <@player> <losses>              # Set loss count | Set số trận thua | 敗北数設定
-set_gid <discord_id> <games>          # Set monthly games | Set số game tháng | 月間ゲーム数設定
-set_stats <discord_id> <elo> <wins> <losses> <games>  # Set all stats | Set tất cả stats | 全統計設定
-ad_rp <winner_id> <winner_score> <loser_score> <loser_id>  # Report match | Báo cáo trận đấu | マッチレポート
```

#### Database Management | Quản lý database | データベース管理
```
-get_lb                                # Get leaderboard from Database | Lấy bảng xếp hạng từ Database | データベースからリーダーボード取得
-sum_lb <season>                       # Calculate total leaderboard for season | Tính tổng bảng xếp hạng mùa | シーズン合計リーダーボード計算
-db_d <server_id> <id>                 # Delete player data | Xóa dữ liệu người chơi | プレイヤーデータ削除
```

## 📊 Elo Calculation Formula | Công thức tính Elo | Elo計算式

**🇺🇸 English:** Bot uses Autony's formula with parameters:
**🇻🇳 Tiếng Việt:** Bot sử dụng công thức Autony với các tham số:
**🇯🇵 日本語:** ボットは以下のパラメータでAutonyの式を使用:

- **K-factor:** 20
- **Expected win rate:** `1 / (1 + 10^(elo_diff/1147.44429731658))`
- **Sweep factor:** `1 - (0.076923 * (winner_score - loser_score - 1))`
- **Base gain:** 5

### Calculation Example | Ví dụ tính toán | 計算例:
**🇺🇸 English:**
- Player A (Elo: 1200) vs Player B (Elo: 1000)
- Result: A wins 3-1
- Elo change: A +15, B -15 (approximately)

**🇻🇳 Tiếng Việt:**
- Player A (Elo: 1200) vs Player B (Elo: 1000)
- Kết quả: A thắng 3-1
- Elo thay đổi: A +15, B -15 (xấp xỉ)

**🇯🇵 日本語:**
- プレイヤーA (Elo: 1200) vs プレイヤーB (Elo: 1000)
- 結果: Aが3-1で勝利
- Elo変更: A +15, B -15 (概算)

## 🗃️ Database Structure | Cấu trúc database | データベース構造

**🇺🇸 English:** Bot uses SQLite with tables:
**🇻🇳 Tiếng Việt:** Bot sử dụng SQLite với các bảng:
**🇯🇵 日本語:** ボットは以下のテーブルでSQLiteを使用:

- `players`: Player information storage | Lưu thông tin người chơi | プレイヤー情報保存
  - `id`: Discord user ID | Discord user ID | DiscordユーザーID
  - `name`: Player name | Tên người chơi | プレイヤー名
  - `elo`: Current Elo rating | Điểm Elo hiện tại | 現在のEloレーティング
  - `games_won`: Total games won | Tổng trận thắng | 総勝利数
  - `games_lost`: Total games lost | Tổng trận thua | 総敗北数
  - `games_played_this_month`: Monthly games | Game trong tháng | 月間ゲーム数
  - `last_rename`: Last rename timestamp | Thời gian đổi tên cuối | 最後の名前変更時刻

## 📁 Directory Structure | Cấu trúc thư mục | ディレクトリ構造

```
elo-bot/
├── elobot.py              # Main file | File chính | メインファイル
├── config.py              # Configuration | Cấu hình | 設定
├── db.py                  # Database connection | Database connection | データベース接続
├── utils.py               # Utility functions | Utility functions | ユーティリティ関数
├── requirements.txt       # Dependencies | Dependencies | 依存関係
├── .env                   # Environment variables | Environment variables | 環境変数
├── databases/             # SQLite databases | SQLite databases | SQLiteデータベース
├── lb/                    # Leaderboard history | Leaderboard history | リーダーボード履歴
│   ├── Season 1/
│   └── Season 2/
└── cogs/                  # Bot modules | Bot modules | ボットモジュール
    ├── admin.py           # Admin commands | Admin commands | 管理者コマンド
    ├── help.py            # Help system | Help system | ヘルプシステム
    ├── leaderboard.py     # Leaderboard system | Leaderboard system | リーダーボードシステム
    ├── registration.py    # Registration system | Registration system | 登録システム
    ├── reporting.py       # Match reporting | Match reporting | マッチレポート
    ├── seasonal_lb.py     # Seasonal leaderboards | Seasonal leaderboards | シーズンリーダーボード
    ├── settings.py        # Bot settings | Bot settings | ボット設定
    └── stats.py           # Player statistics | Player statistics | プレイヤー統計
```

## 🔧 Configuration | Cấu hình | 設定

### Emoji Settings | Emoji settings | 絵文字設定
```python
YES_EMOJI = "<:Yes:1390939480139104356>"
NO_EMOJI = "<:No:1390939497306263603>"
```

### Bot Prefix | Bot prefix | ボットプレフィックス
**🇺🇸 English:** Default: `-`
**🇻🇳 Tiếng Việt:** Mặc định: `-`
**🇯🇵 日本語:** デフォルト: `-`

### Admin Role | Admin role | 管理者ロール
**🇺🇸 English:** Required role: `Elo Bot Manager`
**🇻🇳 Tiếng Việt:** Role cần có: `Elo Bot Manager`
**🇯🇵 日本語:** 必要なロール: `Elo Bot Manager`

## 🎯 Real Usage Examples | Ví dụ sử dụng thực tế | 実際の使用例

### Scenario 1: First-time Registration and Gaming | Đăng ký và chơi game lần đầu | 初回登録とゲームプレイ
```
User: -register Senko
Bot: ✅ Player Registered - You have been registered with an Elo of 500! Your name: Senko

User: -register PlayerTwo  
Bot: ✅ Player Registered - You have been registered with an Elo of 500! Your name: PlayerTwo

User: -report @Senko 3 1 @PlayerTwo
Bot: 📋 Match Pending Confirmation
     Senko defeated PlayerTwo with a score of 3-1.
     New Elo Ratings Preview:
     🏆 Senko: 515 (500 + 15)
     💀 PlayerTwo: 485 (500 - 15)
     
     Both players, please react with ✅ to confirm or ❌ to cancel within 5 minutes.
```

### Scenario 2: Viewing Statistics and Leaderboard | Xem thống kê và bảng xếp hạng | 統計とリーダーボードの表示
```
User: -stats
Bot: 📊 Stats for Senko
     🔥 Elo: 515
     🏆 Games Won: 3
     💀 Games Lost: 1  
     🎮 Monthly Games Played: 4
     📊 Win Rate: 75.00%
     🏅 Ranking by Elo: #1

User: -leaderboard
Bot: 🏆 Leaderboard - Top Elo (Page 1/1)
     1. Senko             515 <- You
     2. PlayerTwo         485
```

### Scenario 3: Admin Management | Quản lý admin | 管理者管理
```
Admin: -register_o @NewPlayer CoolName
Bot: ✅ Player Registered - Player CoolName has been registered with an Elo of 500!

Admin: -set_e @CoolName 1000
Bot: ✅ Elo updated - CoolName's Elo has been set to 1000.
```

## 🚨 Important Notes | Lưu ý quan trọng | 重要な注意事項

**🇺🇸 English:**
1. **Bot auto-shutdown** after 10-15 minutes to prevent overload
2. **Commands have timeout** of 5 minutes for confirmation
3. **Rename has cooldown** of 7 days (except admins)
4. **Names only contain** letters, numbers, and underscores (3-16 characters)
5. **Winning score must be** 3 or 4
6. **Bot only works** in allowed channels

**🇻🇳 Tiếng Việt:**
1. **Bot tự động tắt** sau 10-15 phút để tránh quá tải
2. **Commands có timeout** 5 phút để xác nhận
3. **Đổi tên có cooldown** 7 ngày (trừ admin)
4. **Tên chỉ chứa** chữ cái, số và dấu gạch dưới (3-16 ký tự)
5. **Điểm thắng phải** là 3 hoặc 4
6. **Bot chỉ hoạt động** ở các kênh được phép

**🇯🇵 日本語:**
1. **ボット自動シャットダウン** 過負荷防止のため10-15分後に
2. **コマンドタイムアウト** 確認用に5分間
3. **名前変更クールダウン** 7日間（管理者除く）
4. **名前は文字、数字、アンダースコアのみ** (3-16文字)
5. **勝利スコアは3または4** である必要があります
6. **ボットは許可されたチャンネルのみ** で動作

## 🐛 Common Error Handling | Xử lý lỗi thường gặp | よくあるエラー対処法

### "This interaction failed" | "This interaction failed" | "This interaction failed"
**🇺🇸 English:**
- Bot has auto-shutdown due to overload
- Run command again after a few minutes

**🇻🇳 Tiếng Việt:**
- Bot đã tự tắt do quá tải
- Chạy lại lệnh sau vài phút

**🇯🇵 日本語:**
- ボットが過負荷で自動シャットダウン
- 数分後にコマンドを再実行

### "Permission Denied" | "Permission Denied" | "Permission Denied"
**🇺🇸 English:**
- Missing "Elo Bot Manager" role
- Contact admin for permissions

**🇻🇳 Tiếng Việt:**
- Không có role "Elo Bot Manager"
- Liên hệ admin để được cấp quyền

**🇯🇵 日本語:**
- "Elo Bot Manager"ロールが不足
- 管理者に連絡して権限を取得

### "Player Not Registered" | "Player Not Registered" | "Player Not Registered"
**🇺🇸 English:**
- Player not registered yet
- Use `-register` before playing

**🇻🇳 Tiếng Việt:**
- Người chơi chưa đăng ký
- Sử dụng `-register` trước khi chơi

**🇯🇵 日本語:**
- プレイヤーがまだ登録されていません
- プレイ前に`-register`を使用

### "Invalid score format" | "Invalid score format" | "Invalid score format"
**🇺🇸 English:**
- Incorrect score format
- Use: `31`, `4-2`, `3:1`, or `4 2`

**🇻🇳 Tiếng Việt:**
- Định dạng điểm không đúng
- Sử dụng: `31`, `4-2`, `3:1`, hoặc `4 2`

**🇯🇵 日本語:**
- スコア形式が間違っています
- 使用: `31`, `4-2`, `3:1`, または `4 2`

## 📞 Support | Hỗ trợ | サポート

**🇺🇸 English:**
- Use `-qna` to view frequently asked questions
- Use `-help` to see command list
- Contact admin if you encounter issues

**🇻🇳 Tiếng Việt:**
- Sử dụng `-qna` để xem câu hỏi thường gặp
- Sử dụng `-help` để xem danh sách lệnh
- Liên hệ admin nếu gặp vấn đề

**🇯🇵 日本語:**
- よくある質問を表示するには`-qna`を使用
- コマンドリストを表示するには`-help`を使用
- 問題が発生した場合は管理者に連絡

## 📝 Changelog | Changelog | 変更履歴

### Version 1.0 | Version 1.0 | バージョン1.0
**🇺🇸 English:**
- ✅ Basic registration system
- ✅ Match reporting with confirmation
- ✅ Elo calculation using Autony's formula
- ✅ Diverse leaderboards
- ✅ Player statistics
- ✅ Admin system
- ✅ Seasonal history storage

**🇻🇳 Tiếng Việt:**
- ✅ Hệ thống đăng ký cơ bản
- ✅ Báo cáo trận đấu với xác nhận
- ✅ Tính toán Elo theo công thức Autony
- ✅ Bảng xếp hạng đa dạng
- ✅ Thống kê người chơi
- ✅ Hệ thống admin
- ✅ Lưu trữ lịch sử theo mùa

**🇯🇵 日本語:**
- ✅ 基本登録システム
- ✅ 確認付きマッチレポート
- ✅ Autony式によるElo計算
- ✅ 多様なリーダーボード
- ✅ プレイヤー統計
- ✅ 管理者システム
- ✅ シーズン履歴保存

---

**Developed by Senko** 🦊 | **Phát triển bởi Senko** 🦊 | **Senkoによって開発** 🦊
