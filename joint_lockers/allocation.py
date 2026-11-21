"""停用情景下的换站有效性判定与成套预约分柜。

有效性的硬条件（任一不满足就不是有效替换，只能进人工缺口）：
1. 家长在 can_move 中接受该替代站（已签旧约按已告知条件，不放宽）；
2. 柜型与申请物件规格一致（大柜需求不能用小柜顶替）；
3. 替代柜的可用时段（开启窗减去停用窗）完整覆盖 start..collect_by；
4. 家长可在原领取条件内取回：换站动作必须在预约开始时即可完成，
   且单程路程 + min_buffer 不突破原 collect_by；
5. 同一实体柜同一时间窗只能被一个预约引用（L5 被两站策略同时引用，
   仍是一个柜，先保旧约，绝不双计）。

旧预约按已告知时窗与物品限制优先保护；不得取消正常小柜预约来腾挪，
也不得把单实体备用柜的缺口责任推给后到家庭。
"""
from __future__ import annotations

from collections import defaultdict

from .indexing import build_travel, locker_index
from .timewin import covers_any, gap_minutes, subtract, to_min

GUARANTEED = "有效保障"
HOME_LOCKED = "本站锁定无需换站"
MANUAL_GAP = "人工承接缺口"
BEST_EFFORT = "尽力安排(不构成保障)"
OLD_BOOKING = "已签"
NEW_REQUEST = "新请求"

HOME = "本站柜位"
SUBSTITUTE = "换站替代"


def build_policy_index(book: dict) -> dict[str, dict]:
    """同名策略合并为一条，内含各站的替代柜列表与缓冲、风险上限。"""
    merged: dict[str, dict] = {}
    for row in book["policies"]:
        item = merged.setdefault(row["policy"], {"policy": row["policy"], "stations": {}})
        item["stations"][row["station"]] = {
            "station": row["station"],
            "substitutes": list(row["substitutes"]),
            "min_buffer": row["min_buffer"],
            "risk_ceiling": row["risk_ceiling"],
        }
    return merged


def locker_windows(book: dict, outages: list[dict] | None = None) -> dict[str, list[tuple[int, int]]]:
    """每个柜在当前团期的可用闭窗（开启窗减去停用窗）。"""
    outage_rows = book["outages"] if outages is None else outages
    holes: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for row in outage_rows:
        holes[row["locker"]].append((to_min(row["start"]), to_min(row["end"])))

    windows: dict[str, list[tuple[int, int]]] = {}
    for row in book["lockers"]:
        base = (to_min(row["since"]), to_min(row["until"]))
        windows[row["locker"]] = subtract(base, holes.get(row["locker"], []))
    return windows


def feasible_substitutes(policy: dict, need_kind: str, need_window: tuple[int, int],
                         windows: dict[str, list[tuple[int, int]]], travel: dict[tuple[str, str], int],
                         home_station: str, blocked_lockers: set[str],
                         used_substitutes: set[str], consent_stations: list[str] | None,
                         lockers: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    """返回 (可行替代列表, 不可行原因清单)。可行列表按策略声明顺序排列。"""
    feasible: list[dict] = []
    reasons: list[dict] = []
    for locker in policy["substitutes"]:
        meta = lockers[locker]
        why = []
        if meta["kind"] != need_kind:
            why.append(f"柜型不符：需要{need_kind}，该柜为{meta['kind']}")
        if consent_stations is not None and meta["station"] not in consent_stations:
            why.append("家长未接受该替代站(can_move)")
        if locker in blocked_lockers:
            why.append("该柜在同团期被堵柜/占用")
        if locker in used_substitutes:
            why.append("单实体备用柜已被前一预约引用，不能双计")
        reachable = True
        if meta["station"] != home_station:
            leg = travel.get((home_station, meta["station"]))
            if leg is None:
                reachable = False
                why.append("无站间通路(movements 缺记录)")
            else:
                travel_deadline = need_window[1] - leg - policy["min_buffer"]
                if need_window[0] > travel_deadline:
                    why.append(
                        f"换站来不及：{leg} 分钟路程 + {policy['min_buffer']} 分钟缓冲后，"
                        f"最迟须在 {_hm(travel_deadline)} 前动身，预约开始为 {_hm(need_window[0])}"
                    )
                    reachable = False
        if not covers_any(windows.get(locker, []), need_window):
            why.append("可用时段不能完整覆盖原领取窗")
        if why:
            reasons.append({"locker": locker, "station": meta["station"], "reasons": why})
        else:
            feasible.append({
                "locker": locker,
                "station": meta["station"],
                "kind": meta["kind"],
                "reachable_within_original_terms": reachable,
            })
    return feasible, reasons


def plan_allocation(book: dict, risk_summary: dict) -> dict:
    """停用情景下重判全网可卖新单与旧预约通路，输出逐预约结果与调整台账。"""
    policies = build_policy_index(book)
    windows = locker_windows(book)
    travel = build_travel(book)
    lockers = locker_index(book)
    outaged = {row["locker"] for row in book["outages"]}

    old_bookings = sorted((b for b in book["bookings"] if b["state"] == OLD_BOOKING),
                          key=lambda b: to_min(b["start"]))
    new_requests = sorted((b for b in book["bookings"] if b["state"] == NEW_REQUEST),
                          key=lambda b: to_min(b["start"]))

    used: set[str] = set()
    outcomes: list[dict] = []
    ledger: list[dict] = []

    def apply_policy(booking: dict, protected: bool) -> dict:
        need_window = (to_min(booking["start"]), to_min(booking["collect_by"]))
        home = booking["locker"]
        home_available = (
            home is not None
            and home not in outaged
            and covers_any(windows.get(home, []), need_window)
        )
        if home_available:
            used.add(home)
            outcome = _outcome(booking, HOME, home, HOME_LOCKED, [], protected, lockers)
            ledger.append(_ledger(booking, outcome, "本站柜位可用，保持锁定"))
            return outcome

        station_policy = _station_policy(policies, booking["station"])
        if station_policy is None:
            outcome = _outcome(
                booking, None, None, MANUAL_GAP,
                [{"locker": None, "reasons": ["该站无共享策略且本站柜位不可用"]}],
                protected, lockers,
            )
            ledger.append(_ledger(booking, outcome, "无策略可承接，登记人工缺口"))
            return outcome

        feasible, reasons = feasible_substitutes(
            policy=station_policy,
            need_kind=booking["kind"],
            need_window=need_window,
            windows=windows,
            travel=travel,
            home_station=booking["station"],
            blocked_lockers=set(),
            used_substitutes=used,
            consent_stations=booking["can_move"] if booking["can_move"] else [],
            lockers=lockers,
        )
        if feasible:
            chosen = feasible[0]
            used.add(chosen["locker"])
            outcome = _outcome(booking, SUBSTITUTE, chosen["locker"], GUARANTEED, reasons,
                               protected, lockers, substitute_station=chosen["station"])
            ledger.append(_ledger(
                booking, outcome,
                f"按 {station_policy['policy']} 策略签出替代柜 {chosen['locker']}"
                f"（{chosen['station']}），原领取条件不变",
            ))
            return outcome

        best_effort = _best_effort(station_policy, booking, windows, used)
        outcome = _outcome(booking, None, None, MANUAL_GAP, reasons, protected, lockers,
                           best_effort=best_effort)
        ledger.append(_ledger(
            booking, outcome,
            "无满足全部硬条件的替代；旧约保护优先，缺口由人工承接，不得后推责任",
        ))
        return outcome

    # 旧约先分，新单后分；新单永远不能挤掉旧约
    for booking in old_bookings:
        outcomes.append(apply_policy(booking, protected=True))
    for booking in new_requests:
        outcomes.append(apply_policy(booking, protected=False))

    return {
        "outage_windows": book["outages"],
        "outcomes": outcomes,
        "ledger": ledger,
        "issuance_limits": _issuance_limits(book, policies, windows, used, risk_summary, lockers),
        "single_inventory_note": _single_inventory_note(book),
    }


def _station_policy(policies: dict, station: str) -> dict | None:
    matches = []
    for policy in policies.values():
        if station in policy["stations"]:
            chosen = dict(policy["stations"][station])
            chosen["policy"] = policy["policy"]
            matches.append(chosen)
    if not matches:
        return None
    # 优先选实际配置了替代柜的共享策略；“不共享”只作对照基线
    matches.sort(key=lambda spec: len(spec["substitutes"]), reverse=True)
    return matches[0]


def _best_effort(policy: dict, booking: dict, windows: dict, used: set[str]) -> dict | None:
    need_window = (to_min(booking["start"]), to_min(booking["collect_by"]))
    candidates = []
    for locker in policy["substitutes"]:
        uncovered = gap_minutes(windows.get(locker, []), need_window)
        note = "该实体柜已被前一预约占用，仅作缺口量化，不可再分配" if locker in used else \
            "仅覆盖部分领取窗，不构成有效保障，必须显式告知人工缺口"
        candidates.append((uncovered, locker, note))
    if not candidates:
        return None
    uncovered, locker, note = min(candidates, key=lambda x: x[0])
    return {
        "locker": locker,
        "already_committed": locker in used,
        "uncovered_minutes": uncovered,
        "is_guarantee": False,
        "note": note,
    }


def _outcome(booking: dict, route: str | None, assigned: str | None, result: str,
             reasons: list[dict], protected: bool, lockers: dict[str, dict],
             substitute_station: str | None = None,
             best_effort: dict | None = None) -> dict:
    home = booking["locker"]
    return {
        "booking": booking["booking"],
        "group": booking["group"],
        "station": booking["station"],
        "kind": booking["kind"],
        "state": booking["state"],
        "protected": protected,
        "window": [to_min(booking["start"]), to_min(booking["collect_by"])],
        "home_locker": home,
        "home_station": lockers[home]["station"] if home else booking["station"],
        "can_move": booking["can_move"],
        "route": route,
        "assigned_locker": assigned,
        "substitute_station": substitute_station,
        "result": result,
        "reject_reasons": reasons,
        "best_effort": best_effort,
    }


def _ledger(booking: dict, outcome: dict, action: str) -> dict:
    return {
        "booking": booking["booking"],
        "group": booking["group"],
        "state": booking["state"],
        "action": action,
        "result": outcome["result"],
        "assigned_locker": outcome["assigned_locker"],
    }


def _issuance_limits(book: dict, policies: dict, windows: dict, used: set[str],
                     risk_summary: dict, lockers: dict[str, dict]) -> list[dict]:
    """停用后全网还能继续放卖的保障单限额；低可信/不可估计时自动限额为 0。"""
    rainy = risk_summary["strata"].get("雨", {})
    low_confidence = any(
        rate["confidence"] == "低可信"
        for rate in rainy.get("event_rates", {}).values()
    )
    unestimable = bool(risk_summary.get("unestimable_confluences"))
    limits = []
    for name, policy in policies.items():
        for station, spec in policy["stations"].items():
            free_big = [
                locker for locker in spec["substitutes"]
                if locker not in used and lockers[locker]["kind"] == "大"
                and windows.get(locker)
            ]
            auto_quota = 0 if (low_confidence or unestimable) else len(free_big)
            limits.append({
                "policy": name,
                "station": station,
                "risk_ceiling": spec["risk_ceiling"],
                "free_substitute_lockers": free_big,
                "automatic_guarantee_quota": auto_quota,
                "manual_approval_required": low_confidence or unestimable,
                "reason": (
                    "雨天分层样本低可信且存在无法估计的合流条件，"
                    "风险上限无法被统计验证，自动签发限额置 0"
                    if auto_quota == 0 else "估计可信，可按剩余实体柜数签发"
                ),
            })
    return limits


def _single_inventory_note(book: dict) -> dict:
    refs: dict[str, list[str]] = defaultdict(list)
    for row in book["policies"]:
        for locker in row["substitutes"]:
            refs[locker].append(f"{row['policy']}/{row['station']}")
    shared = {locker: where for locker, where in refs.items() if len(where) > 1}
    return {
        "shared_single_inventory": shared,
        "rule": "同一柜被多策略引用仍只有一个实体，分柜按先旧约后新单串行占用，"
                "无法全保时登记人工缺口，不得把责任移给后到家庭",
    }


def _hm(value: int | None) -> str:
    if value is None:
        return "—"
    return f"{value // 60:02d}:{value % 60:02d}"
