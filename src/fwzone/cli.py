"""命令列介面、結束碼（SPEC 第 7 節）。"""

import argparse
import logging
from collections.abc import Sequence
from pathlib import Path

from fwzone.annotate import annotate_rules, build_resolver, check_output_columns, load_zone_index
from fwzone.config import Config, load_config
from fwzone.errors import InputError, UsageError
from fwzone.groups import GroupResolver
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


def _load_groups(rules: Table, groups_path: Path | None, config: Config) -> GroupResolver:
    """Group 來源見 SPEC 3.3：--groups 優先；否則規則檔為 xlsx 且含 group 工作表時從規則檔讀。"""
    table: Table | None = None
    if groups_path is not None:
        table = read_table(groups_path, config.groups.sheet)
    elif rules.path.suffix.lower() == ".xlsx" and config.groups.sheet in sheet_names(rules.path):
        table = read_table(rules.path, config.groups.sheet)

    if table is None:
        logger.warning("未提供 group 定義，所有 group 名稱將標記為 UNPARSED")
        return build_resolver(None, config)
    table.require_columns(config.groups.name_column, config.groups.member_column)
    return build_resolver(table.rows, config)


def run(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    output: Path = args.output or args.rules.with_name(f"{args.rules.stem}_zoned.xlsx")
    if output.exists() and not args.force:
        raise UsageError(f"輸出檔已存在：{output}；如要覆寫請加 --force")

    rules = read_table(args.rules, config.rules.sheet)
    rules.require_columns(config.rules.source_column, config.rules.destination_column)
    check_output_columns(rules.columns, config)

    zones = read_table(args.zones, config.zones.sheet)
    zones.require_columns(config.zones.cidr_column, config.zones.zone_column)

    resolver = _load_groups(rules, args.groups, config)
    index, _issues = load_zone_index(zones.rows, config.zones, zones.path.name, zones.location)
    # TODO: 問題 log 與摘要輸出在 P4 實作；P3 先蒐集 zone 定義的問題但不輸出

    zone_cells = annotate_rules(rules.rows, config, resolver, index)
    out_cfg = config.output
    columns = [*rules.columns, out_cfg.source_zone_column, out_cfg.destination_zone_column]
    rows = [
        [*(row.get(c, "") for c in rules.columns), source, dest]
        for row, (source, dest) in zip(rules.rows, zone_cells, strict=True)
    ]
    highlight = frozenset({len(rules.columns), len(rules.columns) + 1})
    write_xlsx(output, [SheetData("Rules", columns, rows, highlight)])


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)  # 參數錯誤時 argparse 以結束碼 2 離開
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
