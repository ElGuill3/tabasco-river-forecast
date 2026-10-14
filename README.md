# Tabasco river forecast

Probabilistic 1-, 3- and 7-day river stage forecasts for the main rivers of
Tabasco, Mexico, built only from public data.

> **Status:** research in progress. Nothing here is an official forecast or
> warning. Official river information comes from CONAGUA and the Instituto de
> Protección Civil del Estado de Tabasco (IPCET).

## Approach

- **Target (training and validation only):** daily stage from CONAGUA
  hydrometric stations (SIH/BANDAS). No live gauge feed exists, so observed
  stage is never a model input.
- **Inputs (available every day):** GloFAS discharge forecasts and recent
  simulated discharge, satellite rainfall over the upstream basin, forecast
  rainfall, and season.
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

## Development

```bash
uv sync
uv run pytest
uv run ruff check .
```

Raw and intermediate data live in `data/raw/` and `data/interim/` (not
versioned) and are regenerated by the pipeline.

Work is tracked in [`odd/tasks/usumacinta-forecast.md`](odd/tasks/usumacinta-forecast.md).
