import pandas as pd

from riverforecast import cems_cli
from tests.test_cems import archive


class FakeClient:
    def __init__(self, fail_month=None):
        self.requests = []
        self.fail_month = fail_month

    def retrieve(self, dataset, request, target):
        self.requests.append((dataset, request["year"][0], request["month"][0]))
        if request["month"][0] == self.fail_month:
            raise RuntimeError("queue rejected the request")
        year, month = request["year"][0], request["month"][0]
        issues = [f"{year}-{month}-{day}" for day in request["day"]]
        with open(target, "wb") as handle:
            handle.write(archive(issues))


def test_fetch_stores_one_table_per_month_and_skips_done_months(tmp_path, capsys):
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
    assert sorted(p.name for p in folder.iterdir()) == ["2026-09.csv", "2026-10.csv"]
    october = pd.read_csv(folder / "2026-10.csv")
    assert october.issue.min() == "2026-10-01" and october.issue.max() == "2026-10-14"
    assert len(october) == 14 * 9
    assert sorted(client.requests) == [
        ("cems-glofas-forecast", "2026", "09"),
        ("cems-glofas-forecast", "2026", "10"),
    ]
    out = capsys.readouterr().out
    assert "2026-09: 270 records, 30 issues" in out
    assert "2 months stored, 0 failed" in out
    assert "0 months stored, 0 failed (2 already stored)" in out


def test_a_failed_month_is_reported_and_others_are_kept(tmp_path, capsys):
    client = FakeClient(fail_month="09")
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

    assert cems_cli.main(args, client=client) == 1

    folder = tmp_path / "glofas" / "BDCTB" / "operational"
    assert sorted(p.name for p in folder.iterdir()) == ["2026-10.csv"]
    captured = capsys.readouterr()
    assert "2026-09: failed (RuntimeError: queue rejected the request)" in captured.err
    assert "1 months stored, 1 failed" in captured.out
