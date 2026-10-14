"""Download IPCET semáforo images and record the river states they show.

    uv run python -m riverforecast.semaforo_cli fetch --start 2026-01-01

For each day, downloads ``semaforoDDMMYY.jpeg`` from tabasco.gob.mx, stores it
in ``data/raw/ipcet/images/<date>.jpeg`` and appends one row per river to
``data/raw/ipcet/states.csv``. Days already read are skipped; requests are
spaced by ``--pause`` seconds to be gentle with the server.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import time
from collections.abc import Callable
from pathlib import Path

import requests
from PIL import Image, UnidentifiedImageError

from riverforecast import semaforo

DEFAULT_ROOT = Path("data/raw/ipcet")
FIELDS = ["date", "row", "river", "ayer", "hoy", "sha256"]


def main(
    argv: list[str] | None = None,
    http: requests.Session | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    parser = argparse.ArgumentParser(prog="riverforecast.semaforo_cli")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    commands = parser.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("fetch", help="download and read daily semáforo images")
    fetch.add_argument("--start", type=dt.date.fromisoformat, required=True)
    fetch.add_argument("--end", type=dt.date.fromisoformat, default=dt.date.today())
    fetch.add_argument("--pause", type=float, default=1.5)
    args = parser.parse_args(argv)

    http = http or requests.Session()
    store = args.root / "states.csv"
    done = set()
    if store.exists():
        with store.open(newline="", encoding="utf-8") as handle:
            done = {row["date"] for row in csv.DictReader(handle)}
    else:
        store.parent.mkdir(parents=True, exist_ok=True)
        with store.open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(FIELDS)

    counts = {"read": 0, "missing": 0, "unreadable": 0}
    day = args.start
    while day <= args.end:
        if f"{day}" not in done:
            outcome = _fetch_day(http, day, args.root, store)
            counts[outcome] += 1
            sleep(args.pause)
        day += dt.timedelta(days=1)
    print(
        f"{counts['read']} days read, {counts['missing']} not published, "
        f"{counts['unreadable']} unreadable"
    )
    return 0


def _fetch_day(http, day: dt.date, root: Path, store: Path) -> str:
    answer = http.get(semaforo.image_url(day), timeout=60)
    if answer.status_code == 404:
        print(f"{day}: not published")
        return "missing"
    if answer.status_code != 200:
        print(f"{day}: unreadable (HTTP {answer.status_code})")
        return "unreadable"
    try:
        states = semaforo.read_states(Image.open(io.BytesIO(answer.content)))
    except (UnidentifiedImageError, semaforo.LayoutError) as error:
        print(f"{day}: unreadable ({str(error).split(' <')[0]})")
        return "unreadable"

    images = root / "images"
    images.mkdir(parents=True, exist_ok=True)
    (images / f"{day}.jpeg").write_bytes(answer.content)
    digest = hashlib.sha256(answer.content).hexdigest()
    with store.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        for state in states.values():
            writer.writerow([f"{day}", state.row, state.river, state.ayer, state.hoy, digest])
    usumacinta = states[13]
    print(f"{day}: 14 rivers, Usumacinta {usumacinta.ayer} -> {usumacinta.hoy}")
    return "read"


if __name__ == "__main__":
    raise SystemExit(main())
