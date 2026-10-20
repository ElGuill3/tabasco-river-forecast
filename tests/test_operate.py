"""Offline operational contract; synthetic ensembles and injectable services only."""

import copy
import dataclasses
import datetime as dt
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from riverforecast import levels, operate

NOW = "2026-10-19T23:00:00Z"


def response():
    dates = pd.date_range("2026-10-08", "2026-11-10")
    return {
        "daily": {
            "time": dates.strftime("%Y-%m-%d").tolist(),
            **{
                "river_discharge" + (f"_member{i:02d}" if i else ""): [
                    1000.0 + i + n for n in range(len(dates))
                ]
                for i in range(51)
            },
        }
    }


def encoded(doc):
    return json.dumps(doc).encode()


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("network request attempted")

    monkeypatch.setattr("requests.sessions.Session.request", forbidden)


@pytest.fixture
def seeded(tmp_path):
    source = tmp_path / "seed.json"
    source.write_bytes(encoded(response()))
    root = tmp_path / "out"
    assert (
        operate.main(
            [
                "seed",
                "--data",
                str(root),
                "--response",
                str(source),
                "--run-label",
                "2026-10-19",
                "--now",
                NOW,
            ]
        )
        == 0
    )
    return root


def fake_forecast(artifact, issue, observations, provider, generated_at):
    provider.inputs(pd.Timestamp(issue))
    return {
        "mode": "live" if len(observations) else "glofas_only",
        "issue": str(issue),
        "generated_at": generated_at,
    }


def run(root, doc=None, now=NOW, reader=None, forecaster=fake_forecast):
    return operate.main(
        ["run", "--data", str(root), "--now", now],
        fetch=lambda: encoded(response() if doc is None else doc),
        reader=reader,
        forecaster=forecaster,
    )


@pytest.mark.parametrize(
    "now,label,inferred",
    [
        (NOW, "2026-10-19", False),
        ("2026-10-20T20:00:00Z", "2026-10-20", False),
        ("2026-10-23T20:00:00Z", "2026-10-23", True),
    ],
)
def test_labels(seeded, now, label, inferred):
    doc = response()
    if label != "2026-10-19":
        doc["daily"]["river_discharge_member50"][-1] += 1
    assert run(seeded, doc, now) == 0
    state = json.loads((seeded / "inputs/openmeteo/state.json").read_text())
    assert state["run_label"] == label
    assert state.get("run_label_inferred", False) == inferred
    assert (seeded / f"bdctb/history/{label}.json").exists()


def test_shared_future_dates_only(seeded, capsys):
    assert run(seeded) == 0
    before = snapshot(seeded)
    doc = response()
    for key, values in doc["daily"].items():
        if key != "time":
            values[0] += 99  # past must not count
        values.pop()  # missing tail must not count
    assert run(seeded, doc, "2026-10-20T20:00:00Z") == 0
    assert snapshot(seeded) == before
    assert capsys.readouterr().out.endswith("no change\n")


def test_seed_bootstrap_and_no_change(seeded, capsys):
    assert run(seeded) == 0
    before = snapshot(seeded)
    assert run(seeded) == 0
    assert snapshot(seeded) == before
    assert capsys.readouterr().out.splitlines()[-2:] == [
        "published 2026-10-19 glofas_only (same run)",
        "no change",
    ]


def upload(root, name="levels_2026-01-13.jpg", sha=None, size=None):
    folder = root / "inputs/ipcet/2026-10-19"
    folder.mkdir(parents=True, exist_ok=True)
    content = (Path(__file__).parent / "fixtures/levels_2026-01-13.jpg").read_bytes()
    if "/" not in name:
        (folder / name).write_bytes(content)
    meta = {
        "page_url": "https://example.invalid/article",
        "fetched_at": NOW,
        "images": [
            {
                "url": "https://example.invalid/" + name,
                "last_modified": "Tue, 13 Jan 2026 16:00:00 GMT",
                "sha256": sha or hashlib.sha256(content).hexdigest(),
                "bytes": len(content) if size is None else size,
            }
        ],
    }
    (folder / "meta.json").write_text(json.dumps(meta))
    return folder


def table(day=dt.date(2026, 10, 19), hoy=17.65, diff=-0.32):
    return levels.LevelTable(
        None,
        day,
        (
            levels.StationRow(
                "Usumacinta", "Boca del Cerro", 19.21, 11, hoy - diff, hoy, diff, 19.21 - hoy
            ),
        ),
        (),
    )


def test_ingest_good_table_real_reader_and_idempotent(seeded):
    folder = upload(seeded)
    assert run(seeded) == 0
    csv = pd.read_csv(seeded / "inputs/ipcet/bdctb_levels.csv")
    assert csv.loc[csv.date == "2026-01-13", "stage_m"].item() == 14.33
    assert csv.table_last_modified.unique().tolist() == ["Tue, 13 Jan 2026 16:00:00 GMT"]
    assert (folder / "ingested.json").exists()
    before = snapshot(seeded)
    assert run(seeded, reader=lambda _: pytest.fail("repeated OCR")) == 0
    assert snapshot(seeded) == before


@pytest.mark.parametrize(
    "kwargs,reason",
    [
        ({"sha": "bad"}, "sha256"),
        ({"size": 1}, "bytes"),
        ({"name": "%2e%2e%2foutside.jpg"}, "unsafe"),
        ({"name": "../outside.jpg"}, "unsafe"),
    ],
)
def test_reject_bad_images_without_changing_csv(seeded, kwargs, reason, capsys):
    assert run(seeded) == 0
    before = (seeded / "inputs/ipcet/bdctb_levels.csv").read_bytes()
    folder = upload(seeded, **kwargs)
    assert run(seeded, reader=lambda _: pytest.fail("invalid image OCR")) == 0
    assert (seeded / "inputs/ipcet/bdctb_levels.csv").read_bytes() == before
    assert reason in capsys.readouterr().err
    assert (folder / "ingested.json").exists()


def test_new_ipcet_publishes_same_label(seeded):
    assert run(seeded) == 0
    upload(seeded)
    assert run(seeded, reader=lambda _: table()) == 0
    assert json.loads((seeded / "bdctb/latest.json").read_text())["mode"] == "live"


@pytest.mark.parametrize("kind", ["json", "column", "no_shared"])
def test_hard_error_preserves_all_files(seeded, kind, capsys):
    assert run(seeded) == 0
    doc = copy.deepcopy(response())
    if kind == "column":
        del doc["daily"]["river_discharge_member01"]
    elif kind == "no_shared":
        doc["daily"]["time"] = pd.date_range("2027-01-01", periods=34).strftime("%Y-%m-%d").tolist()
    else:
        doc = {}
    before = snapshot(seeded)
    assert run(seeded, doc) == 1
    assert snapshot(seeded) == before
    assert "error:" in capsys.readouterr().err


def test_requires_seed(tmp_path, capsys):
    assert run(tmp_path) == 1
    assert snapshot(tmp_path) == {}
    assert "seed" in capsys.readouterr().err


@pytest.mark.parametrize("hoy,diff", [(1001, 0.1), (17.65, 0)])
def test_invalid_or_zero_difference_never_imputes_today(seeded, hoy, diff):
    upload(seeded)
    assert run(seeded, reader=lambda _: table(hoy=hoy, diff=diff)) == 0
    csv = pd.read_csv(seeded / "inputs/ipcet/bdctb_levels.csv")
    assert "2026-10-19" not in csv.date.tolist()


def test_publish_error_rolls_back_ingest_and_run(seeded):
    upload(seeded)
    before = snapshot(seeded)

    def fail(*args, **kwargs):
        raise ValueError("bad artifact")

    assert run(seeded, reader=lambda _: table(), forecaster=fail) == 1
    assert snapshot(seeded) == before


@pytest.mark.parametrize(
    "args,expected",
    [(["--help"], "seed"), (["run", "--help"], "--artifact"), (["seed", "--help"], "--run-label")],
)
def test_cli_help(capsys, args, expected):
    with pytest.raises(SystemExit) as error:
        operate.main(args)
    assert error.value.code == 0
    assert expected in capsys.readouterr().out


def test_long_gap_does_not_require_previous_issue_in_fresh_response(seeded):
    doc = response()
    for values in doc["daily"].values():
        del values[:10]
    doc["daily"]["river_discharge_member50"][-1] += 1
    assert run(seeded, doc, "2026-10-28T20:00:00Z") == 0
    state = json.loads((seeded / "inputs/openmeteo/state.json").read_text())
    assert state["run_label"] == "2026-10-28"
    assert state["run_label_inferred"] is True


def test_filesystem_write_error_restores_existing_files(seeded, monkeypatch):
    assert run(seeded) == 0
    upload(seeded)
    before = snapshot(seeded)
    original = Path.write_bytes
    failed = False

    def write(path, content):
        nonlocal failed
        if path == seeded / "bdctb/latest.json" and not failed:
            failed = True
            raise OSError("disk failure")
        return original(path, content)

    monkeypatch.setattr(Path, "write_bytes", write)
    assert run(seeded, reader=lambda _: table()) == 1
    assert snapshot(seeded) == before


def test_conflict_is_not_exported_as_live_input(seeded):
    upload(seeded)
    assert run(seeded, reader=lambda _: table()) == 0
    folder = seeded / "inputs/ipcet/2026-10-20"
    folder.mkdir()
    previous = seeded / "inputs/ipcet/2026-10-19"
    for name in ("meta.json", "levels_2026-01-13.jpg"):
        (folder / name).write_bytes((previous / name).read_bytes())
    doc = response()
    doc["daily"]["river_discharge_member50"][-1] += 1
    assert (
        run(
            seeded,
            doc,
            "2026-10-20T20:00:00Z",
            reader=lambda _: table(day=dt.date(2026, 10, 20), hoy=16, diff=-0.1),
        )
        == 0
    )
    csv = pd.read_csv(seeded / "inputs/ipcet/bdctb_levels.csv")
    assert "2026-10-19" not in csv.date.tolist()


def test_ocr_library_error_is_cached_and_other_folders_continue(seeded, capsys):
    assert run(seeded) == 0
    capsys.readouterr()
    bad = upload(seeded)
    good = seeded / "inputs/ipcet/2026-10-18"
    good.mkdir()
    for name in ("meta.json", "levels_2026-01-13.jpg"):
        (good / name).write_bytes((bad / name).read_bytes())

    def reader(path):
        if path.parent == bad:
            raise RuntimeError("tesseract failed")
        return table(day=dt.date(2026, 10, 18))

    doc = response()
    doc["daily"]["river_discharge_member50"][-1] += 1
    assert run(seeded, doc, "2026-10-20T20:00:00Z", reader=reader) == 0
    error = "rejected inputs/ipcet/2026-10-19: tesseract failed"
    assert capsys.readouterr() == ("published 2026-10-20 live (new run)\n", error + "\n")
    assert json.loads((bad / "ingested.json").read_text()) == {"rows": [], "errors": [error]}
    assert len(json.loads((good / "ingested.json").read_text())["rows"]) == 1
    assert json.loads((seeded / "bdctb/latest.json").read_text())["mode"] == "live"
    before = snapshot(seeded)
    assert (
        run(seeded, doc, "2026-10-20T21:00:00Z", reader=lambda _: pytest.fail("OCR retried")) == 0
    )
    assert snapshot(seeded) == before
    assert capsys.readouterr() == ("no change\n", "")


def test_same_day_revision_and_backwards_clock(seeded, capsys):
    assert run(seeded) == 0
    capsys.readouterr()
    doc = response()
    for revision, timestamp in enumerate(["2026-10-19T23:10:00Z", "2026-10-19T23:20:00Z"], start=1):
        before_state = json.loads((seeded / "inputs/openmeteo/state.json").read_text())
        doc["daily"]["river_discharge_member50"][-1] += 1
        assert run(seeded, doc, timestamp) == 0
        state = json.loads((seeded / "inputs/openmeteo/state.json").read_text())
        assert state["run_label"] == "2026-10-19"
        assert state["fetched_at"] == timestamp.replace("Z", "+00:00")
        assert state["revisions"] == revision
        assert state["fingerprint"] != before_state["fingerprint"]
        assert (seeded / "inputs/openmeteo/2026-10-19.json").read_bytes() == encoded(doc)
        latest = (seeded / "bdctb/latest.json").read_bytes()
        assert latest == (seeded / "bdctb/history/2026-10-19.json").read_bytes()
        assert json.loads(latest)["generated_at"] == timestamp.replace("Z", "+00:00")
        assert capsys.readouterr().err == ""
    before = snapshot(seeded)
    doc["daily"]["river_discharge_member50"][-1] += 1
    assert run(seeded, doc, "2026-10-18T23:00:00Z") == 1
    assert snapshot(seeded) == before
    assert capsys.readouterr() == ("", "error: run label is after fetch date\n")


def test_detection_log_appends_only_detected_changes(seeded):
    assert run(seeded) == 0
    log = seeded / "inputs/openmeteo/detections.csv"
    assert not log.exists()  # Seeding and unchanged polling are not detections.
    doc = response()
    expected = "run_label,fetched_at,kind\n"
    for timestamp, label, kind in [
        ("2026-10-20T01:00:00Z", "2026-10-20", "new"),
        ("2026-10-20T02:00:00Z", "2026-10-20", "revision"),
        ("2026-10-23T01:00:00Z", "2026-10-23", "inferred"),
    ]:
        doc["daily"]["river_discharge_member50"][-1] += 1
        assert run(seeded, doc, timestamp) == 0
        expected += f"{label},{timestamp.replace('Z', '+00:00')},{kind}\n"
        assert log.read_text() == expected
        before = snapshot(seeded)
        assert run(seeded, doc, timestamp) == 0
        assert snapshot(seeded) == before


def test_schedule_polls_every_utc_hour():
    workflow = Path(__file__).parents[1] / ".github/workflows/forecast.yml"
    assert "cron: '17 * * * *'" in workflow.read_text()


@pytest.mark.parametrize(
    "stamp,visible",
    [
        ("Mon, 19 Oct 2026 16:59:59 GMT", True),
        ("Mon, 19 Oct 2026 17:00:00 GMT", True),
        ("Mon, 19 Oct 2026 17:00:01 GMT", False),
    ],
)
def test_issue_cutoff_mexico_city(seeded, stamp, visible, capsys):
    folder = upload(seeded)
    meta = json.loads((folder / "meta.json").read_text())
    meta["images"][0]["last_modified"] = stamp
    (folder / "meta.json").write_text(json.dumps(meta))
    capsys.readouterr()
    assert run(seeded, reader=lambda _: table()) == 0
    mode = "live" if visible else "glofas_only"
    assert capsys.readouterr() == (f"published 2026-10-19 {mode} (same run)\n", "")
    csv = pd.read_csv(seeded / "inputs/ipcet/bdctb_levels.csv")
    assert ("2026-10-19" in csv.date.tolist()) == visible
    assert json.loads((seeded / "bdctb/latest.json").read_text())["mode"] == mode
    receipt = json.loads((folder / "ingested.json").read_text())
    assert receipt["errors"] == []
    assert len(receipt["rows"]) == 1


def test_fetch_timeout_and_two_attempts():
    calls = []

    def get(url, timeout):
        calls.append((url, timeout))
        raise OSError("offline")

    with pytest.raises(OSError):
        operate.fetch_response(get=get)
    assert calls == [(operate.OPENMETEO_URL, 60)] * 2


FIXTURES = Path(__file__).parent / "fixtures"
PAGE_URL = "https://example.invalid/semaforo-19102026"


def upload_table(root, fixture="semaforo_2026-10-19.jpeg", page="2026-10-19"):
    folder = root / f"inputs/ipcet/{page}"
    folder.mkdir(parents=True, exist_ok=True)
    content = (FIXTURES / fixture).read_bytes()
    (folder / fixture).write_bytes(content)
    meta = {
        "page_url": PAGE_URL,
        "fetched_at": NOW,
        "images": [
            {
                "url": "https://example.invalid/" + fixture,
                "last_modified": "Mon, 19 Oct 2026 12:10:00 GMT",
                "sha256": hashlib.sha256(content).hexdigest(),
                "bytes": len(content),
            }
        ],
    }
    (folder / "meta.json").write_text(json.dumps(meta))
    return folder


def row(river, station, hoy=10.0, diff=-0.1, namo=20.0, semaforo="NORMAL"):
    return levels.StationRow(
        river, station, namo, 1.0, round(hoy - diff, 2), hoy, diff, round(namo - hoy, 2), semaforo
    )


def official(day, *rows):
    return levels.LevelTable(None, day, rows, ())


def ipcet(root, name):
    return json.loads((root / f"ipcet/{name}.json").read_text())


def test_official_table_publishes_every_station(seeded, capsys):
    upload_table(seeded)
    assert run(seeded) == 0
    latest = ipcet(seeded, "latest")
    assert {k: v for k, v in latest.items() if k != "stations"} == {
        "schema": "ipcet-levels/v1",
        "table_date": "2026-10-19",
        "issued_at": "2026-10-19T06:00:00-06:00",
        "last_modified": "Mon, 19 Oct 2026 12:10:00 GMT",
        "source_url": PAGE_URL,
    }
    assert [s["code"] for s in latest["stations"]] == [c for _, _, c in operate.stations.STATIONS]
    states = {s["code"]: s["semaforo"] for s in latest["stations"]}
    assert states.pop("SJOCP") == "ALERTA"
    assert set(states.values()) == {"NORMAL"}
    bdctb = next(s for s in latest["stations"] if s["code"] == "BDCTB")
    assert (bdctb["river"], bdctb["station"], bdctb["hoy"], bdctb["namo"]) == (
        "Usumacinta",
        "Boca del Cerro",
        16.49,
        19.21,
    )
    assert bdctb["problems"] == []
    csv = pd.read_csv(seeded / "inputs/ipcet/levels.csv")
    assert len(csv) == 14 and set(csv.table_date) == {"2026-10-19"}
    recent = ipcet(seeded, "recent")
    assert recent["schema"] == "ipcet-recent/v1"
    assert recent["stations"]["BDCTB"] == [
        {"date": "2026-10-19", "hoy": 16.49, "semaforo": "NORMAL"}
    ]
    assert json.loads((seeded / "bdctb/latest.json").read_text())["mode"] == "live"
    capsys.readouterr()
    before = snapshot(seeded)
    assert run(seeded, reader=lambda _: pytest.fail("repeated OCR")) == 0
    assert snapshot(seeded) == before
    assert capsys.readouterr().out == "no change\n"


def test_unknown_and_invalid_rows(seeded):
    upload(seeded)
    bad = dataclasses.replace(
        row("Pichucalco", "San Joaquín", hoy=23.58, semaforo="ALERTA"), diff=5.0
    )
    rows = (
        row("Usumacinta", "Boca del Cerro", hoy=17.65, diff=-0.32, namo=19.21),
        bad,
        row("Usumacinta", "Tenosique", semaforo=None),
    )
    assert run(seeded, reader=lambda _: official(dt.date(2026, 10, 19), *rows)) == 0
    latest = ipcet(seeded, "latest")
    assert [s["code"] for s in latest["stations"]] == ["BDCTB", "SJOCP", None]
    assert latest["stations"][1]["problems"] == ["diff_mismatch"]
    assert latest["stations"][2]["semaforo"] is None
    recent = ipcet(seeded, "recent")["stations"]
    assert sorted(recent) == ["BDCTB", "SJOCP"]
    assert recent["SJOCP"] == [{"date": "2026-10-19", "hoy": None, "semaforo": "ALERTA"}]


def seed_levels(root, rows, since="2026-01-01"):
    source = root.parent / "levels-source.csv"
    pd.DataFrame(rows).to_csv(source, index=False)
    return operate.main(
        ["seed-levels", "--data", str(root), "--levels", str(source), "--since", since]
    )


def archive_row(day, station="Boca del Cerro", river="Usumacinta", **extra):
    values = {
        "page_date": day,
        "table_date": day,
        "obs_date": day,
        "obs_flag": "ok",
        "kind": "station",
        "river": river,
        "station": station,
        "namo": 19.21,
        "ayer": 15.0,
        "hoy": 15.1,
        "diff": 0.1,
        "capacity": 4.11,
        "valid": True,
        "reason": "",
        "sha256": "x",
    }
    return values | extra


def test_seed_levels_keeps_trusted_station_rows(seeded, capsys):
    rows = [
        archive_row("2026-10-01"),
        archive_row("2026-10-01", "San Joaquín", "Pichucalco", hoy=20.5),
        archive_row("2026-10-02", kind="dam", river="Peñitas", station="Peñitas"),
        archive_row("2026-10-03", valid=False, reason="diff_mismatch"),
        archive_row("2026-10-04", obs_flag="far_off"),
        archive_row("2025-12-31"),
        archive_row("2026-10-05", "Tenosique"),
    ]
    assert seed_levels(seeded, rows) == 0
    assert capsys.readouterr().out == "seeded 3 level rows\n"
    seed = pd.read_csv(seeded / "inputs/ipcet/levels_seed.csv", keep_default_na=False)
    assert seed[["table_date", "code", "station", "hoy", "semaforo"]].values.tolist() == [
        ["2026-10-01", "BDCTB", "Boca del Cerro", 15.1, ""],
        ["2026-10-01", "SJOCP", "San Joaquín", 20.5, ""],
        ["2026-10-05", "", "Tenosique", 15.1, ""],
    ]
    before = snapshot(seeded)
    assert seed_levels(seeded, rows) == 1
    assert "already seeded" in capsys.readouterr().err
    assert snapshot(seeded) == before


def test_recent_keeps_last_30_dates_and_newest_table_wins(seeded):
    days = pd.date_range("2026-09-01", periods=40).strftime("%Y-%m-%d").tolist()
    assert seed_levels(seeded, [archive_row(d) for d in days]) == 0
    assert run(seeded) == 0
    assert not (seeded / "ipcet/latest.json").exists()
    assert len(ipcet(seeded, "recent")["stations"]["BDCTB"]) == 30
    older = upload(seeded)
    newer = seeded / "inputs/ipcet/2026-10-18"
    newer.mkdir()
    for path in older.iterdir():
        (newer / path.name).write_bytes(path.read_bytes())

    def reader(path):
        day = dt.date(2026, 10, 18) if path.parent == newer else dt.date(2026, 10, 17)
        return official(day, row("Usumacinta", "Boca del Cerro", hoy=17.0, namo=19.21))

    assert run(seeded, reader=reader) == 0
    assert ipcet(seeded, "latest")["table_date"] == "2026-10-18"
    series = ipcet(seeded, "recent")["stations"]["BDCTB"]
    expected = (days + ["2026-10-17", "2026-10-18"])[-30:]
    assert [p["date"] for p in series] == expected
    assert series[-1] == {"date": "2026-10-18", "hoy": 17.0, "semaforo": "NORMAL"}


def test_each_image_is_read_once_per_run(seeded):
    upload(seeded)
    calls = []

    def reader(path):
        calls.append(path)
        return table()

    assert run(seeded, reader=reader) == 0
    assert len(calls) == 1
    assert json.loads((seeded / "inputs/ipcet/2026-10-19/tables.json").read_text())["errors"] == []


def test_seed_levels_help(capsys):
    with pytest.raises(SystemExit):
        operate.main(["seed-levels", "--help"])
    assert "--since" in capsys.readouterr().out
