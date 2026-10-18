"""Read the stored IPCET daily level-table images into one long CSV.

    uv run python -m riverforecast.levels_cli read data/raw/ipcet/levels \
        --out data/raw/ipcet/levels.csv

Every image in the directory is classified and read (OCR); images that are
not level tables are refused. Rows carry the page date (file name), the
table date (FECHA line), the resolved observation date (the OCR day and
month when they are within 3 days of the page date, with the year from the
page date; the page date when FECHA is unreadable; flagged "far_off" when
the OCR date is further away) and the parsed values with their validation
against the table's own arithmetic and the station's modal NAMO. Values are
never repaired.

    uv run python -m riverforecast.levels_cli report data/raw/ipcet/levels \
        --csv data/raw/ipcet/levels.csv

Prints, per year, how many tables were read or refused and how many Boca del
Cerro rows are valid.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from riverforecast import levels

FIELDS = [
    "page_date",
    "table_date",
    "obs_date",
    "obs_flag",
    "kind",
    "river",
    "station",
    "namo",
    "ayer",
    "hoy",
    "diff",
    "capacity",
    "valid",
    "reason",
    "sha256",
]


def _read_one(path: str) -> tuple[str, levels.LevelTable | None, str]:
    """Worker: read one image; return (path, table, error)."""
    try:
        return (path, levels.read_table(path), "")
    except levels.UnreadableTable as error:
        return (path, None, str(error))
    except Exception as error:  # noqa: BLE001 - keep the batch running
        return (path, None, f"{type(error).__name__}: {error}")


def _fmt(value: float | None) -> str:
    return "" if value is None else f"{value:g}"


def _rows(
    table: levels.LevelTable, modes: dict[tuple[str, str], float], sha256: str
) -> list[list[str]]:
    out = []
    obs, flag = levels.obs_date(table.page_date, table.table_date)
    for kind, rows in (("station", table.stations), ("dam", table.dams)):
        for row in rows:
            name = row.station if kind == "station" else row.name
            river = row.river if kind == "station" else ""
            problems = levels.row_problems(row, modes.get((kind, name)))
            out.append(
                [
                    _fmt_date(table.page_date),
                    _fmt_date(table.table_date),
                    _fmt_date(obs),
                    flag,
                    kind,
                    river,
                    name,
                    _fmt(row.namo),
                    _fmt(row.ayer),
                    _fmt(row.hoy),
                    _fmt(row.diff),
                    _fmt(row.capacity),
                    str(not problems),
                    ";".join(problems),
                    sha256,
                ]
            )
    return out


def _fmt_date(day: dt.date | None) -> str:
    return "" if day is None else f"{day}"


def cmd_read(root: Path, out: Path, workers: int) -> int:
    paths = sorted(root.glob("*.jpg"))
    read = 0
    refused: list[str] = []
    tables: list[levels.LevelTable] = []
    digests: dict[str, str] = {}
    # spawn instead of fork: forked workers hang sporadically around the OCR
    # subprocesses, and the re-import cost is negligible against ~13 s/image
    ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as pool:
        for path, table, error in pool.map(_read_one, [str(p) for p in paths]):
            page = Path(path).stem
            digests[page] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
            if table is None:
                refused.append(f"{page} ({error})")
                continue
            read += 1
            tables.append(table)
    modes = levels.modal_namos(tables)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(FIELDS)
        for table in sorted(tables, key=lambda t: t.page_date or dt.date.min):
            page = f"{table.page_date}" if table.page_date else Path("?").stem
            sha = digests.get(f"{table.page_date}", "")
            for row in _rows(table, modes, sha):
                writer.writerow(row)
    print(f"{read} tables read, {len(refused)} refused")
    for line in refused:
        print(f"  refused: {line}")
    return 0


def cmd_report(root: Path, csv_path: Path) -> int:
    pages_with_rows: set[str] = set()
    boca: dict[str, list[str]] = {}
    with csv_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            pages_with_rows.add(row["page_date"])
            if row["kind"] == "station" and row["station"] == "Boca del Cerro":
                boca.setdefault(row["page_date"][:4], []).append(row["valid"])
    counts: dict[int, dict[str, int]] = {}
    for path in sorted(root.glob("*.jpg")):
        try:
            year = int(path.stem[:4])
        except ValueError:
            continue
        stat = counts.setdefault(year, {"read": 0, "refused": 0})
        if path.stem in pages_with_rows:
            stat["read"] += 1
        else:
            stat["refused"] += 1
    for year, stat in sorted(counts.items()):
        valid = sum(1 for v in boca.get(str(year), []) if v == "True")
        total = len(boca.get(str(year), []))
        print(
            f"{year}: {stat['read']} read, {stat['refused']} refused, "
            f"Boca del Cerro valid {valid}/{total}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="riverforecast.levels_cli")
    commands = parser.add_subparsers(dest="command", required=True)
    read = commands.add_parser("read", help="read all level-table images into a CSV")
    read.add_argument("root", type=Path)
    read.add_argument("--out", type=Path, required=True)
    read.add_argument("--workers", type=int, default=1)
    read.set_defaults(func=lambda args: cmd_read(args.root, args.out, args.workers))
    report = commands.add_parser("report", help="per-year counts of read/refused tables")
    report.add_argument("root", type=Path)
    report.add_argument("--csv", type=Path, required=True)
    report.set_defaults(func=lambda args: cmd_report(args.root, args.csv))
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
