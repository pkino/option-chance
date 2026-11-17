"""イベントカレンダー（config の events）の読み込みと照会

config の events は次の2形式を受け付ける（混在可）。

    events:
      - "2025-12-18"                                   # 旧形式（名前なし。tier は major 扱い）
      - {date: "2026-12-09", name: "FOMC", tier: major}

tier:
  major = 相場の方向を変えうる大型イベント（FOMC・日銀会合・メジャーSQ・国政選挙など）。
          イベント前打診（event_probe）の対象
  minor = 定例の指標・SQ（米CPI・米雇用統計・ミニSQなど）。Gate②C の C1 では数えるが打診の対象外
"""
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

EVENT_TIERS = ("major", "minor")
DEFAULT_TIER = "major"

# 最後の登録イベントまでの残り日数がこれを切ったら「カレンダーを更新して」と警告する
STALE_CALENDAR_DAYS = 30


@dataclass(frozen=True)
class Event:
    date: date
    name: str
    tier: str


def _to_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, pd.Timestamp):
        return value.date()
    return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()


def load_events(raw: Optional[Iterable[Any]]) -> List[Event]:
    """config の events を Event のリスト（日付昇順）に正規化する。"""
    events: List[Event] = []
    for item in raw or []:
        if isinstance(item, Event):
            events.append(item)
            continue
        if isinstance(item, dict):
            if "date" not in item:
                raise ValueError(f"events の要素に date がありません: {item!r}")
            tier = str(item.get("tier", DEFAULT_TIER)).lower()
            if tier not in EVENT_TIERS:
                raise ValueError(
                    f"events の tier は {', '.join(EVENT_TIERS)} のいずれかを指定してください: {item!r}"
                )
            events.append(Event(_to_date(item["date"]), str(item.get("name", "イベント")), tier))
        else:
            # 旧形式（日付文字列のみ）。YAML が date 型に読む場合もある
            events.append(Event(_to_date(item), "イベント", DEFAULT_TIER))

    return sorted(events, key=lambda e: (e.date, e.name))


def upcoming_events(
    events: Sequence[Event],
    current: date,
    min_days: int,
    max_days: int,
    tiers: Optional[Sequence[str]] = None,
) -> List[Tuple[Event, int]]:
    """current から min_days〜max_days 暦日先にあるイベントを (Event, 残日数) で近い順に返す。"""
    result = []
    for event in events:
        if tiers is not None and event.tier not in tiers:
            continue
        days = (event.date - current).days
        if min_days <= days <= max_days:
            result.append((event, days))
    return sorted(result, key=lambda pair: (pair[1], pair[0].name))


def last_event_date(events: Sequence[Event]) -> Optional[date]:
    return max((e.date for e in events), default=None)


def calendar_staleness(
    events: Sequence[Event], today: date, min_days: int = STALE_CALENDAR_DAYS
) -> Optional[str]:
    """カレンダーの更新が必要なら警告文を、不要なら None を返す。"""
    last = last_event_date(events)
    if last is None:
        return "イベントカレンダー（config の events）が空です。主要イベントを登録してください。"
    remaining = (last - today).days
    if remaining < min_days:
        state = f"{-remaining}日前に終わっています" if remaining < 0 else f"残り{remaining}日です"
        return (
            f"イベントカレンダーは {last.isoformat()} までしか登録されておらず、{state}。"
            "config/config.yaml の events に先の日程を追加してください。"
        )
    return None
