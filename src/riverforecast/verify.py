"""Verify the published forecasts against the official IPCET tables (L60).

Each stored forecast (``bdctb/history/*.json`` on the ``forecasts`` branch) is
matched lead by lead with the stage of its target date, which is ``hoy`` of the
IPCET table of that date (``inputs/ipcet/levels.csv``, valid rows only; the
last row of a date wins). Leads whose target has no table yet stay pending and
are never scored. Persistence, the issue-day stage carried forward, is the
reference a forecast has to beat.

    uv run python -m riverforecast.verify --history bdctb/history \\
        --levels inputs/ipcet/levels.csv [--code BDCTB] [--rows rows.csv]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROW_COLUMNS = [
    "issue_date",
    "mode",
    "lead",
    "target_date",
    "q10",
    "q50",
    "q90",
    "observed",
    "error",
    "inside",
    "persistence_error",
]


def load_history(folder: str | Path) -> pd.DataFrame:
    """One row per stored forecast lead, with the issue-day stage when known."""
    rows = []
    for path in sorted(Path(folder).glob("*.json")):
        document = json.loads(path.read_text())
        if "forecasts" not in document:
            raise ValueError(f"{path.name}: missing forecasts")
        live = document.get("live_input") or {}
        for key, lead in document["forecasts"].items():
            rows.append(
                {
                    "issue_date": document["issue_time"][:10],
                    "mode": document["mode"],
                    "lead": int(key.removeprefix("h")),
                    "target_date": lead["target_date"],
                    "q10": float(lead["q10"]),
                    "q50": float(lead["q50"]),
                    "q90": float(lead["q90"]),
                    "issue_stage": live.get("stage_m"),
                }
            )
    return pd.DataFrame(rows).sort_values(["issue_date", "lead"], ignore_index=True)


def load_observed(levels: str | Path, code: str) -> dict[str, float]:
    """Official stage by table date for one station."""
    table = pd.read_csv(levels, dtype={"table_date": str, "code": str, "valid": str})
    table = table[(table["code"] == code) & (table["valid"] == "True")]
    return {date: float(hoy) for date, hoy in zip(table["table_date"], table["hoy"], strict=True)}


def match(history: pd.DataFrame, observed: dict[str, float]) -> pd.DataFrame:
    rows = history.copy()
    rows["observed"] = rows["target_date"].map(observed)
    rows["error"] = rows["q50"] - rows["observed"]
    inside = (rows["q10"] <= rows["observed"]) & (rows["observed"] <= rows["q90"])
    # Pending leads have no verdict, not a miss.
    rows["inside"] = inside.astype("boolean").mask(rows["observed"].isna())
    issue_stage = rows["issue_stage"].astype(float).fillna(rows["issue_date"].map(observed))
    rows["persistence_error"] = issue_stage - rows["observed"]
    return rows[ROW_COLUMNS]


def summarize(rows: pd.DataFrame) -> pd.DataFrame:
    """Per mode and lead: scored count, pending count and the L60 metrics."""
    summary = []
    for (mode, lead), group in rows.groupby(["mode", "lead"], sort=True):
        scored = group.dropna(subset=["observed"])
        summary.append(
            {
                "mode": mode,
                "lead": int(lead),
                "n": len(scored),
                "pending": len(group) - len(scored),
                "mae_q50": scored["error"].abs().mean(),
                "bias_q50": scored["error"].mean(),
                "coverage_q10_q90": scored["inside"].mean() if len(scored) else float("nan"),
                "mae_persistence": scored["persistence_error"].abs().mean(),
            }
        )
    return pd.DataFrame(summary)


def markdown(summary: pd.DataFrame, code: str) -> str:
    def number(value, fmt):
        return "–" if pd.isna(value) else format(value, fmt)

    lines = [
        f"{code} forecasts verified against the IPCET tables",
        "",
        "| Mode | Lead (days) | Scored | Pending | MAE q50 (m) | Bias q50 (m) "
        "| q10–q90 coverage | MAE persistence (m) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in summary.itertuples():
        lines.append(
            f"| {row.mode} | {row.lead} | {row.n} | {row.pending} "
            f"| {number(row.mae_q50, '.2f')} | {number(row.bias_q50, '+.2f')} "
            f"| {number(row.coverage_q10_q90, '.0%')} | {number(row.mae_persistence, '.2f')} |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--history", required=True, help="folder of stored forecast JSON")
    parser.add_argument("--levels", required=True, help="IPCET levels.csv")
    parser.add_argument("--code", default="BDCTB", help="station code (default: BDCTB)")
    parser.add_argument("--rows", help="also write the matched rows to this CSV")
    args = parser.parse_args(argv)
    history = Path(args.history)
    if not history.is_dir() or not any(history.glob("*.json")):
        print(f"error: no forecast history in {history}", file=sys.stderr)
        return 2
    rows = match(load_history(history), load_observed(args.levels, args.code))
    if args.rows:
        rows.to_csv(args.rows, index=False)
    print(markdown(summarize(rows), args.code))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
