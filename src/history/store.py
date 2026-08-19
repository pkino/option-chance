"""日次判定結果の履歴ストア（JSONL）

毎日バッチ（scripts/daily_check.py）は Slack 通知と標準出力にしか結果を出さず、
判定条件の時系列変化を後から追えなかった。ここでは 1 判定日 = 1 レコードの
JSONL として結果と判定根拠の指標値を蓄積し、ダッシュボード
（scripts/build_dashboard.py）の入力にする。
"""
import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from ..models.option import Signal

# 履歴ファイルの既定パス（プロジェクトルート基準）
DEFAULT_HISTORY_PATH = Path(__file__).parent.parent.parent / "history" / "signals.jsonl"

# config.yaml のうち「判定条件」にあたるサブツリー。
# ここが変わった日をダッシュボード上で識別できるよう、ハッシュを各レコードに残す。
THRESHOLD_KEYS = ["gate_vi", "gate_top", "trigger", "risk_management", "option_selection"]


def extract_thresholds(config: Dict[str, Any]) -> Dict[str, Any]:
    """判定条件にあたる設定サブツリーだけを抜き出す。"""
    return {key: config[key] for key in THRESHOLD_KEYS if key in config}


def compute_config_hash(config: Dict[str, Any]) -> str:
    """判定条件サブツリーのハッシュ（先頭8桁）。閾値変更の検知に使う。"""
    payload = json.dumps(extract_thresholds(config), sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]


def build_record(
    signal: Signal,
    config: Dict[str, Any],
    run_date: Optional[date] = None,
) -> Dict[str, Any]:
    """Signal から履歴レコードを組み立てる。

    Args:
        signal: 判定結果
        config: 判定に使った設定（config.yaml 相当）
        run_date: バッチ実行日（省略時は今日）

    Returns:
        JSONL 1 行分の dict
    """
    details = signal.details or {}
    details_a = details.get("gate_top_a", {})
    details_b = details.get("gate_top_b", {})
    details_c = details.get("gate_top_c", {})
    details_trigger = details.get("trigger", {})

    return {
        "date": signal.date.isoformat(),
        "run_date": (run_date or date.today()).isoformat(),
        "flags": {
            "gate_vi": bool(signal.gate_vi),
            "gate_top_a": bool(signal.gate_top_a),
            "gate_top_b": bool(signal.gate_top_b),
            "gate_top_c": bool(signal.gate_top_c),
            "gate_top": bool(signal.gate_top),
            "trigger": bool(signal.trigger),
            "is_entry_signal": bool(signal.is_entry_signal),
            "is_probe_signal": bool(signal.is_probe_signal),
            "is_supply_dominant_entry": bool(signal.is_supply_dominant_entry),
            "is_strong_signal": bool(signal.is_strong_signal),
            "cooldown_suppressed": bool(details.get("cooldown_suppressed", False)),
        },
        "sub": {
            "a1": bool(details_a.get("a1_rsi_reversal", False)),
            "a2": bool(details_a.get("a2_macd_weakening", False)),
            "a3": bool(details_a.get("a3_divergence", False)),
            "a4": bool(details_a.get("a4_overbought", False)),
            "b1": bool(details_b.get("b1_volume_failure", False)),
            "b2": bool(details_b.get("b2_upper_wick_dominance", False)),
            "b3": bool(details_b.get("b3_gap_up_failure", False)),
            "c1": bool(details_c.get("c1_event_proximity", False)),
            "prev_low_break": bool(details_trigger.get("prev_low_break", False)),
            "ma5_break": bool(details_trigger.get("ma5_break", False)),
        },
        "metrics": dict(details.get("technical_values", {})),
        "config_hash": compute_config_hash(config),
        "thresholds": extract_thresholds(config),
    }


def load_records(path: Union[str, Path] = DEFAULT_HISTORY_PATH) -> List[Dict[str, Any]]:
    """履歴を読み込む。ファイルがなければ空リストを返す。"""
    path = Path(path)
    if not path.exists():
        return []

    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            # 壊れた行があっても履歴全体を落とさない
            print(f"⚠️ 履歴の不正な行をスキップしました: {line[:80]}")

    return _sorted_by_date(records)


def append_record(
    record: Dict[str, Any], path: Union[str, Path] = DEFAULT_HISTORY_PATH
) -> List[Dict[str, Any]]:
    """レコードを追記する。同じ date が既にあれば上書き（同日再実行を冪等にする）。

    Returns:
        書き込み後の全レコード
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    records = [r for r in load_records(path) if r.get("date") != record.get("date")]
    records.append(record)
    records = _sorted_by_date(records)

    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")

    return records


def _sorted_by_date(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """判定日の昇順に並べる。"""
    return sorted(records, key=lambda r: str(r.get("date", "")))


def parse_date(value: str) -> date:
    """YYYY-MM-DD を date に変換する。"""
    return datetime.strptime(value, "%Y-%m-%d").date()
