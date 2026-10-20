"""串起各模組：載入 zone 定義、逐列產生 zone 字串。只使用純 Python 資料結構。"""

import ipaddress
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from fwzone.config import Config, ParsingConfig, ZonesConfig
from fwzone.errors import InputError
from fwzone.groups import GroupResolver
from fwzone.parsing import Interval, ParsedToken, TokenKind, sort_markers, tokenize
from fwzone.zone_index import ZoneEntry, ZoneIndex, to_cidrs

Rows = Sequence[dict[str, str]]
Locator = Callable[[int], str]


@dataclass(frozen=True)
class Issue:
    """問題 log 的一筆（SPEC 6.4）。row 為來源檔列號。"""

    code: str
    file: str
    row: int
    location: str
    column: str
    value: str
    message: str

    @property
    def sort_key(self) -> tuple[str, int]:
        return (self.file, self.row)


def load_zone_index(
    rows: Rows, config: ZonesConfig, file: str, locate: Locator
) -> tuple[ZoneIndex, list[Issue]]:
    """逐列檢查 zone 定義（SPEC 3.4），建立索引。有問題的列記錄後繼續，不停止、不修正。"""
    issues: list[Issue] = []
    entries: list[ZoneEntry] = []
    raw_by_row: dict[int, str] = {}

    def issue(code: str, row_no: int, column: str, value: str, message: str) -> None:
        issues.append(Issue(code, file, row_no, locate(row_no), column, value, message))

    for i, row in enumerate(rows):
        row_no = i + 2
        raw = row.get(config.cidr_column, "").strip()
        zone = row.get(config.zone_column, "").strip()
        network: ipaddress.IPv4Network | ipaddress.IPv6Network | None = None

        # TODO(spec): 同一列同時有多種問題時是否都要記錄未規定，目前各自記錄
        try:
            network = ipaddress.ip_network(raw, strict=True)
        except ValueError:
            try:
                loose = ipaddress.ip_network(raw, strict=False)
            except ValueError:
                issue(
                    "INVALID_CIDR", row_no, config.cidr_column, raw,
                    "segment 無法解析為 CIDR；此列未納入比對",
                )
            else:
                issue(
                    "HOST_BITS", row_no, config.cidr_column, raw,
                    f"CIDR 含主機位元，/{loose.prefixlen} 的網段起點應為 "
                    f"{loose.network_address}；此列未納入比對",
                )
        if not zone:
            issue(
                "EMPTY_ZONE", row_no, config.zone_column, zone,
                f"zone 為空（網段 {raw}）；此列未納入比對",
            )

        if network is not None and zone:
            interval = Interval(
                network.version, int(network.network_address), int(network.broadcast_address)
            )
            entries.append(ZoneEntry(row_no, interval, zone))
            raw_by_row[row_no] = raw

    index = ZoneIndex(entries)

    others_by_row: dict[int, set[int]] = {}
    ranges_by_row: dict[int, list[str]] = {}
    for span in index.conflict_spans():
        for row_no in span.rows:
            others_by_row.setdefault(row_no, set()).update(r for r in span.rows if r != row_no)
            ranges_by_row.setdefault(row_no, []).extend(to_cidrs(span.interval))
    for row_no in sorted(others_by_row):
        others = ", ".join(str(r) for r in sorted(others_by_row[row_no]))
        ranges = ", ".join(ranges_by_row[row_no])
        issue(
            "OVERLAP", row_no, config.cidr_column, raw_by_row[row_no],
            f"與第 {others} 列重疊（重疊範圍：{ranges}）；兩列均保留，重疊部分比對為 CONFLICT",
        )

    issues.sort(key=lambda x: x.sort_key)
    return index, issues


def build_resolver(rows: Rows | None, config: Config) -> GroupResolver:
    """rows 為 None 表示沒有 group 定義（所有 group 名稱成為 UNPARSED）。"""
    pairs = [
        (row.get(config.groups.name_column, ""), row.get(config.groups.member_column, ""))
        for row in rows or []
    ]
    return GroupResolver(pairs, config.parsing.delimiters, config.parsing.any_keywords)


def cell_markers(
    text: str, parsing: ParsingConfig, resolver: GroupResolver, index: ZoneIndex
) -> list[str]:
    """一個儲存格的 zone 標記：所有 token（含 group 展開成員）的聯集，已去重並排序。"""
    markers: set[str] = set()

    def add(token: ParsedToken) -> None:
        if token.kind is TokenKind.INTERVAL:
            markers.update(index.lookup(token.interval).markers)
        else:
            marker = token.marker
            assert marker is not None
            markers.add(marker)

    for text_token in tokenize(text, parsing.delimiters):
        parsed = resolver.parser.parse(text_token)
        if parsed is None:
            continue
        if parsed.kind is TokenKind.GROUP:
            for member in resolver.expand(parsed.raw):
                add(member.token)
        else:
            add(parsed)
    return sort_markers(markers)


def annotate_rules(
    rows: Rows, config: Config, resolver: GroupResolver, index: ZoneIndex
) -> list[tuple[str, str]]:
    """每條規則回傳 (Source Zone, Destination Zone) 字串。"""
    sep = config.output.zone_separator
    result: list[tuple[str, str]] = []
    for row in rows:
        source_cell = row.get(config.rules.source_column, "")
        dest_cell = row.get(config.rules.destination_column, "")
        source = cell_markers(source_cell, config.parsing, resolver, index)
        dest = cell_markers(dest_cell, config.parsing, resolver, index)
        result.append((sep.join(source), sep.join(dest)))
    return result


def check_output_columns(columns: Sequence[str], config: Config) -> None:
    """原檔已有同名輸出欄位時拋出 InputError。"""
    for name in (config.output.source_zone_column, config.output.destination_zone_column):
        if name in columns:
            raise InputError(
                f"規則檔已有欄位 {name!r}，請修改設定檔 [output] 的輸出欄名"
            )
