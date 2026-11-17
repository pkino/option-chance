"""エントリーシグナル判定（Gate統合）"""
from typing import List, Dict, Any, Optional
from datetime import date
import numpy as np
import pandas as pd

from ..models.option import Signal, MarketData
from .events import Event, load_events, upcoming_events
from ..indicators.technical import (
    DEFAULT_VI_PERCENTILE_WINDOW,
    DEFAULT_VI_PRICE,
    SignalDetector,
    TechnicalIndicators,
)


# technical_values に常時格納する指標列。
# 各 detect_* は条件不成立時に空の詳細を返すため、判定の成否に関わらず
# ここで指標値を揃えておかないと時系列（履歴・ダッシュボード）が歯抜けになる。
_TECHNICAL_VALUE_COLUMNS = [
    "open",
    "high",
    "low",
    "close",
    "volume",
    "rsi",
    "macd",
    "macd_signal",
    "macd_hist",
    "bb_upper",
    "bb_middle",
    "ma_5",
    "ma_20",
    "ma_25",
    "vi",
    "vi_open",
    "vi_high",
    "vi_low",
    "vi_gate",
    "vi_ma_10",
    "vi_std_10",
    "vi_slope_10",
    "vi_cv_10",
    "vi_pct_1y",
    "volume_ratio",
    "range_ratio",
    "atr_5",
    "atr_20",
    "upper_wick_ratio",
    "lower_wick_ratio",
    "body_ratio",
    "distance_from_high_20_pct",
    "high_20",
    "close_max_20",
    "is_bearish",
]


def _to_plain(val: Any) -> Any:
    """numpy/pandas のスカラーを素の Python 型に、NaN/NaT を None に正規化する。"""
    if val is None:
        return None
    if isinstance(val, (bool, np.bool_)):
        return bool(val)
    if pd.isna(val):
        return None
    if isinstance(val, (int, float, np.integer, np.floating)):
        return float(val)
    return val


def _collect_technical_values(df: pd.DataFrame, idx: int) -> Dict[str, Any]:
    """判定の成否に関わらず、その日の指標値を一式そろえて返す。"""
    row = df.iloc[idx]

    values: Dict[str, Any] = {}
    for col in _TECHNICAL_VALUE_COLUMNS:
        values[col] = _to_plain(row.get(col))

    # 前日安値（Trigger③ の判定根拠。不成立日でも推移を追えるようにする）
    values["prev_low"] = _to_plain(df.iloc[idx - 1]["low"]) if idx > 0 else None

    return values


def _collect_data_quality(df: pd.DataFrame, idx: int) -> Dict[str, bool]:
    """判定に使えたデータの有無。欠けていると一部の条件が代替指標や終値で判定される。"""
    row = df.iloc[idx]

    def _present(col: str) -> bool:
        return col in df.columns and not pd.isna(row.get(col))

    return {
        "volume_available": _present("volume_ratio"),
        "vi_ohlc_available": _present("vi_open") and _present("vi_high") and _present("vi_low"),
    }


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
        events = load_events(events)

        # テクニカル指標を計算
        gate_vi_config = self.config.get("gate_vi", {})
        df = TechnicalIndicators.calculate_all(
            market_data,
            vi_percentile_window=gate_vi_config.get(
                "vi_percentile_window", DEFAULT_VI_PERCENTILE_WINDOW
            ),
            vi_price=gate_vi_config.get("vi_price", DEFAULT_VI_PRICE),
        )

        # SignalDetectorを初期化
        detector = SignalDetector(df, self.config)

        # クールダウン設定（踏み上げ相場での連続エントリーによる資金枯渇防止）
        cooldown_days = self.config.get("risk_management", {}).get("entry_cooldown_days", 0)
        last_entry_date = None

        # イベント前打診の設定
        event_probe_config = self.config.get("event_probe") or {}
        event_probe_enabled = bool(event_probe_config.get("enabled", False))
        lookahead_days = int(event_probe_config.get("lookahead_days", 5))
        event_tiers = list(event_probe_config.get("tiers", ["major"]))
        covered_events: set = set()  # 打診済み（または別シグナルでカバー済み）のイベント

        # 各日についてシグナルをチェック
        signals = []
        for idx in range(len(df)):
            signal = self._check_single_date(df, idx, detector, events)
            if signal is None:
                continue

            in_cooldown = False
            days_since_last = None
            if cooldown_days > 0 and last_entry_date is not None:
                days_since_last = (signal.date - last_entry_date).days
                in_cooldown = days_since_last < cooldown_days

            # クールダウン中はエントリーシグナルと需給主導シグナルを抑制
            # （打診シグナルは情報として残す）
            if in_cooldown and (signal.is_entry_signal or signal.is_supply_dominant_entry):
                signal.details["cooldown_suppressed"] = True
                signal.details["days_since_last_entry"] = days_since_last

            if event_probe_enabled:
                self._apply_event_probe(
                    signal, events, lookahead_days, event_tiers, covered_events,
                    in_cooldown, days_since_last,
                )

            signals.append(signal)

            # エントリーシグナルが発火した日を記録
            if signal.is_entry_signal or signal.is_supply_dominant_entry:
                if not signal.details.get("cooldown_suppressed"):
                    last_entry_date = signal.date

        return signals

    @staticmethod
    def _apply_event_probe(
        signal: Signal,
        events: List[Event],
        lookahead_days: int,
        tiers: List[str],
        covered_events: set,
        in_cooldown: bool,
        days_since_last: Optional[int],
    ) -> None:
        """イベント前打診の成否を signal.details["event_probe"] に書き込む。

        Gate① が実データで成立している日に、1〜lookahead_days 暦日先に未カバーの
        対象イベントがあれば発火する。発火（または同日に別の発注シグナル）で
        そのイベントと、そこから lookahead_days 以内に続くイベントはカバー済みになり、
        以後の日では発火しない。
        """
        vi_details = signal.details.get("gate_vi", {})
        # VIが欠損して Gate① がスキップ扱い（True）になった日では発火させない
        if not (signal.gate_vi and vi_details.get("vi") is not None):
            return

        pending = [
            (event, days)
            for event, days in upcoming_events(events, signal.date, 1, lookahead_days, tiers)
            if event not in covered_events
        ]
        if not pending:
            return

        event, days = pending[0]
        # 発火したら、直後に続くイベント（例: FOMC の2日後の日銀会合）も同じポジションで
        # カバーしたとみなす。対象は「最初のイベントから lookahead_days 暦日以内」の未カバー分
        cluster = [
            e for e in events
            if e.tier in tiers and e not in covered_events
            and 0 <= (e.date - event.date).days <= lookahead_days
        ]
        info: Dict[str, Any] = {
            "fires": not in_cooldown,
            "event_date": event.date.isoformat(),
            "event_name": event.name,
            "event_tier": event.tier,
            "days_until_event": days,
            "events_in_window": [f"{e.name} {e.date.isoformat()}" for e, _ in pending],
            "covers": [f"{e.name} {e.date.isoformat()}" for e in cluster],
        }
        if in_cooldown:
            info["cooldown_suppressed"] = True
            info["days_since_last_entry"] = days_since_last
        signal.details["event_probe"] = info

        other_actionable = (
            (signal.is_entry_signal or signal.is_supply_dominant_entry)
            and not signal.details.get("cooldown_suppressed")
        ) or signal.is_probe_signal
        if info["fires"] or other_actionable:
            covered_events.update(e for e, _ in pending)
            covered_events.update(cluster)

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
            "technical_values": _collect_technical_values(df, idx),
            "data_quality": _collect_data_quality(df, idx),
        }
        # B4 の上昇率（VIの直近安値比）は設定の lookback に依存するので判定結果から取る
        details["technical_values"]["vi_rise"] = _to_plain(
            details_b.get("b4_values", {}).get("vi_rise")
        )

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

    def get_event_probe_signals(self, signals: List[Signal]) -> List[Signal]:
        """イベント前打診シグナルを抽出（Gate①成立 AND 直前に major イベント）"""
        return [s for s in signals if s.is_event_probe_signal]

    def get_strong_signals(self, signals: List[Signal]) -> List[Signal]:
        """
        強いシグナル（C_TRUEも含む）のみを抽出

        Args:
            signals: 全シグナルのリスト

        Returns:
            強いシグナルのみのリスト
        """
        return [s for s in signals if s.is_strong_signal]


# 通知する種類（優先度の高い順）
SIGNAL_KINDS = ("entry", "supply", "probe", "event_probe")
SIGNAL_KIND_LABELS = {
    "entry": "エントリーシグナル",
    "supply": "需給主導エントリー",
    "probe": "打診シグナル",
    "event_probe": "イベント前打診",
}


def classify_signal(signal: Signal) -> Optional[str]:
    """その日に通知すべきシグナルの種類を返す（なければ None）。

    1日に複数成立しうるので、優先度 entry > supply > probe > event_probe で1つに絞る。
    クールダウンで抑制されたエントリー・需給主導は通知しない。
    """
    suppressed = signal.details.get("cooldown_suppressed", False)
    if signal.is_entry_signal and not suppressed:
        return "entry"
    if signal.is_supply_dominant_entry and not suppressed:
        return "supply"
    if signal.is_probe_signal:
        return "probe"
    if signal.is_event_probe_signal:
        return "event_probe"
    return None


def _fmt(val, spec: str) -> str:
    """数値なら format(val, spec)、欠損は N/A、それ以外は str(val) を返す。"""
    if val is None:
        return "N/A"
    if isinstance(val, (int, float)):
        return format(val, spec)
    return str(val)


_B1_PROXY_LABELS = {"volume": "出来高", "range": "値幅"}

_VI_PRICE_LABELS = {"close": "終値", "open": "始値", "median4": "四本値中央値"}


def _vi_label(gate_vi_details: Dict[str, Any]) -> str:
    """「VI判定値=30.60（四本値中央値／終値 25.00）」形式。終値判定なら従来どおり VI=…。"""
    vi = gate_vi_details.get("vi")
    vi_price = gate_vi_details.get("vi_price", DEFAULT_VI_PRICE)
    if vi_price == "close":
        return f"VI={_fmt(vi, '.2f')}"
    label = _VI_PRICE_LABELS.get(vi_price, vi_price)
    return (
        f"VI判定値={_fmt(vi, '.2f')}（{label}／終値 {_fmt(gate_vi_details.get('vi_close'), '.2f')}）"
    )


def format_signal_for_notification(signal: Signal) -> str:
    """
    シグナルを通知用にフォーマット

    Args:
        signal: Signal

    Returns:
        フォーマットされた文字列
    """
    kind = classify_signal(signal)
    lines = []
    lines.append(f"📊 **{SIGNAL_KIND_LABELS.get(kind, '判定結果')}** ({signal.date})")
    lines.append("")

    event_probe = signal.details.get("event_probe", {})
    if event_probe:
        state = "成立" if event_probe.get("fires") else "クールダウンで抑制"
        lines.append(
            f"🗓️ **イベント前打診（{state}）**: {event_probe.get('event_name')} "
            f"{event_probe.get('event_date')} まで{event_probe.get('days_until_event')}日"
        )
        follows = (event_probe.get("covers") or [])[1:]
        if follows:
            lines.append(f"     続くイベントも対象: {', '.join(follows)}")
        lines.append("")

    # Gate状態
    lines.append("**Gate状態:**")

    # Gate① VI安定
    gate_vi_details = signal.details.get("gate_vi", {})
    vi_status = '✓' if signal.gate_vi else '✗'
    lines.append(f"  ✅ Gate① VI安定: {vi_status}")
    if gate_vi_details and gate_vi_details.get("vi") is not None:
        vi_label = _vi_label(gate_vi_details)
        if gate_vi_details.get("mode") == "hybrid":
            pct = gate_vi_details.get("vi_pct_1y")
            rank = f"1年順位 {pct:.0f}%" if pct is not None else "1年順位 —（履歴不足）"
            lines.append(f"     {vi_label}（{rank}）, "
                        f"MA10={_fmt(gate_vi_details.get('vi_ma_10'), '.2f')}, "
                        f"CV10={_fmt(gate_vi_details.get('vi_cv_10'), '.3f')}, "
                        f"Slope10={_fmt(gate_vi_details.get('vi_slope_10'), '.3f')}")
        else:
            lines.append(f"     {vi_label}, "
                        f"MA10={_fmt(gate_vi_details.get('vi_ma_10'), '.2f')}, "
                        f"STD10={_fmt(gate_vi_details.get('vi_std_10'), '.2f')}, "
                        f"Slope10={_fmt(gate_vi_details.get('vi_slope_10'), '.3f')}")

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
    b1_vals = details_b.get("b1_values", {})
    if b1_vals:
        b1_mark = "✓" if details_b.get("b1_volume_failure") else "✗"
        if not b1_vals.get("available", True):
            lines.append(f"     B1(高値圏の失速): 判定不能（{_B1_PROXY_LABELS.get(b1_vals.get('proxy_used'), '')}データなし）")
        else:
            lines.append(f"     B1(高値圏の失速・{_B1_PROXY_LABELS.get(b1_vals.get('proxy_used'), '出来高')}): {b1_mark} "
                        f"5日/20日比={_fmt(b1_vals.get('ratio'), '.2f')}, "
                        f"高値距離%={_fmt(b1_vals.get('distance_from_high_20_pct'), '.2f')}")
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
    if details_b.get("b4_vi_divergence"):
        b4_vals = details_b.get("b4_values", {})
        counted = "" if details_b.get("b4_counted") else "・記録のみ"
        lines.append(f"     B4(VI逆行{counted}): VI={_fmt(b4_vals.get('vi'), '.2f')}, "
                    f"直近安値={_fmt(b4_vals.get('vi_min'), '.2f')}, "
                    f"上昇率={_fmt((b4_vals.get('vi_rise') or 0) * 100, '.0f')}%")

    # Gate② マクロ (C)
    lines.append(f"  {'✅' if signal.gate_top_c else '  '} Gate② マクロ (C): {'✓ 強い天井示唆' if signal.gate_top_c else '✗'}")
    details_c = signal.details.get("gate_top_c", {})
    if details_c.get("c1_event_proximity"):
        c1_vals = details_c.get("c1_values", {})
        lines.append(f"     C1(イベント近接): 最接近イベント={c1_vals.get('nearest_event_name', '')} "
                    f"{c1_vals.get('nearest_event_date', 'N/A')}, "
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

    # シグナル強度・建て方の目安
    if kind == "entry" and signal.is_strong_signal:
        lines.append("🔥 **強い天井示唆シグナル** 🔥")
    elif kind in ("entry", "supply"):
        lines.append("⚠️ 通常エントリーシグナル")
    elif kind == "probe":
        lines.append("🟡 打診: Trigger③は未発火。サイズは通常の半分を目安に")
    elif kind == "event_probe":
        lines.append("🗓️ イベント前打診: 天井条件は不要。サイズは通常の半分を目安に、イベント通過後は早めに見直し")

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
