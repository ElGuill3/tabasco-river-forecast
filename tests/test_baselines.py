import numpy as np
import pandas as pd
import pytest

from riverforecast import baselines


def seasonal_levels(years=range(2000, 2010), seed=0):
    days = pd.date_range(f"{years[0]}-01-01", f"{years[-1]}-12-31", freq="D")
    rng = np.random.default_rng(seed)
    season = 14 + 3 * np.sin(2 * np.pi * (days.dayofyear - 200) / 365.25)
    return pd.Series(season + rng.normal(0, 0.5, len(days)), index=days)


def test_climatology_follows_the_season_and_brackets_it():
    levels = seasonal_levels()
    climatology = baselines.Climatology.fit(levels)

    # Seasonal trough, steepest rise and peak of the synthetic stage.
    days = pd.DatetimeIndex(["2030-04-19", "2030-07-19", "2030-10-18"])
    q10, q50, q90 = climatology.predict(days)

    expected = 14 + 3 * np.sin(2 * np.pi * (days.dayofyear - 200) / 365.25)
    assert q50 == pytest.approx(expected, abs=0.3)
    assert np.all(q10 < q50) and np.all(q50 < q90)
    # Where the season is flat, the central 80% of 0.5 m noise spans about
    # 2 x 1.28 x 0.5 m; on the steep rise the window also spans the trend.
    width = q90 - q10
    assert width[[0, 2]] == pytest.approx([1.28, 1.28], abs=0.2)
    assert width[1] > width[0]


def test_climatology_window_wraps_around_the_new_year():
    days = pd.date_range("2000-01-01", "2009-12-31", freq="D")
    # High in late December and early January only.
    levels = pd.Series(
        np.where((days.dayofyear <= 10) | (days.dayofyear >= 356), 20.0, 10.0), index=days
    )

    q10, q50, q90 = baselines.Climatology.fit(levels, window_days=5).predict(
        pd.DatetimeIndex(["2030-01-01", "2030-12-31"])
    )

    assert q50.tolist() == [20.0, 20.0]


def test_climatology_ignores_missing_days():
    levels = seasonal_levels()
    levels.iloc[::3] = np.nan

    q10, q50, q90 = baselines.Climatology.fit(levels).predict(pd.DatetimeIndex(["2030-06-01"]))

    assert np.isfinite([q10[0], q50[0], q90[0]]).all()


def test_rating_maps_discharge_to_stage_monotonically_with_residual_spread():
    rng = np.random.default_rng(1)
    discharge = pd.Series(rng.uniform(500, 8000, 3000))
    stage = 8 + 1.5 * np.log(discharge) + rng.normal(0, 0.4, 3000)

    rating = baselines.Rating.fit(discharge, pd.Series(stage))
    q10, q50, q90 = rating.predict(np.array([1000.0, 4000.0, 7000.0]))

    assert np.all(np.diff(q50) > 0)
    assert q50 == pytest.approx(8 + 1.5 * np.log([1000, 4000, 7000]), abs=0.15)
    assert q50 - q10 == pytest.approx(np.full(3, 1.28 * 0.4), abs=0.1)
    assert q90 - q50 == pytest.approx(np.full(3, 1.28 * 0.4), abs=0.1)


def test_rating_clips_outside_the_fitted_discharge_range():
    discharge = pd.Series([1000.0, 2000.0, 3000.0, 4000.0])
    stage = pd.Series([10.0, 11.0, 12.0, 13.0])

    rating = baselines.Rating.fit(discharge, stage)
    _, q50, _ = rating.predict(np.array([10.0, 99999.0]))

    assert q50.tolist() == [10.0, 13.0]
