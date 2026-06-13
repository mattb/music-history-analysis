# Artist Relationship Trajectories Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce dense artist activity series, numerical ramp and peak measurements, dormancy and return intervals, active segments, and right-censored cohort retention.

**Architecture:** A pure `trajectories` module owns UTC period construction and lifecycle arithmetic. Thin state and Typer adapters expose two commands. Python emits measurements and coverage diagnostics only; the journalism instructions decide how, or whether, to describe an artist as enduring, dormant, rediscovered, or important.

**Tech Stack:** Python 3.10+, pandas PeriodIndex, NumPy, Typer, pytest, existing JSON envelopes.

---

## File Structure

### Python analytics and command surface

- Create `lastfm/trajectories.py`.
- Modify `lastfm/agent_tools.py:15-42` and append two adapters.
- Modify `lastfm/commands_agent.py:196-446` with `artist-trajectories` and `artist-cohort-retention`.
- Create `tests/test_trajectories.py`.
- Modify `tests/conftest.py`, `tests/test_agent_tools.py`, and `tests/test_agent_cli.py`.
- Create `docs/analytics/artist-trajectories.md`.

### Journalism guidance, deliberately separate

- Modify `skills/lastfm-cli-journalism/SKILL.md` only after the numerical contracts pass.

## Task 1: Build Dense UTC Activity Series

**Files:**
- Create: `lastfm/trajectories.py`
- Create: `tests/test_trajectories.py`

- [ ] **Step 1: Write failing period and matching tests**

```python
import pandas as pd

from lastfm.trajectories import artist_trajectory


def frame(rows):
    df = pd.DataFrame(rows, columns=["timestamp", "artist"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df


def test_monthly_series_includes_zero_months_and_requested_bounds():
    result = artist_trajectory(
        frame([
            ("2020-02-02T00:00:00Z", "Artist A"),
            ("2020-04-02T00:00:00Z", "Artist A"),
        ]),
        "artist a",
        granularity="month",
        start="2020-01",
        end="2020-05",
    )
    assert [(row["period"], row["plays"]) for row in result["series"]] == [
        ("2020-01", 0), ("2020-02", 1), ("2020-03", 0),
        ("2020-04", 1), ("2020-05", 0),
    ]


def test_missing_artist_is_structured_not_found():
    result = artist_trajectory(
        frame([("2020-01-01T00:00:00Z", "Artist A")]), "Artist B"
    )
    assert result == {"artist": None, "query": "Artist B", "status": "not_found"}
```

- [ ] **Step 2: Verify failure**

Run: `uv run --extra dev python -m pytest tests/test_trajectories.py -v`

Expected: FAIL with an import error.

- [ ] **Step 3: Implement validation and dense series**

```python
from typing import Literal

import numpy as np
import pandas as pd

Granularity = Literal["month", "year"]


def _frequency(granularity: Granularity) -> str:
    if granularity == "month":
        return "M"
    if granularity == "year":
        return "Y"
    raise ValueError("granularity must be 'month' or 'year'")


def _period(value: str, granularity: Granularity) -> pd.Period:
    expected = 7 if granularity == "month" else 4
    if len(value) != expected:
        raise ValueError(f"{granularity} bounds have invalid format")
    return pd.Period(value, freq=_frequency(granularity))


def _display_name(values: pd.Series) -> str:
    counts = values.value_counts()
    highest = counts.max()
    return sorted(counts[counts == highest].index)[0]


def artist_trajectory(
    df: pd.DataFrame,
    artist: str,
    *,
    granularity: Granularity = "month",
    start: str | None = None,
    end: str | None = None,
    min_period_plays: int = 1,
    dormancy_periods: int = 6,
) -> dict:
    if df.empty:
        raise ValueError("Listening history is empty")
    if min_period_plays < 1 or dormancy_periods < 1:
        raise ValueError("activity thresholds must be at least 1")
    matches = df[df["artist"].str.casefold() == artist.casefold()].copy()
    if matches.empty:
        return {"artist": None, "query": artist, "status": "not_found"}
    frequency = _frequency(granularity)
    dataset_periods = df["timestamp"].dt.to_period(frequency)
    start_period = _period(start, granularity) if start else dataset_periods.min()
    end_period = _period(end, granularity) if end else dataset_periods.max()
    if start_period > end_period:
        raise ValueError("start must not be after end")
    index = pd.period_range(start_period, end_period, freq=frequency)
    matched_periods = matches["timestamp"].dt.to_period(frequency)
    counts = matched_periods.value_counts().reindex(index, fill_value=0).sort_index()
    series = [
        {"period": str(period), "plays": int(plays), "active": int(plays) >= min_period_plays}
        for period, plays in counts.items()
    ]
    result = {
        "artist": _display_name(matches["artist"]),
        "query": artist,
        "status": "ok",
        "series": series,
    }
    return result
```

- [ ] **Step 4: Add tests for yearly periods, casefold-exact matching, display-name ties, malformed bounds, reversed bounds, empty history, and shuffled input**

- [ ] **Step 5: Run tests and commit**

```bash
uv run --extra dev python -m pytest tests/test_trajectories.py -v
git add lastfm/trajectories.py tests/test_trajectories.py
git commit -m "feat: build dense artist activity series"
```

## Task 2: Add Peak, Ramp, Dormancy, Return, and Segment Measurements

**Files:**
- Modify: `lastfm/trajectories.py`
- Test: `tests/test_trajectories.py`

- [ ] **Step 1: Add failing hand-calculated tests**

Cover tied peaks, earliest-primary-peak selection, OLS slope across zero periods, peak in the first active period, a gap one shorter than the dormancy threshold, a gap exactly equal to it, leading/trailing censoring, multiple returns, and incomplete three/six-period post-return windows.

- [ ] **Step 2: Implement the numerical definitions**

Use the dense count array. Activity is `plays >= min_period_plays`. A dormancy is a run of at least `dormancy_periods` inactive bins strictly between active bins. Leading and trailing zeros are censored and do not create dormancies.

```python
def _ramp(counts: np.ndarray, first: int, peak: int) -> dict:
    distance = peak - first
    window = counts[first:peak + 1].astype(float)
    slope = None if len(window) < 2 else float(np.polyfit(np.arange(len(window)), window, 1)[0])
    change = int(counts[peak] - counts[first])
    return {
        "periods": distance,
        "first_period_plays": int(counts[first]),
        "play_change": change,
        "mean_change_per_period": None if distance == 0 else change / distance,
        "ols_slope": slope,
    }


def _inactive_runs(active: np.ndarray, minimum: int) -> list[tuple[int, int]]:
    active_indices = np.flatnonzero(active)
    runs = []
    for left, right in zip(active_indices[:-1], active_indices[1:]):
        gap = right - left - 1
        if gap >= minimum:
            runs.append((left + 1, right - 1))
    return runs
```

Return exact first/last timestamps, total plays, observed and active period counts, activity shares, inclusive active span, all tied peak periods, ramp metrics, dormancies, returns, and active segments. Each return includes totals over the return bin plus the next two and five bins and completeness booleans. Round ratios and slopes to six decimals only at serialization.

- [ ] **Step 3: Run tests and commit**

```bash
uv run --extra dev python -m pytest tests/test_trajectories.py -v
git add lastfm/trajectories.py tests/test_trajectories.py
git commit -m "feat: measure artist activity trajectories"
```

## Task 3: Add Exact-Offset Cohort Retention

**Files:**
- Modify: `lastfm/trajectories.py`
- Test: `tests/test_trajectories.py`

- [ ] **Step 1: Add failing cohort tests**

Assert that cohort membership uses the first-ever play even when report bounds start later; first-period thresholds exclude low-count artists; retention is activity at the exact offset rather than cumulative activity; right-censored artists leave the denominator; and zero eligible artists yield `null`, not zero.

- [ ] **Step 2: Implement cohort retention**

```python
def cohort_retention(
    df: pd.DataFrame,
    *,
    cohort_granularity: Granularity = "year",
    activity_granularity: Granularity = "month",
    start: str | None = None,
    end: str | None = None,
    min_discovery_plays: int = 1,
    min_active_plays: int = 1,
    offsets: tuple[int, ...] = (1, 3, 6, 12, 24),
) -> dict:
    if any(offset < 0 for offset in offsets) or len(set(offsets)) != len(offsets):
        raise ValueError("offsets must be unique non-negative integers")
    activity_freq = _frequency(activity_granularity)
    work = df.copy()
    work["activity_period"] = work["timestamp"].dt.to_period(activity_freq)
    counts = work.groupby(["artist", "activity_period"]).size()
    first_period = work.groupby("artist")["activity_period"].min()
    dataset_end = work["activity_period"].max()
    cohorts = []
    for cohort_period, members in first_period.groupby(
        first_period.dt.asfreq(_frequency(cohort_granularity))
    ):
        qualified = [
            artist for artist in members.index
            if int(counts.get((artist, first_period[artist]), 0)) >= min_discovery_plays
        ]
        cells = []
        for offset in sorted(offsets):
            eligible = [artist for artist in qualified if first_period[artist] + offset <= dataset_end]
            retained = sum(
                int(counts.get((artist, first_period[artist] + offset), 0)) >= min_active_plays
                for artist in eligible
            )
            cells.append({
                "offset": offset,
                "eligible_artists": len(eligible),
                "retained_artists": retained,
                "retention_rate": None if not eligible else retained / len(eligible),
            })
        cohorts.append({"cohort": str(cohort_period), "cohort_size": len(qualified), "retention": cells})
    return {"schema_version": 1, "cohorts": cohorts}
```

Complete the implementation with report-bound filtering, diagnostics, mean/median first-period plays, and `artists_with_any_later_activity`. Do not emit artist names by default.

- [ ] **Step 3: Run tests and commit**

```bash
uv run --extra dev python -m pytest tests/test_trajectories.py -v
git add lastfm/trajectories.py tests/test_trajectories.py
git commit -m "feat: calculate censored cohort retention"
```

## Task 4: Expose Two Analytics Commands

**Files:**
- Modify: `lastfm/agent_tools.py`
- Modify: `lastfm/commands_agent.py`
- Modify: `tests/conftest.py`
- Modify: `tests/test_agent_tools.py`
- Modify: `tests/test_agent_cli.py`

- [ ] **Step 1: Add failing adapter and CLI tests**

Test `artist-trajectories` with repeated `--artist`, `--granularity`, bounds, thresholds, and stable JSON. Test `artist-cohort-retention` with repeated `--offset`. Assert neither command requires embeddings or critics data.

- [ ] **Step 2: Register thin adapters**

Add mappings:

```python
"artist-trajectories": "get_artist_trajectories",
"artist-cohort-retention": "get_artist_cohort_retention",
```

The wrappers call `artist_trajectories` and `cohort_retention` with `state.df`; they contain no lifecycle arithmetic.

- [ ] **Step 3: Register Typer commands**

Expose repeatable `--artist` and `--offset` values and all parameters defined above. Use the existing mutually exclusive `--session`/`--csv` target and JSON envelope.

- [ ] **Step 4: Run tests and commit**

```bash
uv run --extra dev python -m pytest tests/test_agent_tools.py tests/test_agent_cli.py -v
git add lastfm/agent_tools.py lastfm/commands_agent.py tests/conftest.py tests/test_agent_tools.py tests/test_agent_cli.py
git commit -m "feat: expose trajectory and retention analytics"
```

## Task 5: Document the Numerical Contract

**Files:**
- Create: `docs/analytics/artist-trajectories.md`

- [ ] **Step 1: Document period boundaries and formulas**

Include UTC boundaries, dense zeros, exact matching, peak tie rules, ramp formulas, dormancy threshold, return-window censoring, exact-offset retention, eligible denominators, output schemas, and examples with no interpretive adjectives.

- [ ] **Step 2: Commit**

```bash
git add docs/analytics/artist-trajectories.md
git commit -m "docs: define artist trajectory measurements"
```

## Task 6: Update Journalism Guidance Separately

**Files:**
- Modify: `skills/lastfm-cli-journalism/SKILL.md`

- [ ] **Step 1: Add a distinct `Relationship Trajectory Evidence` section**

```markdown
## Relationship Trajectory Evidence

Use `artist-trajectories` for first and last observations, dense activity series, peaks, measured ramps, inactive intervals, and returns. Use `artist-cohort-retention` when comparing artists discovered in the same period.

Treat trailing inactivity as right-censored whenever the history ends near it. A numerical dormancy is a thresholded inactive interval, not evidence that the listener rejected or forgot an artist. A return is renewed recorded activity, not an explanation for why it happened.

When describing “enduring” artists, cite the measurements that support the phrase: observation span, active-period share, number and length of inactive intervals, returns, and recent activity. Do not let Python assign the adjective.
```

- [ ] **Step 2: Verify the Python boundary**

Run: `rg -n 'enduring|forgot|rejected|devotion|meaning' lastfm/trajectories.py`

Expected: no matches.

- [ ] **Step 3: Commit**

```bash
git add skills/lastfm-cli-journalism/SKILL.md
git commit -m "docs: guide trajectory evidence interpretation"
```

## Task 7: Final Verification

- [ ] **Step 1: Run tests**

```bash
uv run --extra dev python -m pytest tests/test_trajectories.py tests/test_agent_tools.py tests/test_agent_cli.py -v
uv run --extra dev python -m pytest
```

Expected: all tests pass.

- [ ] **Step 2: Run Ruff**

```bash
uv run --extra dev ruff check lastfm/trajectories.py lastfm/agent_tools.py lastfm/commands_agent.py tests/test_trajectories.py tests/test_agent_tools.py tests/test_agent_cli.py tests/conftest.py
uv run --extra dev ruff format --check lastfm/trajectories.py lastfm/agent_tools.py lastfm/commands_agent.py tests/test_trajectories.py tests/test_agent_tools.py tests/test_agent_cli.py tests/conftest.py
```

Expected: both commands exit zero.

## Non-Goals

- No explanation of why activity changed.
- No lifecycle adjectives or generated prose in Python.
- No fuzzy identity resolution.
- No change-point detection or graph analysis.
- No chart rendering.
