"""市場データ（日経平均、VI等）の取得"""
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional
import pandas as pd

from ..models.option import MarketData
from .nikkei_vi import NikkeiVIFetcher
from .nikkei_225 import Nikkei225Fetcher


class MarketDataFetcher:
    """市場データの取得"""

    def __init__(self):
        self.nikkei_vi_fetcher = NikkeiVIFetcher()  # 日経VI取得
        self.nikkei_225_fetcher = Nikkei225Fetcher()  # 日経平均取得

    def fetch_nikkei_data(
        self, start_date: date, end_date: Optional[date] = None
    ) -> List[MarketData]:
        """
        日経平均のデータを取得（日経公式サイト/Investing.comから取得）

        Args:
            start_date: 開始日
            end_date: 終了日（Noneの場合は今日）

        Returns:
            MarketDataのリスト
        """
        if end_date is None:
            end_date = date.today()

        try:
            market_data = self.nikkei_225_fetcher.fetch_nikkei_data(start_date, end_date)

            if market_data:
                print(f"✅ 取得完了: {len(market_data)} 件")
                return market_data
            else:
                print("⚠️ 日経平均データが取得できませんでした")
                return []

        except Exception as e:
            print(f"❌ 日経平均データ取得エラー: {e}")
            import traceback
            traceback.print_exc()
            raise

    def fetch_vi_bars(
        self, start_date: date, end_date: Optional[date] = None, market_data: Optional[List[MarketData]] = None
    ) -> Dict[date, Dict[str, float]]:
        """
        日経VIの四本値を取得（優先順位：日経公式→20日ボラティリティ計算）

        20日ボラへのフォールバック時は終値（"close"）だけが入る。

        Args:
            start_date: 開始日
            end_date: 終了日（Noneの場合は今日）
            market_data: 既に取得した日経平均データ（20日ボラ計算用）

        Returns:
            {date: {"close": .., "open": .., "high": .., "low": ..}} の辞書
        """
        if end_date is None:
            end_date = date.today()

        print(f"日経VIデータ取得: {start_date} 〜 {end_date}")

        # 優先順位1: 日経公式サイトから取得
        try:
            print("📊 日経公式サイトからVI取得を試行...")
            bars = self.nikkei_vi_fetcher.fetch_vi_bars(start_date, end_date)
            if bars:
                print(f"✅ 日経公式VI取得成功: {len(bars)} 件")
                return bars
            print("⚠️ 日経公式VI: 空のデータが返されました")
        except Exception as e:
            print(f"⚠️ 日経公式VI取得失敗: {e}")

        # 優先順位2: 日経平均の20日ボラティリティを計算（終値のみ）
        print("📊 20日ボラティリティ計算にフォールバック...")
        if not market_data:
            print("⚠️ 日経平均データがないため、日経平均データを取得して再試行...")
            try:
                market_data = self.fetch_nikkei_data(start_date, end_date)
            except Exception as e:
                print(f"⚠️ 日経平均データ取得・ボラティリティ計算失敗: {e}")
                market_data = []

        if market_data:
            vi_dict = self.nikkei_225_fetcher.calculate_20day_volatility(market_data)
            if vi_dict:
                print(f"✅ 20日ボラティリティ計算成功: {len(vi_dict)} 件")
                return {d: {"close": v} for d, v in vi_dict.items()}

        print("❌ VIデータが取得できませんでした")
        return {}

    def fetch_vi_data(
        self, start_date: date, end_date: Optional[date] = None, market_data: Optional[List[MarketData]] = None
    ) -> Dict[date, float]:
        """日経VIの終値を取得（互換用。四本値が要る場合は fetch_vi_bars）。"""
        bars = self.fetch_vi_bars(start_date, end_date, market_data=market_data)
        return {d: bar["close"] for d, bar in bars.items()}

    def fetch_market_data_with_vi(
        self, start_date: date, end_date: Optional[date] = None
    ) -> List[MarketData]:
        """
        日経平均とVIを統合したデータを取得

        Args:
            start_date: 開始日
            end_date: 終了日

        Returns:
            VI付きMarketDataのリスト
        """
        # 日経平均データを取得
        market_data = self.fetch_nikkei_data(start_date, end_date)

        # VIデータを取得（日経平均データを渡して20日ボラ計算に利用）
        vi_bars = self.fetch_vi_bars(start_date, end_date, market_data=market_data)

        # VIをマージ（始値・高値・安値は Gate① の判定値の算出に使う）
        for data in market_data:
            bar = vi_bars.get(data.date)
            if bar:
                data.vi = bar["close"]
                data.vi_open = bar.get("open")
                data.vi_high = bar.get("high")
                data.vi_low = bar.get("low")

        vi_count = sum(1 for d in market_data if d.vi is not None)
        ohlc_count = sum(1 for d in market_data if d.vi_open is not None)
        print(f"VI付きデータ: {vi_count} / {len(market_data)}（四本値あり {ohlc_count}）")

        return market_data

    def fetch_latest_market_data(self) -> Optional[MarketData]:
        """最新の市場データを取得"""
        end_date = date.today()
        start_date = end_date - timedelta(days=7)  # 過去1週間分取得

        data_list = self.fetch_market_data_with_vi(start_date, end_date)

        if data_list:
            return data_list[-1]  # 最新
        return None


class CSVMarketDataFetcher:
    """CSVファイルから市場データを読み込む（バックテスト用）"""

    def __init__(self, csv_path: str):
        self.csv_path = csv_path

    def load_data(self) -> List[MarketData]:
        """CSVからデータを読み込む"""
        try:
            df = pd.read_csv(self.csv_path)

            # 必須カラムの確認
            required_cols = ["date", "open", "high", "low", "close"]
            for col in required_cols:
                if col not in df.columns:
                    raise ValueError(f"必須カラムがありません: {col}")

            market_data = []
            for _, row in df.iterrows():
                data = MarketData(
                    date=datetime.strptime(str(row["date"]), "%Y-%m-%d").date(),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=int(row["volume"]) if "volume" in row and pd.notna(row["volume"]) else None,
                    vi=float(row["vi"]) if "vi" in row and pd.notna(row["vi"]) else None,
                )
                market_data.append(data)

            print(f"CSVから読み込み完了: {len(market_data)} 件")
            return market_data

        except Exception as e:
            print(f"CSV読み込みエラー: {e}")
            raise


def test_market_data_fetch():
    """テスト用関数"""
    fetcher = MarketDataFetcher()

    # 過去1ヶ月のデータを取得
    end_date = date.today()
    start_date = end_date - timedelta(days=30)

    try:
        data = fetcher.fetch_market_data_with_vi(start_date, end_date)

        if data:
            print(f"\n取得成功！ {len(data)} 件")
            print("\n最新の5件:")
            for d in data[-5:]:
                vi_str = f"{d.vi:.2f}" if d.vi else "N/A"
                print(
                    f"  {d.date} | Close={d.close:.2f} | VI={vi_str} | Vol={d.volume}"
                )
        else:
            print("データが取得できませんでした")

    except Exception as e:
        print(f"エラー: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    test_market_data_fetch()
