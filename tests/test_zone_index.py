"""SPEC 10.1 的 T01–T10（不含 group 相關的 T11–T14）與索引本身的單元測試。"""

import ipaddress

import pytest

from fwzone.parsing import Interval, TokenKind, TokenParser, sort_markers, tokenize
from fwzone.zone_index import ZoneEntry, ZoneIndex, to_cidrs


def _net(version: int, start: str, end: str | None = None) -> Interval:
    a = ipaddress.ip_address(start)
    b = ipaddress.ip_address(end or start)
    return Interval(version, int(a), int(b))


def _entry(row: int, cidr: str, zone: str) -> ZoneEntry:
    net = ipaddress.ip_network(cidr)
    return ZoneEntry(
        row, Interval(net.version, int(net.network_address), int(net.broadcast_address)), zone
    )


BASE_ENTRIES = [
    _entry(2, "10.1.0.0/24", "ZONE-A"),
    _entry(3, "10.1.1.0/24", "ZONE-B"),
    _entry(4, "10.2.0.0/16", "ZONE-C"),
    _entry(5, "2001:db8::/48", "ZONE-V6"),
]


def annotate(cell: str, entries=BASE_ENTRIES) -> str:
    """暫代 annotate.py（P3）的儲存格彙整，僅供 P1 測試使用。"""
    index = ZoneIndex(entries)
    parser = TokenParser()
    markers: set[str] = set()
    for text in tokenize(cell):
        parsed = parser.parse(text)
        if parsed.kind is TokenKind.INTERVAL:
            markers |= index.lookup(parsed.interval).markers
        else:
            markers.add(parsed.marker)
    return ", ".join(sort_markers(markers))


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        ("10.1.0.5", "ZONE-A"),  # T01
        ("10.1.0.0/24", "ZONE-A"),  # T02
        ("10.1.0.0/23", "ZONE-A, ZONE-B"),  # T03
        ("10.1.0.0/22", "ZONE-A, ZONE-B, UNKNOWN"),  # T04
        ("192.168.1.1", "UNKNOWN"),  # T05
        ("any", "ANY"),  # T06
        ("ANY", "ANY"),
        ("0.0.0.0/0", "ANY"),
        ("10.1.0.250 - 10.1.1.5", "ZONE-A, ZONE-B"),  # T07
        ("10.2.3.4\n10.1.0.5, 10.1.0.6", "ZONE-A, ZONE-C"),  # T08
        ("any; 10.1.1.1; 172.16.0.1; foo", "ZONE-B, ANY, UNKNOWN, UNPARSED:foo"),  # T09
        ("10.1.0.5/24", "UNPARSED:10.1.0.5/24"),  # T10
        ("2001:db8::1", "ZONE-V6"),  # T22
        ("", ""),  # T19
    ],
)
def test_annotate_cell(cell, expected):
    assert annotate(cell) == expected


def test_t04_uncovered_range():
    result = ZoneIndex(BASE_ENTRIES).lookup(_net(4, "10.1.0.0", "10.1.3.255"))
    assert [to_cidrs(i) for i in result.uncovered] == [["10.1.2.0/23"]]


def test_t10_host_bits_issue_reported():
    assert TokenParser().parse("10.1.0.5/24").issue == "HOST_BITS"


def test_ipv4_and_ipv6_are_independent():
    assert annotate("2001:db8::1", [_entry(2, "10.0.0.0/8", "A")]) == "UNKNOWN"
    assert annotate("10.1.1.1", [_entry(2, "2001:db8::/32", "A")]) == "UNKNOWN"


def test_overlap_gives_conflict_t15():
    entries = [*BASE_ENTRIES, _entry(6, "10.1.0.128/25", "ZONE-X")]
    assert annotate("10.1.0.5", entries) == "ZONE-A"
    assert annotate("10.1.0.200", entries) == "CONFLICT"
    assert annotate("10.1.0.0/24", entries) == "ZONE-A, CONFLICT"

    index = ZoneIndex(entries)
    result = index.lookup(_net(4, "10.1.0.0", "10.1.0.255"))
    assert [(to_cidrs(c.interval), c.rows) for c in result.conflicts] == [
        (["10.1.0.128/25"], (2, 6))
    ]
    assert [(to_cidrs(c.interval), c.rows) for c in index.conflict_spans()] == [
        (["10.1.0.128/25"], (2, 6))
    ]


def test_identical_rows_with_same_zone_still_conflict():
    entries = [_entry(2, "10.0.0.0/24", "A"), _entry(3, "10.0.0.0/24", "A")]
    assert annotate("10.0.0.1", entries) == "CONFLICT"
    assert ZoneIndex(entries).lookup(_net(4, "10.0.0.1")).markers == {"CONFLICT"}


def test_adjacent_segments_with_same_rows_are_merged():
    entries = [_entry(2, "10.0.0.0/25", "A"), _entry(3, "10.0.0.128/25", "A")]
    index = ZoneIndex(entries)
    # 不同列不合併，但查詢整段仍應完整覆蓋
    result = index.lookup(_net(4, "10.0.0.0", "10.0.0.255"))
    assert result.markers == {"A"}
    assert result.uncovered == ()


def test_uncovered_gaps_between_and_around():
    entries = [_entry(2, "10.0.0.64/26", "A"), _entry(3, "10.0.0.192/26", "B")]
    result = ZoneIndex(entries).lookup(_net(4, "10.0.0.0", "10.0.0.255"))
    assert result.markers == {"A", "B", "UNKNOWN"}
    assert [to_cidrs(i) for i in result.uncovered] == [
        ["10.0.0.0/26"],
        ["10.0.0.128/26"],
    ]


def test_query_before_first_segment():
    entries = [_entry(2, "10.0.0.0/24", "A")]
    result = ZoneIndex(entries).lookup(_net(4, "9.0.0.0", "9.255.255.255"))
    assert result.markers == {"UNKNOWN"}


def test_query_after_last_segment_and_empty_index():
    assert ZoneIndex([]).lookup(_net(4, "1.1.1.1")).markers == {"UNKNOWN"}
    entries = [_entry(2, "10.0.0.0/24", "A")]
    assert ZoneIndex(entries).lookup(_net(4, "11.0.0.0")).markers == {"UNKNOWN"}


def test_containing_zone_entries():
    entries = [_entry(2, "10.0.0.0/8", "BIG"), _entry(3, "10.1.0.0/16", "SMALL")]
    assert annotate("10.1.2.3", entries) == "CONFLICT"
    assert annotate("10.2.2.3", entries) == "BIG"


def test_to_cidrs_ipv6():
    assert to_cidrs(_net(6, "2001:db8::", "2001:db8::ffff")) == ["2001:db8::/112"]
