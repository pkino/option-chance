"""ステップC: Gate①バリアント別のプット買い損益バックテスト"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from analyze import (base_config, compute_base_frame, vi_gate_variants,  # noqa: E402
                     simulate_trades, apply_cooldown, price_put, select_strike)
from vi_sim import simulate_path  # noqa: E402

pd.set_option("display.width", 240)
pd.set_option("display.max_columns", 60)

N_PATHS = 12
YEARS = 12
MODE = sys.argv[1] if len(sys.argv) > 1 else "premium"   # "premium" or "delta"
HOLD = int(sys.argv[2]) if len(sys.argv) > 2 else None   # 保有営業日数
cfg = base_config()
COOLDOWN = cfg["risk_management"]["entry_cooldown_days"]

per_variant = {}
years_total = 0.0

for s in range(N_PATHS):
    path = simulate_path(252 * YEARS, seed=1000 + s)
    df = compute_base_frame(path, cfg)
    df = df.reset_index(drop=True)
    ok = df["vi_pct_1y"].notna()
    years_total += ok.sum() / 252

    ab_trig = df.a & df.b & df.trigger
    sd_trig = (df.b_count >= 2) & df.trigger
    entry_any = (ab_trig | sd_trig) & ok

    for name, g in vi_gate_variants(df).items():
        dates = list(df.loc[g & entry_any, "date"])
        dates = apply_cooldown(dates, COOLDOWN)
        t = simulate_trades(path, dates, cfg, MODE, HOLD)
        if len(t):
            per_variant.setdefault(name, []).append(t)
    print(f"  path {s} done", flush=True)

print(f"\nモード: {MODE} / 保有{HOLD or cfg['exit_rules']['stop_loss']['time_based_days']}営業日 / 総年数: {years_total:.0f}年相当 / クールダウン{COOLDOWN}日\n")

rows = []
for name, parts in per_variant.items():
    t = pd.concat(parts, ignore_index=True)
    wins = t[t.pnl > 0].pnl.sum()
    losses = abs(t[t.pnl < 0].pnl.sum())
    # 1トレードのリスクを口座の0.5%に固定した場合の年率寄与
    per_year = len(t) / years_total
    rows.append({
        "バリアント": name,
        "トレード数": len(t),
        "回/年": round(per_year, 2),
        "平均建玉": round(t.entry_px.mean(), 1),
        "平均IVレベル": round(t.vi.mean(), 1),
        "勝率%": round((t.pnl > 0).mean() * 100, 1),
        "平均損益%": round(t.pnl_pct.mean(), 1),
        "中央損益%": round(t.pnl_pct.median(), 1),
        "PF": round(wins / losses, 2) if losses > 0 else np.inf,
        "全損率%": round((t.pnl_pct <= -95).mean() * 100, 1),
        "10倍超%": round((t.pnl_pct >= 900).mean() * 100, 1),
        "年率期待%": round(t.pnl_pct.mean() * per_year * 0.005, 2),
    })

pd.concat([t.assign(variant=k) for k, parts in per_variant.items() for t in parts],
          ignore_index=True).to_pickle(Path(__file__).parent / f"trades_{MODE}_{HOLD or 5}.pkl")

res = pd.DataFrame(rows)
print(res.to_string(index=False))

out = Path(__file__).parent / f"pnl_{MODE}_{HOLD or 5}.csv"
res.to_csv(out, index=False)

# 建玉サンプル
t0 = pd.concat(per_variant["V0 ゲート無し"], ignore_index=True)
print("\n[参考] ゲート無しのトレード例（先頭5件）")
print(t0.head(5).round(2).to_string(index=False))
print(f"\n[参考] 選択された権利行使価格の平均OTM率: "
      f"{((t0.K/t0.S - 1)*100).mean():.1f}%  デルタ平均: {t0.delta.mean():.3f}")
