"""Gate②B 需給条件のテスト（B1 値幅による代替、B4 VI逆行）"""
import copy
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.history.store import build_record
from src.indicators.technical import SignalDetector, TechnicalIndicators
from src.models.option import Signal
from src.signals.gate import format_signal_for_notification


@pytest.fixture
def config():
    config_path = Path(__file__).parent.parent / "config" / "config.yaml"
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def flat_frame(n=25, **last):
    """B2/B3 が成立しない平坦な相場。最終行だけ上書きできる。"""
    rows = []
    for _ in range(n):
        rows.append(
            {
                "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5,
                "upper_wick_ratio": 0.25,
                "volume_ratio": np.nan, "range_ratio": 1.0,
                "distance_from_high_20_pct": -0.5,
                "close_max_20": 100.5,
                "vi": 20.0, "vi_gate": 20.0,
            }
        )
    rows[-1].update(last)
    return pd.DataFrame(rows)


def set_b1(config, **values):
    config = copy.deepcopy(config)
    config["gate_top"]["supply_demand"]["volume_failure"].update(values)
    return config


def set_b4(config, **values):
    config = copy.deepcopy(config)
    config["gate_top"]["supply_demand"]["vi_divergence"].update(values)
    return config


class TestRangeIndicators:
    def test_true_range_and_ratio(self):
        df = pd.DataFrame(
            {
                "close": [100.0] * 25,
                "high": [102.0] * 20 + [100.5] * 5,
                "low": [98.0] * 20 + [99.5] * 5,
            }
        )
        df = TechnicalIndicators._add_range_indicators(df)
        assert df["true_range"].iloc[0] == 4.0
        assert df["atr_5"].iloc[-1] == pytest.approx(1.0)
        assert df["atr_20"].iloc[-1] == pytest.approx((15 * 4 + 5 * 1) / 20)
        assert df["range_ratio"].iloc[-1] == pytest.approx(1.0 / 3.25)

    def test_gap_is_included_in_true_range(self):
        df = pd.DataFrame({"close": [100.0, 110.0], "high": [101.0, 111.0], "low": [99.0, 109.0]})
        df = TechnicalIndicators._add_range_indicators(df)
        # 前日終値 100 → 当日高値 111 の 11 が真の値幅
        assert df["true_range"].iloc[1] == 11.0


class TestB1Proxy:
    def test_auto_uses_range_when_volume_missing(self, config):
        df = flat_frame(range_ratio=0.80)
        ok, values = SignalDetector(df, config)._check_volume_failure(24)
        assert ok
        assert values["proxy_used"] == "range"
        assert values["ratio"] == pytest.approx(0.80)
        assert values["volume_available"] is False

    def test_auto_prefers_volume(self, config):
        df = flat_frame(range_ratio=0.80, volume_ratio=0.95)
        ok, values = SignalDetector(df, config)._check_volume_failure(24)
        assert not ok  # 出来高比 0.95 > 0.85
        assert values["proxy_used"] == "volume"

    def test_not_in_high_range(self, config):
        df = flat_frame(range_ratio=0.80, distance_from_high_20_pct=-3.0)
        ok, _ = SignalDetector(df, config)._check_volume_failure(24)
        assert not ok

    def test_volume_mode_without_volume_is_unavailable(self, config):
        df = flat_frame(range_ratio=0.80)
        ok, values = SignalDetector(df, set_b1(config, proxy="volume"))._check_volume_failure(24)
        assert not ok
        assert values["available"] is False

    def test_invalid_proxy(self, config):
        with pytest.raises(ValueError, match="proxy"):
            SignalDetector(flat_frame(), set_b1(config, proxy="atr"))._check_volume_failure(24)

    def test_b1_alone_satisfies_gate_b(self, config):
        satisfied, details = SignalDetector(flat_frame(range_ratio=0.80), config).detect_gate_top_b(24)
        assert satisfied
        assert details["b1_volume_failure"]


def b4_frame(vi_last=24.0, close_last=100.5):
    df = flat_frame()
    df.loc[24, "vi_gate"] = vi_last
    df.loc[24, "close"] = close_last
    return df


class TestB4ViDivergence:
    def test_fires_on_vi_rise_near_high(self, config):
        ok, values = SignalDetector(b4_frame(vi_last=24.0), config)._check_vi_divergence(24)
        assert ok
        assert values["vi_rise"] == pytest.approx(0.20)
        assert values["near_high"]

    def test_below_threshold(self, config):
        ok, _ = SignalDetector(b4_frame(vi_last=22.0), config)._check_vi_divergence(24)
        assert not ok  # +10% < +15%

    def test_not_near_high(self, config):
        ok, _ = SignalDetector(b4_frame(close_last=95.0), config)._check_vi_divergence(24)
        assert not ok

    def test_uses_vi_gate_not_close(self, config):
        df = b4_frame(vi_last=20.0)
        df.loc[24, "vi"] = 30.0  # 終値だけ跳ねても判定値が動かなければ成立しない
        ok, _ = SignalDetector(df, config)._check_vi_divergence(24)
        assert not ok

    def test_record_only_does_not_satisfy_gate_b(self, config):
        assert config["gate_top"]["supply_demand"]["vi_divergence"]["mode"] == "record_only"
        satisfied, details = SignalDetector(b4_frame(), config).detect_gate_top_b(24)
        assert details["b4_vi_divergence"] is True
        assert details["b4_counted"] is False
        assert not satisfied

    def test_active_satisfies_gate_b(self, config):
        satisfied, details = SignalDetector(b4_frame(), set_b4(config, mode="active")).detect_gate_top_b(24)
        assert details["b4_counted"] is True
        assert satisfied

    def test_off(self, config):
        ok, values = SignalDetector(b4_frame(), set_b4(config, mode="off"))._check_vi_divergence(24)
        assert not ok and values == {"mode": "off"}

    def test_invalid_mode(self, config):
        with pytest.raises(ValueError, match="vi_divergence"):
            SignalDetector(b4_frame(), set_b4(config, mode="on"))._check_vi_divergence(24)


def b_signal(**b_details):
    details = {
        "gate_top_a": {},
        "gate_top_b": b_details,
        "gate_top_c": {},
        "trigger": {},
        "technical_values": {"close": 100.0, "range_ratio": 0.8, "vi_rise": 0.2},
        "data_quality": {"volume_available": False, "vi_ohlc_available": True},
    }
    return Signal(
        date=date(2026, 10, 6), gate_vi=True, gate_top_a=False, gate_top_b=True,
        gate_top_c=False, trigger=True, details=details,
    )


class TestSupplyDominantCountsB4OnlyWhenActive:
    def test_record_only_b4_is_not_counted(self):
        signal = b_signal(b1_volume_failure=True, b4_vi_divergence=True, b4_counted=False)
        assert not signal.is_supply_dominant_entry

    def test_active_b4_is_counted(self):
        signal = b_signal(b1_volume_failure=True, b4_vi_divergence=True, b4_counted=True)
        assert signal.is_supply_dominant_entry


class TestRecordAndNotification:
    def test_history_record(self, config):
        signal = b_signal(
            b1_volume_failure=True,
            b1_values={"proxy_used": "range", "available": True, "ratio": 0.8,
                       "distance_from_high_20_pct": -0.5},
            b4_vi_divergence=True, b4_counted=False,
            b4_values={"vi": 24.0, "vi_min": 20.0, "vi_rise": 0.2},
        )
        record = build_record(signal, config, run_date=date(2026, 10, 6))
        assert record["sub"]["b4"] is True
        assert record["b1_proxy"] == "range"
        assert record["data_quality"] == {"volume_available": False, "vi_ohlc_available": True}
        assert record["metrics"]["range_ratio"] == 0.8

    def test_notification_labels(self):
        signal = b_signal(
            b1_volume_failure=True,
            b1_values={"proxy_used": "range", "available": True, "ratio": 0.8,
                       "distance_from_high_20_pct": -0.5},
            b4_vi_divergence=True, b4_counted=False,
            b4_values={"vi": 24.0, "vi_min": 20.0, "vi_rise": 0.2},
        )
        text = format_signal_for_notification(signal)
        assert "B1(高値圏の失速・値幅): ✓" in text
        assert "B4(VI逆行・記録のみ)" in text
        assert "上昇率=20%" in text

    def test_notification_unavailable_b1(self):
        signal = b_signal(b1_values={"proxy_used": "volume", "available": False})
        assert "B1(高値圏の失速): 判定不能（出来高データなし）" in format_signal_for_notification(signal)
