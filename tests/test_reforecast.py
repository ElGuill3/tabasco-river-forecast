import json

import pandas as pd
import pytest

from riverforecast import reforecast


def record(issue, valid, member, value):
    return {
        "issue_time": f"{issue}T00:00:00+00:00",
        "valid_time": f"{valid}T00:00:00+00:00",
        "ensemble_member": member,
        "value": value,
        "unit": "m3 s-1",
        "variable": "modeled_discharge",
        "system_version": "version_4_0",
    }


def write_issue(folder, issue, records, lat=17.425, lon360=268.525):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{issue.replace('-', '')}.json").write_text(
        json.dumps(
            {
                "product": "glofas_reforecast",
                "cell_selection_receipt": {"latitude": lat, "longitude_0_360": lon360},
                "records": records,
            }
        )
    )


def test_reads_issues_into_a_tidy_table(tmp_path):
    write_issue(
        tmp_path,
        "2004-01-01",
        [
            record("2004-01-01", "2004-01-02", "control", 1702.5),
            record("2004-01-01", "2004-01-04", "perturbed_01", 1650.0),
            record("2004-01-01", "2004-01-08", "control", 1500.25),
        ],
    )

    table = reforecast.read_normalized(tmp_path)

    assert list(table.columns) == ["issue", "valid", "lead_days", "member", "discharge_m3s"]
    assert table.to_dict("records") == [
        {
            "issue": "2004-01-01",
            "valid": "2004-01-02",
            "lead_days": 1,
            "member": "control",
            "discharge_m3s": 1702.5,
        },
        {
            "issue": "2004-01-01",
            "valid": "2004-01-04",
            "lead_days": 3,
            "member": "perturbed_01",
            "discharge_m3s": 1650.0,
        },
        {
            "issue": "2004-01-01",
            "valid": "2004-01-08",
            "lead_days": 7,
            "member": "control",
            "discharge_m3s": 1500.25,
        },
    ]


def test_rejects_duplicate_records(tmp_path):
    write_issue(
        tmp_path,
        "2004-01-01",
        [record("2004-01-01", "2004-01-02", "control", 1.0)] * 2,
    )

    with pytest.raises(
        reforecast.ReforecastError, match="duplicate record 2004-01-01 2004-01-02 control"
    ):
        reforecast.read_normalized(tmp_path)


def test_rejects_issues_from_another_grid_cell(tmp_path):
    write_issue(tmp_path, "2004-01-01", [record("2004-01-01", "2004-01-02", "control", 1.0)])
    write_issue(
        tmp_path, "2004-01-04", [record("2004-01-04", "2004-01-05", "control", 1.0)], lat=17.475
    )

    with pytest.raises(reforecast.ReforecastError, match="20040104.json: cell 17.475,268.525"):
        reforecast.read_normalized(tmp_path)


def test_rejects_non_finite_or_negative_discharge(tmp_path):
    write_issue(tmp_path, "2004-01-01", [record("2004-01-01", "2004-01-02", "control", -1.0)])

    with pytest.raises(reforecast.ReforecastError, match="invalid discharge -1.0"):
        reforecast.read_normalized(tmp_path)


def test_ensemble_summary_by_issue_and_lead():
    table = pd.DataFrame(
        {
            "issue": ["2004-01-01"] * 4,
            "valid": ["2004-01-02"] * 4,
            "lead_days": [1] * 4,
            "member": ["control", "perturbed_01", "perturbed_02", "perturbed_03"],
            "discharge_m3s": [10.0, 20.0, 30.0, 40.0],
        }
    )

    summary = reforecast.ensemble_summary(table)

    assert summary.to_dict("records") == [
        {
            "issue": "2004-01-01",
            "lead_days": 1,
            "members": 4,
            "control": 10.0,
            "mean": 25.0,
            "q10": 13.0,
            "q50": 25.0,
            "q90": 37.0,
        }
    ]


def test_cli_imports_several_folders_into_one_table(tmp_path, capsys):
    from riverforecast import reforecast_cli

    write_issue(tmp_path / "a", "2004-01-01", [record("2004-01-01", "2004-01-02", "control", 5.0)])
    write_issue(tmp_path / "b", "2020-09-23", [record("2020-09-23", "2020-09-24", "control", 7.0)])
    root = tmp_path / "raw"

    code = reforecast_cli.main(
        ["--root", str(root), "import", "BDCTB", str(tmp_path / "a"), str(tmp_path / "b")]
    )

    assert code == 0
    table = pd.read_csv(root / "glofas" / "BDCTB" / "reforecast_v4.csv")
    assert table["issue"].tolist() == ["2004-01-01", "2020-09-23"]
    assert table["discharge_m3s"].tolist() == [5.0, 7.0]
    assert capsys.readouterr().out == (
        "BDCTB: 2 records, 2 issues (2004-01-01 to 2020-09-23), leads [1] days, 1 members\n"
    )


def test_cli_rejects_folders_from_different_cells(tmp_path, capsys):
    from riverforecast import reforecast_cli

    write_issue(tmp_path / "a", "2004-01-01", [record("2004-01-01", "2004-01-02", "control", 5.0)])
    write_issue(
        tmp_path / "b",
        "2020-09-23",
        [record("2020-09-23", "2020-09-24", "control", 7.0)],
        lat=17.475,
    )
    root = tmp_path / "raw"

    code = reforecast_cli.main(
        ["--root", str(root), "import", "BDCTB", str(tmp_path / "a"), str(tmp_path / "b")]
    )

    assert code == 1
    assert not root.exists()
    assert capsys.readouterr().err == (
        "BDCTB: folders come from different cells: 17.425,268.525 and 17.475,268.525\n"
    )
