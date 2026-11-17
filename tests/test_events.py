"""イベントカレンダー（config の events）のテスト"""
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.build_dashboard import data_warnings
from src.indicators.technical import SignalDetector
from src.signals.events import (
    Event,
    calendar_staleness,
    load_events,
    upcoming_events,
)


@pytest.fixture
def config():
    config_path = Path(__file__).parent.parent / "config" / "config.yaml"
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


class TestLoadEvents:
    def test_dict_and_legacy_string(self):
        events = load_events([
            {"date": "2026-12-09", "name": "FOMC", "tier": "major"},
            "2026-12-04",
            {"date": date(2026, 12, 10), "name": "米CPI", "tier": "minor"},
        ])
        assert events == [
            Event(date(2026, 12, 4), "イベント", "major"),
            Event(date(2026, 12, 9), "FOMC", "major"),
            Event(date(2026, 12, 10), "米CPI", "minor"),
        ]

    def test_yaml_date_values(self):
        raw = yaml.safe_load("- 2026-12-09\n- {date: 2026-12-10, name: X}\n")
        assert [e.date for e in load_events(raw)] == [date(2026, 12, 9), date(2026, 12, 10)]

    def test_empty(self):
        assert load_events(None) == []

    def test_invalid_tier(self):
        with pytest.raises(ValueError, match="tier"):
            load_events([{"date": "2026-12-09", "tier": "high"}])

    def test_missing_date(self):
        with pytest.raises(ValueError, match="date"):
            load_events([{"name": "FOMC"}])


class TestUpcomingEvents:
    events = load_events([
        {"date": "2026-11-03", "name": "米中間選挙", "tier": "major"},
        {"date": "2026-11-06", "name": "米雇用統計", "tier": "minor"},
    ])

    def test_window_and_order(self):
        result = upcoming_events(self.events, date(2026, 11, 1), 1, 5)
        assert [(e.name, d) for e, d in result] == [("米中間選挙", 2), ("米雇用統計", 5)]

    def test_tier_filter(self):
        result = upcoming_events(self.events, date(2026, 11, 1), 1, 5, tiers=["minor"])
        assert [e.name for e, _ in result] == ["米雇用統計"]

    def test_min_days_excludes_today(self):
        assert upcoming_events(self.events, date(2026, 11, 3), 1, 5, tiers=["major"]) == []


class TestCalendarStaleness:
    events = load_events(["2027-12-17"])

    def test_fresh(self):
        assert calendar_staleness(self.events, date(2026, 11, 17)) is None

    def test_running_out(self):
        message = calendar_staleness(self.events, date(2027, 12, 1))
        assert "残り16日" in message

    def test_expired(self):
        assert "日前に終わっています" in calendar_staleness(self.events, date(2028, 1, 10))

    def test_empty(self):
        assert "空" in calendar_staleness([], date(2026, 11, 17))


def test_shipped_calendar_is_valid_and_fresh(config):
    events = load_events(config["events"])
    assert calendar_staleness(events, date(2026, 11, 17)) is None
    assert {e.tier for e in events} == {"major", "minor"}
    # メジャーSQは金曜日
    assert all(e.date.weekday() == 4 for e in events if "SQ" in e.name)


def test_c1_reports_event_name(config):
    df = pd.DataFrame([{"date": pd.Timestamp("2026-10-30")}])
    ok, details = SignalDetector(df, config).detect_gate_top_c(0, config["events"])
    assert ok
    assert details["c1_values"]["nearest_event_name"] == "日銀会合"
    assert details["c1_values"]["days_until_event"] == 0


class TestDashboardWarnings:
    def record(self, last, quality=None, vi_price="median4"):
        return {
            "date": "2027-12-01",
            "event_calendar_last": last,
            "data_quality": quality or {},
            "thresholds": {"gate_vi": {"vi_price": vi_price}},
        }

    def test_stale_calendar(self):
        assert any("イベントカレンダー" in w for w in data_warnings(self.record("2027-12-17")))

    def test_fresh_calendar(self):
        assert data_warnings(self.record("2028-06-30")) == []

    def test_missing_vi_ohlc(self):
        warnings = data_warnings(self.record("2028-06-30", {"vi_ohlc_available": False}))
        assert any("終値で判定" in w for w in warnings)

    def test_old_record_without_fields(self):
        assert data_warnings({"date": "2026-09-01"}) == []
