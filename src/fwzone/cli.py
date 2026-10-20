"""命令列介面、結束碼、摘要輸出（SPEC 第 7 節）。"""

import argparse
import logging
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from fwzone.annotate import (
    UNRESOLVED_COLUMNS,
    annotate_rules,
    build_resolver,
    build_unresolved,
    check_output_columns,
    format_issues_log,
    group_issues,
    is_unparsed,
    load_zone_index,
    rule_issues,
)
from fwzone.config import Config, load_config
from fwzone.errors import InputError, UsageError
from fwzone.groups import GroupResolver
from fwzone.parsing import UNKNOWN
from fwzone.tableio import SheetData, Table, read_table, sheet_names, write_xlsx

logger = logging.getLogger("fwzone")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fwzone", description="防火牆規則 Zone 標註工具")
    parser.add_argument("--rules", required=True, type=Path, help="規則檔路徑")
    parser.add_argument("--zones", required=True, type=Path, help="Zone 定義檔路徑")
    parser.add_argument("--groups", type=Path, help="Group 檔路徑")
    parser.add_argument("--config", type=Path, help="設定檔路徑")
    parser.add_argument("-o", "--output", type=Path, help="輸出路徑")
    parser.add_argument("--force", action="store_true", help="覆寫既有輸出檔")
    parser.add_argument("-v", "--verbose", action="store_true", help="輸出每筆警告的細節")
    return parser


def _load_groups(
    rules: Table, groups_path: Path | None, config: Config
) -> tuple[GroupResolver, Table | None]:
    """Group 來源見 SPEC 3.3：--groups 優先；否則規則檔為 xlsx 且含 group 工作表時從規則檔讀。"""
    table: Table | None = None
    if groups_path is not None:
        table = read_table(groups_path, config.groups.sheet)
    elif rules.path.suffix.lower() == ".xlsx" and config.groups.sheet in sheet_names(rules.path):
        table = read_table(rules.path, config.groups.sheet)

    if table is None:
        logger.warning("未提供 group 定義，所有 group 名稱將標記為 UNPARSED")
        return build_resolver(None, config), None
    table.require_columns(config.groups.name_column, config.groups.member_column)
    return build_resolver(table.rows, config), table


def _write_log(path: Path, content: str) -> None:
    try:
        with path.open("w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
    except PermissionError as exc:
        # TODO(spec): 寫入失敗的結束碼未定義，目前視為輸入資料錯誤（1）
        raise InputError(f"無法寫入 {path}；若檔案正被其他程式開啟，請先關閉後再執行") from exc


def run(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    output: Path = args.output or args.rules.with_name(f"{args.rules.stem}_zoned.xlsx")
    log_path = output.with_name(f"{output.stem}_issues.log")
    for target in (output, log_path):
        if target.exists() and not args.force:
            raise UsageError(f"輸出檔已存在：{target}；如要覆寫請加 --force")

    rules = read_table(args.rules, config.rules.sheet)
    rules.require_columns(config.rules.source_column, config.rules.destination_column)
    check_output_columns(rules.columns, config)

    zones = read_table(args.zones, config.zones.sheet)
    zones.require_columns(config.zones.cidr_column, config.zones.zone_column)

    resolver, groups = _load_groups(rules, args.groups, config)
    index, issues = load_zone_index(zones.rows, config.zones, zones.path.name, zones.location)
    if groups is not None:
        issues += group_issues(
            groups.rows, config.groups, config.parsing, resolver, groups.path.name, groups.location
        )
    issues += rule_issues(
        rules.rows, config.rules, config.parsing, resolver, rules.path.name, rules.location
    )

    results = annotate_rules(rules.rows, config, resolver, index)
    unresolved = build_unresolved(results)

    out_cfg = config.output
    columns = [*rules.columns, out_cfg.source_zone_column, out_cfg.destination_zone_column]
    rows = [
        [
            *(row.get(c, "") for c in rules.columns),
            result.source.text(out_cfg.zone_separator),
            result.destination.text(out_cfg.zone_separator),
        ]
        for row, result in zip(rules.rows, results, strict=True)
    ]
    highlight = frozenset({len(rules.columns), len(rules.columns) + 1})
    write_xlsx(
        output,
        [
            SheetData("Rules", columns, rows, highlight),
            SheetData("Unresolved", UNRESOLVED_COLUMNS, [r.as_cells() for r in unresolved]),
        ],
    )

    groups_name = groups.path.name if groups is not None else "-"
    now = datetime.now().astimezone()
    _write_log(
        log_path,
        format_issues_log(issues, zones.path.name, rules.path.name, groups_name, now),
    )

    if issues:
        if args.verbose:
            for item in sorted(issues, key=lambda x: x.sort_key):
                logger.warning("[%s] %s：%s", item.code, item.location, item.message)
        else:
            logger.warning("%d 筆資料有問題，詳見問題 log：%s", len(issues), log_path)

    # TODO(spec): 「Unresolved 位址數」未說明以列數或相異位址計，目前以 Unresolved 工作表的列數計
    summary = [
        f"規則列數：{len(results)}",
        f"含 UNKNOWN 的規則數：{sum(r.has_marker(lambda m: m == UNKNOWN) for r in results)}",
        f"含 UNPARSED 的規則數：{sum(r.has_marker(is_unparsed) for r in results)}",
        f"Unresolved 位址數：{len(unresolved)}",
        f"問題 log 筆數：{len(issues)}",
        f"輸出檔：{output}",
        f"問題 log：{log_path}",
    ]
    print("\n".join(summary))


def _tolerate_unencodable_output() -> None:
    """避免 stdout / stderr 的編碼無法表示中文時中斷執行（例如被導向到非 UTF-8 的管線）。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)  # 參數錯誤時 argparse 以結束碼 2 離開
    _tolerate_unencodable_output()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s: %(message)s",
    )
    try:
        run(args)
    except InputError as exc:
        logger.error("%s", exc, exc_info=args.verbose)
        return 1
    except UsageError as exc:
        logger.error("%s", exc, exc_info=args.verbose)
        return 2
    return 0
