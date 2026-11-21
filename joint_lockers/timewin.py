"""HH:MM 当地时刻与时间窗工具。

所有时刻转换为自午夜起的整数分钟；区间一律为左闭右闭的占用窗，
停用区间同样按闭窗处理，相邻分钟即视为不可用，避免边界争议。
"""
from __future__ import annotations


def to_min(value: str | None) -> int | None:
    if value is None:
        return None
    hour, minute = str(value).split(":")
    return int(hour) * 60 + int(minute)


def to_hhmm(value: int | None) -> str | None:
    if value is None:
        return None
    return f"{value // 60:02d}:{value % 60:02d}"


def contains(outer: tuple[int, int], inner: tuple[int, int]) -> bool:
    """outer 是否完整覆盖 inner（闭区间）。"""
    return outer[0] <= inner[0] and inner[1] <= outer[1]


def overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] <= b[1] and b[0] <= a[1]


def subtract(interval: tuple[int, int], holes: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """从 interval 中挖去若干停用窗，返回剩余可用闭区间（已合并、排序）。"""
    pieces = [interval]
    for hole_start, hole_end in sorted(holes):
        remaining: list[tuple[int, int]] = []
        for start, end in pieces:
            if hole_end < start or hole_start > end:
                remaining.append((start, end))
                continue
            if hole_start > start:
                remaining.append((start, min(end, hole_start - 1)))
            if hole_end < end:
                remaining.append((max(start, hole_end + 1), end))
        pieces = remaining
    return pieces


def covers_any(intervals: list[tuple[int, int]], need: tuple[int, int]) -> bool:
    return any(contains(interval, need) for interval in intervals)


def gap_minutes(intervals: list[tuple[int, int]], need: tuple[int, int]) -> int:
    """need 无法被 intervals 覆盖的分钟数（用于量化人工缺口）。"""
    covered = 0
    cursor = need[0]
    for start, end in sorted(intervals):
        if end < cursor:
            continue
        if start > cursor:
            cursor = start
        if start <= need[1] and end >= cursor:
            covered += min(end, need[1]) - cursor + 1
            cursor = min(end, need[1]) + 1
            if cursor > need[1]:
                break
    return max(0, need[1] - need[0] + 1 - covered)
