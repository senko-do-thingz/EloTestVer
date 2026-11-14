# EloBot - Discord Eloレーティングボット

**開発者:** Senko

**ドキュメント言語:** [English](../README.md) | [Tiếng Việt](README.vi.md) | [日本語](README.ja.md)

## 概要

EloBotは、プレイヤー登録、試合結果の報告、Elo計算、ランキング、試合履歴を管理するPython製Discordボットです。サーバーごとにSQLiteデータベースを保存します。標準のコマンドプレフィックスは`-`です。

ドキュメントは英語、ベトナム語、日本語の3言語で提供します。ボットのメッセージ自体は現在ほぼ英語です。ドキュメントの言語を選択してもDiscord上の表示言語は変更されません。データベースには言語フィールドがありますが、ボットUI全体の3言語翻訳は未実装です。

## 主な機能

- プレイヤー登録、名前変更、管理者による登録
- 試合報告、相互確認、Elo更新、試合履歴
- Elo、勝利、敗北、勝率、連勝、月間プレイ数のランキング
- 詳細統計、プレイヤー比較、履歴画像、Excel出力
- シーズン別ランキングと月別アーカイブ
- 毎月の自動Decay、バックアップ、シーズン更新、シーズン末登録削除
- Leagueロール管理、カウント機能、サポートメッセージ、管理者機能

## 動作環境

- Python 3.11または3.12 (CIで検証するバージョン)
- `requirements.txt` に記載の依存ライブラリ (discord.py 2.7.1など)
- Discordボットトークン
- Python標準ライブラリのSQLite

## インストール

1. リポジトリを取得します。

   ~~~bash
   git clone https://github.com/senko-do-thingz/EloTestVer.git
   cd EloTestVer
   ~~~

2. 仮想環境を作成します。

   ~~~bash
   python -m venv .venv
   ~~~

   Windows: `.venv\Scripts\activate`

   macOS / Linux: `source .venv/bin/activate`

3. 依存ライブラリをインストールします。

   ~~~bash
   python -m pip install -r requirements.txt
   ~~~

4. ルートに`.env`を作成します。

   ~~~dotenv
   DISCORD_TOKEN=replace_with_your_bot_token
   ~~~

   トークンや`.env`をコミットしないでください。`config.py`が環境変数を読み込みます。`IS_PRODUCTION=true`で本番用reaction設定を選択できます。`YES_EMOJI`と`NO_EMOJI`の環境変数も利用できます。

5. Discord Developer PortalでMessage Content intentを有効にし、メッセージ、embed、reaction、必要な場合はロール管理の権限を付与してください。
6. ボットを起動します。

   ~~~bash
   python elobot.py
   ~~~

自動テストはDiscordにログインせず、トークンも必要としません。

## 主なコマンド

一部の機能はスラッシュコマンドにも対応しています。完全な構文は`-help`、`-adhelp`、`/help`を参照してください。

| 機能 | コマンド | 補足 |
| --- | --- | --- |
| 登録 | `-register <name>` | 初期Eloは500 |
| 名前変更 | `-rename <new_name>` | 通常7日間のクールダウン |
| 試合報告 | `-report @player1 3 1 @player2` | `-rp`も利用可能 |
| 省略したスコア | `-report @winner 31 @loser` | `3-1`や`3:1`も対応 |
| 現在のランキング | `-leaderboard` / `-lb` | カテゴリ選択とページ切替 |
| シーズン別ランキング | `-lbs` | シーズンと月を選択 |
| 統計 | `-stats` | 他プレイヤーの表示も可能 |
| 試合履歴 | `-history` | スラッシュ版では追加の指定が可能 |
| 比較 | `-compare` / `-cp` | プレイヤー比較 |
| League | `-leagues` | League情報 |
| ヘルプ | `-help` / `/help` | コマンド一覧 |

管理者コマンドには原則`Elo Bot Manager`ロールが必要です。

| 機能 | コマンド |
| --- | --- |
| 他プレイヤーの登録 | `-register_o`、`-register_oid` |
| 他プレイヤーの名前変更 | `-rename_o`、`-rename_oid` |
| IDによる試合報告 | `-ad_rp` |
| Eloや統計の編集 | `-set_e`、`-set_w`、`-set_l`、`-set_stats` |
| Eloリセット | `-reset_e` |
| 全登録削除 | `-c_reg` |
| ランキング出力 | `-get_lb` |
| シーズン集計 | `-sum_lb` |
| シーズンの月数 | `/months` |
| 現在のシーズンと月 | `/season` |
| Decayの確認または実行 | `-decay` |
| Decayログ先の設定 | `-decay_setup` |

その他にもLeague、サポート、モデレーション、管理サーバー専用のコマンドがあります。

## 名前の制限

プレイヤー名は2-20文字です。英数字、空白、アンダースコア、ピリオド、ベトナム語を含むアクセント付きラテン文字を使用できます。空白だけの名前、大小文字を問わず`elobot`を含む名前、未対応の文字は使用できません。プレイヤー名に日本語文字が使えるという意味ではありません。

## Eloとシーズン運用

- 新規プレイヤーは500 Eloから開始します。計算方法は`utils/elo.py`に実装されています。
- 試合報告はデータ保存前にreactionによる確認が必要になる場合があります。
- `/months`でシーズンの最大月数を設定し、`/season`で現在の位置を指定します。`-decay`の手動更新にはdev modeと確認が必要です。
- 自動Decayジョブは毎月1日の00:10 UTCに動作を確認します。通常の月ではDecayと月間カウントのリセットを行います。最終月ではデータをアーカイブし、`-c_reg`と共通の処理で登録を削除した後、次のシーズンへ進みます。
- `-c_reg`は登録プレイヤーや関連する試合履歴を削除し、Leagueロールの除去を試みます。daily streakデータは設計上維持されます。実行前にバックアップしてください。
- 過去のランキングは`lb/<guild_id>/Season <n>/Month <m>/`や`Overall/`に保存されます。

**アーカイブの互換性:** `-lbs`は空白やアクセント付きラテン文字を含む名前に対応しています。Discord IDがない古いアーカイブも読み取れますが、IDによる検索にはアーカイブ内のIDが必要です。

## ディレクトリ構成

~~~text
EloTestVer/
  elobot.py                 ボットの起動とCog読み込み
  config.py                 環境変数とreaction設定
  requirements.txt          依存ライブラリ
  cogs/                     一般コマンドと管理者コマンド
    admin_elo/              Decayと管理者ツール
    roles/                  Leagueロール管理
    seasonal_lb/            過去のランキング
    private/                管理サーバー専用コマンド
  db/                       SQLite関連処理
  utils/                    Elo計算、画像描画、cache
  tests/                    自動テスト
  .github/workflows/        GitHub Actions
  assets/                   描画用フォント
  databases/                実行時のSQLite (コミット対象外)
  lb/                       実行時のアーカイブ (コミット対象外)
~~~

## テストとCI

~~~bash
python -m pip install -r requirements.txt "pytest>=8,<9"
python -m compileall -q cogs db utils tests elobot.py
python -m pytest tests -q
~~~

`main`へのpushまたはpull requestでGitHub ActionsがPython 3.11と3.12のテストを実行します。テストには一時的なSQLiteとDiscordのモックを使用します。実際のDiscordサーバーでの権限、reaction、ロール順序、メッセージ送信は別途確認してください。`-lbs`の空白を含む名前に関する回帰テストは、通常の合格必須テストとして実行されます。

## トラブルシューティング

- `DISCORD_TOKEN`エラー: `.env`または環境変数を確認してください。
- プレフィックスコマンドが動作しない: Message Content intent、権限、extensionの起動ログを確認してください。
- `Permission Denied`: `Elo Bot Manager`ロールとサーバーの制限を確認してください。
- アーカイブが見つからない: 出力したシーズン/月と`lb`ディレクトリを確認してください。
- reactionやロール編集に失敗: 権限、ロールの順序、API制限を確認してください。
- CI失敗: GitHub Actionsのログで原因を確認し、テストを無効化せずに修正してください。

**Developed by Senko.**
