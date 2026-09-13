"""ステップA/B: Gate①の発火頻度分解と、予測力（先行リターン）の検証"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from analyze import base_config, compute_base_frame, vi_gate_variants  # noqa: E402
from vi_sim import simulate_path  # noqa: E402

pd.set_option("display.width", 220)
pd.set_option("display.max_columns", 50)

N_PATHS = 12
YEARS = 12
cfg = base_config()

frames = []
for s in range(N_PATHS):
    p = simulate_path(252 * YEARS, seed=1000 + s)
    df = compute_base_frame(p, cfg)
    df["path"] = s
    # 先行リターン・最大下落率
    c = p.set_index("date")["close"]
    cl = df["date"].map(c)
    for h in (5, 10, 20):
        fwd = cl.shift(-h) / cl - 1
        df[f"fwd{h}"] = fwd * 100
    # 今後20日の最大ドローダウン（終値ベース）
    arr = cl.values
    mdd = np.full(len(arr), np.nan)
    for i in range(len(arr) - 20):
        w = arr[i + 1: i + 21]
        mdd[i] = (w.min() / arr[i] - 1) * 100
    df["fwd20_mdd"] = mdd
    frames.append(df)

all_df = pd.concat(frames, ignore_index=True)
all_df = all_df[all_df["vi_pct_1y"].notna()].copy()
print(f"総営業日数: {len(all_df):,}  (= {len(all_df)/252:.0f} 年相当)\n")

# --- シミュレータの VI 分布チェック（実データとの突き合わせ用） ---
print("=" * 100)
print("[0] シミュレーションVIの性質（実データ検証用）")
print("=" * 100)
q = all_df["vi"].quantile([.05, .25, .5, .75, .95]).round(1).to_dict()
print(f"  VI分位: 5%={q[0.05]} 25%={q[0.25]} 中央={q[0.5]} 75%={q[0.75]} 95%={q[0.95]}")
print(f"  VI<=20 の日の割合: {(all_df.vi<=20).mean()*100:.1f}%")
for lo, hi in [(13, 17), (17, 20), (20, 24), (24, 30), (30, 99)]:
    sub = all_df[(all_df.vi >= lo) & (all_df.vi < hi)]
    if len(sub) > 50:
        print(f"  VI {lo}-{hi}: 10日標準偏差 中央={sub.vi_std_10.median():.2f} "
              f"(25%={sub.vi_std_10.quantile(.25):.2f} 75%={sub.vi_std_10.quantile(.75):.2f}), "
              f"CV中央={sub.vi_cv_10.median():.3f}, SD<=1.5の割合={100*(sub.vi_std_10<=1.5).mean():.0f}%")
print()

# --- A: サブ条件ごとの通過率 ---
print("=" * 100)
print("[1] Gate① サブ条件ごとの通過率（全営業日ベース）")
print("=" * 100)
c1 = all_df.vi <= 20
c2 = all_df.vi_ma_10 <= 20
c3 = all_df.vi_std_10 <= 1.5
c4 = all_df.vi_slope_10 <= 0.1
print(f"  ① VI      <= 20  : {c1.mean()*100:6.2f}%")
print(f"  ② VI_MA10 <= 20  : {c2.mean()*100:6.2f}%")
print(f"  ③ VI_SD10 <= 1.5 : {c3.mean()*100:6.2f}%   （①②成立下では {c3[c1&c2].mean()*100:.1f}%）")
print(f"  ④ 傾き10  <= 0.1 : {c4.mean()*100:6.2f}%   （①②③成立下では {c4[c1&c2&c3].mean()*100:.1f}%）")
print(f"  → 4条件すべて     : {(c1&c2&c3&c4).mean()*100:6.2f}%")
print()
print("  条件を1つずつ外した場合（どれがボトルネックか）:")
full = c1 & c2 & c3 & c4
for name, drop in [("①VI水準を外す", c2 & c3 & c4), ("②MA10を外す", c1 & c3 & c4),
                   ("③SD10を外す", c1 & c2 & c4), ("④傾きを外す", c1 & c2 & c3)]:
    print(f"    {name:<16}: {drop.mean()*100:6.2f}%  (現行 {full.mean()*100:.2f}% の {drop.mean()/max(full.mean(),1e-9):.1f}倍)")
print()

# --- A': 各バリアントの通過率 & 最終エントリー頻度 ---
print("=" * 100)
print("[2] バリアント別: Gate①通過率 / 最終エントリー頻度（年あたり）")
print("=" * 100)
variants = vi_gate_variants(all_df)
ab_trig = all_df.a & all_df.b & all_df.trigger
sd_trig = (all_df.b_count >= 2) & all_df.trigger      # 需給主導型
probe = all_df.a & all_df.b & ~all_df.trigger          # 打診
years = len(all_df) / 252
rows = []
for name, g in variants.items():
    rows.append({
        "バリアント": name,
        "Gate①通過率%": round(g.mean() * 100, 2),
        "確認エントリー/年": round((g & ab_trig).sum() / years, 2),
        "需給主導/年": round((g & sd_trig).sum() / years, 2),
        "打診/年": round((g & probe).sum() / years, 2),
        "合計発火/年": round((g & (ab_trig | sd_trig | probe)).sum() / years, 2),
    })
print(pd.DataFrame(rows).to_string(index=False))
print()

# --- B: 予測力 ---
print("=" * 100)
print("[3] 予測力: Gate①成立日の「その後」の日経リターン（%）")
print("=" * 100)
rows = []
for name, g in variants.items():
    sub = all_df[g & ab_trig]
    if len(sub) < 20:
        rows.append({"バリアント": name, "N": len(sub)})
        continue
    rows.append({
        "バリアント": name,
        "N": len(sub),
        "5日平均": round(sub.fwd5.mean(), 2),
        "10日平均": round(sub.fwd10.mean(), 2),
        "20日平均": round(sub.fwd20.mean(), 2),
        "20日MDD平均": round(sub.fwd20_mdd.mean(), 2),
        "20日で-5%超%": round((sub.fwd20_mdd <= -5).mean() * 100, 1),
        "20日で-8%超%": round((sub.fwd20_mdd <= -8).mean() * 100, 1),
    })
base = all_df
rows.append({
    "バリアント": "（参考）全営業日",
    "N": len(base),
    "5日平均": round(base.fwd5.mean(), 2),
    "10日平均": round(base.fwd10.mean(), 2),
    "20日平均": round(base.fwd20.mean(), 2),
    "20日MDD平均": round(base.fwd20_mdd.mean(), 2),
    "20日で-5%超%": round((base.fwd20_mdd <= -5).mean() * 100, 1),
    "20日で-8%超%": round((base.fwd20_mdd <= -8).mean() * 100, 1),
})
print(pd.DataFrame(rows).to_string(index=False))
print()

# --- B': VI水準帯ごとの「その後の急落確率」 ---
print("=" * 100)
print("[4] エントリー条件(A&B&Trigger)成立日を、エントリー時VI水準で層別")
print("=" * 100)
sub = all_df[ab_trig].copy()
sub["vi_bin"] = pd.cut(sub.vi, [0, 16, 18, 20, 22, 25, 30, 100])
g = sub.groupby("vi_bin", observed=True).agg(
    N=("vi", "size"), 平均VI=("vi", "mean"),
    fwd10平均=("fwd10", "mean"), fwd20平均=("fwd20", "mean"),
    MDD20平均=("fwd20_mdd", "mean"),
    急落5pct=("fwd20_mdd", lambda x: (x <= -5).mean() * 100),
    急落8pct=("fwd20_mdd", lambda x: (x <= -8).mean() * 100),
).round(2)
print(g.to_string())
print()

print("=" * 100)
print("[5] 同じくVIの相対水準（1年パーセンタイル）で層別")
print("=" * 100)
sub["pct_bin"] = pd.cut(sub.vi_pct_1y, [0, 20, 40, 60, 80, 100])
g = sub.groupby("pct_bin", observed=True).agg(
    N=("vi", "size"), 平均VI=("vi", "mean"),
    fwd10平均=("fwd10", "mean"), fwd20平均=("fwd20", "mean"),
    MDD20平均=("fwd20_mdd", "mean"),
    急落5pct=("fwd20_mdd", lambda x: (x <= -5).mean() * 100),
    急落8pct=("fwd20_mdd", lambda x: (x <= -8).mean() * 100),
).round(2)
print(g.to_string())

print()
print("=" * 100)
print("[6] レジーム別（暦年ごとのVI平均で層別）: 年間の発火回数")
print("=" * 100)
all_df["year"] = pd.to_datetime(all_df["date"]).dt.year
yr = all_df.groupby(["path", "year"])
recs = []
for (p_, y_), sub in yr:
    if len(sub) < 200:
        continue
    vg = vi_gate_variants(sub)
    ab = sub.a & sub.b & sub.trigger
    sd = (sub.b_count >= 2) & sub.trigger
    rec = {"vi_year_mean": sub.vi.mean()}
    for name, g in vg.items():
        rec[name] = int((g & (ab | sd)).sum())
    recs.append(rec)
rdf = pd.DataFrame(recs)
rdf["レジーム"] = pd.cut(rdf.vi_year_mean, [0, 18, 21, 24, 100],
                     labels=["静穏(年平均VI<18)", "平常(18-21)", "やや高VI(21-24)", "高VI(>24)"])
agg = rdf.groupby("レジーム", observed=True).agg(
    年数=("vi_year_mean", "size"), 年平均VI=("vi_year_mean", "mean"),
    **{k: (k, "mean") for k in vi_gate_variants(all_df).keys()}
).round(2)
print(agg.T.to_string())
print()
print("  ※ 各レジームで『年間に1回も発火しなかった年』の割合:")
for k in vi_gate_variants(all_df).keys():
    z = rdf.groupby("レジーム", observed=True)[k].apply(lambda x: (x == 0).mean() * 100).round(0)
    print(f"    {k:<42} " + "  ".join(f"{lbl}:{v:.0f}%" for lbl, v in z.items()))

all_df.to_pickle(Path(__file__).parent / "base_frames.pkl")
print("\n保存: base_frames.pkl")
