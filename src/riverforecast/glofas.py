"""GloFAS v4 river discharge from the Open-Meteo flood API.

The API serves GloFAS on a 0.05° grid without an API key. Models used here:

- ``consolidated_v4``: reanalysis (GloFAS driven by ERA5), 1997 to about two months ago.
- ``forecast_v4``: the archived day-0 value of each daily forecast, from 2023-06-01.

A station rarely sits on the grid cell of its river, so the cell is chosen by
comparing nearby cells with the observed discharge (see ``select_river_cell``).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

API_URL = "https://flood-api.open-meteo.com/v1/flood"
SELECTION_RULE = (
    "among cells whose mean is 0.5-2 times the observed mean, the cell nearest "
    "the station whose daily correlation with observed discharge (allowing it to lead "
    "by 0-5 days) is within 0.005 of the best"
)
MEAN_RATIO_RANGE = (0.5, 2.0)
MAX_LEAD_DAYS = 5
CORRELATION_TIE = 0.005
RATE_LIMIT_WAIT_S = 60


class RateLimited(RuntimeError):
    """The API answered HTTP 429."""


class CellSelectionError(ValueError):
    """No grid cell matches the station's observed discharge."""


@dataclass(frozen=True)
class CellChoice:
    cell: str
    correlation: float
    lag_days: int
    mean_ratio: float
    days: int

    def to_json(self) -> str:
        lat, lon = (float(part) for part in self.cell.split(","))
        record = {"cell": self.cell, "latitude": lat, "longitude": lon, **asdict(self)}
        record["rule"] = SELECTION_RULE
        return json.dumps(record, indent=2) + "\n"


def cell_key(lat: float, lon: float) -> str:
    return f"{lat:.3f},{lon:.3f}"


def _open_json(url: str) -> object:
    request = urllib.request.Request(url, headers={"User-Agent": "tabasco-river-forecast"})
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 429:
            raise RateLimited(url) from None
        raise


def get_json_with_retry(
    url: str,
    open_json: Callable[[str], object] = _open_json,
    sleep: Callable[[float], None] = time.sleep,
    retries: int = 3,
) -> object:
    """Fetch JSON, waiting a minute and retrying when the API rate limits.

    Open-Meteo counts a request by its size (cells x days), so a long
    multi-cell request can exhaust the per-minute allowance on its own.
    """
    for attempt in range(retries + 1):
        try:
            return open_json(url)
        except RateLimited:
            if attempt == retries:
                raise
            sleep(RATE_LIMIT_WAIT_S)
    raise AssertionError("unreachable")


def _get_json(url: str) -> object:
    return get_json_with_retry(url)


def fetch_discharge(
    cells: Sequence[tuple[float, float]],
    start: str,
    end: str,
    model: str,
    get_json: Callable[[str], object] = _get_json,
) -> pd.DataFrame:
    """Daily discharge (m³/s) for each (lat, lon), one column per cell key."""
    url = (
        f"{API_URL}?latitude={','.join(f'{lat:.3f}' for lat, _ in cells)}"
        f"&longitude={','.join(f'{lon:.3f}' for _, lon in cells)}"
        f"&daily=river_discharge&start_date={start}&end_date={end}&models={model}"
    )
    answer = get_json(url)
    locations = answer if isinstance(answer, list) else [answer]
    columns = {}
    for (lat, lon), location in zip(cells, locations, strict=True):
        daily = location["daily"]
        values = [np.nan if v is None else float(v) for v in daily["river_discharge"]]
        columns[cell_key(lat, lon)] = pd.Series(values, index=pd.to_datetime(daily["time"]))
    table = pd.DataFrame(columns)
    table.index.name = "date"
    return table


def neighbourhood(lat: float, lon: float, radius: int = 2, step: float = 0.05):
    """Grid cell centres around a point (GloFAS cells are centred on x.x25 / x.x75)."""
    centre_lat = np.floor(lat / step) * step + step / 2
    centre_lon = np.floor(lon / step) * step + step / 2
    return [
        (round(centre_lat + i * step, 3), round(centre_lon + j * step, 3))
        for i in range(-radius, radius + 1)
        for j in range(-radius, radius + 1)
    ]


def select_river_cell(
    cells: pd.DataFrame, observed: pd.Series, station: tuple[float, float]
) -> CellChoice:
    """Pick the cell of the station's river from candidate cell series.

    A cell qualifies when its mean discharge is 0.5-2 times the observed mean on
    the same days (this excludes tributaries that share the timing but not the
    size). Each qualifying cell is scored by its daily correlation with the
    observations, allowing it to lead by up to five days: GloFAS tends to peak
    early, and without the lead a cell further downstream would score better only
    because its delay hides that timing error. Cells along one channel score
    almost the same, so every cell within 0.005 of the best counts as a tie and
    the one nearest the station wins.
    """
    scored = []
    for cell in cells.columns:
        both = pd.concat([cells[cell], observed], axis=1, keys=["cell", "obs"]).dropna()
        if len(both) < 30:
            continue
        ratio = both["cell"].mean() / both["obs"].mean()
        if not MEAN_RATIO_RANGE[0] <= ratio <= MEAN_RATIO_RANGE[1]:
            continue
        correlation, lag = max(
            (cells[cell].shift(lag).corr(observed), lag) for lag in range(MAX_LEAD_DAYS + 1)
        )
        scored.append(CellChoice(cell, round(correlation, 4), lag, round(ratio, 4), len(both)))
    if not scored:
        low, high = MEAN_RATIO_RANGE
        raise CellSelectionError(
            f"no cell has a mean discharge {low}-{high} times the observed mean"
        )
    best = max(choice.correlation for choice in scored)
    ties = [choice for choice in scored if choice.correlation >= best - CORRELATION_TIE]
    return min(ties, key=lambda choice: _distance_km(choice.cell, station))


def _distance_km(cell: str, station: tuple[float, float]) -> float:
    lat, lon = (float(part) for part in cell.split(","))
    dy = (lat - station[0]) * 111.2
    dx = (lon - station[1]) * 111.2 * np.cos(np.radians(station[0]))
    return float(np.hypot(dx, dy))
