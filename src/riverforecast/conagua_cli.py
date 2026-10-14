"""Command line for CONAGUA station files.

uv run python -m riverforecast.conagua_cli download hidros BDCTB
uv run python -m riverforecast.conagua_cli report hidros BDCTB --column level_m
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

from riverforecast import conagua

DEFAULT_ROOT = Path("data/raw/conagua")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="riverforecast.conagua_cli")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="station file folder")
    commands = parser.add_subparsers(dest="command", required=True)

    download = commands.add_parser("download", help="download and validate station files")
    download.add_argument("kind", choices=sorted(conagua.KINDS))
    download.add_argument("codes", nargs="+", metavar="CODE")

    load = commands.add_parser("import", help="validate and store a station file saved by hand")
    load.add_argument("kind", choices=sorted(conagua.KINDS))
    load.add_argument("code")
    load.add_argument("path", type=Path)

    report = commands.add_parser("report", help="coverage and longest gaps of one column")
    report.add_argument("kind", choices=sorted(conagua.KINDS))
    report.add_argument("code")
    report.add_argument("--column", required=True)
    report.add_argument("--start")
    report.add_argument("--end")

    args = parser.parse_args(argv)
    if args.command == "download":
        failures = sum(_download(args.root, args.kind, code) for code in args.codes)
        return 1 if failures else 0
    if args.command == "import":
        text = conagua.decode_sih_bytes(args.path.read_bytes())
        return 1 if _store(args.root, args.kind, args.code, text, "rejected file") else 0
    return _report(args)


def _download(root: Path, kind: str, code: str) -> bool:
    """Download one station; return True when it failed."""
    try:
        text = conagua.download_station(kind, code)
    except conagua.DownloadError as error:
        print(
            f"{kind}/{code}: download failed: {error}; the server may be rate limiting, "
            "retry later or save the file from a browser and use 'import'",
            file=sys.stderr,
        )
        return True
    return _store(root, kind, code, text, "rejected download")


def _store(root: Path, kind: str, code: str, text: str, rejection: str) -> bool:
    """Validate a station file and store it as UTF-8; return True when rejected."""
    label = f"{kind}/{code}"
    try:
        station = conagua.parse_sih_csv(text)
    except conagua.StationFileError as error:
        print(f"{label}: {rejection}: {error}", file=sys.stderr)
        return True

    target = root / kind / f"{code}.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=target.parent, prefix=f".{code}-")
    with os.fdopen(handle, "wb") as out:
        out.write(text.encode("utf-8"))
    os.replace(temporary, target)

    index = station.data.index
    print(f"{label}: {len(index)} days, {index[0]:%Y-%m-%d} to {index[-1]:%Y-%m-%d}")
    return False


def _report(args: argparse.Namespace) -> int:
    path = args.root / args.kind / f"{args.code}.csv"
    if not path.exists():
        print(
            f"{args.kind}/{args.code}: no file at {path}; run 'download' or 'import' first",
            file=sys.stderr,
        )
        return 1
    station = conagua.parse_sih_csv(path.read_text(encoding="utf-8"))
    series = station.data[args.column]
    start = args.start or f"{series.first_valid_index():%Y-%m-%d}"
    end = args.end or f"{series.last_valid_index():%Y-%m-%d}"
    summary = conagua.gap_summary(series, start, end)

    print(
        f"{args.kind}/{args.code} {args.column} {start}..{end}: "
        f"{summary.observed} of {summary.days} days ({summary.coverage:.1%})"
    )
    for first, last, days in summary.longest_gaps:
        unit = "day" if days == 1 else "days"
        print(f"  gap {first:%Y-%m-%d}..{last:%Y-%m-%d} ({days} {unit})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
