"""JPXデータ取得のテスト"""
import sys
from pathlib import Path

# プロジェクトルートをパスに追加
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.data_sources.jpx import JPXDataFetcher


def main():
    """メイン処理"""
    print("JPXデータ取得テスト開始...")

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

            # デルタの範囲チェック
            with_delta = [o for o in in_range if o.delta is not None]
            print(f"デルタ情報あり: {len(with_delta)} 件")

            if with_delta:
                print("\nデルタの範囲:")
                deltas = [o.delta for o in with_delta]
                print(f"  最小: {min(deltas):.4f}")
                print(f"  最大: {max(deltas):.4f}")

            # ギリシャ文字の確認
            print("\nギリシャ文字の有無:")
            print(
                f"  IV: {sum(1 for o in options if o.iv is not None)} / {len(options)}"
            )
            print(
                f"  Delta: {sum(1 for o in options if o.delta is not None)} / {len(options)}"
            )
            print(
                f"  Gamma: {sum(1 for o in options if o.gamma is not None)} / {len(options)}"
            )
            print(
                f"  Vega: {sum(1 for o in options if o.vega is not None)} / {len(options)}"
            )
            print(
                f"  Theta: {sum(1 for o in options if o.theta is not None)} / {len(options)}"
            )

    except Exception as e:
        print(f"エラー: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    main()
