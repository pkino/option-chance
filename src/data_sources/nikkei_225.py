"""日経平均株価データの取得（日経公式CSV）"""
import io
import numpy as np
from datetime import datetime, date
from typing import Dict, Optional, List

import pandas as pd
import requests

from ..models.option import MarketData


class Nikkei225Fetcher:
    """日経平均株価データの取得"""

    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.session = requests.Session()
        # User-Agentを設定
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            }
        )
        # 日経公式の日経平均 CSV URL
        self.nikkei_csv_url = "https://indexes.nikkei.co.jp/nkave/historical/nikkei_stock_average_daily_jp.csv"

    def fetch_nikkei_data(
        self, start_date: date, end_date: Optional[date] = None
    ) -> List[MarketData]:
        """
        日経平均のOHLCデータを取得

        Args:
            start_date: 開始日
            end_date: 終了日（Noneの場合は今日）

        Returns:
            MarketDataのリスト
        """
        if end_date is None:
            end_date = date.today()

        print(f"日経平均データ取得: {start_date} 〜 {end_date}")

        try:
            # 日経公式CSVから取得
            data_dict = self._fetch_from_official_csv(start_date, end_date)
            if data_dict:
                return self._convert_to_market_data(data_dict)

            print("⚠️ 日経平均データの取得に失敗しました")
            return []

        except Exception as e:
            print(f"日経平均取得エラー: {e}")
            import traceback
            traceback.print_exc()
            return []

    def _fetch_from_official_csv(
        self, start_date: date, end_date: date
    ) -> Dict[date, Dict[str, float]]:
        """日経公式サイトから日経平均のCSVを直接ダウンロード"""
        try:
            print(f"  日経公式CSV取得試行: {self.nikkei_csv_url}")

            response = self.session.get(self.nikkei_csv_url, timeout=self.timeout)
            response.raise_for_status()
            print(f"  HTTPステータス: {response.status_code}")

            # CSVをパース（Shift-JIS エンコーディング）
            try:
                df = pd.read_csv(io.StringIO(response.content.decode('shift-jis')))
                print(f"  CSVカラム: {df.columns.tolist()}")
                print(f"  データ行数: {len(df)}")

                if len(df) == 0:
                    print("  CSVにデータがありません")
                    return {}

                # 日経公式CSVのカラム名を確認（日付, 始値, 高値, 安値, 終値など）
                data_dict = {}
                for _, row in df.iterrows():
                    try:
                        # 日付をパース
                        date_value = row.iloc[0]  # 最初のカラムが日付
                        if isinstance(date_value, str):
                            # 複数の日付フォーマットを試行
                            for fmt in ["%Y/%m/%d", "%Y-%m-%d", "%Y年%m月%d日"]:
                                try:
                                    parsed_date = datetime.strptime(date_value, fmt).date()
                                    break
                                except:
                                    continue
                            else:
                                continue
                        elif isinstance(date_value, (datetime, pd.Timestamp)):
                            parsed_date = date_value.date() if hasattr(date_value, 'date') else date_value
                        else:
                            continue

                        # 日付範囲チェック
                        if not (start_date <= parsed_date <= end_date):
                            continue

                        # OHLCデータを取得
                        # カラム名が「始値」「高値」「安値」「終値」または位置ベース
                        if '終値' in df.columns:
                            close = float(row['終値'])
                            open_val = float(row['始値']) if '始値' in df.columns and pd.notna(row['始値']) else close
                            high = float(row['高値']) if '高値' in df.columns and pd.notna(row['高値']) else close
                            low = float(row['安値']) if '安値' in df.columns and pd.notna(row['安値']) else close
                        else:
                            # 位置ベース（日付, 始値, 高値, 安値, 終値の順と想定）
                            close = float(row.iloc[4]) if len(row) > 4 else float(row.iloc[1])
                            open_val = float(row.iloc[1]) if len(row) > 1 and pd.notna(row.iloc[1]) else close
                            high = float(row.iloc[2]) if len(row) > 2 and pd.notna(row.iloc[2]) else close
                            low = float(row.iloc[3]) if len(row) > 3 and pd.notna(row.iloc[3]) else close

                        data_dict[parsed_date] = {
                            "close": close,
                            "open": open_val,
                            "high": high,
                            "low": low,
                        }

                    except Exception as e:
                        # 個別の行のパースエラーは無視
                        continue

                if data_dict:
                    print(f"  ✅ 日経公式CSV取得成功: {len(data_dict)} 件")
                    if data_dict:
                        latest_date = max(data_dict.keys())
                        latest_data = data_dict[latest_date]
                        print(f"   最新値（{latest_date}）: 終値={latest_data['close']:.2f}")
                    return data_dict
                else:
                    print("  パース結果が空です")
                    return {}

            except Exception as e:
                print(f"  CSV解析エラー: {e}")
                import traceback
                traceback.print_exc()
                return {}

        except Exception as e:
            print(f"  日経公式CSV取得エラー: {e}")
            import traceback
            traceback.print_exc()
            return {}

    def _convert_to_market_data(self, data_dict: Dict[date, Dict[str, float]]) -> List[MarketData]:
        """辞書形式のデータをMarketDataリストに変換"""
        market_data = []
        for dt, values in sorted(data_dict.items()):
            market_data.append(
                MarketData(
                    date=dt,
                    open=values["open"],
                    high=values["high"],
                    low=values["low"],
                    close=values["close"],
                    volume=None,
                )
            )
        return market_data

    def calculate_20day_volatility(self, market_data: List[MarketData]) -> Dict[date, float]:
        """
        日経平均データから20日ボラティリティを計算（VI代替値として使用）

        Args:
            market_data: 日経平均のMarketDataリスト

        Returns:
            {date: volatility} の辞書（年率化されたボラティリティ）
        """
        if not market_data or len(market_data) < 20:
            print("⚠️ データが不足しています（20日分必要）")
            return {}

        print(f"📊 20日ボラティリティ計算中（{len(market_data)}件のデータ）...")

        # DataFrameに変換
        df = pd.DataFrame([
            {"date": d.date, "close": d.close}
            for d in market_data
        ])
        df = df.sort_values("date").reset_index(drop=True)
        df.set_index("date", inplace=True)

        # 日次リターンを計算
        df["returns"] = df["close"].pct_change()

        # 20日ローリングボラティリティを計算（年率化: sqrt(252)）
        df["volatility"] = df["returns"].rolling(window=20).std() * np.sqrt(252) * 100

        # 辞書に変換（NaNを除外）
        vi_dict = {}
        for dt, vol in df["volatility"].items():
            if not np.isnan(vol):
                vi_dict[dt] = float(vol)

        print(f"✅ 20日ボラティリティ計算完了: {len(vi_dict)} 件")
        if vi_dict:
            latest_date = max(vi_dict.keys())
            print(f"   最新値（{latest_date}）: {vi_dict[latest_date]:.2f}")

        return vi_dict


def test_fetch():
    """テスト用関数"""
    from datetime import timedelta

    fetcher = Nikkei225Fetcher()
    end_date = date.today()
    start_date = end_date - timedelta(days=30)

    try:
        data = fetcher.fetch_nikkei_data(start_date, end_date)
        if data:
            print(f"\n✅ 取得成功: {len(data)} 件")
            print("\n最新5件:")
            for md in data[-5:]:
                print(f"  {md.date}: O={md.open:.2f} H={md.high:.2f} L={md.low:.2f} C={md.close:.2f}")
        else:
            print("\n❌ データ取得失敗")
    except Exception as e:
        print(f"\n❌ エラー: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    test_fetch()
