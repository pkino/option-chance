"""JPX（日本取引所グループ）からオプション理論価格を取得"""
import io
import re
import zipfile
from datetime import datetime, date
from typing import List, Optional
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

from ..models.option import OptionData


class JPXDataFetcher:
    """JPXオプション理論価格の取得"""

    def __init__(self, base_url: str, timeout: int = 30):
        self.base_url = base_url
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
        )

    def fetch_latest_options(self) -> List[OptionData]:
        """最新のオプションデータを取得"""
        try:
            # 1. 一覧ページから最新のZIPファイルURLを取得
            zip_url = self._find_latest_zip_url()
            if not zip_url:
                raise ValueError("最新のZIPファイルが見つかりませんでした")

            print(f"最新のZIPファイル: {zip_url}")

            # 2. ZIPファイルをダウンロード
            zip_content = self._download_zip(zip_url)

            # 3. ZIP内のCSVをパース
            options = self._parse_zip_content(zip_content)

            print(f"取得したオプション数: {len(options)}")
            return options

        except Exception as e:
            print(f"JPXデータ取得エラー: {e}")
            raise

    def _find_latest_zip_url(self) -> Optional[str]:
        """一覧ページから最新のZIPファイルURLを見つける"""
        try:
            response = self.session.get(self.base_url, timeout=self.timeout)
            response.raise_for_status()

            soup = BeautifulSoup(response.content, "html.parser")

            # ZIPファイルへのリンクを探す
            # 例: /markets/derivatives/option-price/files/j-option-YYYYMMDD.zip
            zip_links = []
            for link in soup.find_all("a", href=True):
                href = link["href"]
                # オプション理論価格のZIPファイルパターン
                if "option" in href.lower() and href.endswith(".zip"):
                    # 絶対URLに変換
                    if href.startswith("http"):
                        full_url = href
                    elif href.startswith("/"):
                        full_url = f"https://www.jpx.co.jp{href}"
                    else:
                        full_url = f"{self.base_url}/{href}"

                    # 日付を抽出してソート用に保存
                    date_match = re.search(r"(\d{8})", href)
                    if date_match:
                        date_str = date_match.group(1)
                        zip_links.append((date_str, full_url))

            if not zip_links:
                raise ValueError("ZIPファイルが見つかりませんでした")

            # 日付でソートして最新を取得
            zip_links.sort(reverse=True)
            latest_zip_url = zip_links[0][1]

            return latest_zip_url

        except Exception as e:
            print(f"ZIPファイルURL取得エラー: {e}")
            return None

    def _download_zip(self, url: str) -> bytes:
        """ZIPファイルをダウンロード"""
        response = self.session.get(url, timeout=self.timeout)
        response.raise_for_status()
        return response.content

    def _parse_zip_content(self, zip_content: bytes) -> List[OptionData]:
        """ZIP内のCSVをパースしてOptionDataリストを返す"""
        options = []

        with zipfile.ZipFile(io.BytesIO(zip_content)) as zf:
            # ZIP内のCSVファイルを探す
            csv_files = [f for f in zf.namelist() if f.endswith(".csv")]

            if not csv_files:
                raise ValueError("ZIP内にCSVファイルが見つかりませんでした")

            # 各CSVファイルを処理（通常は1つ）
            for csv_file in csv_files:
                print(f"処理中: {csv_file}")
                with zf.open(csv_file) as f:
                    # エンコーディングを試行
                    try:
                        # Shift-JIS を試す（JPXは通常これ）
                        df = pd.read_csv(f, encoding="shift-jis")
                    except:
                        # UTF-8を試す
                        f.seek(0)
                        df = pd.read_csv(f, encoding="utf-8")

                    # データをOptionDataに変換
                    options.extend(self._parse_dataframe(df))

        return options

    def _parse_dataframe(self, df: pd.DataFrame) -> List[OptionData]:
        """DataFrameをOptionDataリストに変換"""
        options = []

        # まずカラム名を表示（デバッグ用）
        print(f"カラム名: {df.columns.tolist()}")
        print(f"最初の数行:\n{df.head()}")

        # JPXのCSV形式に応じてパース
        # ※ 実際のCSV形式を確認後、適切にマッピングする必要がある
        # 以下は一般的なパターンの例

        for _, row in df.iterrows():
            try:
                # カラム名は実際のCSVに合わせて調整が必要
                # 一般的なカラム名の例:
                # - 日付, 基準日, Date
                # - 原資産価格, 日経平均, Underlying
                # - 権利行使価格, Strike
                # - 満期, 満期日, Expiry
                # - コール/プット, Type
                # - 理論価格, Price, Premium
                # - IV, Delta, Gamma, Vega, Theta

                option = OptionData(
                    date=self._parse_date(row.get("日付") or row.get("基準日")),
                    underlying_price=float(row.get("原資産価格", 0)),
                    strike=float(row.get("権利行使価格", 0)),
                    expiry=self._parse_date(row.get("満期") or row.get("満期日")),
                    option_type=self._parse_option_type(row.get("タイプ")),
                    close=self._safe_float(row.get("理論価格")),
                    settlement=self._safe_float(row.get("清算値")),
                    iv=self._safe_float(row.get("IV")),
                    delta=self._safe_float(row.get("Delta")),
                    gamma=self._safe_float(row.get("Gamma")),
                    vega=self._safe_float(row.get("Vega")),
                    theta=self._safe_float(row.get("Theta")),
                    volume=self._safe_int(row.get("出来高")),
                    open_interest=self._safe_int(row.get("建玉")),
                )

                options.append(option)

            except Exception as e:
                # パースエラーは個別に記録して続行
                print(f"行のパースエラー: {e}, row: {row}")
                continue

        return options

    def _parse_date(self, date_value) -> date:
        """日付をパース"""
        if pd.isna(date_value):
            raise ValueError("日付が不正です")

        if isinstance(date_value, date):
            return date_value

        if isinstance(date_value, str):
            # 複数の日付フォーマットを試行
            for fmt in ["%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"]:
                try:
                    return datetime.strptime(date_value, fmt).date()
                except:
                    continue

        raise ValueError(f"日付のパースに失敗: {date_value}")

    def _parse_option_type(self, type_value) -> str:
        """オプションタイプをパース"""
        if pd.isna(type_value):
            return "Put"  # デフォルト

        type_str = str(type_value).upper()
        if "CALL" in type_str or "C" == type_str:
            return "Call"
        elif "PUT" in type_str or "P" == type_str:
            return "Put"

        return "Put"  # デフォルト

    def _safe_float(self, value) -> Optional[float]:
        """安全にfloatに変換"""
        if pd.isna(value):
            return None
        try:
            return float(value)
        except:
            return None

    def _safe_int(self, value) -> Optional[int]:
        """安全にintに変換"""
        if pd.isna(value):
            return None
        try:
            return int(value)
        except:
            return None


def test_jpx_fetch():
    """テスト用関数"""
    fetcher = JPXDataFetcher("https://www.jpx.co.jp/markets/derivatives/option-price")

    try:
        options = fetcher.fetch_latest_options()

        if options:
            print(f"\n取得成功！ {len(options)} 件のオプションデータ")
            print("\n最初の3件:")
            for opt in options[:3]:
                print(
                    f"  {opt.date} | Strike={opt.strike} | Type={opt.option_type} | "
                    f"Premium={opt.premium} | Delta={opt.delta} | DTE={opt.dte_business_days}日"
                )

            # Putオプションのみフィルタ
            puts = [o for o in options if o.option_type == "Put"]
            print(f"\nPutオプション: {len(puts)} 件")

            # 20-40円の範囲
            in_range = [
                o
                for o in puts
                if o.premium and 20 <= o.premium <= 40 and o.dte_business_days
            ]
            print(f"20-40円のPut: {len(in_range)} 件")

    except Exception as e:
        print(f"エラー: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    test_jpx_fetch()
