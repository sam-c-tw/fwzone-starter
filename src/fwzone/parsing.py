"""位址 token 的切分與解析（SPEC 第 4 節）。只使用純 Python 資料結構。"""

import ipaddress
import re
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass
from enum import Enum
from typing import NamedTuple

DEFAULT_DELIMITERS: tuple[str, ...] = ("\n", ",", ";")
DEFAULT_ANY_KEYWORDS: tuple[str, ...] = ("any",)

# Zone 標記（SPEC 第 2 節）
ANY = "ANY"
CONFLICT = "CONFLICT"
UNKNOWN = "UNKNOWN"
UNPARSED_PREFIX = "UNPARSED:"

# 問題代碼（SPEC 6.4）
HOST_BITS = "HOST_BITS"

_RANGE_SPACES = re.compile(r"\s*-\s*")


class Interval(NamedTuple):
    """連續 IP 範圍 [start, end]，以整數表示；version 為 4 或 6。"""

    version: int
    start: int
    end: int


class TokenKind(Enum):
    ANY = "any"
    INTERVAL = "interval"
    GROUP = "group"
    UNPARSED = "unparsed"


@dataclass(frozen=True)
class ParsedToken:
    kind: TokenKind
    raw: str
    interval: Interval | None = None
    issue: str | None = None

    @property
    def marker(self) -> str | None:
        """ANY 與 UNPARSED 直接對應的 zone 標記；其餘為 None。"""
        if self.kind is TokenKind.ANY:
            return ANY
        if self.kind is TokenKind.UNPARSED:
            return UNPARSED_PREFIX + self.raw
        return None


def tokenize(text: str, delimiters: Collection[str] = DEFAULT_DELIMITERS) -> list[str]:
    """依分隔符切分儲存格內容，去除前後空白並略過空 token。"""
    text = text.replace("\r\n", "\n")
    if delimiters:
        pattern = "|".join(re.escape(d) for d in delimiters)
        parts = re.split(pattern, text)
    else:
        parts = [text]
    return [p.strip() for p in parts if p.strip()]


def _parse_range(text: str) -> Interval | None:
    if text.count("-") != 1:
        return None
    left, right = text.split("-")
    try:
        start = ipaddress.ip_address(left)
        end = ipaddress.ip_address(right)
    except ValueError:
        return None
    if start.version != end.version or int(start) > int(end):
        return None
    return Interval(start.version, int(start), int(end))


def _parse_network(raw: str) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network | None, bool]:
    """回傳 (網段, 是否含主機位元)。含主機位元時網段為 None。"""
    try:
        return ipaddress.ip_network(raw, strict=True), False
    except ValueError:
        pass
    if "/" in raw:
        try:
            ipaddress.ip_network(raw, strict=False)
        except ValueError:
            return None, False
        return None, True
    return None, False


class TokenParser:
    """依 SPEC 第 4 節的順序解析 token，結果以原始字串為鍵快取。

    Group 的存在與否由 ``is_group`` 判斷；成員展開屬於 groups.py。
    """

    def __init__(
        self,
        is_group: Callable[[str], bool] | None = None,
        any_keywords: Iterable[str] = DEFAULT_ANY_KEYWORDS,
    ) -> None:
        self._is_group = is_group or (lambda _name: False)
        self._any_keywords = frozenset(k.strip().casefold() for k in any_keywords)
        self._cache: dict[str, ParsedToken] = {}

    def parse(self, token: str) -> ParsedToken | None:
        """解析單一 token；空字串回傳 None（略過）。"""
        raw = token.strip()
        if not raw:
            return None
        cached = self._cache.get(raw)
        if cached is None:
            cached = self._parse(raw)
            self._cache[raw] = cached
        return cached

    def _parse(self, raw: str) -> ParsedToken:
        network, host_bits = _parse_network(raw)

        if raw.casefold() in self._any_keywords or (network is not None and network.prefixlen == 0):
            return ParsedToken(TokenKind.ANY, raw)

        interval = _parse_range(_RANGE_SPACES.sub("-", raw))
        if interval is not None:
            return ParsedToken(TokenKind.INTERVAL, raw, interval=interval)

        if network is not None:
            return ParsedToken(
                TokenKind.INTERVAL,
                raw,
                interval=Interval(
                    network.version,
                    int(network.network_address),
                    int(network.broadcast_address),
                ),
            )
        if host_bits:
            return ParsedToken(TokenKind.UNPARSED, raw, issue=HOST_BITS)

        if self._is_group(raw):
            return ParsedToken(TokenKind.GROUP, raw)

        return ParsedToken(TokenKind.UNPARSED, raw)


def sort_markers(markers: Iterable[str]) -> list[str]:
    """去重並排序 zone 標記（SPEC 5.3）。

    一般 zone 依 casefold 升冪，其後依序為 ANY、CONFLICT、UNKNOWN、UNPARSED:*（依原值升冪）。
    """
    unique = set(markers)
    special = (ANY, CONFLICT, UNKNOWN)
    zones = sorted(
        (m for m in unique if m not in special and not m.startswith(UNPARSED_PREFIX)),
        key=lambda m: (m.casefold(), m),
    )
    unparsed = sorted(m for m in unique if m.startswith(UNPARSED_PREFIX))
    return zones + [m for m in special if m in unique] + unparsed
