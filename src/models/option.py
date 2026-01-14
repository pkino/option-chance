"""オプションデータモデル"""
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional


@dataclass
class OptionData:
    """オプションデータ"""

    date: date
    underlying_price: float  # 原資産価格
    strike: float  # 権利行使価格
    expiry: date  # 満期日
    option_type: str  # "Put" or "Call"

    # 価格データ
    bid: Optional[float] = None
    ask: Optional[float] = None
    close: Optional[float] = None
    settlement: Optional[float] = None  # 清算値

    # ギリシャ文字
    iv: Optional[float] = None  # Implied Volatility
    delta: Optional[float] = None
    gamma: Optional[float] = None
    vega: Optional[float] = None
    theta: Optional[float] = None

    # 流動性
    volume: Optional[int] = None
    open_interest: Optional[int] = None  # 建玉

    @property
    def premium(self) -> Optional[float]:
        """プレミアム（価格）を返す。優先順：close > settlement > mid"""
        if self.close is not None:
            return self.close
        if self.settlement is not None:
            return self.settlement
        if self.bid is not None and self.ask is not None:
            return (self.bid + self.ask) / 2
        return None

    @property
    def dte(self) -> int:
        """残存日数（Days To Expiration）"""
        return (self.expiry - self.date).days

    @property
    def dte_business_days(self) -> Optional[int]:
        """残存営業日数（概算：営業日 ≈ 日数 * 5/7）"""
        # 正確な営業日計算は別途実装が必要
        return int(self.dte * 5 / 7)

    def is_in_premium_range(self, min_premium: float, max_premium: float) -> bool:
        """プレミアムが指定範囲内か"""
        if self.premium is None:
            return False
        return min_premium <= self.premium <= max_premium

    def is_in_dte_range(self, min_dte: int, max_dte: int) -> bool:
        """残存営業日が指定範囲内か"""
        dte_bd = self.dte_business_days
        if dte_bd is None:
            return False
        return min_dte <= dte_bd <= max_dte

    def is_in_delta_range(self, min_delta: float, max_delta: float) -> bool:
        """デルタが指定範囲内か"""
        if self.delta is None:
            return False
        return min_delta <= self.delta <= max_delta

    def delta_distance_from_target(self, target_delta: float) -> Optional[float]:
        """目標デルタからの距離"""
        if self.delta is None:
            return None
        return abs(self.delta - target_delta)


@dataclass
class MarketData:
    """市場データ（日経平均、VI等）"""

    date: date
    open: float
    high: float
    low: float
    close: float
    volume: Optional[int] = None

    # VI（Volatility Index）
    vi: Optional[float] = None

    def __post_init__(self):
        """日付の正規化"""
        if isinstance(self.date, str):
            self.date = datetime.strptime(self.date, "%Y-%m-%d").date()


@dataclass
class Signal:
    """エントリーシグナル"""

    date: date
    gate_vi: bool  # Gate① VI安定
    gate_top_a: bool  # Gate② テクニカル
    gate_top_b: bool  # Gate② 需給
    gate_top_c: bool  # Gate② マクロ（オプション）
    trigger: bool  # Trigger③

    # 詳細情報
    details: dict

    @property
    def gate_top(self) -> bool:
        """Gate②が成立しているか（A AND B）"""
        return self.gate_top_a and self.gate_top_b

    @property
    def is_entry_signal(self) -> bool:
        """エントリーシグナルか（Gate① AND Gate② AND Trigger）"""
        return self.gate_vi and self.gate_top and self.trigger

    @property
    def is_strong_signal(self) -> bool:
        """強いシグナルか（C_TRUEも含む）"""
        return self.is_entry_signal and self.gate_top_c


@dataclass
class Trade:
    """トレード記録"""

    entry_date: date
    exit_date: Optional[date]
    option_id: str

    # エントリー時情報
    strike: float
    expiry: date
    dte_entry: int
    premium_entry: float
    delta_entry: Optional[float]

    # イグジット時情報
    premium_exit: Optional[float] = None
    exit_reason: Optional[str] = None  # "time_stop", "structural_stop", "take_profit", etc.

    # 損益
    pnl: Optional[float] = None
    pnl_pct: Optional[float] = None

    # 最大・最小価格
    max_premium: Optional[float] = None
    min_premium: Optional[float] = None

    @property
    def is_closed(self) -> bool:
        """クローズ済みか"""
        return self.exit_date is not None

    def calculate_pnl(self):
        """損益を計算"""
        if self.premium_exit is not None:
            self.pnl = self.premium_exit - self.premium_entry
            self.pnl_pct = (self.premium_exit / self.premium_entry - 1) * 100
