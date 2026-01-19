"""日次エントリーチェック（GHA用メインスクリプト）"""
import sys
from pathlib import Path
from datetime import date, timedelta
from typing import List, Optional
import argparse

# プロジェクトルートをパスに追加
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import yaml
from src.models.option import OptionData, MarketData
from src.data_sources.jpx import JPXDataFetcher
from src.data_sources.market_data import MarketDataFetcher
from src.signals.gate import GateChecker, format_signal_for_notification
from src.notifiers.slack import SlackNotifier


class DailyEntryChecker:
    """日次エントリーチェッカー"""

    def __init__(self, config_path: str = "config/config.yaml"):
        """
        Args:
            config_path: 設定ファイルのパス
        """
        # 設定を読み込み
        config_file = project_root / config_path
        with open(config_file) as f:
            self.config = yaml.safe_load(f)

        # データフェッチャーを初期化
        jpx_config = self.config["data_sources"]["jpx"]
        self.jpx_fetcher = JPXDataFetcher(
            base_url=jpx_config["base_url"], timeout=jpx_config["timeout"]
        )

        self.market_fetcher = MarketDataFetcher()

        # Gate チェッカーを初期化
        self.gate_checker = GateChecker(self.config)

        # Slack 通知を初期化（オプション）
        self.slack_notifier = None
        if self.config.get("notifications", {}).get("slack", {}).get("enabled"):
            try:
                self.slack_notifier = SlackNotifier()
                print("✅ Slack通知が有効です")
            except Exception as e:
                print(f"⚠️ Slack通知の初期化に失敗: {e}")

    def run(self, lookback_days: int = 60, send_no_signal: bool = False) -> bool:
        """
        日次チェックを実行

        Args:
            lookback_days: 過去何日分のデータを取得するか
            send_no_signal: シグナルがない場合もSlackに送信するか

        Returns:
            エントリーシグナルが検出されたか
        """
        print("=" * 70)
        print("日次エントリーチェック開始")
        print("=" * 70)

        try:
            # 1. 市場データを取得
            print("\n[1/4] 市場データ取得中...")
            market_data = self._fetch_market_data(lookback_days)

            if not market_data:
                raise ValueError("市場データが取得できませんでした")

            print(f"  取得完了: {len(market_data)} 件")
            print(f"  最新日付: {market_data[-1].date}")

            # 2. Gateチェック
            print("\n[2/4] Gate条件チェック中...")
            signals = self.gate_checker.check_all_gates(market_data)
            print(f"  総シグナル数: {len(signals)}")

            # 最新のシグナルのみ取得
            if signals:
                latest_signal = signals[-1]
                print(f"  最新シグナル日付: {latest_signal.date}")
            else:
                print("  シグナルなし")
                if self.slack_notifier and send_no_signal:
                    self.slack_notifier.send_no_signal(str(date.today()))
                return False

            # 3. エントリーシグナルをチェック
            print("\n[3/4] エントリーシグナルチェック中...")
            if latest_signal.is_entry_signal:
                print("  ✅ エントリーシグナル検出！")

                # 4. オプション候補を取得
                print("\n[4/4] オプション候補を取得中...")
                option_candidates = self._fetch_option_candidates()

                # 結果を表示
                self._display_results(latest_signal, option_candidates)

                # Slack通知
                if self.slack_notifier:
                    self._send_slack_notification(latest_signal, option_candidates)

                return True
            else:
                print("  ⚠️ エントリーシグナルなし")
                print(f"    - Gate① (VI): {latest_signal.gate_vi}")
                print(f"    - Gate② (A): {latest_signal.gate_top_a}")
                print(f"    - Gate② (B): {latest_signal.gate_top_b}")
                print(f"    - Trigger③: {latest_signal.trigger}")
                print("\n  詳細:")
                print(format_signal_for_notification(latest_signal))

                if self.slack_notifier and send_no_signal:
                    self.slack_notifier.send_no_signal(str(latest_signal.date))

                return False

        except Exception as e:
            print(f"\n❌ エラーが発生しました: {e}")
            import traceback

            traceback.print_exc()

            # エラーをSlackに通知
            if self.slack_notifier:
                self.slack_notifier.send_error(str(e))

            return False

    def _fetch_market_data(self, lookback_days: int) -> List[MarketData]:
        """市場データを取得"""
        end_date = date.today()
        start_date = end_date - timedelta(days=lookback_days)

        return self.market_fetcher.fetch_market_data_with_vi(start_date, end_date)

    def _fetch_option_candidates(self) -> List[dict]:
        """オプション候補を取得"""
        try:
            # JPXから最新のオプションデータを取得
            options = self.jpx_fetcher.fetch_latest_options()

            # Putオプションのみフィルタ
            puts = [o for o in options if o.option_type == "Put"]

            # 条件に合うものをフィルタ
            opt_config = self.config["option_selection"]
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

                # 候補に追加
                candidates.append(
                    {
                        "strike": opt.strike,
                        "premium": opt.premium,
                        "delta": opt.delta,
                        "dte": opt.dte_business_days,
                        "expiry": opt.expiry,
                        "iv": opt.iv,
                        "distance_from_target": opt.delta_distance_from_target(
                            opt_config["target_delta"]
                        ),
                    }
                )

            # 目標デルタに近い順にソート
            candidates.sort(key=lambda x: x.get("distance_from_target", 999))

            print(f"  候補オプション: {len(candidates)} 件")
            return candidates

        except Exception as e:
            print(f"  ⚠️ オプションデータ取得エラー: {e}")
            return []

    def _display_results(self, signal, option_candidates: List[dict]):
        """結果を表示"""
        print("\n" + "=" * 70)
        print(format_signal_for_notification(signal))
        print("=" * 70)

        if option_candidates:
            print("\n推奨オプション候補（上位5つ）:")
            for i, opt in enumerate(option_candidates[:5], 1):
                delta_str = f"{opt['delta']:.4f}" if opt["delta"] else "N/A"
                iv_str = f"{opt['iv']:.2%}" if opt["iv"] else "N/A"
                print(
                    f"  {i}. Strike={opt['strike']} | Premium={opt['premium']:.2f}円 | "
                    f"Delta={delta_str} | IV={iv_str} | DTE={opt['dte']}日 | Expiry={opt['expiry']}"
                )

    def _send_slack_notification(self, signal, option_candidates: List[dict]):
        """Slack通知を送信"""
        try:
            signal_text = format_signal_for_notification(signal)
            self.slack_notifier.send_entry_signal(signal_text, option_candidates)
        except Exception as e:
            print(f"  ⚠️ Slack通知エラー: {e}")


def main():
    """メイン処理"""
    parser = argparse.ArgumentParser(description="日次エントリーチェック")
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=60,
        help="過去何日分のデータを取得するか（デフォルト: 60）",
    )
    parser.add_argument(
        "--send-no-signal",
        action="store_true",
        help="シグナルがない場合もSlackに通知する",
    )
    parser.add_argument(
        "--config", type=str, default="config/config.yaml", help="設定ファイルのパス"
    )

    args = parser.parse_args()

    # チェッカーを初期化
    checker = DailyEntryChecker(config_path=args.config)

    # 実行
    try:
        has_signal = checker.run(lookback_days=args.lookback_days, send_no_signal=args.send_no_signal)

        # 正常終了（シグナルの有無に関わらず成功）
        # エントリーシグナルがない日が大半なので、これは正常な状態
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ 実行エラー: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
