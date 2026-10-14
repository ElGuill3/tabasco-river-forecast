import hashlib
from pathlib import Path

import pandas as pd

from riverforecast import semaforo_cli

FIXTURE = Path(__file__).parent / "fixtures" / "semaforo_2026-10-14.jpeg"


class FakeResponse:
    def __init__(self, status, content=b""):
        self.status_code, self.content = status, content


class FakeSite:
    """Publishes the 2026-10-14 fixture for 14 Oct and a broken image for 13 Oct."""

    def __init__(self):
        self.urls = []

    def get(self, url, timeout):
        self.urls.append(url)
        if url.endswith("semaforo141026.jpeg"):
            return FakeResponse(200, FIXTURE.read_bytes())
        if url.endswith("semaforo131026.jpeg"):
            return FakeResponse(200, b"<html>not an image</html>")
        return FakeResponse(404)


def run(tmp_path, *extra, site=None):
    return semaforo_cli.main(
        ["--root", str(tmp_path), "fetch", "--start", "2026-10-12", "--end", "2026-10-14", *extra],
        http=site or FakeSite(),
        sleep=lambda seconds: None,
    )


def test_fetch_stores_images_and_one_row_per_river(tmp_path, capsys):
    code = run(tmp_path)

    assert code == 0
    image = tmp_path / "images" / "2026-10-14.jpeg"
    assert (
        hashlib.sha256(image.read_bytes()).hexdigest()
        == hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    )
    table = pd.read_csv(tmp_path / "states.csv")
    assert len(table) == 14
    usumacinta = table[table.row == 13].iloc[0]
    assert (usumacinta.date, usumacinta.river, usumacinta.ayer, usumacinta.hoy) == (
        "2026-10-14",
        "Usumacinta",
        "normal",
        "precaucion",
    )
    assert usumacinta.sha256 == hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    assert capsys.readouterr().out == (
        "2026-10-12: not published\n"
        "2026-10-13: unreadable (cannot identify image file)\n"
        "2026-10-14: 14 rivers, Usumacinta normal -> precaucion\n"
        "1 days read, 1 not published, 1 unreadable\n"
    )


def test_rerun_skips_days_already_read(tmp_path):
    site = FakeSite()
    run(tmp_path, site=site)
    first = len(site.urls)

    run(tmp_path, site=site)

    assert len(site.urls) - first == 2  # only the two days without a reading
    assert len(pd.read_csv(tmp_path / "states.csv")) == 14
