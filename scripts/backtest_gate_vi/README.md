# Gate① 日経VI条件の検証スクリプト

`docs/gate_vi_validation.md` のレポートを再現するためのスクリプト群。

## 前提

```bash
pip install pandas numpy pyyaml
```

## 構成

| ファイル | 役割 |
|---|---|
| `vi_sim.py` | 日経平均・日経VIの同時パス生成（公表統計にキャリブレーション済み） |
| `analyze.py` | Gate②A/B・Trigger③ の一括計算、Gate①バリアント定義、オプション価格・トレード評価 |
| `run_freq.py` | 発火頻度の分解、レジーム別集計、先行リターンによる予測力検証 |
| `run_pnl.py` | バリアント別の損益バックテスト |
| `bootstrap.py` | 損益差のブートストラップ信頼区間 |

## 実行

```bash
# 発火頻度・予測力（12パス × 12年 = 137年相当、10分程度）
python3 scripts/backtest_gate_vi/run_freq.py

# 損益バックテスト（第1引数: delta | premium、第2引数: 保有営業日数）
python3 scripts/backtest_gate_vi/run_pnl.py delta 5
python3 scripts/backtest_gate_vi/run_pnl.py premium 10

# 信頼区間（run_pnl.py が出力した trades_*.pkl を使う）
python3 scripts/backtest_gate_vi/bootstrap.py
```

## 実データで再検証する場合

`vi_sim.simulate_path()` の代わりに
`src.data_sources.market_data.MarketDataFetcher.fetch_market_data_with_vi()` の結果を
`date / open / high / low / close / volume / vi` の DataFrame に整形して
`analyze.compute_base_frame()` に渡せば、同じ分析が実データで走る。

**本スクリプトの損益に関する数値はシミュレータの仮定に依存しているため、
実データが取得できる環境では必ず再検証すること。**

## Gate①バリアントの定義

`analyze.vi_gate_variants()` を参照。新しい案を試すときはここに1行追加するだけでよい。
