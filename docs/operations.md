# Operate the daily BDCTB forecast

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
| Schedule `17 12-23 * * *` | Hourly 06:17–17:17 Mexico City, every day. GitHub may delay scheduled runs. |
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
two attempts. It requests the BDCTB river cell 17.425, −91.525, `forecast_v4`,
ensemble, 10 past days and 10 forecast days. The response is validated before
any stored file changes. No CEMS or IPCET website fetch occurs in this module.

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
- No shared future dates, changed member sets, a future state label, or a changed
  trajectory before the next day: fail closed rather than invent a run label.

The per-run response is `inputs/openmeteo/<run_label>.json`; state records
`run_label`, `fingerprint`, and `fetched_at`. This is a trajectory heuristic,
not proof of a source issue date. A stale response remains the old run; it is
never relabeled just because a calendar day passed. Long outages without
shared future coverage need operator investigation and a newly verified seed
in a fresh checkout, not an automatic reset.

## IPCET ingestion and publication

The messenger contract is unchanged: `inputs/ipcet/YYYY-MM-DD/meta.json`
contains `page_url`, timezone-aware `fetched_at`, and `images` with `url`,
verbatim RFC 1123 GMT `last_modified`, `sha256`, and `bytes`. The folder date is
the page date. Decoded image basenames must stay inside that folder; traversal,
symlinks, reserved filenames, hash/size mismatches and implausible readings are
rejected and logged without aborting other images.

The existing `levels` reader OCRs each image once. `ingested.json` caches parsed
BDCTB rows and rejection reasons, including for images that are not level
tables. Printed table dates win; undated tables use the folder date and require
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
