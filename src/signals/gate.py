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

        # クールダウン設定（踏み上げ相場での連続エントリーによる資金枯渇防止）
        cooldown_days = self.config.get("risk_management", {}).get("entry_cooldown_days", 0)
        last_entry_date = None

        # 各日についてシグナルをチェック
        signals = []
        for idx in range(len(df)):
            signal = self._check_single_date(df, idx, detector, events)
            if signal is None:
                continue

            # クールダウン中はエントリーシグナルと需給主導シグナルを抑制
            # （打診シグナルは情報として残す）
            if cooldown_days > 0 and last_entry_date is not None:
                days_since_last = (signal.date - last_entry_date).days
                if days_since_last < cooldown_days:
                    if signal.is_entry_signal or signal.is_supply_dominant_entry:
                        signal.details["cooldown_suppressed"] = True
                        signal.details["days_since_last_entry"] = days_since_last

            signals.append(signal)

            # エントリーシグナルが発火した日を記録
            if signal.is_entry_signal or signal.is_supply_dominant_entry:
                if not signal.details.get("cooldown_suppressed"):
                    last_entry_date = signal.date

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
        gate_vi, vi_values = detector.detect_gate_vi(idx)

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
            "gate_vi": {"satisfied": gate_vi, **vi_values},
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
        エントリーシグナルのみを抽出（クールダウン中のシグナルを除外）

        Args:
            signals: 全シグナルのリスト

        Returns:
            エントリーシグナルのみのリスト
        """
        return [
            s for s in signals
            if s.is_entry_signal and not s.details.get("cooldown_suppressed")
        ]

    def get_probe_signals(self, signals: List[Signal]) -> List[Signal]:
        """
        打診シグナルを抽出（Gate①+②成立、Trigger③未発火）

        IVが低い段階での早期仕込み用。Triggerを待つとIV高騰でプレミアムが
        高くなりすぎるリスクへの対応。ポジションサイズは通常の半分程度に抑える。

        Args:
            signals: 全シグナルのリスト

        Returns:
            打診シグナルのみのリスト
        """
        return [s for s in signals if s.is_probe_signal]

    def get_supply_dominant_signals(self, signals: List[Signal]) -> List[Signal]:
        """
        需給主導型シグナルを抽出（Gate②A不要、Gate②Bに2条件以上）

        テクニカル指標が強気を示す中での突発的需給崩壊（過剰最適化で取りこぼす急落）に対応。
        RSI過熱やMACDの弱化を待っていると「買えないほど高い価格」になるケースへの補完。

        Args:
            signals: 全シグナルのリスト

        Returns:
            需給主導型エントリーシグナルのみのリスト
        """
        return [s for s in signals if s.is_supply_dominant_entry]

    def get_strong_signals(self, signals: List[Signal]) -> List[Signal]:
        """
        強いシグナル（C_TRUEも含む）のみを抽出

        Args:
            signals: 全シグナルのリスト

        Returns:
            強いシグナルのみのリスト
        """
        return [s for s in signals if s.is_strong_signal]


def _fmt(val, spec: str) -> str:
    """数値なら format(val, spec)、それ以外は str(val) を返す。"""
    if isinstance(val, (int, float)):
        return format(val, spec)
    return str(val)


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

    # Gate① VI安定
    gate_vi_details = signal.details.get("gate_vi", {})
    vi_status = '✓' if signal.gate_vi else '✗'
    lines.append(f"  ✅ Gate① VI安定: {vi_status}")
    if gate_vi_details and gate_vi_details.get("vi") is not None:
        lines.append(f"     VI={gate_vi_details.get('vi'):.2f}, MA10={gate_vi_details.get('vi_ma_10'):.2f}, "
                    f"STD10={gate_vi_details.get('vi_std_10'):.2f}, Slope10={gate_vi_details.get('vi_slope_10'):.3f}")

    # Gate② テクニカル (A)
    lines.append(f"  ✅ Gate② テクニカル (A): {'✓' if signal.gate_top_a else '✗'}")
    details_a = signal.details.get("gate_top_a", {})
    if details_a.get("a1_rsi_reversal"):
        a1_vals = details_a.get("a1_values", {})
        lines.append(f"     A1(RSI反転): RSI={_fmt(a1_vals.get('rsi_current', 'N/A'), '.1f')}, "
                    f"1d前={_fmt(a1_vals.get('rsi_1d_ago', 'N/A'), '.1f')}, 2d前={_fmt(a1_vals.get('rsi_2d_ago', 'N/A'), '.1f')}, "
                    f"5d最大={_fmt(a1_vals.get('rsi_max_5d', 'N/A'), '.1f')}")
    if details_a.get("a2_macd_weakening"):
        a2_vals = details_a.get("a2_values", {})
        lines.append(f"     A2(MACD弱化): MACD={_fmt(a2_vals.get('macd', 'N/A'), '.2f')}, "
                    f"Hist={_fmt(a2_vals.get('macd_hist_current', 'N/A'), '.2f')}, "
                    f"1d前={_fmt(a2_vals.get('macd_hist_1d', 'N/A'), '.2f')}, 2d前={_fmt(a2_vals.get('macd_hist_2d', 'N/A'), '.2f')}")
    if details_a.get("a3_divergence"):
        a3_vals = details_a.get("a3_values", {})
        lines.append(f"     A3(ダイバージェンス): Close={_fmt(a3_vals.get('close', 'N/A'), '.2f')}, "
                    f"20d高値={_fmt(a3_vals.get('high_20', 'N/A'), '.2f')}, "
                    f"RSI={_fmt(a3_vals.get('rsi_current', 'N/A'), '.1f')}, 20dRSI最大={_fmt(a3_vals.get('rsi_max_20d', 'N/A'), '.1f')}")
    if details_a.get("a4_overbought"):
        a4_vals = details_a.get("a4_values", {})
        lines.append(f"     A4(買われすぎ): Close={_fmt(a4_vals.get('close', 'N/A'), '.2f')}, "
                    f"BB上限={_fmt(a4_vals.get('bb_upper', 'N/A'), '.2f')}, "
                    f"上髭比={_fmt(a4_vals.get('upper_wick_ratio', 'N/A'), '.2f')}, 陰線={a4_vals.get('is_bearish', False)}")

    # Gate② 需給 (B)
    lines.append(f"  ✅ Gate② 需給 (B): {'✓' if signal.gate_top_b else '✗'}")
    details_b = signal.details.get("gate_top_b", {})
    if details_b.get("b1_volume_failure"):
        b1_vals = details_b.get("b1_values", {})
        lines.append(f"     B1(出来高不全): Volume比={_fmt(b1_vals.get('volume_ratio', 'N/A'), '.2f')}, "
                    f"高値距離%={_fmt(b1_vals.get('distance_from_high_20_pct', 'N/A'), '.2f')}")
    if details_b.get("b2_upper_wick_dominance"):
        b2_vals = details_b.get("b2_values", {})
        wick_ratios = b2_vals.get('upper_wick_ratios', [])
        wick_str = ', '.join([f"{r:.2f}" for r in wick_ratios]) if wick_ratios else 'N/A'
        lines.append(f"     B2(上髭優勢): 該当日数={b2_vals.get('upper_wick_days_count', 'N/A')}, "
                    f"上髭比(3d)=[{wick_str}]")
    if details_b.get("b3_gap_up_failure"):
        b3_vals = details_b.get("b3_values", {})
        lines.append(f"     B3(寄り天): Open={_fmt(b3_vals.get('open', 'N/A'), '.2f')}, "
                    f"Close={_fmt(b3_vals.get('close', 'N/A'), '.2f')}, "
                    f"前日高値={_fmt(b3_vals.get('prev_high', 'N/A'), '.2f')}, Gap%={_fmt(b3_vals.get('gap_pct', 'N/A'), '.2f')}")

    # Gate② マクロ (C)
    lines.append(f"  {'✅' if signal.gate_top_c else '  '} Gate② マクロ (C): {'✓ 強い天井示唆' if signal.gate_top_c else '✗'}")
    details_c = signal.details.get("gate_top_c", {})
    if details_c.get("c1_event_proximity"):
        c1_vals = details_c.get("c1_values", {})
        lines.append(f"     C1(イベント近接): 最接近イベント={c1_vals.get('nearest_event_date', 'N/A')}, "
                    f"残日数={c1_vals.get('days_until_event', 'N/A')}")

    # Trigger③
    trigger_details = signal.details.get("trigger", {})
    trigger_vals = trigger_details.get("values", {})
    trigger_status = '✓' if signal.trigger else '✗'
    lines.append(f"  ✅ Trigger③: {trigger_status}")
    if trigger_vals:
        prev_low_break = '✓' if trigger_details.get("prev_low_break") else '✗'
        ma5_break = '✓' if trigger_details.get("ma5_break") else '✗'
        lines.append(f"     Close={_fmt(trigger_vals.get('close', 'N/A'), '.2f')}, "
                    f"前日安値={_fmt(trigger_vals.get('prev_low', 'N/A'), '.2f')}({prev_low_break}), "
                    f"MA5={_fmt(trigger_vals.get('ma_5', 'N/A'), '.2f')}({ma5_break})")

    lines.append("")

    # テクニカル値サマリー
    tech_vals = signal.details.get("technical_values", {})
    lines.append("**テクニカル値サマリー:**")
    lines.append(f"  終値={_fmt(tech_vals.get('close', 'N/A'), '.2f')}, "
                f"RSI={_fmt(tech_vals.get('rsi', 'N/A'), '.1f')}, "
                f"MACD={_fmt(tech_vals.get('macd', 'N/A'), '.2f')}, "
                f"VI={_fmt(tech_vals.get('vi', 'N/A'), '.2f')}")
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
