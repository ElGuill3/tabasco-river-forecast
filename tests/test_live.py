"""Tests for the live IPCET stage input (L32): availability tiling, reveal rules, census."""

from __future__ import annotations

import pandas as pd
import pytest

from riverforecast import live


def bool_series(dates: list[str], values: list[bool]) -> pd.Series:
    return pd.Series(values, index=pd.DatetimeIndex(dates), dtype=bool)


def stage_series(dates: list[str], values: list[float | None]) -> pd.Series:
    return pd.Series(
        [float("nan") if v is None else v for v in values], index=pd.DatetimeIndex(dates)
    )


# --- tile_availability ------------------------------------------------------


def test_tile_availability_repeats_the_census_in_order():
    census = bool_series(
        [
            "2024-01-01",
            "2024-01-02",
            "2024-01-03",
            "2024-01-04",
            "2024-01-05",
            "2024-01-06",
            "2024-01-07",
        ],
        [True, True, True, True, True, False, False],
    )
    days = pd.date_range("2024-01-01", "2024-01-14", freq="D")
    tiled = live.tile_availability(census, days)
    assert list(tiled.to_numpy()) == [True, True, True, True, True, False, False] * 2


def test_tile_availability_aligns_each_target_day_with_the_same_weekday():
    census = bool_series(["2024-01-02", "2024-01-03", "2024-01-04"], [True, False, True])
    days = pd.DatetimeIndex(
        ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-09", "2024-01-10", "2024-01-11"]
    )
    tiled = live.tile_availability(census, days)
    assert list(tiled.to_numpy()) == [True, False, True, True, False, True]


def test_tile_availability_needs_a_census_day_for_every_weekday():
    census = bool_series(["2024-01-02", "2024-01-03"], [True, False])  # Tue, Wed only
    days = pd.DatetimeIndex(["2024-01-02", "2024-01-08"])  # Tue, Mon
    with pytest.raises(ValueError):
        live.tile_availability(census, days)


# --- revealed_values --------------------------------------------------------


def test_revealed_values_weekday_table_reveals_same_day_and_previous_day():
    stage = stage_series(["2024-01-01", "2024-01-02", "2024-01-03"], [1.0, None, 2.0])  # Mon..Wed
    tables = bool_series(["2024-01-01", "2024-01-02", "2024-01-03"], [True, True, True])
    out = live.revealed_values(stage, tables)
    row = out.loc[pd.Timestamp("2024-01-01")]
    assert row["obs_last"] == 1.0 and row["obs_day"] == pd.Timestamp("2024-01-01")
    assert row["obs_age"] == 0
    # Tuesday's table is visible but Tuesday has no observed value: the k-1 reveal applies.
    row = out.loc[pd.Timestamp("2024-01-02")]
    assert row["obs_last"] == 1.0 and row["obs_day"] == pd.Timestamp("2024-01-01")
    assert row["obs_age"] == 1
    row = out.loc[pd.Timestamp("2024-01-03")]
    assert row["obs_last"] == 2.0 and row["obs_day"] == pd.Timestamp("2024-01-03")
    assert row["obs_age"] == 0


def test_revealed_values_weekend_table_becomes_visible_on_the_next_monday():
    days = ["2024-01-06", "2024-01-07", "2024-01-08"]  # Sat, Sun, Mon
    stage = stage_series(days, [3.0, 4.0, None])
    tables = bool_series(days, [True, False, False])
    out = live.revealed_values(stage, tables)
    # Saturday and Sunday: the Saturday table is not visible yet, so nothing is
    # revealed even though Saturday's value exists (no leakage).
    for day in days[:2]:
        row = out.loc[pd.Timestamp(day)]
        assert pd.isna(row["obs_last"]) and pd.isna(row["obs_day"]) and pd.isna(row["obs_age"])
    # Monday: the Saturday table becomes visible and reveals Saturday.
    row = out.loc[pd.Timestamp(days[2])]
    assert row["obs_last"] == 3.0 and row["obs_day"] == pd.Timestamp("2024-01-06")
    assert row["obs_age"] == 2


def test_revealed_values_cuts_off_after_seven_days():
    days = [f"2024-01-{day:02d}" for day in range(1, 10)]  # Mon .. Tue next week
    stage = stage_series(days, [9.0] + [None] * 8)
    tables = bool_series(days, [True] * 9)
    out = live.revealed_values(stage, tables)
    row = out.loc[pd.Timestamp("2024-01-08")]  # age 7: still within the window
    assert row["obs_last"] == 9.0 and row["obs_day"] == pd.Timestamp("2024-01-01")
    assert row["obs_age"] == 7
    row = out.loc[pd.Timestamp("2024-01-09")]  # age 8: beyond the cutoff
    assert pd.isna(row["obs_last"]) and pd.isna(row["obs_age"])


def test_revealed_values_is_nan_when_no_table_is_ever_visible():
    days = ["2024-01-01", "2024-01-02", "2024-01-03"]
    stage = stage_series(days, [1.0, 2.0, 3.0])
    tables = bool_series(days, [False, False, False])
    out = live.revealed_values(stage, tables)
    assert out["obs_last"].isna().all()
    assert out["obs_day"].isna().all()
    assert out["obs_age"].isna().all()


# --- levels_census ----------------------------------------------------------


def test_levels_census_combines_the_flag_the_image_pattern_and_the_direct_urls(tmp_path):
    path = tmp_path / "levels_census.csv"
    path.write_text(
        "date,status,slug,images,has_levels_table\n"
        "2026-01-05,200,s,https://x/SHM%201%20%2B%20Nivel_002.jpg,1\n"
        "2026-01-06,200,s,https://x/semaforo_060126.jpeg,0\n"
        "2026-01-07,404,,,0\n"
        "2026-01-08,200,s,https://x/semaforo15oct.jpg,0\n"
        "2026-01-09,200,s,https://x/semaforo_090126.jpg,0\n"
        "2026-03-02,404,,,0\n"
    )
    census = live.levels_census(path)
    assert census.loc[pd.Timestamp("2026-01-05")]  # has_levels_table == 1
    assert census.loc[pd.Timestamp("2026-01-06")]  # semaforo_060126.jpeg matches
    assert census.loc[pd.Timestamp("2026-01-07")]  # direct-URL day added
    assert not census.loc[pd.Timestamp("2026-01-08")]  # no underscore + digits: no match
    assert census.loc[pd.Timestamp("2026-01-09")]  # .jpg also matches jpe?g
    assert census.loc[pd.Timestamp("2026-03-02")]  # direct-URL day added
    assert census.loc[pd.Timestamp("2026-04-29")] and census.loc[pd.Timestamp("2026-08-01")]
