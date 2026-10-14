import json
import math

import numpy as np
import pandas as pd
import pytest

from riverforecast import glofas

DATES = ["2020-01-01", "2020-01-02", "2020-01-03"]


def response(*series):
    """Open-Meteo answer for one location per series (a dict when there is one)."""
    locations = [
        {
            "latitude": lat,
            "longitude": lon,
            "daily": {"time": DATES, "river_discharge": values},
        }
        for lat, lon, values in series
    ]
    return locations[0] if len(locations) == 1 else locations


def test_fetch_builds_the_request_and_returns_one_column_per_cell():
    requests = []

    def fake_get(url):
        requests.append(url)
        return response((17.425, -91.525, [1.0, None, 3.0]), (17.475, -91.525, [4.0, 5.0, 6.0]))

    table = glofas.fetch_discharge(
        [(17.425, -91.525), (17.475, -91.525)],
        "2020-01-01",
        "2020-01-03",
        model="consolidated_v4",
        get_json=fake_get,
    )

    assert requests == [
        "https://flood-api.open-meteo.com/v1/flood?latitude=17.425,17.475"
        "&longitude=-91.525,-91.525&daily=river_discharge"
        "&start_date=2020-01-01&end_date=2020-01-03&models=consolidated_v4"
    ]
    assert list(table.columns) == ["17.425,-91.525", "17.475,-91.525"]
    assert list(table.index) == list(pd.to_datetime(DATES))
    assert math.isnan(table.iloc[1, 0])
    assert table.iloc[2, 1] == 6.0


def test_fetch_accepts_a_single_location_answer():
    table = glofas.fetch_discharge(
        [(17.425, -91.525)],
        "2020-01-01",
        "2020-01-03",
        model="forecast_v4",
        get_json=lambda url: response((17.425, -91.525, [1.0, 2.0, 3.0])),
    )

    assert list(table.columns) == ["17.425,-91.525"]
    assert table.iloc[:, 0].tolist() == [1.0, 2.0, 3.0]


def test_selects_the_cell_that_follows_the_observed_discharge():
    days = pd.date_range("2000-01-01", periods=400, freq="D")
    rng = np.random.default_rng(0)
    observed = pd.Series(2000 + 800 * np.sin(np.arange(400) / 30), index=days)
    river = observed * 0.95 + rng.normal(0, 50, 400)
    tributary = observed / 400 + rng.normal(0, 0.01, 400)  # same shape, tiny flow
    neighbour = pd.Series(2000 + rng.normal(0, 500, 400), index=days)  # right size, no signal
    cells = pd.DataFrame(
        {"17.425,-91.475": tributary, "17.425,-91.525": river, "17.475,-91.475": neighbour},
        index=days,
    )

    choice = glofas.select_river_cell(cells, observed, station=(17.43, -91.492))

    assert choice.cell == "17.425,-91.525"
    assert choice.mean_ratio == pytest.approx(0.95, abs=0.01)
    assert choice.correlation > 0.95
    assert choice.lag_days == 0
    assert choice.days == 400


def test_selection_allows_the_model_to_lead_the_observations():
    days = pd.date_range("2000-01-01", periods=400, freq="D")
    rng = np.random.default_rng(1)
    observed = pd.Series(2000 + 800 * np.sin(np.arange(400) / 15), index=days)
    # The station cell peaks two days early; a cell further down peaks on time
    # but carries more noise.
    station_cell = observed.shift(-2) + rng.normal(0, 20, 400)
    downstream = observed + rng.normal(0, 150, 400)
    cells = pd.DataFrame({"17.425,-91.525": station_cell, "17.525,-91.475": downstream})

    choice = glofas.select_river_cell(cells, observed, station=(17.43, -91.492))

    assert choice.cell == "17.425,-91.525"
    assert choice.lag_days == 2


def test_equivalent_cells_resolve_to_the_one_nearest_the_station():
    days = pd.date_range("2000-01-01", periods=400, freq="D")
    rng = np.random.default_rng(2)
    observed = pd.Series(2000 + 800 * np.sin(np.arange(400) / 15), index=days)
    near = observed + rng.normal(0, 30, 400)
    far = observed + rng.normal(0, 29, 400)  # marginally better fit, 10 km away
    cells = pd.DataFrame({"17.425,-91.525": near, "17.525,-91.475": far})
    assert far.corr(observed) > near.corr(observed)

    choice = glofas.select_river_cell(cells, observed, station=(17.43, -91.492))

    assert choice.cell == "17.425,-91.525"


def test_selection_fails_when_no_cell_has_the_observed_size():
    days = pd.date_range("2000-01-01", periods=50, freq="D")
    observed = pd.Series(np.linspace(1000, 3000, 50), index=days)
    cells = pd.DataFrame({"17.425,-91.475": observed / 100}, index=days)

    with pytest.raises(glofas.CellSelectionError, match="no cell has a mean discharge"):
        glofas.select_river_cell(cells, observed, station=(17.43, -91.492))


def test_choice_serializes_to_json_with_the_selection_rule():
    choice = glofas.CellChoice(
        cell="17.425,-91.525", correlation=0.8344, lag_days=2, mean_ratio=0.9612, days=9868
    )

    assert json.loads(choice.to_json()) == {
        "cell": "17.425,-91.525",
        "latitude": 17.425,
        "longitude": -91.525,
        "correlation": 0.8344,
        "lag_days": 2,
        "mean_ratio": 0.9612,
        "days": 9868,
        "rule": "among cells whose mean is 0.5-2 times the observed mean, the cell nearest "
        "the station whose daily correlation with observed discharge (allowing it to lead "
        "by 0-5 days) is within 0.005 of the best",
    }


def test_get_json_waits_and_retries_when_rate_limited():
    calls, waits = [], []

    def opener(url):
        calls.append(url)
        if len(calls) < 3:
            raise glofas.RateLimited()
        return {"ok": True}

    assert glofas.get_json_with_retry("u", open_json=opener, sleep=waits.append) == {"ok": True}
    assert calls == ["u", "u", "u"]
    assert waits == [60, 60]


def test_get_json_gives_up_after_the_last_retry():
    waits = []

    def opener(url):
        raise glofas.RateLimited()

    with pytest.raises(glofas.RateLimited):
        glofas.get_json_with_retry("u", open_json=opener, sleep=waits.append, retries=2)
    assert waits == [60, 60]
