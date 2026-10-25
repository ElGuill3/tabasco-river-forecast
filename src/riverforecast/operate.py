"""Hourly river operation: acquire five cells, ingest messenger files, publish.

Every ingested IPCET level table is also published for all stations (ipcet/*.json).

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

from riverforecast import levels, publish, stations

OPENMETEO_URL = (
    "https://flood-api.open-meteo.com/v1/flood?latitude=17.425&longitude=-91.525"
    "&daily=river_discharge&models=forecast_v4&ensemble=true&past_days=10&forecast_days=10"
)
COLUMNS = ["date", "stage_m", "table_last_modified"]
LEVEL_COLUMNS = [
    "table_date",
    "code",
    "river",
    "station",
    "namo",
    "ayer",
    "hoy",
    "diff",
    "semaforo",
    "valid",
]
RECENT_DATES = 30


def fetch_response(*, get=None, url=OPENMETEO_URL) -> bytes:
    """One endpoint, 60-second timeout, at most two attempts (injectable HTTP)."""
    get = requests.get if get is None else get
    for attempt in range(2):
        try:
            reply = get(url, timeout=60)
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


def seed(root, response, label, now, input_dir="inputs/openmeteo", *, plan_only=False):
    date.fromisoformat(label)
    if target(root, f"{input_dir}/state.json").exists():
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
    plan = {
        f"{input_dir}/{label}.json": payload,
        f"{input_dir}/state.json": json_bytes(state),
    }
    if plan_only:
        return plan
    write_plan(root, plan)
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


def verified_image(root, folder, image):
    """Path and Last-Modified of an uploaded image whose bytes match its metadata."""
    source = image_path(root, folder, image["url"])
    content = source.read_bytes()
    if len(content) != image["bytes"]:
        raise ValueError("image bytes mismatch")
    if hashlib.sha256(content).hexdigest() != image["sha256"]:
        raise ValueError("image sha256 mismatch")
    stamp = image["last_modified"]
    utc(stamp)
    return source, stamp


def read_once(reader):
    """Reader that OCRs each image once per run, replaying its result or error."""
    results = {}

    def read(path):
        key = Path(path)
        if key not in results:
            try:
                results[key] = (True, reader(path))
            except Exception as exc:
                results[key] = (False, exc)
        ok, value = results[key]
        if not ok:
            raise value
        return value

    return read


def ingest(root, issue, now, reader, code="BDCTB"):
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
        marker_name = "ingested.json" if code == "BDCTB" else f"ingested_{code.lower()}.json"
        marker = target(root, f"{folder}/{marker_name}")
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
                    source, stamp = verified_image(root, folder, image)
                    table = reader(source)
                    row = next(
                        r
                        for r in table.stations
                        if (
                            (r.station == "Boca del Cerro" and r.river == "Usumacinta")
                            if code == "BDCTB"
                            else stations.code_for(r.river, r.station) == code
                        )
                    )
                    problems = levels.row_problems(row, stations.NAMO[code])
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
            plan[f"{folder}/{marker_name}"] = json_bytes({"rows": rows, "errors": errors})
        except (ValueError, OSError, KeyError, TypeError) as exc:
            print(f"rejected {folder}: {exc}", file=sys.stderr)
            plan[f"{folder}/{marker_name}"] = json_bytes({"rows": [], "errors": [str(exc)]})
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
    csv_name = f"inputs/ipcet/{code.lower()}_levels.csv"
    csv_path = target(root, csv_name)
    if not csv_path.exists() or csv_path.read_bytes() != csv:
        plan[csv_name] = csv
    return observations, plan, hashlib.sha256(csv).hexdigest()


def finite(value):
    return value if isinstance(value, (int, float)) and math.isfinite(value) else None


def station_record(row):
    record = {
        "code": stations.code_for(row.river, row.station),
        "river": row.river,
        "station": row.station,
        **{
            f: finite(getattr(row, f))
            for f in ("namo", "namino", "ayer", "hoy", "diff", "capacity")
        },
        "semaforo": row.semaforo,
    }
    return record | {"problems": levels.row_problems(row, None)}


def official_tables(root, now, reader):
    """Every station of every uploaded level table, read once per folder.

    Separate from ingest, whose cached BDCTB rows stay untouched. Errors are kept
    in tables.json only: ingest already reports rejected folders on stderr.
    """
    plan, found = {}, []
    for path in sorted(target(root, "inputs/ipcet").glob("*/meta.json")):
        folder = path.parent.relative_to(root).as_posix()
        marker = target(root, f"{folder}/tables.json")
        try:
            target(root, path.relative_to(root).as_posix())
            page = date.fromisoformat(path.parent.name)
            if marker.exists():
                found.extend(json.loads(marker.read_text())["tables"])
                continue
            metadata = json.loads(path.read_text())
            if utc(metadata["fetched_at"]) > now:
                continue
            tables, errors = [], []
            for image in metadata["images"]:
                try:
                    source, stamp = verified_image(root, folder, image)
                    table = reader(source)
                    day, flag = levels.obs_date(page, table.table_date, now.date())
                    if flag not in levels.TRUSTED_FLAGS:
                        raise ValueError(f"untrusted table date ({flag})")
                    tables.append(
                        {
                            "table_date": str(day),
                            "last_modified": stamp,
                            "source_url": metadata.get("page_url"),
                            "stations": [station_record(r) for r in table.stations],
                        }
                    )
                except Exception as exc:
                    errors.append(str(exc))
            found.extend(tables)
            plan[f"{folder}/tables.json"] = json_bytes({"tables": tables, "errors": errors})
        except (ValueError, OSError, KeyError, TypeError) as exc:
            plan[f"{folder}/tables.json"] = json_bytes({"tables": [], "errors": [str(exc)]})
    return plan, found


def level_key(record):
    return (record["table_date"], record["code"] or f"{record['river']}|{record['station']}")


def seeded_levels(root):
    path = target(root, "inputs/ipcet/levels_seed.csv")
    if not path.exists():
        return []
    frame = pd.read_csv(path, dtype={"code": str, "semaforo": str}, keep_default_na=False)
    records = []
    for raw in frame.to_dict("records"):
        record = {k: (None if raw[k] == "" else raw[k]) for k in LEVEL_COLUMNS}
        for field in ("namo", "ayer", "hoy", "diff"):
            record[field] = None if record[field] is None else float(record[field])
        record["valid"] = str(raw["valid"]) == "True"
        records.append(record)
    return records


def publish_levels(root, now, reader):
    """Plan for tables.json markers, inputs/ipcet/levels.csv and ipcet/*.json.

    Regenerated from the seed and the cached tables on every run, so a run
    without a new table writes nothing.
    """
    plan, found = official_tables(root, now, reader)
    found = sorted(found, key=lambda t: (t["table_date"], utc(t["last_modified"])))
    merged = {level_key(r): r for r in seeded_levels(root)}
    for table in found:
        for s in table["stations"]:
            record = {k: s.get(k) for k in LEVEL_COLUMNS if k not in ("table_date", "valid")}
            record |= {"table_date": table["table_date"], "valid": not s["problems"]}
            merged[level_key(record)] = record
    rows = sorted(merged.values(), key=lambda r: r["table_date"])
    outputs = {}
    if rows:
        buffer = io.StringIO()
        frame = pd.DataFrame(rows, columns=LEVEL_COLUMNS)
        frame.to_csv(buffer, index=False, lineterminator="\n")
        outputs["inputs/ipcet/levels.csv"] = buffer.getvalue().encode()
        series = {}
        for r in rows:
            if r["code"]:
                point = {
                    "date": r["table_date"],
                    "hoy": r["hoy"] if r["valid"] else None,
                    "semaforo": r["semaforo"] or None,
                }
                series.setdefault(r["code"], []).append(point)
        recent = {code: points[-RECENT_DATES:] for code, points in series.items()}
        outputs["ipcet/recent.json"] = json_bytes({"schema": "ipcet-recent/v1", "stations": recent})
    if found:
        newest = found[-1]
        issued = pd.Timestamp(newest["table_date"]).tz_localize(publish.ZONE) + pd.Timedelta(
            hours=6
        )
        outputs["ipcet/latest.json"] = json_bytes(
            {
                "schema": "ipcet-levels/v1",
                "table_date": newest["table_date"],
                "issued_at": issued.isoformat(),
                "last_modified": newest["last_modified"],
                "source_url": newest["source_url"],
                "stations": newest["stations"],
            }
        )
    for name, content in outputs.items():
        path = target(root, name)
        if not path.exists() or path.read_bytes() != content:
            plan[name] = content
    return plan


def seed_levels(root, source, since):
    """Bootstrap level history from the research OCR archive (no semáforo)."""
    since = date.fromisoformat(since).isoformat()
    path = target(root, "inputs/ipcet/levels_seed.csv")
    if path.exists():
        raise ValueError("levels already seeded; refusing to replace them")
    archive = pd.read_csv(source, dtype=str, keep_default_na=False)
    keep = archive[
        (archive["kind"] == "station")
        & (archive["valid"] == "True")
        & archive["obs_flag"].isin(levels.TRUSTED_FLAGS)
        & (archive["obs_date"] >= since)
    ]
    merged = {}
    for raw in keep.to_dict("records"):
        record = {
            "table_date": raw["obs_date"],
            "code": stations.code_for(raw["river"], raw["station"]),
            "river": raw["river"],
            "station": raw["station"],
            **{f: float(raw[f]) for f in ("namo", "ayer", "hoy", "diff")},
            "semaforo": None,
            "valid": True,
        }
        merged[level_key(record)] = record
    rows = sorted(merged.values(), key=lambda r: r["table_date"])
    buffer = io.StringIO()
    pd.DataFrame(rows, columns=LEVEL_COLUMNS).to_csv(buffer, index=False, lineterminator="\n")
    write_plan(root, {"inputs/ipcet/levels_seed.csv": buffer.getvalue().encode()})
    return f"seeded {len(rows)} level rows"


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


def run(root, artifact, now, *, fetch, reader, forecaster, code="BDCTB", initial_plan=None):
    output_dir = code.lower()
    input_dir = "inputs/openmeteo" if code == "BDCTB" else f"inputs/openmeteo/{output_dir}"
    plan = dict(initial_plan or {})
    state_name = f"{input_dir}/state.json"
    state_path = target(root, state_name)
    if not state_path.exists() and state_name not in plan:
        raise ValueError("missing Open-Meteo state; use operate seed first")
    state = json.loads(plan[state_name] if state_name in plan else state_path.read_bytes())
    previous = date.fromisoformat(state["run_label"])
    if previous > now.date():
        raise ValueError("run label is after fetch date")
    saved_name = f"{input_dir}/{previous}.json"
    saved = target(root, saved_name)
    reader = read_once(reader)
    payload = fetch()
    with tempfile.TemporaryDirectory(prefix="bdctb-operate-") as directory:
        scratch = Path(directory) / "response.json"
        new = validate(payload, now.date().isoformat(), scratch)
        old = validate(
            plan[saved_name] if saved_name in plan else saved.read_bytes(),
            str(previous),
            Path(directory) / "previous.json",
        )
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
            plan[f"{input_dir}/{label}.json"] = payload
            plan[f"{input_dir}/state.json"] = json_bytes(state)
            kind = "revision" if revision else "inferred" if inferred else "new"
            log_name = f"{input_dir}/detections.csv"
            log_path = target(root, log_name)
            history = log_path.read_bytes() if log_path.exists() else b"run_label,fetched_at,kind\n"
            plan[log_name] = history + f"{label},{now.isoformat()},{kind}\n".encode()
        observations, ingest_plan, digest = ingest(root, str(label), now, reader, code=code)
        plan.update(ingest_plan)
        if code == "BDCTB":
            plan.update(publish_levels(root, now, reader))
        receipt_path = target(root, f"{output_dir}/published.json")
        receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
        should_publish = (
            changed
            or not target(root, f"{output_dir}/latest.json").exists()
            or receipt.get("observations_sha256") != digest
        )
        if should_publish:
            provider = publish.OpenMeteoProvider(scratch if changed or initial_plan else saved)
            result = forecaster(
                artifact,
                issue=str(label),
                observations=observations,
                provider=provider,
                generated_at=now.isoformat(),
            )
            output = json_bytes(result)
            plan[f"{output_dir}/latest.json"] = output
            plan[f"{output_dir}/history/{label}.json"] = output
            plan[f"{output_dir}/published.json"] = json_bytes(
                {"run_label": str(label), "observations_sha256": digest}
            )
            summary = f"published {label} {result['mode']} ({'new run' if changed else 'same run'})"
        else:
            summary = "ingested inputs; forecast unchanged" if plan else "no change"
    write_plan(root, plan)
    return summary


def fetch_all_response():
    """One multi-cell request, in the fixed L67 station order; no longitude conversion."""
    cells = [stations.CELLS[code] for code in stations.PUBLISHED]
    url = (
        "https://flood-api.open-meteo.com/v1/flood?latitude="
        + ",".join(f"{lat:.3f}" for lat, _ in cells)
        + "&longitude="
        + ",".join(f"{lon:.3f}" for _, lon in cells)
        + "&daily=river_discharge&models=forecast_v4&ensemble=true&past_days=10&forecast_days=10"
    )
    return fetch_response(url=url)


def run_all(root, artifact, now, *, fetch, reader, forecaster):
    payload = fetch()
    documents = json.loads(payload)
    reader = read_once(reader)
    if not isinstance(documents, list):
        # Original injected single-cell interface remains available for offline replay.
        return run(root, artifact, now, fetch=lambda: payload, reader=reader, forecaster=forecaster)
    if len(documents) != len(stations.PUBLISHED):
        raise ValueError("Open-Meteo response must contain five station cells")
    summaries = []
    succeeded = 0
    bdctb_failed = False
    for code, document in zip(stations.PUBLISHED, documents, strict=True):
        try:
            content = json_bytes(document)
            initial_plan = None
            if code != "BDCTB":
                input_dir = f"inputs/openmeteo/{code.lower()}"
                if not target(root, f"{input_dir}/state.json").exists():
                    # Adopt the established BDCTB label, not an independently guessed date.
                    state = json.loads(target(root, "inputs/openmeteo/state.json").read_text())
                    with tempfile.TemporaryDirectory(prefix="station-seed-") as directory:
                        source = Path(directory) / "response.json"
                        source.write_bytes(content)
                        initial_plan = seed(
                            root,
                            source,
                            state["run_label"],
                            now,
                            input_dir=input_dir,
                            plan_only=True,
                        )
            summary = run(
                root,
                artifact if code == "BDCTB" else Path(f"models/{code.lower()}"),
                now,
                fetch=lambda content=content: content,
                reader=reader,
                forecaster=forecaster,
                code=code,
                initial_plan=initial_plan,
            )
            summaries.append(f"{code}: {summary}")
            succeeded += 1
        except Exception as exc:
            summaries.append(f"{code}: failed: {exc}")
            if code == "BDCTB":
                bdctb_failed = True
    # A BDCTB failure still fails the run, as before the new stations; the
    # other stations only add to it and never mask its failure.
    if not succeeded or bdctb_failed:
        raise ValueError("; ".join(summaries))
    return "; ".join(summaries)


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
    levels_seed = sub.add_parser(
        "seed-levels", help="bootstrap station level history from the OCR archive"
    )
    levels_seed.add_argument("--data", type=Path, required=True, help="forecasts branch checkout")
    levels_seed.add_argument(
        "--levels", type=Path, required=True, help="levels_cli read CSV (research archive)"
    )
    levels_seed.add_argument("--since", required=True, help="first observation date, YYYY-MM-DD")
    args = parser.parse_args(argv)
    try:
        if args.command == "seed-levels":
            print(seed_levels(args.data, args.levels, args.since))
            return 0
        now = utc(args.now)
        if args.command == "seed":
            summary = seed(args.data, args.response, args.run_label, now)
        else:
            summary = run_all(
                args.data,
                args.artifact,
                now,
                fetch=fetch or fetch_all_response,
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
