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
  - Yahoo FinanceからIV・日経平均データを取得

- ✅ **Slack通知**
  - エントリーシグナル検出時に自動通知
  - 推奨オプション候補の表示

- ✅ **GitHub Actions自動実行**
  - 毎日自動でチェック実行
  - 手動実行も可能

- ✅ **バックテストエンジン**
  - 過去データでの戦略検証
  - 勝率・期待値・Profit Factor等の統計計算

## プロジェクト構造

```
option-chance/
├── src/
│   ├── data_sources/       # データ取得
│   │   ├── jpx.py         # JPXオプション理論価格
│   │   └── market_data.py # 日経平均・VI
│   ├── indicators/        # テクニカル指標
│   │   └── technical.py   # RSI, MACD, BB等
│   ├── signals/           # シグナル判定
│   │   └── gate.py        # Gate条件の統合チェック
│   ├── backtest/          # バックテスト
│   │   └── engine.py      # バックテストエンジン
│   ├── notifiers/         # 通知
│   │   └── slack.py       # Slack通知
│   └── models/            # データモデル
│       └── option.py      # OptionData, MarketData, Signal, Trade
├── scripts/
│   ├── daily_check.py     # 日次エントリーチェック（メイン）
│   ├── test_*.py          # テストスクリプト
│   └── run_backtest.py    # バックテスト実行（TODO）
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

### テスト実行

```bash
# Gate判定のテスト
python scripts/test_gate.py

# 市場データ取得のテスト
python scripts/test_market_data.py

# JPXデータ取得のテスト（GHA環境推奨）
python scripts/test_jpx.py
```

### バックテスト（TODO）

```bash
python scripts/run_backtest.py --start-date 2020-01-01 --end-date 2025-01-01
```

## GitHub Actions

### 自動実行

GitHub Actionsが毎日 **日本時間 9:00 (UTC 0:00)** に自動実行されます。

### 手動実行

1. GitHubリポジトリの「Actions」タブを開く
2. 「Daily Entry Check」ワークフローを選択
3. 「Run workflow」ボタンをクリック
4. パラメータを設定して実行

### シークレットの設定

GitHubリポジトリの Settings > Secrets and variables > Actions で以下を設定：

- `SLACK_WEBHOOK_URL` (必須)

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

**解決策**:
- Yahoo Financeのアクセス制限を確認
- 別のデータソースを検討

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
- [ ] IV×Γ×Δシミュレーション
- [ ] 5日以内に動く確率の定量化

## ライセンス

MIT License

## 貢献

プルリクエストを歓迎します！

## 免責事項

このソフトウェアは教育目的で提供されています。実際の投資判断は自己責任で行ってください。
