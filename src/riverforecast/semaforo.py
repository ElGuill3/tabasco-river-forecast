"""Read river states from the IPCET daily "Semáforo Hidrometeorológico" image.

The Instituto de Protección Civil del Estado de Tabasco publishes one image per
day with a table of 14 rivers and two colored cells per river: yesterday
(``ayer``) and today (``hoy``). The colors follow the image legend:
emergencia, alerta, precaución, normal, bajo and abajo del mínimo. The image
gives no stage in metres.

The table sits at fixed pixel positions (checked on images from January to
October 2026). Reading fails with ``LayoutError`` when the size, the grid lines
or any cell color do not match, so a redesigned image is never misread.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np
from PIL import Image

IMAGE_URL = (
    "https://tabasco.gob.mx/sites/default/files/users/useripcettabasco/semaforo{:%d%m%y}.jpeg"
)
SIZE = (819, 1024)
# Horizontal grid lines: header row, then rows 1-14.
GRID_LINES = (770, 785, 802, 818, 835, 851, 868, 884, 901, 917, 933, 950, 966, 983, 999, 1016)
AYER_X, HOY_X = 433, 497
RIVERS = (
    "Samaria",
    "Carrizal - Ra. González",
    "Oxolotán",
    "Tacotalpa - Tapijulapa",
    "Puyacatengo",
    "Pichucalco",
    "Teapa",
    "De la Sierra - P. Nuevo",
    "Grijalva - Gaviotas",
    "Grijalva - Acachapan",
    "Tulijá - Salto de agua",
    "Puxcatán",
    "Usumacinta",
    "San Pedro",
)
# Legend colors (RGB) sampled from the published images, worst state first.
LEVELS = {
    "emergencia": (114, 50, 160),
    "alerta": (255, 30, 5),
    "precaucion": (253, 238, 20),
    "normal": (0, 175, 40),
    "bajo": (150, 150, 150),
    "abajo_del_minimo": (138, 70, 45),
}
MAX_COLOR_DISTANCE = 60


class LayoutError(ValueError):
    """The image does not match the expected semáforo layout."""


@dataclass(frozen=True)
class RowState:
    row: int
    river: str
    ayer: str
    hoy: str


def image_url(day: dt.date) -> str:
    return IMAGE_URL.format(day)


def read_states(image: Image.Image) -> dict[int, RowState]:
    """States of the 14 rivers, keyed by table row number."""
    if image.size != SIZE:
        raise LayoutError(f"image is {image.size[0]}x{image.size[1]}, expected {SIZE[0]}x{SIZE[1]}")
    pixels = np.asarray(image.convert("RGB")).astype(int)
    _check_grid(pixels)
    states = {}
    for row, river in enumerate(RIVERS, start=1):
        centre = (GRID_LINES[row] + GRID_LINES[row + 1]) // 2
        ayer = _classify(pixels, centre, AYER_X, f"row {row} {river} ayer")
        hoy = _classify(pixels, centre, HOY_X, f"row {row} {river} hoy")
        states[row] = RowState(row, river, ayer, hoy)
    return states


def _check_grid(pixels: np.ndarray) -> None:
    band = pixels[GRID_LINES[0] - 10 : GRID_LINES[-1] + 8, 260:390]
    dark = (band.max(axis=2) < 110).mean(axis=1) > 0.9
    rows = np.flatnonzero(dark) + GRID_LINES[0] - 10
    found = tuple(int(y) for i, y in enumerate(rows) if i == 0 or y - rows[i - 1] > 2)
    if found != GRID_LINES:
        raise LayoutError("table grid not found at the expected rows")


def _classify(pixels: np.ndarray, y: int, x: int, label: str) -> str:
    colour = pixels[y - 3 : y + 4, x - 8 : x + 9].reshape(-1, 3).mean(axis=0)
    distances = {
        level: float(np.linalg.norm(colour - np.array(rgb))) for level, rgb in LEVELS.items()
    }
    level = min(distances, key=distances.get)
    if distances[level] > MAX_COLOR_DISTANCE:
        rgb = tuple(int(round(v)) for v in colour)
        raise LayoutError(f"{label}: color {rgb} matches no legend level")
    return level
