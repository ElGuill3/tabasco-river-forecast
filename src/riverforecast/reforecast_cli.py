"""Import GloFAS v4 reforecasts saved by the first research repository.

That repository was deleted; its data is kept locally under
``data/raw/legacy-research/data-external``.

    uv run python -m riverforecast.reforecast_cli import BDCTB \\
        data/raw/legacy-research/data-external/glofas-v4-reforecast/normalized \\
        data/raw/legacy-research/data-external/glofas-v4-reforecast-h1-holdout/normalized

Writes ``<root>/glofas/<CODE>/reforecast_v4.csv`` with one row per issue, valid
day and ensemble member.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from riverforecast import reforecast

DEFAULT_ROOT = Path("data/raw")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="riverforecast.reforecast_cli")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="raw data folder")
    commands = parser.add_subparsers(dest="command", required=True)
    load = commands.add_parser("import", help="combine reforecast folders into one table")
    load.add_argument("code")
    load.add_argument("folders", nargs="+", type=Path)
    args = parser.parse_args(argv)

    try:
        cells = sorted({reforecast.folder_cell(folder) for folder in args.folders})
        if len(cells) > 1:
            raise reforecast.ReforecastError(
                f"folders come from different cells: {' and '.join(cells)}"
            )
        table = pd.concat([reforecast.read_normalized(folder) for folder in args.folders])
        duplicated = table.duplicated(["issue", "valid", "member"])
        if duplicated.any():
            first = table[duplicated].iloc[0]
            raise reforecast.ReforecastError(
                f"duplicate record across folders {first.issue} {first.valid} {first.member}"
            )
    except reforecast.ReforecastError as error:
        print(f"{args.code}: {error}", file=sys.stderr)
        return 1

    table = table.sort_values(["issue", "lead_days", "member"])
    target = args.root / "glofas" / args.code / "reforecast_v4.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(target, index=False)
    print(
        f"{args.code}: {len(table)} records, {table.issue.nunique()} issues "
        f"({table.issue.min()} to {table.issue.max()}), "
        f"leads {sorted(int(v) for v in table.lead_days.unique())} days, "
        f"{table.member.nunique()} members"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
