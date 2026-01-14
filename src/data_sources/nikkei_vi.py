"""日経公式サイトから日経VI（日経平均ボラティリティ・インデックス）を取得"""
import io
import re
from datetime import datetime, date, timedelta
from typing import Dict, Optional
import pandas as pd
import requests
from bs4 import BeautifulSoup


class NikkeiVIFetcher:
    """日経VIデータの取得"""

    def __init__(self, timeout: int = 30):
        self.base_url = "https://indexes.nikkei.co.jp/nkave"
        self.timeout = timeout
        self.session = requests.Session()
        # より実際のブラウザに近いヘッダーを設定
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Cache-Control": "max-age=0",
            }
        )

    def fetch_vi_data(
        self, start_date: date, end_date: Optional[date] = None
    ) -> Dict[date, float]:
        """
        日経VIデータを取得

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
            # 方法1: Yahoo Finance (yfinance経由)
            vi_dict = self._fetch_from_yahoo_finance(start_date, end_date)
            if vi_dict:
                print(f"✅ 日経VI取得成功（Yahoo Finance）: {len(vi_dict)} 件")
                return vi_dict

            # 方法2: Investing.com
            vi_dict = self._fetch_from_investing_com(start_date, end_date)
            if vi_dict:
                print(f"✅ 日経VI取得成功（Investing.com）: {len(vi_dict)} 件")
                return vi_dict

            # 方法3: 日経公式サイト CSVダウンロード
            vi_dict = self._fetch_from_csv_download(start_date, end_date)
            if vi_dict:
                print(f"✅ 日経VI取得成功（日経公式CSV）: {len(vi_dict)} 件")
                return vi_dict

            # 方法4: 日経公式サイト HTMLテーブルスクレイピング
            vi_dict = self._fetch_from_html_table(start_date, end_date)
            if vi_dict:
                print(f"✅ 日経VI取得成功（日経公式HTML）: {len(vi_dict)} 件")
                return vi_dict

            print("⚠️ 日経VIデータの取得に失敗しました")
            return {}

        except Exception as e:
            print(f"日経VI取得エラー: {e}")
            return {}

    def _fetch_from_yahoo_finance(
        self, start_date: date, end_date: date
    ) -> Dict[date, float]:
        """Yahoo Financeから日経VIデータを取得"""
        try:
            print(f"  Yahoo Finance からVI取得試行...")

            # yfinanceが使えない場合はスキップ
            try:
                import yfinance as yf
            except ImportError:
                print(f"  yfinanceがインストールされていません")
                return {}

            # 日経VIの可能性があるティッカーシンボル
            vi_symbols = [
                "^N225VI",   # 日経VI インデックス
                "^VXN225",   # 別名
                "1552.T",    # 日経VI先物ETF
                "2038.T",    # NEXT FUNDS 日経平均VI先物指数ETF
            ]

            for symbol in vi_symbols:
                try:
                    print(f"    試行中: {symbol}")
                    ticker = yf.Ticker(symbol)

                    # 履歴データを取得
                    df = ticker.history(start=start_date, end=end_date, interval="1d")

                    if df.empty:
                        print(f"      {symbol}: データなし")
                        continue

                    # 終値を取得
                    vi_dict = {}
                    for idx, row in df.iterrows():
                        try:
                            vi_date = idx.date() if hasattr(idx, 'date') else idx
                            vi_value = float(row['Close'])

                            # 日付範囲チェック
                            if start_date <= vi_date <= end_date:
                                vi_dict[vi_date] = vi_value
                        except Exception:
                            continue

                    if vi_dict:
                        print(f"      ✅ {symbol}: {len(vi_dict)} 件取得")
                        return vi_dict
                    else:
                        print(f"      {symbol}: パース失敗")

                except Exception as e:
                    print(f"      {symbol}: エラー - {e}")
                    continue

            print(f"  Yahoo Finance: すべてのシンボルで失敗")
            return {}

        except Exception as e:
            print(f"  Yahoo Finance取得エラー: {e}")
            return {}

    def _fetch_from_investing_com(
        self, start_date: date, end_date: date
    ) -> Dict[date, float]:
        """Investing.comから日経VIデータを取得"""
        try:
            print(f"  Investing.com からVI取得試行...")
            url = "https://jp.investing.com/indices/nikkei-volatility-historical-data"

            # Refererヘッダーを追加
            headers = {"Referer": "https://jp.investing.com/"}
            response = self.session.get(url, headers=headers, timeout=self.timeout)
            response.raise_for_status()
            print(f"  HTTPステータス: {response.status_code}")

            # HTMLからテーブルを抽出
            try:
                # pandas.read_html()で複数のパーサーを試行
                tables = None
                for parser in ['lxml', 'html5lib', 'html.parser']:
                    try:
                        tables = pd.read_html(io.StringIO(response.text), encoding="utf-8", flavor=parser)
                        print(f"  見つかったテーブル: {len(tables)} 件（パーサー: {parser}）")
                        break
                    except ImportError:
                        continue
                    except ValueError:
                        continue

                if tables:
                    for table in tables:
                        # 日付とVIらしきカラムを持つテーブルを探す
                        vi_dict = self._extract_vi_from_investing_table(table, start_date, end_date)
                        if vi_dict:
                            return vi_dict
            except Exception as e:
                print(f"  テーブル解析エラー: {e}")

            # テーブル解析が失敗した場合、BeautifulSoupで手動解析
            print(f"  BeautifulSoupで手動解析を試行...")
            soup = BeautifulSoup(response.content, "html.parser")

            # HTMLの構造をデバッグ出力
            all_tables = soup.find_all("table")
            print(f"  全テーブル数: {len(all_tables)} 件")

            # テーブルのclass/id属性を確認
            if all_tables:
                for i, table in enumerate(all_tables[:3]):  # 最初の3つだけ
                    table_class = table.get('class', [])
                    table_id = table.get('id', '')
                    print(f"    テーブル{i+1}: class={table_class}, id={table_id}")

            # Investing.comのテーブル構造を探す
            # 通常は <table class="historical-data-table"> や <table id="curr_table"> など
            data_tables = soup.find_all("table", {"class": re.compile(r"historical|data", re.I)})
            if not data_tables:
                data_tables = soup.find_all("table", {"id": re.compile(r"curr|data", re.I)})

            # より広範囲にテーブルを探す
            if not data_tables and all_tables:
                print(f"  パターンマッチ失敗、全テーブルを試行...")
                data_tables = all_tables

            print(f"  見つかったデータテーブル: {len(data_tables)} 件")

            for table in data_tables:
                vi_dict = self._parse_investing_html_table(table, start_date, end_date)
                if vi_dict:
                    return vi_dict

            return {}

        except Exception as e:
            print(f"  Investing.com取得エラー: {e}")
            import traceback
            traceback.print_exc()
            return {}

    def _extract_vi_from_investing_table(
        self, df: pd.DataFrame, start_date: date, end_date: date
    ) -> Dict[date, float]:
        """Investing.comのDataFrameから日経VIデータを抽出"""
        try:
            # カラム名を確認
            date_col = None
            vi_col = None

            for col in df.columns:
                col_str = str(col).lower()
                # 日付カラムを探す（Investing.comは「日付」「Date」など）
                if any(x in col_str for x in ["date", "日付", "年月日"]):
                    date_col = col
                # VI値カラムを探す（「終値」「Close」「価格」など）
                if any(x in col_str for x in ["close", "終値", "価格", "終", "closing"]):
                    vi_col = col

            if date_col is None or vi_col is None:
                return {}

            # データを抽出
            vi_dict = {}
            for _, row in df.iterrows():
                try:
                    # 日付をパース
                    date_value = row[date_col]
                    if pd.isna(date_value):
                        continue

                    parsed_date = None
                    if isinstance(date_value, str):
                        # Investing.comの日付フォーマット（複数パターン）
                        # 例: "2026年01月14日", "2026/01/14", "2026.01.14", "Jan 14, 2026"
                        for fmt in [
                            "%Y年%m月%d日",
                            "%Y/%m/%d",
                            "%Y.%m.%d",
                            "%Y-%m-%d",
                            "%b %d, %Y",  # Jan 14, 2026
                            "%d/%m/%Y",
                        ]:
                            try:
                                parsed_date = datetime.strptime(date_value, fmt).date()
                                break
                            except:
                                continue

                        # 日本語の月表記をパース
                        if parsed_date is None:
                            # 例: "2026年1月14日" のようなゼロパディングなし
                            match = re.match(r"(\d{4})年(\d{1,2})月(\d{1,2})日", date_value)
                            if match:
                                y, m, d = match.groups()
                                parsed_date = date(int(y), int(m), int(d))

                    elif isinstance(date_value, (datetime, pd.Timestamp)):
                        parsed_date = date_value.date() if hasattr(date_value, "date") else date_value

                    if parsed_date is None:
                        continue

                    # 日付範囲チェック
                    if not (start_date <= parsed_date <= end_date):
                        continue

                    # VI値を取得（カンマ区切りの数値を処理）
                    vi_value = row[vi_col]
                    if pd.isna(vi_value):
                        continue

                    # 文字列の場合、カンマを除去
                    if isinstance(vi_value, str):
                        vi_value = vi_value.replace(",", "").strip()

                    vi_dict[parsed_date] = float(vi_value)

                except Exception as e:
                    continue

            return vi_dict

        except Exception as e:
            print(f"  Investing.comテーブル抽出エラー: {e}")
            return {}

    def _parse_investing_html_table(
        self, table_element, start_date: date, end_date: date
    ) -> Dict[date, float]:
        """BeautifulSoupで解析したHTML tableからデータを抽出"""
        try:
            # テーブルのヘッダーを取得
            headers = []
            header_row = table_element.find("thead")
            if header_row:
                headers = [th.get_text(strip=True) for th in header_row.find_all("th")]

            # データ行を取得
            rows = table_element.find("tbody")
            if not rows:
                rows = table_element

            data_rows = rows.find_all("tr")

            # カラムインデックスを特定
            date_col_idx = None
            vi_col_idx = None

            for idx, header in enumerate(headers):
                header_lower = header.lower()
                if any(x in header_lower for x in ["date", "日付"]):
                    date_col_idx = idx
                if any(x in header_lower for x in ["close", "終値", "価格"]):
                    vi_col_idx = idx

            # ヘッダーがない場合は0列目を日付、1列目をVIと仮定
            if date_col_idx is None:
                date_col_idx = 0
            if vi_col_idx is None:
                vi_col_idx = 1

            vi_dict = {}
            for row in data_rows:
                cells = row.find_all(["td", "th"])
                if len(cells) <= max(date_col_idx, vi_col_idx):
                    continue

                try:
                    # 日付を取得
                    date_text = cells[date_col_idx].get_text(strip=True)
                    parsed_date = None

                    for fmt in [
                        "%Y年%m月%d日",
                        "%Y/%m/%d",
                        "%Y.%m.%d",
                        "%Y-%m-%d",
                    ]:
                        try:
                            parsed_date = datetime.strptime(date_text, fmt).date()
                            break
                        except:
                            continue

                    # 正規表現でパース
                    if parsed_date is None:
                        match = re.match(r"(\d{4})年(\d{1,2})月(\d{1,2})日", date_text)
                        if match:
                            y, m, d = match.groups()
                            parsed_date = date(int(y), int(m), int(d))

                    if parsed_date is None:
                        continue

                    # 日付範囲チェック
                    if not (start_date <= parsed_date <= end_date):
                        continue

                    # VI値を取得
                    vi_text = cells[vi_col_idx].get_text(strip=True)
                    vi_text = vi_text.replace(",", "").strip()
                    vi_dict[parsed_date] = float(vi_text)

                except Exception:
                    continue

            return vi_dict

        except Exception as e:
            print(f"  HTML table解析エラー: {e}")
            return {}

    def _fetch_from_csv_download(
        self, start_date: date, end_date: date
    ) -> Dict[date, float]:
        """CSVダウンロードでデータ取得"""
        try:
            # まずトップページにアクセスしてcookieを取得
            print(f"  トップページにアクセス中...")
            top_page_url = f"{self.base_url}/index"
            try:
                top_response = self.session.get(top_page_url, timeout=self.timeout)
                top_response.raise_for_status()
                print(f"  トップページアクセス成功: {top_response.status_code}")
            except Exception as e:
                print(f"  トップページアクセス失敗（続行）: {e}")

            # ダウンロードページのURL構造を試行
            # 例: https://indexes.nikkei.co.jp/nkave/index?type=download
            download_url = f"{self.base_url}/index?type=download"
            print(f"  CSV取得試行: {download_url}")

            # Refererヘッダーを追加
            headers = {"Referer": f"{self.base_url}/index"}
            response = self.session.get(download_url, headers=headers, timeout=self.timeout)
            response.raise_for_status()
            print(f"  HTTPステータス: {response.status_code}")

            soup = BeautifulSoup(response.content, "html.parser")

            # CSVファイルへのリンクを探す
            csv_links = []
            for link in soup.find_all("a", href=True):
                href = link["href"]
                if "csv" in href.lower() or "download" in href.lower():
                    # 絶対URLに変換
                    if href.startswith("http"):
                        full_url = href
                    elif href.startswith("/"):
                        full_url = f"https://indexes.nikkei.co.jp{href}"
                    else:
                        full_url = f"{self.base_url}/{href}"

                    csv_links.append(full_url)

            print(f"  見つかったCSVリンク: {len(csv_links)} 件")

            # 各CSVリンクを試す
            for csv_url in csv_links:
                try:
                    print(f"  CSV取得試行: {csv_url}")
                    csv_response = self.session.get(csv_url, timeout=self.timeout)
                    csv_response.raise_for_status()

                    # CSVをパース
                    df = self._parse_csv(csv_response.content)
                    if df is not None and not df.empty:
                        # 日付範囲でフィルタ
                        vi_dict = self._filter_by_date_range(df, start_date, end_date)
                        if vi_dict:
                            return vi_dict

                except Exception as e:
                    print(f"    CSV取得失敗: {e}")
                    continue

            return {}

        except Exception as e:
            print(f"CSV取得エラー: {e}")
            import traceback
            traceback.print_exc()
            return {}

    def _fetch_from_html_table(
        self, start_date: date, end_date: date
    ) -> Dict[date, float]:
        """HTMLテーブルからデータをスクレイピング"""
        try:
            # 日経VIのページURL（複数パターンを試行）
            urls_to_try = [
                f"{self.base_url}/index",
                f"{self.base_url}/historical",
                "https://indexes.nikkei.co.jp/nkave/index/profile?idx=nk225vi",
            ]

            for url in urls_to_try:
                try:
                    print(f"  HTML取得試行: {url}")
                    # Refererヘッダーを追加
                    headers = {"Referer": "https://indexes.nikkei.co.jp/"}
                    response = self.session.get(url, headers=headers, timeout=self.timeout)
                    response.raise_for_status()
                    print(f"  HTTPステータス: {response.status_code}")

                    # HTMLからテーブルを抽出
                    tables = pd.read_html(io.StringIO(response.text), encoding="utf-8")
                    print(f"  見つかったテーブル: {len(tables)} 件")

                    for table in tables:
                        # 日付とVIらしきカラムを持つテーブルを探す
                        vi_dict = self._extract_vi_from_table(table, start_date, end_date)
                        if vi_dict:
                            return vi_dict

                except Exception as e:
                    print(f"    HTML取得失敗: {e}")
                    continue

            return {}

        except Exception as e:
            print(f"HTMLテーブル取得エラー: {e}")
            import traceback
            traceback.print_exc()
            return {}

    def _parse_csv(self, csv_content: bytes) -> Optional[pd.DataFrame]:
        """CSVをパース"""
        try:
            # エンコーディングを試行
            for encoding in ["shift-jis", "utf-8", "cp932"]:
                try:
                    df = pd.read_csv(
                        io.BytesIO(csv_content), encoding=encoding, parse_dates=True
                    )
                    if not df.empty:
                        return df
                except:
                    continue

            return None

        except Exception as e:
            print(f"CSVパースエラー: {e}")
            return None

    def _extract_vi_from_table(
        self, df: pd.DataFrame, start_date: date, end_date: date
    ) -> Dict[date, float]:
        """DataFrameから日経VIデータを抽出"""
        try:
            # カラム名を確認（日付と値のカラムを探す）
            date_col = None
            vi_col = None

            for col in df.columns:
                col_str = str(col).lower()
                # 日付カラムを探す
                if any(
                    x in col_str for x in ["date", "日付", "年月日", "日時", "年/月/日"]
                ):
                    date_col = col
                # VI値カラムを探す
                if any(
                    x in col_str
                    for x in ["vi", "volatility", "終値", "close", "指数値", "index"]
                ):
                    vi_col = col

            if date_col is None or vi_col is None:
                return {}

            # データを抽出
            vi_dict = {}
            for _, row in df.iterrows():
                try:
                    # 日付をパース
                    date_value = row[date_col]
                    if pd.isna(date_value):
                        continue

                    if isinstance(date_value, str):
                        # 複数の日付フォーマットを試行
                        for fmt in ["%Y-%m-%d", "%Y/%m/%d", "%Y%m%d", "%Y年%m月%d日"]:
                            try:
                                parsed_date = datetime.strptime(date_value, fmt).date()
                                break
                            except:
                                continue
                        else:
                            continue
                    elif isinstance(date_value, (datetime, pd.Timestamp)):
                        parsed_date = date_value.date() if hasattr(date_value, "date") else date_value
                    else:
                        continue

                    # 日付範囲チェック
                    if not (start_date <= parsed_date <= end_date):
                        continue

                    # VI値を取得
                    vi_value = row[vi_col]
                    if pd.isna(vi_value):
                        continue

                    vi_dict[parsed_date] = float(vi_value)

                except Exception as e:
                    continue

            return vi_dict

        except Exception as e:
            print(f"テーブル抽出エラー: {e}")
            return {}

    def _filter_by_date_range(
        self, df: pd.DataFrame, start_date: date, end_date: date
    ) -> Dict[date, float]:
        """DataFrameを日付範囲でフィルタ"""
        return self._extract_vi_from_table(df, start_date, end_date)


def test_nikkei_vi_fetch():
    """テスト用関数"""
    fetcher = NikkeiVIFetcher()

    # 過去1ヶ月のデータを取得
    end_date = date.today()
    start_date = end_date - timedelta(days=30)

    vi_data = fetcher.fetch_vi_data(start_date, end_date)

    if vi_data:
        print(f"\n取得成功！ {len(vi_data)} 件")
        print("\n最新の10件:")
        sorted_data = sorted(vi_data.items(), reverse=True)
        for d, vi in sorted_data[:10]:
            print(f"  {d}: {vi:.2f}")
    else:
        print("\nデータが取得できませんでした")


if __name__ == "__main__":
    test_nikkei_vi_fetch()
