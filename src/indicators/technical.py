"""テクニカル指標の計算"""
from typing import List, Dict, Any, Optional
import pandas as pd
import numpy as np

from ..models.option import MarketData


class TechnicalIndicators:
    """テクニカル指標計算クラス"""

    @staticmethod
    def calculate_all(market_data: List[MarketData]) -> pd.DataFrame:
        """
        全てのテクニカル指標を計算

        Args:
            market_data: MarketDataのリスト

        Returns:
            指標を含むDataFrame
        """
        # DataFrameに変換
        df = pd.DataFrame([vars(d) for d in market_data])
        df = df.sort_values("date").reset_index(drop=True)

        # 各指標を計算
        df = TechnicalIndicators._add_rsi(df, period=14)
        df = TechnicalIndicators._add_macd(df, fast=12, slow=26, signal=9)
        df = TechnicalIndicators._add_bollinger_bands(df, period=25, std_dev=2)
        df = TechnicalIndicators._add_moving_averages(df, periods=[5, 20, 25])
        df = TechnicalIndicators._add_volume_indicators(df)
        df = TechnicalIndicators._add_price_patterns(df)
        df = TechnicalIndicators._add_vi_indicators(df)

        return df

    @staticmethod
    def _add_rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        """RSI（Relative Strength Index）を計算"""
        delta = df["close"].diff()

        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)

        avg_gain = gain.rolling(window=period, min_periods=period).mean()
        avg_loss = loss.rolling(window=period, min_periods=period).mean()

        # Wilder's smoothing
        for i in range(period, len(df)):
            avg_gain.iloc[i] = (avg_gain.iloc[i - 1] * (period - 1) + gain.iloc[i]) / period
            avg_loss.iloc[i] = (avg_loss.iloc[i - 1] * (period - 1) + loss.iloc[i]) / period

        rs = avg_gain / avg_loss
        df["rsi"] = 100 - (100 / (1 + rs))

        return df

    @staticmethod
    def _add_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
        """MACD（Moving Average Convergence Divergence）を計算"""
        ema_fast = df["close"].ewm(span=fast, adjust=False).mean()
        ema_slow = df["close"].ewm(span=slow, adjust=False).mean()

        df["macd"] = ema_fast - ema_slow
        df["macd_signal"] = df["macd"].ewm(span=signal, adjust=False).mean()
        df["macd_hist"] = df["macd"] - df["macd_signal"]

        return df

    @staticmethod
    def _add_bollinger_bands(df: pd.DataFrame, period: int = 25, std_dev: int = 2) -> pd.DataFrame:
        """ボリンジャーバンドを計算"""
        df["bb_middle"] = df["close"].rolling(window=period).mean()
        df["bb_std"] = df["close"].rolling(window=period).std()
        df["bb_upper"] = df["bb_middle"] + (df["bb_std"] * std_dev)
        df["bb_lower"] = df["bb_middle"] - (df["bb_std"] * std_dev)

        # 乖離率
        df["bb_divergence_pct"] = ((df["close"] - df["bb_middle"]) / df["bb_middle"]) * 100

        return df

    @staticmethod
    def _add_moving_averages(df: pd.DataFrame, periods: List[int] = [5, 20, 25]) -> pd.DataFrame:
        """移動平均線を計算"""
        for period in periods:
            df[f"ma_{period}"] = df["close"].rolling(window=period).mean()

        return df

    @staticmethod
    def _add_volume_indicators(df: pd.DataFrame) -> pd.DataFrame:
        """出来高関連の指標を計算"""
        if "volume" in df.columns and df["volume"].notna().any():
            df["volume_ma_5"] = df["volume"].rolling(window=5).mean()
            df["volume_ma_20"] = df["volume"].rolling(window=20).mean()
            df["volume_ratio"] = df["volume_ma_5"] / df["volume_ma_20"]
        else:
            df["volume_ma_5"] = np.nan
            df["volume_ma_20"] = np.nan
            df["volume_ratio"] = np.nan

        return df

    @staticmethod
    def _add_price_patterns(df: pd.DataFrame) -> pd.DataFrame:
        """価格パターンを計算（ローソク足分析）"""
        # 上ヒゲ比率
        df["upper_wick_ratio"] = (df["high"] - df[["open", "close"]].max(axis=1)) / (
            df["high"] - df["low"]
        )
        df["upper_wick_ratio"] = df["upper_wick_ratio"].fillna(0)

        # 下ヒゲ比率
        df["lower_wick_ratio"] = (df[["open", "close"]].min(axis=1) - df["low"]) / (
            df["high"] - df["low"]
        )
        df["lower_wick_ratio"] = df["lower_wick_ratio"].fillna(0)

        # 実体比率
        df["body_ratio"] = abs(df["close"] - df["open"]) / (df["high"] - df["low"])
        df["body_ratio"] = df["body_ratio"].fillna(0)

        # 陰線・陽線
        df["is_bearish"] = (df["close"] < df["open"]).astype(int)
        df["is_bullish"] = (df["close"] > df["open"]).astype(int)

        # ギャップ
        df["gap_up"] = df["open"] / df["high"].shift(1)
        df["gap_down"] = df["open"] / df["low"].shift(1)

        # 高値・安値更新
        df["is_new_high_20"] = (df["high"] == df["high"].rolling(window=20).max()).astype(int)
        df["is_new_low_20"] = (df["low"] == df["low"].rolling(window=20).min()).astype(int)

        # 直近高値・安値からの距離
        df["high_20"] = df["high"].rolling(window=20).max()
        df["low_20"] = df["low"].rolling(window=20).min()
        df["distance_from_high_20_pct"] = ((df["close"] - df["high_20"]) / df["high_20"]) * 100
        df["distance_from_low_20_pct"] = ((df["close"] - df["low_20"]) / df["low_20"]) * 100

        return df

    @staticmethod
    def _add_vi_indicators(df: pd.DataFrame) -> pd.DataFrame:
        """VI（Volatility Index）関連の指標を計算"""
        if "vi" in df.columns and df["vi"].notna().any():
            df["vi_ma_10"] = df["vi"].rolling(window=10).mean()
            df["vi_std_10"] = df["vi"].rolling(window=10).std()

            # VIの傾き（10日間の線形回帰）
            vi_slope = []
            for i in range(len(df)):
                if i < 9:
                    vi_slope.append(np.nan)
                else:
                    y = df["vi"].iloc[i - 9 : i + 1].values
                    if np.all(np.isnan(y)):
                        vi_slope.append(np.nan)
                    else:
                        x = np.arange(10)
                        # 欠損値を除外
                        mask = ~np.isnan(y)
                        if mask.sum() < 3:  # 最低3点必要
                            vi_slope.append(np.nan)
                        else:
                            slope = np.polyfit(x[mask], y[mask], 1)[0]
                            vi_slope.append(slope)

            df["vi_slope_10"] = vi_slope
        else:
            df["vi_ma_10"] = np.nan
            df["vi_std_10"] = np.nan
            df["vi_slope_10"] = np.nan

        return df


class SignalDetector:
    """シグナル検出クラス（Gate条件のチェック）"""

    def __init__(self, df: pd.DataFrame, config: Dict[str, Any]):
        """
        Args:
            df: テクニカル指標を含むDataFrame
            config: 設定辞書
        """
        self.df = df
        self.config = config

    def detect_gate_vi(self, idx: int) -> bool:
        """
        Gate① VI安定条件をチェック

        Args:
            idx: DataFrameのインデックス

        Returns:
            条件を満たすか
        """
        if idx < 0 or idx >= len(self.df):
            return False

        row = self.df.iloc[idx]

        # VIデータがない場合は条件を満たさない
        if pd.isna(row.get("vi")) or pd.isna(row.get("vi_ma_10")):
            return False

        config = self.config["gate_vi"]

        conditions = [
            row["vi"] <= config["vi_threshold"],
            row["vi_ma_10"] <= config["vi_10d_avg_threshold"],
            row["vi_std_10"] <= config["vi_10d_std_threshold"],
            row["vi_slope_10"] <= config["vi_10d_slope_threshold"],
        ]

        return all(conditions)

    def detect_gate_top_a(self, idx: int) -> tuple[bool, dict]:
        """
        Gate② テクニカル条件（A）をチェック

        Args:
            idx: DataFrameのインデックス

        Returns:
            (条件を満たすか, 詳細情報)
        """
        if idx < 5:  # 最低5日分のデータが必要
            return False, {}

        row = self.df.iloc[idx]
        config = self.config["gate_top"]["technical"]

        signals = {}

        # A1: RSI過熱→反転
        signals["a1_rsi_reversal"] = self._check_rsi_reversal(idx)

        # A2: MACD弱化
        signals["a2_macd_weakening"] = self._check_macd_weakening(idx)

        # A3: ダイバージェンス
        signals["a3_divergence"] = self._check_divergence(idx)

        # A4: 伸び切り（ボリンジャー）
        signals["a4_overbought"] = self._check_overbought(idx)

        # 2つ以上の条件が成立
        true_count = sum(signals.values())
        is_satisfied = true_count >= config["required_conditions"]

        return is_satisfied, signals

    def _check_rsi_reversal(self, idx: int) -> bool:
        """A1: RSI過熱→反転"""
        config = self.config["gate_top"]["technical"]["rsi"]

        # 過去5営業日以内にRSI >= 70
        lookback = config["lookback_days"]
        overbought_threshold = config["overbought"]

        rsi_past = self.df.iloc[max(0, idx - lookback) : idx + 1]["rsi"]
        reached_overbought = (rsi_past >= overbought_threshold).any()

        if not reached_overbought:
            return False

        # RSIが2日連続で低下
        if idx < 2:
            return False

        rsi_current = self.df.iloc[idx]["rsi"]
        rsi_1d_ago = self.df.iloc[idx - 1]["rsi"]
        rsi_2d_ago = self.df.iloc[idx - 2]["rsi"]

        consecutive_decline = rsi_current < rsi_1d_ago < rsi_2d_ago

        return consecutive_decline

    def _check_macd_weakening(self, idx: int) -> bool:
        """A2: MACD弱化"""
        if idx < 3:
            return False

        config = self.config["gate_top"]["technical"]["macd"]

        row = self.df.iloc[idx]

        # MACDラインが高位（> 0）
        if row["macd"] <= 0:
            return False

        # ヒストグラムが3日連続で縮小
        hist_current = self.df.iloc[idx]["macd_hist"]
        hist_1d = self.df.iloc[idx - 1]["macd_hist"]
        hist_2d = self.df.iloc[idx - 2]["macd_hist"]

        consecutive_decline = hist_current < hist_1d < hist_2d

        return consecutive_decline

    def _check_divergence(self, idx: int) -> bool:
        """A3: ダイバージェンス"""
        config = self.config["gate_top"]["technical"]["divergence"]
        lookback = config["lookback_days"]

        if idx < lookback:
            return False

        row = self.df.iloc[idx]

        # 価格が直近20営業日高値を更新
        is_new_price_high = row["close"] >= row["high_20"]

        if not is_new_price_high:
            return False

        # RSIは直近20営業日高値を更新しない
        rsi_window = self.df.iloc[max(0, idx - lookback) : idx]["rsi"]
        max_rsi_past = rsi_window.max()
        current_rsi = row["rsi"]

        rsi_not_new_high = current_rsi < max_rsi_past

        return rsi_not_new_high

    def _check_overbought(self, idx: int) -> bool:
        """A4: 伸び切り（ボリンジャー上限突破 + 陰線/上ヒゲ）"""
        row = self.df.iloc[idx]

        # ボリンジャーバンド上限を上回る
        above_upper_band = row["close"] > row["bb_upper"]

        if not above_upper_band:
            return False

        # 陰線 OR 上ヒゲが長い
        config = self.config["gate_top"]["technical"]["bollinger"]
        wick_threshold = config["upper_wick_ratio"]

        is_bearish = row["close"] < row["open"]
        has_long_upper_wick = row["upper_wick_ratio"] >= wick_threshold

        return is_bearish or has_long_upper_wick

    def detect_gate_top_b(self, idx: int) -> tuple[bool, dict]:
        """
        Gate② 需給条件（B）をチェック

        Args:
            idx: DataFrameのインデックス

        Returns:
            (条件を満たすか, 詳細情報)
        """
        if idx < 20:  # 最低20日分のデータが必要
            return False, {}

        config = self.config["gate_top"]["supply_demand"]

        signals = {}

        # B1: 高値圏の出来高失速
        signals["b1_volume_failure"] = self._check_volume_failure(idx)

        # B2: 上ヒゲ優勢
        signals["b2_upper_wick_dominance"] = self._check_upper_wick_dominance(idx)

        # B3: ギャップアップ失速
        signals["b3_gap_up_failure"] = self._check_gap_up_failure(idx)

        # 1つ以上の条件が成立
        true_count = sum(signals.values())
        is_satisfied = true_count >= config["required_conditions"]

        return is_satisfied, signals

    def _check_volume_failure(self, idx: int) -> bool:
        """B1: 高値圏の出来高失速"""
        row = self.df.iloc[idx]

        # 出来高データがない場合はFalse
        if pd.isna(row.get("volume_ratio")):
            return False

        config = self.config["gate_top"]["supply_demand"]["volume_failure"]

        # 出来高比率の低下
        volume_declining = row["volume_ratio"] <= config["ratio_threshold"]

        # 高値圏（直近20営業日高値の99%以内）
        in_high_range = row["distance_from_high_20_pct"] >= (config["high_proximity"] - 1) * 100

        return volume_declining and in_high_range

    def _check_upper_wick_dominance(self, idx: int) -> bool:
        """B2: 上ヒゲ優勢"""
        config = self.config["gate_top"]["supply_demand"]["upper_wick"]
        lookback = config["lookback_days"]
        required_days = config["required_days"]
        wick_ratio = config["wick_ratio"]
        close_from_high = config["close_from_high"]

        if idx < lookback:
            return False

        # 直近3日分を取得
        recent_df = self.df.iloc[idx - lookback + 1 : idx + 1]

        # 条件を満たす日数をカウント
        condition_met_days = 0
        for _, row in recent_df.iterrows():
            has_long_upper_wick = row["upper_wick_ratio"] >= wick_ratio
            closed_below_high = row["close"] <= row["high"] * close_from_high

            if has_long_upper_wick and closed_below_high:
                condition_met_days += 1

        return condition_met_days >= required_days

    def _check_gap_up_failure(self, idx: int) -> bool:
        """B3: ギャップアップ失速"""
        if idx < 1:
            return False

        row = self.df.iloc[idx]
        prev_row = self.df.iloc[idx - 1]

        config = self.config["gate_top"]["supply_demand"]["gap_up_failure"]
        gap_threshold = config["gap_threshold"]

        # ギャップアップ
        has_gap_up = row["open"] >= prev_row["high"] * gap_threshold

        # 寄り天（終値 <= 始値）
        failed_to_hold = row["close"] <= row["open"]

        return has_gap_up and failed_to_hold

    def detect_gate_top_c(self, idx: int, events: List[str] = []) -> tuple[bool, dict]:
        """
        Gate② マクロ条件（C）をチェック（オプション）

        Args:
            idx: DataFrameのインデックス
            events: イベント日付のリスト（YYYY-MM-DD）

        Returns:
            (条件を満たすか, 詳細情報)
        """
        row = self.df.iloc[idx]
        current_date = pd.to_datetime(row["date"])

        config = self.config["gate_top"]["macro"]
        lookforward_days = config["event_lookforward_days"]

        signals = {}

        # C1: イベント近接
        signals["c1_event_proximity"] = self._check_event_proximity(
            current_date, events, lookforward_days
        )

        # C2, C3は実装が難しいため、将来的に追加
        signals["c2_macro_headwind"] = False
        signals["c3_valuation_expensive"] = False

        # 1つ以上の条件が成立
        is_satisfied = any(signals.values())

        return is_satisfied, signals

    def _check_event_proximity(
        self, current_date: pd.Timestamp, events: List[str], lookforward_days: int
    ) -> bool:
        """イベント近接チェック"""
        if not events:
            return False

        event_dates = [pd.to_datetime(e) for e in events]

        for event_date in event_dates:
            days_until_event = (event_date - current_date).days
            if 0 <= days_until_event <= lookforward_days:
                return True

        return False

    def detect_trigger(self, idx: int) -> tuple[bool, dict]:
        """
        Trigger③ エントリートリガーをチェック

        Args:
            idx: DataFrameのインデックス

        Returns:
            (条件を満たすか, 詳細情報)
        """
        if idx < 5:
            return False, {}

        row = self.df.iloc[idx]
        prev_row = self.df.iloc[idx - 1]

        signals = {}

        # 前日安値割れ（終値ベース）
        signals["prev_low_break"] = row["close"] < prev_row["low"]

        # 5日MA割れ（終値）
        signals["ma5_break"] = row["close"] < row.get("ma_5", float("inf"))

        # どちらか1つでも成立
        is_satisfied = signals["prev_low_break"] or signals["ma5_break"]

        return is_satisfied, signals
