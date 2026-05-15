"""
バックテスト検知分析スクリプト

現状の設定でGate条件がどの程度の頻度で発火するかを徹底調査する。

データ取得優先順位:
  1. 実際のデータ（日経公式CSV）- ネットワーク環境によっては取得できない場合あり
  2. 日経225の実際の統計特性に基づく合成データ（フォールバック）
     - 2021〜2026年の日経225の実績（年間リターン・ボラティリティ・VI水準）を再現
     - 上昇フェーズ/天井/調整フェーズを繰り返す現実的なシナリオを複数回実施
"""
import sys
from pathlib import Path
from datetime import date, timedelta
from typing import List, Dict, Any, Tuple
import json
import argparse

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import yaml
import pandas as pd
import numpy as np

from src.models.option import MarketData
from src.data_sources.market_data import MarketDataFetcher
from src.signals.gate import GateChecker
from src.indicators.technical import TechnicalIndicators


def load_config() -> Dict[str, Any]:
    config_path = project_root / "config" / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def fetch_real_data(years: int = 5) -> List[MarketData]:
    """実際のデータ取得を試みる"""
    end_date = date.today()
    start_date = end_date - timedelta(days=years * 365 + 90)
    print(f"実データ取得試行: {start_date} 〜 {end_date}")
    fetcher = MarketDataFetcher()
    return fetcher.fetch_market_data_with_vi(start_date, end_date)


def generate_realistic_nikkei_data(years: int = 5, seed: int = 42) -> List[MarketData]:
    """
    日経225の実際の統計特性に基づく合成データを生成する。

    参考値（2021-2026年実績）:
    - 2021年: 年間+4.9%, 日次ボラ~0.9%, VI平均~17
    - 2022年: 年間-9.4%, 日次ボラ~1.1%, VI平均~21 (ウクライナ侵攻等)
    - 2023年: 年間+28.2%, 日次ボラ~0.8%, VI平均~17
    - 2024年: 年間+19.2%, 日次ボラ~1.0%, VI平均~19
    - 2025年前半: 年間-8%程度, 日次ボラ~1.3%, VI平均~22
    """
    rng = np.random.default_rng(seed)

    # 年別パラメータ（実際の市場環境を反映）
    year_params = {
        2021: {"annual_return": 0.049, "annual_vol": 0.105, "vi_mean": 17.0, "vi_std": 3.5},
        2022: {"annual_return": -0.094, "annual_vol": 0.135, "vi_mean": 21.0, "vi_std": 5.0},
        2023: {"annual_return": 0.282, "annual_vol": 0.095, "vi_mean": 17.0, "vi_std": 3.0},
        2024: {"annual_return": 0.192, "annual_vol": 0.110, "vi_mean": 19.0, "vi_std": 5.0},
        2025: {"annual_return": -0.080, "annual_vol": 0.140, "vi_mean": 22.0, "vi_std": 6.0},
        2026: {"annual_return": 0.000, "annual_vol": 0.120, "vi_mean": 20.0, "vi_std": 4.0},
    }

    end_date = date.today()
    start_date = end_date - timedelta(days=years * 365 + 90)

    # 営業日を生成（土日を除く簡易版）
    business_days = []
    current = start_date
    while current <= end_date:
        if current.weekday() < 5:  # 月〜金
            business_days.append(current)
        current += timedelta(days=1)

    # 日経225の開始値
    # 2021年初: 約27,000円
    year_start_map = {
        2021: 27000,
        2022: 28791,  # 2021年末
        2023: 26094,  # 2022年末
        2024: 33464,  # 2023年末
        2025: 39894,  # 2024年末
        2026: 35500,  # 2025年末（推定）
    }

    market_data = []
    prev_close = None

    for bd in business_days:
        year = bd.year
        params = year_params.get(year, year_params[2025])

        # 年始の初期値を設定
        if prev_close is None:
            prev_close = float(year_start_map.get(year, 27000))

        # 日次リターンを生成（ジャンプ拡散モデル）
        daily_vol = params["annual_vol"] / np.sqrt(252)
        drift = params["annual_return"] / 252

        # 通常の日次変動
        daily_return = drift + daily_vol * rng.standard_normal()

        # 稀なジャンプ（ブラックスワン）
        if rng.random() < 0.005:  # 0.5%の確率でジャンプ
            jump_size = rng.choice([-0.04, -0.03, 0.03, 0.04])
            daily_return += jump_size

        close = prev_close * (1 + daily_return)
        close = max(close, 15000)  # 下限

        # OHLC生成（日中変動）
        intraday_range = close * daily_vol * rng.uniform(1.0, 2.5)
        open_drift = rng.normal(0, intraday_range * 0.3)
        open_price = prev_close + open_drift

        # 上ヒゲ・下ヒゲのある現実的なOHLC
        high_add = abs(rng.normal(0, intraday_range * 0.6))
        low_sub = abs(rng.normal(0, intraday_range * 0.6))

        high = max(open_price, close) + high_add
        low = min(open_price, close) - low_sub
        high = max(high, open_price, close)
        low = min(low, open_price, close)

        # VI生成（ボラティリティが高い時にVIも高くなる）
        base_vi = params["vi_mean"]
        vi_noise = rng.normal(0, params["vi_std"])

        # 大きな下落時はVIスパイク
        if daily_return < -0.02:
            vi_spike = abs(daily_return) * 200
        elif daily_return < -0.01:
            vi_spike = abs(daily_return) * 50
        else:
            vi_spike = 0

        # VIは前日からの連続性を持つ（オートコリレーション）
        if market_data:
            prev_vi = market_data[-1].vi or base_vi
            vi = prev_vi * 0.85 + (base_vi + vi_noise + vi_spike) * 0.15
        else:
            vi = base_vi + vi_noise

        vi = max(8.0, min(60.0, vi))  # VI範囲: 8〜60

        # 出来高（高ボラ時に増加）
        base_volume = 1_200_000_000
        volume = int(base_volume * rng.uniform(0.7, 1.5) * (1 + abs(daily_return) * 5))

        market_data.append(MarketData(
            date=bd,
            open=round(open_price, 1),
            high=round(high, 1),
            low=round(low, 1),
            close=round(close, 1),
            volume=volume,
            vi=round(vi, 2),
        ))

        prev_close = close

    print(f"合成データ生成完了: {len(market_data)} 営業日 ({market_data[0].date} 〜 {market_data[-1].date})")
    vi_avg = np.mean([d.vi for d in market_data if d.vi])
    print(f"  価格範囲: {min(d.close for d in market_data):.0f} 〜 {max(d.close for d in market_data):.0f}")
    print(f"  VI平均: {vi_avg:.1f}")

    return market_data


def analyze_signals(market_data: List[MarketData], config: Dict[str, Any]) -> pd.DataFrame:
    """シグナルを生成してDataFrameに変換"""
    checker = GateChecker(config)
    signals = checker.check_all_gates(market_data, events=config.get("events", []))

    rows = []
    for s in signals:
        row = {
            "date": s.date,
            "gate_vi": s.gate_vi,
            "gate_top_a": s.gate_top_a,
            "gate_top_b": s.gate_top_b,
            "gate_top_c": s.gate_top_c,
            "trigger": s.trigger,
            "is_entry": s.is_entry_signal,
            "is_probe": s.is_probe_signal,
            "is_supply_dominant": s.is_supply_dominant_entry,
            "is_strong": s.is_strong_signal,
            "cooldown_suppressed": s.details.get("cooldown_suppressed", False),
        }

        details_a = s.details.get("gate_top_a", {})
        row["a1_rsi"] = details_a.get("a1_rsi_reversal", False)
        row["a2_macd"] = details_a.get("a2_macd_weakening", False)
        row["a3_div"] = details_a.get("a3_divergence", False)
        row["a4_bb"] = details_a.get("a4_overbought", False)

        details_b = s.details.get("gate_top_b", {})
        row["b1_vol"] = details_b.get("b1_volume_failure", False)
        row["b2_wick"] = details_b.get("b2_upper_wick_dominance", False)
        row["b3_gap"] = details_b.get("b3_gap_up_failure", False)

        details_vi = s.details.get("gate_vi", {})
        row["vi_value"] = details_vi.get("vi")
        row["vi_ma10"] = details_vi.get("vi_ma_10")
        row["vi_std10"] = details_vi.get("vi_std_10")
        row["vi_slope10"] = details_vi.get("vi_slope_10")

        tech = s.details.get("technical_values", {})
        row["close"] = tech.get("close")
        row["rsi"] = tech.get("rsi")
        row["macd"] = tech.get("macd")

        trigger_det = s.details.get("trigger", {})
        row["t_prev_low"] = trigger_det.get("prev_low_break", False)
        row["t_ma5"] = trigger_det.get("ma5_break", False)

        rows.append(row)

    return pd.DataFrame(rows)


def print_section(title: str):
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}")


def print_bar(label: str, count: int, total: int, width: int = 28):
    pct = count / total * 100 if total > 0 else 0
    filled = int(pct / 100 * width)
    bar = "█" * filled + "░" * (width - filled)
    print(f"  {label:<32} [{bar}] {count:>4}日 ({pct:5.1f}%)")


def run_analysis(df: pd.DataFrame, config: Dict[str, Any], data_source_note: str = ""):
    total = len(df)
    if total == 0:
        print("シグナルデータがありません")
        return

    print_section(f"バックテスト検知分析レポート  (評価日数: {total} 営業日)")
    if data_source_note:
        print(f"  ※ {data_source_note}")

    # ========== 1. 各ゲート通過率 ==========
    print_section("1. 各ゲートの通過率")
    print_bar("Gate① VI安定", df["gate_vi"].sum(), total)
    print_bar("Gate② A テクニカル (2条件以上)", df["gate_top_a"].sum(), total)
    print_bar("Gate② B 需給 (1条件以上) ★必須", df["gate_top_b"].sum(), total)
    print_bar("Gate② C マクロ (任意強化)", df["gate_top_c"].sum(), total)
    print_bar("Trigger③ エントリー発火", df["trigger"].sum(), total)

    # ========== 2. テクニカル条件内訳 ==========
    print_section("2. Gate② A テクニカル条件の内訳")
    print_bar("  A1: RSI過熱→2日連続低下", df["a1_rsi"].sum(), total)
    print_bar("  A2: MACD 3日ヒスト縮小", df["a2_macd"].sum(), total)
    print_bar("  A3: ダイバージェンス(20日)", df["a3_div"].sum(), total)
    print_bar("  A4: ボリンジャー上抜け+上ヒゲ", df["a4_bb"].sum(), total)

    a_count = df[["a1_rsi", "a2_macd", "a3_div", "a4_bb"]].sum(axis=1)
    req = config["gate_top"]["technical"]["required_conditions"]
    print(f"\n  A条件合計数の分布 (設定: {req}条件以上でGate②A通過):")
    for n in range(5):
        cnt = (a_count == n).sum()
        print_bar(f"    {n}条件成立", cnt, total)

    # ========== 3. 需給条件内訳 ==========
    print_section("3. Gate② B 需給条件の内訳")
    print_bar("  B1: 高値圏の出来高失速", df["b1_vol"].sum(), total)
    print_bar("  B2: 上ヒゲ優勢 (3日中2日)", df["b2_wick"].sum(), total)
    print_bar("  B3: ギャップアップ失速(寄り天)", df["b3_gap"].sum(), total)

    # ========== 4. Trigger条件内訳 ==========
    print_section("4. Trigger③ エントリー条件の内訳")
    print_bar("  T1: 前日安値割れ", df["t_prev_low"].sum(), total)
    print_bar("  T2: 5日MA割れ", df["t_ma5"].sum(), total)
    both_t = (df["t_prev_low"] & df["t_ma5"]).sum()
    print_bar("  T1+T2 両方同時成立", both_t, total)

    # ========== 5. シグナル種別まとめ ==========
    print_section("5. シグナル種別の発生頻度")
    entry_raw = df["is_entry"].sum()
    suppressed = df["cooldown_suppressed"].sum()
    effective_entry = entry_raw - suppressed
    probe = df["is_probe"].sum()
    supply_dom = df["is_supply_dominant"].sum()
    strong = df["is_strong"].sum()

    print_bar("完全エントリーシグナル (G①②③)", entry_raw, total)
    print_bar("  うちクールダウン抑制", suppressed, total)
    print_bar("  ★実質エントリー(有効)", effective_entry, total)
    print_bar("打診シグナル (G①②のみ)", probe, total)
    print_bar("需給主導型エントリー", supply_dom, total)
    print_bar("強シグナル (上記+C条件)", strong, total)

    years_est = total / 250
    if years_est > 0:
        print(f"\n  年間換算 ({years_est:.1f}年分 / {total}営業日):")
        print(f"    完全エントリー(raw):     {entry_raw / years_est:.1f} 回/年")
        print(f"    ★実質エントリー:         {effective_entry / years_est:.1f} 回/年")
        print(f"    打診シグナル:            {probe / years_est:.1f} 回/年")
        print(f"    需給主導型:              {supply_dom / years_est:.1f} 回/年")
        print(f"    強シグナル:              {strong / years_est:.1f} 回/年")

    # ========== 6. ゲートの組合せパターン ==========
    print_section("6. Gate通過パターン (上位10) [①②A②B③]")
    df["pattern"] = (
        df["gate_vi"].astype(str).str[0] +
        df["gate_top_a"].astype(str).str[0] +
        df["gate_top_b"].astype(str).str[0] +
        df["trigger"].astype(str).str[0]
    )
    pattern_counts = df["pattern"].value_counts().head(10)
    for pat, cnt in pattern_counts.items():
        vi_f  = "①" if pat[0] == "T" else "  "
        a_f   = "②A" if pat[1] == "T" else "   "
        b_f   = "②B" if pat[2] == "T" else "   "
        t_f   = "③" if pat[3] == "T" else "  "
        label = f"[{vi_f}{a_f}{b_f}{t_f}]"
        print_bar(f"  {label}", cnt, total)

    # ========== 7. ゲート間の相関 ==========
    print_section("7. ゲート間の同時成立分析")
    gates = [("gate_vi","①"), ("gate_top_a","②A"), ("gate_top_b","②B"), ("trigger","③")]
    for i in range(len(gates)):
        for j in range(i+1, len(gates)):
            c1, l1 = gates[i]
            c2, l2 = gates[j]
            both = (df[c1] & df[c2]).sum()
            c1_sum = df[c1].sum()
            c2_sum = df[c2].sum()
            if c1_sum > 0:
                cond_pct = both / c1_sum * 100
            else:
                cond_pct = 0.0
            print(f"  {l1}+{l2}: 同時成立 {both}日 "
                  f"| {l1}成立{c1_sum}日のうち {cond_pct:.1f}%")

    # ========== 8. VI環境別の分析 ==========
    print_section("8. VI水準別の分析")
    vi_thresh = config["gate_vi"]["vi_threshold"]

    vi_available = df[df["vi_value"].notna()]
    if len(vi_available) > 0:
        low_vi  = vi_available[vi_available["vi_value"] < vi_thresh]
        high_vi = vi_available[vi_available["vi_value"] >= vi_thresh]

        print(f"  VI < {vi_thresh} (低ボラ): {len(low_vi)}日 ({len(low_vi)/total*100:.1f}%)")
        if len(low_vi) > 0:
            ent = low_vi["is_entry"].sum()
            sup = low_vi["is_supply_dominant"].sum()
            print(f"    → 完全エントリーシグナル:  {ent}日 ({ent/len(low_vi)*100:.2f}%)")
            print(f"    → 需給主導型エントリー:    {sup}日")

        print(f"  VI >= {vi_thresh} (高ボラ): {len(high_vi)}日 ({len(high_vi)/total*100:.1f}%)")
        if len(high_vi) > 0:
            ent = high_vi["is_entry"].sum()
            sup = high_vi["is_supply_dominant"].sum()
            print(f"    → 完全エントリーシグナル:  {ent}日 ({ent/len(high_vi)*100:.2f}%)")
            print(f"    → 需給主導型エントリー:    {sup}日")

        vi_vals = vi_available["vi_value"]
        print(f"\n  VI統計:")
        print(f"    平均={vi_vals.mean():.2f}, 中央値={vi_vals.median():.2f}, "
              f"最大={vi_vals.max():.2f}, 最小={vi_vals.min():.2f}")
        for pct in [10, 25, 75, 90]:
            print(f"    {pct}パーセンタイル: {vi_vals.quantile(pct/100):.2f}")
    else:
        print("  VIデータなし")

    # ========== 9. 感度分析: VI閾値 ==========
    print_section("9. 感度分析: VI閾値を変えたときの影響")
    vi_col = df["vi_value"].fillna(df["vi_ma10"])  # MA10をフォールバック
    print(f"  {'VI閾値':>8} {'Gate①通過':>10} {'エントリー':>10} {'年間換算':>10}")
    print(f"  {'-'*8} {'-'*10} {'-'*10} {'-'*10}")
    for thresh in [15, 17, 20, 22, 25, 30]:
        # VI閾値を一時的に変更してシミュレート
        # (実際にはgate_viの再計算が必要だが、近似として現在のgate_viとVI水準で推定)
        vi_ok_days = (vi_col < thresh).sum() if vi_col.notna().any() else 0
        # 元のgate_viをベースに補正（同じ比率で）
        ratio = vi_ok_days / max(len(vi_col[vi_col.notna()]), 1)
        est_entry = effective_entry * ratio / (df["gate_vi"].mean() if df["gate_vi"].mean() > 0 else 1) * df["gate_vi"].mean()
        print(f"  {thresh:>8} {vi_ok_days:>8}日  {int(est_entry):>8}件  {est_entry/years_est:>8.1f}件/年")

    # ========== 10. エントリーシグナル一覧 ==========
    print_section("10. 実質エントリーシグナル一覧 (クールダウン除外後)")
    entry_df = df[df["is_entry"] & ~df["cooldown_suppressed"]].copy()
    if len(entry_df) == 0:
        print("  エントリーシグナルなし")
    else:
        print(f"  計 {len(entry_df)} 件\n")
        hdr = f"  {'日付':<12} {'終値':>7} {'VI':>6} {'RSI':>6} {'①':>3} {'②A':>4} {'②B':>4} {'③':>3} {'強?':>4} {'条件':>20}"
        print(hdr)
        print("  " + "-" * (len(hdr) - 2))
        for _, r in entry_df.iterrows():
            vi_str = f"{r['vi_value']:.1f}" if pd.notna(r["vi_value"]) else " N/A"
            rsi_str = f"{r['rsi']:.1f}" if pd.notna(r["rsi"]) else " N/A"
            close_str = f"{r['close']:.0f}" if pd.notna(r["close"]) else "  N/A"
            strong_mark = "★" if r["is_strong"] else ""
            a_conds = ("A1" if r["a1_rsi"] else "") + ("A2" if r["a2_macd"] else "") + \
                      ("A3" if r["a3_div"] else "") + ("A4" if r["a4_bb"] else "")
            b_conds = ("B1" if r["b1_vol"] else "") + ("B2" if r["b2_wick"] else "") + \
                      ("B3" if r["b3_gap"] else "")
            conds = f"{a_conds}/{b_conds}"
            print(f"  {str(r['date']):<12} {close_str:>7} {vi_str:>6} {rsi_str:>6} "
                  f"{'✓' if r['gate_vi'] else '✗':>3} "
                  f"{'✓' if r['gate_top_a'] else '✗':>4} "
                  f"{'✓' if r['gate_top_b'] else '✗':>4} "
                  f"{'✓' if r['trigger'] else '✗':>3} "
                  f"{strong_mark:>4} {conds:>20}")

    # ========== 11. 打診シグナル一覧 ==========
    probe_df = df[df["is_probe"]].copy()
    print_section("11. 打診シグナル一覧 (Gate①②成立、Trigger③未発火)")
    if len(probe_df) == 0:
        print("  打診シグナルなし")
    else:
        print(f"  計 {len(probe_df)} 件 (Triggerを待てば一部がエントリーに発展)")
        print(f"\n  {'日付':<12} {'終値':>7} {'VI':>6} {'RSI':>6} {'②A':>4} {'②B':>4} {'条件':>20}")
        print("  " + "-" * 65)
        for _, r in probe_df.iterrows():
            vi_str = f"{r['vi_value']:.1f}" if pd.notna(r["vi_value"]) else " N/A"
            rsi_str = f"{r['rsi']:.1f}" if pd.notna(r["rsi"]) else " N/A"
            close_str = f"{r['close']:.0f}" if pd.notna(r["close"]) else "  N/A"
            a_conds = ("A1" if r["a1_rsi"] else "") + ("A2" if r["a2_macd"] else "") + \
                      ("A3" if r["a3_div"] else "") + ("A4" if r["a4_bb"] else "")
            b_conds = ("B1" if r["b1_vol"] else "") + ("B2" if r["b2_wick"] else "") + \
                      ("B3" if r["b3_gap"] else "")
            print(f"  {str(r['date']):<12} {close_str:>7} {vi_str:>6} {rsi_str:>6} "
                  f"{'✓' if r['gate_top_a'] else '✗':>4} "
                  f"{'✓' if r['gate_top_b'] else '✗':>4} "
                  f"  {a_conds}/{b_conds}")

    # ========== 12. 月別分布 ==========
    print_section("12. エントリーシグナルの月別・年別分布")
    if len(entry_df) > 0:
        entry_df["year"] = pd.to_datetime(entry_df["date"]).dt.year
        entry_df["month"] = pd.to_datetime(entry_df["date"]).dt.month

        yearly = entry_df.groupby("year").size()
        print("  年別:")
        for yr, cnt in yearly.items():
            bar = "█" * cnt
            print(f"    {yr}: {bar} ({cnt}件)")

        print("\n  月別 (全年合計):")
        month_names = ["1月","2月","3月","4月","5月","6月",
                       "7月","8月","9月","10月","11月","12月"]
        monthly = entry_df.groupby("month").size()
        for m in range(1, 13):
            cnt = monthly.get(m, 0)
            bar = "█" * cnt
            print(f"    {month_names[m-1]:>4}: {bar} ({cnt}件)")
    else:
        print("  エントリーシグナルなし")

    # ========== 13. 考察 ==========
    print_section("13. 考察・設定評価")

    total_entry = effective_entry
    annual_entry = total_entry / years_est if years_est > 0 else 0

    if annual_entry < 3:
        freq_comment = "⚠️  年間3件未満 → 過剰フィルタの可能性。条件緩和を検討。"
    elif annual_entry <= 8:
        freq_comment = "✅  年間3〜8件 → 適切な頻度。高品質シグナルに絞れている。"
    elif annual_entry <= 15:
        freq_comment = "⚡  年間9〜15件 → やや多め。ノイズが混入している可能性。"
    else:
        freq_comment = "❌  年間15件超 → 過剰検知。条件を厳格化を推奨。"

    print(f"\n  エントリー頻度評価: {annual_entry:.1f}件/年")
    print(f"  {freq_comment}")

    vi_pass_rate = df["gate_vi"].mean() * 100
    a_pass_rate  = df["gate_top_a"].mean() * 100
    b_pass_rate  = df["gate_top_b"].mean() * 100
    t_pass_rate  = df["trigger"].mean() * 100

    print(f"\n  ゲート通過率の評価:")
    print(f"    Gate①  {vi_pass_rate:.1f}% ", end="")
    if vi_pass_rate > 80:
        print("→ VI閾値が緩すぎる可能性")
    elif vi_pass_rate < 40:
        print("→ VI閾値が厳しく市場参加機会が限定的")
    else:
        print("→ 適切")

    print(f"    Gate②A {a_pass_rate:.1f}% ", end="")
    if a_pass_rate > 30:
        print("→ テクニカル条件が緩い")
    elif a_pass_rate < 5:
        print("→ テクニカル条件が厳しすぎる")
    else:
        print("→ 適切")

    print(f"    Gate②B {b_pass_rate:.1f}% ", end="")
    if b_pass_rate > 40:
        print("→ 需給条件が緩い")
    elif b_pass_rate < 10:
        print("→ 需給条件が厳しすぎる")
    else:
        print("→ 適切")

    print(f"    Trigger③ {t_pass_rate:.1f}% → エントリー発火率")

    print(f"\n  クールダウン (entry_cooldown_days={config.get('risk_management',{}).get('entry_cooldown_days',0)}) の影響:")
    if entry_raw > 0:
        suppression_rate = suppressed / entry_raw * 100
        print(f"    シグナル抑制率: {suppression_rate:.1f}% ({suppressed}/{entry_raw}件を抑制)")
    else:
        print("    エントリーシグナルなし")

    print()


def save_results(df: pd.DataFrame, output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)

    df.to_csv(output_dir / "all_signals.csv", index=False)

    entry_df = df[df["is_entry"] & ~df["cooldown_suppressed"]]
    entry_df.to_csv(output_dir / "entry_signals.csv", index=False)

    total = len(df)
    years_est = total / 250
    effective_entry = (df["is_entry"] & ~df["cooldown_suppressed"]).sum()

    summary = {
        "total_evaluation_days": total,
        "estimated_years": round(years_est, 1),
        "gate_pass_rates": {
            "gate_vi_days": int(df["gate_vi"].sum()),
            "gate_vi_pct": round(df["gate_vi"].mean() * 100, 1),
            "gate_top_a_days": int(df["gate_top_a"].sum()),
            "gate_top_a_pct": round(df["gate_top_a"].mean() * 100, 1),
            "gate_top_b_days": int(df["gate_top_b"].sum()),
            "gate_top_b_pct": round(df["gate_top_b"].mean() * 100, 1),
            "gate_top_c_days": int(df["gate_top_c"].sum()),
            "gate_top_c_pct": round(df["gate_top_c"].mean() * 100, 1),
            "trigger_days": int(df["trigger"].sum()),
            "trigger_pct": round(df["trigger"].mean() * 100, 1),
        },
        "sub_conditions": {
            "a1_rsi_days": int(df["a1_rsi"].sum()),
            "a2_macd_days": int(df["a2_macd"].sum()),
            "a3_div_days": int(df["a3_div"].sum()),
            "a4_bb_days": int(df["a4_bb"].sum()),
            "b1_vol_days": int(df["b1_vol"].sum()),
            "b2_wick_days": int(df["b2_wick"].sum()),
            "b3_gap_days": int(df["b3_gap"].sum()),
        },
        "signal_counts": {
            "entry_signals_raw": int(df["is_entry"].sum()),
            "cooldown_suppressed": int(df["cooldown_suppressed"].sum()),
            "entry_signals_effective": int(effective_entry),
            "probe_signals": int(df["is_probe"].sum()),
            "supply_dominant": int(df["is_supply_dominant"].sum()),
            "strong_signals": int(df["is_strong"].sum()),
        },
        "per_year": {
            "entry_effective": round(effective_entry / years_est, 1) if years_est > 0 else 0,
            "probe": round(df["is_probe"].sum() / years_est, 1) if years_est > 0 else 0,
        },
    }

    with open(output_dir / "detection_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    print(f"結果保存: {output_dir}")
    print(f"  all_signals.csv ({total}行) / entry_signals.csv ({len(entry_df)}行) / detection_summary.json")


def main():
    parser = argparse.ArgumentParser(description="バックテスト検知分析")
    parser.add_argument("--years", type=int, default=5, help="分析年数 (デフォルト: 5)")
    parser.add_argument("--output", type=str, default="output/detection_analysis",
                        help="出力ディレクトリ")
    parser.add_argument("--synthetic-only", action="store_true",
                        help="合成データのみ使用（ネットワーク不使用）")
    args = parser.parse_args()

    print("=" * 70)
    print("  バックテスト検知分析 開始")
    print("=" * 70)

    config = load_config()

    # データ取得
    market_data = []
    data_source_note = ""

    if not args.synthetic_only:
        print(f"\n実データ取得試行 ({args.years}年分)...")
        try:
            market_data = fetch_real_data(args.years)
        except Exception as e:
            print(f"  実データ取得エラー: {e}")

    if not market_data:
        print("\n合成データを使用します（日経225の実際の統計特性に基づく）")
        market_data = generate_realistic_nikkei_data(args.years)
        data_source_note = (
            "実ネットワークデータ取得不可のため、日経225の実績統計値を基にした合成データを使用 "
            "(2021-2026年の年間リターン・ボラティリティ・VI水準を再現)"
        )
    else:
        data_source_note = "日経公式サイトの実データを使用"

    print(f"\n取得: {len(market_data)} 営業日 ({market_data[0].date} 〜 {market_data[-1].date})")
    vi_cnt = sum(1 for d in market_data if d.vi is not None)
    print(f"VI付き: {vi_cnt} / {len(market_data)}")

    # シグナル分析
    print("\nシグナル検出中...")
    df = analyze_signals(market_data, config)
    print(f"完了: {len(df)} 日分")

    # レポート
    run_analysis(df, config, data_source_note)

    # 保存
    output_dir = project_root / args.output
    save_results(df, output_dir)


if __name__ == "__main__":
    main()
