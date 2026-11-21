"""装配可反复刷新的风险比较报告（纯数据结构，便于序列化为 JSON）。"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from .allocation import plan_allocation
from .observations import build_period_cards, build_tickets, classify_demand
from .risk import compare_strata, simulate_policy_tails
from .timewin import to_hhmm


def build_report(book: dict, source_path: str | None = None,
                 source_bytes: bytes | None = None) -> dict:
    tickets = build_tickets(book)
    demand_classes = classify_demand(book, tickets)
    cards = build_period_cards(book, tickets, demand_classes)
    risk = compare_strata(cards)
    tails = simulate_policy_tails(book, cards, demand_classes, tickets)
    allocation = plan_allocation(book, risk)

    return {
        "report": "景点寄存联营：团期级尾部风险与停用分柜保障",
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "source": {
            "path": source_path,
            "sha256": hashlib.sha256(source_bytes).hexdigest() if source_bytes else None,
        },
        "methodology": [
            "最小观测单位是团期（period），同团同雨的多柜联动只算一次联合事件，不拆单柜独立概率。",
            "历史样本按天气分层，每层 2–3 个团期，所有比率带低可信标记，禁止外推。",
            "从未出现的合流条件保留为无法估计，仅给 3/n 单边上界，不以 0 参与限额。",
            "换站有效性同时校验家长同意站、柜型、完整可用时段与路程缓冲，缺一即人工缺口。",
            "备用柜单实体串行占用，旧约优先；不取消正常小柜预约，不向后来家庭转移缺口责任。",
        ],
        "lifecycle_summary": _lifecycle_summary(tickets, demand_classes),
        "tickets": [_public_ticket(t) for t in tickets],
        "demand": [_public_demand(d) for d in demand_classes],
        "period_cards": cards,
        "risk_comparison": risk,
        "tail_simulation": tails,
        "allocation": allocation,
        "traceability": _traceability(cards, tickets, allocation),
    }


def _lifecycle_summary(tickets: list[dict], demand_classes: list[dict]) -> dict:
    buckets: dict[str, int] = {}
    for t in tickets:
        buckets[t["lifecycle"]] = buckets.get(t["lifecycle"], 0) + 1
    demand_buckets: dict[str, int] = {}
    for d in demand_classes:
        demand_buckets[d["bucket"]] = demand_buckets.get(d["bucket"], 0) + 1
    return {
        "ticket_lifecycles": buckets,
        "demand_buckets": demand_buckets,
        "four_case_definition": {
            "无预约/无柜等候": "demand 有申请而无对应寄取凭证",
            "明确取消": "usage 状态=取消，柜位未被占用",
            "失联而未释放": "usage 状态=失联未释放；若随后失约返回，堵柜段单独保留",
            "到观察时仍占柜": "data_as_of 时 collected 为空（占柜未结束或失约返回后未取回）",
        },
    }


def _public_ticket(t: dict) -> dict:
    return {
        **t,
        "placed": to_hhmm(t["placed"]),
        "collected": to_hhmm(t["collected"]),
        "blocked_until": to_hhmm(t["blocked_until"]),
    }


def _public_demand(d: dict) -> dict:
    return {**d, "arrived": to_hhmm(d["arrived"]),
            "expected_pickup": to_hhmm(d["expected_pickup"])}


def _traceability(cards: list[dict], tickets: list[dict], allocation: dict) -> dict:
    """管理员从团期/预约号双向追溯：风险事件、限额、分柜调整各影响了谁。"""
    period_index = {
        card["period"]: {
            "weather": card["weather"],
            "teams": card["teams"],
            "events": card["events"],
            "tickets": card["tickets"],
            "unmet_refs": card["unmet_refs"],
            "blocked_lockers": card["blocked_lockers"],
        }
        for card in cards
    }
    booking_index = {
        item["booking"]: {
            "group": item["group"],
            "result": item["result"],
            "assigned_locker": item["assigned_locker"],
            "reject_reasons": item["reject_reasons"],
            "best_effort": item["best_effort"],
        }
        for item in allocation["outcomes"]
    }
    return {
        "by_period": period_index,
        "by_booking": booking_index,
        "adjustment_ledger": allocation["ledger"],
        "issuance_limits": allocation["issuance_limits"],
    }
