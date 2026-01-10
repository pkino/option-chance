"""Gate判定のテスト"""
import sys
from pathlib import Path
from datetime import date, timedelta
import numpy as np

# プロジェクトルートをパスに追加
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import yaml
from src.models.option import MarketData
from src.signals.gate import GateChecker, format_signal_for_notification


def main():
    """メイン処理"""
    print("Gate判定テスト開始...")

    # 設定ファイルを読み込み
    config_path = project_root / "config" / "config.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)

    # ダミーデータを作成（現実的なパターン）
    start_date = date(2025, 1, 1)
    market_data = []

    print("ダミー市場データを生成中...")

    for i in range(100):
        d = start_date + timedelta(days=i)
        # ダミー価格データ（上昇トレンド → 天井 → 下落）
        if i < 50:
            # 上昇トレンド
            close = 30000 + i * 100 + np.random.randn() * 50
            vi = 15 + np.random.randn() * 1.5
        elif i < 70:
            # 天井圏（高値もみ合い）
            close = 35000 + np.random.randn() * 100
            vi = 16 + np.random.randn() * 1.0
        else:
            # 下落開始
            close = 35000 - (i - 70) * 80 + np.random.randn() * 50
            vi = 18 + (i - 70) * 0.3 + np.random.randn() * 2.0

        data = MarketData(
            date=d,
            open=close - 50 + np.random.randn() * 20,
            high=close + 100 + abs(np.random.randn() * 30),
            low=close - 100 - abs(np.random.randn() * 30),
            close=close,
            volume=int(1000000 + np.random.randn() * 100000),
            vi=vi,
        )
        market_data.append(data)

    print(f"市場データ生成完了: {len(market_data)} 件")

    # Gateチェック
    print("\nGate判定実行中...")
    checker = GateChecker(config)
    signals = checker.check_all_gates(market_data)

    print(f"\n総シグナル数: {len(signals)}")

    # エントリーシグナルを抽出
    entry_signals = checker.get_entry_signals(signals)
    print(f"エントリーシグナル: {len(entry_signals)}")

    # 強いシグナルを抽出
    strong_signals = checker.get_strong_signals(signals)
    print(f"強いシグナル（C条件含む）: {len(strong_signals)}")

    # エントリーシグナルを表示
    if entry_signals:
        print("\n" + "=" * 70)
        print("エントリーシグナル詳細:")
        print("=" * 70)

        for i, signal in enumerate(entry_signals[:5], 1):  # 最初の5つ
            print(f"\n--- シグナル {i} ---")
            print(format_signal_for_notification(signal))

    else:
        print("\n⚠️ エントリーシグナルが検出されませんでした")
        print("（ダミーデータでは条件を満たさない可能性があります）")

        # デバッグ情報を表示
        print("\n各Gate条件の成立件数:")
        gate_vi_count = sum(1 for s in signals if s.gate_vi)
        gate_a_count = sum(1 for s in signals if s.gate_top_a)
        gate_b_count = sum(1 for s in signals if s.gate_top_b)
        gate_c_count = sum(1 for s in signals if s.gate_top_c)
        trigger_count = sum(1 for s in signals if s.trigger)

        print(f"  Gate① (VI安定): {gate_vi_count} / {len(signals)}")
        print(f"  Gate② (A: テクニカル): {gate_a_count} / {len(signals)}")
        print(f"  Gate② (B: 需給): {gate_b_count} / {len(signals)}")
        print(f"  Gate② (C: マクロ): {gate_c_count} / {len(signals)}")
        print(f"  Trigger③: {trigger_count} / {len(signals)}")


if __name__ == "__main__":
    main()
