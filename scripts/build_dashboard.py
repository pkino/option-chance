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
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from src.history.store import DEFAULT_HISTORY_PATH, load_records

DEFAULT_OUTPUT_PATH = project_root / "docs" / "index.html"

# ---------------------------------------------------------------------------
# 配色
#
# データ可視化の検証済みパレットから、この画面で使うスロットだけを取り出したもの。
# 系列色は「識別」用のカテゴリカル色、状態色は good/warning/critical 専用で
# 系列には流用しない。ライト/ダークは同じ色相を各サーフェス向けに振り直した対。
# ---------------------------------------------------------------------------
THEME = {
    "light": {
        "surface": "#fcfcfb",
        "page": "#f9f9f7",
        "ink": "#0b0b0b",
        "ink_secondary": "#52514e",
        "muted": "#898781",
        "grid": "#e1e0d9",
        "axis": "#c3c2b7",
        "series_1": "#2a78d6",
        "series_2": "#eb6834",
        "negative": "#e34948",
        "border": "rgba(11,11,11,0.10)",
        "heat_off": "#eef0ee",
    },
    "dark": {
        "surface": "#1a1a19",
        "page": "#0d0d0d",
        "ink": "#ffffff",
        "ink_secondary": "#c3c2b7",
        "muted": "#898781",
        "grid": "#2c2c2a",
        "axis": "#383835",
        "series_1": "#3987e5",
        "series_2": "#d95926",
        "negative": "#e66767",
        "border": "rgba(255,255,255,0.10)",
        "heat_off": "#262624",
    },
}

# 状態色は両モード共通（系列色には決して使わない）
STATUS = {"good": "#0ca30c", "warning": "#fab219", "critical": "#d03b3b"}

# ヒートマップの行。上段の「シグナル」は状態、下段の「条件」は素材という区別。
SIGNAL_ROWS = [
    ("flags", "is_entry_signal", "エントリー成立"),
    ("flags", "is_supply_dominant_entry", "需給主導エントリー"),
    ("flags", "is_probe_signal", "打診シグナル"),
]
CONDITION_ROWS = [
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
# 日本語フェイスを先頭に明示する。system-ui を先頭に置くと、日本語フォントを
# 持たない環境（Linux サーバや一部の Windows）で中国語フォントに落ち、
# 「骨」「直」などの字形が中国語になってしまう。
FONT_STACK = (
    '"Noto Sans JP", "Hiragino Kaku Gothic ProN", "Hiragino Sans", '
    '"Yu Gothic", "Meiryo", sans-serif'
)
GOOGLE_FONTS_URL = (
    "https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;700&display=swap"
)


def _base_layout(height: int, **overrides) -> Dict[str, Any]:
    """全グラフ共通のレイアウト。背景は透過してカード面を透かす。"""
    light = THEME["light"]
    layout = dict(
        height=height,
        margin=dict(l=52, r=56, t=32, b=32),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONT_STACK, size=11, color=light["ink_secondary"]),
        hovermode="x unified",
        hoverlabel=dict(font_family=FONT_STACK, font_size=11),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            x=0,
            font=dict(size=11),
            bgcolor="rgba(0,0,0,0)",
        ),
    )
    layout.update(overrides)
    return layout


def _style_axes(fig: go.Figure) -> go.Figure:
    """軸と目盛りをヘアラインまで後退させる（実線・1色）。"""
    light = THEME["light"]
    fig.update_xaxes(
        showgrid=False,
        showline=True,
        linewidth=1,
        linecolor=light["axis"],
        ticks="",
        tickfont=dict(size=10, color=light["muted"]),
    )
    fig.update_yaxes(
        showgrid=True,
        gridwidth=1,
        gridcolor=light["grid"],
        zeroline=False,
        showline=False,
        ticks="",
        tickfont=dict(size=10, color=light["muted"]),
    )
    return fig


def _threshold(fig: go.Figure, value: float, label: str, **kwargs) -> None:
    """閾値ラインを引く（破線は閾値専用。グリッドや軸には使わない）。"""
    fig.add_hline(
        y=value,
        line_dash="dash",
        line_width=1,
        line_color=THEME["light"]["ink_secondary"],
        annotation_text=label,
        annotation_position="top right",
        annotation_font=dict(size=10, color=THEME["light"]["ink_secondary"]),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# データ取り出し
# ---------------------------------------------------------------------------
def _metric(record: Dict[str, Any], key: str) -> Optional[float]:
    value = record.get("metrics", {}).get(key)
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _series(records: List[Dict[str, Any]], key: str) -> List[Optional[float]]:
    return [_metric(r, key) for r in records]


def _threshold_value(records: List[Dict[str, Any]], *path: str) -> Optional[float]:
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


def describe_verdict(record: Dict[str, Any]) -> Tuple[str, str, str]:
    """最新レコードの総合判定を (ラベル, 状態キー, 補足) で返す。"""
    flags = record.get("flags", {})
    if flags.get("cooldown_suppressed"):
        return "クールダウン抑制", "warning", "直近エントリーから日が浅く、新規は見送り"
    if flags.get("is_entry_signal"):
        note = "Gate①②とTrigger③がすべて成立"
        return ("強い天井示唆" if flags.get("is_strong_signal") else "エントリー成立"), "critical", note
    if flags.get("is_supply_dominant_entry"):
        return "需給主導エントリー", "critical", "需給条件2つ以上とTrigger③が成立"
    if flags.get("is_probe_signal"):
        return "打診シグナル", "warning", "Gate①②は成立、Trigger③は未発火"
    return "待機中", "muted", "エントリー条件は未成立"


# ---------------------------------------------------------------------------
# グラフ
# ---------------------------------------------------------------------------
def build_condition_heatmap(records: List[Dict[str, Any]]) -> go.Figure:
    """判定条件の成立状況（本ダッシュボードの主役）。

    上段のシグナル行は「状態」、下段の条件行は「素材」なので、同じ1つの
    トレース内で z の値を分け（0=不成立 / 1=条件成立 / 2=シグナル発火）、
    離散カラースケールで役割の違いを見せる。
    """
    dates = [r["date"] for r in records]

    z, labels, text = [], [], []
    for section, key, label in SIGNAL_ROWS + CONDITION_ROWS:
        on_value = 2 if (section, key, label) in SIGNAL_ROWS else 1
        values = [on_value if r.get(section, {}).get(key) else 0 for r in records]
        z.append(values)
        labels.append(label)
        text.append(["成立" if v else "不成立" for v in values])

    # y軸は下から積まれるため、定義順に上から並ぶよう反転する
    z, labels, text = z[::-1], labels[::-1], text[::-1]

    light = THEME["light"]
    off, on, fired = light["heat_off"], light["series_1"], STATUS["critical"]
    fig = go.Figure(
        go.Heatmap(
            z=z, x=dates, y=labels, text=text, name="conditions",
            hovertemplate="%{x}<br>%{y}: %{text}<extra></extra>",
            colorscale=[
                [0.0, off], [0.25, off],
                [0.25, on], [0.75, on],
                [0.75, fired], [1.0, fired],
            ],
            showscale=False, xgap=2, ygap=2, zmin=0, zmax=2,
        )
    )

    rows = len(SIGNAL_ROWS) + len(CONDITION_ROWS)
    fig.update_layout(**_base_layout(24 * rows + 56, hovermode="closest", showlegend=False))
    fig.update_layout(margin=dict(l=132, r=16, t=12, b=28))
    _style_axes(fig)
    # 営業日を等間隔のセルとして並べたいので、日付軸ではなくカテゴリ軸にする
    # （日付軸だと土日が穴になり、条件の並びが読みにくくなる）
    fig.update_xaxes(type="category", nticks=12)
    fig.update_yaxes(showgrid=False, tickfont=dict(size=11, color=light["ink_secondary"]))
    # シグナル行と条件行の区別は色（赤／青）が担う。区切り線は add_hline が
    # 図全体（軸ラベル側まで）に伸びてしまい、かえってノイズになるので置かない。
    return fig


def _finalize_time_axis(fig: go.Figure, dates: List[str]) -> go.Figure:
    """点が数個しかない時期でも日付軸が読めるようにする。

    plotly は1点しかない日付軸を秒以下まで拡大してしまい、
    「23:59:59.999」のような目盛りが出る。蓄積が始まったばかりの
    数日間はまさにこの状態になるので、明示的に日単位へ寄せる。
    """
    if len(dates) < 3:
        first = datetime.strptime(dates[0], "%Y-%m-%d")
        last = datetime.strptime(dates[-1], "%Y-%m-%d")
        fig.update_xaxes(
            range=[(first - timedelta(days=1)).isoformat(), (last + timedelta(days=1)).isoformat()],
            dtick=86_400_000,  # 1日（ミリ秒）
            tickformat="%m/%d",
        )
    return fig


def build_vi_level_chart(records: List[Dict[str, Any]]) -> go.Figure:
    """VI と 10日移動平均。単位が同じなので 1 つの軸に載せる。"""
    dates = [r["date"] for r in records]
    light = THEME["light"]

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(x=dates, y=_series(records, "vi"), name="VI",
                   line=dict(color=light["series_1"], width=2))
    )
    fig.add_trace(
        go.Scatter(x=dates, y=_series(records, "vi_ma_10"), name="VI MA10",
                   line=dict(color=light["series_2"], width=2))
    )

    threshold = _threshold_value(records, "gate_vi", "vi_threshold")
    if threshold is not None:
        _threshold(fig, threshold, f"閾値 {threshold}")

    fig.update_layout(**_base_layout(240))
    return _finalize_time_axis(_style_axes(fig), dates)


def build_vi_stability_chart(records: List[Dict[str, Any]]) -> go.Figure:
    """VI のばらつきと傾き。単位が違うので軸を分ける（二重軸にはしない）。"""
    dates = [r["date"] for r in records]
    light = THEME["light"]

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.14,
        subplot_titles=("ばらつき VI STD10", "傾き VI Slope10"),
    )
    fig.add_trace(
        go.Scatter(x=dates, y=_series(records, "vi_std_10"), name="VI STD10",
                   line=dict(color=light["series_1"], width=2)),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scatter(x=dates, y=_series(records, "vi_slope_10"), name="VI Slope10",
                   line=dict(color=light["series_1"], width=2)),
        row=2, col=1,
    )

    for row, keys in ((1, ("vi_10d_std_threshold",)), (2, ("vi_10d_slope_threshold",))):
        value = _threshold_value(records, "gate_vi", *keys)
        if value is not None:
            _threshold(fig, value, f"閾値 {value}", row=row, col=1)

    fig.update_layout(**_base_layout(240, showlegend=False))
    fig.update_annotations(font=dict(size=11, color=light["ink_secondary"]))
    return _finalize_time_axis(_style_axes(fig), dates)


def build_rsi_chart(records: List[Dict[str, Any]]) -> go.Figure:
    """RSI と過熱ライン（1系列なので凡例は不要、タイトルが名前を持つ）。"""
    dates = [r["date"] for r in records]
    light = THEME["light"]

    fig = go.Figure(
        go.Scatter(x=dates, y=_series(records, "rsi"), name="RSI",
                   line=dict(color=light["series_1"], width=2))
    )
    overbought = _threshold_value(records, "gate_top", "technical", "rsi", "overbought")
    if overbought is not None:
        _threshold(fig, overbought, f"過熱 {overbought}")

    fig.update_layout(**_base_layout(240, showlegend=False))
    fig.update_yaxes(range=[0, 100], dtick=25)
    return _finalize_time_axis(_style_axes(fig), dates)


def build_macd_chart(records: List[Dict[str, Any]]) -> go.Figure:
    """MACD ヒストグラム。符号で意味が反転するので発散配色（青↔赤）。"""
    dates = [r["date"] for r in records]
    light = THEME["light"]
    values = _series(records, "macd_hist")

    fig = go.Figure(
        go.Bar(
            x=dates, y=values, name="MACD Hist",
            marker=dict(
                color=[light["series_1"] if (v or 0) >= 0 else light["negative"] for v in values],
                line=dict(width=0),
            ),
            # 日付軸なので幅はミリ秒。省略すると1点のときプロット全幅の棒になる
            width=16 * 60 * 60 * 1000,
        )
    )
    fig.update_layout(**_base_layout(240, showlegend=False))
    fig.update_yaxes(zeroline=True, zerolinewidth=1, zerolinecolor=light["axis"])
    return _finalize_time_axis(_style_axes(fig), dates)


def build_price_chart(records: List[Dict[str, Any]]) -> go.Figure:
    """日経平均とシグナル発生日。BB上限は参照線なので後退させる。"""
    dates = [r["date"] for r in records]
    light = THEME["light"]

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(x=dates, y=_series(records, "bb_upper"), name="BB上限（参考）",
                   line=dict(color=light["muted"], width=1))
    )
    fig.add_trace(
        go.Scatter(x=dates, y=_series(records, "ma_5"), name="MA5",
                   line=dict(color=light["series_2"], width=2))
    )
    fig.add_trace(
        go.Scatter(x=dates, y=_series(records, "close"), name="終値",
                   line=dict(color=light["series_1"], width=2))
    )

    # シグナルは色だけでなく形でも区別する
    for flag, label, color, symbol in (
        ("is_entry_signal", "エントリー", STATUS["critical"], "triangle-down"),
        ("is_probe_signal", "打診", STATUS["warning"], "circle"),
    ):
        marked = [r for r in records if r.get("flags", {}).get(flag)]
        if not marked:
            continue
        fig.add_trace(
            go.Scatter(
                x=[r["date"] for r in marked],
                y=[_metric(r, "close") for r in marked],
                mode="markers", name=label,
                marker=dict(color=color, size=11, symbol=symbol,
                            line=dict(width=2, color=light["surface"])),
            )
        )

    fig.update_layout(**_base_layout(280))
    return _finalize_time_axis(_style_axes(fig), dates)


def _add_config_change_lines(fig: go.Figure, changes: List[Dict[str, str]], label: bool) -> None:
    """判定条件が変わった日に縦線を引く。

    注釈は主役のヒートマップだけに出す。他の図にも同じ文字を重ねると
    サブプロットのタイトルとぶつかり、ノイズにしかならない。
    """
    muted = THEME["light"]["muted"]
    for change in changes:
        annotation = (
            dict(
                annotation_text=f"条件変更 {change['config_hash']}",
                annotation_position="top left",
                annotation_font=dict(size=10, color=muted),
            )
            if label
            else {}
        )
        fig.add_vline(
            x=change["date"], line_width=1, line_dash="dot", line_color=muted, **annotation
        )


# ---------------------------------------------------------------------------
# HTML 部品
# ---------------------------------------------------------------------------
def _fmt(value: Optional[float], spec: str) -> str:
    return format(value, spec) if value is not None else "—"


def build_hero(records: List[Dict[str, Any]]) -> str:
    """最新の判定状況。グラフを読む前に「今どうなっているか」を一目で示す。"""
    latest = records[-1]
    flags = latest.get("flags", {})
    label, status, note = describe_verdict(latest)

    gates = [
        ("Gate①", "VI安定", flags.get("gate_vi")),
        ("Gate②A", "テクニカル", flags.get("gate_top_a")),
        ("Gate②B", "需給", flags.get("gate_top_b")),
        ("Gate②C", "マクロ", flags.get("gate_top_c")),
        ("Trigger③", "引き金", flags.get("trigger")),
    ]
    chips = "".join(
        f'<div class="chip chip-{"on" if ok else "off"}">'
        f'<span class="chip-mark">{"○" if ok else "×"}</span>'
        f'<span class="chip-name">{name}</span>'
        f'<span class="chip-note">{note_}</span></div>'
        for name, note_, ok in gates
    )

    vi = _metric(latest, "vi")
    vi_threshold = _threshold_value(records, "gate_vi", "vi_threshold")
    if vi is not None and vi_threshold is not None:
        vi_note = f"閾値 {vi_threshold} を{'下回る' if vi <= vi_threshold else '上回る'}"
    else:
        vi_note = "閾値との比較不可"

    rsi = _metric(latest, "rsi")
    overbought = _threshold_value(records, "gate_top", "technical", "rsi", "overbought")
    rsi_note = "—"
    if rsi is not None and overbought is not None:
        rsi_note = "過熱圏" if rsi >= overbought else f"過熱 {overbought:.0f} まで {overbought - rsi:.1f}"

    entry_days = sum(1 for r in records if r.get("flags", {}).get("is_entry_signal"))
    probe_days = sum(1 for r in records if r.get("flags", {}).get("is_probe_signal"))

    tiles = [
        ("VI", _fmt(vi, ".2f"), vi_note),
        ("RSI", _fmt(rsi, ".1f"), rsi_note),
        ("終値", _fmt(_metric(latest, "close"), ",.0f"), f"MA5 {_fmt(_metric(latest, 'ma_5'), ',.0f')}"),
        ("期間内のシグナル", f"{entry_days} / {probe_days}", "エントリー / 打診"),
    ]
    tile_html = "".join(
        f'<div class="tile"><div class="tile-label">{lbl}</div>'
        f'<div class="tile-value">{val}</div>'
        f'<div class="tile-note">{note_}</div></div>'
        for lbl, val, note_ in tiles
    )

    return f"""
    <section class="hero">
      <div class="hero-verdict">
        <div class="hero-date">最新判定日　{latest['date']}</div>
        <div class="hero-status status-{status}"><span class="dot"></span>{label}</div>
        <p class="hero-note">{note}</p>
        <div class="chips">{chips}</div>
      </div>
      <div class="tiles">{tile_html}</div>
    </section>"""


def build_recent_table(records: List[Dict[str, Any]], rows: int = 30) -> str:
    """直近 N 日の生値テーブル（グラフの値をすべて読める代替ビュー）。"""
    recent = records[-rows:][::-1]
    headers = ["日付", "判定"] + [label for _, label, _ in TABLE_METRICS]
    head = "".join(f"<th>{h}</th>" for h in headers)

    body = []
    for record in recent:
        flags = record.get("flags", {})
        label, status, _ = describe_verdict(record)
        if status == "muted":
            gates = "".join(
                "○" if flags.get(k) else "×"
                for k in ("gate_vi", "gate_top_a", "gate_top_b", "trigger")
            )
            verdict = (
                f'<span class="gates">①{gates[0]} A{gates[1]} B{gates[2]} ③{gates[3]}</span>'
            )
        else:
            verdict = f'<span class="badge badge-{status}">{label}</span>'

        cells = [f"<td>{record['date']}</td>", f"<td>{verdict}</td>"]
        for key, _, spec in TABLE_METRICS:
            cells.append(f"<td>{_fmt(_metric(record, key), spec)}</td>")
        body.append(f"<tr>{''.join(cells)}</tr>")

    return (
        f'<div class="table-scroll"><table>'
        f"<thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"
    )


def _card(title: str, hint: str, fig: go.Figure, div_id: str, wide: bool = False) -> str:
    plot = fig.to_html(
        include_plotlyjs=False, full_html=False, config=PLOT_CONFIG, div_id=div_id
    )
    return f"""
      <section class="card{' card-wide' if wide else ''}">
        <header class="card-head"><h2>{title}</h2><span class="hint">{hint}</span></header>
        {plot}
      </section>"""


def render_html(records: List[Dict[str, Any]], days: int) -> str:
    """ダッシュボード HTML を組み立てる。"""
    records = records[-days:] if days > 0 else records
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    if not records:
        content = """
    <div class="empty">
      <p class="empty-title">まだ判定履歴がありません</p>
      <p>毎日バッチ（Daily Entry Check）が実行されると、判定条件の推移がここに表示されます。</p>
    </div>"""
        chart_meta: List[Dict[str, Any]] = []
    else:
        changes = find_config_changes(records)

        # (図, ID, カテゴリ軸か) — カテゴリ軸は範囲指定がインデックス基準になる
        specs = [
            (build_condition_heatmap(records), "chart-heatmap", True,
             "判定条件の成立状況", "赤＝シグナル発火／青＝条件成立", True),
            (build_vi_level_chart(records), "chart-vi", False,
             "Gate① VI水準", "VIと10日移動平均", False),
            (build_vi_stability_chart(records), "chart-vi-stability", False,
             "Gate① VIの安定度", "ばらつきと傾き", False),
            (build_rsi_chart(records), "chart-rsi", False,
             "RSI(14)", "A1 RSI反転の根拠", False),
            (build_macd_chart(records), "chart-macd", False,
             "MACD ヒストグラム", "A2 MACD弱化の根拠", False),
            (build_price_chart(records), "chart-price", False,
             "日経平均とシグナル", "終値・MA5・BB上限と発火日", True),
        ]

        cards = []
        chart_meta = []
        for fig, div_id, categorical, title, hint, wide in specs:
            _add_config_change_lines(fig, changes, label=(div_id == "chart-heatmap"))
            cards.append(_card(title, hint, fig, div_id, wide))
            chart_meta.append({"id": div_id, "categorical": categorical})

        change_note = (
            "判定条件の変更: " + "、".join(f"{c['date']}（{c['config_hash']}）" for c in changes)
            if changes
            else "この期間に判定条件の変更はありません"
        )

        # 導入直後は点が数個しかなく、推移としては読めない。黙って薄いグラフを
        # 出すより、あと何営業日ぶんで読めるようになるかを伝える。
        notice = ""
        if len(records) < 20:
            notice = (
                f'<p class="notice">履歴は現在 <strong>{len(records)} 営業日</strong>ぶんです。'
                "毎日バッチが1日1件ずつ追記するので、推移として読めるようになるまで"
                f"あと {20 - len(records)} 営業日ほどかかります。</p>"
            )

        content = f"""
    {notice}
    <div class="grid">{''.join(cards)}</div>

    <details class="card card-wide">
      <summary><h2>直近30日の判定と指標値</h2><span class="hint">数値で確認する</span></summary>
      {build_recent_table(records)}
    </details>

    <footer class="foot">
      <span>期間 {records[0]['date']} 〜 {records[-1]['date']}（{len(records)} 営業日）</span>
      <span>判定条件ハッシュ <code>{records[-1].get('config_hash', '—')}</code></span>
      <span>{change_note}</span>
    </footer>"""

        content = build_hero(records) + content

    ranges_html = "".join(
        '<button class="range" data-days="{}"{}>{}</button>'.format(
            d, ' aria-pressed="true"' if d == 0 else "", lbl
        )
        for d, lbl in ((30, "30日"), (90, "90日"), (0, "全期間"))
    )

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>option-chance 判定条件ダッシュボード</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="{GOOGLE_FONTS_URL}">
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js" charset="utf-8"></script>
<style>
{_stylesheet()}
</style>
</head>
<body>
<div class="wrap">
  <header class="top">
    <div class="top-title">
      <h1>判定条件ダッシュボード</h1>
      <p>日経225プット買い戦略　毎日バッチの判定結果と根拠指標の推移</p>
    </div>
    <div class="top-tools">
      <div class="ranges" role="group" aria-label="表示期間">{ranges_html}</div>
      <span class="stamp">生成 {generated_at}</span>
    </div>
  </header>
{content}
</div>
<script>
const CHARTS = {json.dumps(chart_meta)};
const THEME = {json.dumps(THEME)};
{_script()}
</script>
</body>
</html>
"""


def _stylesheet() -> str:
    light, dark = THEME["light"], THEME["dark"]
    return f"""
  :root {{
    color-scheme: light;
    --surface: {light['surface']};
    --page: {light['page']};
    --ink: {light['ink']};
    --ink-2: {light['ink_secondary']};
    --muted: {light['muted']};
    --grid: {light['grid']};
    --border: {light['border']};
    --series-1: {light['series_1']};
    --good: {STATUS['good']};
    --warning: {STATUS['warning']};
    --critical: {STATUS['critical']};
    --chip-off: {light['heat_off']};
  }}
  @media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) {{
      color-scheme: dark;
      --surface: {dark['surface']};
      --page: {dark['page']};
      --ink: {dark['ink']};
      --ink-2: {dark['ink_secondary']};
      --muted: {dark['muted']};
      --grid: {dark['grid']};
      --border: {dark['border']};
      --series-1: {dark['series_1']};
      --chip-off: {dark['heat_off']};
    }}
  }}

  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    padding: 20px 16px 48px;
    background: var(--page);
    color: var(--ink);
    font-family: {FONT_STACK};
    font-size: 14px;
    line-height: 1.6;
  }}
  .wrap {{ max-width: 1320px; margin: 0 auto; }}

  /* ---- ヘッダー ---- */
  .top {{
    display: flex; flex-wrap: wrap; gap: 12px;
    align-items: flex-end; justify-content: space-between;
    margin-bottom: 16px;
  }}
  .top h1 {{ font-size: 1.3rem; margin: 0; letter-spacing: .01em; }}
  .top p {{ margin: 2px 0 0; color: var(--muted); font-size: .82rem; }}
  .top-tools {{ display: flex; align-items: center; gap: 12px; }}
  .stamp {{ color: var(--muted); font-size: .75rem; }}
  .ranges {{
    display: inline-flex; background: var(--surface);
    border: 1px solid var(--border); border-radius: 8px; padding: 2px;
  }}
  .range {{
    appearance: none; border: 0; background: transparent; cursor: pointer;
    color: var(--ink-2); font: inherit; font-size: .78rem;
    padding: 4px 12px; border-radius: 6px;
  }}
  .range:hover {{ color: var(--ink); }}
  .range[aria-pressed="true"] {{ background: var(--series-1); color: #fff; }}

  /* ---- ヒーロー ---- */
  .hero {{
    display: grid; grid-template-columns: minmax(300px, 1.1fr) 2fr; gap: 16px;
    margin-bottom: 16px;
  }}
  .hero-verdict, .tiles > .tile {{
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 12px; padding: 16px 18px;
  }}
  .hero-date {{ color: var(--muted); font-size: .78rem; letter-spacing: .04em; }}
  .hero-status {{
    display: flex; align-items: center; gap: 10px;
    font-size: 1.55rem; font-weight: 700; margin: 2px 0 4px; line-height: 1.25;
  }}
  .hero-status .dot {{ width: 11px; height: 11px; border-radius: 50%; flex: none; }}
  .status-critical {{ color: var(--critical); }}
  .status-critical .dot {{ background: var(--critical); }}
  .status-warning .dot {{ background: var(--warning); }}
  .status-muted {{ color: var(--ink-2); }}
  .status-muted .dot {{ background: var(--muted); }}
  .hero-note {{ margin: 0 0 12px; color: var(--ink-2); font-size: .82rem; }}
  .chips {{ display: flex; flex-wrap: wrap; gap: 6px; }}
  .chip {{
    display: flex; align-items: baseline; gap: 5px;
    border: 1px solid var(--border); border-radius: 999px;
    padding: 3px 10px; font-size: .75rem; background: var(--chip-off);
  }}
  .chip-mark {{ font-weight: 700; }}
  .chip-name {{ font-weight: 600; }}
  .chip-note {{ color: var(--muted); font-size: .7rem; }}
  .chip-on {{ background: color-mix(in srgb, var(--series-1) 12%, transparent);
              border-color: color-mix(in srgb, var(--series-1) 40%, transparent); }}
  .chip-on .chip-mark {{ color: var(--series-1); }}
  .chip-off .chip-mark {{ color: var(--muted); }}
  .chip-off .chip-name {{ color: var(--ink-2); font-weight: 500; }}

  .tiles {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }}
  .tiles > .tile {{ display: flex; flex-direction: column; justify-content: center; }}
  .tile-label {{ color: var(--muted); font-size: .75rem; }}
  .tile-value {{ font-size: 1.7rem; font-weight: 600; line-height: 1.25; margin: 2px 0; }}
  .tile-note {{ color: var(--ink-2); font-size: .72rem; }}

  /* ---- グラフカード ---- */
  .grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }}
  .card {{
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 12px; padding: 14px 16px 8px; min-width: 0;
  }}
  .card-wide {{ grid-column: 1 / -1; }}
  .card-head {{ display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; margin-bottom: 4px; }}
  .card h2 {{ font-size: .92rem; margin: 0; font-weight: 600; }}
  .hint {{ color: var(--muted); font-size: .74rem; }}

  /* ---- テーブル ---- */
  details.card {{ margin-top: 16px; padding: 0; }}
  details summary {{
    display: flex; align-items: baseline; gap: 10px;
    padding: 14px 16px; cursor: pointer; list-style: none;
  }}
  details summary::-webkit-details-marker {{ display: none; }}
  details summary::before {{ content: "▸"; color: var(--muted); }}
  details[open] summary::before {{ content: "▾"; }}
  .table-scroll {{ max-height: 420px; overflow: auto; border-top: 1px solid var(--border); }}
  table {{ border-collapse: collapse; width: 100%; font-size: .8rem; white-space: nowrap; }}
  th, td {{ padding: 6px 12px; text-align: right; border-bottom: 1px solid var(--grid); }}
  td {{ font-variant-numeric: tabular-nums; }}
  th:first-child, td:first-child, th:nth-child(2), td:nth-child(2) {{ text-align: left; }}
  thead th {{
    position: sticky; top: 0; background: var(--surface);
    color: var(--muted); font-weight: 600; font-size: .74rem;
  }}
  .badge {{ padding: 2px 8px; border-radius: 999px; font-size: .72rem; color: #fff; }}
  .badge-critical {{ background: var(--critical); }}
  .badge-warning {{ background: var(--warning); color: #3a2a00; }}
  .gates {{ color: var(--muted); font-size: .74rem; letter-spacing: .06em; }}

  /* ---- その他 ---- */
  .notice {{
    background: var(--surface); border: 1px solid var(--border);
    border-left: 3px solid var(--series-1); border-radius: 8px;
    padding: 10px 14px; margin: 0 0 16px; color: var(--ink-2); font-size: .82rem;
  }}
  .foot {{
    display: flex; flex-wrap: wrap; gap: 6px 20px;
    margin-top: 16px; color: var(--muted); font-size: .75rem;
  }}
  code {{ background: var(--chip-off); padding: 1px 6px; border-radius: 4px; font-size: .95em; }}
  .empty {{
    background: var(--surface); border: 1px solid var(--border); border-radius: 12px;
    padding: 56px 24px; text-align: center; color: var(--ink-2);
  }}
  .empty-title {{ font-size: 1.05rem; font-weight: 600; color: var(--ink); margin: 0 0 6px; }}

  @media (max-width: 1000px) {{
    .hero {{ grid-template-columns: 1fr; }}
    .grid {{ grid-template-columns: 1fr; }}
    .tiles {{ grid-template-columns: repeat(2, 1fr); }}
  }}
"""


def _script() -> str:
    """テーマ追従と期間フィルタ。グラフはCSS変数を読めないのでJSで色を差し替える。"""
    return """
const q = window.matchMedia('(prefers-color-scheme: dark)');
const palette = () => THEME[q.matches ? 'dark' : 'light'];

// 系列名 → テーマ別の色。凡例名でひけるようにしておく。
const SERIES_KEY = {
  'VI': 'series_1', 'VI MA10': 'series_2', 'VI STD10': 'series_1',
  'VI Slope10': 'series_1', 'RSI': 'series_1', '終値': 'series_1',
  'MA5': 'series_2', 'BB上限（参考）': 'muted',
};

function applyTheme() {
  const c = palette();
  CHARTS.forEach(({ id }) => {
    const el = document.getElementById(id);
    if (!el || !el.data) return;

    el.data.forEach((trace, i) => {
      const key = SERIES_KEY[trace.name];
      if (trace.type === 'heatmap') {
        const scale = [
          [0.0, c.heat_off], [0.25, c.heat_off],
          [0.25, c.series_1], [0.75, c.series_1],
          [0.75, '#d03b3b'], [1.0, '#d03b3b'],
        ];
        Plotly.restyle(el, { colorscale: [scale] }, [i]);
      } else if (trace.type === 'bar') {
        const colors = trace.y.map((v) => ((v || 0) >= 0 ? c.series_1 : c.negative));
        Plotly.restyle(el, { 'marker.color': [colors] }, [i]);
      } else if (key) {
        Plotly.restyle(el, { 'line.color': c[key] }, [i]);
      } else if (trace.mode === 'markers') {
        Plotly.restyle(el, { 'marker.line.color': c.surface }, [i]);
      }
    });

    const shapes = (el.layout.shapes || []).map((sh) => (
      sh.line && sh.line.dash === 'dash'
        ? { ...sh, line: { ...sh.line, color: c.ink_secondary } }
        : sh
    ));
    const annotations = (el.layout.annotations || []).map((an) => (
      an.text && an.text.startsWith('閾値') || (an.text || '').startsWith('過熱')
        ? { ...an, font: { ...an.font, color: c.ink_secondary } }
        : an
    ));

    Plotly.relayout(el, {
      shapes, annotations,
      'font.color': c.ink_secondary,
      'xaxis.linecolor': c.axis,
      'xaxis.tickfont.color': c.muted,
      'yaxis.gridcolor': c.grid,
      'yaxis.tickfont.color': c.muted,
      'yaxis2.gridcolor': c.grid,
      'yaxis2.tickfont.color': c.muted,
      'xaxis2.linecolor': c.axis,
      'hoverlabel.bgcolor': c.surface,
      'hoverlabel.bordercolor': c.axis,
      'hoverlabel.font.color': c.ink,
    });
  });
}

function applyRange(days) {
  CHARTS.forEach(({ id, categorical }) => {
    const el = document.getElementById(id);
    if (!el || !el.data || !el.data.length) return;
    const xs = el.data[0].x || [];
    if (!xs.length) return;

    if (days <= 0 || days >= xs.length) {
      Plotly.relayout(el, { 'xaxis.autorange': true });
    } else if (categorical) {
      Plotly.relayout(el, { 'xaxis.range': [xs.length - days - 0.5, xs.length - 0.5] });
    } else {
      Plotly.relayout(el, { 'xaxis.range': [xs[xs.length - days], xs[xs.length - 1]] });
    }
  });
}

document.querySelectorAll('.range').forEach((btn) => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.range').forEach((b) => b.removeAttribute('aria-pressed'));
    btn.setAttribute('aria-pressed', 'true');
    applyRange(Number(btn.dataset.days));
  });
});

if (CHARTS.length) {
  applyTheme();
  q.addEventListener('change', applyTheme);

  // Webフォントは非同期に届く。届く前に描かれたSVGテキストは字幅が
  // フォールバック基準のままなので、読み込み完了後に測り直させる。
  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(() => {
      CHARTS.forEach(({ id }) => {
        const el = document.getElementById(id);
        if (el) Plotly.Plots.resize(el);
      });
    });
  }
}
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
