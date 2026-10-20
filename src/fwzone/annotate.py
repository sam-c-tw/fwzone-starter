"""串起各模組：載入 zone 定義、逐列產生 zone 字串、彙整 Unresolved 與問題。

只使用純 Python 資料結構。
"""

import ipaddress
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from fwzone.config import Config, GroupsConfig, ParsingConfig, RulesConfig, ZonesConfig
from fwzone.errors import InputError
from fwzone.groups import GroupResolver
from fwzone.parsing import (
    CONFLICT,
    HOST_BITS,
    UNKNOWN,
    UNPARSED_PREFIX,
    Interval,
    ParsedToken,
    TokenKind,
    sort_markers,
    tokenize,
)
from fwzone.zone_index import ZoneEntry, ZoneIndex, to_cidrs

Rows = Sequence[dict[str, str]]
Locator = Callable[[int], str]

UNPARSED = "UNPARSED"
UNRESOLVED_COLUMNS = (
    "Type",
    "Address",
    "Ranges",
    "Conflicting Zones",
    "Via Groups",
    "Side",
    "Rule Rows",
    "Rule Count",
)


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


@dataclass(frozen=True)
class Finding:
    """一個 token 產生的 CONFLICT / UNKNOWN / UNPARSED 紀錄（Unresolved 工作表的原料）。"""

    type: str
    address: str
    ranges: tuple[str, ...] = ()
    conflicting: tuple[tuple[int, str], ...] = ()  # (zone 定義列號, zone)
    via: tuple[str, ...] = ()


@dataclass(frozen=True)
class CellResult:
    markers: tuple[str, ...]
    findings: tuple[Finding, ...]

    def text(self, separator: str) -> str:
        return separator.join(self.markers)


@dataclass(frozen=True)
class RuleResult:
    source: CellResult
    destination: CellResult

    def has_marker(self, predicate: Callable[[str], bool]) -> bool:
        return any(predicate(m) for m in (*self.source.markers, *self.destination.markers))


@dataclass(frozen=True)
class UnresolvedRow:
    type: str
    address: str
    ranges: str
    conflicting_zones: str
    via_groups: str
    side: str
    rule_rows: str
    rule_count: int

    def as_cells(self) -> list[str]:
        return [
            self.type,
            self.address,
            self.ranges,
            self.conflicting_zones,
            self.via_groups,
            self.side,
            self.rule_rows,
            str(self.rule_count),
        ]


def _host_bits_hint(raw: str) -> str:
    loose = ipaddress.ip_network(raw, strict=False)
    return f"CIDR 含主機位元，/{loose.prefixlen} 的網段起點應為 {loose.network_address}"


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
                hint = _host_bits_hint(raw)
            except ValueError:
                issue(
                    "INVALID_CIDR", row_no, config.cidr_column, raw,
                    "segment 無法解析為 CIDR；此列未納入比對",
                )
            else:
                issue(
                    HOST_BITS, row_no, config.cidr_column, raw, f"{hint}；此列未納入比對"
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


def group_issues(
    rows: Rows, config: GroupsConfig, parsing: ParsingConfig, resolver: GroupResolver,
    file: str, locate: Locator,
) -> list[Issue]:
    """Group 成員中含主機位元的 CIDR（HOST_BITS）。同一列同一值只記一筆。"""
    issues: list[Issue] = []
    for i, row in enumerate(rows):
        row_no = i + 2
        seen: set[str] = set()
        for text in tokenize(row.get(config.member_column, ""), parsing.delimiters):
            token = resolver.parser.parse(text)
            if token is None or token.issue != HOST_BITS or token.raw in seen:
                continue
            seen.add(token.raw)
            issues.append(
                Issue(
                    HOST_BITS, file, row_no, locate(row_no), config.member_column, token.raw,
                    f"{_host_bits_hint(token.raw)}；標記為 UNPARSED",
                )
            )
    return issues


def rule_issues(
    rows: Rows, config: RulesConfig, parsing: ParsingConfig, resolver: GroupResolver,
    file: str, locate: Locator,
) -> list[Issue]:
    """規則中直接寫的含主機位元 CIDR（HOST_BITS）。同一 token 只記一筆，說明列出規則列號。"""
    found: dict[str, tuple[list[int], list[str]]] = {}
    for i, row in enumerate(rows):
        row_no = i + 2
        for column in (config.source_column, config.destination_column):
            for text in tokenize(row.get(column, ""), parsing.delimiters):
                token = resolver.parser.parse(text)
                if token is None or token.issue != HOST_BITS:
                    continue
                row_nos, columns = found.setdefault(token.raw, ([], []))
                if row_no not in row_nos:
                    row_nos.append(row_no)
                if column not in columns:
                    columns.append(column)

    issues: list[Issue] = []
    for raw, (row_nos, columns) in found.items():
        first = min(row_nos)
        # TODO(spec): 同一 token 同時出現在 source 與 destination 欄時的欄位名寫法未規定，
        # 目前以逗號並列
        issues.append(
            Issue(
                HOST_BITS, file, first, locate(first), ", ".join(columns), raw,
                f"{_host_bits_hint(raw)}；標記為 UNPARSED（出現於規則列 "
                f"{', '.join(str(r) for r in sorted(row_nos))}）",
            )
        )
    return issues


def annotate_cell(
    text: str, parsing: ParsingConfig, resolver: GroupResolver, index: ZoneIndex
) -> CellResult:
    """一個儲存格的 zone 標記與 Unresolved 紀錄。

    標記為所有 token（含 group 展開成員）的聯集，已去重並排序。
    """
    markers: set[str] = set()
    findings: list[Finding] = []

    def add(token: ParsedToken, via: tuple[str, ...]) -> None:
        if token.kind is TokenKind.INTERVAL:
            assert token.interval is not None
            result = index.lookup(token.interval)
            markers.update(result.markers)
            if result.conflicts:
                ranges = tuple(c for span in result.conflicts for c in to_cidrs(span.interval))
                rows = sorted({r for span in result.conflicts for r in span.rows})
                conflicting = tuple((r, index.zone_for_row(r)) for r in rows)
                findings.append(Finding(CONFLICT, token.raw, ranges, conflicting, via))
            if result.uncovered:
                ranges = tuple(c for gap in result.uncovered for c in to_cidrs(gap))
                findings.append(Finding(UNKNOWN, token.raw, ranges, (), via))
            return
        marker = token.marker
        assert marker is not None
        markers.add(marker)
        if token.kind is TokenKind.UNPARSED:
            findings.append(Finding(UNPARSED, token.raw, via=via))

    for text_token in tokenize(text, parsing.delimiters):
        parsed = resolver.parser.parse(text_token)
        if parsed is None:
            continue
        if parsed.kind is TokenKind.GROUP:
            for member in resolver.expand(parsed.raw):
                add(member.token, member.via)
        else:
            add(parsed, ())
    return CellResult(tuple(sort_markers(markers)), tuple(findings))


def annotate_rules(
    rows: Rows, config: Config, resolver: GroupResolver, index: ZoneIndex
) -> list[RuleResult]:
    return [
        RuleResult(
            annotate_cell(row.get(config.rules.source_column, ""), config.parsing, resolver, index),
            annotate_cell(
                row.get(config.rules.destination_column, ""), config.parsing, resolver, index
            ),
        )
        for row in rows
    ]


@dataclass
class _Accumulator:
    ranges: list[str] = field(default_factory=list)
    conflicting: dict[int, str] = field(default_factory=dict)
    via: set[str] = field(default_factory=set)
    sides: set[str] = field(default_factory=set)
    rule_rows: set[int] = field(default_factory=set)


def build_unresolved(results: Sequence[RuleResult]) -> list[UnresolvedRow]:
    """彙整 Unresolved 工作表（SPEC 6.2）：依 (Type, Address) 去重後排序。"""
    acc: dict[tuple[str, str], _Accumulator] = {}
    for i, result in enumerate(results):
        row_no = i + 2
        for side, cell in (("Source", result.source), ("Destination", result.destination)):
            for finding in cell.findings:
                item = acc.setdefault((finding.type, finding.address), _Accumulator())
                for r in finding.ranges:
                    if r not in item.ranges:
                        item.ranges.append(r)
                item.conflicting.update(finding.conflicting)
                item.via.update(finding.via)
                item.sides.add(side)
                item.rule_rows.add(row_no)

    rows: list[UnresolvedRow] = []
    for (type_, address), item in sorted(acc.items()):
        rows.append(
            UnresolvedRow(
                type=type_,
                address=address,
                ranges=", ".join(item.ranges),
                conflicting_zones=" / ".join(
                    f"{zone} (row {row})" for row, zone in sorted(item.conflicting.items())
                ),
                via_groups=", ".join(sorted(item.via)),
                side="Both" if len(item.sides) == 2 else next(iter(item.sides)),
                rule_rows=", ".join(str(r) for r in sorted(item.rule_rows)),
                rule_count=len(item.rule_rows),
            )
        )
    return rows


def format_issues_log(
    issues: Sequence[Issue], zones: str, rules: str, groups: str, now: datetime
) -> str:
    """問題 log 內容（SPEC 6.4）：一行一筆，欄位以 tab 分隔，依來源檔、列號排序。"""

    def clean(text: str) -> str:
        return " ".join(text.replace("\t", " ").split())

    lines = [
        f"# fwzone issues log  {now.isoformat(timespec='seconds')}",
        f"# zones={zones}  rules={rules}  groups={groups}",
    ]
    for item in sorted(issues, key=lambda x: x.sort_key):
        lines.append(
            "\t".join(
                [item.code, item.location, item.column, clean(item.value), clean(item.message)]
            )
        )
    lines.append(f"# {len(issues)} issues")
    return "\n".join(lines) + "\n"


def check_output_columns(columns: Sequence[str], config: Config) -> None:
    """原檔已有同名輸出欄位時拋出 InputError。"""
    for name in (config.output.source_zone_column, config.output.destination_zone_column):
        if name in columns:
            raise InputError(f"規則檔已有欄位 {name!r}，請修改設定檔 [output] 的輸出欄名")


def is_unparsed(marker: str) -> bool:
    return marker.startswith(UNPARSED_PREFIX)
