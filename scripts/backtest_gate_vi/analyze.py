"""Gate① 日経VI条件の妥当性評価"""
from __future__ import annotations

import copy
import sys
from datetime import date, timedelta
from math import log, sqrt, exp, erf
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(Path(__file__).parent))

from src.models.option import MarketData          # noqa: E402
from src.signals.gate import GateChecker          # noqa: E402
from vi_sim import simulate_path                  # noqa: E402


# ---------------------------------------------------------------- gate 計算
def base_config() -> dict:
    with open(PROJECT / "config" / "config.yaml") as f:
        return yaml.safe_load(f)


def compute_base_frame(path: pd.DataFrame, config: dict) -> pd.DataFrame:
    """VI条件を無効化した状態で Gate②A/B・Trigger を1回だけ計算する。"""
    cfg = copy.deepcopy(config)
    # Gate① を常時 True にして、A/B/Trigger だけを取り出す
    cfg["gate_vi"] = {
        "vi_threshold": 1e9,
        "vi_10d_avg_threshold": 1e9,
        "vi_10d_std_threshold": 1e9,
        "vi_10d_slope_threshold": 1e9,
    }
    cfg["risk_management"]["entry_cooldown_days"] = 0  # クールダウンは後段で自前適用

    md = [
        MarketData(date=r.date, open=r.open, high=r.high, low=r.low,
                   close=r.close, volume=int(r.volume), vi=float(r.vi))
        for r in path.itertuples()
    ]
    signals = GateChecker(cfg).check_all_gates(md, events=[])

    rows = []
    for s in signals:
        b = s.details.get("gate_top_b", {})
        b_count = sum(bool(b.get(k, False)) for k in
                      ("b1_volume_failure", "b2_upper_wick_dominance", "b3_gap_up_failure"))
        tv = s.details["technical_values"]
        rows.append({
            "date": s.date,
            "a": s.gate_top_a,
            "b": s.gate_top_b,
            "b_count": b_count,
            "trigger": s.trigger,
            "close": tv["close"],
            "vi": tv["vi"],
            "vi_ma_10": tv["vi_ma_10"],
            "vi_std_10": tv["vi_std_10"],
            "vi_slope_10": tv["vi_slope_10"],
        })
    df = pd.DataFrame(rows)
    # VIの1年パーセンタイル順位（相対水準）
    vi = df["vi"]
    df["vi_pct_1y"] = vi.rolling(252, min_periods=120).apply(
        lambda w: (w[:-1] < w[-1]).mean() * 100, raw=True)
    df["vi_cv_10"] = df["vi_std_10"] / df["vi_ma_10"]
    return df


# ---------------------------------------------------------------- VIゲート定義
def vi_gate_variants(df: pd.DataFrame) -> dict:
    """各バリアントの Gate① 成立フラグ（bool Series）を返す。"""
    v = {}
    v["V0 ゲート無し"] = pd.Series(True, index=df.index)
    v["V1 現行(VI<=20/MA<=20/SD<=1.5/傾き<=0.1)"] = (
        (df.vi <= 20) & (df.vi_ma_10 <= 20) & (df.vi_std_10 <= 1.5) & (df.vi_slope_10 <= 0.1))
    v["V2 水準のみ VI<=20"] = (df.vi <= 20)
    v["V3 水準22 + SD<=2.0 + 傾き<=0.15"] = (
        (df.vi <= 22) & (df.vi_ma_10 <= 22) & (df.vi_std_10 <= 2.0) & (df.vi_slope_10 <= 0.15))
    v["V4 水準25 + SD<=2.5 + 傾き<=0.2"] = (
        (df.vi <= 25) & (df.vi_ma_10 <= 25) & (df.vi_std_10 <= 2.5) & (df.vi_slope_10 <= 0.2))
    v["V5 相対 1yパーセンタイル<=30%"] = (df.vi_pct_1y <= 30)
    v["V6 相対<=40% + CV<=0.08 + 傾き<=0.1"] = (
        (df.vi_pct_1y <= 40) & (df.vi_cv_10 <= 0.08) & (df.vi_slope_10 <= 0.1))
    v["V7 相対<=50% + CV<=0.10 + 傾き<=0.15"] = (
        (df.vi_pct_1y <= 50) & (df.vi_cv_10 <= 0.10) & (df.vi_slope_10 <= 0.15))
    v["V8 (水準<=25 or 相対<=40%) + CV<=0.10"] = (
        ((df.vi <= 25) | (df.vi_pct_1y <= 40)) & (df.vi_cv_10 <= 0.10) & (df.vi_slope_10 <= 0.15))
    return {k: s.fillna(False) for k, s in v.items()}


# ---------------------------------------------------------------- オプション
def _ncdf(x: float) -> float:
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def bs_put(S: float, K: float, T: float, sigma: float) -> tuple:
    """(価格, デルタ) を返す（金利0）"""
    if T <= 0:
        return max(K - S, 0.0), (-1.0 if K > S else 0.0)
    d1 = (log(S / K) + 0.5 * sigma * sigma * T) / (sigma * sqrt(T))
    d2 = d1 - sigma * sqrt(T)
    price = K * _ncdf(-d2) - S * _ncdf(-d1)
    return price, _ncdf(d1) - 1.0


SKEW = 0.55   # 対数マネネス -0.10 で ATM比 +5.5vol の put スキュー


def iv_for(S: float, K: float, vi: float) -> float:
    m = log(K / S)
    return max(0.05, vi / 100.0 + SKEW * max(0.0, -m))


def price_put(S: float, K: float, T: float, vi: float) -> tuple:
    return bs_put(S, K, T, iv_for(S, K, vi))


def pick_expiry(entry: date, all_days: list) -> date | None:
    """営業日DTEが18〜28に入る満期日（=各月の第2金曜）を選ぶ。"""
    for months in (1, 2):
        y, m = entry.year, entry.month + months
        while m > 12:
            m -= 12
            y += 1
        d = date(y, m, 1)
        fridays = [date(y, m, k) for k in range(1, 22) if date(y, m, k).weekday() == 4]
        exp_ = fridays[1]
        dte_b = sum(1 for x in all_days if entry < x <= exp_)
        if 18 <= dte_b <= 28:
            return exp_
    return None


def select_strike(S: float, T: float, vi: float, mode: str, cfg: dict):
    """mode='delta': 目標デルタ -0.30 / mode='premium': プレミアム20〜40円"""
    strikes = np.arange(round(S * 0.60 / 125) * 125, S * 1.02, 125.0)
    best, best_key = None, None
    for K in strikes:
        p, dl = price_put(S, float(K), T, vi)
        if mode == "delta":
            lo, hi = cfg["option_selection"]["delta_range"]["min"], cfg["option_selection"]["delta_range"]["max"]
            if not (lo <= dl <= hi):
                continue
            key = abs(dl - cfg["option_selection"]["target_delta"])
        else:
            lo, hi = cfg["option_selection"]["premium_range"]["min"], cfg["option_selection"]["premium_range"]["max"]
            if not (lo <= p <= hi):
                continue
            key = abs(p - (lo + hi) / 2)
        if best_key is None or key < best_key:
            best, best_key = (float(K), p, dl), key
    return best


# ---------------------------------------------------------------- トレード
def half_spread(p: float) -> float:
    """日経225オプションの現実的な片道スプレッド（呼値1〜5円 + 相対1.2%）"""
    return max(1.0, 0.012 * p)


def simulate_trades(path: pd.DataFrame, entries: list, cfg: dict, mode: str,
                    hold_days: int | None = None) -> pd.DataFrame:
    days = list(path["date"])
    idx_of = {d: i for i, d in enumerate(days)}
    if hold_days is None:
        hold_days = cfg["exit_rules"]["stop_loss"]["time_based_days"]

    trades = []
    for d in entries:
        i = idx_of[d]
        S = float(path["close"].iloc[i])
        vi = float(path["vi"].iloc[i])
        exp_ = pick_expiry(d, days)
        if exp_ is None:
            continue
        T = (exp_ - d).days / 365.0
        sel = select_strike(S, T, vi, mode, cfg)
        if sel is None:
            continue
        K, theo, delta = sel
        entry_px = theo + half_spread(theo)

        # 保有期間中の日次評価
        remaining, realized, exit_reason = 1.0, 0.0, "time_stop"
        exit_px = None
        for j in range(i + 1, min(i + 1 + hold_days, len(days))):
            dd = days[j]
            if dd > exp_:
                break
            S2 = float(path["close"].iloc[j])
            vi2 = float(path["vi"].iloc[j])
            T2 = (exp_ - dd).days / 365.0
            p2, _ = price_put(S2, K, T2, vi2)
            bid = max(0.0, p2 - half_spread(p2))
            # 2倍で半分利確
            if remaining == 1.0 and bid >= entry_px * 2:
                realized += 0.5 * bid
                remaining = 0.5
                exit_reason = "half_tp"
            exit_px = bid
        if exit_px is None:
            continue
        proceeds = realized + remaining * exit_px
        pnl = proceeds - entry_px
        trades.append({
            "date": d, "S": S, "K": K, "vi": vi, "delta": delta,
            "entry_px": entry_px, "proceeds": proceeds,
            "pnl": pnl, "pnl_pct": pnl / entry_px * 100, "reason": exit_reason,
        })
    return pd.DataFrame(trades)


def apply_cooldown(dates: list, cooldown: int) -> list:
    out, last = [], None
    for d in dates:
        if last is not None and (d - last).days < cooldown:
            continue
        out.append(d)
        last = d
    return out
