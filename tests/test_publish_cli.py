"""Publishing CLI: no network and no training on the serving path."""

import json

import pytest

from riverforecast import publish_cli
from tests.test_publish import Provider, artifact, openmeteo_response  # noqa: F401


def test_publish_cli(artifact, tmp_path, monkeypatch, capsys):  # noqa: F811
    monkeypatch.setattr(publish_cli, "CemsArchiveProvider", lambda raw: Provider())
    csv = tmp_path / "ipcet.csv"
    csv.write_text("date,stage_m,table_last_modified\n2026-10-13,13.2,2026-10-13T16:00:00Z\n")
    out = tmp_path / "forecast.json"
    assert (
        publish_cli.main(
            [
                "publish",
                "--artifact",
                str(artifact),
                "--issue",
                "2026-10-13",
                "--ipcet",
                str(csv),
                "--out",
                str(out),
            ]
        )
        == 0
    )
    assert capsys.readouterr() == ("", "")
    result = json.loads(out.read_text())
    assert result["mode"] == "live"
    assert result["forecasts"]["h1"]["q50"] == 12.0
    before = out.read_bytes()
    csv.write_text("date,stage_m\n2026-10-13,13.2\n")
    assert (
        publish_cli.main(
            [
                "publish",
                "--artifact",
                str(artifact),
                "--issue",
                "2026-10-13",
                "--ipcet",
                str(csv),
                "--out",
                str(out),
            ]
        )
        == 1
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error: IPCET CSV requires date, stage_m, table_last_modified\n"
    assert out.read_bytes() == before


def test_openmeteo_cli(artifact, openmeteo_response, tmp_path, capsys):  # noqa: F811
    csv = tmp_path / "ipcet.csv"
    csv.write_text("date,stage_m,table_last_modified\n")
    out = tmp_path / "forecast.json"
    args = [
        "publish",
        "--artifact",
        str(artifact),
        "--issue",
        "2026-10-13",
        "--ipcet",
        str(csv),
        "--openmeteo",
        str(openmeteo_response),
        "--out",
        str(out),
    ]
    assert publish_cli.main(args) == 0
    assert capsys.readouterr() == ("", "")
    assert json.loads(out.read_text())["forecasts"]["h7"]["q50"] == 12.0
    before = out.read_bytes()
    openmeteo_response.write_text("{}")
    assert publish_cli.main(args) == 1
    assert capsys.readouterr() == ("", "error: Open-Meteo response requires daily/time\n")
    assert out.read_bytes() == before


def test_publish_help(capsys):
    with pytest.raises(SystemExit) as exc:
        publish_cli.main(["publish", "--help"])
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "--openmeteo" in captured.out
    assert "timezone" in captured.out
    assert captured.err == ""


def test_freeze_cli(monkeypatch, capsys):
    def frozen(destination, version):
        assert str(destination) == "models/bdctb"
        assert version == "bdctb-L1-postaudit-v1"
        return {"artifact_bytes": 123}

    monkeypatch.setattr(publish_cli, "freeze", frozen)
    assert publish_cli.main(["freeze"]) == 0
    assert capsys.readouterr() == ('{"artifact_bytes": 123}\n', "")


def test_help(capsys):
    with pytest.raises(SystemExit) as exc:
        publish_cli.main(["--help"])
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "freeze" in captured.out and "publish" in captured.out
    assert captured.err == ""
