# BDCTB forecast JSON v2

`bdctb-forecast/v2` publishes modeled stage at Boca del Cerro, not an observation,
an official warning, or a replacement for the IPCET semáforo. All levels use the
SIH/IPCET gauge datum, in metres; NAMO is **19.21 m**.

## Produce a forecast

```bash
uv run python -m riverforecast.publish_cli freeze
uv run python -m riverforecast.publish_cli publish --issue 2026-10-13 \
  --ipcet .cache/t20/ipcet.csv --raw data/raw \
  --out .cache/t20/forecast.json
```

Freeze is an offline, one-time research/deployment operation. It requires local
CONAGUA stage, reanalysis, reforecasts and the IPCET availability census. It writes
`models/bdctb/models.joblib` (six conformal quantile models: L1 and M1 at each lead)
and `metadata.json`. The metadata records the version, training window and actual
per-lead target coverage, features, conformal margins, scikit-learn version, code
Git commit, station and NAMO. The requested target window is 1998–2022, but available
reforecast rows start in 2004 and h3/h7 end around 2020; no targets are imputed.
The fit is experiment 13's post-audit recipe, including shifted reanalysis past
and simulated availability for L1. Artifacts over 25 MB cause an error; do not
change the model to circumvent the limit.

Publish loads the trusted joblib artifact and stored inputs only. **It never
trains and does not require raw training data.** The runtime must use the metadata's
scikit-learn version (currently 1.9.1); joblib is supplied with scikit-learn. Never
load an artifact from an untrusted source: joblib/pickle can execute code.

## Stored input contract

The IPCET CSV, maintained by the caller, has this header and timezone-qualified upload timestamps:

```csv
date,stage_m,table_last_modified
2026-10-13,17.93,2026-10-13T16:30:00Z
```

- `date`: observation calendar date (06:00 stage), not page date.
- `stage_m`: finite, validated BDCTB stage on the 19.21 m NAMO datum.
- `table_last_modified`: timezone-qualified timestamp when the validated value
  became available. An explicit timezone is required: ISO 8601 `...Z` or an offset,
  and HTTP RFC 1123 GMT values such as `Mon, 19 Oct 2026 16:00:00 GMT`, are accepted.
  Naive values fail with `table_last_modified must include a timezone`; they are
  never silently assumed UTC. Quote RFC 1123 values in CSV because they contain a comma.
  Use the conservative effective availability if a reader rule,
  confirmation or conflict resolution delays acceptance beyond the HTTP upload.
- Empty CSV (header only) is valid and selects fallback. Duplicate usable dates,
  invalid dates/timestamps and nonfinite stages are rejected without writing JSON.

The caller must resolve images **causally** with `levels`/`live`: process only
visible tables, respect printed/page date rules, zero-difference exclusions,
undated confirmations and conflicts. Do not export the retrospective full-archive
`bdctb_levels.csv` as if it were a causal input stream. The three-column interface
cannot reconstruct these table-level decisions. The [daily operation](operations.md)
maintains the causal series from cached messenger table rows, preserves effective
availability and does not backdate later confirmations into older issue windows.

Publish ignores future observations and anything uploaded after the issue cutoff.
It uses `live.real_revealed_values`, exactly as the post-audit library: ages **0–7
inclusive** are eligible; age 8 falls back. This preserves experiment 13's boundary
(the experiment calls it “within 7 days”). The live `obs_day_q` comes from the
archived day-0 series, never reanalysis.

The supplied `CemsArchiveProvider` reads only:

- `<raw>/glofas/BDCTB/operational/*.csv`, the existing CEMS cell archive;
- `<raw>/glofas/BDCTB/forecast_v4.csv`, daily archived day-0 discharge.

It reuses `data.glofas_operational_summary` with control plus perturbed members
01–10 and `features.build`; the provider protocol returns `(ensemble_summary,
day0_series)` for an issue date. All three forecast leads and past discharge at
issue, issue−3 and issue−7 must exist; missing inputs fail rather than producing
a partial forecast. Neither provider nor CLI makes network calls.
For the adopted T19/L51 live source, pass `--openmeteo PATH` to select
`OpenMeteoProvider(PATH)` instead of the default CEMS `--raw` archive:

```bash
uv run python -m riverforecast.publish_cli publish --issue 2026-10-19 \
  --ipcet .cache/t21/ipcet.csv --openmeteo .cache/t19/om/ensemble.json \
  --out .cache/t21/forecast.json --generated-at 2026-10-19T23:03:54Z
```

The provider reads a **saved** Open-Meteo `forecast_v4` ensemble response with
`daily.time`, `daily.river_discharge` (control), and
`daily.river_discharge_member01`–`10` (perturbed 01–10). Other members are ignored
so the summary uses the same 11 members as training. Dates are valid dates
(issue + lead); the caller must supply the matching run's issue date. Leads 1–7
form the summary, and control values on dates at or before issue form past
(day-0) discharge. Every date from issue−7 through issue+7 must be present;
used discharge values must be finite and strictly positive. Invalid JSON,
missing columns/dates, and invalid used values fail before writing output.
Neither this option nor the default CEMS provider downloads inputs. The separate
`python -m riverforecast.operate run --data DIR` operation acquires fresh inputs,
infers the served run label against seeded state, ingests IPCET messenger images,
and publishes only when inputs change. It supplies the saved run response to
this provider and sets `generated_at` to the actual UTC operation time.
See [operations](operations.md) for the run-date heuristic, fixed issue cutoff,
seed command and offline replay. The app reads
`https://raw.githubusercontent.com/ElGuill3/tabasco-river-forecast/forecasts/bdctb/latest.json`;
history lives at `bdctb/history/<run_label>.json` on the same branch. Same-label
IPCET updates overwrite that history entry without changing the schema.

## Output fields

| Field | Meaning |
| --- | --- |
| `schema` | Exact string `bdctb-forecast/v2` |
| `station` | `{"code":"BDCTB","name":"Boca del Cerro"}` |
| `issue_time` | ISO 8601, 11:00 in `America/Mexico_City` (UTC−06 for operational dates) |
| `generated_at` | ISO 8601; defaults to `issue_time` for deterministic replay; override with `--generated-at` |
| `model_version` | Frozen artifact's version |
| `mode` | `live` or `glofas_only` |
| `live_input` | `{"date":"YYYY-MM-DD","stage_m":17.93,"age_days":0}` or `null` for fallback |
| `forecasts` | Objects keyed by `h1`, `h3`, `h7` |
| `disclaimer` | Exact Spanish text below |

Each forecast object contains `target_date` (`issue date + h`), `q10`, `q50`,
`q90` in metres (ordered), `distance_to_namo_m` (`q50 - 19.21`, positive above
NAMO), `namo_m` (19.21) and `data_kind` (`modeled_not_observed`). Quantiles are
not rounded; finite JSON numbers preserve parity to 1e-6 m. The q10–q90 interval
has nominal 80% coverage, not a guarantee.

> Pronóstico modelado, no es una observación ni un aviso oficial. Consulte el semáforo oficial de IPCET.

Fixed artifact, input files, issue date and generation timestamp yield identical
JSON. Success writes the requested JSON silently; input errors print `error: ...`
to stderr and return 1 before touching an existing output. There is no training,
OCR or tesseract dependency in the publish path. Image ingestion separately needs
`tesseract` and the existing `levels` reader.
