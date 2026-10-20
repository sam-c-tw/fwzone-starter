"""P4：Unresolved 工作表、問題 log、摘要輸出（T21–T25）。"""

import logging
import shutil
from pathlib import Path

import pytest

from fwzone.annotate import UNRESOLVED_COLUMNS
from fwzone.cli import main
from fwzone.tableio import read_table

FIXTURES = Path(__file__).parent / "fixtures"

ZONE_CSV = (
    "segment,zone\n"
    "10.1.0.0/24,ZONE-A\n"
    "10.1.1.0/24,ZONE-B\n"
    "10.2.0.0/16,ZONE-C\n"
    "2001:db8::/48,ZONE-V6\n"
)


def write(path, text):
    path.write_text(text, encoding="utf-8")
    return path


def rules_csv(tmp_path, rows):
    lines = ["source_grp,destination_grp", *(f'"{s}","{d}"' for s, d in rows)]
    return write(tmp_path / "rules.csv", "\n".join(lines) + "\n")


def run(tmp_path, rules, zones, *extra):
    out = tmp_path / "out.xlsx"
    code = main(["--rules", str(rules), "--zones", str(zones), "-o", str(out), *extra])
    return code, out


def unresolved(out):
    return read_table(out, "Unresolved")


def log_lines(tmp_path):
    return (tmp_path / "out_issues.log").read_text(encoding="utf-8").splitlines()


def cells(row):
    return [row[c] for c in UNRESOLVED_COLUMNS]


# --- Unresolved 工作表 -------------------------------------------------------


def test_t21_unresolved_sheet(tmp_path):
    zones = write(tmp_path / "zones.csv", ZONE_CSV)
    rules = rules_csv(
        tmp_path,
        [
            ("10.1.0.0/22", "10.1.0.5"),
            ("192.168.1.1", "G_UNDEF"),
            ("www.example.com", "10.0.0.1-20"),
        ],
    )
    code, out = run(tmp_path, rules, zones)
    assert code == 0
    table = unresolved(out)
    assert table.columns == list(UNRESOLVED_COLUMNS)
    assert [cells(r) for r in table.rows] == [
        ["UNKNOWN", "10.1.0.0/22", "10.1.2.0/23", "", "", "Source", "2", "1"],
        ["UNKNOWN", "192.168.1.1", "192.168.1.1/32", "", "", "Source", "3", "1"],
        ["UNPARSED", "10.0.0.1-20", "", "", "", "Destination", "4", "1"],
        ["UNPARSED", "G_UNDEF", "", "", "", "Destination", "3", "1"],
        ["UNPARSED", "www.example.com", "", "", "", "Source", "4", "1"],
    ]


def test_same_address_on_both_sides_and_many_rules_is_one_row(tmp_path):
    zones = write(tmp_path / "zones.csv", ZONE_CSV)
    rules = rules_csv(
        tmp_path, [("192.168.1.1", "192.168.1.1"), ("192.168.1.1", "10.1.0.5"), ("", "foo")]
    )
    code, out = run(tmp_path, rules, zones)
    assert code == 0
    assert [cells(r) for r in unresolved(out).rows] == [
        ["UNKNOWN", "192.168.1.1", "192.168.1.1/32", "", "", "Both", "2, 3", "2"],
        ["UNPARSED", "foo", "", "", "", "Destination", "4", "1"],
    ]


def test_address_with_conflict_and_unknown_has_two_rows(tmp_path):
    zones = write(tmp_path / "zones.csv", "segment,zone\n10.0.0.0/25,A\n10.0.0.64/26,B\n")
    rules = rules_csv(tmp_path, [("10.0.0.0/24", "")])
    code, out = run(tmp_path, rules, zones)
    assert code == 0
    assert [cells(r) for r in unresolved(out).rows] == [
        [
            "CONFLICT", "10.0.0.0/24", "10.0.0.64/26", "A (row 2) / B (row 3)",
            "", "Source", "2", "1",
        ],
        ["UNKNOWN", "10.0.0.0/24", "10.0.0.128/25", "", "", "Source", "2", "1"],
    ]
    assert read_table(out, "Rules").rows[0]["Source Zone"] == "A, CONFLICT, UNKNOWN"


def test_t14_via_groups(tmp_path):
    zones = write(tmp_path / "zones.csv", ZONE_CSV)
    groups = write(
        tmp_path / "groups.csv",
        "group_name,segment\nG5,host.example\nG1,\"10.9.9.9\nG2\"\nG2,10.8.8.8\n",
    )
    rules = rules_csv(tmp_path, [("G5", "G1")])
    code, out = run(tmp_path, rules, zones, "--groups", str(groups))
    assert code == 0
    assert [cells(r) for r in unresolved(out).rows] == [
        ["UNKNOWN", "10.8.8.8", "10.8.8.8/32", "", "G1, G2", "Destination", "2", "1"],
        ["UNKNOWN", "10.9.9.9", "10.9.9.9/32", "", "G1", "Destination", "2", "1"],
        ["UNPARSED", "host.example", "", "", "G5", "Source", "2", "1"],
    ]


def test_t15_conflict_rows_and_log(tmp_path):
    zones = write(tmp_path / "zones.csv", ZONE_CSV + "10.1.0.128/25,ZONE-X\n")
    rules = rules_csv(tmp_path, [("10.1.0.5", "10.1.0.200"), ("10.1.0.0/24", "10.1.0.5")])
    code, out = run(tmp_path, rules, zones)
    assert code == 0
    assert [cells(r) for r in unresolved(out).rows] == [
        [
            "CONFLICT", "10.1.0.0/24", "10.1.0.128/25", "ZONE-A (row 2) / ZONE-X (row 6)",
            "", "Source", "3", "1",
        ],
        [
            "CONFLICT", "10.1.0.200", "10.1.0.200/32", "ZONE-A (row 2) / ZONE-X (row 6)",
            "", "Destination", "2", "1",
        ],
    ]
    lines = log_lines(tmp_path)
    body = [line.split("\t") for line in lines if not line.startswith("#")]
    assert [(b[0], b[1], b[3]) for b in body] == [
        ("OVERLAP", "zones.csv!2", "10.1.0.0/24"),
        ("OVERLAP", "zones.csv!6", "10.1.0.128/25"),
    ]
    assert lines[-1] == "# 2 issues"


# --- 問題 log ----------------------------------------------------------------


def test_log_format_without_issues(tmp_path):
    zones = write(tmp_path / "zones.csv", ZONE_CSV)
    rules = rules_csv(tmp_path, [("10.1.0.5", "10.2.0.1")])
    code, _ = run(tmp_path, rules, zones)
    assert code == 0
    lines = log_lines(tmp_path)
    assert len(lines) == 3
    assert lines[0].startswith("# fwzone issues log  20")
    assert lines[1] == "# zones=zones.csv  rules=rules.csv  groups=-"
    assert lines[2] == "# 0 issues"


def test_log_written_next_to_output_with_tab_separated_fields(tmp_path):
    zones = write(tmp_path / "zones.csv", ZONE_CSV + "10.217.2.0/22,Z\n")
    rules = rules_csv(tmp_path, [("10.1.0.5", "10.2.0.1")])
    out = tmp_path / "sub" / "result.xlsx"
    out.parent.mkdir()
    assert main(["--rules", str(rules), "--zones", str(zones), "-o", str(out)]) == 0
    log = out.parent / "result_issues.log"
    assert log.is_file()
    (line,) = [x for x in log.read_text(encoding="utf-8").splitlines() if not x.startswith("#")]
    code, location, column, value, message = line.split("\t")
    assert (code, location, column, value) == (
        "HOST_BITS", "zones.csv!6", "segment", "10.217.2.0/22",
    )
    assert "/22" in message and "10.217.0.0" in message


def test_t10_rule_host_bits_logged_once_with_rule_rows(tmp_path):
    zones = write(tmp_path / "zones.csv", ZONE_CSV)
    rules = rules_csv(tmp_path, [("10.1.0.5/24", "10.2.0.1"), ("10.2.0.1", "10.1.0.5/24")])
    code, out = run(tmp_path, rules, zones)
    assert code == 0
    rows = read_table(out, "Rules").rows
    assert rows[0]["Source Zone"] == "UNPARSED:10.1.0.5/24"
    (line,) = [x for x in log_lines(tmp_path) if not x.startswith("#")]
    fields = line.split("\t")
    assert fields[:4] == ["HOST_BITS", "rules.csv!2", "source_grp, destination_grp", "10.1.0.5/24"]
    assert "10.1.0.0" in fields[4] and "規則列 2, 3" in fields[4]
    assert cells(unresolved(out).rows[0]) == [
        "UNPARSED", "10.1.0.5/24", "", "", "", "Both", "2, 3", "2",
    ]


def test_group_host_bits_logged_at_group_row(tmp_path):
    zones = write(tmp_path / "zones.csv", ZONE_CSV)
    groups = write(tmp_path / "groups.csv", "group_name,segment\nG0,10.2.0.1\nG1,10.1.0.5/24\n")
    rules = rules_csv(tmp_path, [("G1", "")])
    code, out = run(tmp_path, rules, zones, "--groups", str(groups))
    assert code == 0
    lines = log_lines(tmp_path)
    assert lines[1] == "# zones=zones.csv  rules=rules.csv  groups=groups.csv"
    (line,) = [x for x in lines if not x.startswith("#")]
    assert line.split("\t")[:4] == ["HOST_BITS", "groups.csv!3", "segment", "10.1.0.5/24"]
    assert read_table(out, "Rules").rows[0]["Source Zone"] == "UNPARSED:10.1.0.5/24"


def test_log_sorted_by_source_file_then_row(tmp_path):
    zones = write(tmp_path / "zones.csv", ZONE_CSV + "10.217.2.0/22,Z\n")
    groups = write(tmp_path / "groups.csv", "group_name,segment\nG1,10.1.0.5/24\n")
    rules = rules_csv(tmp_path, [("10.2.0.1/16", "G1")])
    run(tmp_path, rules, zones, "--groups", str(groups))
    locations = [x.split("\t")[1] for x in log_lines(tmp_path) if not x.startswith("#")]
    assert locations == ["groups.csv!2", "rules.csv!2", "zones.csv!6"]


def test_existing_log_without_force_exit_2(tmp_path):
    zones = write(tmp_path / "zones.csv", ZONE_CSV)
    rules = rules_csv(tmp_path, [("10.1.0.5", "10.1.0.5")])
    log = write(tmp_path / "out_issues.log", "old")
    code, out = run(tmp_path, rules, zones)
    assert code == 2
    assert log.read_text(encoding="utf-8") == "old"
    assert not out.exists()


def test_force_overwrites_log(tmp_path):
    zones = write(tmp_path / "zones.csv", ZONE_CSV)
    rules = rules_csv(tmp_path, [("10.1.0.5", "10.1.0.5")])
    write(tmp_path / "out_issues.log", "old")
    code, _ = run(tmp_path, rules, zones, "--force")
    assert code == 0
    assert log_lines(tmp_path)[-1] == "# 0 issues"


def test_warning_is_aggregated_unless_verbose(tmp_path, caplog):
    zones = write(tmp_path / "zones.csv", ZONE_CSV + "10.217.2.0/22,Z\nbad,Z\n")
    rules = rules_csv(tmp_path, [("10.1.0.5", "10.1.0.5")])
    with caplog.at_level(logging.WARNING, logger="fwzone"):
        run(tmp_path, rules, zones)
    assert "2 筆資料有問題" in caplog.text
    assert "HOST_BITS" not in caplog.text

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="fwzone"):
        run(tmp_path, rules, zones, "--force", "-v")
    assert "[HOST_BITS] zones.csv!6" in caplog.text
    assert "[INVALID_CIDR] zones.csv!7" in caplog.text


# --- 摘要 --------------------------------------------------------------------


def test_t23_summary_matches_output(tmp_path, capsys):
    zones = write(tmp_path / "zones.csv", ZONE_CSV + "bad,Z\n")
    rules = rules_csv(
        tmp_path,
        [
            ("10.1.0.0/22", "10.1.0.5"),
            ("192.168.1.1", "G_UNDEF"),
            ("www.example.com", "10.0.0.1-20"),
        ],
    )
    code, out = run(tmp_path, rules, zones)
    assert code == 0
    stdout = capsys.readouterr().out.splitlines()
    assert stdout == [
        "規則列數：3",
        "含 UNKNOWN 的規則數：2",
        "含 UNPARSED 的規則數：2",
        "Unresolved 位址數：5",
        "問題 log 筆數：1",
        f"輸出檔：{out}",
        f"問題 log：{tmp_path / 'out_issues.log'}",
    ]
    assert len(unresolved(out).rows) == 5
    assert log_lines(tmp_path)[-1] == "# 1 issues"


# --- 範例檔 ------------------------------------------------------------------


def _run_fixture(tmp_path, zone_name):
    if not (FIXTURES / "fw_rule.xlsx").is_file() or not (FIXTURES / zone_name).is_file():
        pytest.skip("tests/fixtures/ 的範例檔未提供（不隨 repo 上傳）")
    rules = shutil.copy(FIXTURES / "fw_rule.xlsx", tmp_path / "fw_rule.xlsx")
    zones = shutil.copy(FIXTURES / zone_name, tmp_path / zone_name)
    return run(tmp_path, Path(rules), Path(zones))


def test_t24_host_bits_zone_definition(tmp_path):
    code, out = _run_fixture(tmp_path, "zone_hostbits.xlsx")
    assert code == 0
    body = [x.split("\t") for x in log_lines(tmp_path) if not x.startswith("#")]
    assert len(body) == 1
    assert body[0][:4] == ["HOST_BITS", "zone_hostbits.xlsx:segment!6", "segment", "10.217.2.0/22"]
    assert "10.217.0.0" in body[0][4]
    assert log_lines(tmp_path)[-1] == "# 1 issues"
    assert read_table(out, "Rules").rows[3]["Source Zone"] == "h, uh, UNKNOWN"
    assert [cells(r) for r in unresolved(out).rows] == [
        ["UNKNOWN", "10.217.2.0/24", "10.217.2.0/24", "", "gn_uh_db", "Source", "5", "1"],
    ]


def test_t25_clean_sample_files(tmp_path):
    code, out = _run_fixture(tmp_path, "zone.xlsx")
    assert code == 0
    assert log_lines(tmp_path)[-1] == "# 0 issues"
    assert len(log_lines(tmp_path)) == 3
    table = unresolved(out)
    assert table.columns == list(UNRESOLVED_COLUMNS)
    assert table.rows == []
    assert [(r["Source Zone"], r["Destination Zone"]) for r in read_table(out, "Rules").rows] == [
        ("h-dev", "h-dev"),
        ("h-dev", "h"),
        ("h", "h-dev"),
        ("h, uh", "h-dev"),
    ]
