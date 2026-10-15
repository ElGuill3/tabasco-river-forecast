import datetime as dt
import io
import zipfile

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from riverforecast import cems

LATS = [17.475, 17.425, 17.375]
LONS = [268.475, 268.525, 268.575]
PERIODS = np.array([1, 3, 7], dtype="timedelta64[D]").astype("timedelta64[ns]")


def netcdf(values, issues, members=None):
    """One CEMS NetCDF: dis24 over (number?, forecast_period, forecast_reference_time, lat, lon)."""
    dims = ["forecast_period", "forecast_reference_time", "latitude", "longitude"]
    coords = {
        "forecast_period": PERIODS,
        "forecast_reference_time": pd.to_datetime(issues),
        "latitude": LATS,
        "longitude": LONS,
    }
    if members is not None:
        dims = ["number", *dims]
        coords["number"] = members
    ds = xr.Dataset({"dis24": (dims, values)}, coords=coords)
    return ds.to_netcdf(engine="h5netcdf")


def archive(issues=("2026-10-01",)):
    n = len(issues)
    control = np.zeros((3, n, 3, 3))
    control[:, :, 1, 1] = np.array([100.0, 300.0, 700.0])[:, None]
    perturbed = np.zeros((2, 3, n, 3, 3))
    perturbed[0, :, :, 1, 1] = np.array([110.0, 310.0, 710.0])[:, None]
    perturbed[1, :, :, 1, 1] = np.array([90.0, 290.0, 690.0])[:, None]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zipped:
        zipped.writestr("data_0.nc", netcdf(control, issues))
        zipped.writestr("data_1.nc", netcdf(perturbed, issues, members=[1, 2]))
    return buffer.getvalue()


def test_month_request_lists_every_day_and_lead():
    request = cems.forecast_request(2026, 2, cems.CELL_AREA)

    assert request["day"] == [f"{d:02d}" for d in range(1, 29)]
    assert request["year"] == ["2026"] and request["month"] == ["02"]
    assert request["leadtime_hour"] == ["24", "72", "168"]
    assert request["product_type"] == ["control_forecast", "ensemble_perturbed_forecasts"]
    assert request["area"] == [17.5, -91.55, 17.35, -91.4]


def test_reads_the_river_cell_into_the_reforecast_table():
    table = cems.read_archive(archive(), cell=(17.425, 268.525))

    assert list(table.columns) == ["issue", "valid", "lead_days", "member", "discharge_m3s"]
    assert table.to_dict("records")[:3] == [
        {
            "issue": "2026-10-01",
            "valid": "2026-10-02",
            "lead_days": 1,
            "member": "control",
            "discharge_m3s": 100.0,
        },
        {
            "issue": "2026-10-01",
            "valid": "2026-10-02",
            "lead_days": 1,
            "member": "perturbed_01",
            "discharge_m3s": 110.0,
        },
        {
            "issue": "2026-10-01",
            "valid": "2026-10-02",
            "lead_days": 1,
            "member": "perturbed_02",
            "discharge_m3s": 90.0,
        },
    ]
    assert len(table) == 9
    assert table[table.lead_days == 7].valid.unique().tolist() == ["2026-10-08"]


def test_reads_several_issue_days():
    table = cems.read_archive(archive(("2026-10-01", "2026-10-02")), cell=(17.425, 268.525))

    assert table.issue.unique().tolist() == ["2026-10-01", "2026-10-02"]
    assert len(table) == 18


def test_rejects_an_archive_without_the_cell():
    with pytest.raises(cems.CemsError, match="cell 17.6,268.525 not in the archive"):
        cems.read_archive(archive(), cell=(17.6, 268.525))


def test_rejects_missing_discharge():
    buffer = io.BytesIO()
    values = np.full((3, 1, 3, 3), np.nan)
    with zipfile.ZipFile(buffer, "w") as zipped:
        zipped.writestr("data_0.nc", netcdf(values, ["2026-10-01"]))

    with pytest.raises(cems.CemsError, match="missing discharge for 2026-10-01 lead 1 control"):
        cems.read_archive(buffer.getvalue(), cell=(17.425, 268.525))


def test_months_between_two_dates():
    assert list(cems.months(dt.date(2025, 11, 20), dt.date(2026, 2, 3))) == [
        (2025, 11),
        (2025, 12),
        (2026, 1),
        (2026, 2),
    ]
