"""Zone 索引：掃描線切出基本區間，並以 bisect 查詢（SPEC 第 5 節）。"""

import ipaddress
from bisect import bisect_right
from collections.abc import Iterable
from dataclasses import dataclass, field

from fwzone.parsing import CONFLICT, UNKNOWN, Interval


@dataclass(frozen=True)
class ZoneEntry:
    """一列通過檢查、納入比對的 zone 定義。row 為來源檔的列號。"""

    row: int
    interval: Interval
    zone: str


@dataclass(frozen=True)
class ConflictSpan:
    """被兩列以上 zone 定義覆蓋的區間，rows 為涉及的來源列號（升冪）。"""

    interval: Interval
    rows: tuple[int, ...]


@dataclass(frozen=True)
class LookupResult:
    """單一區間的查詢結果。

    markers 為 zone 名稱，以及有衝突時的 CONFLICT、有未覆蓋部分時的 UNKNOWN。
    """

    markers: frozenset[str]
    uncovered: tuple[Interval, ...]
    conflicts: tuple[ConflictSpan, ...]


@dataclass
class _Table:
    """單一 IP 版本的基本區間，依 start 排序的平行 list。"""

    starts: list[int] = field(default_factory=list)
    ends: list[int] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    rows: list[tuple[int, ...]] = field(default_factory=list)


def _build_table(entries: list[ZoneEntry]) -> _Table:
    # order 0 = 區間結束後一格（移除），1 = 區間起點（加入）；同一位置先處理移除再處理加入
    events: list[tuple[int, int, int]] = []
    for idx, entry in enumerate(entries):
        events.append((entry.interval.start, 1, idx))
        events.append((entry.interval.end + 1, 0, idx))
    events.sort()

    segments: list[tuple[int, int, frozenset[int]]] = []
    active: set[int] = set()
    i = 0
    while i < len(events):
        pos = events[i][0]
        while i < len(events) and events[i][0] == pos:
            _, order, idx = events[i]
            if order:
                active.add(idx)
            else:
                active.discard(idx)
            i += 1
        if active:
            segments.append((pos, events[i][0] - 1, frozenset(active)))

    table = _Table()
    prev_members: frozenset[int] | None = None
    for start, end, members in segments:
        if prev_members == members and table.ends[-1] + 1 == start:
            table.ends[-1] = end
            continue
        ordered = sorted(members, key=lambda idx: entries[idx].row)
        label = entries[ordered[0]].zone if len(ordered) == 1 else CONFLICT
        table.starts.append(start)
        table.ends.append(end)
        table.labels.append(label)
        table.rows.append(tuple(entries[idx].row for idx in ordered))
        prev_members = members
    return table


class ZoneIndex:
    """IPv4、IPv6 各自一份索引，彼此不比對。"""

    def __init__(self, entries: Iterable[ZoneEntry]) -> None:
        by_version: dict[int, list[ZoneEntry]] = {}
        for entry in entries:
            by_version.setdefault(entry.interval.version, []).append(entry)
        self._tables = {v: _build_table(es) for v, es in by_version.items()}

    def conflict_spans(self) -> list[ConflictSpan]:
        """所有被兩列以上覆蓋的區間，依版本、起點排序。"""
        spans: list[ConflictSpan] = []
        for version in sorted(self._tables):
            table = self._tables[version]
            for start, end, label, rows in zip(
                table.starts, table.ends, table.labels, table.rows, strict=True
            ):
                if label == CONFLICT:
                    spans.append(ConflictSpan(Interval(version, start, end), rows))
        return spans

    def lookup(self, interval: Interval) -> LookupResult:
        """找出與查詢區間重疊的所有基本區間，並計算未被覆蓋的子區間。"""
        version, start, end = interval
        table = self._tables.get(version)
        markers: set[str] = set()
        conflicts: list[ConflictSpan] = []
        uncovered: list[Interval] = []
        cursor = start

        if table is not None:
            i = max(bisect_right(table.starts, start) - 1, 0)
            while i < len(table.starts) and table.starts[i] <= end:
                seg_start, seg_end = table.starts[i], table.ends[i]
                if seg_end >= start:
                    overlap_start = max(seg_start, start)
                    overlap_end = min(seg_end, end)
                    if overlap_start > cursor:
                        uncovered.append(Interval(version, cursor, overlap_start - 1))
                    cursor = overlap_end + 1
                    markers.add(table.labels[i])
                    if table.labels[i] == CONFLICT:
                        span = Interval(version, overlap_start, overlap_end)
                        conflicts.append(ConflictSpan(span, table.rows[i]))
                i += 1

        if cursor <= end:
            uncovered.append(Interval(version, cursor, end))
        if uncovered:
            markers.add(UNKNOWN)
        return LookupResult(frozenset(markers), tuple(uncovered), tuple(conflicts))


def to_cidrs(interval: Interval) -> list[str]:
    """將區間以最少數量的 CIDR 表示。"""
    address = ipaddress.IPv4Address if interval.version == 4 else ipaddress.IPv6Address
    networks = ipaddress.summarize_address_range(address(interval.start), address(interval.end))
    return [str(n) for n in networks]
