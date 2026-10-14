"""Upstream basin of a station from HydroBASINS level-12 sub-basins.

Each HydroBASINS sub-basin names the sub-basin it drains into (``NEXT_DOWN``),
so the basin of an outlet is every sub-basin whose downstream chain reaches it.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path


class BasinError(ValueError):
    """The basin cannot be traced."""


def upstream_ids(next_down: Mapping[int, int], outlet: int) -> set[int]:
    """Sub-basin ids that drain to ``outlet``, including the outlet itself."""
    if outlet not in next_down:
        raise BasinError(f"outlet {outlet} is not in the sub-basin table")
    feeders = defaultdict(list)
    for basin, down in next_down.items():
        feeders[down].append(basin)
    found = {outlet}
    frontier = [outlet]
    while frontier:
        frontier = [up for basin in frontier for up in feeders[basin] if up not in found]
        found.update(frontier)
    return found


def trace_basin(hydrobasins_zip: Path, lat: float, lon: float, search_km: float = 9.0):
    """Dissolved upstream basin of the largest sub-basin within ``search_km`` of a point.

    Returns ``(basin, outlet)``: a one-row GeoDataFrame in EPSG:4326 with the
    outlet id, sub-basin count and area, and the outlet id.
    """
    import geopandas as gpd
    from shapely.geometry import Point

    box = (lon - 6, lat - 6, lon + 6, lat + 6)
    table = gpd.read_file(f"zip://{hydrobasins_zip}", bbox=box)
    station = gpd.GeoSeries([Point(lon, lat)], crs=4326).to_crs(6933).iloc[0]
    near = table[table.geometry.to_crs(6933).distance(station) < search_km * 1000]
    if near.empty:
        raise BasinError(f"no sub-basin within {search_km} km of {lat}, {lon}")
    outlet = int(near.sort_values("UP_AREA").iloc[-1]["HYBAS_ID"])

    links = dict(zip(table["HYBAS_ID"].astype(int), table["NEXT_DOWN"].astype(int), strict=True))
    members = table[table["HYBAS_ID"].astype(int).isin(upstream_ids(links, outlet))]
    basin = members[["geometry"]].dissolve()
    basin["outlet_hybas_id"] = outlet
    basin["subbasins"] = len(members)
    basin["area_km2"] = round(float(basin.to_crs(6933).area.iloc[0]) / 1e6, 1)
    return basin, outlet
