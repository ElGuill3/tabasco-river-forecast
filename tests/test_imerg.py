import datetime as dt

import numpy as np
import pandas as pd
import pytest

from riverforecast import imerg

LISTING = """
<a href="3B-DAY-E.MS.MRG.3IMERG.20261012-S000000-E235959.V07C.nc4">x</a>
<a href="3B-DAY-E.MS.MRG.3IMERG.20261012-S000000-E235959.V07C.nc4.xml">x</a>
<a href="3B-DAY-E.MS.MRG.3IMERG.20261011-S000000-E235959.V07B.nc4">x</a>
"""


def test_reads_daily_file_names_from_a_month_listing():
    assert imerg.files_in_listing(LISTING) == {
        dt.date(2026, 10, 11): "3B-DAY-E.MS.MRG.3IMERG.20261011-S000000-E235959.V07B.nc4",
        dt.date(2026, 10, 12): "3B-DAY-E.MS.MRG.3IMERG.20261012-S000000-E235959.V07C.nc4",
    }


def test_grid_window_covers_the_bounding_box():
    # IMERG cell centres are -179.95 + 0.1 i (lon) and -89.95 + 0.1 j (lat).
    window = imerg.GridWindow.covering(west=-92.55, south=14.9, east=-89.13, north=17.63)

    assert window.lon_range == (874, 909)
    assert window.lat_range == (1048, 1076)
    assert window.lons[0] == pytest.approx(-92.55)
    assert window.lons[-1] == pytest.approx(-89.05)
    assert window.lats[0] == pytest.approx(14.85)
    assert window.lats[-1] == pytest.approx(17.65)


def test_opendap_url_requests_only_the_window():
    window = imerg.GridWindow(lon_range=(874, 909), lat_range=(1048, 1076))

    url = imerg.subset_url(
        dt.date(2020, 11, 1), "3B-DAY-E.MS.MRG.3IMERG.20201101-S000000-E235959.V07B.nc4", window
    )

    assert url == (
        "https://gpm1.gesdisc.eosdis.nasa.gov/opendap/GPM_L3/GPM_3IMERGDE.07/2020/11/"
        "3B-DAY-E.MS.MRG.3IMERG.20201101-S000000-E235959.V07B.nc4.nc4"
        "?precipitation%5B0:0%5D%5B874:909%5D%5B1048:1076%5D"
    )


def test_basin_mean_weights_only_cells_inside_the_basin():
    # precipitation is (lon, lat); the mask marks cells whose centre is in the basin.
    precipitation = np.array([[1.0, 2.0], [3.0, 40.0]])
    mask = np.array([[True, True], [True, False]])

    assert imerg.basin_mean(precipitation, mask) == pytest.approx(2.0)


def test_basin_mean_ignores_missing_cells_but_fails_when_all_are_missing():
    mask = np.array([[True, True]])
    assert imerg.basin_mean(np.array([[np.nan, 4.0]]), mask) == pytest.approx(4.0)
    assert np.isnan(imerg.basin_mean(np.array([[np.nan, np.nan]]), mask))


def test_update_fetches_only_missing_days_and_appends_them(tmp_path):
    store = tmp_path / "daily.csv"
    pd.DataFrame({"date": ["2020-01-01"], "basin_mean_mm": [1.5], "file": ["a.nc4"]}).to_csv(
        store, index=False
    )
    fetched = []

    def fetch_day(day, name):
        fetched.append(day)
        return 10.0 + day.day

    listing = {
        dt.date(2020, 1, 1): "a.nc4",
        dt.date(2020, 1, 2): "b.nc4",
        dt.date(2020, 1, 3): "c.nc4",
    }
    added = imerg.update_store(store, listing, fetch_day)

    assert added == 2
    assert fetched == [dt.date(2020, 1, 2), dt.date(2020, 1, 3)]
    table = pd.read_csv(store)
    assert table["date"].tolist() == ["2020-01-01", "2020-01-02", "2020-01-03"]
    assert table["basin_mean_mm"].tolist() == [1.5, 12.0, 13.0]
    assert table["file"].tolist() == ["a.nc4", "b.nc4", "c.nc4"]
