"""Test Investing.com VI data fetching"""
import sys
from pathlib import Path
from datetime import date, timedelta

# プロジェクトルートをパスに追加
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from src.data_sources.nikkei_vi import NikkeiVIFetcher


def main():
    """メイン処理"""
    print("=" * 70)
    print("Investing.com 日経VI取得テスト")
    print("=" * 70)

    fetcher = NikkeiVIFetcher()

    # 過去30日のデータを取得
    end_date = date.today()
    start_date = end_date - timedelta(days=30)

    print(f"\n期間: {start_date} 〜 {end_date}\n")

    vi_data = fetcher.fetch_vi_data(start_date, end_date)

    if vi_data:
        print(f"\n✅ 取得成功！ {len(vi_data)} 件\n")
        print("最新の10件:")
        sorted_data = sorted(vi_data.items(), reverse=True)
        for d, vi in sorted_data[:10]:
            print(f"  {d}: {vi:.2f}")

        # 統計
        values = list(vi_data.values())
        print(f"\nVI統計:")
        print(f"  平均: {sum(values) / len(values):.2f}")
        print(f"  最小: {min(values):.2f}")
        print(f"  最大: {max(values):.2f}")

        return True
    else:
        print("\n❌ データが取得できませんでした")
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
