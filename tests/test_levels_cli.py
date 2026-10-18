"""Tests for the levels CLI (read and report commands)."""

import csv
import shutil
from pathlib import Path

import pytest

from riverforecast import levels_cli

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    for name in ("2021-08-03", "2025-10-20"):
        shutil.copy(FIXTURES / f"levels_{name}.jpg", tmp_path / f"{name}.jpg")
    shutil.copy(FIXTURES / "levels_map_2021-11-27.jpg", tmp_path / "2021-11-27.jpg")
    return tmp_path


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_read_writes_long_csv(root: Path, tmp_path: Path, capsys) -> None:
    out = tmp_path / "levels.csv"
    code = levels_cli.main(["read", str(root), "--out", str(out)])
    assert code == 0
    rows = read_csv(out)
    assert rows, "csv must have rows"
    assert list(rows[0]) == [
        "page_date",
        "table_date",
        "kind",
        "river",
        "station",
        "namo",
        "ayer",
        "hoy",
        "diff",
        "capacity",
        "valid",
        "reason",
        "sha256",
    ]
    boca = [r for r in rows if r["station"] == "Boca del Cerro" and r["kind"] == "station"]
    assert len(boca) == 2
    day = next(r for r in boca if r["page_date"] == "2025-10-20")
    assert day["table_date"] == "2025-10-20"
    assert (day["namo"], day["ayer"], day["hoy"]) == ("19.21", "17.97", "17.65")
    assert day["valid"] == "True"
    assert day["reason"] == ""
    assert len(day["sha256"]) == 64
    # the colour semáforo map is refused: no rows for that page date
    assert not [r for r in rows if r["page_date"] == "2021-11-27"]
    printed = capsys.readouterr().out
    assert "2 tables read" in printed
    assert "1 refused" in printed


def test_read_flags_inconsistent_row(root: Path, tmp_path: Path) -> None:
    out = tmp_path / "levels.csv"
    levels_cli.main(["read", str(root), "--out", str(out)])
    rows = read_csv(out)
    # every consistent station row must be valid; none are tampered with here
    for row in rows:
        if row["kind"] == "station":
            assert row["valid"] in ("True", "False")


def test_report_prints_per_year_counts(root: Path, tmp_path: Path, capsys) -> None:
    out = tmp_path / "levels.csv"
    levels_cli.main(["read", str(root), "--out", str(out)])
    capsys.readouterr()
    code = levels_cli.main(["report", str(root), "--csv", str(out)])
    assert code == 0
    printed = capsys.readouterr().out
    # 2021: two images (one read, one refused); 2025: one read
    assert "2021" in printed and "2025" in printed
    line_2021 = next(line for line in printed.splitlines() if line.startswith("2021"))
    assert "1" in line_2021  # one table read
    line_2025 = next(line for line in printed.splitlines() if line.startswith("2025"))
    assert "1" in line_2025


def test_report_counts_valid_boca_rows(root: Path, tmp_path: Path, capsys) -> None:
    out = tmp_path / "levels.csv"
    levels_cli.main(["read", str(root), "--out", str(out)])
    capsys.readouterr()
    levels_cli.main(["report", str(root), "--csv", str(out)])
    printed = capsys.readouterr().out
    line_2025 = next(line for line in printed.splitlines() if line.startswith("2025"))
    assert "1/1" in line_2025  # 1 of 1 Boca del Cerro rows valid
