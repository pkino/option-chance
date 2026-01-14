"""バックテストエンジン"""
from typing import List, Dict, Any, Optional
from datetime import date, timedelta
from dataclasses import asdict
import pandas as pd

from ..models.option import MarketData, OptionData, Signal, Trade
from ..signals.gate import GateChecker


class BacktestEngine:
    """バックテストエンジン"""

    def __init__(self, config: Dict[str, Any]):
        """
        Args:
            config: 設定辞書
        """
        self.config = config
        self.gate_checker = GateChecker(config)

        # 結果格納
        self.trades: List[Trade] = []
        self.signals: List[Signal] = []

    def run(
        self,
        market_data: List[MarketData],
        options_data: Dict[date, List[OptionData]],
        events: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        バックテストを実行

        Args:
            market_data: 市場データのリスト
            options_data: {date: [OptionData]} の辞書
            events: イベント日付のリスト

        Returns:
            バックテスト結果の辞書
        """
        print("バックテスト開始...")

        # 1. シグナルを生成
        print("シグナル生成中...")
        self.signals = self.gate_checker.check_all_gates(market_data, events)
        entry_signals = [s for s in self.signals if s.is_entry_signal]

        print(f"総シグナル数: {len(self.signals)}")
        print(f"エントリーシグナル数: {len(entry_signals)}")

        # 2. 各エントリーシグナルに対してトレードをシミュレート
        print("トレードシミュレーション中...")
        market_data_dict = {d.date: d for d in market_data}

        for signal in entry_signals:
            trade = self._simulate_trade(signal, options_data, market_data_dict)
            if trade:
                self.trades.append(trade)

        print(f"トレード数: {len(self.trades)}")

        # 3. 結果を集計
        results = self._calculate_results()

        return results

    def _simulate_trade(
        self,
        signal: Signal,
        options_data: Dict[date, List[OptionData]],
        market_data_dict: Dict[date, MarketData],
    ) -> Optional[Trade]:
        """
        1つのトレードをシミュレート

        Args:
            signal: エントリーシグナル
            options_data: オプションデータ
            market_data_dict: 市場データ辞書

        Returns:
            Trade or None
        """
        entry_date = signal.date

        # エントリー日のオプションデータを取得
        if entry_date not in options_data:
            return None

        daily_options = options_data[entry_date]

        # 条件に合うオプションを選択
        selected_option = self._select_option(daily_options)

        if not selected_option:
            return None

        # エントリー価格（ask）
        premium_entry = selected_option.ask or selected_option.premium
        if not premium_entry:
            return None

        # 退出をシミュレート
        exit_date, premium_exit, exit_reason = self._simulate_exit(
            entry_date, selected_option, options_data, market_data_dict
        )

        # トレード記録を作成
        trade = Trade(
            entry_date=entry_date,
            exit_date=exit_date,
            option_id=f"{selected_option.strike}P{selected_option.expiry}",
            strike=selected_option.strike,
            expiry=selected_option.expiry,
            dte_entry=selected_option.dte_business_days or 0,
            premium_entry=premium_entry,
            delta_entry=selected_option.delta,
            premium_exit=premium_exit,
            exit_reason=exit_reason,
        )

        trade.calculate_pnl()

        return trade

    def _select_option(self, options: List[OptionData]) -> Optional[OptionData]:
        """
        条件に合うオプションを選択

        Args:
            options: オプションデータのリスト

        Returns:
            選択されたオプション or None
        """
        opt_config = self.config["option_selection"]

        # Putのみ
        puts = [o for o in options if o.option_type == "Put"]

        # フィルタリング
        candidates = []
        for opt in puts:
            # プレミアム範囲
            if not opt.is_in_premium_range(
                opt_config["premium_range"]["min"], opt_config["premium_range"]["max"]
            ):
                continue

            # DTE範囲
            if not opt.is_in_dte_range(
                opt_config["dte_range"]["min"], opt_config["dte_range"]["max"]
            ):
                continue

            # デルタ範囲
            if opt.delta and not opt.is_in_delta_range(
                opt_config["delta_range"]["min"], opt_config["delta_range"]["max"]
            ):
                continue

            candidates.append(opt)

        if not candidates:
            return None

        # 目標デルタに最も近いものを選択
        target_delta = opt_config["target_delta"]
        candidates.sort(key=lambda x: x.delta_distance_from_target(target_delta) or 999)

        return candidates[0]

    def _simulate_exit(
        self,
        entry_date: date,
        option: OptionData,
        options_data: Dict[date, List[OptionData]],
        market_data_dict: Dict[date, MarketData],
    ) -> tuple[date, float, str]:
        """
        退出をシミュレート

        Args:
            entry_date: エントリー日
            option: オプション
            options_data: オプションデータ
            market_data_dict: 市場データ辞書

        Returns:
            (exit_date, premium_exit, exit_reason)
        """
        exit_config = self.config["exit_rules"]
        time_stop_days = exit_config["stop_loss"]["time_based_days"]

        # 時間損切り日
        time_stop_date = entry_date + timedelta(days=time_stop_days)

        # 現在の最高価格（2倍利確用）
        entry_premium = option.premium or 0
        max_premium = entry_premium
        double_target = entry_premium * 2

        # 日次でチェック
        current_date = entry_date + timedelta(days=1)
        half_closed = False

        while current_date <= time_stop_date and current_date <= option.expiry:
            # その日のオプション価格を取得
            if current_date not in options_data:
                current_date += timedelta(days=1)
                continue

            daily_options = options_data[current_date]

            # 同じオプションを探す
            current_option = None
            for opt in daily_options:
                if (
                    opt.option_type == option.option_type
                    and opt.strike == option.strike
                    and opt.expiry == option.expiry
                ):
                    current_option = opt
                    break

            if not current_option:
                current_date += timedelta(days=1)
                continue

            current_premium = current_option.bid or current_option.premium

            if not current_premium:
                current_date += timedelta(days=1)
                continue

            # 最高価格を更新
            if current_premium > max_premium:
                max_premium = current_premium

            # 2倍到達チェック
            if not half_closed and current_premium >= double_target:
                # 50%利確（簡易実装：残りを保有継続）
                half_closed = True
                # 実際のバックテストでは、ここでポジションサイズを半分にする

            # 時間損切りに到達
            if current_date >= time_stop_date:
                return current_date, current_premium, "time_stop"

            current_date += timedelta(days=1)

        # 満期または時間切れ
        # 最終価格を取得
        exit_date = min(time_stop_date, option.expiry)
        if exit_date in options_data:
            daily_options = options_data[exit_date]
            for opt in daily_options:
                if (
                    opt.option_type == option.option_type
                    and opt.strike == option.strike
                    and opt.expiry == option.expiry
                ):
                    exit_premium = opt.bid or opt.premium or 0
                    return exit_date, exit_premium, "time_stop"

        # データがない場合は0とする
        return exit_date, 0, "time_stop"

    def _calculate_results(self) -> Dict[str, Any]:
        """
        バックテスト結果を集計

        Returns:
            結果の辞書
        """
        if not self.trades:
            return {
                "summary": {"total_trades": 0},
                "trades": [],
            }

        # DataFrameに変換
        trades_df = pd.DataFrame([asdict(t) for t in self.trades])

        # 勝ち負けを計算
        trades_df["is_win"] = trades_df["pnl"] > 0

        # サマリー統計
        summary = {
            "total_trades": len(trades_df),
            "win_trades": trades_df["is_win"].sum(),
            "lose_trades": (~trades_df["is_win"]).sum(),
            "win_rate": trades_df["is_win"].mean(),
            "total_pnl": trades_df["pnl"].sum(),
            "avg_pnl": trades_df["pnl"].mean(),
            "median_pnl": trades_df["pnl"].median(),
            "max_pnl": trades_df["pnl"].max(),
            "min_pnl": trades_df["pnl"].min(),
            "avg_pnl_pct": trades_df["pnl_pct"].mean(),
            "median_pnl_pct": trades_df["pnl_pct"].median(),
        }

        # Profit Factor
        total_wins = trades_df[trades_df["pnl"] > 0]["pnl"].sum()
        total_losses = abs(trades_df[trades_df["pnl"] < 0]["pnl"].sum())
        summary["profit_factor"] = total_wins / total_losses if total_losses > 0 else float("inf")

        # 期待値
        summary["expectancy"] = summary["avg_pnl"]

        return {
            "summary": summary,
            "trades": trades_df.to_dict("records"),
        }

    def save_results(self, output_dir: str = "output"):
        """
        結果をファイルに保存

        Args:
            output_dir: 出力ディレクトリ
        """
        from pathlib import Path
        import json

        output_path = Path(output_dir)
        output_path.mkdir(exist_ok=True)

        # トレード結果をCSVで保存
        if self.trades:
            trades_df = pd.DataFrame([asdict(t) for t in self.trades])
            trades_df.to_csv(output_path / "trades.csv", index=False)
            print(f"トレード結果を保存: {output_path / 'trades.csv'}")

        # シグナルをCSVで保存
        if self.signals:
            signals_data = []
            for s in self.signals:
                signals_data.append(
                    {
                        "date": s.date,
                        "gate_vi": s.gate_vi,
                        "gate_top_a": s.gate_top_a,
                        "gate_top_b": s.gate_top_b,
                        "gate_top_c": s.gate_top_c,
                        "trigger": s.trigger,
                        "is_entry_signal": s.is_entry_signal,
                    }
                )
            signals_df = pd.DataFrame(signals_data)
            signals_df.to_csv(output_path / "signals.csv", index=False)
            print(f"シグナルを保存: {output_path / 'signals.csv'}")

        # サマリーをJSONで保存
        results = self._calculate_results()
        with open(output_path / "summary.json", "w") as f:
            json.dump(results["summary"], f, indent=2, default=str)
        print(f"サマリーを保存: {output_path / 'summary.json'}")
