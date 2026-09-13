"""オプション銘柄の選定（プレミアム基準 / デルタ基準）

`premium_range`（20〜40円）と `delta_range`（-0.40〜-0.25）が指す銘柄は全く別物で、
両方をANDで適用すると候補が常に空になる。どちらを基準にするかを
`option_selection.selection_mode` で排他的に決める。
詳細は docs/gate_vi_validation.md 4.2。
"""
from typing import Any, Dict, List

from ..models.option import OptionData

PREMIUM_MODE = "premium"
DELTA_MODE = "delta"
VALID_MODES = (PREMIUM_MODE, DELTA_MODE)

# 基準値が取れない銘柄を並べ替えの最後に回すための距離
_UNRANKED = float("inf")


def selection_mode(opt_config: Dict[str, Any]) -> str:
    """選定モードを取り出す。未知の値は設定ミスなので黙って既定に落とさず弾く。"""
    mode = str(opt_config.get("selection_mode", PREMIUM_MODE)).lower()
    if mode not in VALID_MODES:
        raise ValueError(
            f"option_selection.selection_mode が不正です: {mode!r} "
            f"（有効な値: {', '.join(VALID_MODES)}）"
        )
    return mode


def select_candidates(
    options: List[OptionData], opt_config: Dict[str, Any]
) -> List[OptionData]:
    """条件に合うプットを、目標値に近い順に並べて返す。

    Args:
        options: オプションデータ（プット以外を含んでいてよい）
        opt_config: config の option_selection セクション

    Returns:
        目標値に近い順に並べた候補のリスト
    """
    mode = selection_mode(opt_config)
    dte_range = opt_config["dte_range"]

    candidates = []
    for opt in options:
        if opt.option_type != "Put":
            continue
        if not opt.is_in_dte_range(dte_range["min"], dte_range["max"]):
            continue
        if mode == DELTA_MODE:
            delta_range = opt_config["delta_range"]
            if not opt.is_in_delta_range(delta_range["min"], delta_range["max"]):
                continue
        else:
            premium_range = opt_config["premium_range"]
            if not opt.is_in_premium_range(premium_range["min"], premium_range["max"]):
                continue
        candidates.append(opt)

    if mode == DELTA_MODE:
        target = opt_config["target_delta"]
        key = lambda o: _or_unranked(o.delta_distance_from_target(target))  # noqa: E731
    else:
        target = opt_config["target_premium"]
        key = lambda o: _or_unranked(o.premium_distance_from_target(target))  # noqa: E731

    return sorted(candidates, key=key)


def select_best(
    options: List[OptionData], opt_config: Dict[str, Any]
) -> OptionData | None:
    """最も目標値に近い1銘柄を返す。該当なしなら None。"""
    candidates = select_candidates(options, opt_config)
    return candidates[0] if candidates else None


def _or_unranked(distance: float | None) -> float:
    return _UNRANKED if distance is None else distance
