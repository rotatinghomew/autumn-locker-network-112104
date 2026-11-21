"""景点寄存联营：团期级尾部风险与停用分柜保障分析。

模块划分：
- timewin：当地时刻（HH:MM）与区间运算，全部为整数分钟。
- observations：寄取凭证生命周期、需求匹配与团期联合观测卡。
- risk：以团期（而非单柜）为单位的分层风险比较与不可估计标记。
- allocation：停用情景下的换站有效性、成套预约签发与人工缺口台账。
- report：装配可反复刷新的风险比较报告。
"""

from .allocation import plan_allocation
from .observations import build_period_cards, build_tickets, classify_demand
from .report import build_report
from .risk import compare_strata, simulate_policy_tails

__all__ = [
    "build_period_cards",
    "build_tickets",
    "classify_demand",
    "compare_strata",
    "simulate_policy_tails",
    "plan_allocation",
    "build_report",
]
