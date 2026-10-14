import pandas as pd
import pytest

from riverforecast import data


def test_imerg_rain_is_sorted_by_date_and_rejects_duplicates(tmp_path):
    folder = tmp_path / "imerg" / "BDCTB"
    folder.mkdir(parents=True)
    store = folder / "daily.csv"
    store.write_text(
        "date,basin_mean_mm,file\n2020-11-01,3.0,b\n1998-01-01,1.0,a\n1998-01-02,,a2\n"
    )

    rain = data.imerg_basin_rain("BDCTB", raw=tmp_path)

    assert list(rain.index) == list(pd.to_datetime(["1998-01-01", "1998-01-02", "2020-11-01"]))
    assert rain.iloc[0] == 1.0 and pd.isna(rain.iloc[1])

    store.write_text("date,basin_mean_mm,file\n1998-01-01,1.0,a\n1998-01-01,2.0,a\n")
    with pytest.raises(ValueError, match="duplicate dates in .*daily.csv: 1998-01-01"):
        data.imerg_basin_rain("BDCTB", raw=tmp_path)
