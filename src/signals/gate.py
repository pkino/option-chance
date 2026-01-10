"""エントリーシグナル判定（Gate統合）"""
from typing import List, Dict, Any, Optional
from datetime import date
import pandas as pd

from ..models.option import Signal, MarketData
from ..indicators.technical import TechnicalIndicators, SignalDetector


class GateChecker:
    """Gate条件の統合チェッカー"""

    def __init__(self, config: Dict[str, Any]):
        """
        Args:
            config: 設定辞書
        """
        self.config = config

    def check_all_gates(
        self, market_data: List[MarketData], events: Optional[List[str]] = None
    ) -> List[Signal]:
        """
        全てのGate条件をチェックしてシグナルを生成

        Args:
            market_data: 市場データのリスト
            events: イベント日付のリスト（YYYY-MM-DD形式）

        Returns:
            Signalのリスト
        """
        if events is None:
            events = self.config.get("events", [])

        # テクニカル指標を計算
        df = TechnicalIndicators.calculate_all(market_data)

        # SignalDetectorを初期化
        detector = SignalDetector(df, self.config)

        # 各日についてシグナルをチェック
        signals = []
        for idx in range(len(df)):
            signal = self._check_single_date(df, idx, detector, events)
            if signal:
                signals.append(signal)

        return signals

    def _check_single_date(
        self, df: pd.DataFrame, idx: int, detector: SignalDetector, events: List[str]
    ) -> Optional[Signal]:
        """
        1日分のシグナルをチェック

        Args:
            df: テクニカル指標を含むDataFrame
            idx: インデックス
            detector: SignalDetector
            events: イベント日付リスト

        Returns:
            Signal or None
        """
        # 最低限のデータが揃っていない場合はスキップ
        if idx < 20:
            return None

        row = df.iloc[idx]
        signal_date = pd.to_datetime(row["date"]).date()

        # Gate① VI安定
        gate_vi = detector.detect_gate_vi(idx)

        # Gate② テクニカル（A）
        gate_top_a, details_a = detector.detect_gate_top_a(idx)

        # Gate② 需給（B）
        gate_top_b, details_b = detector.detect_gate_top_b(idx)

        # Gate② マクロ（C）- オプション
        gate_top_c, details_c = detector.detect_gate_top_c(idx, events)

        # Trigger③
        trigger, details_trigger = detector.detect_trigger(idx)

        # 詳細情報を統合
        details = {
            "gate_vi": {"satisfied": gate_vi},
            "gate_top_a": {"satisfied": gate_top_a, **details_a},
            "gate_top_b": {"satisfied": gate_top_b, **details_b},
            "gate_top_c": {"satisfied": gate_top_c, **details_c},
            "trigger": {"satisfied": trigger, **details_trigger},
            "technical_values": {
                "close": row["close"],
                "rsi": row.get("rsi"),
                "macd": row.get("macd"),
                "macd_hist": row.get("macd_hist"),
                "bb_upper": row.get("bb_upper"),
                "vi": row.get("vi"),
                "volume_ratio": row.get("volume_ratio"),
            },
        }

        signal = Signal(
            date=signal_date,
            gate_vi=gate_vi,
            gate_top_a=gate_top_a,
            gate_top_b=gate_top_b,
            gate_top_c=gate_top_c,
            trigger=trigger,
            details=details,
        )

        return signal

    def get_entry_signals(self, signals: List[Signal]) -> List[Signal]:
        """
        エントリーシグナルのみを抽出

        Args:
            signals: 全シグナルのリスト

        Returns:
            エントリーシグナルのみのリスト
        """
        return [s for s in signals if s.is_entry_signal]

    def get_strong_signals(self, signals: List[Signal]) -> List[Signal]:
        """
        強いシグナル（C_TRUEも含む）のみを抽出

        Args:
            signals: 全シグナルのリスト

        Returns:
            強いシグナルのみのリスト
        """
        return [s for s in signals if s.is_strong_signal]


def format_signal_for_notification(signal: Signal) -> str:
    """
    シグナルを通知用にフォーマット

    Args:
        signal: Signal

    Returns:
        フォーマットされた文字列
    """
    lines = []
    lines.append(f"📊 **エントリーシグナル検出** ({signal.date})")
    lines.append("")

    # Gate状態
    lines.append("**Gate状態:**")
    lines.append(f"  ✅ Gate① VI安定: {'✓' if signal.gate_vi else '✗'}")
    lines.append(f"  ✅ Gate② テクニカル (A): {'✓' if signal.gate_top_a else '✗'}")
    lines.append(f"  ✅ Gate② 需給 (B): {'✓' if signal.gate_top_b else '✗'}")
    lines.append(f"  {'✅' if signal.gate_top_c else '  '} Gate② マクロ (C): {'✓ 強い天井示唆' if signal.gate_top_c else '✗'}")
    lines.append(f"  ✅ Trigger③: {'✓' if signal.trigger else '✗'}")
    lines.append("")

    # テクニカル値
    tech_vals = signal.details.get("technical_values", {})
    lines.append("**テクニカル値:**")
    lines.append(f"  - 終値: {tech_vals.get('close', 'N/A'):.2f}")
    if tech_vals.get("rsi"):
        lines.append(f"  - RSI(14): {tech_vals['rsi']:.2f}")
    if tech_vals.get("macd"):
        lines.append(f"  - MACD: {tech_vals['macd']:.2f}")
    if tech_vals.get("vi"):
        lines.append(f"  - VI: {tech_vals['vi']:.2f}")
    lines.append("")

    # 詳細条件
    details_a = signal.details.get("gate_top_a", {})
    if details_a:
        a_conditions = [k for k, v in details_a.items() if k != "satisfied" and v]
        if a_conditions:
            lines.append("**テクニカル条件 (A):**")
            for cond in a_conditions:
                lines.append(f"  - {cond}")
            lines.append("")

    details_b = signal.details.get("gate_top_b", {})
    if details_b:
        b_conditions = [k for k, v in details_b.items() if k != "satisfied" and v]
        if b_conditions:
            lines.append("**需給条件 (B):**")
            for cond in b_conditions:
                lines.append(f"  - {cond}")
            lines.append("")

    # シグナル強度
    if signal.is_strong_signal:
        lines.append("🔥 **強い天井示唆シグナル** 🔥")
    else:
        lines.append("⚠️ 通常エントリーシグナル")

    return "\n".join(lines)


def test_gate_checker():
    """テスト用関数"""
    import yaml
    from pathlib import Path

    # 設定ファイルを読み込み
    config_path = Path(__file__).parent.parent.parent / "config" / "config.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)

    # ダミーデータを作成
    import numpy as np
    from datetime import timedelta

    start_date = date(2025, 1, 1)
    market_data = []

    for i in range(100):
        d = start_date + timedelta(days=i)
        # ダミー価格データ（上昇トレンド → 天井 → 下落）
        if i < 50:
            close = 30000 + i * 100 + np.random.randn() * 50
        elif i < 70:
            close = 35000 + np.random.randn() * 100  # 天井
        else:
            close = 35000 - (i - 70) * 80 + np.random.randn() * 50  # 下落

        data = MarketData(
            date=d,
            open=close - 50 + np.random.randn() * 20,
            high=close + 100 + abs(np.random.randn() * 30),
            low=close - 100 - abs(np.random.randn() * 30),
            close=close,
            volume=int(1000000 + np.random.randn() * 100000),
            vi=15 + np.random.randn() * 2 if i < 70 else 20 + np.random.randn() * 3,
        )
        market_data.append(data)

    # Gateチェック
    checker = GateChecker(config)
    signals = checker.check_all_gates(market_data)

    print(f"総シグナル数: {len(signals)}")

    # エントリーシグナルを抽出
    entry_signals = checker.get_entry_signals(signals)
    print(f"エントリーシグナル: {len(entry_signals)}")

    # 最初の3つを表示
    for signal in entry_signals[:3]:
        print("\n" + "=" * 60)
        print(format_signal_for_notification(signal))


if __name__ == "__main__":
    test_gate_checker()
