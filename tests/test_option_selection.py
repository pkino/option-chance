"""オプション銘柄の選定と、時間損切りの営業日計算のテスト"""
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.backtest.engine import _business_days_after
from src.models.option import MarketData, OptionData
from src.signals.selection import select_best, select_candidates, selection_mode


BASE_CONFIG = {
    "selection_mode": "premium",
    "premium_range": {"min": 20, "max": 40},
    "target_premium": 30,
    "dte_range": {"min": 18, "max": 28},
    "delta_range": {"min": -0.40, "max": -0.25},
    "target_delta": -0.30,
}


def make_option(strike, premium, delta, dte_calendar=32, option_type="Put"):
    """dte_business_days = dte * 5/7 なので、暦日32日 ≒ 22営業日で DTE 条件を満たす。"""
    today = date(2026, 9, 1)
    return OptionData(
        date=today,
        underlying_price=65000.0,
        expiry=today + timedelta(days=dte_calendar),
        strike=strike,
        option_type=option_type,
        settlement=premium,
        delta=delta,
    )


# 20〜40円のプットはデルタ -0.01〜-0.02、デルタ -0.30 のプットは600〜1000円。
# 現実の板を模して、両者が同時に存在する候補群を作る。
DEEP_OTM = [
    make_option(55000, 23.0, -0.013),
    make_option(56000, 36.0, -0.020),
]
NEAR_ATM = [
    make_option(62000, 452.0, -0.204),
    make_option(63000, 665.0, -0.282),
    make_option(64000, 960.0, -0.378),
]
ALL_PUTS = DEEP_OTM + NEAR_ATM


class TestPremiumMode:
    def test_selects_deep_otm_by_premium(self):
        result = select_candidates(ALL_PUTS, BASE_CONFIG)
        assert [o.strike for o in result] == [56000, 55000]

    def test_ranks_by_distance_from_target_premium(self):
        """target_premium=30 に最も近い36円が、23円より先に来る。"""
        assert select_best(ALL_PUTS, BASE_CONFIG).premium == 36.0

    def test_delta_range_is_not_applied(self):
        """デルタが付いていても premium モードでは除外されない（旧実装はここで全滅していた）。"""
        assert all(o.delta is not None for o in DEEP_OTM)
        assert len(select_candidates(DEEP_OTM, BASE_CONFIG)) == 2

    def test_missing_delta_is_fine(self):
        no_delta = [make_option(56000, 36.0, None)]
        assert select_best(no_delta, BASE_CONFIG).strike == 56000


class TestDeltaMode:
    @pytest.fixture
    def config(self):
        return {**BASE_CONFIG, "selection_mode": "delta"}

    def test_selects_near_atm_by_delta(self, config):
        # 62000 はデルタ -0.204 で delta_range(-0.40〜-0.25) の外なので除外される
        result = select_candidates(ALL_PUTS, config)
        assert [o.strike for o in result] == [63000, 64000]

    def test_ranks_by_distance_from_target_delta(self, config):
        assert select_best(ALL_PUTS, config).delta == -0.282

    def test_premium_range_is_not_applied(self, config):
        """600〜1000円（premium_range の外）でも delta モードなら候補に残る。"""
        in_delta_range = [o for o in NEAR_ATM if o.strike in (63000, 64000)]
        assert all(o.premium > 40 for o in in_delta_range)
        assert len(select_candidates(in_delta_range, config)) == 2

    def test_options_without_delta_are_excluded(self, config):
        assert select_candidates([make_option(63000, 665.0, None)], config) == []


class TestCommonFilters:
    def test_calls_are_excluded(self):
        call = make_option(56000, 36.0, 0.02, option_type="Call")
        assert select_candidates([call], BASE_CONFIG) == []

    @pytest.mark.parametrize("dte_calendar", [14, 60])
    def test_dte_range_is_applied_in_both_modes(self, dte_calendar):
        opt = make_option(56000, 36.0, -0.020, dte_calendar=dte_calendar)
        assert select_candidates([opt], BASE_CONFIG) == []
        assert select_candidates([opt], {**BASE_CONFIG, "selection_mode": "delta"}) == []

    def test_no_candidates_returns_none(self):
        assert select_best([], BASE_CONFIG) is None

    def test_unknown_mode_is_rejected(self):
        """設定ミスを黙って既定に落とさない。"""
        with pytest.raises(ValueError, match="selection_mode"):
            selection_mode({**BASE_CONFIG, "selection_mode": "both"})

    def test_default_mode_is_premium(self):
        config = {k: v for k, v in BASE_CONFIG.items() if k != "selection_mode"}
        assert selection_mode(config) == "premium"


class TestBusinessDaysAfter:
    @staticmethod
    def market(dates):
        return {
            d: MarketData(date=d, open=1.0, high=1.0, low=1.0, close=1.0, volume=1)
            for d in dates
        }

    def test_skips_weekend(self):
        """木曜エントリーの5営業日後は翌週木曜。暦日だと日曜になってしまう。"""
        # 2026-09-03(木) 〜 2026-09-11(金) の平日
        weekdays = [date(2026, 9, d) for d in (3, 4, 7, 8, 9, 10, 11)]
        result = _business_days_after(date(2026, 9, 3), 5, self.market(weekdays))
        assert result == date(2026, 9, 10)
        assert result != date(2026, 9, 3) + timedelta(days=5)

    def test_skips_holidays_absent_from_market_data(self):
        """市場データに無い日（祝日）は営業日として数えない。"""
        weekdays = [date(2026, 9, d) for d in (3, 4, 8, 9, 10)]  # 9/7 が休場
        assert _business_days_after(date(2026, 9, 3), 3, self.market(weekdays)) == date(2026, 9, 9)

    def test_caps_at_last_available_date(self):
        weekdays = [date(2026, 9, d) for d in (3, 4)]
        assert _business_days_after(date(2026, 9, 3), 5, self.market(weekdays)) == date(2026, 9, 4)

    def test_falls_back_to_calendar_approximation(self):
        """市場データが全く無い場合のみ暦日で近似する（5営業日 ≒ 7暦日）。"""
        assert _business_days_after(date(2026, 9, 3), 5, {}) == date(2026, 9, 10)

    def test_zero_days(self):
        assert _business_days_after(date(2026, 9, 3), 0, {}) == date(2026, 9, 3)


def test_shipped_config_has_consistent_selection():
    """配布 config の選定条件が自己矛盾していないこと。"""
    config_path = Path(__file__).parent.parent / "config" / "config.yaml"
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    opt_config = config["option_selection"]
    mode = selection_mode(opt_config)
    assert mode == "premium"

    # 目標値が自分のレンジに入っていること
    assert (
        opt_config["premium_range"]["min"]
        <= opt_config["target_premium"]
        <= opt_config["premium_range"]["max"]
    )
    assert (
        opt_config["delta_range"]["min"]
        <= opt_config["target_delta"]
        <= opt_config["delta_range"]["max"]
    )

    # 時間損切りは営業日で解釈される
    assert config["exit_rules"]["stop_loss"]["time_based_days"] > 0
