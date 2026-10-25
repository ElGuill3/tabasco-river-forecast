"""L67 offline publication and isolation contracts."""

import copy
import datetime as dt
import json

import pandas as pd
import pytest

from riverforecast import operate, publish
from tests import test_operate, test_publish
from tests.test_operate import NOW, encoded, official, response, row, upload
from tests.test_publish import Provider

seeded = test_operate.seeded
artifact = test_publish.artifact
no_network = test_operate.no_network

CASES = [
    ("MACTB", "Macuspana", 9.85),
    ("SAACP", "Salto de Agua", 10.99),
    ("PNVTB", "Pueblo Nuevo", 7.49),
    ("TAPTB", "Tapijulapa", 24.63),
]


@pytest.mark.parametrize("code,name,namo", CASES)
def test_station_schema_namo(artifact, code, name, namo):
    meta = json.loads((artifact / "metadata.json").read_text())
    meta.update(station={"code": code, "name": name}, namo_m=namo)
    (artifact / "metadata.json").write_text(json.dumps(meta))
    result = publish.forecast(artifact, "2026-10-19", publish.empty_observations(), Provider())
    assert result["station"] == {"code": code, "name": name}
    assert result["schema"] == "bdctb-forecast/v2"
    assert result["mode"] == "glofas_only" and result["live_input"] is None
    for point in result["forecasts"].values():
        assert point["namo_m"] == namo
        assert point["distance_to_namo_m"] == 12 - namo


def multi():
    return [copy.deepcopy(response()) for _ in range(5)]


def test_multistation_paths_rows_fallback_isolation(seeded, capsys):
    upload(seeded)
    rows = [
        row("Usumacinta", "Boca del Cerro", namo=19.21),
        row("Puxcatan", "Macuspana", hoy=7, namo=9.85),
        row("Tulija", "Salto de Agua", hoy=8, namo=10.99),
        row("De la Sierra", "Pueblo Nuevo", hoy=5, namo=7.49),
    ]
    calls = {}

    def forecast(artifact, issue, observations, provider, generated_at):
        code = artifact.name.upper()
        provider.inputs(pd.Timestamp(issue))
        calls[code] = observations
        if code == "SAACP":
            raise ValueError("station failed")
        return {"mode": "live" if len(observations) else "glofas_only"}

    args = ["run", "--data", str(seeded), "--now", NOW]
    capsys.readouterr()
    assert (
        operate.main(
            args,
            fetch=lambda: encoded(multi()),
            reader=lambda _: official(dt.date(2026, 10, 19), *rows),
            forecaster=forecast,
        )
        == 0
    )
    captured = capsys.readouterr()
    assert "SAACP" in captured.out and "station failed" in captured.out
    assert calls["MACTB"].iloc[-1].stage_m == 7
    assert calls["PNVTB"].iloc[-1].stage_m == 5
    assert calls["TAPTB"].empty
    for code in ("bdctb", "mactb", "pnvtb", "taptb"):
        latest = (seeded / code / "latest.json").read_bytes()
        assert latest == (seeded / code / "history/2026-10-19.json").read_bytes()
        assert (
            json.loads((seeded / code / "published.json").read_text())["run_label"] == "2026-10-19"
        )
    assert not (seeded / "saacp/latest.json").exists()
    assert not (seeded / "inputs/openmeteo/saacp/state.json").exists()
    assert json.loads((seeded / "taptb/latest.json").read_text())["mode"] == "glofas_only"
    capsys.readouterr()
    assert (
        operate.main(
            args,
            fetch=lambda: encoded(multi()),
            reader=lambda _: official(dt.date(2026, 10, 19), *rows),
            forecaster=forecast,
        )
        == 0
    )
    assert "SAACP" in capsys.readouterr().out


@pytest.mark.parametrize("code,name,namo", CASES)
def test_ingest_own_namo_and_validity(seeded, code, name, namo):
    upload(seeded)
    river = next(r for r, s, c in operate.stations.STATIONS if c == code)
    invalid = row(river, name, hoy=6, namo=namo + 1)
    obs, _, _ = operate.ingest(
        seeded,
        "2026-10-19",
        operate.utc(NOW),
        lambda _: official(dt.date(2026, 10, 19), invalid),
        code=code,
    )
    assert obs.empty


def test_training_construction_matches_l65(monkeypatch):
    import importlib.util
    from pathlib import Path

    monkeypatch.syspath_prepend(str(Path("experiments").resolve()))
    spec = importlib.util.spec_from_file_location(
        "station_models_reference", "experiments/16_station_models.py"
    )
    reference = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reference)
    days = pd.date_range("1998-12-01", "2023-01-10")
    stage = pd.Series(10.0, index=days).drop(days[20:25])
    discharge = pd.Series(100.0, index=days)
    for lead in (1, 3, 7):
        actual = publish.training_table(stage, discharge, lead)
        expected = reference.training_table(stage, discharge, lead)
        for a, e in zip(actual, expected, strict=True):
            if isinstance(a, pd.Series):
                pd.testing.assert_series_equal(a, e)
            else:
                pd.testing.assert_frame_equal(a, e)


def test_multicell_request_uses_frozen_cells(monkeypatch):
    calls = []
    monkeypatch.setattr(operate, "fetch_response", lambda **kwargs: calls.append(kwargs) or b"[]")
    assert operate.fetch_all_response() == b"[]"
    assert calls == [
        {
            "url": "https://flood-api.open-meteo.com/v1/flood?latitude=17.425,17.725,17.575,17.975,17.525&longitude=-91.525,-92.475,-92.375,-92.975,-92.825&daily=river_discharge&models=forecast_v4&ensemble=true&past_days=10&forecast_days=10"
        }
    ]


@pytest.mark.parametrize("code,name,namo", CASES)
@pytest.mark.parametrize("age,mode", [(0, "live"), (7, "live"), (8, "glofas_only")])
def test_frozen_station_age_rules(code, name, namo, age, mode):
    issue = pd.Timestamp("2026-10-19")
    observations = pd.DataFrame(
        {
            "date": [issue - pd.Timedelta(days=age)],
            "stage_m": [namo - 2],
            "table_last_modified": ["2026-10-19T16:00:00Z"],
        }
    )
    result = publish.forecast(f"models/{code.lower()}", issue, observations, Provider())
    assert result["station"] == {"code": code, "name": name}
    assert result["model_version"] == f"{code.lower()}-L65-v1"
    assert result["mode"] == mode
    assert result["live_input"] == (
        None
        if mode == "glofas_only"
        else {
            "date": f"{issue - pd.Timedelta(days=age):%Y-%m-%d}",
            "stage_m": namo - 2,
            "age_days": age,
        }
    )
    for point in result["forecasts"].values():
        assert point["namo_m"] == namo
        assert point["distance_to_namo_m"] == point["q50"] - namo


def test_five_successful_stations_write_nothing_on_repeat(seeded, capsys):
    from tests.test_operate import snapshot

    def forecast(artifact, issue, observations, provider, generated_at):
        provider.inputs(pd.Timestamp(issue))
        return {"mode": "glofas_only"}

    args = ["run", "--data", str(seeded), "--now", NOW]
    assert operate.main(args, fetch=lambda: encoded(multi()), forecaster=forecast) == 0
    before = snapshot(seeded)
    capsys.readouterr()
    assert operate.main(args, fetch=lambda: encoded(multi()), forecaster=forecast) == 0
    assert snapshot(seeded) == before
    assert capsys.readouterr() == (
        "BDCTB: no change; MACTB: no change; SAACP: no change; PNVTB: no change; TAPTB: no change\n",
        "",
    )


@pytest.mark.parametrize("bad_index", range(5))
def test_bad_cell_does_not_block_other_stations(seeded, capsys, bad_index):
    from tests.test_operate import fake_forecast

    documents = multi()
    documents[bad_index]["daily"].pop("river_discharge_member01")
    assert (
        operate.main(
            ["run", "--data", str(seeded), "--now", NOW],
            fetch=lambda: encoded(documents),
            forecaster=fake_forecast,
        )
        == 0
    )
    bad_code = operate.stations.PUBLISHED[bad_index]
    assert f"{bad_code}: failed:" in capsys.readouterr().out
    assert not (seeded / bad_code.lower() / "latest.json").exists()
    for code in operate.stations.PUBLISHED:
        if code != bad_code:
            assert (seeded / code.lower() / "latest.json").exists()


def test_fit_code_dispatch_and_bdctb_default(monkeypatch, capsys):
    from pathlib import Path

    from riverforecast import publish_cli

    calls = []
    monkeypatch.setattr(publish_cli, "freeze", lambda *args: calls.append(args) or {})
    monkeypatch.setattr(publish_cli, "freeze_station", lambda *args: calls.append(args) or {})
    assert publish_cli.main(["fit", "--code", "mactb"]) == 0
    assert calls.pop() == ("MACTB", None, None)
    assert publish_cli.main(["freeze"]) == 0
    assert calls.pop() == (Path("models/bdctb"), "bdctb-L1-postaudit-v1")
    assert capsys.readouterr() == ("{}\n{}\n", "")


def test_workflow_stages_only_existing_station_directories():
    from pathlib import Path

    workflow = Path(".github/workflows/forecast.yml").read_text()
    assert "for station in mactb saacp pnvtb taptb; do" in workflow
    assert 'if [ -d "$station" ]; then' in workflow
    assert 'timeout 30 git add "$station"' in workflow


def test_bdctb_fixed_output_pin(artifact):
    result = publish.forecast(artifact, "2026-10-19", publish.empty_observations(), Provider())
    import hashlib

    assert (
        hashlib.sha256(operate.json_bytes(result)).hexdigest()
        == "944848f5610c5fa51c214841ba0441db7c9fc3221923315a3a712c6da4ff8aff"
    )
