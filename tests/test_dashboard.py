"""ダッシュボード生成のテスト"""
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.build_dashboard import (
    FONT_STACK,
    describe_verdict,
    find_config_changes,
    render_html,
)
from src.history.store import append_record, build_record

from tests.test_history_store import make_signal


@pytest.fixture
def config():
    config_path = Path(__file__).parent.parent / "config" / "config.yaml"
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def declared_charts(html: str):
    """ページが JS に渡しているグラフ定義（CHARTS）を取り出す。"""
    match = re.search(r"const CHARTS = (\[.*?\]);", html, re.S)
    return json.loads(match.group(1)) if match else []


def assert_every_declared_chart_is_rendered(html: str) -> None:
    charts = declared_charts(html)
    assert charts, "グラフが1つも宣言されていない"
    assert html.count("Plotly.newPlot") == len(charts)


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
        """履歴が 1 件でもすべてのグラフを描画する（点が 1 つ並ぶだけ）。"""
        assert_every_declared_chart_is_rendered(render_html(make_records(config, 1), days=180))

    def test_multiple_records_render_all_sections(self, config):
        html = render_html(make_records(config, 40), days=180)

        # グラフのタイトルは Plotly の JSON 内で \uXXXX にエスケープされるため、
        # グラフは宣言との突き合わせで、素の HTML で出す部分は文字列で確認する
        assert_every_declared_chart_is_rendered(html)
        assert "直近30日の判定と指標値" in html
        assert "判定条件ハッシュ" in html
        assert "最新判定日" in html

    def test_hero_shows_latest_verdict_and_gate_chips(self, config):
        html = render_html(make_records(config, 10), days=180)

        assert "hero-status" in html
        for gate in ("Gate①", "Gate②A", "Gate②B", "Gate②C", "Trigger③"):
            assert gate in html

    def test_range_filter_matches_declared_charts(self, config):
        """期間ボタンは全グラフを同じ期間に揃えるので、宣言と描画が一致している必要がある。"""
        html = render_html(make_records(config, 40), days=180)

        assert 'class="range" data-days="30"' in html
        for meta in declared_charts(html):
            assert f'id="{meta["id"]}"' in html

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


class TestDescribeVerdict:
    def test_entry_signal_is_critical(self, config):
        record = make_records(config, 1)[0]
        record["flags"].update(is_entry_signal=True, is_strong_signal=False)

        label, status, _ = describe_verdict(record)

        assert (label, status) == ("エントリー成立", "critical")

    def test_probe_signal_is_warning(self, config):
        record = make_records(config, 1)[0]
        record["flags"].update(is_entry_signal=False, is_probe_signal=True)

        assert describe_verdict(record)[1] == "warning"

    def test_cooldown_takes_precedence(self, config):
        """抑制中はエントリー成立より先に伝える必要がある。"""
        record = make_records(config, 1)[0]
        record["flags"].update(is_entry_signal=True, cooldown_suppressed=True)

        assert describe_verdict(record)[0] == "クールダウン抑制"

    def test_no_signal_is_muted(self, config):
        record = make_records(config, 1)[0]
        record["flags"].update(
            is_entry_signal=False, is_probe_signal=False, is_supply_dominant_entry=False
        )

        assert describe_verdict(record)[:2] == ("待機中", "muted")


class TestTypography:
    """日本語フォントの指定が崩れると字形が中国語になるので、明示的に守る。"""

    def test_font_stack_leads_with_a_japanese_face(self):
        assert FONT_STACK.startswith('"Noto Sans JP"')

    def test_font_stack_has_no_generic_system_ui(self):
        # system-ui を含めると、日本語フォントを持たない環境で中国語フォントに落ちる
        assert "system-ui" not in FONT_STACK

    def test_page_loads_the_webfont(self, config):
        html = render_html(make_records(config, 5), days=180)

        assert "fonts.googleapis.com/css2?family=Noto+Sans+JP" in html
        assert 'rel="preconnect" href="https://fonts.gstatic.com"' in html

    def test_empty_page_also_loads_the_webfont(self):
        assert "fonts.googleapis.com" in render_html([], days=180)
