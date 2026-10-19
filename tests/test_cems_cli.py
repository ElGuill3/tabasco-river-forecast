import zipfile

import numpy as np
import pandas as pd

from riverforecast import cems, cems_cli
from tests.test_cems import archive


class RawClient:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def retrieve(self, dataset, request, target):
        self.calls.append((dataset, request))
        if self.error:
            raise RuntimeError(self.error)
        from tests.test_cems import netcdf

        issues = cems.request_dates(request)
        count = 50 if dataset == cems.DATASET else 10
        with zipfile.ZipFile(target, "w") as zipped:
            zipped.writestr("control.nc", netcdf(np.ones((3, len(issues), 3, 3)), issues))
            zipped.writestr(
                "ensemble.nc",
                netcdf(np.ones((count, 3, len(issues), 3, 3)), issues, list(range(1, count + 1))),
            )


class FakeClient:
    def __init__(self, fail_month=None):
        self.requests = []
        self.days = []
        self.fail_month = fail_month

    def retrieve(self, dataset, request, target):
        self.requests.append((dataset, request["year"][0], request["month"][0]))
        self.days.append(request["day"])
        if request["month"][0] == self.fail_month:
            raise RuntimeError("queue rejected the request")
        year, month = request["year"][0], request["month"][0]
        issues = [f"{year}-{month}-{day}" for day in request["day"]]
        with open(target, "wb") as handle:
            handle.write(archive(issues))


def test_fetch_splits_months_into_five_day_requests_and_skips_done_ones(tmp_path, capsys):
    client = FakeClient()
    args = [
        "--root",
        str(tmp_path),
        "fetch",
        "BDCTB",
        "--start",
        "2026-09-01",
        "--end",
        "2026-10-14",
    ]

    assert cems_cli.main(args, client=client) == 0
    assert cems_cli.main(args, client=client) == 0

    folder = tmp_path / "glofas" / "BDCTB" / "operational"
    names = sorted(p.name for p in folder.iterdir())
    assert names == [
        "2026-09-01.csv",
        "2026-09-06.csv",
        "2026-09-11.csv",
        "2026-09-16.csv",
        "2026-09-21.csv",
        "2026-09-26.csv",
        "2026-10-01.csv",
        "2026-10-06.csv",
        "2026-10-11.csv",
    ]
    last = pd.read_csv(folder / "2026-10-11.csv")
    assert last.issue.unique().tolist() == ["2026-10-11", "2026-10-12", "2026-10-13", "2026-10-14"]
    # Nine requests on the first run, none on the second.
    assert len(client.requests) == 9
    assert all(len(days) <= 5 for days in client.days)
    out = capsys.readouterr().out
    assert "2026-09-01: 45 records, 5 issues" in out
    assert "9 requests stored, 0 failed" in out
    assert "0 requests stored, 0 failed (9 already stored)" in out


def test_raw_atomic_resume_and_invalid_archive_retry(tmp_path, capsys):
    args = [
        "fetch-raw",
        "--area",
        "tabasco",
        "--start",
        "2026-10-01",
        "--end",
        "2026-10-06",
        "--out",
        str(tmp_path),
        "--parallel",
        "1",
    ]
    client = RawClient()
    assert cems_cli.main(args, client=client) == 0
    assert cems_cli.main(args, client=client) == 0
    assert len(client.calls) == 2
    assert client.calls[0][1]["area"] == [18.3, -93.6, 17.1, -90.9]
    assert client.calls[0][1]["day"] == ["01", "02", "03", "04", "05"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["2026-10-01.zip", "2026-10-06.zip"]
    (tmp_path / "2026-10-01.zip").write_bytes(archive(["2026-09-01"]))
    assert cems_cli.main(args, client=client) == 0
    assert len(client.calls) == 3
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == (
        "2026-10-01: stored\n2026-10-06: stored\n2 requests stored, 0 failed\n0 requests stored, 0 failed (2 already stored)\n2026-10-01: stored\n1 requests stored, 0 failed (1 already stored)\n"
    )


def test_raw_cost_limit_stops_submissions_and_is_retryable(tmp_path, capsys):
    args = [
        "fetch-raw",
        "--area",
        "tabasco",
        "--start",
        "2026-10-01",
        "--end",
        "2026-10-20",
        "--out",
        str(tmp_path),
        "--parallel",
        "1",
    ]
    client = RawClient("cost limits exceeded")
    assert cems_cli.main(args, client=client) == 1
    assert len(client.calls) == 1
    assert not list(tmp_path.glob("*.zip"))
    captured = capsys.readouterr()
    assert captured.err == "2026-10-01: failed (RuntimeError: cost limits exceeded)\n"
    assert captured.out == "0 requests stored, 1 failed\n"
    client.error = "queue rejected"
    assert cems_cli.main(args, client=client) == 1
    assert len(client.calls) == 5
    assert not list(tmp_path.glob("*.zip"))
    client.error = None
    assert cems_cli.main(args, client=client) == 0
    assert len(list(tmp_path.glob("*.zip"))) == 4


def test_raw_invalid_reply_never_replaces_stored_file(tmp_path, capsys):
    target = tmp_path / "2026-10-01.zip"
    previous = archive(["2026-09-01"])
    target.write_bytes(previous)

    class IncompleteClient:
        def retrieve(self, dataset, request, output):
            from pathlib import Path

            assert Path(output) != target
            assert target.read_bytes() == previous
            Path(output).write_bytes(archive(["2026-10-01"]))

    args = [
        "fetch-raw",
        "--area",
        "tabasco",
        "--start",
        "2026-10-01",
        "--end",
        "2026-10-05",
        "--out",
        str(tmp_path),
    ]
    assert cems_cli.main(args, client=IncompleteClient()) == 1
    assert target.read_bytes() == previous
    assert list(tmp_path.iterdir()) == [target]
    captured = capsys.readouterr()
    assert captured.out == "0 requests stored, 1 failed\n"
    assert (
        captured.err
        == "2026-10-01: failed (CemsError: answer has incomplete issue/lead/member coverage)\n"
    )


def test_one_request_stays_under_the_cems_field_limit():
    assert cems_cli.DAYS_PER_REQUEST * 51 * len(cems_cli.cems.LEADS_HOURS) <= 900


def test_a_failed_request_is_reported_and_others_are_kept(tmp_path, capsys):
    client = FakeClient(fail_month="09")
    args = [
        "--root",
        str(tmp_path),
        "fetch",
        "BDCTB",
        "--start",
        "2026-09-26",
        "--end",
        "2026-10-03",
    ]

    assert cems_cli.main(args, client=client) == 1

    folder = tmp_path / "glofas" / "BDCTB" / "operational"
    assert sorted(p.name for p in folder.iterdir()) == ["2026-10-01.csv"]
    captured = capsys.readouterr()
    assert "2026-09-26: failed (RuntimeError: queue rejected the request)" in captured.err
    assert "1 requests stored, 1 failed" in captured.out
