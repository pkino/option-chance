"""日経公式VI取得のテスト"""
import sys
from pathlib import Path
from datetime import date, timedelta

# プロジェクトルートをパスに追加
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.data_sources.nikkei_vi import NikkeiVIFetcher
from src.data_sources.market_data import MarketDataFetcher


def test_nikkei_vi_direct():
    """日経VI直接取得のテスト"""
    print("=" * 70)
    print("日経公式VI直接取得テスト")
    print("=" * 70)

    fetcher = NikkeiVIFetcher()

    # 過去1ヶ月のデータを取得
    end_date = date.today()
    start_date = end_date - timedelta(days=30)

    vi_data = fetcher.fetch_vi_data(start_date, end_date)

    if vi_data:
        print(f"\n✅ 取得成功！ {len(vi_data)} 件")
        print("\n最新の10件:")
        sorted_data = sorted(vi_data.items(), reverse=True)
        for d, vi in sorted_data[:10]:
            print(f"  {d}: {vi:.2f}")

        # 統計
        values = list(vi_data.values())
        print(f"\nVI統計:")
        print(f"  平均: {sum(values) / len(values):.2f}")
        print(f"  最小: {min(values):.2f}")
        print(f"  最大: {max(values):.2f}")
    else:
        print("\n❌ データが取得できませんでした")


def test_market_data_with_vi():
    """MarketDataFetcherのVI取得テスト（フォールバック含む）"""
    print("\n" + "=" * 70)
    print("MarketDataFetcher VI取得テスト（フォールバック含む）")
    print("=" * 70)

    fetcher = MarketDataFetcher()

    # 過去1ヶ月のデータを取得
    end_date = date.today()
    start_date = end_date - timedelta(days=30)

    market_data = fetcher.fetch_market_data_with_vi(start_date, end_date)

    if market_data:
        print(f"\n✅ 取得成功！ {len(market_data)} 件")
        print("\n最新の10件:")
        for d in market_data[-10:]:
            vi_str = f"{d.vi:.2f}" if d.vi else "N/A"
            print(
                f"  {d.date} | Close={d.close:.2f} | VI={vi_str}"
            )

        # VI統計
        vi_data = [d.vi for d in market_data if d.vi is not None]
        if vi_data:
            print(f"\nVI統計:")
            print(f"  データ数: {len(vi_data)} / {len(market_data)}")
            print(f"  平均: {sum(vi_data) / len(vi_data):.2f}")
            print(f"  最小: {min(vi_data):.2f}")
            print(f"  最大: {max(vi_data):.2f}")
        else:
            print("\n⚠️ VIデータなし")
    else:
        print("\n❌ データが取得できませんでした")


def main():
    """メイン処理"""
    # テスト1: 日経VI直接取得
    test_nikkei_vi_direct()

    # テスト2: MarketDataFetcher経由（フォールバック含む）
    test_market_data_with_vi()


if __name__ == "__main__":
    main()
