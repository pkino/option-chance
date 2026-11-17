"""日経公式サイトから日経VI（日経平均ボラティリティ・インデックス）を取得"""
import io
from datetime import datetime, date
from typing import Dict, Optional

import pandas as pd
import requests

# 1日分のVI四本値。キーは close / open / high / low（取れなかった値は入らない）
VIBar = Dict[str, float]

# 日経公式CSVの列名 → 内部キー
_COLUMN_KEYS = {"終値": "close", "始値": "open", "高値": "high", "安値": "low"}
# 列名で見つからない場合の列位置（日経公式CSVは 日付,終値,始値,高値,安値 の順）
_POSITIONAL_KEYS = ["close", "open", "high", "low"]

_DATE_FORMATS = ["%Y/%m/%d", "%Y-%m-%d", "%Y年%m月%d日"]


def _parse_date(value) -> Optional[date]:
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.date()
    if not isinstance(value, str):
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _to_float(value) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(result) else result


def parse_vi_csv(text: str, start_date: date, end_date: date) -> Dict[date, VIBar]:
    """日経公式VI CSV（デコード済みテキスト）から期間内の四本値を取り出す。

    列は名前（終値/始値/高値/安値）で探し、見つからなければ列位置で読む。
    末尾の注記行など、日付として読めない行は無視する。終値が読めない行も捨てる。
    """
    df = pd.read_csv(io.StringIO(text))
    if len(df) == 0:
        return {}

    columns = {}
    for name, key in _COLUMN_KEYS.items():
        if name in df.columns:
            columns[key] = name
    if "close" not in columns and "Close" in df.columns:
        columns["close"] = "Close"
    if "close" not in columns:
        # 列名が想定外なら位置で読む（1列目=日付）
        columns = {
            key: df.columns[i + 1]
            for i, key in enumerate(_POSITIONAL_KEYS)
            if i + 1 < len(df.columns)
        }

    bars: Dict[date, VIBar] = {}
    for _, row in df.iterrows():
        parsed_date = _parse_date(row.iloc[0])
        if parsed_date is None or not (start_date <= parsed_date <= end_date):
            continue

        bar: VIBar = {}
        for key, column in columns.items():
            value = _to_float(row[column])
            if value is not None:
                bar[key] = value
        if "close" in bar:
            bars[parsed_date] = bar

    return bars


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

    def fetch_vi_bars(
        self, start_date: date, end_date: Optional[date] = None
    ) -> Dict[date, VIBar]:
        """日経VIの四本値を取得する（日経公式CSV）。

        Returns:
            {date: {"close": .., "open": .., "high": .., "low": ..}}。失敗時は空 dict
        """
        if end_date is None:
            end_date = date.today()

        print(f"日経VIデータ取得: {start_date} 〜 {end_date}")

        try:
            print(f"  日経公式CSV取得試行: {self.vi_csv_url}")
            response = self.session.get(self.vi_csv_url, timeout=self.timeout)
            response.raise_for_status()
            print(f"  HTTPステータス: {response.status_code}")

            bars = parse_vi_csv(response.content.decode("shift-jis"), start_date, end_date)
            if not bars:
                print("  パース結果が空です")
                return {}

            latest_date = max(bars)
            latest = bars[latest_date]
            ohlc = sum(1 for b in bars.values() if {"open", "high", "low"} <= b.keys())
            print(f"  ✅ 日経公式CSV取得成功: {len(bars)} 件（四本値あり {ohlc} 件）")
            print(
                f"   最新値（{latest_date}）: 終値 {latest['close']:.2f}"
                + (
                    f" / 始値 {latest['open']:.2f} 高値 {latest['high']:.2f} 安値 {latest['low']:.2f}"
                    if {"open", "high", "low"} <= latest.keys()
                    else ""
                )
            )
            return bars

        except Exception as e:
            print(f"  日経公式CSV取得エラー: {e}")
            import traceback
            traceback.print_exc()
            return {}

    def fetch_vi_data(
        self, start_date: date, end_date: Optional[date] = None
    ) -> Dict[date, float]:
        """日経VIの終値を取得する（互換用。四本値が要る場合は fetch_vi_bars）。

        Returns:
            {date: vi_close} の辞書
        """
        return {d: bar["close"] for d, bar in self.fetch_vi_bars(start_date, end_date).items()}


def test_vi_fetch():
    """テスト用関数"""
    from datetime import timedelta

    fetcher = NikkeiVIFetcher()
    end_date = date.today()
    start_date = end_date - timedelta(days=30)

    try:
        bars = fetcher.fetch_vi_bars(start_date, end_date)
        if bars:
            print(f"\n✅ 取得成功: {len(bars)} 件")
            print("\n最新5件:")
            for dt in sorted(bars)[-5:]:
                print(f"  {dt}: {bars[dt]}")
        else:
            print("\n❌ データ取得失敗")
    except Exception as e:
        print(f"\n❌ エラー: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    test_vi_fetch()
