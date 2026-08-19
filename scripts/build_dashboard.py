"""判定履歴（history/signals.jsonl）から HTML ダッシュボードを生成する。

毎日バッチの判定結果は Slack と GHA ログにしか出ておらず、Gate/Trigger の
成立状況や VI・RSI といった判定根拠が「どう推移しているか」を追えなかった。
このスクリプトは蓄積された履歴を 1 枚の HTML にまとめ、GitHub Pages で
公開できるようにする（ローカルではそのままブラウザで開ける）。

使い方:
    python scripts/build_dashboard.py
    python scripts/build_dashboard.py --history /tmp/dummy.jsonl --out /tmp/index.html --days 90
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from src.history.store import DEFAULT_HISTORY_PATH, load_records

DEFAULT_OUTPUT_PATH = project_root / "docs" / "index.html"

# ヒートマップの行（上から順に表示）。key は record 内の参照先。
CONDITION_ROWS = [
    ("flags", "is_entry_signal", "▶ エントリー成立"),
    ("flags", "is_probe_signal", "▶ 打診シグナル"),
    ("flags", "is_supply_dominant_entry", "▶ 需給主導"),
    ("flags", "gate_vi", "Gate① VI安定"),
    ("flags", "gate_top_a", "Gate②A テクニカル"),
    ("sub", "a1", "A1 RSI反転"),
    ("sub", "a2", "A2 MACD弱化"),
    ("sub", "a3", "A3 ダイバージェンス"),
    ("sub", "a4", "A4 伸び切り"),
    ("flags", "gate_top_b", "Gate②B 需給"),
    ("sub", "b1", "B1 出来高失速"),
    ("sub", "b2", "B2 上ヒゲ優勢"),
    ("sub", "b3", "B3 寄り天"),
    ("flags", "gate_top_c", "Gate②C マクロ"),
    ("flags", "trigger", "Trigger③"),
    ("sub", "prev_low_break", "③a 前日安値割れ"),
    ("sub", "ma5_break", "③b 5日MA割れ"),
]

# 直近テーブルに出す指標
TABLE_METRICS = [
    ("close", "終値", ".0f"),
    ("rsi", "RSI", ".1f"),
    ("macd_hist", "MACDヒスト", ".1f"),
    ("vi", "VI", ".2f"),
    ("vi_ma_10", "VI MA10", ".2f"),
    ("vi_std_10", "VI STD10", ".2f"),
    ("vi_slope_10", "VI Slope10", ".3f"),
    ("volume_ratio", "出来高比", ".2f"),
    ("upper_wick_ratio", "上ヒゲ比", ".2f"),
]

PLOT_CONFIG = {"displayModeBar": False, "responsive": True}
PLOT_LAYOUT = dict(
    template="plotly_white",
    margin=dict(l=60, r=30, t=50, b=40),
    hovermode="x unified",
    font=dict(family="system-ui, -apple-system, 'Hiragino Sans', 'Noto Sans JP', sans-serif"),
)


def _metric(record: Dict[str, Any], key: str) -> Optional[float]:
    """レコードから指標値を取り出す（欠損は None）。"""
    value = record.get("metrics", {}).get(key)
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _series(records: List[Dict[str, Any]], key: str) -> List[Optional[float]]:
    return [_metric(r, key) for r in records]


def _threshold(records: List[Dict[str, Any]], *path: str) -> Optional[float]:
    """最新レコードの thresholds から閾値を取り出す。"""
    node: Any = records[-1].get("thresholds", {}) if records else {}
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node if isinstance(node, (int, float)) else None


def find_config_changes(records: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """判定条件（config_hash）が変わった日を返す。"""
    changes = []
    for prev, cur in zip(records, records[1:]):
        if prev.get("config_hash") != cur.get("config_hash"):
            changes.append({"date": cur["date"], "config_hash": cur.get("config_hash", "")})
    return changes


def _add_config_change_lines(
    fig: go.Figure, changes: List[Dict[str, str]], annotation_position: str = "top left"
) -> None:
    """判定条件が変わった日に縦線を引く。

    サブプロットを持つ図では注釈がサブプロットタイトルと重なるため、
    呼び出し側で annotation_position を下側に寄せる。
    """
    for change in changes:
        fig.add_vline(
            x=change["date"],
            line_width=1,
            line_dash="dot",
            line_color="#d62728",
            annotation_text=f"条件変更 {change['config_hash']}",
            annotation_position=annotation_position,
            annotation_font_size=10,
        )


def build_condition_heatmap(records: List[Dict[str, Any]]) -> go.Figure:
    """判定条件の成立状況を日付×条件のヒートマップにする（本ダッシュボードの主役）。"""
    dates = [r["date"] for r in records]

    z, labels, text = [], [], []
    for section, key, label in CONDITION_ROWS:
        row = [1 if record.get(section, {}).get(key) else 0 for record in records]
        z.append(row)
        labels.append(label)
        text.append(["成立" if v else "不成立" for v in row])

    # y 軸は下から積まれるので、定義順に上から並ぶよう反転する
    fig = go.Figure(
        go.Heatmap(
            z=z[::-1],
            x=dates,
            y=labels[::-1],
            text=text[::-1],
            hovertemplate="%{x}<br>%{y}: %{text}<extra></extra>",
            colorscale=[[0, "#eef1f5"], [1, "#1f77b4"]],
            showscale=False,
            xgap=1,
            ygap=1,
        )
    )
    fig.update_layout(
        title="判定条件の成立状況",
        height=max(420, 26 * len(CONDITION_ROWS) + 120),
        **PLOT_LAYOUT,
    )
    return fig


def build_vi_chart(records: List[Dict[str, Any]]) -> go.Figure:
    """Gate① VI安定条件の 4 指標と、それぞれの閾値。"""
    dates = [r["date"] for r in records]

    fig = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.06,
        subplot_titles=("VI / VI MA10", "VI STD10（ばらつき）", "VI Slope10（傾き）"),
    )

    fig.add_trace(go.Scatter(x=dates, y=_series(records, "vi"), name="VI", line=dict(color="#1f77b4")), row=1, col=1)
    fig.add_trace(
        go.Scatter(x=dates, y=_series(records, "vi_ma_10"), name="VI MA10", line=dict(color="#ff7f0e")),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(x=dates, y=_series(records, "vi_std_10"), name="VI STD10", line=dict(color="#2ca02c")),
        row=2,
        col=1,
    )
    fig.add_trace(
        go.Scatter(x=dates, y=_series(records, "vi_slope_10"), name="VI Slope10", line=dict(color="#9467bd")),
        row=3,
        col=1,
    )

    for row, keys in ((1, ("gate_vi", "vi_threshold")), (2, ("gate_vi", "vi_10d_std_threshold")), (3, ("gate_vi", "vi_10d_slope_threshold"))):
        threshold = _threshold(records, *keys)
        if threshold is not None:
            fig.add_hline(
                y=threshold,
                row=row,
                col=1,
                line_dash="dash",
                line_color="#d62728",
                annotation_text=f"閾値 {threshold}",
                annotation_font_size=10,
            )

    fig.update_layout(title="Gate① VI安定条件の推移", height=720, **PLOT_LAYOUT)
    return fig


def build_technical_chart(records: List[Dict[str, Any]]) -> go.Figure:
    """Gate②A の根拠となる RSI と MACD ヒストグラム。"""
    dates = [r["date"] for r in records]

    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        subplot_titles=("RSI(14)", "MACD ヒストグラム"),
    )

    fig.add_trace(go.Scatter(x=dates, y=_series(records, "rsi"), name="RSI", line=dict(color="#1f77b4")), row=1, col=1)

    overbought = _threshold(records, "gate_top", "technical", "rsi", "overbought")
    if overbought is not None:
        fig.add_hline(
            y=overbought,
            row=1,
            col=1,
            line_dash="dash",
            line_color="#d62728",
            annotation_text=f"過熱 {overbought}",
            annotation_font_size=10,
        )

    hist = _series(records, "macd_hist")
    fig.add_trace(
        go.Bar(
            x=dates,
            y=hist,
            name="MACD Hist",
            marker_color=["#2ca02c" if (v or 0) >= 0 else "#d62728" for v in hist],
        ),
        row=2,
        col=1,
    )

    fig.update_layout(title="Gate②A テクニカル指標の推移", height=560, showlegend=False, **PLOT_LAYOUT)
    return fig


def build_price_chart(records: List[Dict[str, Any]]) -> go.Figure:
    """日経平均とシグナル発生日。"""
    dates = [r["date"] for r in records]

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=dates, y=_series(records, "close"), name="終値", line=dict(color="#333333")))
    fig.add_trace(go.Scatter(x=dates, y=_series(records, "ma_5"), name="MA5", line=dict(color="#ff7f0e", width=1)))
    fig.add_trace(
        go.Scatter(x=dates, y=_series(records, "bb_upper"), name="BB上限", line=dict(color="#aec7e8", dash="dot"))
    )

    for flag, label, color, symbol in (
        ("is_entry_signal", "エントリー", "#d62728", "triangle-down"),
        ("is_probe_signal", "打診", "#ff9896", "circle"),
    ):
        marked = [r for r in records if r.get("flags", {}).get(flag)]
        if not marked:
            continue
        fig.add_trace(
            go.Scatter(
                x=[r["date"] for r in marked],
                y=[_metric(r, "close") for r in marked],
                mode="markers",
                name=label,
                marker=dict(color=color, size=12, symbol=symbol),
            )
        )

    fig.update_layout(title="日経平均とシグナル", height=460, **PLOT_LAYOUT)
    return fig


def build_recent_table(records: List[Dict[str, Any]], rows: int = 30) -> str:
    """直近 N 日の生値テーブル（HTML）。"""
    recent = records[-rows:][::-1]

    headers = ["日付", "判定"] + [label for _, label, _ in TABLE_METRICS]
    head = "".join(f"<th>{h}</th>" for h in headers)

    body = []
    for record in recent:
        flags = record.get("flags", {})
        if flags.get("is_entry_signal"):
            verdict = '<span class="badge badge-entry">エントリー</span>'
        elif flags.get("is_probe_signal"):
            verdict = '<span class="badge badge-probe">打診</span>'
        elif flags.get("is_supply_dominant_entry"):
            verdict = '<span class="badge badge-supply">需給主導</span>'
        else:
            gates = "".join(
                "○" if flags.get(k) else "×" for k in ("gate_vi", "gate_top_a", "gate_top_b", "trigger")
            )
            verdict = f'<span class="gates">①{gates[0]} A{gates[1]} B{gates[2]} ③{gates[3]}</span>'

        cells = [f"<td>{record['date']}</td>", f"<td>{verdict}</td>"]
        for key, _, spec in TABLE_METRICS:
            value = _metric(record, key)
            cells.append(f"<td>{format(value, spec) if value is not None else '—'}</td>")
        body.append(f"<tr>{''.join(cells)}</tr>")

    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def render_html(records: List[Dict[str, Any]], days: int) -> str:
    """ダッシュボード HTML を組み立てる。"""
    records = records[-days:] if days > 0 else records
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    if not records:
        body = (
            '<p class="empty">まだ判定履歴がありません。'
            "毎日バッチ（Daily Entry Check）が実行されると、ここに推移が表示されます。</p>"
        )
        summary = ""
    else:
        changes = find_config_changes(records)
        # (図, 注釈位置) — サブプロット図はタイトルとの衝突を避けて下寄せ
        figures_with_pos = [
            (build_condition_heatmap(records), "top left"),
            (build_vi_chart(records), "bottom right"),
            (build_technical_chart(records), "bottom right"),
            (build_price_chart(records), "top left"),
        ]
        for fig, position in figures_with_pos:
            _add_config_change_lines(fig, changes, position)
        figures = [fig for fig, _ in figures_with_pos]

        # 1件しかない日でもグラフ自体は描画される（点が1つ並ぶだけ）
        charts = "".join(
            f'<section class="card">{fig.to_html(include_plotlyjs=False, full_html=False, config=PLOT_CONFIG)}</section>'
            for fig in figures
        )

        change_note = (
            "<li>判定条件の変更: "
            + "、".join(f"{c['date']}（{c['config_hash']}）" for c in changes)
            + "</li>"
            if changes
            else "<li>この期間に判定条件の変更はありません</li>"
        )
        entry_days = sum(1 for r in records if r.get("flags", {}).get("is_entry_signal"))
        probe_days = sum(1 for r in records if r.get("flags", {}).get("is_probe_signal"))

        summary = f"""
        <section class="card summary">
          <ul>
            <li>期間: {records[0]['date']} 〜 {records[-1]['date']}（{len(records)} 営業日）</li>
            <li>エントリー成立: {entry_days} 日 / 打診シグナル: {probe_days} 日</li>
            <li>現在の判定条件ハッシュ: <code>{records[-1].get('config_hash', '—')}</code></li>
            {change_note}
          </ul>
        </section>"""

        body = charts + f'<section class="card"><h2>直近30日の判定と指標値</h2>{build_recent_table(records)}</section>'

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>option-chance 判定条件ダッシュボード</title>
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js" charset="utf-8"></script>
<style>
  :root {{ color-scheme: light; }}
  body {{
    margin: 0; padding: 24px 16px 64px;
    background: #f5f6f8; color: #1b1f24;
    font-family: system-ui, -apple-system, "Hiragino Sans", "Noto Sans JP", sans-serif;
  }}
  .wrap {{ max-width: 1100px; margin: 0 auto; }}
  h1 {{ font-size: 1.4rem; margin: 0 0 4px; }}
  .meta {{ color: #656d76; font-size: .85rem; margin: 0 0 20px; }}
  .card {{
    background: #fff; border: 1px solid #e2e5e9; border-radius: 10px;
    padding: 16px; margin-bottom: 20px; overflow-x: auto;
  }}
  .card h2 {{ font-size: 1.05rem; margin: 0 0 12px; }}
  .summary ul {{ margin: 0; padding-left: 20px; line-height: 1.9; font-size: .9rem; }}
  table {{ border-collapse: collapse; width: 100%; font-size: .85rem; white-space: nowrap; }}
  th, td {{ border-bottom: 1px solid #eceff2; padding: 6px 10px; text-align: right; }}
  th:first-child, td:first-child, th:nth-child(2), td:nth-child(2) {{ text-align: left; }}
  thead th {{ background: #fafbfc; position: sticky; top: 0; }}
  .badge {{ padding: 2px 8px; border-radius: 999px; font-size: .75rem; color: #fff; }}
  .badge-entry {{ background: #d62728; }}
  .badge-probe {{ background: #ff9896; }}
  .badge-supply {{ background: #ff7f0e; }}
  .gates {{ color: #656d76; font-size: .8rem; letter-spacing: .04em; }}
  .empty {{ background: #fff; border: 1px solid #e2e5e9; border-radius: 10px; padding: 32px; text-align: center; color: #656d76; }}
  code {{ background: #f0f2f4; padding: 1px 6px; border-radius: 4px; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>判定条件ダッシュボード</h1>
  <p class="meta">日経225プット買い戦略 / 毎日バッチの判定結果と根拠指標の推移　—　生成: {generated_at}</p>
  {summary}
  {body}
</div>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(description="判定履歴からHTMLダッシュボードを生成")
    parser.add_argument(
        "--history", type=str, default=str(DEFAULT_HISTORY_PATH), help="判定履歴（JSONL）のパス"
    )
    parser.add_argument("--out", type=str, default=str(DEFAULT_OUTPUT_PATH), help="出力HTMLのパス")
    parser.add_argument(
        "--days", type=int, default=180, help="表示する直近日数（0で全期間、デフォルト: 180）"
    )

    args = parser.parse_args()

    records = load_records(args.history)
    print(f"履歴読み込み: {args.history} ({len(records)} 件)")

    html = render_html(records, args.days)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")

    print(f"✅ ダッシュボードを生成: {out_path}")


if __name__ == "__main__":
    main()
