# IPCET official level publications

`riverforecast.operate run` publishes the official IPCET daily level table for
every station, next to the BDCTB forecast. The values and the semáforo state are
IPCET's own; nothing here comes from the model. The state vocabulary is in
[semaforo-states.md](semaforo-states.md).

Both files live on the `forecasts` branch:

```text
https://raw.githubusercontent.com/ElGuill3/tabasco-river-forecast/forecasts/ipcet/latest.json
https://raw.githubusercontent.com/ElGuill3/tabasco-river-forecast/forecasts/ipcet/recent.json
```

## `ipcet/latest.json` (`ipcet-levels/v1`)

The newest ingested table, by resolved table date and then by image
Last-Modified.

| Field | Meaning |
| --- | --- |
| `schema` | `"ipcet-levels/v1"` |
| `table_date` | Resolved observation date of the table (`YYYY-MM-DD`), see `levels.obs_date` |
| `issued_at` | 06:00 Mexico City on `table_date`, ISO 8601 with offset |
| `last_modified` | The image's verbatim HTTP Last-Modified header |
| `source_url` | The IPCET page the messenger read, or null |
| `stations` | One entry per table row, in table order |

Each station has:

| Field | Meaning |
| --- | --- |
| `code` | CONAGUA code (`riverforecast.stations`), or null for a row this repository does not list |
| `river`, `station` | Names as read by OCR |
| `namo`, `namino`, `ayer`, `hoy`, `diff`, `capacity` | Metres as printed, or null when unreadable |
| `semaforo` | `NORMAL`, `A LA BAJA`, `BAJO`, `PREVENCION`, `ALERTA`, `EMERGENCIA`, or null |
| `problems` | `levels.row_problems` reasons (for example `diff_mismatch`); empty when the printed arithmetic checks out |

Values are never repaired. A consumer should treat a station with problems as
showing its official state but an unconfirmed level.

## `ipcet/recent.json` (`ipcet-recent/v1`)

```json
{
  "schema": "ipcet-recent/v1",
  "stations": {
    "BDCTB": [{ "date": "2026-10-19", "hoy": 16.49, "semaforo": "NORMAL" }]
  }
}
```

The last 30 dates per station code, ascending. A date can appear for one
station and not for another.
- `hoy` is null when that row had problems.
- `semaforo` is null for seeded history, because the research archive did not read it.

Rows without a code are left out.

## Source and update rules

- `inputs/ipcet/YYYY-MM-DD/tables.json` caches every station of each level table
  in an uploaded folder, plus the errors for images that are not tables.
  Like `ingested.json`, it is written once per folder; the BDCTB `ingested.json`
  path is unchanged.
- `inputs/ipcet/levels_seed.csv` holds the history loaded once with
  `operate seed-levels`.
- `inputs/ipcet/levels.csv` (`table_date,code,river,station,namo,ayer,hoy,diff,semaforo,valid`)
  is regenerated from the seed and every `tables.json`. A table overrides a seeded
  row for the same date and station.
- The 11:00 issue cutoff of the forecast does not apply: an official table is
  published as soon as it is ingested.
- Each file is written only when its content changes, so a run without a new
  table writes nothing.
