"""イベント前打診シグナルと、通知の種類分けのテスト"""
import copy
import sys
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.build_dashboard import describe_verdict, render_html
from src.history.store import build_record, compute_config_hash
from src.models.option import MarketData, Signal
from src.notifiers.slack import SlackNotifier
from src.signals.events import load_events
from src.signals.gate import GateChecker, classify_signal, format_signal_for_notification

START = date(2026, 1, 5)


@pytest.fixture
def config():
    config_path = Path(__file__).parent.parent / "config" / "config.yaml"
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def calm_market(n=200, vi=18.0):
    """VIが低く一定（Gate①成立）、株価はなだらかに上昇（Gate②不成立）の相場。"""
    data = []
    for i in range(n):
        close = 40000 + 10 * i
        data.append(
            MarketData(
                date=START + timedelta(days=i),
                open=close - 20, high=close + 30, low=close - 30, close=close,
                vi=vi, vi_open=vi, vi_high=vi, vi_low=vi,
            )
        )
    return data


def day(i):
    return START + timedelta(days=i)


def run(config, events, data=None):
    signals = GateChecker(config).check_all_gates(data or calm_market(), events=events)
    return {s.date: s for s in signals}


class TestEventProbeFiring:
    def test_fires_once_on_first_day_of_window(self, config):
        event_day = day(150)
        signals = run(config, [{"date": event_day.isoformat(), "name": "FOMC", "tier": "major"}])
        fired = [d for d, s in signals.items() if s.is_event_probe_signal]
        assert fired == [event_day - timedelta(days=5)]
        info = signals[fired[0]].details["event_probe"]
        assert info["event_name"] == "FOMC" and info["days_until_event"] == 5
        # Gate②は不成立（天井条件なしで発火している）
        assert not signals[fired[0]].gate_top

    def test_not_on_event_day_itself(self, config):
        event_day = day(150)
        signals = run(config, [{"date": event_day.isoformat(), "name": "FOMC", "tier": "major"}])
        assert not signals[event_day].is_event_probe_signal

    def test_each_event_fires_separately(self, config):
        events = [
            {"date": day(150).isoformat(), "name": "FOMC", "tier": "major"},
            {"date": day(180).isoformat(), "name": "日銀会合", "tier": "major"},
        ]
        fired = [d for d, s in run(config, events).items() if s.is_event_probe_signal]
        assert fired == [day(145), day(175)]

    def test_events_in_same_window_fire_once(self, config):
        events = [
            {"date": day(150).isoformat(), "name": "FOMC", "tier": "major"},
            {"date": day(152).isoformat(), "name": "日銀会合", "tier": "major"},
        ]
        signals = run(config, events)
        fired = [d for d, s in signals.items() if s.is_event_probe_signal]
        assert fired == [day(145)]
        info = signals[day(145)].details["event_probe"]
        assert info["events_in_window"] == [f"FOMC {day(150).isoformat()}"]
        # 2日後の日銀会合も同じ打診でカバーする
        assert info["covers"] == [f"FOMC {day(150).isoformat()}", f"日銀会合 {day(152).isoformat()}"]

    def test_minor_events_are_ignored(self, config):
        signals = run(config, [{"date": day(150).isoformat(), "name": "米CPI", "tier": "minor"}])
        assert not any(s.is_event_probe_signal for s in signals.values())

    def test_requires_gate_vi(self, config):
        # VIが高く（1年で最高水準）CVも大きい → Gate①不成立
        data = calm_market()
        for i, d in enumerate(data):
            v = 20.0 + i * 0.3 + (3.0 if i % 2 else 0.0)
            d.vi = d.vi_open = d.vi_high = d.vi_low = v
        signals = run(config, [{"date": day(150).isoformat(), "name": "FOMC", "tier": "major"}], data)
        assert not signals[day(145)].gate_vi
        assert not any(s.is_event_probe_signal for s in signals.values())

    def test_missing_vi_does_not_fire(self, config):
        data = calm_market()
        for d in data:
            d.vi = d.vi_open = d.vi_high = d.vi_low = None
        signals = run(config, [{"date": day(150).isoformat(), "name": "FOMC", "tier": "major"}], data)
        assert signals[day(145)].gate_vi  # VI欠損時は Gate① をスキップ扱い（True）
        assert not any(s.is_event_probe_signal for s in signals.values())

    def test_disabled(self, config):
        config = copy.deepcopy(config)
        config["event_probe"]["enabled"] = False
        signals = run(config, [{"date": day(150).isoformat(), "name": "FOMC", "tier": "major"}])
        assert not any("event_probe" in s.details for s in signals.values())

    def test_lookahead_days(self, config):
        config = copy.deepcopy(config)
        config["event_probe"]["lookahead_days"] = 2
        signals = run(config, [{"date": day(150).isoformat(), "name": "FOMC", "tier": "major"}])
        assert [d for d, s in signals.items() if s.is_event_probe_signal] == [day(148)]


def make_signal(probe=False, **details):
    """Gate①成立の Signal。probe=True なら Gate②も成立（打診）にする。"""
    base = {"gate_vi": {"vi": 18.0}, "gate_top_b": {}, "technical_values": {"close": 40000.0}}
    base.update(details)
    return Signal(
        date=day(145), gate_vi=True, gate_top_a=probe, gate_top_b=probe,
        gate_top_c=False, trigger=False, details=base,
    )


class TestApplyEventProbe:
    events = load_events([{"date": day(150).isoformat(), "name": "FOMC", "tier": "major"}])

    def apply(self, signal, covered, in_cooldown=False):
        GateChecker._apply_event_probe(
            signal, self.events, 5, ["major"], covered, in_cooldown, 2 if in_cooldown else None
        )

    def test_cooldown_suppresses_without_covering(self):
        covered = set()
        signal = make_signal()
        self.apply(signal, covered, in_cooldown=True)
        assert signal.details["event_probe"]["cooldown_suppressed"] is True
        assert not signal.is_event_probe_signal
        assert covered == set()  # 抑制明けの日にはまだ発火できる

    def test_probe_takes_priority_and_covers_event(self):
        covered = set()
        signal = make_signal(probe=True)
        self.apply(signal, covered)
        assert signal.is_probe_signal
        assert not signal.is_event_probe_signal
        assert classify_signal(signal) == "probe"
        assert covered == set(self.events)


class TestClassifySignal:
    def signal(self, **flags):
        values = dict(gate_vi=True, gate_top_a=True, gate_top_b=True, gate_top_c=False, trigger=True)
        values.update(flags)
        return Signal(date=day(0), details={"gate_top_b": {}}, **values)

    def test_entry(self):
        assert classify_signal(self.signal()) == "entry"

    def test_cooldown_suppressed_entry_is_not_notified(self):
        signal = self.signal()
        signal.details["cooldown_suppressed"] = True
        assert classify_signal(signal) is None

    def test_supply(self):
        signal = self.signal(gate_top_a=False)
        signal.details["gate_top_b"] = {"b1_volume_failure": True, "b2_upper_wick_dominance": True}
        assert classify_signal(signal) == "supply"

    def test_probe(self):
        assert classify_signal(self.signal(trigger=False)) == "probe"

    def test_event_probe(self):
        signal = make_signal(event_probe={"fires": True})
        assert classify_signal(signal) == "event_probe"

    def test_nothing(self):
        assert classify_signal(self.signal(gate_vi=False)) is None


class TestNotificationAndRecords:
    def event_signal(self):
        return make_signal(
            event_probe={
                "fires": True, "event_name": "FOMC", "event_date": "2026-06-04",
                "days_until_event": 5,
            }
        )

    def test_notification_text(self):
        text = format_signal_for_notification(self.event_signal())
        assert "**イベント前打診**" in text
        assert "FOMC 2026-06-04 まで5日" in text
        assert "サイズは通常の半分" in text

    def test_history_record(self, config):
        record = build_record(self.event_signal(), config, run_date=day(145))
        assert record["flags"]["is_event_probe_signal"] is True
        assert record["event_probe"]["event_name"] == "FOMC"

    def test_config_hash_tracks_event_probe(self, config):
        changed = copy.deepcopy(config)
        changed["event_probe"]["lookahead_days"] = 3
        assert compute_config_hash(changed) != compute_config_hash(config)

    def test_dashboard(self, config):
        record = build_record(self.event_signal(), config, run_date=day(145))
        label, status, note = describe_verdict(record)
        assert label == "イベント前打診" and status == "warning"
        assert "FOMC" in note
        html = render_html([record], days=90)
        assert "イベント前打診" in html

    @pytest.mark.parametrize(
        "kind, header",
        [("entry", "🔴 エントリーシグナル検出"), ("probe", "🟡 打診シグナル検出"),
         ("event_probe", "🗓️ イベント前打診")],
    )
    def test_slack_header(self, kind, header):
        notifier = SlackNotifier(webhook_url="https://example.invalid/hook")
        with mock.patch.object(notifier, "send_message", return_value=True) as send:
            notifier.send_signal(kind, "本文", [{"strike": 50000, "premium": None, "dte": 20}])
        blocks = send.call_args.kwargs["blocks"]
        assert blocks[0]["text"]["text"] == header
        assert "Premium: N/A円" in blocks[-1]["text"]["text"]
