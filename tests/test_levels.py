"""Tests for the IPCET daily level-table reader.

Expected values were read by hand from the fixture images (2025-10-20 full
table; single Boca del Cerro rows for the other years).
"""

import datetime as dt
from pathlib import Path

import pytest

from riverforecast import levels

FIXTURES = Path(__file__).parent / "fixtures"


def boca(table: levels.LevelTable) -> levels.StationRow:
    return next(r for r in table.stations if r.station == "Boca del Cerro")


def test_read_table_station_rows_2025() -> None:
    table = levels.read_table(FIXTURES / "levels_2025-10-20.jpg")
    assert table.page_date == dt.date(2025, 10, 20)
    assert table.table_date == dt.date(2025, 10, 20)
    # (river, station, namo, ayer, hoy, diff, capacity); values read by hand
    # from the fixture image. namino is best-effort OCR (nothing validates it)
    # and is not pinned here.
    expected = [
        ("Samaria", "Samaria", 14.90, 11.58, 11.58, 0.00, 3.32),
        ("Carrizal", "González", 8.77, 3.85, 4.00, 0.15, 4.77),
        ("Oxolotán", "Oxolotán", 39.53, 34.50, 35.56, 1.06, 3.97),
        ("Tacotalpa", "Tapijulapa", 24.63, 16.42, 16.40, -0.02, 8.23),
        ("Puyacatengo", "Puyacatengo", 29.65, 25.21, 25.24, 0.03, 4.41),
        ("Teapa", "Teapa", 37.71, 33.82, 33.85, 0.03, 3.86),
        ("De la Sierra", "Pueblo Nuevo", 7.49, 5.02, 4.99, -0.03, 2.50),
        ("Pichucalco", "San Joaquín", 23.12, 20.49, 20.48, -0.01, 2.64),
        ("Grijalva", "Gaviotas", 5.42, 3.71, 3.54, -0.17, 1.88),
        ("Grijalva", "Porvenir", 4.74, 2.72, 2.62, -0.10, 2.12),
        ("Tulijá", "Salto de Agua", 10.99, 4.79, 6.13, 1.34, 4.86),
        ("Puxcatán", "Macuspana", 9.85, 4.69, 4.37, -0.32, 5.48),
        ("Usumacinta", "Boca del Cerro", 19.21, 17.97, 17.65, -0.32, 1.56),
        ("San Pedro", "San Pedro", 9.01, 8.82, 8.83, 0.01, 0.18),
    ]
    assert len(table.stations) == len(expected)
    for row, exp in zip(table.stations, expected, strict=True):
        got = (row.river, row.station, row.namo, row.ayer, row.hoy, row.diff, row.capacity)
        assert got == exp, f"{row.station}: {got} != {exp}"


def test_read_table_dams_2025() -> None:
    table = levels.read_table(FIXTURES / "levels_2025-10-20.jpg")
    expected = [
        ("Angostura", 533.00, 528.65, 528.70, 0.05, 4.30),
        ("Chicoasén", 392.00, 390.85, 390.95, 0.10, 1.05),
        ("Malpaso", 182.50, 170.57, 170.64, 0.07, 11.86),
        ("Peñitas", 87.40, 85.58, 85.77, 0.19, 1.63),
    ]
    assert len(table.dams) == 4
    for row, exp in zip(table.dams, expected, strict=True):
        got = (row.name, row.namo, row.ayer, row.hoy, row.diff, row.capacity)
        assert got == exp, f"{row.name}: {got} != {exp}"


@pytest.mark.parametrize(
    "name, ayer, hoy",
    [
        ("levels_2021-08-03.jpg", 15.87, 16.01),
        ("levels_2022-06-15.jpg", 13.23, 13.37),
        ("levels_2026-01-13.jpg", 14.21, 14.33),
    ],
)
def test_read_table_boca_del_cerro_other_years(name: str, ayer: float, hoy: float) -> None:
    table = levels.read_table(FIXTURES / name)
    row = boca(table)
    # NAMO may carry a last-digit OCR slip within the validation tolerance;
    # ayer and hoy must be exact.
    assert abs(row.namo - 19.21) <= levels.TOLERANCE
    assert (row.ayer, row.hoy) == (ayer, hoy)


def test_read_table_dates() -> None:
    assert levels.read_table(FIXTURES / "levels_2026-01-13.jpg").table_date == dt.date(2026, 1, 13)
    assert levels.read_table(FIXTURES / "levels_2022-06-15.jpg").table_date == dt.date(2022, 6, 15)


def test_read_table_refuses_colour_semaforo_map() -> None:
    with pytest.raises(levels.UnreadableTable):
        levels.read_table(FIXTURES / "levels_map_2021-11-27.jpg")


def test_row_problems_empty_when_consistent() -> None:
    row = levels.StationRow("Usumacinta", "Boca del Cerro", 19.21, 11.0, 17.97, 17.65, -0.32, 1.56)
    assert levels.row_problems(row, 19.21) == []


def test_row_problems_diff_mismatch() -> None:
    row = levels.StationRow("Usumacinta", "Boca del Cerro", 19.21, 11.0, 17.97, 17.65, -0.30, 1.56)
    assert "diff_mismatch" in levels.row_problems(row, 19.21)


def test_row_problems_capacity_mismatch() -> None:
    row = levels.StationRow("Usumacinta", "Boca del Cerro", 19.21, 11.0, 17.97, 17.65, -0.32, 1.60)
    assert "capacity_mismatch" in levels.row_problems(row, 19.21)


def test_row_problems_namo_mismatch_against_modal() -> None:
    row = levels.StationRow("Usumacinta", "Boca del Cerro", 19.91, 11.0, 17.97, 17.65, -0.32, 1.56)
    assert "namo_mismatch" in levels.row_problems(row, 19.21)


def test_row_problems_unreadable_cell() -> None:
    row = levels.StationRow("Usumacinta", "Boca del Cerro", 19.21, 11.0, None, 17.65, -0.32, 1.56)
    problems = levels.row_problems(row, 19.21)
    assert any(p.startswith("unreadable") for p in problems)


def test_row_problems_tolerates_rounding_within_tolerance() -> None:
    row = levels.StationRow("Usumacinta", "Boca del Cerro", 19.21, 11.0, 17.97, 17.65, -0.32, 1.57)
    assert levels.row_problems(row, 19.21) == []


def test_modal_namos_picks_most_common_value() -> None:
    tables = [
        levels.LevelTable(
            page_date=None,
            table_date=None,
            stations=(
                levels.StationRow("Usumacinta", "Boca del Cerro", 19.21, 11.0, 1.0, 1.0, 0.0, 10.0),
                levels.StationRow("Teapa", "Teapa", 37.71, 33.5, 1.0, 1.0, 0.0, 4.0),
            ),
            dams=(),
        ),
        levels.LevelTable(
            page_date=None,
            table_date=None,
            stations=(
                levels.StationRow("Usumacinta", "Boca del Cerro", 19.21, 11.0, 1.0, 1.0, 0.0, 10.0),
                levels.StationRow("Teapa", "Teapa", 37.71, 33.5, 1.0, 1.0, 0.0, 4.0),
            ),
            dams=(),
        ),
        levels.LevelTable(
            page_date=None,
            table_date=None,
            stations=(
                levels.StationRow("Usumacinta", "Boca del Cerro", 18.21, 11.0, 1.0, 1.0, 0.0, 10.0),
            ),
            dams=(),
        ),
    ]
    modes = levels.modal_namos(tables)
    assert modes[("station", "Boca del Cerro")] == 19.21
    assert modes[("station", "Teapa")] == 37.71
