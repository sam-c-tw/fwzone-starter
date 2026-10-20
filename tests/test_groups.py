"""SPEC 10.1 的 T11–T14 與 group 載入、展開的單元測試。"""

import ipaddress

import pytest

from fwzone.errors import InputError
from fwzone.groups import GroupResolver
from fwzone.parsing import Interval, TokenKind, sort_markers, tokenize
from fwzone.zone_index import ZoneEntry, ZoneIndex

FIXTURE_ROWS = [
    ("G1", "10.1.0.5\nG2"),
    ("G2", "10.2.0.0/24"),
    ("G5", "host.example"),
]


def _entry(row: int, cidr: str, zone: str) -> ZoneEntry:
    net = ipaddress.ip_network(cidr)
    interval = Interval(net.version, int(net.network_address), int(net.broadcast_address))
    return ZoneEntry(row, interval, zone)


ENTRIES = [
    _entry(2, "10.1.0.0/24", "ZONE-A"),
    _entry(3, "10.1.1.0/24", "ZONE-B"),
    _entry(4, "10.2.0.0/16", "ZONE-C"),
]


def annotate(cell: str, resolver: GroupResolver) -> str:
    """暫代 annotate.py（P3）的儲存格彙整，僅供測試使用。"""
    index = ZoneIndex(ENTRIES)
    markers: set[str] = set()
    for text in tokenize(cell):
        parsed = resolver.parser.parse(text)
        members = (
            [m.token for m in resolver.expand(parsed.raw)]
            if parsed.kind is TokenKind.GROUP
            else [parsed]
        )
        for token in members:
            if token.kind is TokenKind.INTERVAL:
                markers |= index.lookup(token.interval).markers
            else:
                markers.add(token.marker)
    return ", ".join(sort_markers(markers))


@pytest.fixture
def resolver() -> GroupResolver:
    return GroupResolver(FIXTURE_ROWS)


def test_t11_nested_group(resolver):
    assert annotate("G1", resolver) == "ZONE-A, ZONE-C"


@pytest.mark.parametrize(
    "cell",
    ["G_UNDEF", "www.example.com", "10.0.0.1-20"],
)
def test_t12_unparsed(cell, resolver):
    assert annotate(cell, resolver) == f"UNPARSED:{cell}"


def test_t13_cycle_reports_path():
    rows = [*FIXTURE_ROWS, ("G3", "G4"), ("G4", "G3")]
    with pytest.raises(InputError) as exc:
        GroupResolver(rows)
    assert "G3 -> G4 -> G3" in str(exc.value)


def test_t14_unparsed_member_keeps_member_value_and_via(resolver):
    assert annotate("G5", resolver) == "UNPARSED:host.example"
    (member,) = resolver.expand("G5")
    assert member.token.raw == "host.example"
    assert member.via == ("G5",)


def test_self_reference_is_a_cycle():
    with pytest.raises(InputError) as exc:
        GroupResolver([("G1", "G1")])
    assert "G1 -> G1" in str(exc.value)


def test_longer_cycle_path_excludes_entry_prefix():
    rows = [("A", "B"), ("B", "C"), ("C", "D"), ("D", "B")]
    with pytest.raises(InputError) as exc:
        GroupResolver(rows)
    assert "B -> C -> D -> B" in str(exc.value)


def test_diamond_is_not_a_cycle():
    rows = [("TOP", "L\nR"), ("L", "BASE"), ("R", "BASE"), ("BASE", "10.1.0.5")]
    resolver = GroupResolver(rows)
    members = resolver.expand("TOP")
    assert [m.via for m in members] == [("TOP", "L", "BASE"), ("TOP", "R", "BASE")]


def test_via_chain_for_nested_members(resolver):
    assert [(m.token.raw, m.via) for m in resolver.expand("G1")] == [
        ("10.1.0.5", ("G1",)),
        ("10.2.0.0/24", ("G1", "G2")),
    ]


def test_same_group_in_multiple_rows_merges_members():
    resolver = GroupResolver([("G", "10.1.0.5"), ("G", "10.1.1.5\n10.1.0.5")])
    assert [m.token.raw for m in resolver.expand("G")] == ["10.1.0.5", "10.1.1.5"]


def test_group_name_is_stripped_and_case_sensitive():
    resolver = GroupResolver([("  G1 ", "10.1.0.5")])
    assert "G1" in resolver
    assert "g1" not in resolver
    assert annotate("g1", resolver) == "UNPARSED:g1"


def test_any_member_inside_group():
    resolver = GroupResolver([("G", "any\n10.1.0.5")])
    assert annotate("G", resolver) == "ZONE-A, ANY"


def test_member_delimiters_and_crlf():
    resolver = GroupResolver([("G", "10.1.0.5\r\n10.1.1.5, 10.2.0.1; 8.8.8.8")])
    assert annotate("G", resolver) == "ZONE-A, ZONE-B, ZONE-C, UNKNOWN"


def test_host_bits_member_carries_issue():
    resolver = GroupResolver([("G", "10.1.0.5/24")])
    (member,) = resolver.expand("G")
    assert member.token.marker == "UNPARSED:10.1.0.5/24"
    assert member.token.issue == "HOST_BITS"


def test_address_form_wins_over_group_with_same_name():
    resolver = GroupResolver([("10.1.0.5", "10.2.0.1")])
    assert annotate("10.1.0.5", resolver) == "ZONE-A"


def test_empty_group_name_row_is_skipped_and_empty_members_ok():
    resolver = GroupResolver([("", "10.1.0.5"), ("EMPTY", "")])
    assert len(resolver) == 1
    assert resolver.expand("EMPTY") == ()


def test_expansion_is_cached(resolver):
    assert resolver.expand("G1") is resolver.expand("G1")


def test_no_groups_defined_everything_unparsed():
    assert annotate("G1", GroupResolver([])) == "UNPARSED:G1"
