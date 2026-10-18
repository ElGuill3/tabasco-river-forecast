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


def write_operational(raw, name, rows):
    folder = raw / "glofas" / "BDCTB" / "operational"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(
        "issue,valid,lead_days,member,discharge_m3s\n" + "".join(rows), encoding="utf-8"
    )


def test_operational_summary_has_the_reforecast_summary_shape(tmp_path):
    write_operational(
        tmp_path,
        "2024-01-01.csv",
        [
            "2024-01-01,2024-01-02,1,control,10.0\n",
            "2024-01-01,2024-01-02,1,perturbed_01,20.0\n",
            "2024-01-01,2024-01-04,3,control,30.0\n",
            "2024-01-01,2024-01-04,3,perturbed_01,50.0\n",
        ],
    )

    summary = data.glofas_operational_summary("BDCTB", raw=tmp_path)

    expected = ["issue", "lead_days", "members", "control", "mean", "q10", "q50", "q90", "valid"]
    assert list(summary.columns) == expected
    assert str(summary["issue"].dtype).startswith("datetime64")
    lead1 = summary[summary["lead_days"] == 1].iloc[0]
    assert lead1["members"] == 2 and lead1["control"] == 10.0 and lead1["q50"] == 15.0
    assert lead1["valid"] == pd.Timestamp("2024-01-02")
    lead3 = summary[summary["lead_days"] == 3].iloc[0]
    assert lead3["valid"] == pd.Timestamp("2024-01-04")


def test_operational_summary_keeps_only_requested_members(tmp_path):
    write_operational(
        tmp_path,
        "2024-01-01.csv",
        [
            "2024-01-01,2024-01-02,1,control,10.0\n",
            "2024-01-01,2024-01-02,1,perturbed_01,20.0\n",
            "2024-01-01,2024-01-02,1,perturbed_02,30.0\n",
        ],
    )

    summary = data.glofas_operational_summary(
        "BDCTB", raw=tmp_path, members=["control", "perturbed_01"]
    )

    assert summary["members"].tolist() == [2]
    assert summary["q50"].tolist() == [15.0]


def test_operational_summary_rejects_duplicate_rows(tmp_path):
    write_operational(tmp_path, "2024-01-01.csv", ["2024-01-01,2024-01-02,1,control,10.0\n"])
    write_operational(tmp_path, "2024-01-02.csv", ["2024-01-01,2024-01-02,1,control,11.0\n"])

    with pytest.raises(
        ValueError, match="duplicate operational rows: issue 2024-01-01 lead 1 member control"
    ):
        data.glofas_operational_summary("BDCTB", raw=tmp_path)


def test_glofas_day0_reads_the_forecast_archive_like_the_reanalysis(tmp_path):
    path = tmp_path / "glofas" / "BDCTB" / "forecast_v4.csv"
    path.parent.mkdir(parents=True)
    path.write_text(
        "date,discharge_m3s\n2023-06-01,1000.0\n2023-06-02,\n2023-06-03,1200.0\n",
        encoding="utf-8",
    )

    series = data.glofas_day0("BDCTB", raw=tmp_path)

    assert series.index.tolist() == [pd.Timestamp("2023-06-01"), pd.Timestamp("2023-06-03")]
    assert series.tolist() == [1000.0, 1200.0]


def test_glofas_training_past_is_the_reanalysis_shifted_one_day(tmp_path):
    # The day-0 archive labels a daily mean one day later than the reanalysis
    # (D3, L39): past[d] must be reanalysis[d-1] so that training labels match
    # the day-0 series used in operation.
    path = tmp_path / "glofas" / "BDCTB" / "consolidated_v4.csv"
    path.parent.mkdir(parents=True)
    path.write_text(
        "date,discharge_m3s\n2020-01-01,100.0\n2020-01-02,200.0\n2020-01-04,400.0\n",
        encoding="utf-8",
    )

    past = data.glofas_training_past("BDCTB", raw=tmp_path)

    assert past.index.tolist() == [
        pd.Timestamp("2020-01-02"),
        pd.Timestamp("2020-01-03"),
        pd.Timestamp("2020-01-05"),
    ]
    assert past.tolist() == [100.0, 200.0, 400.0]
