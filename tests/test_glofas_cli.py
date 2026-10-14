import json
from urllib.parse import parse_qs, urlparse

import numpy as np
import pandas as pd

from riverforecast import glofas, glofas_cli

DAYS = pd.date_range("2023-01-01", periods=200, freq="D")
SIGNAL = 2000 + 800 * np.sin(np.arange(200) / 20)


def fake_api(url):
    query = parse_qs(urlparse(url).query)
    lats = [float(v) for v in query["latitude"][0].split(",")]
    lons = [float(v) for v in query["longitude"][0].split(",")]
    model = query["models"][0]
    start, end = query["start_date"][0], query["end_date"][0]
    days = pd.date_range(start, end, freq="D")
    signal = pd.Series(SIGNAL, index=DAYS).reindex(days)
    answers = []
    for lat, lon in zip(lats, lons, strict=True):
        on_river = (lat, lon) == (17.425, -91.525)
        values = signal * (1.0 if model == "consolidated_v4" else 1.1) if on_river else signal / 500
        if model == "forecast_v4":
            values = values.where(days >= "2023-04-01")
        answers.append(
            {
                "latitude": lat,
                "longitude": lon,
                "daily": {
                    "time": [f"{d:%Y-%m-%d}" for d in days],
                    "river_discharge": [None if np.isnan(v) else float(v) for v in values],
                },
            }
        )
    return answers if len(answers) > 1 else answers[0]


def write_station(root):
    lines = ["Clave: BDCTB", "", "Fecha, Nivel(m), Gasto(m³/s)"]
    lines += [f"{d:%Y/%m/%d},10.00,{q:.2f}" for d, q in zip(DAYS, SIGNAL, strict=True)]
    path = root / "conagua" / "hidros" / "BDCTB.csv"
    path.parent.mkdir(parents=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_fetch_selects_the_river_cell_and_stores_both_models(tmp_path, monkeypatch, capsys):
    write_station(tmp_path)
    calls = []

    def counting_api(url):
        calls.append(url)
        return fake_api(url)

    monkeypatch.setattr(glofas, "_get_json", counting_api)

    code = glofas_cli.main(
        [
            "--root",
            str(tmp_path),
            "fetch",
            "BDCTB",
            "--lat",
            "17.43",
            "--lon",
            "-91.492",
            "--start",
            "2023-01-01",
            "--end",
            "2023-07-19",
        ]
    )

    assert code == 0
    folder = tmp_path / "glofas" / "BDCTB"
    cell = json.loads((folder / "cell.json").read_text())
    assert cell["cell"] == "17.425,-91.525"
    assert cell["mean_ratio"] == 1.0
    consolidated = pd.read_csv(folder / "consolidated_v4.csv", index_col="date", parse_dates=True)
    forecast = pd.read_csv(folder / "forecast_v4.csv", index_col="date", parse_dates=True)
    assert list(consolidated.columns) == ["discharge_m3s"]
    assert len(consolidated) == 200
    assert forecast["discharge_m3s"].first_valid_index() == pd.Timestamp("2023-04-01")
    assert (folder / "candidates_consolidated_v4.csv").exists()
    assert len(calls) == 3
    assert capsys.readouterr().out == (
        "BDCTB: river cell 17.425,-91.525 (correlation 1.000 at lag 0 days, "
        "mean ratio 1.00, 200 days)\n"
        "BDCTB: consolidated_v4 200 days, forecast_v4 110 days\n"
        "BDCTB: forecast_v4 / consolidated_v4 mean ratio 1.10 on 110 shared days\n"
    )


def test_fetch_reuses_stored_candidates(tmp_path, monkeypatch):
    write_station(tmp_path)
    calls = []

    def counting_api(url):
        calls.append(url)
        return fake_api(url)

    monkeypatch.setattr(glofas, "_get_json", counting_api)
    args = [
        "--root",
        str(tmp_path),
        "fetch",
        "BDCTB",
        "--lat",
        "17.43",
        "--lon",
        "-91.492",
        "--start",
        "2023-01-01",
        "--end",
        "2023-07-19",
    ]
    assert glofas_cli.main(args) == 0
    assert glofas_cli.main(args) == 0

    # The 25-cell candidate request runs once; each run fetches the two models.
    assert len([url for url in calls if url.count(",") > 2]) == 1
    assert len(calls) == 5
