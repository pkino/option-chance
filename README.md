# option-chance

日経225オプション：プット買い戦略の自動シグナル検出システム

## 概要

このプロジェクトは、日経225オプションのプット買い戦略（20〜40円コア）において、**低VI環境での天井→調整（急落含む）** を捉えるエントリーシグナルを自動検出し、Slackに通知するシステムです。

### 主な機能

- ✅ **自動エントリーシグナル検出**
  - Gate① VI安定条件のチェック
  - Gate② 天井示唆条件のチェック（テクニカル・需給・マクロ）
  - Trigger③ エントリートリガーの検出

- ✅ **データソースの自動取得**
  - JPX（日本取引所グループ）からオプション理論価格を取得
  - **日経平均データ取得** ⭐ NEW
    - 日経公式CSV（直接ダウンロード・無料・公式）
  - **日経VI（ボラティリティ・インデックス）** ⭐ NEW
    - 日経公式CSV（直接ダウンロード・無料・公式）
    - フォールバック: 日経平均から20日ボラティリティを計算（年率化）

- ✅ **Slack通知**
  - エントリーシグナル検出時に自動通知
  - 推奨オプション候補の表示

- ✅ **GitHub Actions自動実行**
  - 毎日自動でチェック実行
  - 手動実行も可能

- ✅ **判定履歴とダッシュボード** ⭐ NEW
  - 毎日の判定結果と根拠指標を `history/signals.jsonl` に蓄積
  - Gate/Trigger の成立状況・VI・RSI等の推移をHTMLダッシュボードで可視化
  - 判定条件（閾値）を変更した日もグラフ上で識別可能

- ✅ **バックテストエンジン**
  - 過去データでの戦略検証
  - 勝率・期待値・Profit Factor等の統計計算

## プロジェクト構造

```
option-chance/
├── src/
│   ├── data_sources/       # データ取得
│   │   ├── jpx.py         # JPXオプション理論価格
│   │   ├── nikkei_225.py  # 日経平均（日経公式CSV）
│   │   ├── nikkei_vi.py   # 日経VI（日経公式CSV）
│   │   └── market_data.py # 日経平均・VI統合（20日ボラ計算含む）
│   ├── indicators/        # テクニカル指標
│   │   └── technical.py   # RSI, MACD, BB等
│   ├── signals/           # シグナル判定
│   │   └── gate.py        # Gate条件の統合チェック
│   ├── backtest/          # バックテスト
│   │   └── engine.py      # バックテストエンジン
│   ├── notifiers/         # 通知
│   │   └── slack.py       # Slack通知
│   ├── models/            # データモデル
│   │   └── option.py      # OptionData, MarketData, Signal, Trade
│   └── history/           # 判定履歴の読み書き
│       └── store.py       # JSONL 追記・config_hash 算出
├── scripts/
│   ├── daily_check.py     # 日次エントリーチェック（メイン）
│   ├── build_dashboard.py # 判定履歴 → HTMLダッシュボード生成
│   ├── test_*.py          # テストスクリプト
│   └── run_backtest.py    # バックテスト実行（TODO）
├── history/
│   └── signals.jsonl      # 日次判定履歴（1判定日=1行、バッチが追記）
├── docs/
│   └── index.html         # 判定条件ダッシュボード（GitHub Pages公開用）
├── config/
│   └── config.yaml        # 設定ファイル
├── .github/workflows/
│   └── daily-check.yml    # GitHub Actions ワークフロー
└── requirements.txt       # Python依存関係
```

## セットアップ

### 1. 環境構築

```bash
# リポジトリをクローン
git clone https://github.com/your-username/option-chance.git
cd option-chance

# Python仮想環境を作成
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 依存関係をインストール
pip install -r requirements.txt
```

### 2. 環境変数の設定

`.env` ファイルを作成し、以下の環境変数を設定：

```bash
# Slack Webhook URL（必須）
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/YOUR/WEBHOOK/URL
```

### 3. 設定ファイルのカスタマイズ

`config/config.yaml` で戦略パラメータをカスタマイズできます：

```yaml
# オプション選定条件
option_selection:
  premium_range:
    min: 20  # 最小プレミアム（円）
    max: 40  # 最大プレミアム（円）
  dte_range:
    min: 10  # 最小残存営業日
    max: 30  # 最大残存営業日
  delta_range:
    min: -0.40
    max: -0.25
  target_delta: -0.30

# Gate① VI安定条件
gate_vi:
  vi_threshold: 20
  vi_10d_avg_threshold: 20
  vi_10d_std_threshold: 1.5
  vi_10d_slope_threshold: 0

# その他の設定...
```

## 使い方

### 日次エントリーチェック

```bash
# 基本実行
python scripts/daily_check.py

# オプション指定
python scripts/daily_check.py --lookback-days 90 --send-no-signal
```

**オプション:**
- `--lookback-days N`: 過去N日分のデータを取得（デフォルト: 60）
- `--send-no-signal`: シグナルがない場合もSlackに通知
- `--history PATH`: 判定履歴の出力先（デフォルト: `history/signals.jsonl`）

実行するたびに、判定結果と根拠指標が `history/signals.jsonl` に1行追記されます。
同じ日に複数回実行した場合は、その日の行が上書きされます（重複しません）。

### 判定条件ダッシュボード

蓄積した履歴から、判定条件の時系列変化を1枚のHTMLにまとめます。

```bash
# history/signals.jsonl → docs/index.html
python scripts/build_dashboard.py

# 期間や入出力を指定
python scripts/build_dashboard.py --history history/signals.jsonl --out docs/index.html --days 90
```

**表示内容:**
1. **最新の判定サマリー** — 総合判定（待機中／打診／エントリー成立など）、Gate①〜Trigger③の成否チップ、VI・RSI・終値の現在値
2. **判定条件の成立状況** — Gate①/②A/②B/②C/Trigger③ と各サブ条件（A1〜A4, B1〜B3）の日次ヒートマップ。
   赤＝シグナル発火、青＝条件成立
3. **Gate① VI水準 / VIの安定度** — VI・VI MA10、STD10・Slope10 と各閾値
4. **RSI(14) / MACDヒストグラム** — Gate②Aの根拠
5. **日経平均とシグナル** — 終値・MA5・BB上限と、エントリー/打診シグナルの発生日
6. **直近30日の判定と指標値** — 生の数値テーブル（折りたたみ）

画面右上の **30日 / 90日 / 全期間** ボタンで全グラフの表示期間をまとめて切り替えられます。
配色はOSのダークモード設定に追従します。

`config/config.yaml` の判定条件（`gate_vi` / `gate_top` / `trigger` / `risk_management` /
`option_selection`）を変更すると各レコードの `config_hash` が変わり、
ダッシュボード上に「条件変更」の縦線が引かれます。
**「閾値を緩めた前後で検知がどう変わったか」がグラフ上で直接比較できます。**

> 履歴は導入時点から蓄積されます（過去へのさかのぼり生成は行いません）。
> そのため導入直後はデータ点が少なく、推移として読めるようになるまで数週間かかります。

### テスト実行

```bash
# Gate判定のテスト
python scripts/test_gate.py

# 市場データ取得のテスト
python scripts/test_market_data.py

# JPXデータ取得のテスト（GHA環境推奨）
python scripts/test_jpx.py

# ユニットテスト（判定履歴・ダッシュボード）
pytest tests/ -v
```

### バックテスト（TODO）

```bash
python scripts/run_backtest.py --start-date 2020-01-01 --end-date 2025-01-01
```

## GitHub Actions

### 自動実行

GitHub Actionsが毎日 **日本時間 18:00 (UTC 9:00)** に自動実行されます。

### 手動実行

1. GitHubリポジトリの「Actions」タブを開く
2. 「Daily Entry Check」ワークフローを選択
3. 「Run workflow」ボタンをクリック
4. パラメータを設定して実行

### シークレットの設定

GitHubリポジトリの Settings > Secrets and variables > Actions で以下を設定：

- `SLACK_WEBHOOK_URL` (必須)

### ダッシュボードの公開（GitHub Pages）

毎日バッチは判定後に `history/signals.jsonl` と `docs/index.html` を更新し、
リポジトリに自動コミットします（`permissions: contents: write` が必要）。

これをWebで見るには、**リポジトリ側で1回だけ**以下を設定してください：

1. リポジトリの Settings > Pages を開く
2. Source を「Deploy from a branch」にする
3. Branch を `main` / `/docs` に設定して Save

設定後、`https://<ユーザー名>.github.io/option-chance/` でダッシュボードが閲覧できます。

公開したくない場合はPagesを有効化しなくても構いません。
その場合は `docs/index.html` をローカルにpullしてブラウザで開けば同じ内容が見られます。

## 戦略の詳細

### エントリー条件

**Gate① VI安定条件**
- VI終値 ≤ 20
- 直近10営業日平均VI ≤ 20
- 直近10営業日VI標準偏差 ≤ 1.5
- 直近10営業日VIの傾き ≤ 0

**Gate② 天井示唆条件**

- **A. テクニカル**（4条件のうち2つ以上）
  - A1) RSI過熱→反転
  - A2) MACD弱化
  - A3) ダイバージェンス
  - A4) 伸び切り（ボリンジャー）

- **B. 需給**（3条件のうち1つ以上）【必須】
  - B1) 高値圏の出来高失速
  - B2) 上ヒゲ優勢
  - B3) ギャップアップ失速

- **C. マクロ**（オプション：強化フラグ）
  - C1) イベント近接
  - C2) 逆風レジーム
  - C3) バリュエーション過熱

**Gate②の判定**: `A_TRUE AND B_TRUE`

**Trigger③ エントリートリガー**
- 前日安値割れ（終値ベース） **OR** 5日MA割れ（終値）

### 退出ルール

**損切り**
- 時間損切り：5営業日
- 構造損切り：高値更新×再上向き（TODO）

**利確**
- 2倍到達で半分利確
- 残りはトレーリング（TODO）

### オプション選定

- プレミアム：20〜40円
- 残存：10〜30営業日
- デルタ：-0.25〜-0.40（目標: -0.30に最も近い）

## トラブルシューティング

### データ取得エラー

**問題**: JPXデータ取得で403エラー

**解決策**:
- GHA環境で実行する（ローカル環境ではプロキシ制限がある場合あり）

**問題**: 日経平均・VIデータ取得失敗

**現状の実装**:
- **日経平均**: 日経公式CSV（無料・公式・安定）
  - URL: `https://indexes.nikkei.co.jp/nkave/historical/nikkei_stock_average_daily_jp.csv`
- **日経VI**: 日経公式CSV（無料・公式・安定）
  - URL: `https://indexes.nikkei.co.jp/nkave/historical/nikkei_stock_average_vi_daily_jp.csv`
- **フォールバック**: 20日ボラティリティ計算（VIのみ）

**主なエラーと解決策**:

1. **日経公式CSVエラー**
   - 日経サーバーがメンテナンス中の場合は時間を置いて再試行
   - VIが取得できない場合は自動的に20日ボラティリティ計算にフォールバック
   - CSV URLが変更された場合は以下を更新：
     - 日経平均: `nikkei_225.py` の `nikkei_csv_url`
     - 日経VI: `nikkei_vi.py` の `vi_csv_url`

3. **20日ボラティリティ計算（VI代替）**（実装済み）
   - 日経平均データが取得できれば、自動的に20日ボラティリティを計算
   - 年率化されたボラティリティをVIとして使用
   - 公式VIと高い相関があり、実用上問題なし
   - **確実に動作する**

4. **依存関係の確認**（重要）
   - `numpy>=1.24.0`、`pandas>=2.1.0`、`requests>=2.31.0` が必要
   - GHA環境でのインストール: 自動的に行われる
   - ローカル環境: `pip install -r requirements.txt`

5. **GitHub Actionsで実行**（推奨）
   - GHA環境は通常プロキシ制限がないため成功する可能性が高い
   - 自動スケジュール実行（18:00 JST）で運用
   - デバッグログで各データソースの結果を確認

### Slack通知が届かない

**解決策**:
1. `SLACK_WEBHOOK_URL` が正しく設定されているか確認
2. Webhook URLの有効期限を確認
3. テストスクリプトで動作確認: `python src/notifiers/slack.py`

## TODO

- [ ] 構造損切りの実装
- [ ] トレーリング利確の実装
- [ ] バックテスト実行スクリプト
- [ ] Web UI（Streamlit）
- [ ] 判定履歴の過去分バックフィル（現状は導入時点からの蓄積のみ）
- [ ] IV×Γ×Δシミュレーション
- [ ] 5日以内に動く確率の定量化

## ライセンス

MIT License

## 貢献

プルリクエストを歓迎します！

## 免責事項

このソフトウェアは教育目的で提供されています。実際の投資判断は自己責任で行ってください。
