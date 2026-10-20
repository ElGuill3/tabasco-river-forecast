import json

import pytest

from riverforecast import verify

LEVELS_HEADER = "table_date,code,river,station,namo,ayer,hoy,diff,semaforo,valid\n"


def forecast(issue, mode="live", stage=16.49, leads=None):
    leads = leads or {
        "h1": ("2026-10-20", 17.07, 17.76, 17.98),
        "h3": ("2026-10-22", 17.43, 18.76, 19.63),
        "h7": ("2026-10-26", 16.29, 17.63, 19.08),
    }
    return {
        "issue_time": f"{issue}T11:00:00-06:00",
        "mode": mode,
        "live_input": None if stage is None else {"date": issue, "stage_m": stage},
        "forecasts": {
            lead: {"target_date": target, "q10": q10, "q50": q50, "q90": q90, "namo_m": 19.21}
            for lead, (target, q10, q50, q90) in leads.items()
        },
    }


def write_history(folder, *documents):
    folder.mkdir(parents=True, exist_ok=True)
    for document in documents:
        name = document["issue_time"][:10]
        (folder / f"{name}.json").write_text(json.dumps(document))
    return folder


def write_levels(path, rows):
    path.write_text(
        LEVELS_HEADER
        + "".join(
            f"{date},{code},Usumacinta,Boca del Cerro,19.21,0,{hoy},0,,{valid}\n"
            for date, code, hoy, valid in rows
        )
    )
    return path


def test_scores_each_lead_against_the_official_stage_and_keeps_pending_leads(tmp_path):
    history = write_history(tmp_path / "history", forecast("2026-10-19"))
    levels = write_levels(
        tmp_path / "levels.csv",
        [
            ("2026-10-19", "BDCTB", 16.49, True),
            ("2026-10-20", "BDCTB", 17.02, True),
            ("2026-10-20", "SPTTB", 9.99, True),
        ],
    )
    rows = verify.match(verify.load_history(history), verify.load_observed(levels, "BDCTB"))
    assert list(rows["lead"]) == [1, 3, 7]
    scored = rows.dropna(subset=["observed"])
    assert len(scored) == 1
    first = scored.iloc[0]
    assert first["issue_date"] == "2026-10-19"
    assert first["target_date"] == "2026-10-20"
    assert first["observed"] == 17.02
    assert first["error"] == pytest.approx(0.74)
    assert not first["inside"]
    assert first["persistence_error"] == pytest.approx(16.49 - 17.02)


def test_band_coverage_is_inclusive_and_last_valid_row_wins(tmp_path):
    leads = {"h1": ("2026-10-20", 17.0, 17.5, 18.0)}
    history = write_history(tmp_path / "history", forecast("2026-10-19", leads=leads))
    levels = write_levels(
        tmp_path / "levels.csv",
        [
            ("2026-10-20", "BDCTB", 16.0, True),
            ("2026-10-20", "BDCTB", 18.0, True),
            ("2026-10-20", "BDCTB", 15.0, False),
        ],
    )
    rows = verify.match(verify.load_history(history), verify.load_observed(levels, "BDCTB"))
    assert rows.iloc[0]["observed"] == 18.0
    assert bool(rows.iloc[0]["inside"])


def test_persistence_falls_back_to_the_issue_table_without_live_input(tmp_path):
    history = write_history(
        tmp_path / "history", forecast("2026-10-19", mode="glofas_only", stage=None)
    )
    levels = write_levels(
        tmp_path / "levels.csv",
        [("2026-10-19", "BDCTB", 16.4, True), ("2026-10-20", "BDCTB", 17.0, True)],
    )
    rows = verify.match(verify.load_history(history), verify.load_observed(levels, "BDCTB"))
    assert rows.iloc[0]["mode"] == "glofas_only"
    assert rows.iloc[0]["persistence_error"] == pytest.approx(-0.6)


def test_summary_groups_by_mode_and_lead(tmp_path):
    history = write_history(
        tmp_path / "history",
        forecast("2026-10-19", stage=16.5, leads={"h1": ("2026-10-20", 17.0, 17.5, 18.0)}),
        forecast("2026-10-20", stage=17.0, leads={"h1": ("2026-10-21", 17.0, 17.4, 17.8)}),
        forecast("2026-10-21", stage=17.2, leads={"h1": ("2026-10-22", 17.0, 17.4, 17.8)}),
    )
    levels = write_levels(
        tmp_path / "levels.csv",
        [
            ("2026-10-19", "BDCTB", 16.5, True),
            ("2026-10-20", "BDCTB", 17.0, True),
            ("2026-10-21", "BDCTB", 17.2, True),
        ],
    )
    rows = verify.match(verify.load_history(history), verify.load_observed(levels, "BDCTB"))
    summary = verify.summarize(rows)
    assert list(summary.columns) == [
        "mode",
        "lead",
        "n",
        "pending",
        "mae_q50",
        "bias_q50",
        "coverage_q10_q90",
        "mae_persistence",
    ]
    row = summary.iloc[0]
    assert (row["mode"], row["lead"], row["n"], row["pending"]) == ("live", 1, 2, 1)
    assert row["mae_q50"] == pytest.approx((0.5 + 0.2) / 2)
    assert row["bias_q50"] == pytest.approx((0.5 + 0.2) / 2)
    assert row["coverage_q10_q90"] == pytest.approx(1.0)
    assert row["mae_persistence"] == pytest.approx((0.5 + 0.2) / 2)


def test_rejects_a_history_file_without_forecasts(tmp_path):
    folder = tmp_path / "history"
    folder.mkdir()
    (folder / "2026-10-19.json").write_text(json.dumps({"issue_time": "x", "mode": "live"}))
    with pytest.raises(ValueError, match="2026-10-19.json: missing forecasts"):
        verify.load_history(folder)


def test_cli_prints_the_summary_and_writes_the_rows(tmp_path, capsys):
    history = write_history(tmp_path / "history", forecast("2026-10-19"))
    levels = write_levels(
        tmp_path / "levels.csv",
        [("2026-10-19", "BDCTB", 16.49, True), ("2026-10-20", "BDCTB", 17.02, True)],
    )
    out = tmp_path / "rows.csv"
    assert (
        verify.main(["--history", str(history), "--levels", str(levels), "--rows", str(out)]) == 0
    )
    printed = capsys.readouterr().out
    assert printed.splitlines()[0] == "BDCTB forecasts verified against the IPCET tables"
    assert "| live | 1 | 1 | 0 | 0.74 | +0.74 | 0% | 0.53 |" in printed
    assert "| live | 3 | 0 | 1 |" in printed
    lines = out.read_text().splitlines()
    assert lines[0] == (
        "issue_date,mode,lead,target_date,q10,q50,q90,observed,error,inside,persistence_error"
    )
    assert lines[1].split(",")[9] == "False"
    assert [line.split(",")[9] for line in lines[2:]] == ["", ""]


def test_cli_reports_a_missing_history_folder(tmp_path, capsys):
    levels = write_levels(tmp_path / "levels.csv", [])
    missing = tmp_path / "nothing"
    assert verify.main(["--history", str(missing), "--levels", str(levels)]) == 2
    assert capsys.readouterr().err == f"error: no forecast history in {missing}\n"
