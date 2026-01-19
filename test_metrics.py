"""メトリクス値表示のテストスクリプト"""
import sys
from pathlib import Path
from datetime import date, timedelta
import yaml
import numpy as np

# プロジェクトルートをパスに追加
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from src.models.option import MarketData
from src.signals.gate import GateChecker, format_signal_for_notification

# 設定を読み込み
config_path = project_root / "config" / "config.yaml"
with open(config_path) as f:
    config = yaml.safe_load(f)

# ダミーデータを作成（天井圏からの下落パターン）
start_date = date(2025, 1, 1)
market_data = []

np.random.seed(42)  # 再現性のため

for i in range(100):
    d = start_date + timedelta(days=i)

    # 上昇 → 天井 → 下落のパターンを作成
    if i < 40:
        # 上昇トレンド
        close = 30000 + i * 150 + np.random.randn() * 50
        vi = 12 + np.random.randn() * 1.5
    elif i < 60:
        # 天井圏（高値もみ合い）
        close = 36000 + np.random.randn() * 200
        vi = 14 + np.random.randn() * 1.5
    else:
        # 下落開始
        close = 36000 - (i - 60) * 100 + np.random.randn() * 100
        vi = 16 + (i - 60) * 0.3 + np.random.randn() * 2

    high = close + 100 + abs(np.random.randn() * 50)
    low = close - 100 - abs(np.random.randn() * 50)
    open_price = close + np.random.randn() * 80

    # 天井圏で上ヒゲの長いローソクを意図的に作る
    if 55 <= i <= 65:
        high = close + 300 + abs(np.random.randn() * 100)
        if i % 2 == 0:
            close = open_price - 50  # 陰線

    data = MarketData(
        date=d,
        open=max(low, min(high, open_price)),
        high=high,
        low=low,
        close=max(low, min(high, close)),
        volume=int(1000000 + np.random.randn() * 200000),
        vi=max(10, vi),  # VIは10以上
    )
    market_data.append(data)

# Gateチェック
print("=" * 70)
print("メトリクス値表示テスト")
print("=" * 70)

checker = GateChecker(config)
signals = checker.check_all_gates(market_data)

print(f"\n総シグナル数: {len(signals)}")

if signals:
    # 最新のシグナルを表示
    latest_signal = signals[-1]
    print(f"\n最新シグナル日付: {latest_signal.date}")
    print(f"エントリーシグナル: {latest_signal.is_entry_signal}")
    print("\n" + "=" * 70)
    print(format_signal_for_notification(latest_signal))
    print("=" * 70)

    # 最後の5つのシグナルも簡易表示
    print("\n\n最後の5つのシグナル:")
    for signal in signals[-5:]:
        status = "✅" if signal.is_entry_signal else "⚠️"
        print(f"\n{status} {signal.date}: VI={signal.gate_vi}, A={signal.gate_top_a}, "
              f"B={signal.gate_top_b}, C={signal.gate_top_c}, Trigger={signal.trigger}")

        # Gate VI の詳細
        vi_details = signal.details.get("gate_vi", {})
        if vi_details:
            print(f"   Gate① VI: {vi_details.get('vi'):.2f}, MA={vi_details.get('vi_ma_10'):.2f}")

        # Trigger の詳細
        trigger_details = signal.details.get("trigger", {})
        trigger_vals = trigger_details.get("values", {})
        if trigger_vals:
            print(f"   Trigger: Close={trigger_vals.get('close'):.2f}, "
                  f"PrevLow={trigger_vals.get('prev_low'):.2f}, MA5={trigger_vals.get('ma_5'):.2f}")
else:
    print("\nシグナルなし")

print("\n✅ テスト完了")
