"""Experiment 11: readability and fidelity of the IPCET daily level tables.

Checks (3) and (4) pre-registered in L30, computed from the reader output
(``data/raw/ipcet/levels.csv``, written by ``levels_cli read``); this script
never runs the reader itself.

Check (3), readability: tables read and refused per year, the share of valid
Boca del Cerro rows (printed arithmetic holds: diff = hoy - ayer and
capacity = NAMO - hoy), how the observation date was resolved, and the
continuity of consecutive tables: yesterday's value printed on day D must equal
today's value printed on day D-1. The reader picks among OCR readings the one
that matches the printed arithmetic, so validity alone is not independent
evidence; continuity and check (4) are.

Check (4), fidelity: valid Boca del Cerro 06:00 values against the CONAGUA SIH
daily stage (``data.stage("BDCTB")``, which ends 2025-09-03) on common days.

It also writes ``data/raw/ipcet/bdctb_levels.csv``, the daily Boca del Cerro
series from the tables: today's value of day D, or else yesterday's value
printed on day D+1, valid rows only.

    uv run python experiments/11_levels_check.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

from riverforecast import data, levels

LEVELS = data.RAW / "ipcet" / "levels.csv"
SERIES = data.RAW / "ipcet" / "bdctb_levels.csv"
LOG = Path(".cache/levels-read-v2.log")
OUT = Path("docs/results")
STATION = "Boca del Cerro"
DOWNLOADED = pd.Timestamp("2026-10-17").date()


def load() -> pd.DataFrame:
    """Reader output with the observation date re-resolved by ``levels.obs_date``
    from the page and printed dates (no OCR is repeated)."""
    table = pd.read_csv(LEVELS, parse_dates=["page_date", "table_date"])
    table["valid"] = table["valid"].astype(str).str.lower() == "true"
    resolved = [
        levels.obs_date(page.date(), None if pd.isna(printed) else printed.date(), DOWNLOADED)
        for page, printed in zip(table["page_date"], table["table_date"], strict=True)
    ]
    table["obs_date"] = pd.to_datetime([day for day, _ in resolved])
    table["obs_flag"] = [flag for _, flag in resolved]
    return table


def refusals(table: pd.DataFrame) -> dict[str, int]:
    """Refused images per year: manifest dates without any row in the CSV."""
    manifest = pd.read_csv(data.RAW / "ipcet" / "levels" / "manifest.csv", parse_dates=["date"])
    read = set(table["page_date"])
    missing = manifest.loc[~manifest["date"].isin(read), "date"]
    return {str(y): int(n) for y, n in missing.dt.year.value_counts().sort_index().items()}


def boca(table: pd.DataFrame) -> pd.DataFrame:
    rows = table[(table["kind"] == "station") & table["station"].str.contains("Boca", na=False)]
    return rows.copy()


def readability(table: pd.DataFrame) -> dict:
    rows = boca(table)
    per_year = {}
    for year, part in rows.groupby(rows["page_date"].dt.year):
        per_year[str(year)] = {
            "tables_read": int(part["page_date"].nunique()),
            "bdc_valid": int(part["valid"].sum()),
        }
    refused = refusals(table)
    for year, n in refused.items():
        per_year.setdefault(year, {"tables_read": 0, "bdc_valid": 0})["refused"] = n
    for entry in per_year.values():
        entry.setdefault("refused", 0)
    trusted = rows["obs_flag"].isin(levels.TRUSTED_FLAGS)
    valid = rows[rows["valid"] & trusted].drop_duplicates("obs_date")
    valid = valid.set_index("obs_date").sort_index()
    previous = valid["hoy"].reindex(valid.index - pd.Timedelta(days=1)).to_numpy()
    gap = pd.Series(valid["ayer"].to_numpy() - previous, index=valid.index).dropna()
    worst = gap.abs().sort_values(ascending=False).head(10)
    return {
        "per_year": per_year,
        "bdc_rows": int(len(rows)),
        "bdc_valid": int(rows["valid"].sum()),
        "obs_flag": {str(k): int(v) for k, v in rows["obs_flag"].value_counts().items()},
        "continuity_pairs": int(len(gap)),
        "continuity_exact": int((gap.abs() <= 0.005).sum()),
        "continuity_within_005": int((gap.abs() <= 0.05).sum()),
        "continuity_worst": {d.date().isoformat(): round(float(v), 2) for d, v in worst.items()},
    }


def daily_series(table: pd.DataFrame) -> pd.DataFrame:
    """Boca del Cerro daily stage built by ``levels.station_series``."""
    return levels.station_series(boca(table))


def fidelity(series: pd.DataFrame) -> dict:
    stage = data.stage("BDCTB")
    joined = pd.concat({"ipcet": series["stage_m"], "sih": stage}, axis=1, sort=True).dropna()
    diff = joined["ipcet"] - joined["sih"]
    worst = diff.abs().sort_values(ascending=False).head(10)
    return {
        "n": int(len(joined)),
        "period": [joined.index.min().date().isoformat(), joined.index.max().date().isoformat()],
        "mae": round(float(diff.abs().mean()), 4),
        "bias": round(float(diff.mean()), 4),
        "share_within_005": round(float((diff.abs() <= 0.05).mean()), 3),
        "share_exact": round(float((diff.abs() <= 0.005).mean()), 3),
        "worst": {
            d.date().isoformat(): {
                "ipcet": float(joined.loc[d, "ipcet"]),
                "sih": float(joined.loc[d, "sih"]),
            }
            for d in worst.index
        },
    }


def main() -> None:
    table = load()
    series = daily_series(table)
    series.to_csv(SERIES)
    counts = {str(y): int(n) for y, n in series.index.year.value_counts().sort_index().items()}
    result = {
        "readability": readability(table),
        "fidelity": fidelity(series),
        "series_days_per_year": counts,
        "series_last_date": series.index.max().date().isoformat(),
    }
    if LOG.exists():
        reasons = re.findall(r"refused: \S+ \((.*)\)", LOG.read_text())
        result["refusal_reasons"] = (
            pd.Series([re.sub(r"\d+", "N", r) for r in reasons]).value_counts().to_dict()
        )
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "11-levels-check.json").write_text(json.dumps(result, indent=2) + "\n")
    write_report(result)
    print(json.dumps(result, indent=2))


def write_report(result: dict) -> None:
    read, fid = result["readability"], result["fidelity"]
    lines = [
        "# Experiment 11: IPCET daily level tables, readability and fidelity",
        "",
        "Generated by `experiments/11_levels_check.py` from `data/raw/ipcet/levels.csv`.",
        "Checks (3) and (4) pre-registered in L30 of `odd/tasks/usumacinta-forecast.md`.",
        "",
        "## Check (3): readability",
        "",
        "| year | tables read | refused | Boca del Cerro valid |",
        "| --- | --- | --- | --- |",
    ]
    for year, entry in sorted(read["per_year"].items()):
        lines.append(
            f"| {year} | {entry['tables_read']} | {entry['refused']} | {entry['bdc_valid']} |"
        )
    lines += [
        "",
        f"Boca del Cerro rows: {read['bdc_rows']}, valid {read['bdc_valid']}. "
        f"Observation date resolution: {read['obs_flag']}.",
        "",
        f"Continuity (yesterday's value on day D equals today's value on D-1): "
        f"{read['continuity_exact']} of {read['continuity_pairs']} pairs exact, "
        f"{read['continuity_within_005']} within 0.05 m. Worst: {read['continuity_worst']}.",
        "",
        "## Check (4): agreement with CONAGUA SIH",
        "",
        f"{fid['n']} common days ({fid['period'][0]}..{fid['period'][1]}): "
        f"MAE {fid['mae']} m, bias {fid['bias']} m, "
        f"{fid['share_within_005']:.1%} within 0.05 m, {fid['share_exact']:.1%} exact.",
        "",
        "| date | IPCET 06:00 | SIH daily |",
        "| --- | --- | --- |",
    ]
    for day, pair in fid["worst"].items():
        lines.append(f"| {day} | {pair['ipcet']:.2f} | {pair['sih']:.2f} |")
    lines += [
        "",
        f"Daily Boca del Cerro series written to `data/raw/ipcet/bdctb_levels.csv` "
        f"(last date {result['series_last_date']}); days per year: "
        f"{result['series_days_per_year']}.",
    ]
    if "refusal_reasons" in result:
        lines += ["", f"Refusal reasons: {result['refusal_reasons']}."]
    (OUT / "11-levels-check.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
