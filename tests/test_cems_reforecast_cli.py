from riverforecast import cems_reforecast_cli as cli
from tests.test_cems_cli import RawClient


def test_plan_never_downloads(capsys):
    client = RawClient()
    assert cli.main(["plan"], client=client) == 0
    out = capsys.readouterr()
    assert "requests" in out.out and "fields" in out.out and "MiB" in out.out
    assert out.err == "" and client.calls == []


def test_single_raw_reforecast_and_resume(tmp_path, capsys):
    client = RawClient()
    args = ["fetch-raw", "--area", "tabasco", "--out", str(tmp_path), "--issue", "2003-04-03"]
    assert cli.main(args, client=client) == 0
    assert cli.main(args, client=client) == 0
    assert len(client.calls) == 1
    dataset, request = client.calls[0]
    assert dataset == "cems-glofas-reforecast"
    assert request["product_type"] == ["control_reforecast", "ensemble_perturbed_reforecast"]
    assert request["system_version"] == ["version_4_0"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["2003-04-03.zip"]
    output = capsys.readouterr()
    assert output.err == ""
    assert (
        output.out
        == "2003-04-03: stored\n1 requests stored, 0 failed\n0 requests stored, 0 failed (1 already stored)\n"
    )
