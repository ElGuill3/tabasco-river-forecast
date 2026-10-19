# Tabasco river forecast

Probabilistic 1-, 3- and 7-day river stage forecasts for the main rivers of
Tabasco, Mexico, built only from public data.

> **Status:** research in progress. Nothing here is an official forecast or
> warning. Official river information comes from CONAGUA and the Instituto de
> Protección Civil del Estado de Tabasco (IPCET).

## Approach

- **Target:** daily stage from CONAGUA hydrometric stations (SIH/BANDAS),
  never imputed. The validated IPCET daily 06:00 stage is also a live input
  when available, with a GloFAS-only fallback.
- **Inputs:** GloFAS discharge forecasts, recent simulated discharge, season,
  and the latest causally visible IPCET value and its age. Rainfall was tested
  but not admitted as a separate model input (see Model).
- **Baselines first:** every model is compared with climatology and with GloFAS
  converted to stage, on whole years the model never saw.
- **Official validation:** CONAGUA records and the daily IPCET
  *Semáforo Hidrometeorológico*.

The first river is the Usumacinta at Boca del Cerro (station BDCTB, Tenosique).
The plan then extends to the 14 rivers in the IPCET semáforo.

## Data

### CONAGUA stations (target and validation)

Daily station files come from the CONAGUA Sistema de Información Hidrológica
(`https://sih.conagua.gob.mx/basedatos/{Hidros,Climas}/<CODE>.csv`). Every file is
parsed strictly before it is stored under `data/raw/conagua/<kind>/<CODE>.csv`
(UTF-8); an invalid download never replaces a stored file.

```bash
uv run python -m riverforecast.conagua_cli download hidros BDCTB
uv run python -m riverforecast.conagua_cli import hidros BDCTB path/to/BDCTB.csv
uv run python -m riverforecast.conagua_cli report hidros BDCTB --column level_m --start 1997-01-01
```

The SIH server sits behind bot protection that temporarily blocks repeated
downloads (HTTP 302/403). When that happens, save the file from a browser and
use `import`.

BDCTB (Boca del Cerro) level covers 94.7% of the days from 1997-01-01 to
2025-09-03. The longest gap, 2011-09-24 to 2011-12-31, falls on the peak of the
2011 flood, so that event needs other validation sources.

### GloFAS discharge (input)

GloFAS v4 daily discharge comes from the Open-Meteo flood API (no key):
`consolidated_v4` is the reanalysis (1997 to about two months ago) and
`forecast_v4` keeps the day-0 value of each daily forecast (from 2023-06-01).

```bash
uv run python -m riverforecast.glofas_cli fetch BDCTB --lat 17.43 --lon -91.492
```

The command chooses the station's river cell among the 5 x 5 cells around it:
cells must carry 0.5-2 times the observed mean discharge, are scored by their
daily correlation with observed discharge allowing a lead of 0-5 days, and the
nearest cell within 0.005 of the best score wins. The rule and scores are
written to `data/raw/glofas/<CODE>/cell.json`.

For BDCTB the river cell is 17.425, -91.525 (correlation 0.832 with a 2-day
lead, mean 0.96 times the observed). The nine cells on the main channel score
0.831-0.832, so the choice barely matters. Where both sources exist
(2023-06-01..2025-05-31), the archived day-0 forecasts follow the reanalysis
closely (correlation 0.966, median difference 3.3%, 90th percentile 11.3%).

### GloFAS reforecasts (forecast error)

Reforecasts are past forecasts issued as GloFAS would have issued them, so they
carry the forecast error a live model faces. GloFAS v4 reforecasts for the BDCTB
river cell (Copernicus CEMS `cems-glofas-reforecast`, 11 members, leads of 1, 3
and 7 days) were downloaded by the first research repository and are imported
with:

```bash
uv run python -m riverforecast.reforecast_cli import BDCTB <folder> [<folder> ...]
```

BDCTB: 2 294 issues from 2004-01-01 to 2023-11-25 (twice a week; 2020-2023 only
at 1 day). Against the reanalysis, the control run differs by a median of 3.2%
at 1 day, 4.2% at 3 days and 7.4% at 7 days (correlation 0.986, 0.973, 0.938).

### GloFAS operational forecasts (2023 onwards)

Daily GloFAS v4 forecasts as issued (control plus 50 members) come from the
Copernicus CEMS Early Warning Data Store (`cems-glofas-forecast`; account in
`~/.cdsapirc`). CEMS caps a request at about 900 fields and one issue day of
the full ensemble is 153 (51 members x 3 leads), so each request covers five
days; requests queue for about 40 minutes and three run at once. The stored
tables take about 7.5 KB per issue day (under 10 MB for 2023-2026):

```bash
uv run python -m riverforecast.cems_cli fetch BDCTB --start 2023-08-01
```

CEMS and Open-Meteo label the same cells half a grid step apart in longitude:
the BDCTB river cell is 17.425 / 268.525 in CEMS and 17.425 / -91.525 in
Open-Meteo.

### Retained CEMS archives for all station cells

Use **raw mode** when downloading for the 14 stations: it retains every cell in
`[18.3, -93.6, 17.1, -90.9]` (north, west, south, east), rather than discarding
the NetCDFs after extracting BDCTB. The existing per-cell `fetch` command is unchanged.
Both downloaders request leads 24, 72 and 168 hours.

```bash
uv run python -m riverforecast.cems_cli fetch-raw --area tabasco \
    --start 2023-08-01 --end 2026-10-13 \
    --out data/raw/glofas/cems/operational --parallel 3
uv run python -m riverforecast.cems_reforecast_cli plan
uv run python -m riverforecast.cems_reforecast_cli fetch-raw --area tabasco \
    --out data/raw/glofas/cems/reforecast --parallel 3
```

| Archive | Requests and coverage | Storage and indicative queue time |
| --- | --- | --- |
| Operational | 51 members; at most five days within one month (765 fields); ZIP named by first issue day | Validation: 1,966,003 bytes for five days, 396 s. Roughly 0.45 GB for the requested period; about 9 h at three concurrent requests if that queue time holds. |
| Reforecast | v4 (`version_4_0` / `lisflood`), both products, 11 members; 3,500 issues from 2003-03-27 through 2023-11-25 | 201 requests, 115,500 fields; 571.0 MiB float32 payload before metadata/compression. Validation: one issue, 105,842 bytes, 757 s. Roughly 0.4–0.6 GB; about 14–45 h at concurrency three if requests take 13–40 min. |

These are planning estimates, not service guarantees: queue time and compression vary.
The operational estimate excludes NetCDF overhead; reserve at least 1 GB for both archives.
The raw size estimate assumes 24 × 54 cells on the 0.05-degree grid.

The committed EWDS snapshot, `data/glofas/reforecast-constraints.json`, is the
source of truth for reforecast dates. Only its v4 rows are used (not older
`htessel_lisflood` versions). Calendars differ across years: requests group one
month and an identical day set, then slice years so each contains at most 27
issues (891 fields). Every requested date and product is checked against the
snapshot. Group filenames show their first and last issue dates; dates within
those bounds are not necessarily daily. For a minimal service probe, use
`fetch-raw --area tabasco --out <folder> --issue 2003-04-03` (one issue, 33 fields).
Keep probes in a separate folder from bulk archives if avoiding duplicate issue
coverage matters.

Both modes skip ZIPs only after checking CRCs and exact issue, lead and member
coverage. Downloads are validated at a temporary name on the same filesystem
and atomically renamed; invalid or partial replies never replace a stored ZIP.
Failures are printed to stderr and give a nonzero exit; rerun the same command
to retry. A cost-limit rejection stops new submissions for the entire run;
already-running service requests may finish. `--parallel` defaults to 3 and must
be positive. Each production worker uses its own CDS client.

Extract additional station cells later without requesting CEMS again:

```python
from riverforecast.cems import read_cell  # also available in cems_reforecast

rows = read_cell("data/raw/glofas/cems/operational/2026-10-01.zip", 17.425, -91.475)
# Columns: issue, valid, lead_days, member, discharge_m3s
```

`read_cell` accepts ZIP or NetCDF paths and either longitude convention, but
requires an exact grid cell (no spatial interpolation). Use CEMS labels: BDCTB
is 17.425 / 268.525 (−91.475), not the half-step-offset Open-Meteo longitude.
The October 1–5, 2026 raw validation matched all 765 existing BDCTB CSV rows
exactly; the April 3, 2003 reforecast probe opened as 33 rows / 11 members.

### Upstream basin

The basin of each station is traced from HydroBASINS level 12 (Lehner & Grill
2013, `hybas_na_lev12_v1c.zip` from https://data.hydrosheds.org) and versioned
in `data/basins/<CODE>.geojson`.

```bash
uv run python -m riverforecast.basin_cli trace BDCTB --lat 17.43 --lon -91.492 \
    --hydrobasins data/raw/hydrobasins/hybas_na_lev12_v1c.zip
```

BDCTB drains 51 457 km² (380 sub-basins, outlet 7121209020), mostly in
Guatemala and Chiapas.

### Satellite rainfall (input)

Daily basin-mean rainfall comes from NASA GPM IMERG Early Run V07 (0.1°,
available about four hours after real time, from 1998-01-01), so training and
daily forecasting use the same source. Only the basin's bounding box is
requested through OPeNDAP (about 30 KB per day instead of 31 MB), and the mean
covers the cells whose centre lies inside the basin.

It needs a free NASA Earthdata account: put
`machine urs.earthdata.nasa.gov login <user> password <password>` in
`~/.netrc` (`chmod 600`) and approve the "NASA GESDISC DATA ARCHIVE"
application in the Earthdata profile.

```bash
uv run python -m riverforecast.imerg_cli update BDCTB --start 1998-01-01
```

Rerunning fetches only the days missing from `data/raw/imerg/<CODE>/daily.csv`.

### IPCET daily level tables (live stage)

Every day IPCET (Tabasco civil protection) publishes, next to the colour semáforo,
a table with the 06:00 stage of 16 stations on the 14 semáforo rivers and the levels
of the four Grijalva dams; the stated source is CONAGUA's Tabasco office. Boca del
Cerro uses the same gauge datum as the SIH series (NAMO 19.21 m).

```bash
uv run python -m riverforecast.levels_cli read data/raw/ipcet/levels --out data/raw/ipcet/levels.csv --workers 4
uv run python experiments/11_levels_check.py
```

- Coverage: the first table is from 2021-08-03; tables exist on 73-83% of days per
  year (61% so far in 2026), mostly missing on weekends and holidays. Each table also
  prints yesterday's value, so 77-91% of days are eventually published. Tables are
  usually uploaded between 07:00 and 11:00 local time.
- Reader: `riverforecast.levels` reads each cell with tesseract across the 2021-22,
  2024-25 and 2026 layouts and refuses images that are not level tables (1366 of
  1414 images read). The printed date wins over the page date because IPCET reuses
  file names across years. Today's value is ignored when the printed difference is
  zero (it can be yesterday's value copied before the reading).
- Checks (experiment 11): consecutive tables agree on 903 of 911 days; against the
  SIH series on 1071 common days the mean absolute difference is 0.035 m and 94% of
  days are within 0.05 m (13 days differ by more than 0.5 m).

### IPCET semáforo (official validation)

The Instituto de Protección Civil del Estado de Tabasco publishes a daily
*Semáforo Hidrometeorológico* image with the state of 14 rivers yesterday and
today (emergencia, alerta, precaución, normal, bajo, abajo del mínimo; no stage
in metres). `riverforecast.semaforo` reads the cell colors at fixed positions
and refuses any image whose size, table grid or colors do not match.

```bash
uv run python -m riverforecast.semaforo_cli fetch --start 2026-01-01
```

Images go to `data/raw/ipcet/images/` and states to `data/raw/ipcet/states.csv`.
The site keeps images for 2026 only, and not every day.

## Evaluation

`riverforecast.evaluation` fixes the protocol before any model exists:

- **Development, 1998-2016:** fitting and every modelling choice, scored with
  folds that hold out four whole years (14-day buffer).
- **Test, 2017-2025:** used once, at the end, for the reported results.
- **Metrics:** MAE and RMSE of the median, pinball loss of q10/q50/q90, coverage
  and width of the q10-q90 interval (nominal 80%), MAE on high-water days, and
  detection and false-alarm ratio for reaching the high-water threshold (90th
  percentile of development stage, 17.83 m at BDCTB).

Experiments live in `experiments/` and write their results to `docs/results/`.
[Experiment 01](docs/results/01-baselines-development.md) sets the references:
climatology misses by 1.15 m on average, and GloFAS reforecasts read through a
rating curve by about 0.80 m at 1, 3 and 7 days.

## Model

`riverforecast.features` builds, for each GloFAS forecast issue and lead, inputs
known on the issue day only (a test alters later discharge and checks the
inputs do not change): the ensemble forecast (log median, mean, control and
spread), simulated discharge on the issue day and 3 and 7 days earlier, the
3-day trend, the expected change, and the target day's season.

`riverforecast.model` fits one gradient-boosting regressor per quantile (q10,
q50, q90; sorted so they never cross). `ConformalQuantileModel` widens q10-q90
by conformalized quantile regression (Romano et al. 2019) on inner year folds,
because the raw interval is too narrow.

[Experiment 02](docs/results/02-glofas-model-development.md) (development
2004-2016, same days for every forecast):

| Lead | GloFAS rating MAE | Model MAE | Model coverage (80% nominal) | Width |
| --- | --- | --- | --- | --- |
| 1 day | 0.805 m | 0.684 m | 0.803 | 2.10 m |
| 3 days | 0.787 m | 0.658 m | 0.808 | 2.10 m |
| 7 days | 0.803 m | 0.712 m | 0.822 | 2.26 m |

[Experiment 00](docs/results/00-rain-check.md) checks IMERG against the CONAGUA
gauge at Boca del Cerro: monthly correlation 0.69 and similar totals (205 vs
191 mm per month), with a mild drift (IMERG/gauge 1.16 up to 2007, 0.99 from
2015). [Experiment 03](docs/results/03-rain-development.md) adds basin rainfall
(1-90 day sums) to the model: MAE 0.702 / 0.670 / 0.705 m against 0.684 / 0.658
/ 0.712 m without it, with lower flood detection at 3 and 7 days, so rainfall is
**not** a model input. GloFAS is itself driven by rainfall, so the satellite
series adds little and brings its own drift.

### Test result

[Experiment 04](docs/results/04-test.md) scores the frozen model once on target
days from 2017 (fitted on 2004-2016 only):

| Lead | Climatology MAE | GloFAS rating MAE | Model MAE | Model coverage (80%) | Test period |
| --- | --- | --- | --- | --- | --- |
| 1 day | 1.144 m | 0.793 m | **0.594 m** | 0.77 | 2017-2023 (796 issues) |
| 3 days | 1.074 m | 0.778 m | **0.578 m** | 0.73 | 2017-2019 (337 issues) |
| 7 days | 1.118 m | 0.771 m | **0.600 m** | 0.76 | 2017-2019 (341 issues) |

The model beats both references in every test year, including the 2020 flood
(1 day: 1.08 m against 1.18 m for the rating and 2.78 m for climatology). At
1 day it detects 78% of high-water days (>= 17.83 m) with 18% false alarms.
The 2017-2019 window holds only 4-5 high-water days at 3 and 7 days, too few to
judge flood skill there; the 2023-2026 operational forecasts will extend it.

### Operational test

[Experiment 05](docs/results/05-operational.md) scores the same frozen model,
unchanged, on the real GloFAS operational forecasts issued 2023-08-01 to
2026-10-13, as issued: the ensemble is summarized on the 11 reforecast members,
past discharge comes from the archived day-0 forecasts, and the model was fitted
once on development data only. Target days are 2023-08-2025-09, where observed
stage exists:

| Lead | Climatology MAE | GloFAS rating MAE | Model MAE | Model coverage (80%) | High water (n) | Detection / false alarms | Issues |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 day | 1.030 m | 0.816 m | **0.710 m** | 0.80 | 34 | 0.68 / 0.57 | 693 |
| 3 days | 1.022 m | 0.782 m | **0.697 m** | 0.80 | 32 | 0.69 / 0.50 | 689 |
| 7 days | 1.020 m | 0.802 m | **0.732 m** | 0.81 | 32 | 0.78 / 0.55 | 687 |

The model still beats the GloFAS rating at every lead, but its MAE (0.70-0.73 m)
is above the 0.58-0.60 m of the reforecast test: these are real operational
forecasts with day-0 archived past discharge, and 2023-2025 is a different period.
Coverage matches the nominal 80%, and feeding all 51 operational members instead
of the 11 comparable ones changes almost nothing (MAE 0.710/0.696/0.710 m).
High-water detection is 0.68-0.78 but the false-alarm ratio is 0.50-0.57 (only 34
high-water days): the model over-warns in operations. The 2026 semáforo check is
only ordinal: on the two precaución days the predicted stage is 17.8-17.9 m
against 12.7 m on normal days (rank AUC 0.997 at 1 and 3 days), consistent, but
two events decide nothing and no threshold was fitted.

### Warning rule

[Experiment 06](docs/results/06-warning.md) treats the warning as a
probabilistic forecast problem (Wilks 2011; Jolliffe and Stephenson 2012): the
frozen model's q10/q50/q90 become an exceedance probability of 17.83 m,
recalibrated by isotonic regression on development out-of-fold predictions
(2004-2016, 4-year folds), and the operating point t* is the probability
threshold whose development warning frequency equals the event frequency
(0.75 / 0.67 / 0.32 at 1 / 3 / 7 days), plus an EFAS-style rule that requires
the warning on two consecutive issues. All three rules were fixed on
development data only and scored once on the test and operational periods:

| Lead | Period | R0 POD / FAR | R1 POD / FAR | R2 POD / FAR | BSS | ROC AUC |
| --- | --- | --- | --- | --- | --- | --- |
| 1 day | test (2017-2023) | 0.78 / 0.18 | 0.78 / 0.27 | 0.67 / 0.25 | 0.59 | 0.97 |
| 1 day | operational (2023-2025) | 0.68 / 0.57 | 0.71 / 0.56 | 0.68 / 0.48 | 0.16 | 0.96 |
| 3 days | test (2017-2019) | 0.20 / 0.50 | 0.40 / 0.67 | 0.20 / 0.00 | 0.47 | 0.98 |
| 3 days | operational (2023-2025) | 0.69 / 0.50 | 0.75 / 0.61 | 0.69 / 0.57 | 0.20 | 0.97 |
| 7 days | test (2017-2020) | 0.25 / 0.50 | 0.50 / 0.60 | 0.25 / 0.50 | 0.49 | 0.96 |
| 7 days | operational (2023-2025) | 0.78 / 0.55 | 0.81 / 0.66 | 0.75 / 0.59 | -0.01 | 0.95 |

R0 is the current rule (q50 >= 17.83), R1 the calibrated probability above t*,
R2 R1 on two consecutive issues; BSS and ROC AUC describe the calibrated
probability itself (BSS against the development base rate, ~12%). The honest
conclusion: the probability forecast is skillful, but recalibration does **not**
reduce false alarms. R1 warns at the development event rate (~12% of days),
which in the drier operational period (4.6-4.9% events) means even more
warnings than R0; on the reforecast test its lead-1 FAR rises from 0.18 to
0.27. Persistence (R2) is the only rule that lowers the operational false-alarm
ratio (0.57 to 0.48 at lead 1, with detection unchanged), at the cost of missed
events elsewhere; h3/h7 test rows have only 5 and 4 events and decide nothing.
Diagnosis (reported, not corrected): most R0 false alarms are near misses (8 of
11 test and 11 of 30 operational lead-1 false alarms within 1 m of the
threshold), and the median error on days with observed stage >= 15 m flips from
-0.4 to -0.7 m in development and test to +0.1 to +0.3 m operationally, i.e. the
operational forecasts run systematically high in exactly the situation that
creates false alarms.

Experiment 07 (diagnosis only) traced that operational over-forecast to its
mechanism: the mean model error on days with observed stage >= 15 m flips
year by year (-0.14 m in 2023, +0.77 m in 2024, -1.54 m in 2025 at 1 day),
tracking a year-specific GloFAS discharge error against the gauge (+25-33% in
2024), while the gauge rating, the past-discharge source and the ensemble
system are minor effects.

### Post-diagnosis re-score

Following the pre-registration in the task log (L24), [experiment
08](docs/results/08-rescore.md) re-scores once, treating 2023-2025 as what it
now is: a consulted period. The only change is the standard final refit: model
M1 is the same conformal quantile model refitted with target days 1998-2022
(the frozen model used 1998-2016), and the whole warning chain (isotonic
calibrator, t*, R0/R1/R2) is re-derived on 1998-2022 out-of-fold predictions
only. High water stays 17.83 m. On the same operational target days:

| Lead | Frozen MAE | M1 MAE | M1 coverage (80%) | Best M1 rule (by Peirce) POD / FAR |
| --- | --- | --- | --- | --- |
| 1 day | 0.710 m | 0.706 m | 0.79 | R1: 0.79 / 0.58 |
| 3 days | 0.697 m | 0.685 m | 0.80 | R1: 0.78 / 0.56 |
| 7 days | 0.732 m | 0.717 m | 0.81 | R0: 0.78 / 0.48 |

The honest reading: the refit helps a little (MAE down 0.004-0.015 m) but far
less than the ~0.1 m hoped for at high stage, and the lead-1 high-stage bias
even rises slightly. The 2024 GloFAS error remains (+0.6-0.8 m on high-stage
days at every lead), and 2025 gets no better at 3 and 7 days. This confirms
the L23/L24 finding: the 2023-2025 error is year-specific GloFAS behaviour
that no correction estimated before 2023 can remove; only live observations
(the independent verification of T15) can detect and one day correct it.

### Independent verification (2025-26)

[Experiment 09](docs/results/09-independent.md) applies the pre-registered
design of L26 to evidence that was never scored against stage: M1, unchanged,
on forecasts valid 2025-09-04..2026-10-14. Against the ten press-quoted Boca
del Cerro levels (19.21 m NAMO datum, same as the training stage), lead-1 MAE
is 0.80 m (bias -0.53 m, coverage 0.80) and lead-3 MAE 0.83 m (bias -0.75 m,
coverage 0.60); the model now runs slightly low where it ran high in 2024.
Four of the six dated high-water/alert reports with a forecast within +-1 day
were warned by R2 at lead 1 (R0: three), including the IPCET alerts of
2025-09-24 and the October 2026 rise (the semáforo's first precaución days are
ranked above the normal days, rank AUC 0.981/0.994 at leads 1/3, n=2).
These are press-quoted values on a small sample: no threshold or model was
fitted on them, and one circulating bulletin quotes the same station on an
older ~12.56 m scale, which was excluded. A fresh SIH import is still needed
to score the period against observed stage.

### Live stage input (IPCET)

The IPCET daily 06:00 stage is used as a live input (pre-registered in L35,
scored once in [experiment 12](docs/results/12-live-test.md)). Added to the
GloFAS model are the latest visible IPCET value, its age, and the GloFAS
discharge on that day, with a GloFAS-only fallback when nothing was published
within 7 days. An audit then found four defects (L39): the past discharge was
one day older in operation than in training, the obs-day discharge came from
the reanalysis (not available in real time), table visibility ignored upload
times, and the live series used later tables. All four were fixed and the
scoring was repeated once, labelled post-audit
([experiment 13](docs/results/13-postaudit.md)); these are the current numbers:

| Period | Lead | GloFAS-only MAE | Live-input MAE | Live coverage (80%) | Fallback share |
| --- | --- | --- | --- | --- | --- |
| Test 2017+ (simulated feed) | 1 day | 0.599 m | 0.293 m | 0.81 | 0% |
| Test 2017+ (simulated feed) | 3 days | 0.578 m | 0.350 m | 0.82 | 0% |
| Test 2017+ (simulated feed) | 7 days | 0.590 m | 0.452 m | 0.83 | 0% |
| Operational 2023-08..2025-09 (SIH target) | 1 day | 0.677 m | 0.367 m | 0.80 | 12.8% |
| Operational 2023-08..2025-09 (SIH target) | 3 days | 0.669 m | 0.470 m | 0.80 | 13.2% |
| Operational 2023-08..2025-09 (SIH target) | 7 days | 0.718 m | 0.636 m | 0.80 | 13.1% |
| Independent 2025-09-04..2026-10-16 (IPCET target) | 1 day | 0.456 m | 0.326 m | 0.77 | 4.0% |
| Independent 2025-09-04..2026-10-16 (IPCET target) | 3 days | 0.522 m | 0.449 m | 0.74 | 5.0% |
| Independent 2025-09-04..2026-10-16 (IPCET target) | 7 days | 0.619 m | 0.593 m | 0.76 | 5.0% |

A live value at most a day or two old roughly halves the lead-1 error and cuts
the lead-1 false-alarm ratio from about 0.5 to 0.13 in the operational period;
its worth fades by an age of 3-4 days and is small at 7 days, where future
rain dominates. Caveats: the test row is an upper bound, because the SIH daily
values used to simulate the feed carry information from after 06:00; in the
operational row the input comes from IPCET and the target from SIH; the
deployed model was trained with the simulated availability of L32 on
1998-2022 targets because the real feed only starts in 2021-08; the intervals
are slightly narrow against IPCET targets (74-77% instead of 80%); and the feed
has gaps (fallback on 4-13% of operational issue days).

## Frozen BDCTB publishing

Freeze the post-audit L1 live-input model and M1 fallback once, offline, using
local research data. Daily publishing then requires only the versioned artifact
and stored serving inputs, **not training data or a model fit**:

```bash
uv run python -m riverforecast.publish_cli freeze
uv run python -m riverforecast.publish_cli publish --issue 2026-10-13 \
  --ipcet .cache/t20/ipcet.csv --raw data/raw \
  --out .cache/t20/forecast.json
```

The caller maintains the IPCET CSV (`date,stage_m,table_last_modified`), using
validated, causally available values and UTC upload timestamps. Header-only CSV
selects GloFAS-only fallback. Issue time is 11:00 Mexico City; output contains
h1/h3/h7 q10/q50/q90, distance to NAMO 19.21 m, and live-input provenance.
See the [v2 JSON schema and input contract](docs/forecast-schema.md).

`models/bdctb/` holds the trusted joblib models and versioned training metadata.
Serving requires scikit-learn 1.9.1 (the artifact's version) and joblib, but no
OCR/tesseract; image ingestion separately needs the existing `levels` reader and
tesseract. The current provider reads the stored CEMS operational CSVs and day-0
archive only, without network calls. Open-Meteo is an explicit unimplemented
stub pending T19 validation; acquisition and daily scheduling remain T19/T21.

## Development

```bash
uv sync
uv run pytest
uv run ruff check .
```

Raw and intermediate data live in `data/raw/` and `data/interim/` (not
versioned) and are regenerated by the pipeline.

Work is tracked in [`odd/tasks/usumacinta-forecast.md`](odd/tasks/usumacinta-forecast.md).
