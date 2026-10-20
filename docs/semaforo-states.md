# Official IPCET station states

`levels.read_table` returns the official daily table's semáforo in each
`StationRow.semaforo`. It never derives an alert from model output, NAMO,
river stage or the separate hydrometeorological map. The normalized vocabulary
is `NORMAL`, `A LA BAJA`, `BAJO`, `PREVENCION`, `ALERTA`, `EMERGENCIA`.
Unknown or missing state is `None`, not normal.

## Archive survey

Offline survey of `data/raw/ipcet/levels/manifest.csv` and its stored JPGs:
638 images selected deterministically, with 60 evenly spaced dates in each
of 2021–2026, plus **all stored June–November dates in 2024–2026**. Selection
is the union (rounded indices `i * (n - 1) / 59`, `i = 0..59`, sorted dates).
Dates identify stored pages, not necessarily the printed year: IPCET sometimes
reuses an image on a later page. No image was downloaded or changed.

| Page year | Sampled | Tables located |
| --- | ---: | ---: |
| 2021 | 60 | 55 |
| 2022 | 60 | 58 |
| 2023 | 60 | 56 |
| 2024 | 176 | 167 |
| 2025 | 171 | 166 |
| 2026 | 111 | 110 |
| **Total** | **638** | **612** |

26 images were refused: 20 had no readable FECHA, 6 had no semáforo column
with 13–15 station bands. These are not counted as empty station cells.
The 612 located tables supplied **8,568 station cells**.

### Text and dominant background

The table below separates exact normalized OCR from unreadable/unrecognized
OCR. Counts are cell occurrences, not unique dates. Text normalization removes
accents, whitespace and punctuation before matching the vocabulary; OCR slips
such as `BAIO` and `ALA BAIA` remain unrecognized in these survey counts.
RGB is the median across per-cell dominant-background RGBs. Examples are stored
image names, all under `data/raw/ipcet/levels/`.

| Normalized text | Colour family | Cells | Typical RGB | Examples |
| --- | --- | ---: | --- | --- |
| NORMAL | green | 6,454 | 11, 173, 84 | 2021-08-03.jpg; 2021-08-05.jpg; 2021-08-07.jpg |
| A LA BAJA | grey | 290 | 128, 128, 128 | 2021-08-14.jpg; 2021-09-16.jpg; 2021-10-12.jpg |
| BAJO | brown | 85 | 101, 50, 2 | 2021-08-16.jpg; 2021-08-18.jpg; 2021-08-23.jpg |
| PREVENCION | yellow | 40 | 248, 248, 26 | 2022-09-28.jpg; 2022-10-12.jpg; 2022-10-22.jpg |
| ALERTA | red | 12 | 243, 10, 10 | 2025-09-23.jpg; 2025-09-24.jpg; 2025-10-02.jpg |
| EMERGENCIA | purple | 2 | 108, 51, 152 | 2022-11-23.jpg; 2023-11-06.jpg |
| unreadable/unrecognized | green | 804 | 15, 173, 87 | 2023-05-14.jpg; 2024-04-03.jpg; 2024-04-09.jpg |
| unreadable/unrecognized | grey/empty | 381 | 130, 130, 130 | 2023-05-14.jpg; 2024-04-03.jpg; 2024-04-09.jpg |
| unreadable/unrecognized | brown | 204 | 101, 51, 5 | 2023-05-14.jpg; 2024-04-03.jpg; 2024-04-09.jpg |
| unreadable/unrecognized | yellow | 195 | 249, 248, 22 | 2021-09-19.jpg; 2021-09-27.jpg; 2021-09-29.jpg |
| unreadable/unrecognized | red | 101 | 238, 2, 2 | 2021-09-10.jpg; 2021-09-12.jpg; 2021-09-14.jpg |

No orange station background was observed. Brown and purple are reported
separately rather than incorrectly lumped into red. By background family,
totals are green 7,258; grey/empty 671; brown 289; yellow 235; red 113;
purple 2; orange 0. These are **not** counts of text-confirmed states.
The survey's first colour pass conflated brown/purple with red; the table
above is the corrected pass, with both text and RGB retained per cell.

### Method and visual checks

The existing FECHA, grid, column and station-band locators were reused.
Background runs across the upper/lower interior margins of each row recover
the full semáforo width without white text splitting its colour profile.
The column was OCRed at 4× with Tesseract `--psm 6`, assigning word centres to
station bands. Per-cell background RGB excludes low-saturation text when
coloured pixels occupy at least 15% of the cell. Two read-only survey passes
took about three minutes each, bounded by `timeout 2700`, with four worker
processes and `OMP_THREAD_LIMIT=1`. Survey OCR misses were not manually
silently corrected.

Visual checks confirmed brown BAJO (2021-08-16), yellow PREVENCION
(2021-09-19), purple EMERGENCIA (2022-11-23), and red ALERTA (2025-09-23).
There is no river-state legend on those daily level tables: the colour scale
at the foot of recent tables is **rainfall**, not river alert states.
The separate 2026-10-14 hydrometeorological **map** has the legend Emergencia
(purple), Alerta (red), Precaución (yellow), Normal (green), Bajo (grey),
Abajo del mínimo (brown). Its labels differ from the table's PREVENCION,
A LA BAJA and BAJO: do not substitute the map vocabulary for the table's.

## Reader contract and compatibility

Legible recognized cell text takes precedence over colour. Accent-free
uppercase states use the observed table vocabulary, including `PREVENCION`
(not the map's `PRECAUCION`). S/D, empty white cells and unrecognized
backgrounds return `None`. Unreadable text can fall back only to a confidently
matched observed background (green, grey, brown, yellow, red or purple).
No orange-to-state mapping is invented. Numeric reading and validation are
unchanged; semáforo does not affect whether stage measurements are valid.

`semaforo: str | None = None` is the final `StationRow` field, preserving all
existing positional constructors. State-source provenance is not added in
this change. The `levels_cli read` CSV schema is intentionally unchanged:
existing CLI tests pin its exact header, so consumers need no migration.
`live.boca_rows` selects CSV columns by name; `data.py` does not consume the
level-reader CSV, and `operate.py` consumes table objects and its own live
series/cache. Publication of the new state is a separate task (feature T2).

## Fixture expectations

- `semaforo_2026-10-19.jpeg` (166,489 bytes): San Joaquín ALERTA; other 13 NORMAL.
- `levels_2026-10-14.jpg` (140,603 bytes): Samaria A LA BAJA;
  Boca del Cerro PREVENCION; other 12 NORMAL. Copied byte-for-byte from the
  stored daily table, inspected by eye.
- The existing `semaforo_2026-10-14.jpeg` is a map, remains unchanged and
  must still be refused by the table reader.
- Existing `levels_2022-10-10.jpg` already exercises the older title/layout
  with PREVENCION at Gaviotas and San Pedro. Reuse it instead of adding an
  unauthorized older fixture; its existing file exceeds 300 KB, but no new
  older-format fixture is introduced.
