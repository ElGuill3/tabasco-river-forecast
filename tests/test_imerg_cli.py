import io
import json

import h5py
import numpy as np
import pandas as pd
import requests

from riverforecast import imerg_cli

DAY_FILE = "3B-DAY-E.MS.MRG.3IMERG.{:%Y%m%d}-S000000-E235959.V07B.nc4"


def subset_bytes(values):
    buffer = io.BytesIO()
    with h5py.File(buffer, "w") as handle:
        data = handle.create_dataset("precipitation", data=np.array([values], dtype="float32"))
        data.attrs["_FillValue"] = np.float32(-9999.9)
    return buffer.getvalue()


class FakeResponse:
    def __init__(self, status=200, text="", content=b""):
        self.status_code, self.text, self.content = status, text, content

    def raise_for_status(self):
        if self.status_code >= 400:
            error = requests.HTTPError(f"HTTP {self.status_code}")
            error.response = self
            raise error


class FakeEarthdata:
    """GES DISC with two days in 2020-11; the second day carries a fill value."""

    def __init__(self, subset_status=200):
        self.urls = []
        self.subset_status = subset_status

    def get(self, url, timeout):
        self.urls.append(url)
        if url.endswith("/2020/11/"):
            days = pd.date_range("2020-11-01", periods=2)
            return FakeResponse(text=" ".join(f'href="{DAY_FILE.format(d)}"' for d in days))
        if "/opendap/" in url:
            if self.subset_status != 200:
                return FakeResponse(status=self.subset_status)
            inner = [[2.0, 4.0], [6.0, 8.0]] if "20201101" in url else [[-9999.9, 3.0], [5.0, 7.0]]
            grid = np.full((4, 4), 100.0)
            grid[1:3, 1:3] = inner
            return FakeResponse(content=subset_bytes(grid.tolist()))
        return FakeResponse(status=404)


def write_basin(folder):
    # The square holds the centres -92.55/-92.45 x 14.85/14.95. The window that
    # covers it adds a ring of cells whose centres fall outside (100 mm each),
    # which the basin mask must leave out.
    square = [[-92.58, 14.82], [-92.42, 14.82], [-92.42, 14.98], [-92.58, 14.98], [-92.58, 14.82]]
    folder.mkdir(parents=True)
    (folder / "BDCTB.geojson").write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {},
                        "geometry": {"type": "Polygon", "coordinates": [square]},
                    }
                ],
            }
        )
    )


def test_update_stores_the_basin_mean_of_each_listed_day(tmp_path, capsys):
    write_basin(tmp_path / "basins")
    http = FakeEarthdata()

    code = imerg_cli.main(
        [
            "--basins",
            str(tmp_path / "basins"),
            "--root",
            str(tmp_path / "imerg"),
            "update",
            "BDCTB",
            "--start",
            "2020-11-01",
            "--end",
            "2020-11-30",
        ],
        http=http,
    )

    assert code == 0
    table = pd.read_csv(tmp_path / "imerg" / "BDCTB" / "daily.csv")
    assert table["date"].tolist() == ["2020-11-01", "2020-11-02"]
    # Day 2 has one fill value, so its mean covers the other three cells.
    assert table["basin_mean_mm"].tolist() == [5.0, 5.0]
    assert "%5B0:0%5D%5B873:876%5D%5B1047:1050%5D" in http.urls[1]
    assert capsys.readouterr().out == (
        "BDCTB: 2020-11 +2 days\n"
        f"BDCTB: 2 days added to {tmp_path / 'imerg' / 'BDCTB' / 'daily.csv'}\n"
    )


def test_rejected_credentials_stop_with_a_hint(tmp_path, capsys):
    write_basin(tmp_path / "basins")

    code = imerg_cli.main(
        [
            "--basins",
            str(tmp_path / "basins"),
            "--root",
            str(tmp_path / "imerg"),
            "update",
            "BDCTB",
            "--start",
            "2020-11-01",
            "--end",
            "2020-11-30",
        ],
        http=FakeEarthdata(subset_status=401),
    )

    assert code == 1
    assert capsys.readouterr().err == (
        "BDCTB: 2020-11 failed: HTTP 401 (check ~/.netrc and the GES DISC app approval)\n"
    )
