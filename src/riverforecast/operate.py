"""Daily BDCTB operation: acquire one ensemble, ingest messenger files, publish.

Network access is confined to fetch_response; run/main accept a replacement fetch.
All input validation and inference finish before any checkout file is written.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import sys
import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pandas as pd
import requests

from riverforecast import levels, publish

OPENMETEO_URL = (
    "https://flood-api.open-meteo.com/v1/flood?latitude=17.425&longitude=-91.525"
    "&daily=river_discharge&models=forecast_v4&ensemble=true&past_days=10&forecast_days=10"
)
COLUMNS = ["date", "stage_m", "table_last_modified"]


def fetch_response(*, get=None) -> bytes:
    """One endpoint, 60-second timeout, at most two attempts (injectable HTTP)."""
    get = requests.get if get is None else get
    for attempt in range(2):
        try:
            reply = get(OPENMETEO_URL, timeout=60)
            reply.raise_for_status()
            return reply.content
        except (requests.RequestException, OSError):
            if attempt == 1:
                raise
    raise AssertionError("unreachable")


def utc(value=None) -> datetime:
    stamp = pd.Timestamp(value if value is not None else datetime.now(UTC))
    if stamp.tzinfo is None or pd.isna(stamp):
        raise ValueError("timestamp must include a timezone")
    return stamp.tz_convert("UTC").to_pydatetime()


def json_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()


def target(root: Path, relative: str) -> Path:
    """Reject symlinks as well as lexical escapes, including output destinations."""
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"unsafe path: {relative}")
    if any(p.is_symlink() for p in [path, *path.parents] if p != root.parent):
        raise ValueError(f"unsafe symlink: {relative}")
    return path


def triples(document, days):
    daily = document["daily"]
    result = []
    for day in sorted(days):
        index = daily["time"].index(day)
        for member in sorted(k for k in daily if k.startswith("river_discharge")):
            value = daily[member][index]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("invalid Open-Meteo fingerprint value")
            if not math.isfinite(value) or value <= 0:
                raise ValueError("invalid Open-Meteo fingerprint value")
            result.append((day, member, float(value)))
    return result


def fingerprint(document, days):
    return hashlib.sha256(json_bytes(triples(document, days))).hexdigest()


def validate(payload: bytes, label: str, scratch: Path):
    scratch.write_bytes(payload)
    publish.OpenMeteoProvider(scratch).inputs(pd.Timestamp(label))
    document = json.loads(payload)
    daily = document["daily"]
    # Fingerprint all supplied members, not only the eleven used for inference.
    for key, values in daily.items():
        if key.startswith("river_discharge") and (
            not isinstance(values, list) or len(values) != len(daily["time"])
        ):
            raise ValueError(f"invalid Open-Meteo column length: {key}")
    return document


def seed(root, response, label, now):
    date.fromisoformat(label)
    if target(root, "inputs/openmeteo/state.json").exists():
        raise ValueError("already seeded; refusing to replace run state")
    payload = Path(response).read_bytes()
    with tempfile.TemporaryDirectory(prefix="bdctb-seed-") as directory:
        doc = validate(payload, label, Path(directory) / "response.json")
    future = [day for day in doc["daily"]["time"] if day > now.date().isoformat()]
    state = {
        "run_label": label,
        "fingerprint": fingerprint(doc, future),
        "fetched_at": now.isoformat(),
    }
    write_plan(
        root,
        {
            f"inputs/openmeteo/{label}.json": payload,
            "inputs/openmeteo/state.json": json_bytes(state),
        },
    )
    return f"seeded {label}"


def image_path(root, folder, url):
    parsed = urlsplit(url)
    raw_name = parsed.path.rsplit("/", 1)[-1]
    name = unquote(raw_name)
    if (
        not name
        or name in (".", "..", "meta.json", "ingested.json", "page.html")
        or "/" in name
        or "\\" in name
        or any(ord(c) < 32 for c in name)
        or any(p in (".", "..") for p in unquote(parsed.path).split("/"))
    ):
        raise ValueError("unsafe image basename")
    return target(root, f"{folder}/{name}")


def ingest(root, issue, now, reader):
    """Cache OCR once; resolve only tables visible at this run's issue cutoff.

    Reuse station_series for zero differences, confirmations and conflicting
    readings. Re-resolve on each run from cached rows, never the research archive.
    """
    plan = {}
    records = []
    base = target(root, "inputs/ipcet")
    cutoff = pd.Timestamp(issue).tz_localize(publish.ZONE) + pd.Timedelta(hours=11)
    for path in sorted(base.glob("*/meta.json")):
        folder = path.parent.relative_to(root).as_posix()
        marker = target(root, f"{folder}/ingested.json")
        try:
            target(root, path.relative_to(root).as_posix())
            page = date.fromisoformat(path.parent.name)
            if marker.exists():
                records.extend(json.loads(marker.read_text())["rows"])
                continue
            metadata = json.loads(path.read_text())
            if utc(metadata["fetched_at"]) > now:
                continue
            rows, errors = [], []
            for image in metadata["images"]:
                try:
                    source = image_path(root, folder, image["url"])
                    content = source.read_bytes()
                    if len(content) != image["bytes"]:
                        raise ValueError("image bytes mismatch")
                    if hashlib.sha256(content).hexdigest() != image["sha256"]:
                        raise ValueError("image sha256 mismatch")
                    stamp = image["last_modified"]
                    utc(stamp)
                    table = reader(source)
                    row = next(
                        r
                        for r in table.stations
                        if r.station == "Boca del Cerro" and r.river == "Usumacinta"
                    )
                    problems = levels.row_problems(row, publish.NAMO)
                    if any(
                        v is None or not math.isfinite(v) or abs(v) > 1000
                        for v in (row.hoy, row.ayer)
                    ):
                        problems.append("implausible stage")
                    if problems:
                        raise ValueError("; ".join(problems))
                    day, flag = levels.obs_date(page, table.table_date, now.date())
                    rows.append(
                        {
                            "obs_date": str(day),
                            "obs_flag": flag,
                            "valid": True,
                            "ayer": row.ayer,
                            "hoy": row.hoy,
                            "diff": row.diff,
                            "last_modified": stamp,
                            "page_date": str(page),
                        }
                    )
                except Exception as exc:
                    # OCR libraries raise their own errors; one image must not
                    # poison every subsequent poll. Interrupts still propagate.
                    message = f"rejected {folder}: {exc}"
                    print(message, file=sys.stderr)
                    errors.append(message)
            records.extend(rows)
            plan[f"{folder}/ingested.json"] = json_bytes({"rows": rows, "errors": errors})
        except (ValueError, OSError, KeyError, TypeError) as exc:
            print(f"rejected {folder}: {exc}", file=sys.stderr)
            plan[f"{folder}/ingested.json"] = json_bytes({"rows": [], "errors": [str(exc)]})
    visible = [
        r
        for r in records
        if utc(r["last_modified"]) <= min(now, cutoff.to_pydatetime()) and r["page_date"] <= issue
    ]
    observations = publish.empty_observations()
    if visible:
        series = levels.station_series(pd.DataFrame(visible))
        output = []
        for day, row in series.iterrows():
            if day > pd.Timestamp(issue):
                continue
            contributors = [
                r
                for r in visible
                if pd.Timestamp(r["obs_date"]) in (day, day + pd.Timedelta(days=1))
            ]
            # Untrusted-date confirmation can involve the table's other day.
            # Conservatively delay it to the newest visible table, never backdate.
            if any(r["obs_flag"] not in levels.TRUSTED_FLAGS for r in contributors):
                contributors = visible
            stamp = max(contributors, key=lambda r: utc(r["last_modified"]))["last_modified"]
            output.append(
                {"date": f"{day:%Y-%m-%d}", "stage_m": row.stage_m, "table_last_modified": stamp}
            )
        observations = pd.DataFrame(output, columns=COLUMNS)
    buffer = io.StringIO()
    observations.to_csv(buffer, index=False, lineterminator="\n")
    csv = buffer.getvalue().encode()
    csv_path = target(root, "inputs/ipcet/bdctb_levels.csv")
    if not csv_path.exists() or csv_path.read_bytes() != csv:
        plan["inputs/ipcet/bdctb_levels.csv"] = csv
    return observations, plan, hashlib.sha256(csv).hexdigest()


def write_plan(root, plan):
    # Preflight every destination before creating anything. Input/inference errors
    # cannot leave a partial ingest, state update or publication behind.
    paths = {target(root, name): content for name, content in plan.items()}
    backups = {path: path.read_bytes() if path.exists() else None for path in paths}
    written = []
    try:
        for path, content in paths.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            written.append(path)
            path.write_bytes(content)
    except OSError:
        for path in reversed(written):
            if backups[path] is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(backups[path])
        raise


def run(root, artifact, now, *, fetch, reader, forecaster):
    state_path = target(root, "inputs/openmeteo/state.json")
    if not state_path.exists():
        raise ValueError("missing Open-Meteo state; use operate seed first")
    state = json.loads(state_path.read_text())
    previous = date.fromisoformat(state["run_label"])
    if previous > now.date():
        raise ValueError("run label is after fetch date")
    saved = target(root, f"inputs/openmeteo/{previous}.json")
    payload = fetch()
    plan = {}
    with tempfile.TemporaryDirectory(prefix="bdctb-operate-") as directory:
        scratch = Path(directory) / "response.json"
        new = validate(payload, now.date().isoformat(), scratch)
        old = validate(saved.read_bytes(), str(previous), Path(directory) / "previous.json")
        future = {day for day in new["daily"]["time"] if day > now.date().isoformat()}
        shared = future.intersection(old["daily"]["time"])
        if not shared:
            raise ValueError("no shared future Open-Meteo dates; cannot infer run")
        old_members = {key for key in old["daily"] if key.startswith("river_discharge")}
        new_members = {key for key in new["daily"] if key.startswith("river_discharge")}
        if old_members != new_members:
            raise ValueError("Open-Meteo member set changed")
        changed = fingerprint(old, shared) != fingerprint(new, shared)
        label = previous
        if changed:
            label = now.date()
            validate(payload, str(label), scratch)
            revision = label == previous
            revisions = state.get("revisions", 0) + 1 if revision else 0
            inferred = (
                state.get("run_label_inferred", False)
                if revision
                else (previous + timedelta(days=1) != label)
            )
            state = {
                "run_label": str(label),
                "fingerprint": fingerprint(new, future),
                "fetched_at": now.isoformat(),
                "revisions": revisions,
            }
            if inferred:
                state["run_label_inferred"] = True
            plan[f"inputs/openmeteo/{label}.json"] = payload
            plan["inputs/openmeteo/state.json"] = json_bytes(state)
            kind = "revision" if revision else "inferred" if inferred else "new"
            log_name = "inputs/openmeteo/detections.csv"
            log_path = target(root, log_name)
            history = log_path.read_bytes() if log_path.exists() else b"run_label,fetched_at,kind\n"
            plan[log_name] = history + f"{label},{now.isoformat()},{kind}\n".encode()
        observations, ingest_plan, digest = ingest(root, str(label), now, reader)
        plan.update(ingest_plan)
        receipt_path = target(root, "bdctb/published.json")
        receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
        should_publish = (
            changed
            or not target(root, "bdctb/latest.json").exists()
            or receipt.get("observations_sha256") != digest
        )
        if should_publish:
            provider = publish.OpenMeteoProvider(scratch if changed else saved)
            result = forecaster(
                artifact,
                issue=str(label),
                observations=observations,
                provider=provider,
                generated_at=now.isoformat(),
            )
            output = json_bytes(result)
            plan["bdctb/latest.json"] = output
            plan[f"bdctb/history/{label}.json"] = output
            plan["bdctb/published.json"] = json_bytes(
                {"run_label": str(label), "observations_sha256": digest}
            )
            summary = f"published {label} {result['mode']} ({'new run' if changed else 'same run'})"
        else:
            summary = "ingested inputs; forecast unchanged" if plan else "no change"
    write_plan(root, plan)
    return summary


def main(argv=None, *, fetch=None, reader=None, forecaster=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "seed"):
        command = sub.add_parser(
            name, help="publish changed inputs" if name == "run" else "bootstrap a known run"
        )
        command.add_argument("--data", type=Path, required=True, help="forecasts branch checkout")
        command.add_argument("--now", help="timezone-aware ISO timestamp; default current UTC")
        if name == "run":
            command.add_argument("--artifact", type=Path, default=Path("models/bdctb"))
        else:
            command.add_argument("--response", type=Path, required=True)
            command.add_argument(
                "--run-label", required=True, help="independently established YYYY-MM-DD run"
            )
    args = parser.parse_args(argv)
    try:
        now = utc(args.now)
        if args.command == "seed":
            summary = seed(args.data, args.response, args.run_label, now)
        else:
            summary = run(
                args.data,
                args.artifact,
                now,
                fetch=fetch or fetch_response,
                reader=reader or levels.read_table,
                forecaster=forecaster or publish.forecast,
            )
        print(summary)
        return 0
    except (ValueError, OSError, KeyError, TypeError, requests.RequestException) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
