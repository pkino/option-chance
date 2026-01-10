"""市場データ取得のテスト"""
import sys
from pathlib import Path
from datetime import date, timedelta

# プロジェクトルートをパスに追加
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.data_sources.market_data import MarketDataFetcher


def main():
    """メイン処理"""
    print("市場データ取得テスト開始...")

    fetcher = MarketDataFetcher()

    # 過去1ヶ月のデータを取得
    end_date = date.today()
    start_date = end_date - timedelta(days=30)

    try:
        data = fetcher.fetch_market_data_with_vi(start_date, end_date)

        if data:
            print(f"\n取得成功！ {len(data)} 件")
            print("\n最新の10件:")
            for d in data[-10:]:
                vi_str = f"{d.vi:.2f}" if d.vi else "N/A"
                print(
                    f"  {d.date} | O={d.open:.2f} H={d.high:.2f} L={d.low:.2f} "
                    f"C={d.close:.2f} | VI={vi_str}"
                )

            # VIの統計
            vi_data = [d.vi for d in data if d.vi is not None]
            if vi_data:
                print(f"\nVI統計:")
                print(f"  平均: {sum(vi_data) / len(vi_data):.2f}")
                print(f"  最小: {min(vi_data):.2f}")
                print(f"  最大: {max(vi_data):.2f}")
            else:
                print("\nVI統計: データなし")

        else:
            print("データが取得できませんでした")

    except Exception as e:
        print(f"エラー: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    main()
