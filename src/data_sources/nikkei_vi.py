"""日経公式サイトから日経VI（日経平均ボラティリティ・インデックス）を取得"""
import io
from datetime import datetime, date
from typing import Dict, Optional
import pandas as pd
import requests


class NikkeiVIFetcher:
    """日経VIデータの取得"""

    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.session = requests.Session()
        # User-Agentを設定
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            }
        )
        # 日経公式のVI CSV URL
        self.vi_csv_url = "https://indexes.nikkei.co.jp/nkave/historical/nikkei_stock_average_vi_daily_jp.csv"

    def fetch_vi_data(
        self, start_date: date, end_date: Optional[date] = None
    ) -> Dict[date, float]:
        """
        日経VIデータを取得（日経公式CSVから直接ダウンロード）

        Args:
            start_date: 開始日
            end_date: 終了日（Noneの場合は今日）

        Returns:
            {date: vi_value} の辞書
        """
        if end_date is None:
            end_date = date.today()

        print(f"日経VIデータ取得: {start_date} 〜 {end_date}")

        try:
            # 日経公式サイト CSVダウンロード
            vi_dict = self._fetch_from_official_csv(start_date, end_date)
            if vi_dict:
                return vi_dict

            print("⚠️ 日経VIデータの取得に失敗しました")
            return {}

        except Exception as e:
            print(f"日経VI取得エラー: {e}")
            import traceback
            traceback.print_exc()
            return {}

    def _fetch_from_official_csv(
        self, start_date: date, end_date: date
    ) -> Dict[date, float]:
        """日経公式サイトから日経VIのCSVを直接ダウンロード"""
        try:
            print(f"  日経公式CSV取得試行: {self.vi_csv_url}")

            response = self.session.get(self.vi_csv_url, timeout=self.timeout)
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

                # 日経公式CSVのカラム名を確認（日付と終値）
                # 通常: '日付', '終値' のようなカラム名
                vi_dict = {}
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

                        # VI値を取得（終値カラム）
                        # カラム名が「終値」または2番目のカラムを使用
                        if '終値' in df.columns:
                            vi_value = float(row['終値'])
                        elif 'Close' in df.columns:
                            vi_value = float(row['Close'])
                        else:
                            vi_value = float(row.iloc[1])  # 2番目のカラム

                        vi_dict[parsed_date] = vi_value

                    except Exception as e:
                        # 個別の行のパースエラーは無視
                        continue

                if vi_dict:
                    print(f"  ✅ 日経公式CSV取得成功: {len(vi_dict)} 件")
                    if vi_dict:
                        latest_date = max(vi_dict.keys())
                        print(f"   最新値（{latest_date}）: {vi_dict[latest_date]:.2f}")
                    return vi_dict
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


def test_vi_fetch():
    """テスト用関数"""
    from datetime import timedelta

    fetcher = NikkeiVIFetcher()
    end_date = date.today()
    start_date = end_date - timedelta(days=30)

    try:
        vi_data = fetcher.fetch_vi_data(start_date, end_date)
        if vi_data:
            print(f"\n✅ 取得成功: {len(vi_data)} 件")
            print("\n最新5件:")
            for dt in sorted(vi_data.keys())[-5:]:
                print(f"  {dt}: {vi_data[dt]:.2f}")
        else:
            print("\n❌ データ取得失敗")
    except Exception as e:
        print(f"\n❌ エラー: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    test_vi_fetch()
