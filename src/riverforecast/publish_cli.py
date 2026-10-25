"""Freeze offline deployment models, or publish from stored inputs without training."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from riverforecast.publish import (
    CemsArchiveProvider,
    OpenMeteoProvider,
    forecast,
    freeze,
    freeze_station,
    read_observations,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    fit = sub.add_parser("freeze", aliases=["fit"], help="fit frozen L1/M1 from local data")
    fit.add_argument(
        "--code",
        default="BDCTB",
        type=str.upper,
        choices=["BDCTB", "MACTB", "SAACP", "PNVTB", "TAPTB"],
    )
    fit.add_argument("--artifact", type=Path, help="default models/<code lowercase>")
    fit.add_argument("--version", help="default station's pre-registered version")
    serve = sub.add_parser(
        "publish", help="write v2 JSON from a trusted frozen artifact; never train"
    )
    serve.add_argument("--artifact", type=Path, default=Path("models/bdctb"))
    serve.add_argument("--issue", required=True, help="issue date YYYY-MM-DD; 11:00 Mexico City")
    serve.add_argument(
        "--ipcet",
        type=Path,
        required=True,
        help="validated CSV: date,stage_m,table_last_modified (explicit timezone required)",
    )
    serve.add_argument(
        "--raw",
        type=Path,
        default=Path("data/raw"),
        help="stored CEMS operational CSVs and archived day-0 inputs (no training data)",
    )
    serve.add_argument(
        "--openmeteo",
        type=Path,
        help="saved Open-Meteo forecast_v4 ensemble JSON; replaces --raw (no network)",
    )
    serve.add_argument("--out", type=Path, required=True, help="output JSON path")
    serve.add_argument(
        "--generated-at", help="explicit generation timestamp; default is issue_time"
    )
    args = parser.parse_args(argv)
    try:
        if args.command in ("freeze", "fit"):
            if args.code == "BDCTB":
                result = freeze(
                    args.artifact or Path("models/bdctb"), args.version or "bdctb-L1-postaudit-v1"
                )
            else:
                result = freeze_station(args.code, args.artifact, args.version)
            print(json.dumps(result, ensure_ascii=False))
        else:
            result = forecast(
                args.artifact,
                args.issue,
                read_observations(args.ipcet),
                OpenMeteoProvider(args.openmeteo)
                if args.openmeteo is not None
                else CemsArchiveProvider(args.raw),
                args.generated_at,
            )
            payload = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(payload, encoding="utf-8")
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
