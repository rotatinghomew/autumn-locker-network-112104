"""观测归类与团期级风险的规则测试。"""
import unittest
from pathlib import Path

from joint_lockers.observations import (
    EARLY_SURGE,
    JOINT_ADVERSE_BIG,
    NO_SHOW_BLOCK,
    STILL_OCCUPIED,
    build_period_cards,
    build_tickets,
    classify_demand,
)
from joint_lockers.risk import RARE_CONFLUENCES, compare_strata, simulate_policy_tails
from joint_lockers.timewin import gap_minutes, subtract, to_min
from source_book import load

DATA = Path(__file__).resolve().parents[1] / "data" / "samples.json"


class TimeWindowTest(unittest.TestCase):
    def test_subtract_splits_around_outage(self):
        # L1 08:00-17:30 挖去停用 10:00-17:30，只剩 08:00-09:59
        pieces = subtract((to_min("08:00"), to_min("17:30")),
                          [(to_min("10:00"), to_min("17:30"))])
        self.assertEqual(pieces, [(to_min("08:00"), to_min("09:59"))])

    def test_gap_minutes_counts_uncovered_part(self):
        need = (to_min("09:00"), to_min("16:50"))
        # L5 只开到 16:00，缺口应为 16:01..16:50 共 50 分钟
        self.assertEqual(gap_minutes([(to_min("09:00"), to_min("16:00"))], need), 50)
        self.assertEqual(gap_minutes([(to_min("09:00"), to_min("16:50"))], need), 0)


class ObservationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.book = load(DATA)
        cls.tickets = build_tickets(cls.book)
        cls.demand = classify_demand(cls.book, cls.tickets)
        cls.cards = build_period_cards(cls.book, cls.tickets, cls.demand)
        cls.ticket = {t["ticket"]: t for t in cls.tickets}
        cls.card = {c["period"]: c for c in cls.cards}

    def test_no_show_ticket_keeps_both_vouchers_and_blocked_until_return(self):
        t6 = self.ticket["u6"]
        self.assertEqual(t6["lifecycle"], "失联后返回仍占柜")
        self.assertEqual(t6["events"], ["失联未释放", "失约后返回"])
        # 堵柜段必须保留到返回时刻 12:20，不能被返回凭证抹掉
        self.assertEqual(t6["blocked_until"], to_min("12:20"))
        self.assertTrue(t6["open_at_cutoff"])

    def test_four_cases_are_distinguished(self):
        lifecycles = {t["lifecycle"] for t in self.tickets}
        self.assertIn("明确取消", lifecycles)
        self.assertIn("失联后返回仍占柜", lifecycles)
        self.assertIn("观察时仍占柜", lifecycles)
        buckets = {d["bucket"] for d in self.demand}
        self.assertIn("无柜等候", buckets)  # 无预约/无柜等候
        unmet_refs = {d["ref"] for d in self.demand if not d["served"]}
        self.assertEqual(unmet_refs, {"d3", "d4", "d5", "d6", "d7"})

    def test_cancelled_locker_is_not_blocked(self):
        u4 = self.ticket["u4"]
        self.assertEqual(u4["lifecycle"], "明确取消")
        self.assertIsNone(u4["placed"])
        self.assertIsNone(u4["blocked_until"])

    def test_rainy_periods_are_joint_observations_not_locker_counts(self):
        for period in ("p1", "p3", "p5"):
            self.assertIn(JOINT_ADVERSE_BIG, self.card[period]["events"])
            self.assertEqual(len(self.card[period]["adverse_stations"]), 2)
        # 雨天提前取包合流只在 p1/p5 出现，p3 是失联堵柜型事件
        self.assertIn(EARLY_SURGE, self.card["p1"]["events"])
        self.assertIn(EARLY_SURGE, self.card["p5"]["events"])
        self.assertNotIn(EARLY_SURGE, self.card["p3"]["events"])
        self.assertIn(NO_SHOW_BLOCK, self.card["p3"]["events"])
        self.assertIn(STILL_OCCUPIED, self.card["p3"]["events"])

    def test_d3_second_early_request_is_unmet_not_duplicate_service(self):
        d3 = next(d for d in self.demand if d["ref"] == "d3")
        self.assertFalse(d3["served"])
        d2 = next(d for d in self.demand if d["ref"] == "d2")
        self.assertTrue(d2["served"])  # 同团第一单已寄存，第二单仍需独立柜


class RiskTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.book = load(DATA)
        tickets = build_tickets(cls.book)
        demand = classify_demand(cls.book, tickets)
        cls.cards = build_period_cards(cls.book, tickets, demand)
        cls.risk = compare_strata(cls.cards)
        cls.tails = simulate_policy_tails(cls.book, cls.cards, demand, tickets)

    def test_strata_rates_are_period_level_and_low_confidence(self):
        rainy = self.risk["strata"]["雨"]
        self.assertEqual(rainy["sample_periods"], 3)
        joint = rainy["event_rates"][JOINT_ADVERSE_BIG]
        self.assertEqual(joint["count"], 3)
        self.assertEqual(joint["rate"], 1.0)
        self.assertEqual(joint["confidence"], "低可信")

    def test_rare_confluences_keep_null_estimate_not_zero(self):
        codes = {item["code"] for item in self.risk["unestimable_confluences"]}
        self.assertEqual(len(codes), len(RARE_CONFLUENCES))
        for item in self.risk["unestimable_confluences"]:
            self.assertIsNone(item["estimate"])
            self.assertIsNotNone(item["rule_of_three_upper_bound"])

    def test_tail_simulation_uses_real_windows_and_single_inventory(self):
        join = self.tails["联营"]
        # 历史两个雨天团期都有两单大柜残余，且 L5 16:00 关门无法清尾
        self.assertEqual(join["worst_residual_units"], 2)
        for period in join["by_period"]:
            self.assertTrue(period["joint_period"])
            self.assertGreater(period["residual"], 0)

    def test_policy_profile_states_capacity_source_and_same_period_attribution(self):
        profiles = {p["policy"]: p for p in self.tails["policy_profiles"]}
        join = profiles["联营"]
        sources = {s["home_station"]: [x["locker"] for x in s["substitute_lockers"]]
                   for s in join["capacity_sources"]}
        self.assertEqual(sources["松站"], ["L5", "L3"])
        self.assertEqual(sources["湾站"], ["L5", "L1"])
        attr = join["joint_failure_attribution"]
        self.assertTrue(attr["same_period"])
        self.assertEqual(attr["affected_periods"], ["p1", "p5"])


if __name__ == "__main__":
    unittest.main()
