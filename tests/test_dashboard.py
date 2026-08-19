"""ダッシュボード生成のテスト"""
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.build_dashboard import find_config_changes, render_html
from src.history.store import append_record, build_record

from tests.test_history_store import make_signal


@pytest.fixture
def config():
    config_path = Path(__file__).parent.parent / "config" / "config.yaml"
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def make_records(config, count: int, start: date = date(2026, 1, 1)):
    return [
        build_record(make_signal(start + timedelta(days=i), trigger=i % 3 == 0), config)
        for i in range(count)
    ]


class TestRenderHtml:
    def test_empty_history_renders_placeholder(self):
        """導入直後（履歴 0 件）でも例外を出さずページを返す。"""
        html = render_html([], days=180)

        assert "<html" in html
        assert "まだ判定履歴がありません" in html

    def test_single_record_renders_charts(self, config):
        """履歴が 1 件でも 4 つのグラフを描画する（点が 1 つ並ぶだけ）。"""
        html = render_html(make_records(config, 1), days=180)

        assert html.count("Plotly.newPlot") == 4

    def test_multiple_records_render_all_sections(self, config):
        html = render_html(make_records(config, 40), days=180)

        # グラフのタイトルは Plotly の JSON 内で \uXXXX にエスケープされるため、
        # グラフは個数で、素の HTML で出す部分は文字列で確認する
        assert html.count("Plotly.newPlot") == 4
        assert "直近30日の判定と指標値" in html
        assert "現在の判定条件ハッシュ" in html

    def test_days_option_limits_the_period(self, config):
        html = render_html(make_records(config, 60), days=10)

        assert "（10 営業日）" in html

    def test_missing_metrics_do_not_break_rendering(self, config):
        records = make_records(config, 5)
        for record in records:
            record["metrics"] = {}

        html = render_html(records, days=180)

        assert "Plotly.newPlot" in html


class TestFindConfigChanges:
    def test_detects_threshold_change_day(self, config):
        records = make_records(config, 6)
        for record in records[3:]:
            record["config_hash"] = "deadbeef"

        changes = find_config_changes(records)

        assert len(changes) == 1
        assert changes[0]["date"] == records[3]["date"]

    def test_no_change_returns_empty(self, config):
        assert find_config_changes(make_records(config, 5)) == []


class TestCliOutput:
    def test_writes_file(self, tmp_path, config):
        history = tmp_path / "signals.jsonl"
        for record in make_records(config, 3):
            append_record(record, history)

        out = tmp_path / "out" / "index.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_html(make_records(config, 3), days=180), encoding="utf-8")

        assert out.exists()
        assert "判定条件ダッシュボード" in out.read_text(encoding="utf-8")
