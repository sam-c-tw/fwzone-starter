"""P3 的 CLI 端到端測試（T15–T20）與範例檔的 Rules 工作表驗證。"""

import logging
import shutil
from pathlib import Path

import pytest

from fwzone.annotate import load_zone_index
from fwzone.cli import main
from fwzone.config import ZonesConfig
from fwzone.tableio import read_table

FIXTURES = Path(__file__).parent / "fixtures"

ZONE_CSV = (
    "segment,zone\n"
    "10.1.0.0/24,ZONE-A\n"
    "10.1.1.0/24,ZONE-B\n"
    "10.2.0.0/16,ZONE-C\n"
    "2001:db8::/48,ZONE-V6\n"
)


def write(path, text, encoding="utf-8"):
    path.write_text(text, encoding=encoding)
    return path


def rules_csv(tmp_path, rows, header="source_grp,destination_grp", name="rules.csv"):
    lines = [header, *(f'"{s}","{d}"' for s, d in rows)]
    return write(tmp_path / name, "\n".join(lines) + "\n")


def run(tmp_path, rules, zones, *extra):
    out = tmp_path / "out.xlsx"
    code = main(["--rules", str(rules), "--zones", str(zones), "-o", str(out), *extra])
    return code, out


def test_t15_overlap_conflict(tmp_path):
    zones = write(tmp_path / "zones.csv", ZONE_CSV + "10.1.0.128/25,ZONE-X\n")
    rules = rules_csv(
        tmp_path,
        [("10.1.0.5", "10.1.0.200"), ("10.1.0.0/24", "10.1.0.5")],
    )
    code, out = run(tmp_path, rules, zones)
    assert code == 0
    rows = read_table(out, "Rules").rows
    assert (rows[0]["Source Zone"], rows[0]["Destination Zone"]) == ("ZONE-A", "CONFLICT")
    assert rows[1]["Source Zone"] == "ZONE-A, CONFLICT"

    table = read_table(zones, None)
    _, issues = load_zone_index(table.rows, ZonesConfig(), "zones.csv", table.location)
    assert [(i.code, i.row) for i in issues] == [("OVERLAP", 2), ("OVERLAP", 6)]
    assert "第 6 列" in issues[0].message and "10.1.0.128/25" in issues[0].message
    assert "第 2 列" in issues[1].message


def test_zone_definition_problems_are_recorded_and_rows_excluded(tmp_path):
    zones = write(
        tmp_path / "zones.csv",
        "segment,zone\n"
        "10.1.0.0/24,ZONE-A\n"  # row 2 ok
        "10.217.2.0/22,ZONE-H\n"  # row 3 HOST_BITS
        "not-a-cidr,ZONE-I\n"  # row 4 INVALID_CIDR
        "10.9.0.0/24,\n",  # row 5 EMPTY_ZONE
    )
    table = read_table(zones, None)
    index, issues = load_zone_index(table.rows, ZonesConfig(), "zones.csv", table.location)
    assert [(i.code, i.row) for i in issues] == [
        ("HOST_BITS", 3),
        ("INVALID_CIDR", 4),
        ("EMPTY_ZONE", 5),
    ]
    assert "10.217.0.0" in issues[0].message
    assert issues[0].location == "zones.csv!3"
    rules = rules_csv(tmp_path, [("10.1.0.5", "10.217.2.5"), ("10.9.0.1", "10.9.0.1")])
    code, out = run(tmp_path, rules, zones)
    assert code == 0
    rows = read_table(out, "Rules").rows
    assert rows[0]["Destination Zone"] == "UNKNOWN"
    assert rows[1]["Source Zone"] == "UNKNOWN"
    assert index.lookup(read_ip(4, "10.1.0.5")).markers == {"ZONE-A"}


def read_ip(version, text):
    import ipaddress

    from fwzone.parsing import Interval

    n = int(ipaddress.ip_address(text))
    return Interval(version, n, n)


def test_t16_bom_csv_through_cli(tmp_path):
    zones = write(tmp_path / "zones.csv", "﻿" + ZONE_CSV)
    rules = write(tmp_path / "rules.csv", "﻿source_grp,destination_grp\n10.1.0.5,10.2.0.1\n")
    code, out = run(tmp_path, rules, zones)
    assert code == 0
    table = read_table(out, "Rules")
    assert table.columns[0] == "source_grp"
    assert table.rows[0]["Source Zone"] == "ZONE-A"
    assert table.rows[0]["Destination Zone"] == "ZONE-C"


def test_t17_missing_rule_column_exit_1(tmp_path, caplog):
    zones = write(tmp_path / "zones.csv", ZONE_CSV)
    rules = write(tmp_path / "rules.csv", "src,dst\n1.1.1.1,2.2.2.2\n")
    with caplog.at_level(logging.ERROR, logger="fwzone"):
        code, out = run(tmp_path, rules, zones)
    assert code == 1
    assert not out.exists()
    assert "'src'" in caplog.text and "'dst'" in caplog.text and "'source_grp'" in caplog.text


def test_t17_missing_zone_column_exit_1(tmp_path, caplog):
    zones = write(tmp_path / "zones.csv", "cidr,name\n10.1.0.0/24,A\n")
    rules = rules_csv(tmp_path, [("10.1.0.5", "10.1.0.5")])
    with caplog.at_level(logging.ERROR, logger="fwzone"):
        code, _ = run(tmp_path, rules, zones)
    assert code == 1
    assert "'cidr'" in caplog.text and "'name'" in caplog.text


def test_t17_missing_sheet_exit_1(tmp_path, caplog):
    import openpyxl

    rules = tmp_path / "rules.xlsx"
    wb = openpyxl.Workbook()
    wb.active.title = "other"
    wb.save(rules)
    zones = write(tmp_path / "zones.csv", ZONE_CSV)
    with caplog.at_level(logging.ERROR, logger="fwzone"):
        code, _ = run(tmp_path, rules, zones)
    assert code == 1
    assert "'rule'" in caplog.text and "'other'" in caplog.text


def test_t18_extra_columns_and_leading_zeros_preserved(tmp_path):
    zones = write(tmp_path / "zones.csv", ZONE_CSV)
    rules = write(
        tmp_path / "rules.csv",
        'id,source_grp,note,destination_grp\n00123,10.1.0.5,"a,b",10.2.0.1\n',
    )
    code, out = run(tmp_path, rules, zones)
    assert code == 0
    table = read_table(out, "Rules")
    assert table.columns == [
        "id", "source_grp", "note", "destination_grp", "Source Zone", "Destination Zone",
    ]
    assert table.rows[0] == {
        "id": "00123",
        "source_grp": "10.1.0.5",
        "note": "a,b",
        "destination_grp": "10.2.0.1",
        "Source Zone": "ZONE-A",
        "Destination Zone": "ZONE-C",
    }


def test_t19_empty_cell_gives_empty_string(tmp_path):
    zones = write(tmp_path / "zones.csv", ZONE_CSV)
    rules = write(tmp_path / "rules.csv", "source_grp,destination_grp\n,10.1.0.5\n")
    code, out = run(tmp_path, rules, zones)
    assert code == 0
    row = read_table(out, "Rules").rows[0]
    assert row["Source Zone"] == ""
    assert row["Destination Zone"] == "ZONE-A"


def test_t20_existing_output_without_force_exit_2(tmp_path):
    zones = write(tmp_path / "zones.csv", ZONE_CSV)
    rules = rules_csv(tmp_path, [("10.1.0.5", "10.1.0.5")])
    out = tmp_path / "out.xlsx"
    out.write_bytes(b"original")
    assert main(["--rules", str(rules), "--zones", str(zones), "-o", str(out)]) == 2
    assert out.read_bytes() == b"original"


def test_force_overwrites(tmp_path):
    zones = write(tmp_path / "zones.csv", ZONE_CSV)
    rules = rules_csv(tmp_path, [("10.1.0.5", "10.1.0.5")])
    out = tmp_path / "out.xlsx"
    out.write_bytes(b"original")
    assert main(["--rules", str(rules), "--zones", str(zones), "-o", str(out), "--force"]) == 0
    assert read_table(out, "Rules").rows[0]["Source Zone"] == "ZONE-A"


def test_default_output_name(tmp_path):
    zones = write(tmp_path / "zones.csv", ZONE_CSV)
    rules = rules_csv(tmp_path, [("10.1.0.5", "10.1.0.5")], name="fw.csv")
    assert main(["--rules", str(rules), "--zones", str(zones)]) == 0
    assert (tmp_path / "fw_zoned.xlsx").is_file()


def test_output_column_name_collision_exit_1(tmp_path, caplog):
    zones = write(tmp_path / "zones.csv", ZONE_CSV)
    rules = write(
        tmp_path / "rules.csv", "source_grp,destination_grp,Source Zone\n10.1.0.5,10.1.0.5,x\n"
    )
    with caplog.at_level(logging.ERROR, logger="fwzone"):
        code, out = run(tmp_path, rules, zones)
    assert code == 1
    assert "Source Zone" in caplog.text
    assert not out.exists()


def test_invalid_config_exit_2(tmp_path):
    zones = write(tmp_path / "zones.csv", ZONE_CSV)
    rules = rules_csv(tmp_path, [("10.1.0.5", "10.1.0.5")])
    config = write(tmp_path / "bad.toml", "[rules\n")
    code, _ = run(tmp_path, rules, zones, "--config", str(config))
    assert code == 2


def test_missing_arguments_exit_2():
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2


def test_custom_config_columns_and_separator(tmp_path):
    zones = write(tmp_path / "zones.csv", "cidr,name\n10.1.0.0/24,A\n10.1.1.0/24,B\n")
    rules = write(tmp_path / "rules.csv", "s,d\n10.1.0.0/23,10.1.0.1\n")
    config = write(
        tmp_path / "c.toml",
        '[rules]\nsource_column="s"\ndestination_column="d"\n'
        '[zones]\ncidr_column="cidr"\nzone_column="name"\n'
        '[output]\nsource_zone_column="SZ"\ndestination_zone_column="DZ"\nzone_separator=" | "\n',
    )
    code, out = run(tmp_path, rules, zones, "--config", str(config))
    assert code == 0
    row = read_table(out, "Rules").rows[0]
    assert (row["SZ"], row["DZ"]) == ("A | B", "A")


def test_groups_from_separate_file_and_warning_when_missing(tmp_path, caplog):
    zones = write(tmp_path / "zones.csv", ZONE_CSV)
    rules = rules_csv(tmp_path, [("G1", "G1")])
    with caplog.at_level(logging.WARNING, logger="fwzone"):
        code, out = run(tmp_path, rules, zones)
    assert code == 0
    assert "group" in caplog.text
    assert read_table(out, "Rules").rows[0]["Source Zone"] == "UNPARSED:G1"

    groups = write(tmp_path / "groups.csv", 'group_name,segment\nG1,"10.1.0.5\n10.2.0.1"\n')
    out.unlink()
    (tmp_path / "out_issues.log").unlink()
    code, out = run(tmp_path, rules, zones, "--groups", str(groups))
    assert code == 0
    assert read_table(out, "Rules").rows[0]["Source Zone"] == "ZONE-A, ZONE-C"


def test_group_cycle_exit_1(tmp_path, caplog):
    zones = write(tmp_path / "zones.csv", ZONE_CSV)
    rules = rules_csv(tmp_path, [("G3", "G3")])
    groups = write(tmp_path / "groups.csv", "group_name,segment\nG3,G4\nG4,G3\n")
    with caplog.at_level(logging.ERROR, logger="fwzone"):
        code, _ = run(tmp_path, rules, zones, "--groups", str(groups))
    assert code == 1
    assert "G3 -> G4 -> G3" in caplog.text


# --- 範例檔（tests/fixtures）的 Rules 工作表 ---------------------------------


def _run_fixture(tmp_path, zone_name):
    rules = shutil.copy(FIXTURES / "fw_rule.xlsx", tmp_path / "fw_rule.xlsx")
    zones = shutil.copy(FIXTURES / zone_name, tmp_path / zone_name)
    code, out = run(tmp_path, Path(rules), Path(zones))
    assert code == 0
    return read_table(out, "Rules")


def test_t25_sample_files_rules_sheet(tmp_path):
    table = _run_fixture(tmp_path, "zone.xlsx")
    assert table.columns[-2:] == ["Source Zone", "Destination Zone"]
    assert table.columns[:6] == ["FW_ID", "source_grp", "destination_grp", "service_grp", "status", "remark"]
    assert [(r["Source Zone"], r["Destination Zone"]) for r in table.rows] == [
        ("h-dev", "h-dev"),
        ("h-dev", "h"),
        ("h", "h-dev"),
        ("h, uh", "h-dev"),
    ]


def test_t24_sample_files_rules_sheet_with_host_bits(tmp_path):
    table = _run_fixture(tmp_path, "zone_hostbits.xlsx")
    assert table.rows[3]["Source Zone"] == "h, uh, UNKNOWN"
    assert table.rows[3]["Destination Zone"] == "h-dev"

    zones = read_table(FIXTURES / "zone_hostbits.xlsx", "segment")
    _, issues = load_zone_index(zones.rows, ZonesConfig(), zones.path.name, zones.location)
    assert [(i.code, i.location) for i in issues] == [("HOST_BITS", "zone_hostbits.xlsx:segment!6")]
