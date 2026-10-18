"""Live input from the IPCET daily river-level table, pre-registered in L32.

The IPCET "semaforo de niveles de rios" page publishes a table image with the
06:00 stage of the previous day and of the current day (L28). It is not
published every day (L31), so a live forecast must know, on each issue day,
which value is actually visible and how old it is. This module provides:

- ``levels_census``: which days have a published level table (from the
  day-by-day census of L31).
- ``tile_availability``: that census repeated over historical days, aligned on
  weekday, to simulate the observed availability pattern.
- ``revealed_values``: the reveal rules of L32 - a table for day ``k`` becomes
  visible on ``k`` itself if ``k`` is Monday-Friday, otherwise on the next
  Monday; a visible table reveals the 06:00 value of ``k`` and of ``k-1``.
- ``boca_rows`` / ``boca_visible_on``: the real visibility dates of L35 - for
  each observed day, the earliest date on which a published table carrying it
  could be seen.
- ``real_revealed_values``: the L35 rules on an explicit per-day visibility
  series - a value observed on day ``k`` is usable at issue day ``i`` only when
  it is visible by ``i``.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from riverforecast import data, levels

CENSUS = data.RAW / "ipcet" / "levels_census.csv"
READER_OUTPUT = data.RAW / "ipcet" / "levels.csv"
# Days whose level table is only reachable through the direct image URL (L31).
DIRECT_URL_DAYS = ("2026-01-07", "2026-03-02", "2026-04-29", "2026-08-01")
# 2026 image names of the form semaforo_DDMMYY.jpeg / .jpg (L31).
TABLE_IMAGE = r"semaforo_\d{6}\.jpe?g"


def levels_census(path: Path | None = None) -> pd.Series:
    """Days with a published IPCET level table, as a boolean daily series.

    A day counts when the census flag ``has_levels_table`` is 1 or when any
    recorded image name matches ``semaforo_\\d{6}.jpe?g``; the four days that
    are only reachable through their direct image URL are always added.
    """
    if path is None:
        path = CENSUS
    frame = pd.read_csv(path)
    dates = pd.to_datetime(frame["date"])
    images = frame["images"].fillna("").astype(str)
    exists = pd.Series(
        (frame["has_levels_table"].to_numpy() == 1)
        | images.str.contains(TABLE_IMAGE, regex=True).to_numpy(),
        index=dates,
    )
    extra = pd.DatetimeIndex(DIRECT_URL_DAYS)
    exists = exists.reindex(exists.index.union(extra))
    exists.loc[extra] = True
    return exists.sort_index()


def tile_availability(census: pd.Series, days: pd.DatetimeIndex) -> pd.Series:
    """Repeat the census of table existence over ``days``, aligned on weekday.

    Alignment rule (deterministic): for every weekday separately, the census
    days with that weekday, in date order, are tiled cyclically - the j-th day
    with that weekday in ``days`` (counted from the first one) takes the
    availability of the (j mod n)-th census day with that weekday. Every
    weekday present in ``days`` must therefore appear in ``census``.
    """
    census = census.sort_index().astype(bool)
    by_weekday = {
        weekday: group.to_numpy()
        for weekday, group in census.groupby(census.index.weekday, sort=True)
    }
    missing = sorted({day.weekday() for day in days} - set(by_weekday))
    if missing:
        raise ValueError(f"census lacks weekday(s) {missing} (0=Monday) that days need")
    counts = dict.fromkeys(by_weekday, 0)
    values = []
    for day in days:
        weekday = day.weekday()
        sequence = by_weekday[weekday]
        values.append(sequence[counts[weekday] % len(sequence)])
        counts[weekday] += 1
    return pd.Series(values, index=pd.DatetimeIndex(days), dtype=bool)


def revealed_values(stage: pd.Series, table_exists: pd.Series) -> pd.DataFrame:
    """Latest revealed 06:00 value on each day of ``table_exists`` (L32 rules).

    A table for day ``k`` is visible on ``k`` if ``k`` is Monday-Friday, else
    on the next Monday. A visible table reveals the values of ``k`` and
    ``k-1``; only days where ``stage`` is observed contribute a value. For
    each day ``i`` the columns give the latest revealed value (``obs_last``),
    the day it was observed on (``obs_day``) and its age in days (``obs_age``,
    0 when today's value is visible). Nothing within 7 days leaves all three
    NaN. Only days present in ``table_exists`` are considered: a stage value
    on a day outside that index is never revealed.
    """
    tables = table_exists.sort_index().astype(bool)
    days = pd.DatetimeIndex(tables.index)
    weekday = days.weekday.to_numpy()
    delay = np.where(weekday <= 4, 0, 7 - weekday)
    visible = pd.Series(days + pd.to_timedelta(delay, unit="D")).where(tables.to_numpy())
    # A value on day d is revealed by the table of d (visible on its own
    # visibility day) or by the table of d+1, whichever becomes visible first.
    first_visible = pd.DataFrame({"own": visible, "next": visible.shift(-1)}).min(axis=1)
    first_visible.index = days

    values = stage.sort_index().reindex(days).to_numpy(dtype=float)
    day_array = days.to_numpy()
    first = first_visible.to_numpy()
    n = len(days)
    obs_last = np.full(n, np.nan)
    obs_day = np.full(n, np.datetime64("NaT"), dtype="datetime64[ns]")
    obs_age = np.full(n, np.nan)
    for i in range(n):
        for back in range(8):  # ages 0..7; beyond 7 days the value is not used (L32)
            p = i - back
            if p < 0:
                break
            if np.isnan(values[p]) or np.isnat(first[p]) or first[p] > day_array[i]:
                continue
            obs_day[i] = day_array[p]
            obs_last[i] = values[p]
            obs_age[i] = back
            break
    return pd.DataFrame({"obs_last": obs_last, "obs_day": obs_day, "obs_age": obs_age}, index=days)


def boca_rows(path: Path | None = None) -> pd.DataFrame:
    """Boca del Cerro rows of the reader output, with obs_date re-resolved.

    The reader output stores the observation date of an earlier reader
    version, so it is re-resolved here from the page and printed dates with
    :func:`riverforecast.levels.obs_date`, exactly as experiment 11 does; the
    printed date wins over the page date because IPCET reuses image file
    names (L34). ``latest`` for the resolution is the newest page date in the
    file, which keeps the result deterministic.
    """
    if path is None:
        path = READER_OUTPUT
    frame = pd.read_csv(path, parse_dates=["page_date", "table_date"])
    rows = frame[
        (frame["kind"] == "station") & frame["station"].astype(str).str.contains("Boca", na=False)
    ].copy()
    latest = rows["page_date"].max()
    resolved = [
        levels.obs_date(
            None if pd.isna(page) else page.date(),
            None if pd.isna(printed) else printed.date(),
            None if pd.isna(latest) else latest.date(),
        )
        for page, printed in zip(rows["page_date"], rows["table_date"], strict=True)
    ]
    rows["obs_date"] = pd.to_datetime([day for day, _ in resolved])
    rows["obs_flag"] = [flag for _, flag in resolved]
    return rows


def boca_visible_on(rows: pd.DataFrame) -> pd.Series:
    """Earliest date on which each observed day's 06:00 value could be seen (L35).

    ``rows`` holds the Boca del Cerro rows of the reader output with the
    resolved observation date (:func:`boca_rows`). A table with observation
    date ``D`` and flag "ok" is visible on ``max(D, page_date)`` (the page
    cannot be seen before it is uploaded); any other flag is visible on
    ``D + 1`` - conservative, because those pages do not tell when the table
    was first online. A table carries ``D`` (hoy, but only when the printed
    difference is not zero: then the value may be yesterday's copied before
    the 06:00 reading, L34) and ``D - 1`` (ayer). A day carried by several
    tables takes the earliest visibility, never earlier than the day itself.
    Invalid rows and rows without a resolved date carry nothing.
    """
    candidates: dict[pd.Timestamp, list[pd.Timestamp]] = {}
    valid = rows[rows["valid"].astype(str).str.lower() == "true"]
    for row in valid.itertuples(index=False):
        if pd.isna(row.obs_date):
            continue
        day = pd.Timestamp(row.obs_date)
        if row.obs_flag == "ok" and not pd.isna(row.page_date):
            visible = max(day, pd.Timestamp(row.page_date))
        else:
            visible = day + pd.Timedelta(days=1)
        if row.diff is not None and not pd.isna(row.diff) and round(float(row.diff), 2) != 0:
            candidates.setdefault(day, []).append(visible)
        if row.ayer is not None and not pd.isna(row.ayer):
            candidates.setdefault(day - pd.Timedelta(days=1), []).append(visible)
    return pd.Series({day: max(day, min(when)) for day, when in candidates.items()}).sort_index()


def real_revealed_values(
    series: pd.Series, visible_on: pd.Series, issue_days: pd.DatetimeIndex
) -> pd.DataFrame:
    """Latest revealed value on each issue day with explicit visibility dates (L35).

    Like :func:`revealed_values`, but the visibility date of each observed day
    is given explicitly by ``visible_on`` (indexed like ``series``): a value
    observed on day ``k`` is usable at issue day ``i`` only when
    ``visible_on[k] <= i`` and ``k <= i``. The newest usable value within
    7 days is returned as ``obs_last`` / ``obs_day`` / ``obs_age``; when there
    is none, all three are NaN and the forecast falls back to the GloFAS-only
    model.
    """
    issue_days = pd.DatetimeIndex(issue_days)
    ordered = series.sort_index()
    days = ordered.index
    values = ordered.to_numpy(dtype=float)
    stamps = days.to_numpy(dtype="datetime64[ns]")
    vis = pd.Series(visible_on).reindex(days).to_numpy(dtype="datetime64[ns]")
    cutoff = np.timedelta64(7, "D")
    n = len(issue_days)
    obs_last = np.full(n, np.nan)
    obs_day = np.full(n, np.datetime64("NaT"), dtype="datetime64[ns]")
    obs_age = np.full(n, np.nan)
    for row, issue in enumerate(issue_days):
        stamp = np.datetime64(issue, "ns")
        p = int(np.searchsorted(stamps, stamp, side="right")) - 1
        while p >= 0 and stamp - stamps[p] <= cutoff:
            if not np.isnan(values[p]) and not np.isnat(vis[p]) and vis[p] <= stamp:
                obs_day[row] = stamps[p]
                obs_last[row] = values[p]
                obs_age[row] = float((stamp - stamps[p]) / np.timedelta64(1, "D"))
                break
            p -= 1
    return pd.DataFrame(
        {"obs_last": obs_last, "obs_day": obs_day, "obs_age": obs_age}, index=issue_days
    )
