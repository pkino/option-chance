"""Gate① VI安定条件（hybrid / absolute）のテスト"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.indicators.technical import SignalDetector, TechnicalIndicators


HYBRID_CONFIG = {
    "gate_vi": {
        "mode": "hybrid",
        "vi_threshold": 25,
        "vi_10d_avg_threshold": 25,
        "vi_percentile_window": 252,
        "vi_percentile_threshold": 40,
        "vi_10d_cv_threshold": 0.10,
        "vi_10d_slope_threshold": 0.15,
        "vi_10d_std_threshold": 1.5,
    }
}

ABSOLUTE_CONFIG = {
    "gate_vi": {
        "mode": "absolute",
        "vi_threshold": 20,
        "vi_10d_avg_threshold": 20,
        "vi_10d_std_threshold": 1.5,
        "vi_10d_slope_threshold": 0.1,
    }
}


def make_detector(config, **overrides):
    """1行だけのDataFrameを持つ SignalDetector を作る。"""
    row = {
        "vi": 24.0,
        "vi_ma_10": 24.0,
        "vi_std_10": 1.2,
        "vi_slope_10": 0.0,
        "vi_cv_10": 0.05,
        "vi_pct_1y": 30.0,
    }
    row.update(overrides)
    return SignalDetector(pd.DataFrame([row]), config)


class TestHybridMode:
    def test_absolute_and_relative_both_pass(self):
        satisfied, values = make_detector(HYBRID_CONFIG).detect_gate_vi(0)
        assert satisfied
        assert values["mode"] == "hybrid"
        assert values["level_absolute"] and values["level_relative"]

    def test_relative_only_passes(self):
        """高VIレジーム。絶対水準25を超えていても1年順位が低ければ通す。"""
        satisfied, values = make_detector(
            HYBRID_CONFIG, vi=31.0, vi_ma_10=30.0, vi_pct_1y=25.0, vi_cv_10=0.06
        ).detect_gate_vi(0)
        assert satisfied
        assert values["level_absolute"] is False
        assert values["level_relative"] is True

    def test_absolute_only_passes(self):
        """低VIレジーム。順位が高くても絶対水準が十分低ければ通す（保険としてのOR）。"""
        satisfied, values = make_detector(
            HYBRID_CONFIG, vi=16.0, vi_ma_10=16.0, vi_pct_1y=80.0
        ).detect_gate_vi(0)
        assert satisfied
        assert values["level_absolute"] is True
        assert values["level_relative"] is False

    def test_both_level_conditions_fail(self):
        satisfied, _ = make_detector(
            HYBRID_CONFIG, vi=31.0, vi_ma_10=30.0, vi_pct_1y=70.0
        ).detect_gate_vi(0)
        assert not satisfied

    def test_cv_above_threshold_fails(self):
        satisfied, _ = make_detector(HYBRID_CONFIG, vi_cv_10=0.15).detect_gate_vi(0)
        assert not satisfied

    def test_slope_above_threshold_fails(self):
        satisfied, _ = make_detector(HYBRID_CONFIG, vi_slope_10=0.30).detect_gate_vi(0)
        assert not satisfied

    def test_std_is_not_used(self):
        """絶対SDはhybridでは判定に使わない（水準条件との重複を排除した狙い）。"""
        satisfied, _ = make_detector(HYBRID_CONFIG, vi_std_10=9.9).detect_gate_vi(0)
        assert satisfied

    def test_falls_back_to_absolute_when_percentile_missing(self):
        """履歴不足で順位が出ない期間は、絶対水準のみで判定する。"""
        passing, _ = make_detector(
            HYBRID_CONFIG, vi=18.0, vi_ma_10=18.0, vi_pct_1y=np.nan
        ).detect_gate_vi(0)
        failing, _ = make_detector(
            HYBRID_CONFIG, vi=31.0, vi_ma_10=30.0, vi_pct_1y=np.nan
        ).detect_gate_vi(0)
        assert passing
        assert not failing

    def test_missing_cv_fails(self):
        satisfied, _ = make_detector(HYBRID_CONFIG, vi_cv_10=np.nan).detect_gate_vi(0)
        assert not satisfied

    def test_missing_vi_skips_gate(self):
        """VI自体が無い日は従来どおりGate①をスキップ（Trueで続行）する。"""
        satisfied, values = make_detector(HYBRID_CONFIG, vi=np.nan).detect_gate_vi(0)
        assert satisfied
        assert values == {}


class TestAbsoluteMode:
    def test_all_conditions_pass(self):
        satisfied, values = make_detector(
            ABSOLUTE_CONFIG, vi=18.0, vi_ma_10=18.0, vi_std_10=1.0, vi_slope_10=0.05
        ).detect_gate_vi(0)
        assert satisfied
        assert values["mode"] == "absolute"

    @pytest.mark.parametrize(
        "override",
        [
            {"vi": 21.0},
            {"vi_ma_10": 21.0},
            {"vi_std_10": 2.0},
            {"vi_slope_10": 0.5},
        ],
    )
    def test_each_condition_can_fail(self, override):
        base = {"vi": 18.0, "vi_ma_10": 18.0, "vi_std_10": 1.0, "vi_slope_10": 0.05}
        base.update(override)
        satisfied, _ = make_detector(ABSOLUTE_CONFIG, **base).detect_gate_vi(0)
        assert not satisfied

    def test_cv_is_not_used(self):
        satisfied, _ = make_detector(
            ABSOLUTE_CONFIG, vi=18.0, vi_ma_10=18.0, vi_std_10=1.0, vi_slope_10=0.05,
            vi_cv_10=9.9,
        ).detect_gate_vi(0)
        assert satisfied


class TestViIndicators:
    def test_cv_and_percentile(self):
        # 20→40 を上下する系列。最後の値は系列中で最も低い水準になるようにする
        vi = list(np.linspace(20, 40, 150)) + list(np.linspace(40, 18, 150))
        df = pd.DataFrame({"vi": vi})
        df = TechnicalIndicators._add_vi_indicators(df, percentile_window=252)

        assert df["vi_cv_10"].iloc[-1] == pytest.approx(
            df["vi_std_10"].iloc[-1] / df["vi_ma_10"].iloc[-1]
        )
        # 直近252営業日の中で最も低い水準なので順位は極めて低い
        assert df["vi_pct_1y"].iloc[-1] < 5
        # 参照期間の半分に満たない期間は順位を出さない
        assert df["vi_pct_1y"].iloc[125].__class__ is np.float64
        assert np.isnan(df["vi_pct_1y"].iloc[124])
        assert not np.isnan(df["vi_pct_1y"].iloc[126])

    def test_percentile_is_level_independent_over_regimes(self):
        """高VIレジームでも「その中では低い方」を拾えることを確認する。"""
        vi = list(np.full(200, 30.0) + np.linspace(0, 10, 200)) + [28.0]
        df = TechnicalIndicators._add_vi_indicators(pd.DataFrame({"vi": vi}))
        assert df["vi_pct_1y"].iloc[-1] < 10

    def test_no_vi_column(self):
        df = TechnicalIndicators._add_vi_indicators(pd.DataFrame({"close": [1.0, 2.0]}))
        assert df["vi_cv_10"].isna().all()
        assert df["vi_pct_1y"].isna().all()


def test_shipped_config_is_hybrid_and_self_consistent():
    """実際に配布している config が hybrid 判定に必要なキーを持っていること。"""
    config_path = Path(__file__).parent.parent / "config" / "config.yaml"
    with open(config_path, encoding="utf-8") as f:
        gate_vi = yaml.safe_load(f)["gate_vi"]

    assert gate_vi["mode"] == "hybrid"
    for key in (
        "vi_threshold",
        "vi_10d_avg_threshold",
        "vi_percentile_window",
        "vi_percentile_threshold",
        "vi_10d_cv_threshold",
        "vi_10d_slope_threshold",
    ):
        assert key in gate_vi, f"{key} が config に無い"
    assert 0 < gate_vi["vi_percentile_threshold"] <= 100
