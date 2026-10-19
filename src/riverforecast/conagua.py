"""CONAGUA SIH station files: download, decode and strict parsing.

The Sistema de Información Hidrológica (SIH) publishes one CSV per station with a
few ``Key: value`` metadata lines, a ``Fecha, ...`` column header and one row per
day. Missing values are written as ``-`` or left empty.
"""

from __future__ import annotations

import math
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime

import pandas as pd

SIH_URL = "https://sih.conagua.gob.mx/basedatos/{kind}/{code}.csv"
KINDS = {"hidros": "Hidros", "climas": "Climas"}

COLUMNS = {
    "Nivel(m)": "level_m",
    "Gasto(m³/s)": "discharge_m3s",
    "Gasto en Rio(m3/s)": "discharge_m3s",
    "Precipitación(mm)": "precip_mm",
    "Temperatura Ambiente(ºC)": "temp_mean_c",
    "Temperatura Máxima(ºC)": "temp_max_c",
    "Temperatura Mínima(ºC)": "temp_min_c",
    "Evaporación(mm)": "evaporation_mm",
}
MISSING = {"", "-"}


class StationFileError(ValueError):
    """A station file does not follow the SIH format."""


class DownloadError(RuntimeError):
    """The SIH server did not return a station file."""


@dataclass(frozen=True)
class Station:
    metadata: dict[str, str]
    data: pd.DataFrame


@dataclass(frozen=True)
class GapSummary:
    days: int
    observed: int
    coverage: float
    longest_gaps: list[tuple[pd.Timestamp, pd.Timestamp, int]]


def decode_sih_bytes(raw: bytes) -> str:
    """Decode a SIH file served as UTF-8, Latin-1 or double-encoded UTF-8."""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("latin-1")
    if "Ã" in text or "Â" in text:
        try:
            return text.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return text


def download_station(kind: str, code: str, timeout: float = 120) -> str:
    """Download and decode one SIH station file (``kind`` is hidros or climas)."""
    url = SIH_URL.format(kind=KINDS[kind], code=code)
    request = urllib.request.Request(url, headers={"User-Agent": "tabasco-river-forecast"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return decode_sih_bytes(response.read())
    except urllib.error.HTTPError as error:
        raise DownloadError(f"HTTP {error.code}") from None
    except (urllib.error.URLError, TimeoutError) as error:
        reason = getattr(error, "reason", error)
        raise DownloadError(f"network error ({reason})") from None


def parse_sih_csv(text: str) -> Station:
    """Parse a SIH station file into metadata and a daily DataFrame.

    Two layouts exist: ``Fecha, ...`` with ``YYYY/MM/DD`` dates, and (some
    Chiapas stations) ``Estacion,Fecha,...`` with ISO dates and a leading
    station key that must equal the ``Clave`` metadata on every row.

    Raises StationFileError, naming the line, on any malformed content: wrong
    field count, invalid date or number, duplicate or out-of-order dates, a
    foreign station key, or an unknown column.
    """
    lines = text.splitlines()

    def first_fields(line: str) -> list[str]:
        return [field.strip() for field in line.split(",")[:2]]

    header_at = next(
        (
            i
            for i, line in enumerate(lines)
            if first_fields(line)[0] == "Fecha" or first_fields(line) == ["Estacion", "Fecha"]
        ),
        None,
    )
    if header_at is None:
        raise StationFileError("no 'Fecha' column header")
    keyed = first_fields(lines[header_at])[0] == "Estacion"
    date_format = "%Y-%m-%d" if keyed else "%Y/%m/%d"

    metadata = {}
    for line in lines[:header_at]:
        key, sep, value = line.partition(":")
        if sep:
            metadata[key.strip()] = value.strip()

    names = [name.strip() for name in lines[header_at].split(",")[1 + keyed :]]
    for name in names:
        if name not in COLUMNS:
            raise StationFileError(f"unknown column {name!r}")
    columns = [COLUMNS[name] for name in names]

    dates, rows = [], []
    for number, line in enumerate(lines[header_at + 1 :], start=header_at + 2):
        if not line.strip():
            continue
        fields = [field.strip() for field in line.split(",")]
        if keyed:
            if fields[0] != metadata.get("Clave"):
                raise StationFileError(
                    f"line {number}: station {fields[0]!r} is not {metadata.get('Clave')!r}"
                )
            fields = fields[1:]
        if len(fields) != len(columns) + 1:
            raise StationFileError(
                f"line {number}: expected {len(columns) + 1} fields, got {len(fields)}"
            )
        try:
            date = datetime.strptime(fields[0], date_format)
        except ValueError:
            raise StationFileError(f"line {number}: invalid date {fields[0]!r}") from None
        if dates and date <= dates[-1]:
            problem = "duplicate date" if date == dates[-1] else "date"
            suffix = "" if date == dates[-1] else f" is not after {dates[-1]:%Y-%m-%d}"
            raise StationFileError(f"line {number}: {problem} {date:%Y-%m-%d}{suffix}")
        dates.append(date)
        rows.append([_number(field, number) for field in fields[1:]])

    data = pd.DataFrame(rows, index=pd.DatetimeIndex(dates, name="date"), columns=columns)
    return Station(metadata=metadata, data=data.astype(float))


def _number(field: str, line: int) -> float:
    if field in MISSING:
        return math.nan
    try:
        return float(field)
    except ValueError:
        raise StationFileError(f"line {line}: invalid number {field!r}") from None


def gap_summary(series: pd.Series, start: str, end: str, top: int = 5) -> GapSummary:
    """Coverage of a daily series over [start, end] and its longest runs of missing days."""
    index = pd.date_range(start, end, freq="D")
    present = series.reindex(index).notna().to_numpy()

    gaps = []
    run_start = None
    for day, ok in zip(index, present, strict=True):
        if not ok and run_start is None:
            run_start = day
        elif ok and run_start is not None:
            gaps.append((run_start, day - pd.Timedelta(days=1)))
            run_start = None
    if run_start is not None:
        gaps.append((run_start, index[-1]))

    ranked = sorted(((a, b, (b - a).days + 1) for a, b in gaps), key=lambda gap: (-gap[2], gap[0]))
    observed = int(present.sum())
    return GapSummary(
        days=len(index),
        observed=observed,
        coverage=observed / len(index),
        longest_gaps=ranked[:top],
    )
