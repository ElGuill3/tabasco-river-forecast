# Set up the forecasts branch

Copy `.github/workflows/relay.yml` from this directory to the same path at the
root of the **forecasts** branch. Keep model code and the daily `forecast.yml`
workflow on main; do not copy the research checkout into the data branch.
The parent/operator creates and seeds the branch; the operation never creates it.

## Branch layout (L47)

```text
.github/workflows/relay.yml
inputs/openmeteo/state.json
inputs/openmeteo/YYYY-MM-DD.json
inputs/ipcet/YYYY-MM-DD/page.html
inputs/ipcet/YYYY-MM-DD/<original image basename>
inputs/ipcet/YYYY-MM-DD/meta.json
inputs/ipcet/YYYY-MM-DD/ingested.json
inputs/ipcet/bdctb_levels.csv
bdctb/latest.json
bdctb/history/YYYY-MM-DD.json
bdctb/published.json
```

The app uploads the article HTML and images through the GitHub contents API,
then writes `meta.json` **last**. Its push dispatches main's `forecast.yml` using
the relay's repository `GITHUB_TOKEN` (`actions: write`); no PAT is needed in
Actions. GitHub permits `workflow_dispatch` initiated by `GITHUB_TOKEN` to create
runs. The app's separate fine-grained contents token stays on the messenger.

The raw app feed is
`https://raw.githubusercontent.com/ElGuill3/tabasco-river-forecast/forecasts/bdctb/latest.json`.
`history` is keyed by inferred/known GloFAS run label, not publication time;
an IPCET update may overwrite the same label. `ingested.json` is the OCR receipt,
including rejected-image reasons and cached parsed rows; `published.json` tracks
the last published observation digest. Preserve both on subsequent runs.

See [operations on main](https://github.com/ElGuill3/tabasco-river-forecast/blob/main/docs/operations.md)
for seeding, timing rules, failure handling and local offline replay.
