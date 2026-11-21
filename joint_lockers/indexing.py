"""共享的资料索引助手。"""
from __future__ import annotations


def locker_meta(book_or_lockers: dict | list, locker: str | None = None) -> dict:
    """按柜号取柜元数据。支持两种调用：
    locker_meta(book, "L1") 或在已构造索引后 locker_meta(index) 传索引。
    """
    if locker is None:
        # 传入的是已构造好的索引
        return book_or_lockers
    lockers = book_or_lockers["lockers"]
    for row in lockers:
        if row["locker"] == locker:
            return row
    raise KeyError(f"未知柜号：{locker}")


def locker_index(book: dict) -> dict[str, dict]:
    return {row["locker"]: row for row in book["lockers"]}


def build_travel(book: dict) -> dict[tuple[str, str], int]:
    return {(m["from_station"], m["to_station"]): m["minutes"] for m in book["movements"]}
