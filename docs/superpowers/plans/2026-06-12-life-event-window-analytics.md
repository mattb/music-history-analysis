# Life-Event Window Analytics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compare measured listening immediately before, during, and after a supplied date against a clipped surrounding baseline.

**Architecture:** A pure event-window module constructs non-overlapping half-open intervals in a caller-supplied IANA timezone, converts them to UTC for filtering, and returns counts, rates, shares, deltas, expected counts, residuals, and coverage diagnostics. Command adapters only validate and dispatch. Python never infers what an event meant or whether a measured difference is personally significant.

**Tech Stack:** Python 3.10+ `zoneinfo`, pandas, NumPy, Typer, pytest, existing JSON envelopes.

---

## File Structure

### Python analytics and command surface

- Create `lastfm/event_windows.py`.
- Modify `lastfm/agent_tools.py:15-42` and append `get_life_event_window`.
- Modify `lastfm/commands_agent.py:196-446` with `life-event-window`.
- Create `tests/test_event_windows.py`.
- Modify `tests/test_agent_tools.py` and `tests/test_agent_cli.py`.
- Create `docs/analytics/life-event-windows.md`.

### Journalism guidance, deliberately separate

- Modify `skills/lastfm-cli-journalism/SKILL.md` only after the numerical contract passes.

## Public Contract

```python
@dataclass(frozen=True)
class EventWindowSpec:
    event_date: date
    timezone: str = "UTC"
    pre_days: int = 28
    event_days: int = 1
    post_days: int = 28
    baseline_days: int = 84
    entity: Literal["artist", "album", "track"] = "artist"
    top_n: int = 50


def compare_event_window(df: pd.DataFrame, spec: EventWindowSpec) -> dict[str, Any]:
```

Intervals are local calendar midnights converted to UTC and are half-open:

```text
pre             [event - pre_days, event)
event           [event, event + event_days)
post            [event_end, event_end + post_days)
baseline_before [pre_start - baseline_days, pre_start)
baseline_after  [post_end, post_end + baseline_days)
```

The combined baseline is the union of its two non-contiguous parts. Available-history coverage is clipped and reported; absent source history is never converted into zero listening.

## Task 1: Construct Validated Local-Time Intervals

**Files:**
- Create: `lastfm/event_windows.py`
- Create: `tests/test_event_windows.py`

- [ ] **Step 1: Add failing boundary and daylight-saving tests**

```python
from datetime import date

import pandas as pd

from lastfm.event_windows import EventWindowSpec, build_intervals


def test_intervals_are_non_overlapping_and_half_open():
    intervals = build_intervals(EventWindowSpec(
        event_date=date(2020, 3, 15), pre_days=2, event_days=1,
        post_days=2, baseline_days=3,
    ))
    assert intervals["pre"].end == intervals["event"].start
    assert intervals["event"].end == intervals["post"].start
    assert intervals["baseline_before"].end == intervals["pre"].start
    assert intervals["post"].end == intervals["baseline_after"].start


def test_local_midnights_respect_dst():
    intervals = build_intervals(EventWindowSpec(
        event_date=date(2024, 3, 10), timezone="America/Los_Angeles",
        pre_days=1, event_days=1, post_days=1, baseline_days=1,
    ))
    assert (intervals["event"].end - intervals["event"].start).total_seconds() == 23 * 3600
```

- [ ] **Step 2: Verify the import failure**

Run: `uv run --extra dev python -m pytest tests/test_event_windows.py -v`

Expected: FAIL with an import error.

- [ ] **Step 3: Implement validation and intervals**

```python
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal, NamedTuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd


class Interval(NamedTuple):
    start: datetime
    end: datetime


@dataclass(frozen=True)
class EventWindowSpec:
    event_date: date
    timezone: str = "UTC"
    pre_days: int = 28
    event_days: int = 1
    post_days: int = 28
    baseline_days: int = 84
    entity: Literal["artist", "album", "track"] = "artist"
    top_n: int = 50


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown timezone: {name}") from exc


def build_intervals(spec: EventWindowSpec) -> dict[str, Interval]:
    for field in ("pre_days", "event_days", "post_days", "baseline_days", "top_n"):
        if getattr(spec, field) < 1:
            raise ValueError(f"{field} must be at least 1")
    if spec.entity not in {"artist", "album", "track"}:
        raise ValueError("entity must be 'artist', 'album', or 'track'")
    zone = _zone(spec.timezone)
    event = datetime.combine(spec.event_date, time.min, tzinfo=zone)
    pre = event - timedelta(days=spec.pre_days)
    event_end = event + timedelta(days=spec.event_days)
    post_end = event_end + timedelta(days=spec.post_days)
    local = {
        "pre": Interval(pre, event),
        "event": Interval(event, event_end),
        "post": Interval(event_end, post_end),
        "baseline_before": Interval(pre - timedelta(days=spec.baseline_days), pre),
        "baseline_after": Interval(post_end, post_end + timedelta(days=spec.baseline_days)),
    }
    return {
        name: Interval(value.start.astimezone(timezone.utc), value.end.astimezone(timezone.utc))
        for name, value in local.items()
    }
```

- [ ] **Step 4: Add tests for invalid dates, unknown zones, nonpositive values, invalid entities, and exact boundary assignment**

- [ ] **Step 5: Run tests and commit**

```bash
uv run --extra dev python -m pytest tests/test_event_windows.py -v
git add lastfm/event_windows.py tests/test_event_windows.py
git commit -m "feat: define event comparison windows"
```

## Task 2: Aggregate Periods and Coverage

**Files:**
- Modify: `lastfm/event_windows.py`
- Test: `tests/test_event_windows.py`

- [ ] **Step 1: Add failing aggregation tests**

Test all five intervals, baseline exclusion of pre/event/post, history clipping on both sides, rates based on covered local calendar days, empty periods, and grouping keys for artist, `(artist, album)`, and `(artist, track)`.

- [ ] **Step 2: Implement deterministic grouping and period summaries**

```python
def _entity_columns(entity: str) -> list[str]:
    return {
        "artist": ["artist"],
        "album": ["artist", "album"],
        "track": ["artist", "track"],
    }[entity]


def _slice(df: pd.DataFrame, interval: Interval) -> pd.DataFrame:
    return df[(df["timestamp"] >= interval.start) & (df["timestamp"] < interval.end)]


def _counts(df: pd.DataFrame, entity: str) -> dict[tuple[str, ...], int]:
    columns = _entity_columns(entity)
    valid = df.dropna(subset=columns)
    if entity != "artist":
        valid = valid[valid[columns[-1]] != ""]
    grouped = valid.groupby(columns, sort=True).size()
    return {
        key if isinstance(key, tuple) else (key,): int(value)
        for key, value in grouped.items()
    }
```

Define source coverage as local calendar days from the day containing the first timestamp through the day containing the last timestamp, inclusive. Intersect each requested interval with that coverage. Report requested days, covered days, plays, plays per covered day, and unique artist/album/track counts. A clipped period is successful with diagnostics. An event interval with zero coverage raises `ValueError("event window does not overlap listening history")`.

- [ ] **Step 3: Run tests and commit**

```bash
uv run --extra dev python -m pytest tests/test_event_windows.py -v
git add lastfm/event_windows.py tests/test_event_windows.py
git commit -m "feat: aggregate event-window listening"
```

## Task 3: Calculate Entity Deltas and Baseline Residuals

**Files:**
- Modify: `lastfm/event_windows.py`
- Test: `tests/test_event_windows.py`

- [ ] **Step 1: Add failing hand-calculated metric tests**

For each entity assert counts and shares in pre/event/post/baseline, `post_minus_pre_count`, `post_minus_pre_share`, expected counts from baseline rate, standardized residuals, zero-expected `null`, first-ever-play boolean, union-of-top-N selection, and deterministic sorting.

- [ ] **Step 2: Implement transparent arithmetic**

```python
def _expected(baseline_count: int, baseline_days: int, period_days: int) -> float | None:
    if baseline_days == 0:
        return None
    return baseline_count / baseline_days * period_days


def _residual(observed: int, expected: float | None) -> float | None:
    if expected is None or expected == 0:
        return None
    return (observed - expected) / expected**0.5
```

The entity table is the union of each period's top `top_n` entities. Sort by descending absolute post-minus-pre share, then descending post count, then lexicographic entity key. Return booleans for presence in each period and `first_ever_play_in_event_window`; do not return categorical verbs such as rose, vanished, or returned.

- [ ] **Step 3: Assemble schema version 1**

Return `timezone`, `event_date`, parameters, all period boundaries in UTC plus local dates, `periods`, `entities`, and diagnostics containing history bounds, requested/covered baseline days, clipping booleans, and empty periods. Round rates, shares, expected counts, and residuals to ten decimal places.

- [ ] **Step 4: Run tests and commit**

```bash
uv run --extra dev python -m pytest tests/test_event_windows.py -v
git add lastfm/event_windows.py tests/test_event_windows.py
git commit -m "feat: compare event-window entity rates"
```

## Task 4: Expose the Analytics Command

**Files:**
- Modify: `lastfm/agent_tools.py`
- Modify: `lastfm/commands_agent.py`
- Modify: `tests/test_agent_tools.py`
- Modify: `tests/test_agent_cli.py`

- [ ] **Step 1: Add failing dispatch and CLI tests**

```python
def test_dispatch_life_event_window(monkeypatch, sample_csv):
    state = loaded_lightweight_state(monkeypatch, sample_csv)
    result = dispatch(state, "life-event-window", {
        "event_date": "2024-01-02",
        "timezone": "UTC",
        "pre_days": 1,
        "event_days": 1,
        "post_days": 1,
        "baseline_days": 1,
        "entity": "artist",
        "top_n": 10,
    })
    assert result["schema_version"] == 1
```

Also test command registration, every option, session/one-shot equality, error envelopes, and JSON without NaN.

- [ ] **Step 2: Register the thin adapter**

Add `"life-event-window": "get_life_event_window"` to `COMMANDS`. Parse the ISO date with `date.fromisoformat`, construct `EventWindowSpec`, and call `compare_event_window(state.df, spec)`.

- [ ] **Step 3: Register the Typer command**

Expose required `--event-date` and optional `--timezone`, `--pre-days`, `--event-days`, `--post-days`, `--baseline-days`, `--entity`, and `--top-n`, plus the standard target and JSON options.

- [ ] **Step 4: Run tests and commit**

```bash
uv run --extra dev python -m pytest tests/test_agent_tools.py tests/test_agent_cli.py -v
git add lastfm/agent_tools.py lastfm/commands_agent.py tests/test_agent_tools.py tests/test_agent_cli.py
git commit -m "feat: expose life-event window analytics"
```

## Task 5: Document the Numerical Contract

**Files:**
- Create: `docs/analytics/life-event-windows.md`

- [ ] **Step 1: Document intervals and formulas**

Document local-midnight construction, UTC filtering, DST behavior, half-open boundaries, source-coverage clipping, baseline union, entity keys, expected-count and residual formulas, sorting, and the difference between zero recorded plays and absent source coverage.

- [ ] **Step 2: Commit**

```bash
git add docs/analytics/life-event-windows.md
git commit -m "docs: define life-event window measurements"
```

## Task 6: Update Journalism Guidance Separately

**Files:**
- Modify: `skills/lastfm-cli-journalism/SKILL.md`

- [ ] **Step 1: Add a distinct `Life-Event Evidence` section**

```markdown
## Life-Event Evidence

Use `life-event-window` only after the user supplies the event date and relevant timezone. Inspect coverage before comparing periods. Distinguish a zero-play covered interval from an interval outside the source history.

Lead with measured counts, rates, shares, deltas, and baseline residuals. The analytics do not establish that the event caused a change. Treat large residuals as evidence for closer inspection, not as emotional or biographical conclusions.

Check artist, album, and track groupings when the question warrants them. Name the window lengths, baseline length, timezone, and clipped coverage in the evidence trail.
```

- [ ] **Step 2: Verify separation**

Run: `rg -n 'meaningful|caused|wedding music|emotional' lastfm/event_windows.py`

Expected: no matches.

- [ ] **Step 3: Commit**

```bash
git add skills/lastfm-cli-journalism/SKILL.md
git commit -m "docs: guide life-event evidence interpretation"
```

## Task 7: Final Verification

- [ ] **Step 1: Run tests**

```bash
uv run --extra dev python -m pytest tests/test_event_windows.py tests/test_agent_tools.py tests/test_agent_cli.py -v
uv run --extra dev python -m pytest
```

Expected: all tests pass.

- [ ] **Step 2: Run Ruff**

```bash
uv run --extra dev ruff check lastfm/event_windows.py lastfm/agent_tools.py lastfm/commands_agent.py tests/test_event_windows.py tests/test_agent_tools.py tests/test_agent_cli.py
uv run --extra dev ruff format --check lastfm/event_windows.py lastfm/agent_tools.py lastfm/commands_agent.py tests/test_event_windows.py tests/test_agent_tools.py tests/test_agent_cli.py
```

Expected: both commands exit zero.

## Non-Goals

- No causal inference or emotional interpretation.
- No automatic event discovery.
- No generated prose or playlist.
- No significance adjective based on an arbitrary threshold.
- No modification of source data.
