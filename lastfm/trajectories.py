"""Deterministic artist trajectory and cohort-retention measurements."""

from __future__ import annotations

import re
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


def _observation(
    start: pd.Period,
    end: pd.Period,
    source_start: pd.Period,
    source_end: pd.Period,
    counts: pd.Series,
) -> dict[str, Any]:
    active_positions = np.flatnonzero(counts.to_numpy() > 0)
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
        "right_censored": bool(end < source_end),
        "leading_inactivity_periods": leading,
        "trailing_inactivity_periods": trailing,
        "leading_inactivity_censored": leading > 0,
        "trailing_inactivity_censored": trailing > 0,
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
    frame = _validate_history(df)
    freq = _frequency(granularity)
    if not isinstance(artist, str) or not artist:
        raise ValueError("artist must be nonempty")
    if min_period_plays <= 0 or dormancy_periods <= 0:
        raise ValueError("thresholds must be positive")
    first, last, source_start, source_end = _window(frame, granularity, start, end)
    periods = pd.period_range(first, last, freq=freq)
    parameters = {
        "granularity": granularity,
        "start": start,
        "end": end,
        "min_period_plays": min_period_plays,
        "dormancy_periods": dormancy_periods,
    }

    matches = frame[frame["artist"].map(str.casefold) == artist.casefold()].copy()
    if matches.empty:
        zero_counts = pd.Series(0, index=periods, dtype="int64")
        return {
            "query_artist": artist,
            "status": "not_found",
            "artist": None,
            "parameters": parameters,
            "observation": _observation(
                first, last, source_start, source_end, zero_counts
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
        "query_artist": artist,
        "status": "ok",
        "artist": display,
        "parameters": parameters,
        "observation": _observation(first, last, source_start, source_end, counts),
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


def artist_trajectories(
    df: pd.DataFrame,
    artists: Sequence[str],
    **kwargs: Any,
) -> dict[str, Any]:
    """Measure artists in query order without fuzzy matching."""
    if not artists:
        raise ValueError("artist list must be nonempty")
    results = [artist_trajectory(df, artist, **kwargs) for artist in artists]
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
    if min_discovery_plays <= 0 or min_active_plays <= 0:
        raise ValueError("thresholds must be positive")
    offset_values = list(offsets)
    if not offset_values or any(
        not isinstance(value, int) or value < 0 for value in offset_values
    ):
        raise ValueError("offsets must be nonempty nonnegative integers")
    offset_values = sorted(set(offset_values))
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
    discoveries = normalized.groupby("identity", sort=True)["cohort_period"].min()
    cohort_counts = normalized.groupby(["identity", "cohort_period"]).size()
    activity_counts = normalized.groupby(["identity", "activity_period"]).size()
    report_activity_end = last.end_time.to_period(activity_freq)
    cohorts: list[dict[str, Any]] = []
    for cohort in pd.period_range(first, last, freq=cohort_freq):
        members = [
            identity
            for identity in discoveries.index[discoveries == cohort]
            if int(cohort_counts.get((identity, cohort), 0)) >= min_discovery_plays
        ]
        discovery_plays = [
            int(cohort_counts[(identity, cohort)]) for identity in members
        ]
        cohort_activity_period = cohort.start_time.to_period(activity_freq)
        later_count = sum(
            bool(
                normalized[
                    (normalized["identity"] == identity)
                    & (normalized["activity_period"] > cohort_activity_period)
                    & (normalized["activity_period"] <= report_activity_end)
                ].shape[0]
            )
            for identity in members
        )
        cells = []
        for offset in offset_values:
            target = cohort_activity_period + offset
            eligible = len(members) if target <= report_activity_end else 0
            retained = (
                sum(
                    int(activity_counts.get((identity, target), 0)) >= min_active_plays
                    for identity in members
                )
                if eligible
                else 0
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
            "left_truncated": bool(first > source_start),
            "right_censored": bool(last < source_end),
            "source_artists": int(normalized["identity"].nunique()),
        },
        "cohorts": cohorts,
        "diagnostics": {
            "cohorts": len(cohorts),
            "nonempty_cohorts": sum(cohort["cohort_size"] > 0 for cohort in cohorts),
            "artists_in_cohorts": sum(cohort["cohort_size"] for cohort in cohorts),
        },
    }
