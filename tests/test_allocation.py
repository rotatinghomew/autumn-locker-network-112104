"""停用分柜引擎、成套预约规则与报告追溯测试。"""
import copy
import json
import unittest
from pathlib import Path

from joint_lockers.allocation import (
    GUARANTEED,
    HOME_LOCKED,
    MANUAL_GAP,
    build_policy_index,
    feasible_substitutes,
    locker_windows,
    plan_allocation,
)
from joint_lockers.indexing import build_travel, locker_index
from joint_lockers.observations import build_period_cards, build_tickets, classify_demand
from joint_lockers.report import build_report
from joint_lockers.risk import compare_strata
from joint_lockers.timewin import to_min
from source_book import load

DATA = Path(__file__).resolve().parents[1] / "data" / "samples.json"


class AllocationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.book = load(DATA)
        tickets = build_tickets(cls.book)
        demand = classify_demand(cls.book, tickets)
        cards = build_period_cards(cls.book, tickets, demand)
        cls.risk = compare_strata(cards)
        cls.plan = plan_allocation(cls.book, cls.risk)
        cls.outcome = {o["booking"]: o for o in cls.plan["outcomes"]}

    def test_r2_substitution_meets_all_hard_conditions(self):
        r2 = self.outcome["r2"]
        self.assertEqual(r2["result"], GUARANTEED)
        self.assertEqual(r2["assigned_locker"], "L5")
        self.assertEqual(r2["substitute_station"], "中转")
        # 湾站->中转 14 分钟 + 缓冲 10 分钟，须在原 collect_by 15:10 内
        self.assertTrue(r2["window"][1] >= r2["window"][0] + 14 + 10)

    def test_r1_window_beyond_l5_close_is_manual_gap_with_real_minutes(self):
        r1 = self.outcome["r1"]
        self.assertEqual(r1["result"], MANUAL_GAP)
        self.assertIsNone(r1["assigned_locker"])
        self.assertEqual(r1["best_effort"]["locker"], "L5")
        self.assertEqual(r1["best_effort"]["uncovered_minutes"], 50)
        self.assertFalse(r1["best_effort"]["is_guarantee"])

    def test_single_backup_locker_is_never_double_counted(self):
        r2 = self.outcome["r2"]
        r5 = self.outcome["r5"]
        self.assertEqual(r2["assigned_locker"], "L5")
        self.assertNotEqual(r5["assigned_locker"], "L5")
        reject = next(x for x in r5["reject_reasons"] if x["locker"] == "L5")
        self.assertIn("不能双计", reject["reasons"][0])
        self.assertTrue(r5["best_effort"]["already_committed"])

    def test_normal_small_locker_booking_is_protected_not_cancelled(self):
        r3 = self.outcome["r3"]
        self.assertEqual(r3["result"], HOME_LOCKED)
        self.assertEqual(r3["assigned_locker"], "L2")
        self.assertTrue(r3["protected"])

    def test_old_bookings_are_processed_before_new_requests(self):
        ledger_bookings = [row["booking"] for row in self.plan["ledger"]]
        self.assertLess(ledger_bookings.index("r2"), ledger_bookings.index("r5"))
        for item in self.plan["outcomes"]:
            if item["state"] == "已签":
                self.assertTrue(item["protected"])

    def test_consent_station_is_a_hard_condition(self):
        book = self.book
        windows = locker_windows(book)
        policy = build_policy_index(book)["联营"]["stations"]["湾站"]
        policy = dict(policy, policy="联营")
        feasible, _ = feasible_substitutes(
            policy=policy,
            need_kind="大",
            need_window=(to_min("10:00"), to_min("14:00")),
            windows=windows,
            travel=build_travel(book),
            home_station="湾站",
            blocked_lockers=set(),
            used_substitutes=set(),
            consent_stations=["中转"],
            lockers=locker_index(book),
        )
        self.assertEqual([x["locker"] for x in feasible], ["L5"])
        # 家长只接受中转时，松站 L1 永远不可行
        self.assertNotIn("L1", [x["locker"] for x in feasible])

    def test_kind_mismatch_rejects_substitution(self):
        book = self.book
        windows = locker_windows(book)
        spec = {"substitutes": ["L2"], "min_buffer": 0}
        feasible, reasons = feasible_substitutes(
            policy=spec,
            need_kind="大",
            need_window=(to_min("10:00"), to_min("11:00")),
            windows=windows,
            travel=build_travel(book),
            home_station="松站",
            blocked_lockers=set(),
            used_substitutes=set(),
            consent_stations=["松站"],
            lockers=locker_index(book),
        )
        self.assertFalse(feasible)
        self.assertIn("柜型不符", reasons[0]["reasons"][0])

    def test_issuance_quota_zero_when_estimates_unverifiable(self):
        for limit in self.plan["issuance_limits"]:
            self.assertEqual(limit["automatic_guarantee_quota"], 0)
            self.assertTrue(limit["manual_approval_required"])

    def test_refresh_is_repeatable_and_traceable(self):
        raw = DATA.read_bytes()
        report_a = build_report(self.book, str(DATA), raw)
        report_b = build_report(copy.deepcopy(self.book), str(DATA), raw)
        self.assertEqual(
            json.dumps(report_a, ensure_ascii=False, sort_keys=True),
            json.dumps(report_b, ensure_ascii=False, sort_keys=True),
        )
        trace = report_a["traceability"]
        self.assertIn("p1", trace["by_period"])
        self.assertEqual(trace["by_booking"]["r2"]["assigned_locker"], "L5")
        affected = {row["booking"] for row in trace["adjustment_ledger"]}
        self.assertEqual(affected, {"r1", "r2", "r3", "r4", "r5"})


if __name__ == "__main__":
    unittest.main()
