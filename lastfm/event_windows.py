"""Pure measurements around a user-supplied local-calendar event window."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable, NamedTuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd


PERIOD_NAMES = ("baseline_before", "pre", "event", "post", "baseline_after")
COMPARISON_PERIODS = ("pre", "event", "post", "baseline")


@dataclass(frozen=True)
class EventWindowSpec:
    event_date: date | str
    timezone: str = "UTC"
    pre_days: int = 28
    event_days: int = 1
    post_days: int = 28
    baseline_days: int = 84
    entity: str = "artist"
    top_n: int = 50

    def __post_init__(self) -> None:
        if type(self.event_date) is date:
            parsed = self.event_date
        elif isinstance(self.event_date, str):
            try:
                parsed = date.fromisoformat(self.event_date)
            except ValueError as exc:
                raise ValueError("event_date must be an ISO date (YYYY-MM-DD)") from exc
            if parsed.isoformat() != self.event_date:
                raise ValueError("event_date must be an ISO date (YYYY-MM-DD)")
        else:
            raise ValueError("event_date must be an ISO date (YYYY-MM-DD)")
        object.__setattr__(self, "event_date", parsed)
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, TypeError) as exc:
            raise ValueError(f"Unknown IANA timezone: {self.timezone}") from exc
        if self.entity not in {"artist", "album", "track"}:
            raise ValueError("entity must be artist, album, or track")
        for name in ("pre_days", "event_days", "post_days", "baseline_days", "top_n"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive finite integer")


@dataclass(frozen=True)
class _Interval:
    local_start: date
    local_end: date
    utc_start: datetime
    utc_end: datetime

    @property
    def days(self) -> int:
        return (self.local_end - self.local_start).days


class Interval(NamedTuple):
    start: datetime
    end: datetime


def _midnight(day: date, zone: ZoneInfo) -> datetime:
    return datetime.combine(day, time.min, tzinfo=zone)


def _interval(start: date, end: date, zone: ZoneInfo) -> _Interval:
    return _Interval(
        local_start=start,
        local_end=end,
        utc_start=_midnight(start, zone).astimezone(timezone.utc),
        utc_end=_midnight(end, zone).astimezone(timezone.utc),
    )


def _clip(
    interval: _Interval, coverage_start: date, coverage_end: date, zone: ZoneInfo
) -> _Interval | None:
    start = max(interval.local_start, coverage_start)
    end = min(interval.local_end, coverage_end)
    return _interval(start, end, zone) if start < end else None


def _iso_utc(value: datetime | pd.Timestamp | None) -> str | None:
    if value is None:
        return None
    stamp = pd.Timestamp(value).tz_convert("UTC")
    return stamp.isoformat().replace("+00:00", "Z")


def _round(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError("analytics produced a non-finite value")
    return round(value, 10)


def _clean(value: Any) -> str:
    return "" if pd.isna(value) else str(value)


def _entity_key(row: Any, entity: str) -> tuple[str, ...] | None:
    artist = _clean(row.artist)
    if not artist:
        return None
    if entity == "artist":
        return (artist,)
    value = _clean(getattr(row, entity))
    return (artist, value) if value else None


def _slice(df: pd.DataFrame, intervals: Iterable[_Interval]) -> pd.DataFrame:
    mask = pd.Series(False, index=df.index)
    for interval in intervals:
        mask |= (df["timestamp"] >= interval.utc_start) & (
            df["timestamp"] < interval.utc_end
        )
    return df.loc[mask]


def _counts(frame: pd.DataFrame, entity: str) -> Counter[tuple[str, ...]]:
    result: Counter[tuple[str, ...]] = Counter()
    for row in frame.itertuples(index=False):
        key = _entity_key(row, entity)
        if key is not None:
            result[key] += 1
    return result


def _unique_pairs(frame: pd.DataFrame, column: str) -> int:
    pairs = {
        (_clean(row.artist), _clean(getattr(row, column)))
        for row in frame.itertuples(index=False)
        if _clean(getattr(row, column))
    }
    return len(pairs)


def _period_payload(
    df: pd.DataFrame,
    requested: list[_Interval],
    covered: list[_Interval],
    entity: str,
) -> tuple[dict[str, Any], Counter[tuple[str, ...]]]:
    frame = _slice(df, covered)
    counts = _counts(frame, entity)
    requested_days = sum(item.days for item in requested)
    covered_days = sum(item.days for item in covered)
    plays = len(frame)
    entity_counts = [
        {
            "key": list(key),
            "count": count,
            "share": _round(count / plays) if plays else 0.0,
        }
        for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]
    payload = {
        "local_start": requested[0].local_start.isoformat(),
        "local_end_exclusive": requested[-1].local_end.isoformat(),
        "requested_start_utc": _iso_utc(requested[0].utc_start),
        "requested_end_utc": _iso_utc(requested[-1].utc_end),
        "start_utc": _iso_utc(covered[0].utc_start) if covered else None,
        "end_utc": _iso_utc(covered[-1].utc_end) if covered else None,
        "covered_local_start": covered[0].local_start.isoformat() if covered else None,
        "covered_local_end_exclusive": covered[-1].local_end.isoformat()
        if covered
        else None,
        "requested_days": requested_days,
        "covered_days": covered_days,
        "plays": plays,
        "plays_per_covered_day": _round(plays / covered_days) if covered_days else None,
        "unique_artists": int(frame["artist"].map(_clean).replace("", pd.NA).nunique())
        if plays
        else 0,
        "unique_albums": _unique_pairs(frame, "album"),
        "unique_tracks": _unique_pairs(frame, "track"),
        "entity_counts": entity_counts,
        "intervals": [
            {
                "local_start": item.local_start.isoformat(),
                "local_end_exclusive": item.local_end.isoformat(),
                "start_utc": _iso_utc(item.utc_start),
                "end_utc": _iso_utc(item.utc_end),
            }
            for item in covered
        ],
    }
    return payload, counts


def build_intervals(spec: EventWindowSpec) -> dict[str, Interval]:
    """Return the five requested half-open intervals in UTC."""
    zone = ZoneInfo(spec.timezone)
    event_start = spec.event_date
    assert isinstance(event_start, date)
    pre_start = event_start - timedelta(days=spec.pre_days)
    event_end = event_start + timedelta(days=spec.event_days)
    post_end = event_end + timedelta(days=spec.post_days)
    local = {
        "baseline_before": _interval(
            pre_start - timedelta(days=spec.baseline_days), pre_start, zone
        ),
        "pre": _interval(pre_start, event_start, zone),
        "event": _interval(event_start, event_end, zone),
        "post": _interval(event_end, post_end, zone),
        "baseline_after": _interval(
            post_end, post_end + timedelta(days=spec.baseline_days), zone
        ),
    }
    return {
        name: Interval(value.utc_start, value.utc_end) for name, value in local.items()
    }


def compare_event_window(df: pd.DataFrame, spec: EventWindowSpec) -> dict[str, Any]:
    """Measure listening in non-overlapping local-calendar windows."""
    required = {"timestamp", "artist", "album", "track"}
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(missing)}")
    if df.empty:
        raise ValueError("Cannot analyze an empty listening history")

    source = df.loc[:, list(required)].copy()
    source["timestamp"] = pd.to_datetime(source["timestamp"], utc=True, errors="raise")
    if source["timestamp"].isna().any():
        raise ValueError("timestamp values must not be missing")
    source = source.sort_values("timestamp", kind="stable")
    zone = ZoneInfo(spec.timezone)
    event_start = spec.event_date
    assert isinstance(event_start, date)
    pre_start = event_start - timedelta(days=spec.pre_days)
    event_end = event_start + timedelta(days=spec.event_days)
    post_end = event_end + timedelta(days=spec.post_days)
    requested = {
        "baseline_before": _interval(
            pre_start - timedelta(days=spec.baseline_days), pre_start, zone
        ),
        "pre": _interval(pre_start, event_start, zone),
        "event": _interval(event_start, event_end, zone),
        "post": _interval(event_end, post_end, zone),
        "baseline_after": _interval(
            post_end, post_end + timedelta(days=spec.baseline_days), zone
        ),
    }

    first_timestamp = source["timestamp"].iloc[0]
    last_timestamp = source["timestamp"].iloc[-1]
    coverage_start = first_timestamp.tz_convert(zone).date()
    coverage_end = last_timestamp.tz_convert(zone).date() + timedelta(days=1)
    covered = {
        name: _clip(interval, coverage_start, coverage_end, zone)
        for name, interval in requested.items()
    }
    if covered["event"] is None:
        raise ValueError(
            "The event interval has zero local-calendar days inside source coverage"
        )

    periods: dict[str, dict[str, Any]] = {}
    period_counts: dict[str, Counter[tuple[str, ...]]] = {}
    for name in PERIOD_NAMES:
        requested_parts = [requested[name]]
        covered_parts = [covered[name]] if covered[name] is not None else []
        periods[name], period_counts[name] = _period_payload(
            source, requested_parts, covered_parts, spec.entity
        )

    baseline_requested = [requested["baseline_before"], requested["baseline_after"]]
    baseline_covered = [
        item
        for item in (covered["baseline_before"], covered["baseline_after"])
        if item is not None
    ]
    periods["baseline"], period_counts["baseline"] = _period_payload(
        source, baseline_requested, baseline_covered, spec.entity
    )

    candidates: set[tuple[str, ...]] = set()
    for name in COMPARISON_PERIODS:
        ranked = sorted(
            period_counts[name].items(), key=lambda item: (-item[1], item[0])
        )
        candidates.update(key for key, _count in ranked[: spec.top_n])

    eligible = source[
        source.apply(lambda row: _entity_key(row, spec.entity) is not None, axis=1)
    ]
    first_seen: dict[tuple[str, ...], pd.Timestamp] = {}
    for row in eligible.itertuples(index=False):
        key = _entity_key(row, spec.entity)
        assert key is not None
        first_seen.setdefault(key, row.timestamp)

    baseline_days = periods["baseline"]["covered_days"]
    event_interval = covered["event"]
    assert event_interval is not None
    entity_rows = []
    for key in candidates:
        counts = {name: period_counts[name][key] for name in COMPARISON_PERIODS}
        shares = {
            name: _round(counts[name] / periods[name]["plays"])
            if periods[name]["plays"]
            else 0.0
            for name in COMPARISON_PERIODS
        }
        expected: dict[str, float | None] = {}
        residual: dict[str, float | None] = {}
        for name in ("pre", "event", "post"):
            if not baseline_days:
                expected[name] = None
                residual[name] = None
                continue
            raw_expected = (
                counts["baseline"] / baseline_days * periods[name]["covered_days"]
            )
            expected[name] = _round(raw_expected)
            residual[name] = (
                _round((counts[name] - raw_expected) / math.sqrt(raw_expected))
                if raw_expected > 0
                else None
            )
        first = first_seen[key]
        entity_rows.append(
            {
                "key": list(key),
                "counts": counts,
                "shares": shares,
                "post_minus_pre": {
                    "count": counts["post"] - counts["pre"],
                    "share": _round(shares["post"] - shares["pre"]),
                },
                "expected_from_baseline": expected,
                "standardized_residual": residual,
                "presence": {name: counts[name] > 0 for name in COMPARISON_PERIODS},
                "first_ever_play_in_event_window": bool(
                    event_interval.utc_start <= first < event_interval.utc_end
                ),
            }
        )
    entity_rows.sort(
        key=lambda row: (
            -abs(row["post_minus_pre"]["share"]),
            -row["counts"]["post"],
            tuple(row["key"]),
        )
    )

    baseline_requested_days = 2 * spec.baseline_days
    baseline_covered_days = periods["baseline"]["covered_days"]
    return {
        "schema_version": 1,
        "timezone": spec.timezone,
        "event_date": spec.event_date.isoformat(),
        "parameters": {
            "pre_days": spec.pre_days,
            "event_days": spec.event_days,
            "post_days": spec.post_days,
            "baseline_days": spec.baseline_days,
            "entity": spec.entity,
            "top_n": spec.top_n,
        },
        "periods": periods,
        "entities": entity_rows,
        "diagnostics": {
            "source_bounds": {
                "first_timestamp_utc": _iso_utc(first_timestamp),
                "last_timestamp_utc": _iso_utc(last_timestamp),
                "first_local_date": coverage_start.isoformat(),
                "last_local_date": (coverage_end - timedelta(days=1)).isoformat(),
                "covered_local_days": (coverage_end - coverage_start).days,
            },
            "baseline": {
                "requested_days": baseline_requested_days,
                "covered_days": baseline_covered_days,
                "clipped": baseline_covered_days < baseline_requested_days,
            },
            "empty_periods": [
                name for name in PERIOD_NAMES if not periods[name]["plays"]
            ],
        },
    }


analyze_event_window = compare_event_window
