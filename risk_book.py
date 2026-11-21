"""命令行入口：反复刷新团期风险比较与停用分柜报告。

用法：
    python3 risk_book.py data/samples.json                 # 中文决策摘要
    python3 risk_book.py data/samples.json --json out.json # 完整结构化报告
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from joint_lockers.report import build_report
from source_book import load
from joint_lockers.timewin import to_hhmm


def main() -> None:
    parser = argparse.ArgumentParser(description="团期级尾部风险比较与停用分柜保障")
    parser.add_argument("path", type=Path)
    parser.add_argument("--json", dest="json_path", type=Path, default=None,
                        help="把完整报告写入该 JSON 文件")
    args = parser.parse_args()

    raw = args.path.read_bytes()
    book = load(args.path)
    report = build_report(book, source_path=str(args.path), source_bytes=raw)

    if args.json_path:
        args.json_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"完整报告已写入 {args.json_path}")
    print(render_summary(report))


def render_summary(report: dict) -> str:
    lines: list[str] = []
    lines.append("=" * 68)
    lines.append(report["report"])
    lines.append(f"生成时间 {report['generated_at']}  来源摘要 {report['source']['sha256'][:12]}")
    lines.append("=" * 68)

    summary = report["lifecycle_summary"]
    lines.append("\n【一、四类情形与凭证生命周期】")
    for name, count in sorted(summary["ticket_lifecycles"].items()):
        lines.append(f"  - {name}: {count} 张凭证")
    for name, count in sorted(summary["demand_buckets"].items()):
        lines.append(f"  - 申请归类 {name}: {count} 条")

    lines.append("\n【二、团期联合观测卡（同团同雨不拆单柜）】")
    for card in report["period_cards"]:
        events = "、".join(card["events"]) if card["events"] else "无受损事件"
        lines.append(
            f"  - {card['period']} {card['date']} {card['weather']} "
            f"团队{'/'.join(card['teams'])}：{events}"
        )
        if card["unmet_refs"]:
            lines.append(f"      未满足申请 {', '.join(card['unmet_refs'])}；"
                         f"堵柜 {', '.join(card['blocked_lockers']) or '无'}")

    lines.append("\n【三、分层风险比较】")
    for weather, stratum in report["risk_comparison"]["strata"].items():
        lines.append(f"  - {weather}天（{stratum['sample_periods']} 个团期，低可信）：")
        for code, stat in stratum["event_rates"].items():
            lines.append(
                f"      {code}: {stat['count']}/{stat['sample_periods']} "
                f"={stat['rate']:.2f} [{stat['confidence']}]"
            )
        lines.append(f"      {stratum['independence_dilution_note']}")
    lines.append("  - 无法估计的合流条件：")
    for item in report["risk_comparison"]["unestimable_confluences"]:
        bound = item["rule_of_three_upper_bound"]
        lines.append(
            f"      {item['code']}：{item['name']}；观测 0/{item['sample_periods']}，"
            f"3/n 上界 {bound:.2f} 仅作排查，估计值保留 null"
        )

    lines.append("\n【四、尾部堆积模拟（实际柜型与领取窗逐单判定）】")
    for policy, detail in report["tail_simulation"].items():
        if policy == "policy_profiles":
            continue
        lines.append(
            f"  - 策略「{policy}」：模拟 {detail['periods_simulated']} 个团期，"
            f"最糟残余 {detail['worst_residual_units']} 单，"
            f"联合团期仍有缺口 {detail['joint_periods_with_residual']} 次"
        )
        for period in detail["by_period"]:
            for assignment in period["assignments"]:
                if not assignment["served"]:
                    reason = "；".join(
                        r["reasons"][0] for r in assignment["reject_reasons"] if r["reasons"]
                    ) or "策略未覆盖该站/无可配给柜型"
                    lines.append(
                        f"      {assignment['ref']} {assignment['station']} "
                        f"窗 {_w(assignment['window'])} 无法兑现：{reason}"
                    )

    lines.append("\n【四之二、备选共享政策画像】")
    for profile in report["tail_simulation"]["policy_profiles"]:
        lines.append(f"  - 政策「{profile['policy']}」")
        for src in profile["capacity_sources"]:
            if not src["substitute_lockers"]:
                lines.append(f"      保障容量：{src['home_station']} 无共享柜（仅本站自担）")
            for locker in src["substitute_lockers"]:
                lines.append(
                    f"      保障容量来自 {locker['station']} 的 {locker['locker']}"
                    f"（{locker['kind']}柜，开放 {locker['open_window'][0]}–"
                    f"{locker['open_window'][1]}），缓冲 {src['min_buffer_minutes']} 分钟"
                )
        lines.append(f"      最易无法兑现：{profile['most_vulnerable_kind']}；{profile['most_vulnerable_window']}")
        attr = profile["joint_failure_attribution"]
        lines.append(f"      联动归因：{attr['detail']}")

    lines.append("\n【五、停用情景分柜结果（旧约优先、单实体不双计）】")
    for item in report["allocation"]["outcomes"]:
        window = _w(item["window"])
        tag = "旧约保护" if item["protected"] else "新请求"
        lines.append(
            f"  - {item['booking']}（{item['group']}，{tag}，{item['station']}{item['kind']}柜，"
            f"窗 {window}）→ {item['result']}"
        )
        if item["assigned_locker"]:
            lines.append(f"      安排：{item['route']} {item['assigned_locker']}"
                         f"（{item['substitute_station'] or item['home_station']}）")
        for reason in item["reject_reasons"]:
            lines.append(f"      被拒 {reason['locker']}：{'；'.join(reason['reasons'])}")
        if item["best_effort"]:
            be = item["best_effort"]
            if be["already_committed"] and be["uncovered_minutes"] == 0:
                lines.append(f"      尽力安排 {be['locker']} 物理上可覆盖该窗但实体已被前约占用，"
                             "不能双计 → 真实人工缺口（不计保障）")
            else:
                lines.append(f"      尽力安排 {be['locker']} 仍有 {be['uncovered_minutes']} "
                             "分钟无法覆盖 → 真实人工缺口（不计保障）")

    lines.append("\n【六、全网签发限额与追溯】")
    for limit in report["allocation"]["issuance_limits"]:
        lines.append(
            f"  - {limit['policy']}/{limit['station']}：自动保障限额 "
            f"{limit['automatic_guarantee_quota']}（风险上限 {limit['risk_ceiling']}），"
            f"需人工审批={limit['manual_approval_required']}；{limit['reason']}"
        )
    shared = report["allocation"]["single_inventory_note"]["shared_single_inventory"]
    for locker, where in shared.items():
        lines.append(f"  - 单实体共用柜 {locker} 被 {', '.join(where)} 同时引用，串行占用不双计")
    lines.append("  - 追溯入口：report.traceability.by_period（团期→事件/凭证）、"
                 "by_booking（预约号→结果/原因）、adjustment_ledger（每次调整影响了谁）")
    return "\n".join(lines)


def _w(window: list[int]) -> str:
    return f"{to_hhmm(window[0])}–{to_hhmm(window[1])}"


if __name__ == "__main__":
    main()
