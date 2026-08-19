"""判定履歴ストアのテスト"""
import copy
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.history.store import (
    append_record,
    build_record,
    compute_config_hash,
    extract_thresholds,
    load_records,
)
from src.models.option import Signal


@pytest.fixture
def config():
    config_path = Path(__file__).parent.parent / "config" / "config.yaml"
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def make_signal(day: date, **overrides) -> Signal:
    """テスト用の Signal を組み立てる。"""
    defaults = dict(
        gate_vi=True,
        gate_top_a=True,
        gate_top_b=True,
        gate_top_c=False,
        trigger=True,
    )
    defaults.update(overrides)

    details = {
        "gate_top_a": {"a1_rsi_reversal": True, "a2_macd_weakening": False},
        "gate_top_b": {"b1_volume_failure": True},
        "gate_top_c": {},
        "trigger": {"prev_low_break": True, "ma5_break": False},
        "technical_values": {"close": 39000.0, "rsi": 72.5, "vi": 18.2},
    }
    return Signal(date=day, details=details, **defaults)


class TestBuildRecord:
    def test_flags_and_sub_conditions(self, config):
        record = build_record(make_signal(date(2026, 1, 5)), config, run_date=date(2026, 1, 5))

        assert record["date"] == "2026-01-05"
        assert record["run_date"] == "2026-01-05"
        assert record["flags"]["is_entry_signal"] is True
        assert record["flags"]["gate_top"] is True
        assert record["flags"]["cooldown_suppressed"] is False
        assert record["sub"]["a1"] is True
        assert record["sub"]["a2"] is False
        assert record["sub"]["b1"] is True
        assert record["sub"]["ma5_break"] is False
        assert record["metrics"]["rsi"] == 72.5

    def test_missing_sub_conditions_default_to_false(self, config):
        """条件不成立で詳細が空でも、サブ条件は False として記録される。"""
        signal = make_signal(date(2026, 1, 6), gate_top_a=False)
        signal.details["gate_top_a"] = {}

        record = build_record(signal, config)

        assert record["sub"]["a1"] is False
        assert record["sub"]["a4"] is False

    def test_cooldown_suppressed_is_recorded(self, config):
        signal = make_signal(date(2026, 1, 7))
        signal.details["cooldown_suppressed"] = True

        assert build_record(signal, config)["flags"]["cooldown_suppressed"] is True

    def test_record_is_json_serializable(self, config):
        record = build_record(make_signal(date(2026, 1, 8)), config)

        assert json.loads(json.dumps(record, default=str))["date"] == "2026-01-08"


class TestConfigHash:
    def test_changes_when_threshold_changes(self, config):
        changed = copy.deepcopy(config)
        changed["gate_vi"]["vi_threshold"] = 18

        assert compute_config_hash(changed) != compute_config_hash(config)

    def test_stable_for_unrelated_config_changes(self, config):
        """通知設定など判定に無関係な変更ではハッシュは変わらない。"""
        changed = copy.deepcopy(config)
        changed["notifications"]["slack"]["enabled"] = False
        changed["events"] = ["2030-01-01"]

        assert compute_config_hash(changed) == compute_config_hash(config)

    def test_thresholds_snapshot_contains_gate_config(self, config):
        thresholds = extract_thresholds(config)

        assert thresholds["gate_vi"]["vi_threshold"] == config["gate_vi"]["vi_threshold"]
        assert "technical" in thresholds["gate_top"]
        assert "notifications" not in thresholds


class TestAppendRecord:
    def test_appends_and_sorts_by_date(self, tmp_path, config):
        path = tmp_path / "signals.jsonl"

        for day in (date(2026, 1, 9), date(2026, 1, 7), date(2026, 1, 8)):
            append_record(build_record(make_signal(day), config), path)

        assert [r["date"] for r in load_records(path)] == [
            "2026-01-07",
            "2026-01-08",
            "2026-01-09",
        ]

    def test_same_date_is_replaced_not_duplicated(self, tmp_path, config):
        """同日にバッチが再実行されても履歴は 1 行のまま更新される。"""
        path = tmp_path / "signals.jsonl"
        day = date(2026, 1, 10)

        append_record(build_record(make_signal(day, trigger=False), config), path)
        append_record(build_record(make_signal(day, trigger=True), config), path)

        records = load_records(path)
        assert len(records) == 1
        assert records[0]["flags"]["trigger"] is True

    def test_creates_parent_directory(self, tmp_path, config):
        path = tmp_path / "nested" / "signals.jsonl"

        append_record(build_record(make_signal(date(2026, 1, 11)), config), path)

        assert path.exists()

    def test_load_missing_file_returns_empty(self, tmp_path):
        assert load_records(tmp_path / "absent.jsonl") == []

    def test_broken_line_is_skipped(self, tmp_path, config):
        path = tmp_path / "signals.jsonl"
        append_record(build_record(make_signal(date(2026, 1, 12)), config), path)
        with path.open("a", encoding="utf-8") as f:
            f.write("{壊れた行\n")

        assert len(load_records(path)) == 1
