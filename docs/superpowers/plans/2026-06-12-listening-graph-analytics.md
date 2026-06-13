# Listening Graph Analytics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deterministic artist co-listening graph with unnamed communities, bridge measurements, artist neighborhoods, and stable JSON or GraphML export.

**Architecture:** A new pure analytics module converts time-bounded listening sessions into an undirected weighted graph, calculates deterministic NetworkX communities and centralities, and serializes measurements without genre labels or interpretation. Existing state and command modules remain thin adapters. The journalism instructions consume these measurements separately and retain sole responsibility for interpretation.

**Tech Stack:** Python 3.10+, pandas, NetworkX 3.x, Typer, pytest, existing JSON command envelopes.

---

## File Structure

### Python analytics and command surface

- Create `lastfm/listening_graph.py`: identity keys, session graph construction, metrics, neighborhood extraction, JSON/GraphML serialization.
- Modify `lastfm/agent_tools.py:15-42`: register and adapt `listening-graph`.
- Modify `lastfm/commands_agent.py:196-446`: expose the command and validate options.
- Modify `pyproject.toml:7-26`: add bounded NetworkX dependency.
- Create `tests/test_listening_graph.py`: deterministic unit tests.
- Modify `tests/test_agent_tools.py`: dispatch test.
- Modify `tests/test_agent_cli.py`: command contract tests.
- Create `docs/analytics/listening-graph.md`: formulas and schema.

### Journalism guidance, deliberately separate

- Modify `skills/lastfm-cli-journalism/SKILL.md`: explain when to request graph evidence, distinguish co-listening from musical similarity, and reserve community names and meaning for the writer.

## Public Analytics Contract

```python
@dataclass(frozen=True)
class GraphConfig:
    gap_minutes: int = 30
    min_artist_plays: int = 10
    min_shared_sessions: int = 2
    start_year: int | None = None
    end_year: int | None = None
    community_resolution: float = 1.0
    community_seed: int = 0
    betweenness_samples: int = 100


def analyze_listening_graph(
    df: pd.DataFrame,
    config: GraphConfig,
    focus_artist: str | None = None,
    hops: int = 1,
    output_format: Literal["json", "graphml"] = "json",
) -> dict[str, Any]:
```

Each session contributes at most one count to an artist pair. For edge `(i, j)`:

```text
shared_sessions = sessions containing both i and j
weight = shared_sessions
distance = 1 / shared_sessions
jaccard = shared_sessions / (sessions_i + sessions_j - shared_sessions)
```

Community IDs are Louvain partitions with sorted insertion order and seed `0`; they are integers, never generated genre names. Node output includes plays, session count, degree, strength, weighted betweenness, closeness, participation coefficient, articulation-point status, and community ID. `bridge_score` is not added: callers receive the constituent measurements rather than an opaque editorial composite.

Artist identity is conservative: use `mbid:<lowercase-mbid>` when present, otherwise `name:<normalize_for_matching(name)>`. Merge name spellings only when they share the same identity key. Do not fuzzy-merge name-only and MBID nodes.

## Task 1: Build and Filter the Session Graph

**Files:**
- Modify: `pyproject.toml`
- Create: `lastfm/listening_graph.py`
- Test: `tests/test_listening_graph.py`

- [ ] **Step 1: Add failing construction tests**

Create `tests/test_listening_graph.py` with explicit timestamps:

```python
import pandas as pd

from lastfm.listening_graph import GraphConfig, build_session_graph


def rows(items):
    frame = pd.DataFrame(items, columns=["timestamp", "artist", "artist_mbid"])
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    return frame


def test_repeats_inside_one_session_contribute_one_pair():
    df = rows([
        ("2024-01-01T00:00:00Z", "A", ""),
        ("2024-01-01T00:03:00Z", "A", ""),
        ("2024-01-01T00:06:00Z", "B", ""),
    ])
    graph, diagnostics = build_session_graph(
        df, GraphConfig(min_artist_plays=1, min_shared_sessions=1)
    )
    assert graph["name:a"]["name:b"]["shared_sessions"] == 1
    assert diagnostics["sessions"] == 1


def test_gap_over_threshold_starts_a_new_session():
    df = rows([
        ("2024-01-01T00:00:00Z", "A", ""),
        ("2024-01-01T00:30:00Z", "B", ""),
        ("2024-01-01T01:00:01Z", "A", ""),
    ])
    graph, diagnostics = build_session_graph(
        df, GraphConfig(min_artist_plays=1, min_shared_sessions=1)
    )
    assert graph["name:a"]["name:b"]["shared_sessions"] == 1
    assert diagnostics["sessions"] == 2
```

- [ ] **Step 2: Run the tests and verify the import fails**

Run: `uv run --extra dev python -m pytest tests/test_listening_graph.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'lastfm.listening_graph'`.

- [ ] **Step 3: Add NetworkX and implement the constructor**

Add `"networkx>=3.2,<4",` to `project.dependencies`. Implement these exact mechanics in `lastfm/listening_graph.py`:

```python
from collections import Counter, defaultdict
from dataclasses import dataclass
from itertools import combinations

import networkx as nx
import pandas as pd

from . import crossref, data


@dataclass(frozen=True)
class GraphConfig:
    gap_minutes: int = 30
    min_artist_plays: int = 10
    min_shared_sessions: int = 2
    start_year: int | None = None
    end_year: int | None = None
    community_resolution: float = 1.0
    community_seed: int = 0
    betweenness_samples: int = 100


def _identity(name: str, mbid: str) -> str | None:
    if pd.notna(mbid) and str(mbid).strip():
        return f"mbid:{str(mbid).strip().lower()}"
    normalized = crossref.normalize_for_matching(name)
    return f"name:{normalized}" if normalized else None


def build_session_graph(df: pd.DataFrame, config: GraphConfig):
    filtered = df.copy()
    if config.start_year is not None:
        filtered = filtered[filtered["timestamp"].dt.year >= config.start_year]
    if config.end_year is not None:
        filtered = filtered[filtered["timestamp"].dt.year <= config.end_year]
    filtered = filtered.sort_values("timestamp", kind="mergesort").copy()
    filtered["node_id"] = [
        _identity(name, mbid)
        for name, mbid in zip(filtered["artist"], filtered["artist_mbid"])
    ]
    empty_artist = int(filtered["node_id"].isna().sum())
    filtered = filtered[filtered["node_id"].notna()]
    play_counts = filtered["node_id"].value_counts()
    eligible = set(play_counts[play_counts >= config.min_artist_plays].index)
    # Detect sessions before artist-frequency filtering. A filtered-out artist
    # between two retained artists must still preserve the original session.
    sessionized = data.detect_sessions(filtered, gap_minutes=config.gap_minutes)
    pair_counts = Counter()
    artist_sessions = defaultdict(set)
    for session_id, group in sessionized.groupby("session_id", sort=True):
        artists = sorted(set(group["node_id"].unique()) & eligible)
        for artist in artists:
            artist_sessions[artist].add(int(session_id))
        pair_counts.update(combinations(artists, 2))
    graph = nx.Graph()
    for node_id in sorted(eligible):
        graph.add_node(
            node_id,
            plays=int(play_counts[node_id]),
            session_count=len(artist_sessions[node_id]),
        )
    for (source, target), count in sorted(pair_counts.items()):
        if count >= config.min_shared_sessions:
            union = len(artist_sessions[source] | artist_sessions[target])
            graph.add_edge(
                source,
                target,
                shared_sessions=int(count),
                weight=int(count),
                distance=1.0 / count,
                jaccard=count / union,
            )
    return graph, {"sessions": int(sessionized["session_id"].nunique()), "empty_artist": empty_artist}
```

- [ ] **Step 4: Add tests for identity, thresholds, year bounds, singleton nodes, empty input, and shuffled row order**

Assert exact node IDs, aliases, counts, and identical sorted edge tuples. Do not test fuzzy matching because it is explicitly excluded.

- [ ] **Step 5: Run focused tests**

Run: `uv run --extra dev python -m pytest tests/test_listening_graph.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock lastfm/listening_graph.py tests/test_listening_graph.py
git commit -m "feat: build session co-listening graphs"
```

## Task 2: Calculate Communities and Bridge Measurements

**Files:**
- Modify: `lastfm/listening_graph.py`
- Test: `tests/test_listening_graph.py`

- [ ] **Step 1: Add failing metric tests**

Use hand-built graphs to assert: a path's middle node is an articulation point and has highest betweenness; participation is zero for an isolate; two disconnected cliques receive distinct communities; repeated runs and reversed insertion order produce the same IDs.

- [ ] **Step 2: Implement deterministic metrics**

```python
def graph_metrics(graph: nx.Graph, config: GraphConfig) -> dict[str, dict]:
    if graph.number_of_nodes() == 0:
        return {}
    communities = list(nx.community.louvain_communities(
        graph,
        weight="weight",
        resolution=config.community_resolution,
        seed=config.community_seed,
    )) if graph.number_of_edges() else [{node} for node in sorted(graph.nodes)]
    communities.sort(key=lambda members: min(members))
    community_by_node = {
        node: index for index, members in enumerate(communities) for node in members
    }
    sample_count = min(config.betweenness_samples, graph.number_of_nodes())
    between = nx.betweenness_centrality(
        graph,
        k=sample_count if sample_count < graph.number_of_nodes() else None,
        weight="distance",
        seed=config.community_seed,
    )
    close = nx.closeness_centrality(graph, distance="distance")
    articulation = set(nx.articulation_points(graph))
    result = {}
    for node in sorted(graph.nodes):
        strength_by_community = Counter()
        for neighbor, edge in graph[node].items():
            strength_by_community[community_by_node[neighbor]] += edge["weight"]
        strength = sum(strength_by_community.values())
        participation = 0.0 if strength == 0 else 1.0 - sum(
            (value / strength) ** 2 for value in strength_by_community.values()
        )
        result[node] = {
            "degree": int(graph.degree(node)),
            "strength": int(strength),
            "betweenness_centrality": float(between[node]),
            "closeness_centrality": float(close[node]),
            "participation_coefficient": float(participation),
            "articulation_point": node in articulation,
            "community_id": community_by_node[node],
        }
    return result
```

- [ ] **Step 3: Run tests and commit**

Run: `uv run --extra dev python -m pytest tests/test_listening_graph.py -v`

Expected: PASS.

```bash
git add lastfm/listening_graph.py tests/test_listening_graph.py
git commit -m "feat: measure graph communities and bridges"
```

## Task 3: Add Neighborhoods and Stable JSON/GraphML Export

**Files:**
- Modify: `lastfm/listening_graph.py`
- Test: `tests/test_listening_graph.py`

- [ ] **Step 1: Add failing serialization tests**

Assert sorted nodes, canonical `source < target` edges, float rounding to 12 places, no NaN, stable compact JSON for shuffled input, a one-hop induced neighborhood, and parseable GraphML from `networkx.parse_graphml`.

- [ ] **Step 2: Implement the result schema**

The JSON result must contain `schema_version`, `graph_type`, `parameters`, `source`, `summary`, `communities`, `nodes`, `edges`, and `diagnostics`. Sort every collection deterministically. For `focus_artist`, resolve by exact case-insensitive display name and return the induced nodes within `hops` shortest unweighted steps. Raise `ValueError("focus artist not found")` when unresolved.

For GraphML, copy only scalar attributes to a fresh NetworkX graph and return:

```python
def graphml_payload(graph: nx.Graph) -> dict[str, str | int]:
    content = "\n".join(nx.generate_graphml(graph, named_key_ids=True))
    return {"schema_version": 1, "format": "graphml", "content": content}
```

Do not write files from the analytics or daemon process. Callers can redirect the stable payload.

- [ ] **Step 3: Run tests and commit**

Run: `uv run --extra dev python -m pytest tests/test_listening_graph.py -v`

Expected: PASS.

```bash
git add lastfm/listening_graph.py tests/test_listening_graph.py
git commit -m "feat: export graph data and neighborhoods"
```

## Task 4: Expose the Analytics Command

**Files:**
- Modify: `lastfm/agent_tools.py:15-42`
- Modify: `lastfm/commands_agent.py:196-446`
- Modify: `tests/test_agent_tools.py`
- Modify: `tests/test_agent_cli.py`

- [ ] **Step 1: Add failing dispatch and command tests**

```python
def test_dispatch_listening_graph(monkeypatch, sample_csv):
    state = loaded_lightweight_state(monkeypatch, sample_csv)
    result = dispatch(state, "listening-graph", {
        "gap_minutes": 30,
        "min_artist_plays": 1,
        "min_shared_sessions": 1,
        "start_year": None,
        "end_year": None,
        "community_resolution": 1.0,
        "community_seed": 0,
        "betweenness_samples": 100,
        "focus_artist": None,
        "hops": 1,
        "output_format": "json",
    })
    assert result["graph_type"] == "artist_session_cooccurrence"
```

Add CLI assertions for registration, every option, one-shot JSON, parameter forwarding, positive-number validation, reversed year bounds, and `json.dumps(result, allow_nan=False)`.

- [ ] **Step 2: Register the thin adapter**

Add `"listening-graph": "get_listening_graph"` to `COMMANDS`. `get_listening_graph` must construct `GraphConfig` and call `analyze_listening_graph`; it must not calculate metrics itself.

- [ ] **Step 3: Register the Typer command**

Expose `--gap-minutes`, `--min-artist-plays`, `--min-shared-sessions`, `--start-year`, `--end-year`, `--community-resolution`, `--community-seed`, `--betweenness-samples`, `--artist`, `--hops`, and `--format [json|graphml]`. Use `typer.BadParameter` for local validation and `_run_agent_command` for execution.

- [ ] **Step 4: Run tests and commit**

Run: `uv run --extra dev python -m pytest tests/test_agent_tools.py tests/test_agent_cli.py -v`

Expected: PASS.

```bash
git add lastfm/agent_tools.py lastfm/commands_agent.py tests/test_agent_tools.py tests/test_agent_cli.py
git commit -m "feat: expose listening graph analytics"
```

## Task 5: Document the Analytics Contract

**Files:**
- Create: `docs/analytics/listening-graph.md`

- [ ] **Step 1: Write the technical documentation**

Document the identity rule, half-open session threshold behavior, pair counting, filters, formulas, approximate-betweenness parameter, Louvain reproducibility boundary, JSON schema, GraphML payload, performance complexity, and empty-graph behavior. State plainly: “Co-occurrence measures listening proximity, not musical similarity.”

- [ ] **Step 2: Commit**

```bash
git add docs/analytics/listening-graph.md
git commit -m "docs: define listening graph measurements"
```

## Task 6: Update Journalism Guidance Separately

**Files:**
- Modify: `skills/lastfm-cli-journalism/SKILL.md`

- [ ] **Step 1: Add a distinct `Graph Evidence` section**

Add these operational rules, without duplicating Python formulas:

```markdown
## Graph Evidence

Use `listening-graph` when the question concerns communities, bridge artists, or local neighborhoods. Treat its communities as unnamed numerical partitions until you inspect their members. Treat edge weights as shared listening sessions, not stylistic similarity.

The analytics layer may report community IDs, centralities, participation, articulation points, and neighborhoods. The journalism layer decides whether those measurements support a useful musical description. Name a community only from its member evidence, and call a bridge important only when the relevant metric and graph scope are stated.

Do not compare centrality values from graphs built with different filters as though they share one scale. Report `min_artist_plays`, `min_shared_sessions`, date bounds, and whether betweenness was sampled.
```

- [ ] **Step 2: Verify the separation**

Run: `rg -n "genre|important|gateway|meaning" lastfm/listening_graph.py docs/analytics/listening-graph.md`

Expected: no generated genre labels or interpretive classifications in Python; documentation may contain only explicit warnings against them.

- [ ] **Step 3: Commit**

```bash
git add skills/lastfm-cli-journalism/SKILL.md
git commit -m "docs: guide graph evidence interpretation"
```

## Task 7: Final Verification

- [ ] **Step 1: Run focused and full tests**

```bash
uv run --extra dev python -m pytest tests/test_listening_graph.py tests/test_agent_tools.py tests/test_agent_cli.py -v
uv run --extra dev python -m pytest
```

Expected: all tests pass.

- [ ] **Step 2: Run Ruff lint and format checks**

```bash
uv run --extra dev ruff check lastfm/listening_graph.py lastfm/agent_tools.py lastfm/commands_agent.py tests/test_listening_graph.py tests/test_agent_tools.py tests/test_agent_cli.py
uv run --extra dev ruff format --check lastfm/listening_graph.py lastfm/agent_tools.py lastfm/commands_agent.py tests/test_listening_graph.py tests/test_agent_tools.py tests/test_agent_cli.py
```

Expected: both commands exit zero.

## Non-Goals

- No genre or community naming in Python.
- No recommendation, surprise, influence, or causal claims.
- No fuzzy artist merging.
- No image rendering.
- No cross-person graph union; that can consume two stable exports later.
