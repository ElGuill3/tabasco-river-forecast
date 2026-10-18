"""Read river stages from the IPCET daily level-table image.

The Instituto de Protección Civil del Estado de Tabasco publishes a daily
image with the table "SEMAFORO DE NIVELES DE RIOS" (2021-2025) or "NIVELES
DE RIOS Y REGISTROS DE LLUVIAS" (2026): one row per station with NAMO,
NAMINO, yesterday's and today's 06:00 stage (escala), the difference and the
regulation capacity, then a block with the Angostura, Chicoasén, Malpaso and
Peñitas dams. Source: CONAGUA Dirección Local Tabasco.

The layout and image size vary over the years, so nothing is read at fixed
pixels. The reader finds the FECHA line by OCR, the station rows as the
horizontal-grid intervals that contain a coloured semáforo cell, the columns
through the vertical grid lines, and OCRs every numeric cell with a digit
whitelist (whole columns first, then per-cell variants for rows that fail
the table's own arithmetic). Values are never repaired: a kept number always
comes from OCR of that cell, and a row whose printed arithmetic cannot be
matched stays flagged by :func:`row_problems`. Images that are not level
tables (the colour semáforo map, unrelated infographics) raise
:class:`UnreadableTable`.
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import pytesseract
from PIL import Image

MONTHS = {
    "ene": 1,
    "feb": 2,
    "mar": 3,
    "abr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "ago": 8,
    "sep": 9,
    "set": 9,
    "oct": 10,
    "nov": 11,
    "dic": 12,
}
# The 14 station rows, always in this order in the published tables.
STATIONS: tuple[tuple[str, str], ...] = (
    ("Samaria", "Samaria"),
    ("Carrizal", "González"),
    ("Oxolotán", "Oxolotán"),
    ("Tacotalpa", "Tapijulapa"),
    ("Puyacatengo", "Puyacatengo"),
    ("Teapa", "Teapa"),
    ("De la Sierra", "Pueblo Nuevo"),
    ("Pichucalco", "San Joaquín"),
    ("Grijalva", "Gaviotas"),
    ("Grijalva", "Porvenir"),
    ("Tulijá", "Salto de Agua"),
    ("Puxcatán", "Macuspana"),
    ("Usumacinta", "Boca del Cerro"),
    ("San Pedro", "San Pedro"),
)
DAMS: tuple[str, ...] = ("Angostura", "Chicoasén", "Malpaso", "Peñitas")
TOLERANCE = 0.011


class UnreadableTable(ValueError):
    """The image is not a readable daily level table."""


@dataclass(frozen=True)
class StationRow:
    river: str
    station: str
    namo: float | None
    namino: float | None
    ayer: float | None
    hoy: float | None
    diff: float | None
    capacity: float | None


@dataclass(frozen=True)
class DamRow:
    name: str
    namo: float | None
    ayer: float | None
    hoy: float | None
    diff: float | None
    capacity: float | None


@dataclass(frozen=True)
class LevelTable:
    page_date: dt.date | None
    table_date: dt.date | None
    stations: tuple[StationRow, ...]
    dams: tuple[DamRow, ...]


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def _runs_1d(mask: np.ndarray, minlen: int = 1, maxlen: int = 10**9) -> list[tuple[int, int]]:
    """Runs of True in a 1-D mask, as inclusive (start, end) pairs."""
    out: list[tuple[int, int]] = []
    y, n = 0, len(mask)
    while y < n:
        if mask[y]:
            y2 = y
            while y2 + 1 < n and mask[y2 + 1]:
                y2 += 1
            if minlen <= y2 - y + 1 <= maxlen:
                out.append((y, y2))
            y = y2 + 1
        else:
            y += 1
    return out


def _merge_lines(candidates: list[list[int]]) -> list[int]:
    """Union of line positions from several passes, deduplicated."""
    merged: list[int] = []
    for c in sorted(p for cand in candidates for p in cand):
        if not merged or c - merged[-1] > 2:
            merged.append(c)
    return merged


def _h_lines(gray: np.ndarray, thr: int) -> list[int]:
    """Horizontal grid lines: thin dark runs give their centre, thick dark
    blocks (the coloured header of the 2025-2026 layouts) give their edges.
    """
    _, w = gray.shape
    f = (gray[:, w // 10 : 9 * w // 10] < thr).mean(axis=1)
    out = []
    for a, b in _runs_1d(f > 0.55, 1, 90):
        if b - a <= 4:
            out.append((a + b) // 2)
        else:
            out.extend((a, b))
    return out


def _v_lines(gray: np.ndarray, y0: int, y1: int, thr: int = 130) -> list[int]:
    """Vertical grid lines: thin dark runs give their centre, thick dark runs
    (the edge of a coloured semáforo cell) give their left edge.
    """
    f = (gray[y0:y1, :] < thr).mean(axis=0)
    out = []
    for a, b in _runs_1d(f > 0.85, 1, 20):
        if b - a <= 4:
            out.append((a + b) // 2)
        else:
            out.append(a)
    return out


def _dedupe(positions: list[int], gap: int) -> list[int]:
    """Collapse line positions closer than ``gap`` (line + cell edge pairs)."""
    out: list[int] = []
    for x in positions:
        if not out or x - out[-1] > gap:
            out.append(x)
    return out


def _station_bands(
    lines: list[int], fill: np.ndarray, y_lo: int, y_hi: int, thr: float = 0.45
) -> list[tuple[int, int]]:
    """Station rows = horizontal-grid intervals covered by a semáforo cell.

    Using the grid lines as band edges keeps fragments from the page header
    or legend (which share the column's colours) out of the station rows.
    """
    bands = []
    for a, b in zip(lines, lines[1:], strict=False):
        if not (10 <= b - a <= 26 and b <= y_hi):
            continue
        seg = fill[max(0, a - y_lo) : b + 1 - y_lo]
        if seg.size and (seg > 0.65).mean() >= thr:
            bands.append((a, b))
    return bands


def _semaforo_column(
    rgb: np.ndarray,
    gray: np.ndarray,
    lines: list[int],
    y_lo: int,
    y_hi: int,
) -> tuple[int, int, np.ndarray, list[tuple[int, int]]]:
    """Locate the semáforo column; return (x0, x1, fill, station bands).

    The cells carry a coloured (or plain grey) background, so a window over
    the column shows one solid block per station row. Among the windows whose
    fill marks 13-15 grid intervals as rows, the one with the most colour
    mass wins (a window straddling the column edge sees only fragments).
    """
    r, g, b = rgb[..., 0].astype(int), rgb[..., 1].astype(int), rgb[..., 2].astype(int)
    sat = np.maximum(np.maximum(r, g), b) - np.minimum(np.minimum(r, g), b)
    grey = (sat < 40) & (gray > 110) & (gray < 200)
    coloured = (sat > 60) | grey
    h, w = coloured.shape
    y_hi = min(y_hi, h)
    best: tuple[int, int, np.ndarray, list[tuple[int, int]], float] | None = None
    for width in (30, 40, 50, 60, 25, 70):
        for x0 in range(0, w - width, 3):
            fill = coloured[y_lo:y_hi, x0 : x0 + width].mean(axis=1)
            bands = _station_bands(lines, fill, y_lo, y_hi)
            if not 13 <= len(bands) <= 15:
                continue
            mass = float((sat[y_lo:y_hi, x0 : x0 + width] > 60).mean())
            if best is None or (len(bands), mass) > (len(best[3]), best[4]):
                best = (x0, x0 + width, fill, bands, mass)
        if best is not None and len(best[3]) == 14:
            break
    if best is None:
        raise UnreadableTable("no semáforo column with 13-15 station rows found")
    return best[0], best[1], best[2], best[3]


def _fit_rows(bands: list[tuple[int, int]], lines: list[int]) -> list[tuple[int, int]]:
    """Keep the station-row block, rebuild it from the grid lines, snap to 14.

    Fragments above the table (the coloured forecast box or header cell) form
    their own block and are dropped; the station rows form one contiguous
    block. Rows whose semáforo cell is empty (S/D) or a very light grey may
    be missed by the colour fill, so the chosen block is rebuilt from the
    horizontal grid lines, which still cross those rows.
    """
    blocks: list[list[tuple[int, int]]] = []
    for band in bands:
        if blocks and band[0] - blocks[-1][-1][1] <= 28:
            blocks[-1].append(band)
        else:
            blocks.append([band])
    bands = max(blocks, key=len)
    bands = _rebuild_from_lines(bands, lines)
    if len(bands) == 13:
        gaps = [b[0] - a[1] for a, b in zip(bands, bands[1:], strict=False)]
        i = int(np.argmax(gaps))
        if 10 <= gaps[i] <= 26:
            bands = bands[: i + 1] + [(bands[i][1], bands[i + 1][0])] + bands[i + 1 :]
    if len(bands) == 15:
        centres = [(a + b) // 2 for a, b in bands]
        gaps = [b - a for a, b in zip(centres, centres[1:], strict=False)]
        i = int(np.argmin(gaps))
        bands = bands[:i] + [(bands[i][0], bands[i + 1][1])] + bands[i + 2 :]
    if len(bands) != 14:
        raise UnreadableTable(f"expected 13-15 station rows, found {len(bands)}")
    return list(bands)


def _rebuild_from_lines(bands: list[tuple[int, int]], lines: list[int]) -> list[tuple[int, int]]:
    """Rebuild a 12-13 row station block from its grid lines.

    The block boundaries are grid lines themselves; the interior lines give
    every row edge, including rows the colour fill missed. One extra line
    above or below the block is tried when the rebuild is short, because a
    missed first row shifts the whole block one line down.
    """
    if not 12 <= len(bands) <= 13:
        return bands

    def edges_of(top: int, bot: int) -> list[tuple[int, int]]:
        edges = [top] + _dedupe([x for x in lines if top < x < bot], 3) + [bot]
        return [(a, b) for a, b in zip(edges, edges[1:], strict=False) if 10 <= b - a <= 30]

    rebuilt = edges_of(bands[0][0], bands[-1][1])
    if len(rebuilt) == 14:
        return rebuilt
    for shift in (-1, 1):
        neighbours = (
            [x for x in lines if x < bands[0][0] - 2]
            if shift < 0
            else [x for x in lines if x > bands[-1][1] + 2]
        )
        if not neighbours:
            continue
        edge = neighbours[-1] if shift < 0 else neighbours[0]
        gap = bands[0][0] - edge if shift < 0 else edge - bands[-1][1]
        if not 10 <= gap <= 30:
            continue
        top, bot = (edge, bands[-1][1]) if shift < 0 else (bands[0][0], edge)
        if len(edges_of(top, bot)) == 14:
            return edges_of(top, bot)
    return bands


def _fecha_words(strip: np.ndarray, invert: bool) -> tuple[list[tuple[str, int]], int | None]:
    """OCR words of the top strip; return (words, y of the FECHA word)."""
    if invert:
        strip = 255 - strip
    im = Image.fromarray(strip)
    im = im.resize((im.width * 2, im.height * 2), Image.LANCZOS)
    data = pytesseract.image_to_data(im, config="--psm 6", output_type=pytesseract.Output.DICT)
    words: list[tuple[str, int]] = []
    fecha_y: int | None = None
    for i, text in enumerate(data["text"]):
        text = text.strip()
        if not text or data["level"][i] != 5 or data["conf"][i] < 30:
            continue
        normed = _norm(text)
        y = int(data["top"][i] / 2)
        words.append((normed, y))
        if normed.startswith("fecha") and fecha_y is None:
            fecha_y = y
    return words, fecha_y


def _rows_from_lines(
    rgb: np.ndarray, gray: np.ndarray, lines: list[int], y_lo: int, y_hi: int
) -> list[tuple[int, int]] | None:
    """Fallback station rows: the best 15-line window over the grid.

    When the colour fill misses several rows (light grey or empty semáforo
    cells), the station rows are rebuilt from the horizontal grid lines: 15
    consecutive lines spaced 10-30 px give 14 rows. Among the candidate
    windows the one with the most coloured (or grey) mass inside the
    semáforo column wins; the column is the x-run with the most colour in
    the search band.
    """
    if len(lines) < 15:
        return None
    red, grn, blu = rgb[..., 0].astype(int), rgb[..., 1].astype(int), rgb[..., 2].astype(int)
    sat = np.maximum(np.maximum(red, grn), blu) - np.minimum(np.minimum(red, grn), blu)
    grey = (sat < 40) & (gray > 110) & (gray < 200)
    coloured = (sat > 60) | grey
    prof = coloured[y_lo:y_hi].mean(axis=0)
    runs = _runs_1d(prof > 0.4, 15, 90)
    if not runs:
        return None
    x0, x1 = max(runs, key=lambda rn: float(prof[rn[0] : rn[1]].sum()))
    best: tuple[float, list[tuple[int, int]]] | None = None
    for i in range(len(lines) - 14):
        window = lines[i : i + 15]
        if window[0] < y_lo or window[-1] > y_hi:
            continue
        if not all(10 <= b - a <= 30 for a, b in zip(window, window[1:], strict=False)):
            continue
        score = sum(
            float(coloured[a:b, x0:x1].mean()) for a, b in zip(window, window[1:], strict=False)
        )
        if best is None or score > best[0]:
            best = (score, list(zip(window, window[1:], strict=False)))
    return best[1] if best else None


def _find_fecha(gray: np.ndarray) -> tuple[dt.date | None, int | None]:
    """Parse the FECHA line; return (date, y of the FECHA word).

    Most layouts print FECHA in dark text on the page background, but some
    (2024 and a few 2021-2025 tables) print it in white on a black bar, so
    the inverted strip is tried too.
    """
    h = gray.shape[0]
    strip = gray[: h // 3, :]
    words, fecha_y = _fecha_words(strip, invert=False)
    if fecha_y is None:
        words, fecha_y = _fecha_words(strip, invert=True)
    if fecha_y is None:
        return _fecha_from_bars(gray)
    line = " ".join(t for t, y in words if abs(y - fecha_y) < 25)
    return _parse_fecha(line), fecha_y


def _fecha_from_bars(gray: np.ndarray) -> tuple[dt.date | None, int | None]:
    """FECHA printed in white on a black bar (2024 and some 2021-2025 pages).

    The dark full-width bars of the top third are inverted and OCRd one by
    one; the first bar whose text names FECHA and parses as a date wins.
    """
    h = gray.shape[0]
    mask = (gray[: h // 3, :] < 90).mean(axis=1) > 0.5
    mask = _close_gaps(mask, 8)
    for a, b in _runs_1d(mask, 12, 220):
        im = Image.fromarray(255 - gray[a : b + 1, :])
        im = im.resize((im.width * 3, im.height * 3), Image.LANCZOS)
        text = pytesseract.image_to_string(im, config="--psm 6").strip()
        if "fecha" not in _norm(text):
            continue
        date = _parse_fecha(_norm(text))
        if date is not None:
            return date, (a + b) // 2
    return None, None


def _close_gaps(mask: np.ndarray, gap: int) -> np.ndarray:
    """Fill short False gaps in a 1-D mask (text rows split a dark bar)."""
    out = mask.copy()
    runs = _runs_1d(~mask, 1, gap)
    for a, b in runs:
        if 0 < a and b < len(out) - 1:
            out[a : b + 1] = True
    return out


def _parse_fecha(text: str) -> dt.date | None:
    m = re.search(
        r"(\d{1,2})\s*de\s*(ene|feb|mar|abr|may|jun|jul|ago|sep|set|oct|nov|dic)\w*\s*(?:de\s*)?(\d{4})",
        text,
    )
    if m:
        try:
            return dt.date(int(m.group(3)), MONTHS[m.group(2)], int(m.group(1)))
        except ValueError:  # OCR slip, e.g. 31 DE FEBRERO
            return None
    m = re.search(r"(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})", text)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000
        try:
            return dt.date(y, mo, d)
        except ValueError:
            return None
    return None


def _ocr_column(
    gray: np.ndarray, bands: list[tuple[int, int]], x0: int, x1: int, scale: int = 4
) -> list[str]:
    """OCR one column of stacked cells; each band gets the words inside it."""
    y0, y1 = bands[0][0], bands[-1][1]
    crop = gray[y0:y1, x0:x1]
    im = Image.fromarray(crop)
    im = im.resize((im.width * scale, im.height * scale), Image.LANCZOS)
    data = pytesseract.image_to_data(
        im,
        config="--psm 6 -c tessedit_char_whitelist=-0123456789.",
        output_type=pytesseract.Output.DICT,
    )
    words = []
    for i, text in enumerate(data["text"]):
        text = text.strip()
        if not text or data["level"][i] != 5:
            continue
        yc = y0 + (data["top"][i] + data["height"][i] / 2) / scale
        words.append((yc, text))
    out = []
    for top, bot in bands:
        out.append(" ".join(t for yc, t in words if top - 1 <= yc <= bot + 1))
    return out


# Per-cell re-OCR variants for rows that fail the table's own arithmetic:
# scales, thresholds and page segmentation modes.
_VARIANTS = (
    (4, None, 7),
    (6, None, 7),
    (8, None, 7),
    (4, 150, 7),
    (6, 150, 7),
)


def _ocr_cell(
    src: np.ndarray,
    x0: int,
    x1: int,
    top: int,
    bot: int,
    scale: int,
    thr: int | None,
    psm: int = 7,
) -> str:
    crop = src[top:bot, x0:x1]
    im = Image.fromarray(crop)
    im = im.resize((max(1, im.width * scale), max(1, im.height * scale)), Image.LANCZOS)
    if thr is not None:
        arr = np.asarray(im)
        im = Image.fromarray(np.where(arr < thr, 0, 255).astype(np.uint8))
    return pytesseract.image_to_string(
        im, config=f"--psm {psm} -c tessedit_char_whitelist=-0123456789."
    ).strip()


def _ocr_variants(
    gray: np.ndarray,
    crops: list[tuple[int, int, int, int, np.ndarray, int | None]],
    chunk: int = 24,
) -> list[str]:
    """OCR many variant crops of one column with few tesseract calls.

    Each crop is upscaled, optionally thresholded, and the crops are stacked
    into blocks separated by blank bands; every crop gets the words read
    inside its own band of the block.
    """
    if not crops:
        return []
    scale = 4
    blocks: list[list[str]] = [[] for _ in crops]
    for start in range(0, len(crops), chunk):
        part = crops[start : start + chunk]
        scaled = []
        for x0, x1, top, bot, channel, thr in part:
            arr = np.ascontiguousarray(channel[top:bot, x0:x1])
            if thr is not None:
                arr = np.where(arr < thr, 0, 255).astype(np.uint8)
            im = Image.fromarray(arr)
            im = im.resize((im.width * scale, im.height * scale), Image.LANCZOS)
            scaled.append(np.asarray(im))
        width = max(a.shape[1] for a in scaled)
        height = sum(a.shape[0] for a in scaled) + 10 * (len(scaled) + 1)
        canvas = np.full((height, width), 255, dtype=np.uint8)
        spans = []
        y = 10
        for arr in scaled:
            canvas[y : y + arr.shape[0], : arr.shape[1]] = arr
            spans.append((y, y + arr.shape[0]))
            y += arr.shape[0] + 10
        data = pytesseract.image_to_data(
            Image.fromarray(canvas),
            config="--psm 6 -c tessedit_char_whitelist=-0123456789.",
            output_type=pytesseract.Output.DICT,
        )
        words = []
        for i, text in enumerate(data["text"]):
            text = text.strip()
            if not text or data["level"][i] != 5:
                continue
            words.append((data["top"][i] + data["height"][i] / 2, text))
        for (y0, y1), acc in zip(spans, blocks[start : start + chunk], strict=False):
            acc.append(" ".join(t for yc, t in words if y0 <= yc < y1))
    return [" ".join(x for x in block if x) for block in blocks]


def _parse_num(text: str) -> float | None:
    text = re.sub(r"[^0-9.\-]", "", text.replace(",", ""))
    # minus signs only count at the start (tesseract appends stray dashes)
    text = text.replace("-", "") if not text.startswith("-") else "-" + text[1:].replace("-", "")
    if text.startswith("."):
        text = "0" + text
    elif text.startswith("-."):
        text = "-0" + text[1:]
    if text.count(".") > 1:
        head, tail = text.rsplit(".", 1)
        text = head.replace(".", "") + "." + tail
    if not re.fullmatch(r"-?\d+(\.\d+)?", text or ""):
        return None
    value = float(text)
    return None if abs(value) > 1000 else round(value, 2)


def _point_candidates(text: str) -> list[float]:
    """Format-aware extra readings: NAMO, ayer, hoy, diff and capacity are
    always printed with two decimals, so a digit run whose decimal point was
    lost in OCR also yields value/100. Both readings compete in the solver;
    the printed arithmetic decides, nothing is forced.
    """
    return [
        round(int(token) / 100, 2)
        for token in re.findall(r"\d{3,6}", text)
        if 0.01 <= int(token) / 100 <= 999
    ]


def _parse_field(text: str) -> float | None:
    """Read a fixed two-decimal cell: the OCR text, or - when the decimal
    point was lost and exactly one digit-run reading exists - that reading.
    """
    value = _parse_num(text)
    if value is not None:
        return value
    points = _point_candidates(text)
    return points[0] if len(points) == 1 else None


def _parse_tokens(text: str) -> list[float | None]:
    """Numeric tokens of a cell that holds two values (NAMO and NAMINO share
    a column without a separator line in the 2021-2022 layouts)."""
    return [_parse_num(t) for t in re.findall(r"-?\d+(?:\.\d+)?", text.replace(",", ""))]


def _parse_pair(text: str) -> tuple[float | None, float | None] | None:
    """Split a merged two-value cell whose separator line was dropped.

    The second value always prints with two decimals, so the split falls
    where its trailing ``-d.dd`` run starts: "16.840.21" is 16.84 and 0.21,
    never 16.840 and 21. Single short digits without a decimal point are not
    a first value (a lone "14.90" must not split).
    """
    m = re.match(r"^\s*(-?\d[\d.,]*?)\s*(-?\d\.\d{2})\s*$", text)
    if not m or re.fullmatch(r"-?\d", m.group(1)):
        return None
    first = _parse_num(m.group(1))
    if first is None:
        points = _point_candidates(m.group(1))
        first = points[0] if len(points) == 1 else None
    return first, _parse_num(m.group(2))


def _residuals(row: StationRow | DamRow) -> tuple[float, float]:
    """|printed diff - (hoy - ayer)| and |capacity - (namo - hoy)|."""
    if row.ayer is None or row.hoy is None or row.diff is None:
        return (1.0, 1.0)
    if row.namo is None or row.capacity is None:
        return (1.0, 1.0)
    return (
        abs(row.diff - round(row.hoy - row.ayer, 2)),
        abs(row.capacity - round(row.namo - row.hoy, 2)),
    )


def _consistent(row: StationRow | DamRow) -> bool:
    """The table's own arithmetic within the validation tolerance."""
    return all(r <= TOLERANCE for r in _residuals(row))


def _exact(row: StationRow | DamRow) -> bool:
    """The table's own arithmetic matching to the printed cent."""
    return all(r < 0.005 for r in _residuals(row))


def _solve_row(
    primary: StationRow | DamRow,
    candidates: dict[str, list[float | None]],
    make: object,
    fields: tuple[str, ...],
) -> StationRow | DamRow:
    """Pick the OCR reading combination that matches the printed arithmetic
    exactly (both residuals < 0.005). Every candidate value comes from OCR of
    the same cell (the stacked-column read first, then per-cell variants);
    nothing is ever repaired or inferred. When no combination matches, the
    primary reading is kept and the row stays flagged by :func:`row_problems`.
    """
    import itertools

    pools = [candidates[f] or [None] for f in fields]
    fallback = None
    for combo in itertools.product(*pools):
        row = make(*combo)
        if _exact(row):
            return row
        if fallback is None and _consistent(row):
            fallback = row
    return fallback or primary


def read_table(path: str | Path) -> LevelTable:
    """Read one daily level-table image. Raises UnreadableTable otherwise."""
    image = Image.open(path)
    gray = np.asarray(image.convert("L"))
    rgb = np.asarray(image.convert("RGB"))

    table_date, fecha_y = _find_fecha(gray)
    if fecha_y is None:
        raise UnreadableTable("no FECHA line found")
    lines = _merge_lines([_h_lines(gray, 100), _h_lines(gray, 150)])
    sem_left, _, fill, bands = _semaforo_column(rgb, gray, lines, fecha_y + 5, fecha_y + 420)
    try:
        bands = _fit_rows(bands, lines)
    except UnreadableTable:
        # the colour fill missed several station rows (light grey or empty
        # cells); the grid lines still cross them
        bands = _rows_from_lines(rgb, gray, lines, fecha_y + 5, fecha_y + 420)
        if bands is None:
            raise
    y0, y1 = bands[0][0], bands[-1][1]

    # some layouts print the NAMO/NAMINO separators fainter than the rest of
    # the grid, so the vertical lines are searched at two darkness levels
    vlines = _merge_lines([_v_lines(gray, y0 - 1, y1 + 2), _v_lines(gray, y0 - 1, y1 + 2, 170)])
    vlines = _dedupe(vlines, 14)
    # true left edge of the coloured column: the saturation run holding the window
    red, grn, blu = rgb[..., 0].astype(int), rgb[..., 1].astype(int), rgb[..., 2].astype(int)
    sat = np.maximum(np.maximum(red, grn), blu) - np.minimum(np.minimum(red, grn), blu)
    grey = (sat < 40) & (gray > 110) & (gray < 200)
    coloured = (sat > 60) | grey
    prof = coloured[y0:y1].mean(axis=0)
    # the white cell text lowers the profile of narrow cells well below 0.5
    runs = _runs_1d(prof > 0.5, 15, 90) or _runs_1d(prof > 0.4, 15, 90)
    # the search window may start a few pixels left of the coloured cells
    holder = [rn for rn in runs if rn[0] < sem_left + 60 and rn[1] > sem_left]
    if not holder:
        raise UnreadableTable("semáforo column edge not found in the colour profile")
    sem_edge = max(holder, key=lambda rn: float(prof[rn[0] : rn[1]].sum()))[0]
    left = [x for x in vlines if x < sem_edge - 3]
    if len(left) < 7:
        raise UnreadableTable(f"only {len(left)} vertical lines left of the semáforo column")
    take = min(9, len(left))
    bounds = left[-take:] + [sem_edge]
    # numeric columns = the intervals after river and station. Some yearly
    # layouts drop an interior separator (NAMO|NAMINO in 2021-2022, HOY|DIFF
    # in some 2023 images); a column is merged when most of its rows read as
    # two numeric tokens.
    fields = ("namo", "namino", "ayer", "hoy", "diff", "capacity")
    intervals = [(bounds[2 + j] + 2, bounds[3 + j] - 1) for j in range(len(bounds) - 3)]
    raw_by_interval = [_ocr_column(gray, bands, x0, x1) for x0, x1 in intervals]
    merged = []
    for raw in raw_by_interval:
        pairs = sum(
            1 for text in raw if len([t for t in _parse_tokens(text) if t is not None]) >= 2
        )
        merged.append(pairs >= 0.5 * len(bands))
    field_cols: dict[str, tuple[int, int, int]] = {}
    f = 0
    for j, is_merged in enumerate(merged):
        if f >= len(fields):
            break
        x0, x1 = intervals[j]
        if is_merged and f < len(fields) - 1:
            field_cols[fields[f]] = (x0, x1, 0)
            field_cols[fields[f + 1]] = (x0, x1, 1)
            f += 2
        else:
            field_cols[fields[f]] = (x0, x1, 0)
            f += 1
    if f != len(fields):
        raise UnreadableTable(
            f"unexpected column layout: {len(intervals)} columns, merged {merged}"
        )

    raw_by_field = {
        name: _ocr_column(gray, bands, x0, x1) for name, (x0, x1, _) in field_cols.items()
    }
    # fields that share one interval (a dropped separator line) are parsed as
    # a printed two-decimal pair, e.g. "16.840.21" = 16.84 | 0.21
    shared: dict[tuple[int, int], int] = Counter()
    for x0, x1, _ in field_cols.values():
        shared[(x0, x1)] += 1
    columns: dict[str, list[float | None]] = {name: [] for name in fields}
    for name, (x0, x1, token) in field_cols.items():
        paired = shared[(x0, x1)] > 1
        for k in range(len(bands)):
            raw = raw_by_field[name][k]
            value = _parse_pair(raw)[token] if paired else None
            if value is None:
                tokens = _parse_tokens(raw)
                value = tokens[token] if len(tokens) > token else None
            if value is None and token == 0 and name != "namino":
                points = _point_candidates(raw)
                value = points[0] if len(points) == 1 else None
            columns[name].append(value)

    blue = np.ascontiguousarray(rgb[..., 2])

    stations = []
    for k in range(len(STATIONS)):
        primary = StationRow(STATIONS[k][0], STATIONS[k][1], *(columns[n][k] for n in fields))
        if _consistent(primary):
            stations.append(primary)
            continue
        cands = _cell_candidates(gray, bands[k], fields, field_cols, raw_by_field, k, blue)
        stations.append(
            _solve_row(
                primary,
                cands,
                lambda *c, k=k: StationRow(STATIONS[k][0], STATIONS[k][1], *c),
                fields,
            )
        )

    dams = _read_dams(gray, blue, lines, y1)
    return LevelTable(_page_date(path), table_date, tuple(stations), dams)


def _split_combined(texts: list[str]) -> tuple[list[float | None], list[float | None]]:
    namo, namino = [], []
    for text in texts:
        tokens = _parse_tokens(text)
        namo.append(tokens[0] if len(tokens) >= 1 else None)
        namino.append(tokens[1] if len(tokens) >= 2 else None)
    return namo, namino


def _cell_candidates(
    gray: np.ndarray,
    band: tuple[int, int],
    fields: tuple[str, ...],
    field_cols: dict[str, tuple[int, int, int]],
    raw_by_field: dict[str, list[str]],
    k: int,
    blue: np.ndarray,
) -> dict[str, list[float | None]]:
    """Per-cell OCR readings for one row, primary stacked read first.

    The difference column is printed in colour, so it also gets the blue
    channel, where red/orange ink is dark.
    """
    top, bot = band
    cands: dict[str, list[float | None]] = {}
    for name in fields:
        x0, x1, token = field_cols[name]
        raws = [raw_by_field[name][k]]
        channels = (gray, blue) if name == "diff" else (gray,)
        crops = [
            (x0, x1, max(0, a), b, channel, thr)
            for a, b in (
                (top, bot),
                (top + 1, bot),
                (top + 2, bot - 1),
                (top + 1, bot - 2),
                # some layouts set the digits tight against the lower grid
                # line, so the crop is extended below it too
                (top, bot + 2),
                (max(0, top - 1), bot + 2),
            )
            for channel in channels
            for thr in (None, 150, 90)
        ]
        raws.extend(_ocr_variants(gray, crops))
        seen: list[float | None] = []
        for raw in raws:
            tokens = _parse_tokens(raw)
            value = tokens[token] if len(tokens) > token else None
            if value is not None and value not in seen:
                seen.append(value)
            if name != "namino" and token == 0:
                for value in _point_candidates(raw):
                    if value not in seen:
                        seen.append(value)
        cands[name] = seen
    return cands


def _read_dams(
    gray: np.ndarray, blue: np.ndarray, lines: list[int], station_bottom: int
) -> tuple[DamRow, ...]:
    """The 4 dam rows below the station table, found by their 6-column grid."""
    candidates = [
        (a, b)
        for a, b in zip(lines, lines[1:], strict=False)
        if a > station_bottom and 10 <= b - a <= 260
    ]
    good: list[tuple[int, int]] = []
    for a, b in candidates:
        if len(_v_lines(gray, a, b + 1)) in (5, 6, 7, 8, 9):
            good.append((a, b))
    # keep the last 4 consecutive candidates (the dam rows)
    group: list[tuple[int, int]] = []
    for band in good:
        if group and band[0] - group[-1][1] <= 4:
            group.append(band)
        else:
            group = [band]
        if len(group) > 4:
            group = group[-4:]
    empty = tuple(DamRow(name, None, None, None, None, None) for name in DAMS)
    if len(group) < 4:
        return empty
    vlines = _dedupe(_v_lines(gray, group[0][0], group[-1][1] + 2), 14)
    if len(vlines) < 7:
        return empty
    names = ("namo", "ayer", "hoy", "diff", "capacity")
    bounds = vlines[1:7]
    columns = {
        name: [_parse_field(t) for t in _ocr_column(gray, group, bounds[i] + 2, bounds[i + 1] - 1)]
        for i, name in enumerate(names)
    }
    dams = []
    for k in range(4):
        primary = DamRow(DAMS[k], *(columns[n][k] for n in names))
        if _consistent(primary):
            dams.append(primary)
            continue
        cands: dict[str, list[float | None]] = {}
        for i, name in enumerate(names):
            seen: list[float | None] = [columns[name][k]]
            channels = (gray, blue) if name == "diff" else (gray,)
            crops = [
                (a, bounds[i + 1], group[k][0] + 1, group[k][1], channel, thr)
                for a in (bounds[i] + 1, bounds[i] + 5)
                for channel in channels
                for thr in (None, 150)
            ]
            for raw in _ocr_variants(gray, crops):
                v = _parse_num(raw)
                if v is not None and v not in seen:
                    seen.append(v)
                if name != "namino":
                    for v in _point_candidates(raw):
                        if v not in seen:
                            seen.append(v)
            cands[name] = seen
        dams.append(_solve_row(primary, cands, lambda *c, k=k: DamRow(DAMS[k], *c), names))
    return tuple(dams)


def modal_namos(tables: list[LevelTable]) -> dict[tuple[str, str], float]:
    """Most common NAMO per (kind, station) over all parsed rows."""
    counts: Counter[tuple[str, str, float]] = Counter()
    for table in tables:
        for row in table.stations:
            if row.namo is not None:
                counts[("station", row.station, row.namo)] += 1
        for row in table.dams:
            if row.namo is not None:
                counts[("dam", row.name, row.namo)] += 1
    modes: dict[tuple[str, str], float] = {}
    for (kind, station, namo), count in counts.items():
        key = (kind, station)
        if key not in modes or count > counts[(*key, modes[key])]:
            modes[key] = namo
    return modes


def row_problems(row: StationRow | DamRow, namo_mode: float | None) -> list[str]:
    """Reasons a parsed row fails validation. Values are never repaired."""
    problems: list[str] = []
    for field in ("namo", "ayer", "hoy", "diff", "capacity"):
        if getattr(row, field) is None:
            problems.append(f"unreadable_{field}")
    if row.ayer is not None and row.hoy is not None and row.diff is not None:
        if abs(row.diff - round(row.hoy - row.ayer, 2)) > TOLERANCE:
            problems.append("diff_mismatch")
    if row.namo is not None and row.hoy is not None and row.capacity is not None:
        if abs(row.capacity - round(row.namo - row.hoy, 2)) > TOLERANCE:
            problems.append("capacity_mismatch")
    if namo_mode is not None and row.namo is not None:
        if abs(row.namo - namo_mode) > TOLERANCE:
            problems.append("namo_mismatch")
    return problems


def _page_date(path: str | Path) -> dt.date | None:
    m = re.search(r"(\d{4}-\d{2}-\d{2})", Path(path).stem)
    return dt.date.fromisoformat(m.group(1)) if m else None


FIRST_TABLE = dt.date(2021, 8, 1)
TRUSTED_FLAGS = ("ok", "printed", "printed_other")


def obs_date(
    page: dt.date | None, table: dt.date | None, latest: dt.date | None = None
) -> tuple[dt.date | None, str]:
    """Resolved observation date of a table and its flag.

    IPCET reuses image file names every year, so a page can serve a table
    from another day or year (a 2023 page showing a 2021 table, or an old
    page whose file was overwritten later). The printed FECHA is therefore
    the observation date whenever it is possible (between 2021-08-01, the
    first table, and ``latest``, the download date): flag "ok" when it is
    within 3 days of the page date, "printed_other" otherwise. A printed date
    outside that range is an OCR misread: its day and month are kept with
    the page year when they fall within 3 days of the page date ("ok", with
    the Dec/Jan wrap handled), else the page date is returned as "far_off".
    An unreadable FECHA gives the page date as "no_fecha". Flags other than
    "ok", "printed" and "printed_other" need confirmation by another table
    before their values are used (:func:`station_series`).
    """
    latest = latest or dt.date.today()
    if table is None:
        return page, "no_fecha"
    if page is None:
        return table, "printed"
    if FIRST_TABLE <= table <= latest:
        return table, ("ok" if abs((table - page).days) <= 3 else "printed_other")

    def with_year(year: int) -> dt.date | None:
        try:
            return dt.date(year, table.month, table.day)
        except ValueError:
            return None

    candidate = with_year(page.year)
    if candidate is not None and (candidate - page).days > 180:
        candidate = with_year(page.year - 1)
    elif candidate is not None and (candidate - page).days < -180:
        candidate = with_year(page.year + 1)
    if candidate is not None and abs((candidate - page).days) <= 3:
        return candidate, "ok"
    return page, "far_off"


def station_series(rows: pd.DataFrame, agree: float = 0.05) -> pd.DataFrame:
    """Daily stage of one station from its table rows.

    ``rows`` has obs_date, obs_flag, valid, ayer, hoy and diff. Each valid
    table gives yesterday's value to obs_date - 1 and today's value to
    obs_date, except when the printed difference is zero: then today's value
    may be yesterday's copied before the 06:00 reading and is not used.
    Tables with an untrusted date (no_fecha, far_off) count only when one of
    their values equals, to the cent, a value of a trusted table for the
    same day. A day whose values disagree by more than ``agree`` metres is
    dropped. Returns stage_m (median of the agreeing values) and n_sources,
    indexed by date.
    """
    valid = rows[rows["valid"].astype(bool)]
    items = []
    for number, row in enumerate(valid.itertuples(index=False)):
        day = pd.Timestamp(row.obs_date)
        trusted = row.obs_flag in TRUSTED_FLAGS
        items.append((day - pd.Timedelta(days=1), float(row.ayer), trusted, number))
        if round(float(row.diff), 2) != 0:
            items.append((day, float(row.hoy), trusted, number))
    values = pd.DataFrame(items, columns=["date", "stage_m", "trusted", "table"])
    if values.empty:
        return pd.DataFrame(columns=["stage_m", "n_sources"]).rename_axis("date")
    trusted = values[values["trusted"]]
    confirmed = set()
    for table, part in values[~values["trusted"]].groupby("table"):
        for item in part.itertuples(index=False):
            same = trusted.loc[trusted["date"] == item.date, "stage_m"]
            if ((same - item.stage_m).abs() < 0.005).any():
                confirmed.add(table)
    used = values[values["trusted"] | values["table"].isin(confirmed)]
    out = []
    for day, part in used.groupby("date"):
        if part["stage_m"].max() - part["stage_m"].min() > agree:
            continue
        out.append((day, round(float(part["stage_m"].median()), 2), len(part)))
    return pd.DataFrame(out, columns=["date", "stage_m", "n_sources"]).set_index("date")
