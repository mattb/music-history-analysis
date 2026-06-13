"""Deterministic artist trajectory and cohort-retention measurements."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from statistics import median
from typing import Any

import numpy as np
import pandas as pd


_FREQUENCIES = {"month": "M", "year": "Y"}
_BOUND_PATTERNS = {
    "month": (re.compile(r"^\d{4}-(0[1-9]|1[0-2])$"), "YYYY-MM"),
    "year": (re.compile(r"^\d{4}$"), "YYYY"),
}


def _validate_history(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        raise ValueError("listening history must not be empty")
    if not {"timestamp", "artist"}.issubset(df.columns):
        raise ValueError("listening history requires timestamp and artist columns")
    result = df[["timestamp", "artist"]].copy()
    result["timestamp"] = pd.to_datetime(result["timestamp"], utc=True, errors="coerce")
    if result["timestamp"].isna().any():
        raise ValueError("listening history contains invalid timestamps")
    result["artist"] = result["artist"].fillna("").astype(str)
    return result.sort_values(["timestamp", "artist"], kind="stable")


def _frequency(granularity: str) -> str:
    try:
        return _FREQUENCIES[granularity]
    except KeyError as exc:
        raise ValueError("granularity must be month or year") from exc


def _period(value: str | None, granularity: str, label: str) -> pd.Period | None:
    if value is None:
        return None
    pattern, expected = _BOUND_PATTERNS[granularity]
    if not pattern.fullmatch(value):
        raise ValueError(f"{label} must use {expected} format")
    return pd.Period(value, freq=_frequency(granularity))


def _window(
    frame: pd.DataFrame,
    granularity: str,
    start: str | None,
    end: str | None,
) -> tuple[pd.Period, pd.Period, pd.Period, pd.Period]:
    freq = _frequency(granularity)
    source_periods = frame["timestamp"].dt.tz_localize(None).dt.to_period(freq)
    source_start = source_periods.min()
    source_end = source_periods.max()
    first = _period(start, granularity, "start") or source_start
    last = _period(end, granularity, "end") or source_end
    if first > last:
        raise ValueError("start must not exceed end")
    return first, last, source_start, source_end


def _display_name(names: pd.Series) -> str:
    counts = names.value_counts()
    maximum = int(counts.max())
    return sorted(str(name) for name, count in counts.items() if count == maximum)[0]


def _safe_float(value: float) -> float:
    return round(float(value), 12)


def _positive_integer(value: Any, name: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, np.integer))
        or value <= 0
    ):
        raise ValueError(f"{name} must be a positive integer")
    return int(value)


def _nonnegative_integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)) or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return int(value)


def _observation(
    start: pd.Period,
    end: pd.Period,
    source_start: pd.Period,
    source_end: pd.Period,
    counts: pd.Series,
    min_period_plays: int,
) -> dict[str, Any]:
    active_positions = np.flatnonzero(counts.to_numpy() >= min_period_plays)
    leading = int(active_positions[0]) if len(active_positions) else len(counts)
    trailing = (
        int(len(counts) - active_positions[-1] - 1)
        if len(active_positions)
        else len(counts)
    )
    return {
        "start_period": str(start),
        "end_period": str(end),
        "source_start_period": str(source_start),
        "source_end_period": str(source_end),
        "left_truncated": bool(start > source_start),
        "right_truncated": bool(end < source_end),
        "right_censored": bool(end >= source_end),
        "leading_inactivity_periods": leading,
        "trailing_inactivity_periods": trailing,
        "leading_inactivity_censored": leading > 0,
        "trailing_inactivity_censored": trailing > 0,
    }


def _prepare_trajectory(
    df: pd.DataFrame,
    granularity: str,
    start: str | None,
    end: str | None,
    min_period_plays: int,
    dormancy_periods: int,
) -> tuple[
    pd.DataFrame,
    str,
    int,
    int,
    pd.Period,
    pd.Period,
    pd.Period,
    pd.Period,
    pd.PeriodIndex,
    dict[str, Any],
    dict[str, pd.Index],
]:
    frame = _validate_history(df)
    freq = _frequency(granularity)
    min_period_plays = _positive_integer(min_period_plays, "min_period_plays")
    dormancy_periods = _positive_integer(dormancy_periods, "dormancy_periods")
    first, last, source_start, source_end = _window(frame, granularity, start, end)
    periods = pd.period_range(first, last, freq=freq)
    parameters = {
        "granularity": granularity,
        "start": start,
        "end": end,
        "min_period_plays": min_period_plays,
        "dormancy_periods": dormancy_periods,
    }
    identities = frame["artist"].map(str.casefold)
    artist_index = {
        identity: pd.Index(indices)
        for identity, indices in identities.groupby(
            identities, sort=False
        ).groups.items()
    }
    return (
        frame,
        freq,
        min_period_plays,
        dormancy_periods,
        first,
        last,
        source_start,
        source_end,
        periods,
        parameters,
        artist_index,
    )


def _artist_trajectory_from_prepared(
    prepared: tuple[
        pd.DataFrame,
        str,
        int,
        int,
        pd.Period,
        pd.Period,
        pd.Period,
        pd.Period,
        pd.PeriodIndex,
        dict[str, Any],
        dict[str, pd.Index],
    ],
    artist: str,
) -> dict[str, Any]:
    (
        frame,
        freq,
        min_period_plays,
        dormancy_periods,
        first,
        last,
        source_start,
        source_end,
        periods,
        parameters,
        artist_index,
    ) = prepared
    if not isinstance(artist, str) or not artist:
        raise ValueError("artist must be nonempty")

    indices = artist_index.get(artist.casefold())
    matches = frame.loc[indices].copy() if indices is not None else frame.iloc[0:0]
    if matches.empty:
        zero_counts = pd.Series(0, index=periods, dtype="int64")
        return {
            "schema_version": 1,
            "query_artist": artist,
            "status": "not_found",
            "artist": None,
            "parameters": parameters,
            "observation": _observation(
                first,
                last,
                source_start,
                source_end,
                zero_counts,
                min_period_plays,
            ),
            "timeline": [],
            "summary": None,
            "peak": None,
            "ramp": None,
            "dormancy": {
                "threshold_periods": dormancy_periods,
                "episodes": [],
                "return_count": 0,
            },
            "segments": [],
        }

    display = _display_name(matches["artist"])
    match_periods = matches["timestamp"].dt.tz_localize(None).dt.to_period(freq)
    in_window = matches[(match_periods >= first) & (match_periods <= last)].copy()
    in_periods = match_periods[(match_periods >= first) & (match_periods <= last)]
    counts = (
        in_periods.value_counts()
        .reindex(periods, fill_value=0)
        .sort_index()
        .astype(int)
    )
    active = counts >= min_period_plays
    active_positions = np.flatnonzero(active.to_numpy())
    total = int(counts.sum())
    active_count = int(active.sum())

    if total:
        exact_first = in_window["timestamp"].min().isoformat()
        exact_last = in_window["timestamp"].max().isoformat()
    else:
        exact_first = exact_last = None
    if active_count:
        span = int(active_positions[-1] - active_positions[0] + 1)
    else:
        span = 0
    summary = {
        "total_plays": total,
        "observed_periods": len(periods),
        "active_periods": active_count,
        "active_share": _safe_float(active_count / len(periods)),
        "active_span_periods": span,
        "active_span_share": _safe_float(active_count / span) if span else None,
        "first_play": exact_first,
        "last_play": exact_last,
    }

    if total:
        peak_plays = int(counts.max())
        peak_periods = [str(period) for period in counts.index[counts == peak_plays]]
        primary_position = int(np.flatnonzero(counts.to_numpy() == peak_plays)[0])
        peak = {
            "plays": peak_plays,
            "periods": peak_periods,
            "primary_period": peak_periods[0],
        }
        if active_count:
            first_active_position = int(active_positions[0])
            ramp_values = counts.iloc[
                first_active_position : primary_position + 1
            ].to_numpy(dtype=float)
            distance = primary_position - first_active_position
            slope = None
            if len(ramp_values) >= 2:
                slope = _safe_float(
                    np.polyfit(np.arange(len(ramp_values)), ramp_values, 1)[0]
                )
            ramp = {
                "first_period": str(counts.index[first_active_position]),
                "peak_period": str(counts.index[primary_position]),
                "period_distance": distance,
                "first_period_plays": int(ramp_values[0]),
                "play_change": int(ramp_values[-1] - ramp_values[0]),
                "mean_period_change": _safe_float(
                    (ramp_values[-1] - ramp_values[0]) / distance
                )
                if distance
                else None,
                "ols_slope": slope,
            }
        else:
            ramp = None
    else:
        peak = ramp = None

    episodes: list[dict[str, Any]] = []
    segments: list[dict[str, Any]] = []
    if active_count:
        segment_start = int(active_positions[0])
        previous = segment_start
        segment_active = 1
        for position in active_positions[1:]:
            position = int(position)
            gap = position - previous - 1
            if gap >= dormancy_periods:
                segments.append(
                    {
                        "start_period": str(periods[segment_start]),
                        "end_period": str(periods[previous]),
                        "active_periods": segment_active,
                    }
                )
                episode = {
                    "start_period": str(periods[previous + 1]),
                    "end_period": str(periods[position - 1]),
                    "inactive_periods": gap,
                    "return_period": str(periods[position]),
                    "return_plays": int(counts.iloc[position]),
                }
                for window_size in (3, 6):
                    stop = position + window_size
                    episode[f"plays_first_{window_size}_periods"] = int(
                        counts.iloc[position:stop].sum()
                    )
                    episode[f"first_{window_size}_periods_complete"] = stop <= len(
                        counts
                    )
                episodes.append(episode)
                segment_start = position
                segment_active = 1
            else:
                segment_active += 1
            previous = position
        segments.append(
            {
                "start_period": str(periods[segment_start]),
                "end_period": str(periods[previous]),
                "active_periods": segment_active,
            }
        )

    return {
        "schema_version": 1,
        "query_artist": artist,
        "status": "ok",
        "artist": display,
        "parameters": parameters,
        "observation": _observation(
            first, last, source_start, source_end, counts, min_period_plays
        ),
        "timeline": [
            {
                "period": str(period),
                "plays": int(value),
                "active": bool(value >= min_period_plays),
            }
            for period, value in counts.items()
        ],
        "summary": summary,
        "peak": peak,
        "ramp": ramp,
        "dormancy": {
            "threshold_periods": dormancy_periods,
            "episodes": episodes,
            "return_count": len(episodes),
        },
        "segments": segments,
    }


def artist_trajectory(
    df: pd.DataFrame,
    artist: str,
    granularity: str = "month",
    start: str | None = None,
    end: str | None = None,
    min_period_plays: int = 1,
    dormancy_periods: int = 6,
) -> dict[str, Any]:
    """Measure one artist over a dense inclusive observation window."""
    prepared = _prepare_trajectory(
        df,
        granularity,
        start,
        end,
        min_period_plays,
        dormancy_periods,
    )
    return _artist_trajectory_from_prepared(prepared, artist)


def artist_trajectories(
    df: pd.DataFrame,
    artists: Sequence[str],
    **kwargs: Any,
) -> dict[str, Any]:
    """Measure artists in query order without fuzzy matching."""
    if not artists:
        raise ValueError("artist list must be nonempty")
    prepared = _prepare_trajectory(
        df,
        kwargs.get("granularity", "month"),
        kwargs.get("start"),
        kwargs.get("end"),
        kwargs.get("min_period_plays", 1),
        kwargs.get("dormancy_periods", 6),
    )
    results = [_artist_trajectory_from_prepared(prepared, artist) for artist in artists]
    return {"artists": results, "count": len(results)}


def cohort_retention(
    df: pd.DataFrame,
    cohort_granularity: str = "month",
    activity_granularity: str = "month",
    start: str | None = None,
    end: str | None = None,
    min_discovery_plays: int = 1,
    min_active_plays: int = 1,
    offsets: Iterable[int] = (1, 3, 6, 12, 24),
) -> dict[str, Any]:
    """Measure exact-offset activity for full-history discovery cohorts."""
    frame = _validate_history(df)
    cohort_freq = _frequency(cohort_granularity)
    activity_freq = _frequency(activity_granularity)
    min_discovery_plays = _positive_integer(min_discovery_plays, "min_discovery_plays")
    min_active_plays = _positive_integer(min_active_plays, "min_active_plays")
    offset_values = list(offsets)
    if not offset_values:
        raise ValueError("offsets must be nonempty nonnegative integers")
    offset_values = sorted(
        {_nonnegative_integer(value, "offset") for value in offset_values}
    )
    first, last, source_start, source_end = _window(
        frame, cohort_granularity, start, end
    )

    normalized = frame.assign(identity=frame["artist"].map(str.casefold))
    normalized = normalized[normalized["identity"] != ""].copy()
    normalized["cohort_period"] = (
        normalized["timestamp"].dt.tz_localize(None).dt.to_period(cohort_freq)
    )
    normalized["activity_period"] = (
        normalized["timestamp"].dt.tz_localize(None).dt.to_period(activity_freq)
    )
    first_rows = (
        normalized.sort_values("timestamp").groupby("identity", sort=True).first()
    )
    discovery_cohorts = first_rows["cohort_period"]
    discovery_activity_periods = first_rows["activity_period"]
    activity_counts = normalized.groupby(["identity", "activity_period"]).size()
    qualifying_periods: dict[str, list[pd.Period]] = defaultdict(list)
    for (identity, period), count in activity_counts.items():
        if int(count) >= min_active_plays:
            qualifying_periods[identity].append(period)
    requested_activity_end = last.end_time.to_period(activity_freq)
    source_activity_end = normalized["activity_period"].max()
    report_activity_end = min(requested_activity_end, source_activity_end)
    cohorts: list[dict[str, Any]] = []
    for cohort in pd.period_range(first, last, freq=cohort_freq):
        members = [
            identity
            for identity in discovery_cohorts.index[discovery_cohorts == cohort]
            if int(
                activity_counts.get((identity, discovery_activity_periods[identity]), 0)
            )
            >= min_discovery_plays
        ]
        discovery_plays = [
            int(activity_counts[(identity, discovery_activity_periods[identity])])
            for identity in members
        ]
        later_count = sum(
            any(
                discovery_activity_periods[identity] < period <= report_activity_end
                for period in qualifying_periods.get(identity, ())
            )
            for identity in members
        )
        cells = []
        for offset in offset_values:
            eligible_members = [
                identity
                for identity in members
                if discovery_activity_periods[identity] + offset <= report_activity_end
            ]
            eligible = len(eligible_members)
            retained = sum(
                int(
                    activity_counts.get(
                        (
                            identity,
                            discovery_activity_periods[identity] + offset,
                        ),
                        0,
                    )
                )
                >= min_active_plays
                for identity in eligible_members
            )
            cells.append(
                {
                    "offset": offset,
                    "eligible_artists": eligible,
                    "retained_artists": retained,
                    "retention_rate": _safe_float(retained / eligible)
                    if eligible
                    else None,
                }
            )
        cohorts.append(
            {
                "cohort": str(cohort),
                "cohort_size": len(members),
                "first_period_plays": {
                    "mean": _safe_float(sum(discovery_plays) / len(discovery_plays))
                    if discovery_plays
                    else None,
                    "median": _safe_float(median(discovery_plays))
                    if discovery_plays
                    else None,
                },
                "any_later_activity": {
                    "count": later_count,
                    "rate": _safe_float(later_count / len(members))
                    if members
                    else None,
                },
                "cells": cells,
            }
        )

    return {
        "schema_version": 1,
        "parameters": {
            "cohort_granularity": cohort_granularity,
            "activity_granularity": activity_granularity,
            "start": start,
            "end": end,
            "min_discovery_plays": min_discovery_plays,
            "min_active_plays": min_active_plays,
            "offsets": offset_values,
        },
        "observation": {
            "start_period": str(first),
            "end_period": str(last),
            "source_start_period": str(source_start),
            "source_end_period": str(source_end),
            "last_observable_activity_period": str(report_activity_end),
            "left_truncated": bool(first > source_start),
            "right_truncated": bool(requested_activity_end < source_activity_end),
            "right_censored": bool(requested_activity_end >= source_activity_end),
            "source_artists": int(normalized["identity"].nunique()),
        },
        "cohorts": cohorts,
        "diagnostics": {
            "cohorts": len(cohorts),
            "nonempty_cohorts": sum(cohort["cohort_size"] > 0 for cohort in cohorts),
            "artists_in_cohorts": sum(cohort["cohort_size"] for cohort in cohorts),
        },
    }
