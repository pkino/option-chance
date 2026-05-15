"""ボトルネック分析: 各条件を単独で緩和した場合の検知数の変化"""
import sys
from pathlib import Path
import copy

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import yaml
from scripts.backtest_detection_analysis import generate_realistic_nikkei_data, analyze_signals


def load_config():
    with open(project_root / "config" / "config.yaml") as f:
        return yaml.safe_load(f)


def run_with_config(cfg, data):
    df = analyze_signals(data, cfg)
    total = len(df)
    years = total / 250
    entry = (df["is_entry"] & ~df["cooldown_suppressed"]).sum()
    probe = df["is_probe"].sum()
    a1 = df["a1_rsi"].sum()
    a2 = df["a2_macd"].sum()
    a3 = df["a3_div"].sum()
    a4 = df["a4_bb"].sum()
    return {
        "gate_vi_pct": df["gate_vi"].mean() * 100,
        "gate_a_pct": df["gate_top_a"].mean() * 100,
        "gate_b_pct": df["gate_top_b"].mean() * 100,
        "trig_pct": df["trigger"].mean() * 100,
        "entry": entry,
        "entry_py": entry / years,
        "probe": probe,
        "a1": a1, "a2": a2, "a3": a3, "a4": a4,
    }


def main():
    base_config = load_config()
    data = generate_realistic_nikkei_data(5, seed=42)

    print("=" * 80)
    print("  ボトルネック分析: 各条件を1つずつ緩和した場合の効果 (seed=42, 5年)")
    print("=" * 80)

    base = run_with_config(base_config, data)
    header = "設定変更内容                                G1%   G2A%  G2B%  Entry  /yr  Probe"
    print(header)
    print("-" * len(header))
    print("[BASE] 現在の設定                              %5.1f %5.1f %5.1f   %3d  %4.2f    %d" % (
        base["gate_vi_pct"], base["gate_a_pct"], base["gate_b_pct"],
        base["entry"], base["entry_py"], base["probe"]
    ))
    print()

    scenarios = []

    # --- Gate① ---
    c = copy.deepcopy(base_config)
    c["gate_vi"]["vi_threshold"] = 25
    c["gate_vi"]["vi_10d_avg_threshold"] = 25
    scenarios.append(("Gate1: VI threshold 20->25", c))

    c = copy.deepcopy(base_config)
    c["gate_vi"]["vi_10d_slope_threshold"] = 0.1
    scenarios.append(("Gate1: VI slope <=0 -> <=0.1", c))

    c = copy.deepcopy(base_config)
    c["gate_vi"]["vi_10d_slope_threshold"] = 0.5
    scenarios.append(("Gate1: VI slope <=0 -> <=0.5 (大幅緩和)", c))

    c = copy.deepcopy(base_config)
    c["gate_vi"]["vi_10d_std_threshold"] = 2.0
    scenarios.append(("Gate1: VI std 1.5 -> 2.0", c))

    c = copy.deepcopy(base_config)
    c["gate_vi"]["vi_10d_std_threshold"] = 3.0
    scenarios.append(("Gate1: VI std 1.5 -> 3.0 (大幅緩和)", c))

    # --- Gate②A ---
    c = copy.deepcopy(base_config)
    c["gate_top"]["technical"]["required_conditions"] = 1
    scenarios.append(("Gate2A: 必要条件数 2->1 (OR条件)", c))

    c = copy.deepcopy(base_config)
    c["gate_top"]["technical"]["rsi"]["overbought"] = 65
    scenarios.append(("A1: RSI過熱閾値 70->65", c))

    c = copy.deepcopy(base_config)
    c["gate_top"]["technical"]["rsi"]["lookback_days"] = 10
    scenarios.append(("A1: RSI遡及期間 5d->10d", c))

    c = copy.deepcopy(base_config)
    c["gate_top"]["technical"]["rsi"]["consecutive_decline"] = 1
    scenarios.append(("A1: RSI連続低下 2d->1d", c))

    c = copy.deepcopy(base_config)
    c["gate_top"]["technical"]["macd"]["hist_consecutive_decline"] = 2
    scenarios.append(("A2: MACDヒスト縮小 3d->2d", c))

    c = copy.deepcopy(base_config)
    c["gate_top"]["technical"]["macd"]["macd_positive"] = False
    scenarios.append(("A2: MACD>0条件を撤廃", c))

    # --- Gate②B ---
    c = copy.deepcopy(base_config)
    c["gate_top"]["supply_demand"]["upper_wick"]["wick_ratio"] = 0.3
    scenarios.append(("B2: 上ヒゲ比率 0.4->0.3", c))

    c = copy.deepcopy(base_config)
    c["gate_top"]["supply_demand"]["volume_failure"]["ratio_threshold"] = 0.90
    scenarios.append(("B1: 出来高比閾値 0.85->0.90", c))

    # --- Combined ---
    c = copy.deepcopy(base_config)
    c["gate_vi"]["vi_10d_slope_threshold"] = 0.1
    c["gate_vi"]["vi_10d_std_threshold"] = 2.0
    c["gate_top"]["technical"]["required_conditions"] = 1
    scenarios.append(("【複合】slope+std緩和 & A2->1条件", c))

    c = copy.deepcopy(base_config)
    c["gate_vi"]["vi_10d_slope_threshold"] = 0.5
    c["gate_top"]["technical"]["required_conditions"] = 1
    scenarios.append(("【複合】slope大幅緩和 & A2->1条件", c))

    c = copy.deepcopy(base_config)
    c["gate_vi"]["vi_threshold"] = 25
    c["gate_vi"]["vi_10d_avg_threshold"] = 25
    c["gate_vi"]["vi_10d_slope_threshold"] = 0.1
    c["gate_vi"]["vi_10d_std_threshold"] = 2.0
    c["gate_top"]["technical"]["required_conditions"] = 1
    scenarios.append(("【複合】VI緩和+slope+std+A2->1", c))

    print("Gate①条件の変更:")
    gate1_scenarios = scenarios[:5]
    for name, cfg in gate1_scenarios:
        r = run_with_config(cfg, data)
        d = r["entry"] - base["entry"]
        diff_str = ("+%d" % d) if d > 0 else str(d)
        print("  %-43s %5.1f %5.1f %5.1f   %3d  %4.2f    %d  (%s)" % (
            name, r["gate_vi_pct"], r["gate_a_pct"], r["gate_b_pct"],
            r["entry"], r["entry_py"], r["probe"], diff_str
        ))

    print()
    print("Gate②A条件の変更:")
    gate2a_scenarios = scenarios[5:11]
    for name, cfg in gate2a_scenarios:
        r = run_with_config(cfg, data)
        d = r["entry"] - base["entry"]
        diff_str = ("+%d" % d) if d > 0 else str(d)
        print("  %-43s %5.1f %5.1f %5.1f   %3d  %4.2f    %d  (%s)" % (
            name, r["gate_vi_pct"], r["gate_a_pct"], r["gate_b_pct"],
            r["entry"], r["entry_py"], r["probe"], diff_str
        ))

    print()
    print("Gate②B条件の変更:")
    gate2b_scenarios = scenarios[11:13]
    for name, cfg in gate2b_scenarios:
        r = run_with_config(cfg, data)
        d = r["entry"] - base["entry"]
        diff_str = ("+%d" % d) if d > 0 else str(d)
        print("  %-43s %5.1f %5.1f %5.1f   %3d  %4.2f    %d  (%s)" % (
            name, r["gate_vi_pct"], r["gate_a_pct"], r["gate_b_pct"],
            r["entry"], r["entry_py"], r["probe"], diff_str
        ))

    print()
    print("複合変更 (複数条件を同時に緩和):")
    combined_scenarios = scenarios[13:]
    for name, cfg in combined_scenarios:
        r = run_with_config(cfg, data)
        d = r["entry"] - base["entry"]
        diff_str = ("+%d" % d) if d > 0 else str(d)
        print("  %-43s %5.1f %5.1f %5.1f   %3d  %4.2f    %d  (%s)" % (
            name, r["gate_vi_pct"], r["gate_a_pct"], r["gate_b_pct"],
            r["entry"], r["entry_py"], r["probe"], diff_str
        ))

    print()
    print("=" * 80)
    print("【主要な発見事項】")
    print()
    print("1. 最大のボトルネック: Gate①×Gate②Aの同時成立が極めて稀")
    print("   - 理由: 低VIの静穏期(Gate①通過)にはRSI/MACDの天井シグナル(Gate②A)が出にくい")
    print("   - Gate①は27%通過するが、その中でGate②Aが成立するのは約3-4%")
    print()
    print("2. Gate②A内の問題: A3ダイバージェンスが0件")
    print("   - 実装では close >= high_20 (20日高値更新) が必要")
    print("   - その上でRSIが過去20日最高値を下回る必要がある")
    print("   - 20日高値更新日はRSIも通常高いため、ほぼ発動しない")
    print()
    print("3. 最も効果的な緩和策: Gate②Aの必要条件数を2→1に下げること")
    print("   - A2(MACD弱化)単体で25%成立するため、これだけで大幅に改善")
    print("   - ただしノイズが増える可能性あり")
    print()
    print("4. VI slope条件（<=0）も大きなボトルネック")
    print("   - VIが10日間下落傾向でなければならず、多くの静穏期を除外している")
    print("   - 0.1-0.5程度に緩和することで Gate①通過率が大幅改善")


if __name__ == "__main__":
    main()
