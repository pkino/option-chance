"""
日経VI Gate① 妥当性検証用の市場シミュレータ

実データ（日経公式CSV / JPX）がネットワークポリシーで取得できないため、
公表されている日経VIの統計的性質にキャリブレーションした
モンテカルロ・パスを生成して Gate① の妥当性を評価する。

キャリブレーション目標（実績値ベース）:
  - 日経VIの直近52週レンジ: 18.77 〜 66.65
  - 日経VIの長期的な「常態」レンジ: 20〜30pt（急騰後はここへ回帰）
  - 年別の平均水準: 2021 ~17, 2022 ~21, 2023 ~17, 2024 ~19-20,
                    2025 ~22, 2026 ~25（レジームがゆっくり遷移する）
  - VI日次変化の対数標準偏差: 約5〜7%
  - 平均回帰の半減期: 20〜30営業日
  - 株価リターンとVI変化の相関: 約 -0.6（レバレッジ効果）
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import List

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def simulate_path(
    n_days: int,
    seed: int,
    start_price: float = 40000.0,
    regime_mean_levels: tuple = (16.0, 19.0, 23.0, 28.0),
    regime_persistence: int = 250,
) -> pd.DataFrame:
    """
    株価と日経VIの同時パスを生成する。

    VI: 対数OU過程（平均回帰）+ レバレッジ効果 + ジャンプ
    株価: そのVIを瞬間ボラとして使い、負の歪度・fat tail を持つリターン
    """
    rng = np.random.default_rng(seed)

    kappa = 0.035          # 平均回帰速度（半減期 ln2/0.035 ≈ 20営業日）
    sigma_v = 0.042        # VI対数変化のノイズ（日次）
    beta = 3.0             # レバレッジ効果: 株価-1%でVI対数+3%
    nu = 4.0               # 株価リターンのt分布自由度（fat tail）
    VRP_SCALE = 0.78       # 分散リスクプレミアム（実現ボラ / 期待ボラ）
    DRIFT = 0.00115        # 平常時ドリフト（カスケードの下押しを相殺し年+5%程度に）

    # --- VIレジーム（長期平均水準）をゆっくり遷移させる ---
    theta = np.empty(n_days)
    cur = rng.choice(regime_mean_levels)
    i = 0
    while i < n_days:
        length = max(60, int(rng.normal(regime_persistence, regime_persistence * 0.4)))
        nxt = rng.choice(regime_mean_levels)
        end = min(n_days, i + length)
        # レジーム間はランプでつなぐ（急な段差を避ける）
        theta[i:end] = np.linspace(cur, nxt, end - i)
        cur = nxt
        i = end

    log_vi = np.empty(n_days)
    ret = np.empty(n_days)
    log_vi[0] = np.log(theta[0])

    # --- 急落カスケード（クラッシュは1日で終わらず数日続く） ---
    # 日経の実績: -10%級の多日急落が概ね1.5年に1回、-5%級が年2〜3回
    crash_left, crash_daily = 0, 0.0

    for t in range(1, n_days):
        vi_prev = np.exp(log_vi[t - 1])
        # 実現ボラは期待ボラ（VI）より低い＝分散リスクプレミアム
        daily_vol = VRP_SCALE * vi_prev / 100.0 / np.sqrt(TRADING_DAYS)

        # 株価リターン: fat tail + 負の歪度
        z = rng.standard_t(nu) / np.sqrt(nu / (nu - 2))
        r = DRIFT + daily_vol * z - 0.5 * daily_vol ** 2

        # 新しいカスケードの発生
        if crash_left == 0:
            u = rng.random()
            if u < 0.0026:          # 大規模: 年0.65回相当
                length = int(rng.integers(3, 9))
                total = -rng.uniform(0.09, 0.22)
                crash_left, crash_daily = length, total / length
            elif u < 0.0026 + 0.010:  # 中規模: 年2.5回相当
                length = int(rng.integers(2, 5))
                total = -rng.uniform(0.035, 0.075)
                crash_left, crash_daily = length, total / length

        if crash_left > 0:
            # カスケード中は日ごとにばらつかせる（初日に偏らせる）
            r += crash_daily * rng.uniform(0.5, 1.6)
            crash_left -= 1

        ret[t] = r

        # VI: 対数OU + レバレッジ + 独立ノイズ
        eps = rng.standard_normal()
        d_log = kappa * (np.log(theta[t]) - log_vi[t - 1]) - beta * r + sigma_v * eps
        log_vi[t] = log_vi[t - 1] + d_log
        # 下限・上限（実データの下限は概ね15前後、上限は70前後）
        log_vi[t] = np.clip(log_vi[t], np.log(13.5), np.log(70.0))

    ret[0] = 0.0
    vi = np.exp(log_vi)
    close = start_price * np.exp(np.cumsum(ret))

    # --- OHLC を日中レンジから生成 ---
    daily_vol = 0.85 * vi / 100.0 / np.sqrt(TRADING_DAYS)
    rng2 = np.random.default_rng(seed + 100000)
    intraday = close * daily_vol * rng2.uniform(0.8, 2.2, n_days)
    prev_close = np.concatenate([[start_price], close[:-1]])
    open_ = prev_close * (1 + rng2.normal(0, 0.25, n_days) * daily_vol)
    hi_add = np.abs(rng2.normal(0, 0.55, n_days)) * intraday
    lo_sub = np.abs(rng2.normal(0, 0.55, n_days)) * intraday
    high = np.maximum(open_, close) + hi_add
    low = np.minimum(open_, close) - lo_sub

    # --- 出来高（高ボラ時に増える + 高値圏で失速する傾向） ---
    volume = (1.2e9 * rng2.uniform(0.75, 1.4, n_days) * (1 + np.abs(ret) * 6)).astype(np.int64)

    # 営業日の日付列
    days: List[date] = []
    d = date(2010, 1, 4)
    while len(days) < n_days:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)

    return pd.DataFrame(
        {
            "date": days,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "vi": vi,
            "ret": ret,
        }
    )


def calibration_report(df: pd.DataFrame) -> dict:
    vi = df["vi"].values
    dlog = np.diff(np.log(vi))
    ret = df["ret"].values[1:]
    return {
        "vi_mean": float(np.mean(vi)),
        "vi_median": float(np.median(vi)),
        "vi_p05": float(np.percentile(vi, 5)),
        "vi_p95": float(np.percentile(vi, 95)),
        "vi_min": float(np.min(vi)),
        "vi_max": float(np.max(vi)),
        "vi_dlog_std": float(np.std(dlog)),
        "corr_ret_dvi": float(np.corrcoef(ret, dlog)[0, 1]),
        "pct_below_20": float(np.mean(vi <= 20) * 100),
        "ann_vol_pct": float(np.std(df["ret"].values) * np.sqrt(TRADING_DAYS) * 100),
    }
