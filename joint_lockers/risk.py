"""以团期为单位的分层风险比较与尾部堆积模拟。

统计口径刻意不使用“单柜空置率 × 柜数”的独立概率：同一团期、同一场雨
引起的两站联动只有一次联合观测，拆成单柜会把尾部联动稀释掉。
样本很少（每层只有 2–3 个团期），因此：
- 任何估计都同时给出样本量与低可信标记；
- 从未出现过的合流条件不填 0，标记为“无法估计”，仅给单边 3/n 上界
  （rule of three，仅供排查，不可当作概率使用）；
- 尾部模拟用实际可配给的柜型与领取窗逐单判定，而不是平均容量。
"""
from __future__ import annotations

from collections import defaultdict

from .observations import JOINT_ADVERSE_BIG

MIN_STRATUM_SAMPLES = 4

# 罕见合流条件：样册中无观测，禁止用 0 冒充估计
RARE_CONFLUENCES = [
    {
        "code": "dual_station_same_backup_contention",
        "name": "两站在同一团期争抢同一备用柜",
        "detail": "L5 被两站联营策略同时引用但只有一个实体；历史团期没有同时争抢的记录。",
    },
    {
        "code": "rain_cohort_double_arrival_confluence",
        "name": "雨天提前取包与下一批提前到站的三向合流",
        "detail": "早取、未释放堵柜、新申请到站在同一时间窗叠加；样册只有两向挤压的观测。",
    },
]


def compare_strata(cards: list[dict]) -> dict:
    """按天气分层，统计团期级事件频率与独立口径对照值。"""
    strata: dict[str, list[dict]] = defaultdict(list)
    for card in cards:
        strata[card["weather"]].append(card)

    event_codes = sorted({event for card in cards for event in card["events"]})
    result = {}
    for weather, group in sorted(strata.items()):
        n = len(group)
        frequencies = {}
        for code in event_codes:
            hits = sum(1 for card in group if code in card["events"])
            frequencies[code] = {
                "periods_hit": [c["period"] for c in group if code in card["events"]],
                "count": hits,
                "sample_periods": n,
                "rate": hits / n,
                "confidence": "低可信" if n < MIN_STRATUM_SAMPLES else "可用",
                "note": "样本不足，仅作分层对比，不可外推" if n < MIN_STRATUM_SAMPLES else "",
            }
        # 独立口径对照：若把每站当作独立事件相乘得到的联合概率，
        # 仅用于展示独立假设会把联动稀释，不参与任何保障决策。
        station_hit = defaultdict(int)
        for card in group:
            for station in card["adverse_stations"]:
                station_hit[station] += 1
        station_rates = {station: hits / n for station, hits in station_hit.items()}
        if station_rates:
            naive_joint = 1.0
            for rate in station_rates.values():
                naive_joint *= rate
        else:
            naive_joint = 0.0
        observed_joint = frequencies.get(JOINT_ADVERSE_BIG, {}).get("rate", 0.0)
        result[weather] = {
            "sample_periods": n,
            "periods": [c["period"] for c in group],
            "event_rates": frequencies,
            "joint_adverse_big_rate": observed_joint,
            "naive_independent_reference_rate": naive_joint,
            "independence_dilution_note": (
                f"独立口径给出联合受损率 {naive_joint:.3f}，团期联合观测为 {observed_joint:.3f}；"
                "两站坏结果来自同一场雨/同一团期时，独立口径会系统性偏低。"
            ),
        }

    unestimable = []
    total = len(cards)
    for item in RARE_CONFLUENCES:
        unestimable.append({
            **item,
            "observed_periods": 0,
            "sample_periods": total,
            "rule_of_three_upper_bound": 3 / total if total else None,
            "estimate": None,
            "reason": "无观测且机制上可能发生，保留为无法估计，不得以 0 进入限额计算",
        })
    return {"strata": result, "unestimable_confluences": unestimable}


def simulate_policy_tails(book: dict, cards: list[dict], demand_classes: list[dict],
                          tickets: list[dict]) -> dict:
    """对历史上出现过大柜未满足的团期，逐单模拟各策略的实际配给结果。"""
    from .allocation import build_policy_index, feasible_substitutes, locker_windows
    from .indexing import locker_index

    policies = build_policy_index(book)
    windows = locker_windows(book, outages=[])  # 历史期没有停用窗记录
    lockers = locker_index(book)
    travel = {(m["from_station"], m["to_station"]): m["minutes"] for m in book["movements"]}
    blocked = _historical_blocked(tickets)

    unmet_by_period: dict[str, list[dict]] = defaultdict(list)
    for d in demand_classes:
        if not d["served"] and d["kind"] == "大":
            unmet_by_period[d["period"]].append(d)

    policy_results: dict[str, list[dict]] = defaultdict(list)
    for policy_name in policies:
        for period, demands in sorted(unmet_by_period.items()):
            card = next(c for c in cards if c["period"] == period)
            occupied = _used_lockers(book, tickets, period)
            used_substitutes: set[str] = set()
            order = sorted(demands, key=lambda d: d["arrived"])
            lines = []
            for d in order:
                spec = policies[policy_name]["stations"].get(d["station"])
                if spec is None:
                    feasible, reasons = [], [{
                        "locker": None,
                        "station": d["station"],
                        "reasons": ["该策略未覆盖此站，无任何可配给柜型"],
                    }]
                elif not spec["substitutes"]:
                    feasible, reasons = [], [{
                        "locker": None,
                        "station": d["station"],
                        "reasons": ["该策略在本站不配置共享柜，缺口只能留在本站"],
                    }]
                else:
                    station_policy = dict(spec)
                    station_policy["policy"] = policy_name
                    feasible, reasons = feasible_substitutes(
                        policy=station_policy,
                        need_kind=d["kind"],
                        need_window=(d["arrived"], d["expected_pickup"]),
                        windows=windows,
                        travel=travel,
                        home_station=d["station"],
                        blocked_lockers=blocked.get(period, set()) | occupied,
                        used_substitutes=used_substitutes,
                        consent_stations=None,  # 历史申请无 can_move，按物理可行性上限测算
                        lockers=lockers,
                    )
                chosen = feasible[0] if feasible else None
                if chosen is not None:
                    used_substitutes.add(chosen["locker"])
                lines.append({
                    "ref": d["ref"],
                    "station": d["station"],
                    "window": [d["arrived"], d["expected_pickup"]],
                    "served_by": chosen["locker"] if chosen else None,
                    "served": chosen is not None,
                    "reject_reasons": reasons if not chosen else [],
                })
            policy_results[policy_name].append({
                "period": period,
                "weather": card["weather"],
                "unmet_demand": len(order),
                "cleared": sum(1 for x in lines if x["served"]),
                "residual": sum(1 for x in lines if not x["served"]),
                "joint_period": JOINT_ADVERSE_BIG in card["events"],
                "assignments": lines,
            })

    summary = {}
    for policy_name, period_lines in policy_results.items():
        residual_by_period = [p["residual"] for p in period_lines]
        summary[policy_name] = {
            "periods_simulated": len(period_lines),
            "worst_residual_units": max(residual_by_period, default=0),
            "joint_periods_with_residual": sum(
                1 for p in period_lines if p["joint_period"] and p["residual"] > 0
            ),
            "by_period": period_lines,
        }
    summary["policy_profiles"] = _policy_profiles(book, policy_results, policies)
    return summary


def _policy_profiles(book: dict, policy_results: dict, policies: dict) -> list[dict]:
    """把每个备选政策讲清楚：容量从哪个站来、最易断供的窗与物件、联动归因。"""
    lockers = {row["locker"]: row for row in book["lockers"]}
    profiles = []
    for name, policy in policies.items():
        capacity_sources = []
        for station, spec in policy["stations"].items():
            capacity_sources.append({
                "home_station": station,
                "substitute_lockers": [
                    {"locker": locker, "station": lockers[locker]["station"],
                     "kind": lockers[locker]["kind"],
                     "open_window": [lockers[locker]["since"], lockers[locker]["until"]]}
                    for locker in spec["substitutes"]
                ],
                "min_buffer_minutes": spec["min_buffer"],
                "risk_ceiling": spec["risk_ceiling"],
            })

        reject_windows: list[dict] = []
        for period_line in policy_results.get(name, []):
            for assignment in period_line["assignments"]:
                if assignment["served"]:
                    continue
                first_reason = next(
                    (r["reasons"][0] for r in assignment["reject_reasons"] if r["reasons"]),
                    "策略未覆盖该站",
                )
                reject_windows.append({
                    "period": period_line["period"],
                    "weather": period_line["weather"],
                    "ref": assignment["ref"],
                    "station": assignment["station"],
                    "window": [_hm(assignment["window"][0]), _hm(assignment["window"][1])],
                    "reason": first_reason,
                })

        # 联动归因：残余缺口是否集中在同一团期（同一场雨），而非两次独立短缺
        joint_periods = [
            p["period"] for p in policy_results.get(name, [])
            if p["joint_period"] and p["residual"] > 0
        ]
        same_period_failures = sorted({
            (p["period"], a["station"])
            for p in policy_results.get(name, [])
            if p["joint_period"]
            for a in p["assignments"] if not a["served"]
        })
        affected_periods = sorted({period for period, _ in same_period_failures})
        profiles.append({
            "policy": name,
            "capacity_sources": capacity_sources,
            "most_vulnerable_kind": "大（童车等大件）：历史未满足与停用冲击全部集中在大柜",
            "most_vulnerable_window": "午后 13:00–16:40 的提前取包/提前到站叠加窗，"
                                      "且备用柜 L5 仅开放到 16:00，晚于该时刻的领取无法兑现",
            "rejected_windows": reject_windows,
            "joint_failure_attribution": {
                "same_period": bool(affected_periods),
                "affected_periods": affected_periods,
                "detail": (
                    f"两站残余缺口同处团期 {', '.join(affected_periods)}（同一场雨联动），"
                    "不是两次独立短缺；禁止按单柜独立概率折算"
                    if affected_periods else "残余缺口未在同一团期联动"
                ),
            },
        })
    return profiles


def _hm(value: int) -> str:
    return f"{value // 60:02d}:{value % 60:02d}"


def _historical_blocked(tickets: list[dict]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    for t in tickets:
        if t["blocked_until"] is not None or t["open_at_cutoff"]:
            out[t["period"]].add(t["locker"])
    return out


def _used_lockers(book: dict, tickets: list[dict], period: str) -> set[str]:
    """同团期已经正常占用的柜不能再分配给未满足申请。"""
    return {t["locker"] for t in tickets if t["period"] == period and t["placed"] is not None}
