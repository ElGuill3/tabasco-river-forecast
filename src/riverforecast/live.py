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
  could be seen; since L39 the HTTP Last-Modified upload time moves that date
  when the table went online after the 11:00 issue (fix 3).
- ``boca_table_visibility``: the visibility date of each table row itself.
- ``real_revealed_values``: the L35 rules on an explicit per-day visibility
  series - a value observed on day ``k`` is usable at issue day ``i`` only when
  it is visible by ``i``.
- ``causal_revealed_values``: the strictly causal live series of L39 (fix 4) -
  for each issue day, the station series rebuilt from the table rows visible
  by that day only, so a later table can never confirm or drop an earlier
  value before it is visible.
- ``obs_day_discharge``: the log discharge on the day of the live value, from
  the same past-discharge series the features use (L39 fix 2).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from riverforecast import data, levels

CENSUS = data.RAW / "ipcet" / "levels_census.csv"
READER_OUTPUT = data.RAW / "ipcet" / "levels.csv"
MANIFEST = data.RAW / "ipcet" / "levels" / "manifest.csv"
# Issue hour of the live forecast, local time (UTC-6): a table uploaded after
# 11:00 local is first usable the next day (L39 fix 3).
ISSUE_HOUR = 11
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


def boca_table_visibility(rows: pd.DataFrame, last_modified: pd.Series | None = None) -> pd.Series:
    """Visibility date of each table row itself (L35 rule, upload times of L39).

    A table with observation date ``D`` and flag "ok" is visible on
    ``max(D, page_date)`` (the page cannot be seen before it is uploaded); any
    other flag is visible on ``D + 1`` - conservative, because those pages do
    not tell when the table was first online. When ``last_modified`` is given
    (a series indexed by the ``sha256`` of each row, HTTP dates in GMT), the
    table is additionally visible only from its upload day: local time is GMT
    - 6 h, the upload day is the local date, plus one day when uploaded after
    :data:`ISSUE_HOUR` (11:00 local), so a forecast issued at 11:00 cannot see
    it that day. The upload rule is applied only when the upload date is 0-14
    days after the page date (a file re-uploaded years later keeps the current
    rule, L31/L39). The result never moves earlier than the current rule.
    """
    stamps: pd.Series | None = None
    if last_modified is not None:
        stamps = pd.to_datetime(pd.Series(last_modified))
    visible = pd.Series(pd.NaT, index=rows.index, dtype="datetime64[ns]")
    for idx, row in rows.iterrows():
        if pd.isna(row["obs_date"]):
            continue
        day = pd.Timestamp(row["obs_date"])
        if row["obs_flag"] == "ok" and not pd.isna(row["page_date"]):
            when = max(day, pd.Timestamp(row["page_date"]))
        else:
            when = day + pd.Timedelta(days=1)
        if stamps is not None:
            sha = row.get("sha256")
            if sha is not None and sha in stamps.index and not pd.isna(stamps[sha]):
                local = stamps[sha] - pd.Timedelta(hours=6)
                upload_day = local.normalize()
                if (local.hour, local.minute) > (ISSUE_HOUR, 0):
                    upload_day += pd.Timedelta(days=1)
                page = row["page_date"]
                if not pd.isna(page) and 0 <= (upload_day - pd.Timestamp(page)).days <= 14:
                    when = max(when, upload_day)
        visible.loc[idx] = when
    return visible


def boca_visible_on(rows: pd.DataFrame, last_modified: pd.Series | None = None) -> pd.Series:
    """Earliest date on which each observed day's 06:00 value could be seen (L35).

    ``rows`` holds the Boca del Cerro rows of the reader output with the
    resolved observation date (:func:`boca_rows`). Each table is visible from
    :func:`boca_table_visibility` (the L35 rule, moved later by the HTTP
    Last-Modified upload time when ``last_modified`` is given, L39 fix 3). A
    table carries ``D`` (hoy, but only when the printed difference is not
    zero: then the value may be yesterday's copied before the 06:00 reading,
    L34) and ``D - 1`` (ayer). A day carried by several tables takes the
    earliest visibility, never earlier than the day itself. Invalid rows and
    rows without a resolved date carry nothing.
    """
    per_table = boca_table_visibility(rows, last_modified)
    candidates: dict[pd.Timestamp, list[pd.Timestamp]] = {}
    valid = rows[rows["valid"].astype(str).str.lower() == "true"]
    for row in valid.itertuples(index=True):
        if pd.isna(row.obs_date):
            continue
        day = pd.Timestamp(row.obs_date)
        when = per_table.loc[row.Index]
        if pd.isna(when):
            continue
        if row.diff is not None and not pd.isna(row.diff) and round(float(row.diff), 2) != 0:
            candidates.setdefault(day, []).append(when)
        if row.ayer is not None and not pd.isna(row.ayer):
            candidates.setdefault(day - pd.Timedelta(days=1), []).append(when)
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


def obs_day_discharge(revealed: pd.DataFrame, past: pd.Series) -> pd.Series:
    """Log discharge on the day of the live value (feature ``obs_day_q``).

    The third live feature (L32) is the discharge of the day the visible value
    was observed on, so the model can turn the GloFAS change since that day
    into a stage change. It must come from the same past-discharge series the
    ``past_*`` features use (L37/L39 fix 2): the day-0 archive in operation,
    ``data.glofas_training_past`` in training - never the reanalysis, which is
    not available in real time. ``revealed`` is a frame with ``obs_day`` (as
    from :func:`revealed_values` or :func:`real_revealed_values`); ``past`` is
    daily discharge. Returns log discharge indexed like ``revealed``, NaN
    where the obs day is missing or has no discharge.
    """
    days = pd.DatetimeIndex(revealed["obs_day"])
    values = past.sort_index().reindex(days).to_numpy(dtype=float)
    return pd.Series(np.log(values), index=revealed.index)


def causal_revealed_values(
    rows: pd.DataFrame,
    visible_on_rows: pd.Series,
    issue_days: pd.DatetimeIndex,
    agree: float = 0.05,
) -> pd.DataFrame:
    """Strictly causal live values: each issue day sees only its visible tables.

    For each issue day ``i`` the station series is rebuilt from the table rows
    whose visibility date (:func:`boca_table_visibility`) is at most ``i`` -
    exactly what an operational forecast could have seen - and the newest
    value within 7 days is returned as ``obs_last`` / ``obs_day`` / ``obs_age``
    like :func:`real_revealed_values`. Unlike that function on the whole
    archive, a later table can never confirm an undated one or create a
    conflict that drops a value before it is visible (L39 fix 4).

    ``rows`` holds the station rows of the reader output (as from
    :func:`boca_rows`) and ``visible_on_rows`` the per-row visibility dates
    indexed like ``rows``. The visible sets grow monotonically with the issue
    day, so the station series is computed once per distinct visibility
    threshold, which keeps ~1200 issue days affordable.
    """
    issue_days = pd.DatetimeIndex(issue_days)
    vis = pd.to_datetime(pd.Series(visible_on_rows).reindex(rows.index))
    thresholds = pd.DatetimeIndex(sorted(vis.dropna().unique()))
    result = pd.DataFrame(
        {
            "obs_last": np.full(len(issue_days), np.nan),
            "obs_day": np.full(len(issue_days), np.datetime64("NaT"), dtype="datetime64[ns]"),
            "obs_age": np.full(len(issue_days), np.nan),
        },
        index=issue_days,
    )
    series_by_threshold: dict[pd.Timestamp, pd.DataFrame] = {}
    for issue in issue_days:
        eligible = thresholds[thresholds <= issue]
        if eligible.empty:
            continue
        stamp = eligible[-1]
        if stamp not in series_by_threshold:
            keep = (vis <= stamp).fillna(False).to_numpy()
            series_by_threshold[stamp] = levels.station_series(rows[keep], agree)
        series = series_by_threshold[stamp]
        result.loc[issue] = real_revealed_values(
            series["stage_m"],
            pd.Series(series.index, index=series.index),
            pd.DatetimeIndex([issue]),
        ).iloc[0]
    return result
