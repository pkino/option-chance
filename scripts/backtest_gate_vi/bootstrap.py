"""バリアント間の差がノイズかどうかをブートストラップで検証"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

here = Path(__file__).parent
np.random.seed(0)


def boot(x, n=4000, stat=np.mean):
    x = np.asarray(x, dtype=float)
    idx = np.random.randint(0, len(x), size=(n, len(x)))
    return np.sort(stat(x[idx], axis=1))


for f, label in [("trades_delta_5.pkl", "デルタ-0.30 / 保有5営業日"),
                 ("trades_premium_10.pkl", "プレミアム20-40円 / 保有10営業日")]:
    p = here / f
    if not p.exists():
        print(f"(skip {f})")
        continue
    t = pd.read_pickle(p)
    print("=" * 92)
    print(f"[{label}] 1トレード平均損益% の95%信頼区間（ブートストラップ4000回）")
    print("=" * 92)
    base = t[t.variant.str.startswith("V0")].pnl_pct.values
    bb = boot(base)
    for name, sub in t.groupby("variant"):
        b = boot(sub.pnl_pct.values)
        lo, hi = b[int(.025 * len(b))], b[int(.975 * len(b))]
        # V0 との差（独立標本として近似）
        d = boot(sub.pnl_pct.values) - bb
        d = np.sort(d)
        dlo, dhi = d[int(.025 * len(d))], d[int(.975 * len(d))]
        mark = "" if (dlo <= 0 <= dhi) else "  ★有意"
        print(f"  {name:<42} N={len(sub):>4}  平均{sub.pnl_pct.mean():+7.1f}%  "
              f"95%CI[{lo:+7.1f},{hi:+7.1f}]  V0比 {dlo:+6.1f}〜{dhi:+6.1f}{mark}")
    print()
