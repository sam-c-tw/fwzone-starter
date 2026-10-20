import pytest

from fwzone.parsing import (
    HOST_BITS,
    Interval,
    TokenKind,
    TokenParser,
    sort_markers,
    tokenize,
)


def test_tokenize_mixed_delimiters_and_crlf():
    assert tokenize("a\r\nb, c;d\n\n  ,e ") == ["a", "b", "c", "d", "e"]


def test_tokenize_empty():
    assert tokenize("") == []
    assert tokenize("  \n ") == []


@pytest.mark.parametrize("text", ["any", "ANY", "Any", "0.0.0.0/0", "::/0"])
def test_any(text):
    parsed = TokenParser().parse(text)
    assert parsed.kind is TokenKind.ANY
    assert parsed.marker == "ANY"


def test_single_ip_and_cidr():
    parser = TokenParser()
    assert parser.parse("10.1.0.5").interval == Interval(4, 0x0A010005, 0x0A010005)
    assert parser.parse("10.1.0.0/24").interval == Interval(4, 0x0A010000, 0x0A0100FF)


def test_ipv6():
    parsed = TokenParser().parse("2001:db8::1")
    assert parsed.interval.version == 6


def test_range_with_spaces():
    parsed = TokenParser().parse("10.1.0.250 - 10.1.1.5")
    assert parsed.interval == Interval(4, 0x0A0100FA, 0x0A010105)


@pytest.mark.parametrize(
    "text",
    [
        "10.0.0.1-20",  # 簡寫不支援
        "10.0.0.9-10.0.0.1",  # 起點大於終點
        "10.0.0.1-2001:db8::1",  # 版本不同
        "www.example.com",
        "host.example",
        "foo",
    ],
)
def test_unparsed(text):
    parsed = TokenParser().parse(text)
    assert parsed.kind is TokenKind.UNPARSED
    assert parsed.marker == f"UNPARSED:{text}"
    assert parsed.issue is None


def test_host_bits_is_unparsed_with_issue():
    parsed = TokenParser().parse("10.1.0.5/24")
    assert parsed.kind is TokenKind.UNPARSED
    assert parsed.marker == "UNPARSED:10.1.0.5/24"
    assert parsed.issue == HOST_BITS


def test_host_bits_zero_prefix_is_not_any():
    assert TokenParser().parse("1.2.3.4/0").kind is TokenKind.UNPARSED


def test_group_recognised_after_address_forms():
    parser = TokenParser(is_group=lambda name: name in {"G1", "gn_h-dev_a"})
    assert parser.parse("G1").kind is TokenKind.GROUP
    assert parser.parse("gn_h-dev_a").kind is TokenKind.GROUP
    assert parser.parse("G_UNDEF").kind is TokenKind.UNPARSED


def test_empty_token_skipped():
    assert TokenParser().parse("  ") is None


def test_token_is_stripped_and_cached():
    parser = TokenParser()
    assert parser.parse("  10.1.0.5 ") is parser.parse("10.1.0.5")


def test_custom_any_keywords():
    parser = TokenParser(any_keywords=["all"])
    assert parser.parse("ALL").kind is TokenKind.ANY
    assert parser.parse("any").kind is TokenKind.UNPARSED


def test_sort_markers_order():
    markers = ["UNPARSED:foo", "UNKNOWN", "ZONE-C", "CONFLICT", "ANY", "zone-a", "UNPARSED:bar"]
    assert sort_markers(markers) == [
        "zone-a",
        "ZONE-C",
        "ANY",
        "CONFLICT",
        "UNKNOWN",
        "UNPARSED:bar",
        "UNPARSED:foo",
    ]


def test_sort_markers_dedup():
    assert sort_markers(["B", "A", "B"]) == ["A", "B"]
