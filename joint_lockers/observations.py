"""把原始寄取凭证与申请整理为团期联合观测。

四类需区分的情形（与正常服务并列）：
- 无预约/无柜等候：demand 中有申请但找不到对应寄取凭证；
- 明确取消：usage 状态为“取消”，柜位未被占用；
- 失联而未释放：usage 出现“失联未释放”，若其后有“失约后返回”，
  保留两条凭证并记录返回时间，不把前一段堵柜抹掉；
- 到观察时仍占柜：观察截止（calendar.data_as_of）时 collected 为空，
  包括“占柜未结束”和失约返回后仍未取回。

风险的最小观测单位是团期（period），同一团期、同一场雨下的多柜联动
合并到一张观测卡，绝不拆成单柜独立样本。
"""
from __future__ import annotations

from collections import defaultdict

from .timewin import to_min

# 团期卡上使用的稳定事件代码
JOINT_ADVERSE_BIG = "joint_adverse_big"       # 两站大柜在同一团期同时受损
UNMET_BIG = "unmet_big"                       # 任一站有大柜申请未满足
EARLY_SURGE = "early_pickup_surge"            # 雨天提前取包后又有新到站申请
STILL_OCCUPIED = "still_occupied_at_cutoff"   # 观察截止仍占柜
NO_SHOW_BLOCK = "no_show_block"               # 失联未释放造成堵柜
CANCELLED = "cancelled"

OPEN_STATES = {"占柜未结束", "失联未释放", "失约后返回"}


def _locker_index(book: dict) -> dict[str, dict]:
    return {row["locker"]: row for row in book["lockers"]}


def build_tickets(book: dict) -> list[dict]:
    """按 ticket 合并同一凭证的多行观测（失约与返回是同一家庭的连续事件）。"""
    locker_of = _locker_index(book)
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in book["usage"]:
        grouped[row["ticket"]].append(row)

    tickets: list[dict] = []
    for ticket, rows in grouped.items():
        # 保留资料中的凭证行序：失联未释放在前、失约后返回在后，不按时间重排
        rows = list(rows)
        first = rows[0]
        statuses = [r["status"] for r in rows]
        locker = first["locker"]
        meta = locker_of[locker]
        as_of = _as_of(book, first["period"])

        placed_rows = [r for r in rows if r["placed"] is not None]
        first_placed = to_min(placed_rows[0]["placed"]) if placed_rows else None
        last_collect = max((to_min(r["collected"]) for r in rows if r["collected"] is not None),
                           default=None)
        open_at_cutoff = bool(placed_rows) and last_collect is None

        if "失联未释放" in statuses:
            lifecycle = "失联后返回仍占柜" if "失约后返回" in statuses else "失联未释放"
        elif "取消" in statuses:
            lifecycle = "明确取消"
        elif "占柜未结束" in statuses:
            lifecycle = "观察时仍占柜"
        elif "早取" in statuses:
            lifecycle = "提前取包"
        else:
            lifecycle = "正常完成"

        # 堵柜截止时间：失约未释放段持续到返回事件发生；仍占柜持续到观察截止
        blocked_until = None
        if "失联未释放" in statuses and "失约后返回" in statuses:
            blocked_until = to_min(next(r["placed"] for r in rows if r["status"] == "失约后返回"))
        elif open_at_cutoff:
            blocked_until = as_of

        tickets.append({
            "ticket": ticket,
            "period": first["period"],
            "group": first["group"],
            "locker": locker,
            "station": meta["station"],
            "kind": meta["kind"],
            "events": statuses,
            "lifecycle": lifecycle,
            "placed": first_placed,
            "collected": last_collect,
            "open_at_cutoff": open_at_cutoff,
            "blocked_until": blocked_until,
        })
    return sorted(tickets, key=lambda t: (t["period"], t["ticket"]))


def _as_of(book: dict, period: str) -> int:
    row = next(r for r in book["calendar"] if r["period"] == period)
    return to_min(row["data_as_of"])


def classify_demand(book: dict, tickets: list[dict]) -> list[dict]:
    """把每条申请标记为已满足或无柜等候，并给出四类情形归属。"""
    placed_index: dict[tuple, list[dict]] = defaultdict(list)
    for t in tickets:
        if t["placed"] is None:
            continue
        placed_index[(t["period"], t["group"], t["station"], t["kind"], t["placed"])].append(t)

    result: list[dict] = []
    for row in book["demand"]:
        locker_meta = None
        arrived = to_min(row["arrived"])
        key = (row["period"], row["group"], row["station"], row["kind"], arrived)
        matched = placed_index.get(key, [])
        ticket = matched.pop(0) if matched else None
        if ticket is not None:
            bucket = "已寄存"
            if ticket["lifecycle"] == "失联后返回仍占柜":
                bucket = "失约返回后仍占柜"
            elif ticket["lifecycle"] == "观察时仍占柜":
                bucket = "观察时仍占柜"
        else:
            bucket = "无柜等候"
        result.append({
            "ref": row["ref"],
            "period": row["period"],
            "group": row["group"],
            "station": row["station"],
            "kind": row["kind"],
            "arrived": arrived,
            "expected_pickup": to_min(row["expected_pickup"]),
            "ticket": ticket["ticket"] if ticket else None,
            "served": ticket is not None,
            "bucket": bucket,
        })
    return result


def build_period_cards(book: dict, tickets: list[dict], demand_classes: list[dict]) -> list[dict]:
    """每个团期一张联合观测卡：天气、两队、两站大柜受损与联动事件。"""
    tickets_by_period: dict[str, list[dict]] = defaultdict(list)
    for t in tickets:
        tickets_by_period[t["period"]].append(t)
    demand_by_period: dict[str, list[dict]] = defaultdict(list)
    for d in demand_classes:
        demand_by_period[d["period"]].append(d)

    cards: list[dict] = []
    for cal in book["calendar"]:
        period = cal["period"]
        as_of = to_min(cal["data_as_of"])
        period_tickets = tickets_by_period.get(period, [])
        period_demand = demand_by_period.get(period, [])

        unmet = [d for d in period_demand if not d["served"]]
        unmet_big_stations = {d["station"] for d in unmet if d["kind"] == "大"}
        blocked_big_stations = set()
        for t in period_tickets:
            if t["kind"] != "大":
                continue
            if t["lifecycle"] in ("失联未释放", "失联后返回仍占柜", "观察时仍占柜"):
                blocked_big_stations.add(t["station"])

        adverse_stations = unmet_big_stations | blocked_big_stations
        events: list[str] = []
        if unmet_big_stations:
            events.append(UNMET_BIG)
        if len(adverse_stations) >= 2:
            events.append(JOINT_ADVERSE_BIG)
        if any(t["lifecycle"] in ("失联未释放", "失联后返回仍占柜") for t in period_tickets):
            events.append(NO_SHOW_BLOCK)
        if any(t["open_at_cutoff"] for t in period_tickets):
            events.append(STILL_OCCUPIED)
        if any(t["lifecycle"] == "明确取消" for t in period_tickets):
            events.append(CANCELLED)
        # 雨天提前取包 + 同团期随后又有到站申请，即题目所述的合流挤压
        early_groups = {t["group"] for t in period_tickets if t["lifecycle"] == "提前取包"}
        early_times = [t["collected"] for t in period_tickets if t["lifecycle"] == "提前取包"]
        later_arrivals = [
            d for d in period_demand
            if early_times and d["arrived"] is not None
            and d["arrived"] >= min(x for x in early_times if x is not None)
        ]
        if cal["weather"] == "雨" and early_groups and later_arrivals:
            events.append(EARLY_SURGE)

        cards.append({
            "period": period,
            "date": cal["date"],
            "weather": cal["weather"],
            "teams": list(cal["teams"]),
            "data_as_of": as_of,
            "events": events,
            "adverse_stations": sorted(adverse_stations),
            "unmet_refs": [d["ref"] for d in unmet],
            "unmet_by_station_kind": _count(unmet, ("station", "kind")),
            "blocked_lockers": sorted({
                t["locker"] for t in period_tickets
                if t["lifecycle"] in ("失联未释放", "失联后返回仍占柜", "观察时仍占柜")
            }),
            "tickets": [t["ticket"] for t in period_tickets],
        })
    return cards


def _count(rows: list[dict], keys: tuple[str, ...]) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for row in rows:
        out["|".join(str(row[k]) for k in keys)] += 1
    return dict(out)
