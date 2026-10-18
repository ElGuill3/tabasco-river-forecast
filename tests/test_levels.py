"""Tests for the IPCET daily level-table reader.

Expected values were read by hand from the fixture images (2025-10-20 full
table; single Boca del Cerro rows for the other years).
"""

import datetime as dt
from pathlib import Path

import pandas as pd
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


def test_read_table_fecha_on_dark_bar_2024() -> None:
    """The 2024 layout prints FECHA in white on a black bar (L28 failure group)."""
    table = levels.read_table(FIXTURES / "levels_2024-03-17.jpg")
    assert table.table_date == dt.date(2024, 3, 17)
    row = boca(table)
    assert abs(row.namo - 19.21) <= levels.TOLERANCE
    assert (row.ayer, row.hoy) == (11.58, 11.57)


def test_read_table_2025_sd_row_keeps_fourteen_rows() -> None:
    """A station row whose semáforo cell is empty (S/D) must not shrink the
    station block below 14 rows (2025-03-05 read only 12 rows)."""
    table = levels.read_table(FIXTURES / "levels_2025-03-05.jpg")
    assert len(table.stations) == 14
    row = boca(table)
    assert (row.ayer, row.hoy) == (13.49, 13.24)


def test_read_table_2026_light_sem_cell() -> None:
    """A light-grey 'A LA BAJA' cell must not drop the first station row
    (2026-01-19 read 13 rows and misplaced the block)."""
    table = levels.read_table(FIXTURES / "levels_2026-01-19.jpg")
    assert len(table.stations) == 14
    row = boca(table)
    assert (row.ayer, row.hoy) == (13.73, 13.77)


@pytest.mark.parametrize(
    "name, ayer, hoy, diff",
    [
        ("levels_2026-02-18.jpg", 12.05, 12.05, 0.00),
        ("levels_2026-05-14.jpg", 11.41, 11.36, -0.05),
    ],
)
def test_read_table_faint_vertical_lines(name: str, ayer: float, hoy: float, diff: float) -> None:
    """Some 2026 images print the NAMO/NAMINO separators fainter, so the
    vertical lines must be searched at more than one darkness threshold."""
    row = boca(levels.read_table(FIXTURES / name))
    assert (row.ayer, row.hoy, row.diff) == (ayer, hoy, diff)


def test_read_table_2024_diff_capacity_separators() -> None:
    """The 2024 layout separates DIFERENCIA from CAPACIDAD with a fainter
    line; without it the two columns merge and the layout is refused."""
    row = boca(levels.read_table(FIXTURES / "levels_2024-10-20.jpg"))
    assert (row.ayer, row.hoy, row.diff) == (14.19, 15.03, 0.84)


@pytest.mark.parametrize(
    "name, ayer, hoy, diff",
    [
        ("levels_2022-10-10.jpg", 16.63, 16.84, 0.21),
        ("levels_2022-12-29.jpg", 15.21, 15.45, 0.24),
    ],
)
def test_read_table_merged_hoy_diff_pair(name: str, ayer: float, hoy: float, diff: float) -> None:
    """In the 2021-2022 layouts HOY and DIFERENCIA share one column without
    a separator; the concatenated OCR text (e.g. "16.840.21") must be split
    into the printed two-decimal pair, never misread as 21.0."""
    row = boca(levels.read_table(FIXTURES / name))
    assert (row.ayer, row.hoy, row.diff) == (ayer, hoy, diff)
    assert levels.row_problems(row, 19.21) == []


def test_read_table_still_refuses_colour_semaforo_map_2022() -> None:
    """The 2022 colour semáforo map (a frequent refusal in the failure list)
    must stay refused after the reader fixes."""
    with pytest.raises(levels.UnreadableTable):
        levels.read_table(FIXTURES / "levels_map_2022-03-12.jpg")


def test_parse_fecha_rejects_impossible_day() -> None:
    """An OCR slip like 31 DE FEBRERO must yield no date, not a crash."""
    assert levels._parse_fecha("FECHA: 31 DE FEBRERO DE 2024 06:00 hrs.") is None


def test_read_table_dates() -> None:
    assert levels.read_table(FIXTURES / "levels_2026-01-13.jpg").table_date == dt.date(2026, 1, 13)
    assert levels.read_table(FIXTURES / "levels_2022-06-15.jpg").table_date == dt.date(2022, 6, 15)


LATEST = dt.date(2026, 10, 17)


def test_obs_date_trusts_a_printed_date_from_another_year() -> None:
    # image file names repeat every year, so a 2023 page can serve a 2021
    # table; the printed date (year included) is the observation date
    day, flag = levels.obs_date(dt.date(2023, 11, 23), dt.date(2021, 11, 23), LATEST)
    assert (day, flag) == (dt.date(2021, 11, 23), "printed_other")


def test_obs_date_trusts_a_printed_date_after_the_page() -> None:
    # a file overwritten in a later year: still the printed date
    day, flag = levels.obs_date(dt.date(2021, 8, 3), dt.date(2022, 8, 4), LATEST)
    assert (day, flag) == (dt.date(2022, 8, 4), "printed_other")


def test_obs_date_printed_near_page_is_ok() -> None:
    day, flag = levels.obs_date(dt.date(2024, 3, 17), dt.date(2024, 3, 16), LATEST)
    assert (day, flag) == (dt.date(2024, 3, 16), "ok")


def test_obs_date_falls_back_to_page_when_unreadable() -> None:
    day, flag = levels.obs_date(dt.date(2024, 3, 17), None, LATEST)
    assert (day, flag) == (dt.date(2024, 3, 17), "no_fecha")


def test_obs_date_impossible_year_keeps_day_month_near_page() -> None:
    # a misread year outside 2021-08-01..latest: day and month near the page
    day, flag = levels.obs_date(dt.date(2024, 3, 17), dt.date(2031, 3, 18), LATEST)
    assert (day, flag) == (dt.date(2024, 3, 18), "ok")
    day, flag = levels.obs_date(dt.date(2022, 1, 2), dt.date(2019, 12, 31), LATEST)
    assert (day, flag) == (dt.date(2021, 12, 31), "ok")


def test_obs_date_flags_impossible_far_off_date() -> None:
    day, flag = levels.obs_date(dt.date(2024, 3, 17), dt.date(2019, 4, 10), LATEST)
    assert (day, flag) == (dt.date(2024, 3, 17), "far_off")


def test_obs_date_without_page_uses_table_date() -> None:
    day, flag = levels.obs_date(None, dt.date(2024, 3, 17), LATEST)
    assert (day, flag) == (dt.date(2024, 3, 17), "printed")


def _rows(*rows: tuple) -> pd.DataFrame:
    cols = ["obs_date", "obs_flag", "valid", "ayer", "hoy", "diff"]
    frame = pd.DataFrame(rows, columns=cols)
    frame["obs_date"] = pd.to_datetime(frame["obs_date"])
    return frame


def test_station_series_uses_today_and_yesterday() -> None:
    out = levels.station_series(_rows(("2025-10-20", "ok", True, 17.97, 17.65, -0.32)))
    assert out["stage_m"].to_dict() == {
        pd.Timestamp("2025-10-19"): 17.97,
        pd.Timestamp("2025-10-20"): 17.65,
    }


def test_station_series_ignores_today_when_difference_is_zero() -> None:
    # a zero difference can be yesterday's value copied before the reading
    out = levels.station_series(_rows(("2025-10-20", "ok", True, 17.97, 17.97, 0.0)))
    assert list(out.index) == [pd.Timestamp("2025-10-19")]


def test_station_series_ignores_invalid_rows() -> None:
    out = levels.station_series(_rows(("2025-10-20", "ok", False, 17.97, 17.65, -0.32)))
    assert out.empty


def test_station_series_needs_confirmation_for_undated_tables() -> None:
    lonely = _rows(("2025-10-22", "no_fecha", True, 17.10, 16.90, -0.20))
    assert levels.station_series(lonely).empty
    confirmed = _rows(
        ("2025-10-21", "ok", True, 17.40, 17.10, -0.30),
        ("2025-10-22", "no_fecha", True, 17.10, 16.90, -0.20),
    )
    out = levels.station_series(confirmed)
    assert out.loc[pd.Timestamp("2025-10-22"), "stage_m"] == 16.90


def test_station_series_drops_conflicting_days() -> None:
    out = levels.station_series(
        _rows(
            ("2025-10-20", "ok", True, 17.97, 17.65, -0.32),
            ("2025-10-21", "ok", True, 17.20, 17.00, -0.20),
        )
    )
    assert pd.Timestamp("2025-10-20") not in out.index
    assert out.loc[pd.Timestamp("2025-10-21"), "stage_m"] == 17.00


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
