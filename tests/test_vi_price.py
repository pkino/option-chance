"""Gate① VI判定値（gate_vi.vi_price）と、日経VI四本値の取得のテスト"""
import sys
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.data_sources.nikkei_vi import NikkeiVIFetcher, parse_vi_csv
from src.indicators.technical import (
    SignalDetector,
    TechnicalIndicators,
    compute_vi_gate,
)
from src.models.option import MarketData
from src.signals.gate import format_signal_for_notification, GateChecker

from tests.test_gate_vi import HYBRID_CONFIG

SAMPLE_CSV = (
    "データ日付,終値,始値,高値,安値\n"
    '"2026/11/02","35.72","35.88","36.05","34.96"\n'
    '"2026/11/04","24.98","36.20","40.30","24.98"\n'
    '"2026/11/05","33.56","34.15","36.56","33.56"\n'
    '"本資料は日経の著作物であり、無断で複写することができません。"\n'
)


class TestParseViCsv:
    def test_reads_ohlc_by_column_name(self):
        bars = parse_vi_csv(SAMPLE_CSV, date(2026, 11, 1), date(2026, 11, 30))
        assert bars[date(2026, 11, 4)] == {
            "close": 24.98, "open": 36.20, "high": 40.30, "low": 24.98,
        }
        assert len(bars) == 3  # 注記行は捨てる

    def test_filters_by_date_range(self):
        bars = parse_vi_csv(SAMPLE_CSV, date(2026, 11, 3), date(2026, 11, 4))
        assert list(bars) == [date(2026, 11, 4)]

    def test_falls_back_to_column_position(self):
        text = SAMPLE_CSV.replace("データ日付,終値,始値,高値,安値", "d,c,o,h,l")
        bars = parse_vi_csv(text, date(2026, 11, 1), date(2026, 11, 30))
        assert bars[date(2026, 11, 4)]["open"] == 36.20
        assert bars[date(2026, 11, 4)]["close"] == 24.98

    def test_close_only_csv(self):
        text = "日付,終値\n2026/11/04,24.98\n"
        bars = parse_vi_csv(text, date(2026, 11, 1), date(2026, 11, 30))
        assert bars == {date(2026, 11, 4): {"close": 24.98}}

    def test_fetcher_decodes_shift_jis(self):
        fetcher = NikkeiVIFetcher()
        response = mock.Mock(status_code=200, content=SAMPLE_CSV.encode("shift-jis"))
        response.raise_for_status = mock.Mock()
        with mock.patch.object(fetcher.session, "get", return_value=response):
            bars = fetcher.fetch_vi_bars(date(2026, 11, 1), date(2026, 11, 30))
            closes = fetcher.fetch_vi_data(date(2026, 11, 1), date(2026, 11, 30))
        assert bars[date(2026, 11, 5)]["high"] == 36.56
        assert closes == {
            date(2026, 11, 2): 35.72, date(2026, 11, 4): 24.98, date(2026, 11, 5): 33.56,
        }


class TestComputeViGate:
    def frame(self):
        return pd.DataFrame(
            {
                "vi": [24.98, 30.0, 20.0],
                "vi_open": [36.20, np.nan, np.nan],
                "vi_high": [40.30, np.nan, np.nan],
                "vi_low": [24.98, np.nan, 18.0],
            }
        )

    def test_median4(self):
        gate = compute_vi_gate(self.frame(), "median4")
        # 1行目: 四本値 24.98, 36.20, 40.30, 24.98 の中央値 = (24.98 + 36.20) / 2
        assert gate.iloc[0] == pytest.approx(30.59)
        # 2行目: 終値しかない日は終値
        assert gate.iloc[1] == 30.0
        # 3行目: 取れた値（終値と安値）だけで中央値
        assert gate.iloc[2] == pytest.approx(19.0)

    def test_close_is_unchanged(self):
        df = self.frame()
        assert compute_vi_gate(df, "close").tolist() == df["vi"].tolist()

    def test_open_falls_back_to_close(self):
        gate = compute_vi_gate(self.frame(), "open")
        assert gate.tolist() == [36.20, 30.0, 20.0]

    def test_without_ohlc_columns(self):
        df = pd.DataFrame({"vi": [25.0, 26.0]})
        assert compute_vi_gate(df, "median4").tolist() == [25.0, 26.0]
        assert compute_vi_gate(df, "open").tolist() == [25.0, 26.0]

    def test_invalid_mode(self):
        with pytest.raises(ValueError, match="vi_price"):
            compute_vi_gate(self.frame(), "high")


def make_market_data(n=60, spiky=True):
    """終値だけが1日おきに安値へ急落する（2026年の日経VIの症状を再現した）系列。"""
    start = date(2026, 1, 1)
    data = []
    for i in range(n):
        level = 22.0
        close = 15.0 if (spiky and i % 2 == 0) else level
        data.append(
            MarketData(
                date=start + timedelta(days=i),
                open=40000 + i, high=40100 + i, low=39900 + i, close=40050 + i,
                vi=close, vi_open=level, vi_high=level + 0.5, vi_low=close,
            )
        )
    return data


class TestIndicatorsUseViGate:
    def test_median4_removes_close_noise_from_cv(self):
        data = make_market_data()
        close_df = TechnicalIndicators.calculate_all(data, vi_price="close")
        median_df = TechnicalIndicators.calculate_all(data, vi_price="median4")

        # 終値ベースでは急落ノイズでCVが大きい
        assert close_df["vi_cv_10"].iloc[-1] > 0.10
        # 中央値ベースではノイズが消える（終値=安値の日も中央値は始値と終値の間に収まる）
        assert median_df["vi_cv_10"].iloc[-1] < close_df["vi_cv_10"].iloc[-1] / 2
        # 生の終値は残る
        assert median_df["vi"].tolist() == close_df["vi"].tolist()

    def test_default_is_close(self):
        data = make_market_data()
        default_df = TechnicalIndicators.calculate_all(data)
        assert default_df["vi_gate"].tolist() == default_df["vi"].tolist()


class TestDetectGateViUsesGateValue:
    def test_level_uses_vi_gate(self):
        config = {"gate_vi": {**HYBRID_CONFIG["gate_vi"], "vi_price": "median4"}}
        row = {
            "vi": 15.0,       # 終値は低いが…
            "vi_gate": 31.0,  # 判定値は高い
            "vi_ma_10": 30.0, "vi_std_10": 1.0, "vi_slope_10": 0.0,
            "vi_cv_10": 0.05, "vi_pct_1y": 70.0,
        }
        satisfied, values = SignalDetector(pd.DataFrame([row]), config).detect_gate_vi(0)
        assert not satisfied
        assert values["vi"] == 31.0
        assert values["vi_close"] == 15.0
        assert values["vi_price"] == "median4"


def test_notification_shows_gate_value_and_close():
    config_path = Path(__file__).parent.parent / "config" / "config.yaml"
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    config["gate_vi"]["vi_price"] = "median4"
    signals = GateChecker(config).check_all_gates(make_market_data(300), events=[])
    text = format_signal_for_notification(signals[-1])
    assert "VI判定値=" in text
    assert "四本値中央値／終値" in text
    tech = signals[-1].details["technical_values"]
    assert tech["vi_gate"] is not None and tech["vi_open"] == 22.0


def test_shipped_config_uses_median4():
    config_path = Path(__file__).parent.parent / "config" / "config.yaml"
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    assert config["gate_vi"]["vi_price"] == "median4"
