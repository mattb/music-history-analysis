# Listening Change-Point Analytics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect reproducible structural boundaries in calendar-binned artist distributions and return unnamed numerical segments with diagnostics.

**Architecture:** A pure module builds continuous UTC weekly or monthly artist vectors, transforms them, and performs exact penalized multivariate segmentation using prefix-sum SSE. It returns boundary dates, objective values, segment statistics, and artist-share deltas. Interpretation and labels such as “Berlin period” remain exclusively in the journalism instructions.

**Tech Stack:** Python 3.10+, pandas, NumPy, Typer, pytest, existing JSON envelopes; no new segmentation dependency.

---

## File Structure

### Python analytics and command surface

- Create `lastfm/change_points.py`.
- Modify `lastfm/agent_tools.py:15-42` and append `get_listening_change_points`.
- Modify `lastfm/commands_agent.py:196-446` with `listening-change-points`.
- Create `tests/test_change_points.py`.
- Modify `tests/test_agent_tools.py` and `tests/test_agent_cli.py`.
- Create `docs/analytics/listening-change-points.md`.

### Journalism guidance, deliberately separate

- Modify `skills/lastfm-cli-journalism/SKILL.md` only after the segmentation contract passes.

## Public Contract

```python
@dataclass(frozen=True)
class ChangePointSpec:
    frequency: Literal["week", "month"] = "month"
    vector_mode: Literal["shares", "counts"] = "shares"
    top_artists: int = 100
    min_segment_bins: int = 6
    penalty_multiplier: float = 1.0
    top_deltas: int = 20


def detect_change_points(df: pd.DataFrame, spec: ChangePointSpec) -> dict[str, Any]:
```

Vocabulary is the globally most-played `top_artists`, sorted by descending plays then artist name, plus `__OTHER__`. Every UTC calendar bin from first through last is present, including zero-play bins.

Share vectors use Hellinger coordinates `sqrt(count / total)`. Count vectors use `log1p(count)` followed by per-component population-standardization; constant components become zero.

For transformed vectors `x[t]`, segment cost is multivariate within-segment SSE. Exact dynamic programming minimizes:

```text
sum(segment SSE) + penalty * number_of_change_points
```

subject to every segment containing at least `min_segment_bins`.

## Task 1: Build Continuous Calendar Vectors

**Files:**
- Create: `lastfm/change_points.py`
- Create: `tests/test_change_points.py`

- [ ] **Step 1: Add failing bin and vocabulary tests**

```python
import numpy as np
import pandas as pd

from lastfm.change_points import ChangePointSpec, build_vectors


def frame(rows):
    df = pd.DataFrame(rows, columns=["timestamp", "artist"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df


def test_missing_month_is_an_explicit_zero_bin():
    result = build_vectors(frame([
        ("2020-01-02T00:00:00Z", "A"),
        ("2020-03-02T00:00:00Z", "A"),
    ]), ChangePointSpec(top_artists=1, min_segment_bins=1))
    assert result.labels == ["2020-01", "2020-02", "2020-03"]
    assert result.counts[1].sum() == 0


def test_other_column_preserves_total_plays():
    result = build_vectors(frame([
        ("2020-01-01T00:00:00Z", "A"),
        ("2020-01-02T00:00:00Z", "B"),
        ("2020-01-03T00:00:00Z", "C"),
    ]), ChangePointSpec(top_artists=1, min_segment_bins=1))
    assert result.vocabulary[-1] == "__OTHER__"
    assert int(result.counts.sum()) == 3
```

- [ ] **Step 2: Verify the import failure**

Run: `uv run --extra dev python -m pytest tests/test_change_points.py -v`

Expected: FAIL with an import error.

- [ ] **Step 3: Implement validation and vector construction**

```python
from dataclasses import dataclass
from typing import Literal, NamedTuple

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ChangePointSpec:
    frequency: Literal["week", "month"] = "month"
    vector_mode: Literal["shares", "counts"] = "shares"
    top_artists: int = 100
    min_segment_bins: int = 6
    penalty_multiplier: float = 1.0
    top_deltas: int = 20


class VectorData(NamedTuple):
    labels: list[str]
    starts: list[pd.Timestamp]
    vocabulary: list[str]
    counts: np.ndarray
    transformed: np.ndarray


def _frequency(value: str) -> str:
    if value == "month":
        return "M"
    if value == "week":
        return "W-SUN"
    raise ValueError("frequency must be 'week' or 'month'")


def build_vectors(df: pd.DataFrame, spec: ChangePointSpec) -> VectorData:
    if df.empty:
        raise ValueError("Listening history is empty")
    if spec.vector_mode not in {"shares", "counts"}:
        raise ValueError("vector_mode must be 'shares' or 'counts'")
    if spec.top_artists < 1 or spec.top_deltas < 1:
        raise ValueError("top_artists and top_deltas must be at least 1")
    if spec.min_segment_bins < 1 or spec.penalty_multiplier <= 0:
        raise ValueError("segment length and penalty must be positive")
    frequency = _frequency(spec.frequency)
    periods = df["timestamp"].dt.to_period(frequency)
    index = pd.period_range(periods.min(), periods.max(), freq=frequency)
    totals = df["artist"].value_counts()
    ordered = sorted(totals.items(), key=lambda item: (-item[1], item[0]))
    vocabulary = [name for name, _ in ordered[:spec.top_artists]] + ["__OTHER__"]
    work = df.assign(period=periods)
    work["feature"] = work["artist"].where(work["artist"].isin(vocabulary[:-1]), "__OTHER__")
    table = work.groupby(["period", "feature"]).size().unstack(fill_value=0)
    table = table.reindex(index=index, columns=vocabulary, fill_value=0)
    counts = table.to_numpy(dtype=float)
    if spec.vector_mode == "shares":
        row_totals = counts.sum(axis=1, keepdims=True)
        shares = np.divide(counts, row_totals, out=np.zeros_like(counts), where=row_totals != 0)
        transformed = np.sqrt(shares)
    else:
        logged = np.log1p(counts)
        std = logged.std(axis=0)
        transformed = np.divide(
            logged - logged.mean(axis=0), std,
            out=np.zeros_like(logged), where=std != 0,
        )
    return VectorData(
        [str(period) for period in index],
        [period.start_time.tz_localize("UTC") for period in index],
        vocabulary, counts, transformed,
    )
```

- [ ] **Step 4: Add tests for ISO weeks, stable tie order, share sums, Hellinger values, count standardization, constant columns, shuffled rows, and invalid parameters**

- [ ] **Step 5: Run tests and commit**

```bash
uv run --extra dev python -m pytest tests/test_change_points.py -v
git add lastfm/change_points.py tests/test_change_points.py
git commit -m "feat: build listening distribution vectors"
```

## Task 2: Implement Exact Penalized Segmentation

**Files:**
- Modify: `lastfm/change_points.py`
- Test: `tests/test_change_points.py`

- [ ] **Step 1: Add failing cost and synthetic-regime tests**

Assert prefix cost equals a direct NumPy SSE, two known regimes return one exact boundary, three regimes return two, a constant series returns none, all segment lengths meet the minimum, higher penalty never increases boundary count, and repeated runs are identical.

- [ ] **Step 2: Implement prefix-sum cost and penalty**

```python
def _cost_factory(x: np.ndarray):
    prefix = np.vstack([np.zeros(x.shape[1]), np.cumsum(x, axis=0)])
    squared = np.concatenate([[0.0], np.cumsum(np.einsum("ij,ij->i", x, x))])

    def cost(start: int, end: int) -> float:
        length = end - start
        vector_sum = prefix[end] - prefix[start]
        return float(squared[end] - squared[start] - vector_sum @ vector_sum / length)

    return cost


def _penalty(x: np.ndarray, multiplier: float) -> tuple[float, float, int]:
    active_dimensions = int((x.var(axis=0) > 0).sum())
    if len(x) < 2 or active_dimensions == 0:
        return 0.0, 0.0, active_dimensions
    distances = np.einsum("ij,ij->i", np.diff(x, axis=0), np.diff(x, axis=0))
    positive = distances[distances > 0]
    scale_source = np.median(distances)
    if scale_source == 0 and len(positive):
        scale_source = positive.min()
    variance = float(scale_source / (2 * active_dimensions)) if scale_source else 0.0
    return multiplier * variance * active_dimensions * np.log(len(x)), variance, active_dimensions
```

- [ ] **Step 3: Implement deterministic dynamic programming**

```python
def segment(x: np.ndarray, minimum: int, penalty: float) -> tuple[list[int], float]:
    n = len(x)
    cost = _cost_factory(x)
    best = [float("inf")] * (n + 1)
    paths: list[tuple[int, ...] | None] = [None] * (n + 1)
    best[0], paths[0] = -penalty, ()
    for end in range(minimum, n + 1):
        for start in range(0, end - minimum + 1):
            if start != 0 and paths[start] is None:
                continue
            value = best[start] + cost(start, end) + penalty
            candidate = paths[start] + ((start,) if start else ())
            current = paths[end]
            tie_breaks_earlier = current is None or (
                len(candidate), candidate
            ) < (len(current), current)
            if value < best[end] - 1e-12 or (
                abs(value - best[end]) <= 1e-12 and tie_breaks_earlier
            ):
                best[end], paths[end] = value, candidate
    if paths[n] is None:
        raise ValueError("history has too few bins for the requested minimum segment length")
    return list(paths[n]), float(best[n])
```

Because `best[start]` is populated only for feasible prefixes and `end - start >= minimum` is enforced by the loop bounds, every segment satisfies the minimum. Tests must include cases that would otherwise leave a short first or middle segment. When objective values tie, compare number of boundaries first, then boundary tuples lexicographically.

- [ ] **Step 4: Run tests and commit**

```bash
uv run --extra dev python -m pytest tests/test_change_points.py -v
git add lastfm/change_points.py tests/test_change_points.py
git commit -m "feat: detect penalized listening boundaries"
```

## Task 3: Return Unnamed Segments, Boundaries, and Deltas

**Files:**
- Modify: `lastfm/change_points.py`
- Test: `tests/test_change_points.py`

- [ ] **Step 1: Add failing schema tests**

Assert boundary timestamps identify the first bin of the right segment; segment counts reconcile to the input; top artist-share deltas are correct and deterministically ordered; empty and low-volume bins appear only in diagnostics; and no `name`, `label`, `description`, location, genre, or causal field exists.

- [ ] **Step 2: Implement `detect_change_points`**

Build vectors, calculate penalty, segment, and return:

```text
schema_version
timezone = UTC
parameters
vector: frequency, mode, transformation, dimensions, active_dimensions
model: algorithm, noise_variance, penalty, objective
change_points: bin_index, timestamp, left_segment, right_segment,
               centroid_distance, plays_per_bin_delta, artist_share_deltas
segments: index, start, end_exclusive, bins, plays, plays_per_bin,
          unique_artists, empty_bins, top_artist_shares
diagnostics: total bins, empty-bin labels, mechanically low-volume bins (<10 plays),
             constant_series
```

Round floats to ten places. Sort delta rows by descending absolute delta, then artist. `constant_series` returns one segment and no boundaries without error.

- [ ] **Step 3: Run tests and commit**

```bash
uv run --extra dev python -m pytest tests/test_change_points.py -v
git add lastfm/change_points.py tests/test_change_points.py
git commit -m "feat: summarize unnamed listening segments"
```

## Task 4: Expose the Analytics Command

**Files:**
- Modify: `lastfm/agent_tools.py`
- Modify: `lastfm/commands_agent.py`
- Modify: `tests/test_agent_tools.py`
- Modify: `tests/test_agent_cli.py`

- [ ] **Step 1: Add failing dispatch and CLI tests**

```python
def test_dispatch_listening_change_points(monkeypatch, sample_csv):
    state = loaded_lightweight_state(monkeypatch, sample_csv)
    result = dispatch(state, "listening-change-points", {
        "frequency": "month",
        "vector_mode": "shares",
        "top_artists": 10,
        "min_segment_bins": 1,
        "penalty_multiplier": 1.0,
        "top_deltas": 5,
    })
    assert result["model"]["algorithm"] == "exact_penalized_sse"
```

Also test registration, every option, one-shot/session equality, failure envelopes, and `json.dumps(result, allow_nan=False)`.

- [ ] **Step 2: Register a thin adapter**

Add `"listening-change-points": "get_listening_change_points"` to `COMMANDS`. The wrapper constructs `ChangePointSpec` and calls `detect_change_points(state.df, spec)`.

- [ ] **Step 3: Register the Typer command**

Expose `--frequency`, `--vector-mode`, `--top-artists`, `--min-segment-bins`, `--penalty-multiplier`, and `--top-deltas`, plus standard target and JSON options.

- [ ] **Step 4: Run tests and commit**

```bash
uv run --extra dev python -m pytest tests/test_agent_tools.py tests/test_agent_cli.py -v
git add lastfm/agent_tools.py lastfm/commands_agent.py tests/test_agent_tools.py tests/test_agent_cli.py
git commit -m "feat: expose listening change-point analytics"
```

## Task 5: Document the Numerical Contract

**Files:**
- Create: `docs/analytics/listening-change-points.md`

- [ ] **Step 1: Document vectors and optimization**

Document UTC bins, zero bins, vocabulary and `__OTHER__`, transformations, SSE, noise scale, penalty, dynamic-programming tie rules, segment minimums, delta calculations, complexity `O(n²d)`, diagnostics, and how parameter changes make results non-comparable.

- [ ] **Step 2: Commit**

```bash
git add docs/analytics/listening-change-points.md
git commit -m "docs: define listening change-point measurements"
```

## Task 6: Update Journalism Guidance Separately

**Files:**
- Modify: `skills/lastfm-cli-journalism/SKILL.md`

- [ ] **Step 1: Add a distinct `Change-Point Evidence` section**

```markdown
## Change-Point Evidence

Use `listening-change-points` to locate candidate structural boundaries without choosing the years first. The returned segments are numbered statistical partitions, not named eras.

Inspect segment volume, empty-bin diagnostics, boundary centroid distance, and artist-share deltas before describing a change. Compare runs only when frequency, vector mode, vocabulary size, minimum segment length, and penalty match.

The journalism layer may name a period from external biographical context supplied by the user, but must state that the name is interpretation. The analytics establish a distributional boundary; they do not establish a location, cause, mood, or life event.
```

- [ ] **Step 2: Verify separation**

Run: `rg -n 'Berlin|era_name|genre|mood|cause|meaning' lastfm/change_points.py`

Expected: no matches.

- [ ] **Step 3: Commit**

```bash
git add skills/lastfm-cli-journalism/SKILL.md
git commit -m "docs: guide change-point interpretation"
```

## Task 7: Final Verification

- [ ] **Step 1: Run tests**

```bash
uv run --extra dev python -m pytest tests/test_change_points.py tests/test_agent_tools.py tests/test_agent_cli.py -v
uv run --extra dev python -m pytest
```

Expected: all tests pass.

- [ ] **Step 2: Run Ruff**

```bash
uv run --extra dev ruff check lastfm/change_points.py lastfm/agent_tools.py lastfm/commands_agent.py tests/test_change_points.py tests/test_agent_tools.py tests/test_agent_cli.py
uv run --extra dev ruff format --check lastfm/change_points.py lastfm/agent_tools.py lastfm/commands_agent.py tests/test_change_points.py tests/test_agent_tools.py tests/test_agent_cli.py
```

Expected: both commands exit zero.

## Non-Goals

- No segment names, genre labels, locations, moods, or causes in Python.
- No life-event matching.
- No graph analysis or recommendation.
- No dropped empty bins.
- No generated prose or chart rendering.
