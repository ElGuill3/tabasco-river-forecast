# Operate the hourly river forecasts

The model runs on GitHub Actions, not the fair laptop. Main holds the frozen
artifact and Python code; the `forecasts` branch holds messenger inputs and the
published [v2 JSON](forecast-schema.md). The app reads `bdctb/latest.json` and
keeps its own offline cache. No training happens during daily operation.

## Quick path: initialize once

The operator creates the `forecasts` branch and checks it out separately. Copy
[`ops/forecasts-branch/.github/workflows/relay.yml`](../ops/forecasts-branch/.github/workflows/relay.yml)
to `.github/workflows/relay.yml` at that branch's root. See the
[branch layout](../ops/forecasts-branch/README.md).

From main, seed the data checkout with an independently identified GloFAS run:

```bash
uv run python -m riverforecast.operate seed --data /tmp/forecasts \
  --response .cache/t19/om/ensemble.json --run-label 2026-10-19 \
  --now 2026-10-19T23:03:54Z
```

This validates the saved response with `OpenMeteoProvider` and creates
`inputs/openmeteo/2026-10-19.json` plus `state.json`. Seeding refuses to replace
existing state. The known 2026-10-19 label is established by T19/L51, not by the
HTTP fetch date. Commit and upload this initialization as an operator action;
the Python CLI never commits, pushes or creates branches.

## Daily execution and triggers

```bash
uv run python -m riverforecast.operate run --data /tmp/forecasts
```

`--artifact` defaults to `models/bdctb`. `--now` accepts a timezone-aware ISO
instant for reproducible local replay; actual runs use current UTC. The CLI
prints one summary line and exits 0 for publication or no forecast change.
Hard errors print `error: ...` to stderr and exit 1.

| Trigger | Behavior |
| --- | --- |
| Schedule `17 * * * *` | Minute 17 of every UTC hour, all day, every day. GitHub may delay scheduled runs. |
| Messenger upload | The app writes article HTML and original image files, then `meta.json` last. The data-branch relay dispatches main's `forecast.yml`. |
| Manual | Run `forecast.yml` with `workflow_dispatch`. |

The workflow checks out main and `forecasts` into `out`, installs
`tesseract-ocr` and `tesseract-ocr-eng` (the reader uses default **English**
language data, not Spanish), sets up uv, and uses `uv sync --frozen` so the
artifact's scikit-learn version matches. It runs the operation once and commits
changed input receipts and outputs as `github-actions[bot]`. A failed push gets
one `git pull --rebase` and retry. Jobs are serialized, never canceled in
progress, with a 20-minute job limit. No separate Actions secret is needed.

**GitHub disables scheduled workflows after 60 days without repository
activity.** Monitor the Actions page and re-enable the schedule when needed.
Keep the relay installed on the data branch as well as `forecast.yml` on main.

## How run dates are inferred

There is one Open-Meteo endpoint request, with a 60-second timeout and at most
two attempts. One multi-cell request covers BDCTB, MACTB, SAACP, PNVTB and
TAPTB, in that order, using their frozen Open-Meteo river cells (`stations.CELLS`,
matching `data/raw/glofas/<CODE>/cell.json`, without CEMS longitude conversion).
It requests `forecast_v4`, ensemble, 10 past days and 10 forecast days. Each
station's response is validated before its stored files change. No CEMS or IPCET website fetch occurs in this module.

Open-Meteo does not expose the served GloFAS issue date. Compare the saved
response with the fresh response using SHA-256 over sorted `(absolute date,
member, value)` triples, **only on shared dates strictly after the fetch's UTC
date**. Past values, extra dates and dropped tail dates do not signal a new run.
All supplied ensemble members enter the comparison; inference still uses the
provider's original eleven-member subset.

- Equal trajectories: retain the old label and its original response and state.
- Changed trajectories: use previous label + one day if that equals today's UTC
  date. After a larger gap, use today's UTC date and record
  `run_label_inferred: true` in state.
- Changed trajectories on the same UTC day as the previous label: treat as a
  revision of that label, replace its saved response, increment `revisions`,
  update `fingerprint` and `fetched_at`, and publish again.
- No shared future dates, changed member sets, or a future state label (clock
  moved backwards): fail closed rather than invent a run label.

The per-run response is `inputs/openmeteo/<run_label>.json`; state records
`run_label`, `fingerprint`, `fetched_at`, and a `revisions` count (zero for a
new label; older seeded states may omit it). Revisions preserve an existing
`run_label_inferred` flag. This is a trajectory heuristic,
not proof of a source issue date. A stale response remains the old run; it is
never relabeled just because a calendar day passed. Long outages without
shared future coverage need operator investigation and a newly verified seed
in a fresh checkout, not an automatic reset.

Each new label or same-day revision appends to
`inputs/openmeteo/detections.csv`, with header `run_label,fetched_at,kind` and
kind `new`, `inferred` (after a missed-day gap), or `revision`. Seeding and
unchanged polls do not append detections. The log is written in the same
rollback-protected plan as the response, state and publication.

The inferred label is the **UTC date of detection**, correct only if Open-Meteo
starts serving run D during UTC day D. Polling every hour of the day helps
catch a run on its own UTC day, but is not proof of this assumption. Inspect
the detection hours in this log after a few days to audit the assumption;
scheduler delays and source delays remain possible.

## IPCET ingestion and publication

The messenger contract is unchanged: `inputs/ipcet/YYYY-MM-DD/meta.json`
contains `page_url`, timezone-aware `fetched_at`, and `images` with `url`,
verbatim RFC 1123 GMT `last_modified`, `sha256`, and `bytes`. The folder date is
the page date. Decoded image basenames must stay inside that folder; traversal,
symlinks, reserved filenames, hash/size mismatches and implausible readings are
rejected and logged without aborting other images.

The existing `levels` reader OCRs each image once. `ingested.json` caches parsed
BDCTB rows and rejection reasons, including for images that are not level
tables. Library exceptions such as tesseract failures are also cached as
rejections: they do not abort other images or folders, or block publication of
a new run. Process interrupts (`BaseException`) are not swallowed.
Printed table dates win; undated tables use the folder date and require
the reader's normal confirmation. `levels.row_problems` enforces the station's
NAMO and printed arithmetic; the existing numeric range (absolute value at most
1000) is retained. No value is repaired or imputed.

Each operation resolves the cached rows through `levels.station_series` using
only tables whose upload time is at/before both now and **11:00 Mexico City on
the labeled issue date**, and whose page date is not later than that date.
This preserves the publisher's fixed issue-time contract: a newly received
post-11:00 table cannot update that day's fixed 11:00 forecast; it becomes
eligible on a later run label. Yesterday's printed value is also usable;
today's zero-difference value is excluded. Conflicting days are dropped and
undated rows require confirmation, without consulting the retrospective
research archive.

`inputs/ipcet/bdctb_levels.csv` has `date,stage_m,table_last_modified`.
Timestamps are quoted verbatim HTTP headers. When multiple tables contribute,
the latest contributing timestamp is used; undated confirmation is conservatively
delayed to the newest visible table. Cached rows are re-resolved as new tables
arrive, so confirmations/corrections are not backdated into older issue windows.

A new run, a changed resolved IPCET series, or a missing first publication
calls `publish.forecast` with the frozen artifact and the **saved** run response.
It writes `bdctb/latest.json` and `bdctb/history/<run_label>.json`; a same-label
IPCET update overwrites that history entry. `generated_at` is the actual UTC
operation time. `bdctb/published.json` records the observation digest, so repeated
runs do not regenerate JSON. A fully unchanged run prints `no change` and
writes nothing. Processing rejected or not-yet-visible images can write an
OCR receipt without changing the forecast; its summary is
`ingested inputs; forecast unchanged` rather than `no change`.

### Official table for every station

The same run also publishes IPCET's official table for all stations as
`ipcet/latest.json` and `ipcet/recent.json` ([schema](ipcet-schema.md)). It keeps
its own once-per-folder cache, `tables.json`, and never changes the BDCTB path
above. Each image is still OCRed once per run. To give the chart history from
day one, seed the station history once from the research OCR archive:

```bash
uv run python -m riverforecast.operate seed-levels --data /tmp/forecasts \
  --levels data/raw/ipcet/levels.csv --since 2026-08-01
```

The seed keeps only valid station rows with a trusted date, on or after `--since`,
and leaves `semaforo` empty. It refuses to replace an existing
`inputs/ipcet/levels_seed.csv`.

## Additional published stations (L65/L67)

The hourly operation also publishes Macuspana (MACTB), Salto de Agua (SAACP),
Pueblo Nuevo (PNVTB) and Tapijulapa (TAPTB). Each has
`<code lowercase>/latest.json`, `history/<run_label>.json` and `published.json`.
They use the same `bdctb-forecast/v2` schema (the schema identifier is retained
for compatibility), with their own `station.code`, `station.name`, model version
and NAMO: 9.85, 10.99, 7.49 and 24.63 m, respectively.
`distance_to_namo_m` remains q50 minus that station's NAMO. BDCTB's artifact,
paths, schema and forecast bytes for fixed inputs are unchanged.

Artifacts live in `models/<code lowercase>/`. To reproduce one offline:

```bash
timeout 1500 env OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  uv run python -B -m riverforecast.publish_cli fit --code MACTB
```

`freeze --code MACTB` is equivalent. With no code, both `fit` and the original
`freeze` retain the BDCTB recipe and defaults. The four new artifacts are exactly
the experiment-16 L65 recipe: `training_table`, `fit_l1` L1 plus M1 fallback,
CONAGUA stage and perfect-forecast reanalysis rows with targets in 1999–2022.
No 2023+ row is fitted. Metadata records the source cell, period, per-lead row
counts, version `<code>-L65-v1` and L65/L67 reference. This is not a new scoring
or model-selection exercise.

Each station resolves its own validated IPCET row by code using the original
causal `station_series` rules, fixed 11:00 cutoff and reading ages 0–7; age 8
or no valid reading uses M1. Yesterday's value and zero-difference handling
remain as at BDCTB. Validation uses that station's NAMO, never BDCTB's.
Station caches are `ingested_<code>.json`; causal observations are
`inputs/ipcet/<code>_levels.csv`. Official `levels_seed.csv` is not imported as a
live input because it lacks causal upload timestamps.

New stations bootstrap their own run state from the established BDCTB run label
on their first valid response. Their responses, state and detections live under
`inputs/openmeteo/<code>/`; detection and observation receipts otherwise follow
the BDCTB rules. Inference always uses control plus members 1–10 and the same
provider's day-0 past discharge. No fitting occurs in the operation.

The summary reports each station's publication/no-change or failure. A failed new
station (MACTB, SAACP, PNVTB, TAPTB) preserves its prior publication and cannot
stop the others; it is retried on the next run, and the run exits 0 so Actions can
commit the successful stations. A BDCTB failure still exits 1, as before the new
stations, so it stays visible as a failed run; that hour nothing is committed and
every station is retried on the next run. If every station fails, the command
also exits 1. Bootstrap response/state
and first publication share the same validated write plan: a failed first
publication leaves neither bootstrap state nor a partial forecast.
Each station's plan has the existing write rollback protection; the overall run
is intentionally not an all-station transaction. The shared HTTP request itself
is a common failure boundary.

The subset is the L67 product decision: accepted at all three leads and h1 L1
skill at least 0.10. L65 evaluated age-zero stage availability, not the full live
availability pattern. M1 generally loses to persistence at h1/h3; fallback is
explicitly labelled, not a claim of equal skill. The additional stations have
not been trained on aged readings, even though operation retains BDCTB's age
rules. See [experiment 16](results/16-station-models.md) for the scored results.

## Local offline replay

Normal `run` downloads Open-Meteo. **For an offline dry run, inject a fetch**;
never invoke normal `run` when network access is prohibited:

```python
from pathlib import Path
from riverforecast import operate

response = Path(".cache/t19/om/ensemble.json").read_bytes()
code = operate.main(
    ["run", "--data", "/tmp/forecasts", "--now", "2026-10-19T23:03:54Z"],
    fetch=lambda: response,
)
assert code == 0
```

The first seeded run publishes with `(same run)` even if no fresh run arrived;
the second identical invocation prints `no change`. Add an IPCET upload folder
with matching bytes/hash and a usable pre-cutoff Last-Modified, then invoke
again to exercise OCR and live inference. `reader`, `forecaster`, and the HTTP
`get` transport are injectable for deterministic tests. Use a fresh system temp
directory, never a live branch checkout, for test state.

## Verifying the published forecasts

`riverforecast.verify` compares every stored forecast with the official stage of
its target date: the `hoy` value of the IPCET table dated that day, valid rows
only. Leads whose target date has no table yet are counted as pending. The
persistence column is the issue-day stage carried forward; a useful forecast
should have a lower error than persistence over many days, not on one.

```bash
cd .cache/t21/forecasts   # a checkout of the forecasts branch
uv run --project ../../.. python -m riverforecast.verify \
    --history bdctb/history --levels inputs/ipcet/levels.csv --rows verify-rows.csv
```

It prints one Markdown table per mode and lead: scored and pending counts,
MAE and bias of the median, q10–q90 coverage (80 % expected) and the
persistence MAE. The metrics were fixed before the first score (ODD L60); the
model is never retuned from them during operation.

## Failure modes and recovery

| Symptom | Action |
| --- | --- |
| Missing state | Seed an independently identified response; do not guess from fetch date. |
| Invalid ensemble / missing required dates / missing shared future dates | Exit 1, preserve files, inspect saved inputs; never replace latest with partial output. |
| Rejected OCR/hash/unsafe path | Inspect stderr and `ingested.json`. Receipt prevents repeated OCR; correct the upload/receipt deliberately after investigation. |
| No recent usable stage | Publisher uses GloFAS-only fallback; ages 0–7 are live, age 8 is fallback. |
| Model/runtime mismatch | Use the frozen uv lock, not a refitted artifact. |
| Push conflict after retry | Job fails; inspect branch changes, then rerun. Never force-push. |

Input and inference validation complete before checkout writes. Recoverable
write errors roll back written files. A process kill or persistent filesystem
failure during a multi-file write cannot be made transactionally atomic;
Actions does not commit a failed operation, so its disposable checkout is the
recovery boundary. Local operators should retain a clean checkout or backup.
