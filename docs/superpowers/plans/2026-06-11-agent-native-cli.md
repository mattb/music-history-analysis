# Agent-Native Last.fm CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the MCP server with a top-level, agent-friendly CLI that exposes the same analysis capabilities, supports long-lived named daemon sessions, and has a self-describing JSON contract.

**Architecture:** Extract MCP state and tool bodies into reusable Python modules, then make Typer top-level commands call those modules either directly (`--csv`) or through a named daemon session (`--session`). The daemon stores per-session metadata and listens on a Unix domain socket under `~/.cache/lastfm-analysis/sessions/<session-id>/`, allowing multiple concurrent sessions. After CLI parity is covered by tests, remove `lastfm/mcp_server.py`.

**Tech Stack:** Python 3.10+, Typer, pandas, existing Last.fm analysis modules, stdlib `socketserver`/Unix sockets, JSON/NDJSON stdout contracts, pytest for new tests.

---

## File Structure

- Create `lastfm/analysis_state.py`: owns extracted `AnalysisState`, CSV discovery, critics JSON loading, lazy indices, and loaded-session metadata.
- Create `lastfm/agent_tools.py`: pure Python functions for the former MCP tools and resources. Functions accept an `AnalysisState` and return JSON-serializable dict/list values.
- Create `lastfm/agent_output.py`: shared JSON envelope helpers, NDJSON lifecycle event helpers, and stable error payloads.
- Create `lastfm/session_daemon.py`: daemon process entrypoint and request handler for named sessions over Unix sockets.
- Create `lastfm/session_client.py`: CLI client for session lifecycle and command dispatch.
- Create `lastfm/commands_agent.py`: top-level Typer command functions registered on the root app with MCP-equivalent hyphenated names plus session lifecycle commands.
- Modify `lastfm/cli.py`: register top-level agent commands, improve root help for agent workflows, and keep existing human command groups intact.
- Delete `lastfm/mcp_server.py` after parity tests pass.
- Create `tests/`: pytest coverage for state extraction, agent tool parity, JSON envelopes, CLI help, session lifecycle, and daemon dispatch.
- Modify `pyproject.toml`: add pytest as an optional dev dependency and expose any needed package data only if tests prove it is required.

## Public CLI Contract

Top-level session lifecycle commands:

```bash
lastfm session-start --session-id music-2025 --csv /abs/path/recenttracks.csv --json
lastfm session-status --session music-2025 --json
lastfm session-list --json
lastfm session-stop --session music-2025 --json
lastfm session-cleanup --older-than 24h --json
```

Top-level analysis commands:

```text
taste-evolution
musical-bridges
blind-spots
artist-deep-dive
similar-artists
listening-stats
top-artists
critic-alignment
temporal-patterns
period-summary
year-review
listening-by-release-era
common-transitions
discovery-context
critics-world
album-acclaim
validated-albums
critic-profile
search-critics-artist
obsession-tracks
one-track-artists
ep-single-artists
overview-summary
discovered-artists
critics-lists
```

All analysis commands support exactly one execution target:

```text
--session SESSION_ID    Dispatch to a long-lived daemon session.
--csv PATH              Run one-shot by loading the CSV in this process.
```

All agent-facing commands support:

```text
--json                  Emit machine-readable JSON only on stdout.
--pretty                Emit human-readable Rich output.
```

Default output for new agent-facing analysis commands is JSON. Existing pre-agent commands keep their current defaults.

JSON success envelope:

```json
{
  "ok": true,
  "command": "artist-deep-dive",
  "session_id": "music-2025",
  "result": {}
}
```

JSON failure envelope:

```json
{
  "ok": false,
  "command": "artist-deep-dive",
  "session_id": "music-2025",
  "error": {
    "code": "SESSION_NOT_FOUND",
    "message": "No running session named music-2025",
    "retryable": false
  }
}
```

`session-start --json` emits NDJSON lifecycle events on stdout:

```json
{"event":"start","session_id":"music-2025"}
{"event":"load_csv","session_id":"music-2025","path":"/abs/path/recenttracks.csv"}
{"event":"build_user_embeddings","session_id":"music-2025","cache":"hit"}
{"event":"build_critics_embeddings","session_id":"music-2025","cache":"hit"}
{"event":"ready","session_id":"music-2025","socket":"/Users/mattb/.cache/lastfm-analysis/sessions/music-2025/lastfm.sock"}
```

## Task 1: Add Test Harness And Fixture Data

**Files:**
- Modify: `/Users/mattb/Dev/music-2025/pyproject.toml`
- Create: `/Users/mattb/Dev/music-2025/tests/conftest.py`
- Create: `/Users/mattb/Dev/music-2025/tests/test_agent_output.py`

- [ ] **Step 1: Add pytest dev dependency**

Add this block to `/Users/mattb/Dev/music-2025/pyproject.toml`:

```toml
[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
]
```

- [ ] **Step 2: Create reusable pytest fixtures**

Create `/Users/mattb/Dev/music-2025/tests/conftest.py`:

```python
from pathlib import Path

import pytest


@pytest.fixture
def sample_csv(tmp_path: Path) -> Path:
    path = tmp_path / "recenttracks-test-1.csv"
    path.write_text(
        "\n".join(
            [
                "uts,utc_time,artist,artist_mbid,album,album_mbid,track,track_mbid",
                "1704067200,01 Jan 2024,Artist A,,Album A,,Track 1,",
                "1704153600,02 Jan 2024,Artist A,,Album A,,Track 2,",
                "1704240000,03 Jan 2024,Artist B,,Album B,,Track 1,",
                "1735689600,01 Jan 2025,Artist C,,Album C,,Track 1,",
            ]
        )
        + "\n"
    )
    return path


@pytest.fixture
def critics_file(tmp_path: Path) -> Path:
    path = tmp_path / "critics-2024.json"
    path.write_text(
        """[
  {
    "critic": "Critic One",
    "publication": "Example Weekly",
    "albums": [
      {"artist": "Artist A", "title": "Album A", "rank": 1},
      {"artist": "Artist D", "title": "Album D", "rank": 2}
    ]
  }
]"""
    )
    return path
```

- [ ] **Step 3: Add first failing tests for JSON envelopes**

Create `/Users/mattb/Dev/music-2025/tests/test_agent_output.py`:

```python
from lastfm.agent_output import error_envelope, success_envelope


def test_success_envelope_contains_command_session_and_result():
    assert success_envelope(
        command="artist-deep-dive",
        result={"artist": "Artist A"},
        session_id="music-2025",
    ) == {
        "ok": True,
        "command": "artist-deep-dive",
        "session_id": "music-2025",
        "result": {"artist": "Artist A"},
    }


def test_error_envelope_contains_stable_error_contract():
    assert error_envelope(
        command="artist-deep-dive",
        code="SESSION_NOT_FOUND",
        message="No running session named music-2025",
        retryable=False,
        session_id="music-2025",
    ) == {
        "ok": False,
        "command": "artist-deep-dive",
        "session_id": "music-2025",
        "error": {
            "code": "SESSION_NOT_FOUND",
            "message": "No running session named music-2025",
            "retryable": False,
        },
    }
```

- [ ] **Step 4: Run tests to verify initial failure**

Run:

```bash
python3 -m pytest tests/test_agent_output.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'lastfm.agent_output'`.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml tests/conftest.py tests/test_agent_output.py
git commit -m "test: add agent cli test harness"
```

## Task 2: Add Shared Agent Output Contract

**Files:**
- Create: `/Users/mattb/Dev/music-2025/lastfm/agent_output.py`
- Test: `/Users/mattb/Dev/music-2025/tests/test_agent_output.py`

- [ ] **Step 1: Implement JSON envelope helpers**

Create `/Users/mattb/Dev/music-2025/lastfm/agent_output.py`:

```python
"""Structured output helpers for agent-facing CLI commands."""

from __future__ import annotations

import json
import sys
from typing import Any


def success_envelope(command: str, result: Any, session_id: str | None = None) -> dict[str, Any]:
    return {
        "ok": True,
        "command": command,
        "session_id": session_id,
        "result": result,
    }


def error_envelope(
    command: str,
    code: str,
    message: str,
    retryable: bool,
    session_id: str | None = None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "command": command,
        "session_id": session_id,
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
        },
    }


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def emit_event(event: str, **fields: Any) -> None:
    payload = {"event": event, **fields}
    sys.stdout.write(json.dumps(payload, sort_keys=True) + "\n")
    sys.stdout.flush()
```

- [ ] **Step 2: Run tests**

Run:

```bash
python3 -m pytest tests/test_agent_output.py -v
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add lastfm/agent_output.py tests/test_agent_output.py
git commit -m "feat: add structured agent output envelopes"
```

## Task 3: Extract MCP Session State

**Files:**
- Create: `/Users/mattb/Dev/music-2025/lastfm/analysis_state.py`
- Modify: `/Users/mattb/Dev/music-2025/lastfm/mcp_server.py`
- Create: `/Users/mattb/Dev/music-2025/tests/test_analysis_state.py`

- [ ] **Step 1: Write failing tests for CSV discovery and metadata**

Create `/Users/mattb/Dev/music-2025/tests/test_analysis_state.py`:

```python
from pathlib import Path

import pytest

from lastfm.analysis_state import AnalysisState, find_csv


def test_find_csv_prefers_explicit_env(monkeypatch, sample_csv):
    monkeypatch.setenv("LASTFM_CSV", str(sample_csv))
    assert find_csv(Path.cwd()) == sample_csv


def test_find_csv_uses_newest_recenttracks(monkeypatch, tmp_path):
    monkeypatch.delenv("LASTFM_CSV", raising=False)
    older = tmp_path / "recenttracks-user-1.csv"
    newer = tmp_path / "recenttracks-user-2.csv"
    older.write_text("x\n")
    newer.write_text("x\n")
    assert find_csv(tmp_path) == newer


def test_state_metadata_after_lightweight_load(monkeypatch, sample_csv):
    state = AnalysisState()
    monkeypatch.setattr(state, "_build_user_embeddings", lambda: None)
    monkeypatch.setattr(state, "_build_critics_embeddings", lambda: None)
    monkeypatch.setattr(state, "_build_critic_vectors", lambda: None)
    state.load(sample_csv)
    assert state.metadata()["csv_path"] == str(sample_csv)
    assert state.metadata()["plays"] == 4
    assert state.metadata()["artists"] == 3
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
python3 -m pytest tests/test_analysis_state.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'lastfm.analysis_state'`.

- [ ] **Step 3: Create extracted state module**

Create `/Users/mattb/Dev/music-2025/lastfm/analysis_state.py` by moving `AnalysisState`, `_find_csv`, and `_to_serializable` behavior out of `/Users/mattb/Dev/music-2025/lastfm/mcp_server.py`. The module must expose this public API:

```python
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import numpy as np

from . import crossref, data, embeddings


def to_serializable(obj: Any) -> Any:
    """Convert numpy and pandas-adjacent values to JSON-serializable Python values."""
    if isinstance(obj, dict):
        return {k: to_serializable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [to_serializable(v) for v in obj]
    if isinstance(obj, set):
        return [to_serializable(v) for v in obj]
    if isinstance(obj, (np.integer, np.int64, np.int32)):
        return int(obj)
    if isinstance(obj, (np.floating, np.float64, np.float32)):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, np.bool_):
        return bool(obj)
    return obj


def find_csv(cwd: Path | None = None) -> Path | None:
    env_path = os.environ.get("LASTFM_CSV")
    if env_path:
        path = Path(env_path)
        if path.exists():
            return path

    base = cwd or Path.cwd()
    csvs = list(base.glob("recenttracks-*.csv"))
    if csvs:
        return sorted(csvs)[-1]
    return None


class AnalysisState:
    def __init__(self, critics_root: Path | None = None):
        self.csv_path: Path | None = None
        self.critics_root = critics_root or Path(__file__).parent.parent
        self.df = None
        self.user_embeddings = None
        self.critics_embeddings = None
        self.critic_vectors = None
        self._critics_cache: dict[int, list] = {}
        self._album_critics_index: dict | None = None
        self._critic_picks_index: dict | None = None
        self._listened_albums_cache: set | None = None

    def is_loaded(self) -> bool:
        return self.df is not None

    def load(self, csv_path: Path | None = None) -> None:
        resolved = csv_path or find_csv()
        if resolved is None:
            raise ValueError(
                "No CSV found. Set LASTFM_CSV or pass --csv with a recenttracks-*.csv file."
            )
        self.csv_path = Path(resolved)
        self.df = data.load_scrobbles(self.csv_path)
        self._build_user_embeddings()
        self._build_critics_embeddings()
        self._build_critic_vectors()

    def _build_user_embeddings(self) -> None:
        self.user_embeddings = embeddings.build_embeddings_from_csv(self.csv_path)

    def _build_critics_embeddings(self) -> None:
        try:
            self.critics_embeddings = embeddings.get_or_build_critics_embeddings()
        except Exception:
            self.critics_embeddings = None

    def _build_critic_vectors(self) -> None:
        try:
            self.critic_vectors = embeddings.get_or_build_critic_vectors()
        except Exception:
            self.critic_vectors = None

    def metadata(self) -> dict[str, Any]:
        if self.df is None:
            return {"loaded": False}
        return to_serializable({
            "loaded": True,
            "csv_path": str(self.csv_path),
            "plays": len(self.df),
            "artists": self.df["artist"].nunique(),
            "date_range": {
                "first": self.df["timestamp"].min().isoformat(),
                "last": self.df["timestamp"].max().isoformat(),
            },
            "critics_years": self.get_all_critics_years(),
        })

    def get_critics_data(self, year: int) -> list:
        if year not in self._critics_cache:
            critics_path = self.critics_root / f"critics-{year}.json"
            if critics_path.exists():
                with open(critics_path) as f:
                    self._critics_cache[year] = json.load(f)
            else:
                self._critics_cache[year] = []
        return self._critics_cache[year]

    def get_all_critics_years(self) -> list[int]:
        return [
            year
            for year in range(2011, 2026)
            if (self.critics_root / f"critics-{year}.json").exists()
        ]

    def _build_critics_indices(self) -> None:
        if self._album_critics_index is not None:
            return
        self._album_critics_index = {}
        self._critic_picks_index = {}
        for year in self.get_all_critics_years():
            for critic_list in self.get_critics_data(year):
                critic = critic_list.get("critic", "Unknown")
                publication = critic_list.get("publication", "Unknown")
                self._critic_picks_index.setdefault(
                    critic, {"publication": publication, "picks": []}
                )
                for album in critic_list.get("albums", []):
                    artist = album.get("artist", "")
                    title = album.get("title", "")
                    rank = album.get("rank")
                    if not artist or not title:
                        continue
                    key = (
                        crossref.normalize_for_matching(artist),
                        crossref.normalize_for_matching(title),
                    )
                    self._album_critics_index.setdefault(
                        key, {"artist": artist, "album": title, "critics": []}
                    )
                    self._album_critics_index[key]["critics"].append({
                        "critic": critic,
                        "publication": publication,
                        "year": year,
                        "rank": rank,
                    })
                    self._critic_picks_index[critic]["picks"].append({
                        "artist": artist,
                        "album": title,
                        "year": year,
                        "rank": rank,
                    })

    def get_album_critics_index(self) -> dict:
        self._build_critics_indices()
        return self._album_critics_index

    def get_critic_picks_index(self) -> dict:
        self._build_critics_indices()
        return self._critic_picks_index

    def get_listened_albums(self, min_familiarity: float = 0.4) -> set:
        if self._listened_albums_cache is None:
            listened = data.get_albums_by_familiarity(self.df, min_familiarity=min_familiarity)
            self._listened_albums_cache = {
                (crossref.normalize_for_matching(a), crossref.normalize_for_matching(t))
                for a, t in listened
            }
        return self._listened_albums_cache
```

- [ ] **Step 4: Update MCP imports without changing behavior**

Modify `/Users/mattb/Dev/music-2025/lastfm/mcp_server.py` so it imports:

```python
from .analysis_state import AnalysisState, find_csv as _find_csv, to_serializable as _to_serializable
```

Then remove the duplicated local `_to_serializable`, `AnalysisState`, and `_find_csv` definitions from `mcp_server.py`. Keep `_state = AnalysisState()` and all `@mcp.tool` bodies unchanged except for imports.

- [ ] **Step 5: Run tests**

Run:

```bash
python3 -m pytest tests/test_analysis_state.py tests/test_agent_output.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add lastfm/analysis_state.py lastfm/mcp_server.py tests/test_analysis_state.py
git commit -m "refactor: extract reusable analysis state"
```

## Task 4: Move MCP Tools Into Shared Agent Functions

**Files:**
- Create: `/Users/mattb/Dev/music-2025/lastfm/agent_tools.py`
- Modify: `/Users/mattb/Dev/music-2025/lastfm/mcp_server.py`
- Create: `/Users/mattb/Dev/music-2025/tests/test_agent_tools.py`

- [ ] **Step 1: Add tests for a small representative tool subset**

Create `/Users/mattb/Dev/music-2025/tests/test_agent_tools.py`:

```python
from lastfm.agent_tools import get_listening_stats, get_top_artists
from lastfm.analysis_state import AnalysisState


def loaded_lightweight_state(monkeypatch, sample_csv):
    state = AnalysisState()
    monkeypatch.setattr(state, "_build_user_embeddings", lambda: None)
    monkeypatch.setattr(state, "_build_critics_embeddings", lambda: None)
    monkeypatch.setattr(state, "_build_critic_vectors", lambda: None)
    state.load(sample_csv)
    return state


def test_get_listening_stats_all_time(monkeypatch, sample_csv):
    state = loaded_lightweight_state(monkeypatch, sample_csv)
    result = get_listening_stats(state)
    assert result["period"] == "all time"
    assert result["total_plays"] == 4
    assert result["unique_artists"] == 3


def test_get_top_artists(monkeypatch, sample_csv):
    state = loaded_lightweight_state(monkeypatch, sample_csv)
    assert get_top_artists(state, limit=2) == [
        {"artist": "Artist A", "plays": 2},
        {"artist": "Artist B", "plays": 1},
    ]
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
python3 -m pytest tests/test_agent_tools.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'lastfm.agent_tools'`.

- [ ] **Step 3: Create agent tool module and move all MCP tool bodies**

Create `/Users/mattb/Dev/music-2025/lastfm/agent_tools.py`. Move every current MCP tool/resource implementation from `/Users/mattb/Dev/music-2025/lastfm/mcp_server.py` into this module as functions that take `state: AnalysisState` as the first argument. Preserve all existing return shapes.

The module must define this complete public command map:

```python
from __future__ import annotations

from typing import Any

from .analysis_state import AnalysisState, to_serializable

COMMANDS = {
    "taste-evolution": "explore_taste_evolution",
    "musical-bridges": "find_musical_bridges",
    "blind-spots": "discover_blind_spots",
    "artist-deep-dive": "get_artist_deep_dive",
    "similar-artists": "find_similar_artists",
    "listening-stats": "get_listening_stats",
    "top-artists": "get_top_artists",
    "critic-alignment": "get_critic_alignment",
    "temporal-patterns": "get_temporal_patterns",
    "period-summary": "get_period_summary",
    "year-review": "get_year_review",
    "listening-by-release-era": "get_listening_by_release_era",
    "common-transitions": "get_common_transitions",
    "discovery-context": "get_discovery_context",
    "critics-world": "explore_critics_world",
    "album-acclaim": "get_album_acclaim",
    "validated-albums": "get_my_validated_albums",
    "critic-profile": "get_critic_profile",
    "search-critics-artist": "search_critics_for_artist",
    "obsession-tracks": "get_obsession_tracks",
    "one-track-artists": "get_one_track_artists",
    "ep-single-artists": "get_ep_single_artists",
    "overview-summary": "get_overview",
    "discovered-artists": "get_discovered_artists",
    "critics-lists": "get_critics_lists",
}


def dispatch(state: AnalysisState, command: str, params: dict[str, Any]) -> Any:
    if command not in COMMANDS:
        raise ValueError(f"Unknown agent command: {command}")
    fn = globals()[COMMANDS[command]]
    return to_serializable(fn(state, **params))
```

Convert each function signature from:

```python
def get_top_artists(year: Optional[int] = None, limit: int = 20) -> list:
    _ensure_loaded()
    df = _state.df
```

to:

```python
def get_top_artists(state: AnalysisState, year: int | None = None, limit: int = 20) -> list:
    df = state.df
```

Apply that same conversion for all listed commands. Replace `_state` with `state`, `_ensure_loaded()` with no-op assumptions because callers load state before dispatch, and `_to_serializable` with `to_serializable`.

- [ ] **Step 4: Keep MCP working during migration**

Modify `/Users/mattb/Dev/music-2025/lastfm/mcp_server.py` so each `@mcp.tool` wrapper delegates to `agent_tools`.

Example wrapper:

```python
@mcp.tool
def get_top_artists(year: int | None = None, limit: int = 20) -> list:
    _ensure_loaded()
    return agent_tools.get_top_artists(_state, year=year, limit=limit)
```

Do this for every former MCP tool/resource/prompt-backed function. Keep prompt text in `mcp_server.py` until MCP deletion.

- [ ] **Step 5: Run tests**

Run:

```bash
python3 -m pytest tests/test_agent_tools.py tests/test_analysis_state.py tests/test_agent_output.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add lastfm/agent_tools.py lastfm/mcp_server.py tests/test_agent_tools.py
git commit -m "refactor: move mcp analysis tools into shared agent functions"
```

## Task 5: Add Top-Level One-Shot Agent Commands

**Files:**
- Create: `/Users/mattb/Dev/music-2025/lastfm/commands_agent.py`
- Modify: `/Users/mattb/Dev/music-2025/lastfm/cli.py`
- Create: `/Users/mattb/Dev/music-2025/tests/test_agent_cli.py`

- [ ] **Step 1: Write failing CLI tests**

Create `/Users/mattb/Dev/music-2025/tests/test_agent_cli.py`:

```python
import json

from typer.testing import CliRunner

from lastfm.cli import app


runner = CliRunner()


def test_agent_command_requires_session_or_csv():
    result = runner.invoke(app, ["listening-stats", "--json"])
    assert result.exit_code == 2
    assert "Provide exactly one of --session or --csv" in result.output


def test_listening_stats_one_shot_json(monkeypatch, sample_csv):
    import lastfm.analysis_state

    monkeypatch.setattr(lastfm.analysis_state.AnalysisState, "_build_user_embeddings", lambda self: None)
    monkeypatch.setattr(lastfm.analysis_state.AnalysisState, "_build_critics_embeddings", lambda self: None)
    monkeypatch.setattr(lastfm.analysis_state.AnalysisState, "_build_critic_vectors", lambda self: None)

    result = runner.invoke(app, ["listening-stats", "--csv", str(sample_csv), "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["command"] == "listening-stats"
    assert payload["session_id"] is None
    assert payload["result"]["total_plays"] == 4


def test_root_help_mentions_agent_workflow():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Agent workflow" in result.output
    assert "session-start" in result.output
    assert "listening-stats" in result.output
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
python3 -m pytest tests/test_agent_cli.py -v
```

Expected: FAIL because `listening-stats` is not registered.

- [ ] **Step 3: Implement agent command registration**

Create `/Users/mattb/Dev/music-2025/lastfm/commands_agent.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

import typer

from . import agent_tools
from .agent_output import error_envelope, print_json, success_envelope
from .analysis_state import AnalysisState


def _resolve_target(session: str | None, csv: Path | None) -> tuple[str | None, AnalysisState]:
    if bool(session) == bool(csv):
        raise typer.BadParameter("Provide exactly one of --session or --csv")
    if session:
        from .session_client import dispatch_to_session

        raise RuntimeError("session dispatch is added in the daemon task")
    state = AnalysisState()
    state.load(csv)
    return None, state


def _run_agent_command(command: str, session: str | None, csv: Path | None, params: dict[str, Any]) -> None:
    try:
        session_id, state = _resolve_target(session, csv)
        result = agent_tools.dispatch(state, command, params)
        print_json(success_envelope(command=command, result=result, session_id=session_id))
    except typer.BadParameter:
        raise
    except Exception as exc:
        print_json(error_envelope(
            command=command,
            code=type(exc).__name__.upper(),
            message=str(exc),
            retryable=False,
            session_id=session,
        ))
        raise typer.Exit(1)


def register(app: typer.Typer) -> None:
    @app.command("listening-stats", help="Agent command: return listening statistics as JSON.")
    def listening_stats(
        session: str | None = typer.Option(None, "--session", help="Named daemon session ID."),
        csv: Path | None = typer.Option(None, "--csv", help="Run one-shot against this scrobbles CSV."),
        year: int | None = typer.Option(None, "--year", help="Optional year filter."),
        json_output: bool = typer.Option(True, "--json", help="Emit structured JSON on stdout."),
    ):
        _run_agent_command("listening-stats", session, csv, {"year": year})

    @app.command("top-artists", help="Agent command: return top artists as JSON.")
    def top_artists(
        session: str | None = typer.Option(None, "--session", help="Named daemon session ID."),
        csv: Path | None = typer.Option(None, "--csv", help="Run one-shot against this scrobbles CSV."),
        year: int | None = typer.Option(None, "--year", help="Optional year filter."),
        limit: int = typer.Option(20, "--limit", help="Maximum artists to return."),
        json_output: bool = typer.Option(True, "--json", help="Emit structured JSON on stdout."),
    ):
        _run_agent_command("top-artists", session, csv, {"year": year, "limit": limit})
```

- [ ] **Step 4: Register command module and improve root help**

Modify `/Users/mattb/Dev/music-2025/lastfm/cli.py`:

```python
from . import commands_agent
```

After existing `app.add_typer(...)` calls, add:

```python
commands_agent.register(app)
```

Change the root Typer help text to include this exact paragraph:

```python
help=(
    "Analyze your Last.fm listening history.\n\n"
    "Agent workflow:\n"
    "  lastfm session-start --session-id music-2025 --csv recenttracks.csv --json\n"
    "  lastfm listening-stats --session music-2025 --json\n"
    "  lastfm blind-spots --session music-2025 --year 2025 --limit 20 --json\n"
    "  lastfm session-stop --session music-2025 --json\n"
)
```

- [ ] **Step 5: Run tests**

Run:

```bash
python3 -m pytest tests/test_agent_cli.py tests/test_agent_tools.py tests/test_analysis_state.py tests/test_agent_output.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add lastfm/commands_agent.py lastfm/cli.py tests/test_agent_cli.py
git commit -m "feat: add top-level one-shot agent commands"
```

## Task 6: Add Complete Top-Level Agent Command Surface

**Files:**
- Modify: `/Users/mattb/Dev/music-2025/lastfm/commands_agent.py`
- Modify: `/Users/mattb/Dev/music-2025/tests/test_agent_cli.py`

- [ ] **Step 1: Add registration coverage test**

Append to `/Users/mattb/Dev/music-2025/tests/test_agent_cli.py`:

```python
def test_all_agent_commands_are_registered_in_help():
    expected = [
        "taste-evolution",
        "musical-bridges",
        "blind-spots",
        "artist-deep-dive",
        "similar-artists",
        "critic-alignment",
        "temporal-patterns",
        "period-summary",
        "year-review",
        "listening-by-release-era",
        "common-transitions",
        "discovery-context",
        "critics-world",
        "album-acclaim",
        "validated-albums",
        "critic-profile",
        "search-critics-artist",
        "obsession-tracks",
        "one-track-artists",
        "ep-single-artists",
        "overview-summary",
        "discovered-artists",
        "critics-lists",
    ]
    output = runner.invoke(app, ["--help"]).output
    for command in expected:
        assert command in output
```

- [ ] **Step 2: Register remaining commands**

Extend `/Users/mattb/Dev/music-2025/lastfm/commands_agent.py` with explicit Typer commands for every name in `agent_tools.COMMANDS`. Each command must call `_run_agent_command` with a concrete params dict and must expose only the parameters required by its underlying function.

Use these parameter mappings:

```python
"taste-evolution": {"start_year": start_year, "end_year": end_year}
"musical-bridges": {"artist": artist, "top_n": top_n}
"blind-spots": {"year": year, "min_critics": min_critics, "limit": limit}
"artist-deep-dive": {"artists": artists}
"similar-artists": {"artist": artist, "source": source, "top_n": top_n}
"critic-alignment": {"limit": limit}
"temporal-patterns": {"year": year}
"period-summary": {"start_year": start_year, "end_year": end_year}
"year-review": {"years": years}
"listening-by-release-era": {"year": year, "limit": limit}
"common-transitions": {"artist": artist, "top_n": top_n}
"discovery-context": {"artist": artist}
"critics-world": {"year": year}
"album-acclaim": {"artist": artist, "album": album, "year": year}
"validated-albums": {"year": year, "limit": limit}
"critic-profile": {"critic_name": critic_name, "year": year}
"search-critics-artist": {"artist": artist, "year": year}
"obsession-tracks": {"year": year, "limit": limit}
"one-track-artists": {"year": year, "limit": limit}
"ep-single-artists": {"year": year}
"overview-summary": {}
"discovered-artists": {"year": year}
"critics-lists": {"year": year}
```

For `artist-deep-dive`, expose:

```python
artists: list[str] = typer.Option(..., "--artist", help="Artist name. Repeat for multiple artists.")
```

For `similar-artists`, expose source as:

```python
source: str = typer.Option("user", "--source", help="Similarity source: user or critics.")
```

For `year-review`, expose:

```python
years: list[int] = typer.Option([2025], "--year", help="Year to review. Repeat for multiple years.")
```

- [ ] **Step 3: Run tests**

Run:

```bash
python3 -m pytest tests/test_agent_cli.py -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add lastfm/commands_agent.py tests/test_agent_cli.py
git commit -m "feat: register full agent analysis command surface"
```

## Task 7: Add Session Daemon Lifecycle

**Files:**
- Create: `/Users/mattb/Dev/music-2025/lastfm/session_daemon.py`
- Create: `/Users/mattb/Dev/music-2025/lastfm/session_client.py`
- Modify: `/Users/mattb/Dev/music-2025/lastfm/commands_agent.py`
- Create: `/Users/mattb/Dev/music-2025/tests/test_session_client.py`

- [ ] **Step 1: Add session path and metadata tests**

Create `/Users/mattb/Dev/music-2025/tests/test_session_client.py`:

```python
from pathlib import Path

from lastfm.session_client import SessionPaths, session_paths


def test_session_paths_are_isolated_by_id(tmp_path, monkeypatch):
    monkeypatch.setenv("LASTFM_SESSION_ROOT", str(tmp_path))
    paths = session_paths("music-2025")
    assert paths.root == tmp_path / "music-2025"
    assert paths.socket == tmp_path / "music-2025" / "lastfm.sock"
    assert paths.pid == tmp_path / "music-2025" / "pid"
    assert paths.metadata == tmp_path / "music-2025" / "metadata.json"
```

- [ ] **Step 2: Implement session client primitives**

Create `/Users/mattb/Dev/music-2025/lastfm/session_client.py`:

```python
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SessionPaths:
    root: Path
    socket: Path
    pid: Path
    metadata: Path


def session_root() -> Path:
    return Path(os.environ.get("LASTFM_SESSION_ROOT", Path.home() / ".cache" / "lastfm-analysis" / "sessions"))


def session_paths(session_id: str) -> SessionPaths:
    root = session_root() / session_id
    return SessionPaths(
        root=root,
        socket=root / "lastfm.sock",
        pid=root / "pid",
        metadata=root / "metadata.json",
    )


def start_session(session_id: str, csv_path: Path, json_output: bool = True) -> subprocess.Popen:
    paths = session_paths(session_id)
    paths.root.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "lastfm.session_daemon",
        "--session-id",
        session_id,
        "--csv",
        str(csv_path),
    ]
    if json_output:
        cmd.append("--json")
    return subprocess.Popen(cmd)


def read_metadata(session_id: str) -> dict[str, Any]:
    path = session_paths(session_id).metadata
    if not path.exists():
        raise FileNotFoundError(f"No metadata found for session {session_id}")
    return json.loads(path.read_text())


def dispatch_to_session(session_id: str, command: str, params: dict[str, Any]) -> Any:
    paths = session_paths(session_id)
    if not paths.socket.exists():
        raise FileNotFoundError(f"No running session named {session_id}")
    request = json.dumps({"command": command, "params": params}).encode() + b"\n"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.connect(str(paths.socket))
        sock.sendall(request)
        chunks = []
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    response = json.loads(b"".join(chunks).decode())
    if not response.get("ok"):
        raise RuntimeError(response.get("error", {}).get("message", "Session command failed"))
    return response["result"]
```

- [ ] **Step 3: Implement daemon request loop**

Create `/Users/mattb/Dev/music-2025/lastfm/session_daemon.py`:

```python
from __future__ import annotations

import argparse
import json
import os
import socketserver
from pathlib import Path

from . import agent_tools
from .agent_output import emit_event, error_envelope, success_envelope
from .analysis_state import AnalysisState
from .session_client import session_paths


class AgentRequestHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        raw = self.rfile.readline().decode()
        request = json.loads(raw)
        command = request["command"]
        params = request.get("params", {})
        try:
            result = agent_tools.dispatch(self.server.state, command, params)
            payload = success_envelope(command=command, session_id=self.server.session_id, result=result)
        except Exception as exc:
            payload = error_envelope(
                command=command,
                session_id=self.server.session_id,
                code=type(exc).__name__.upper(),
                message=str(exc),
                retryable=False,
            )
        self.wfile.write(json.dumps(payload).encode())


class UnixAgentServer(socketserver.UnixStreamServer):
    def __init__(self, socket_path: str, handler, state: AnalysisState, session_id: str):
        self.state = state
        self.session_id = session_id
        super().__init__(socket_path, handler)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--csv", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    paths = session_paths(args.session_id)
    paths.root.mkdir(parents=True, exist_ok=True)
    if paths.socket.exists():
        paths.socket.unlink()

    if args.json:
        emit_event("start", session_id=args.session_id)
        emit_event("load_csv", session_id=args.session_id, path=str(Path(args.csv).resolve()))

    state = AnalysisState()
    state.load(Path(args.csv))

    paths.pid.write_text(str(os.getpid()))
    metadata = {
        "session_id": args.session_id,
        "pid": os.getpid(),
        "socket": str(paths.socket),
        **state.metadata(),
    }
    paths.metadata.write_text(json.dumps(metadata, indent=2, sort_keys=True))

    if args.json:
        emit_event("ready", session_id=args.session_id, socket=str(paths.socket))

    server = UnixAgentServer(str(paths.socket), AgentRequestHandler, state, args.session_id)
    try:
        server.serve_forever()
    finally:
        if paths.socket.exists():
            paths.socket.unlink()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Add lifecycle CLI commands and session dispatch**

Modify `/Users/mattb/Dev/music-2025/lastfm/commands_agent.py`:

```python
from .session_client import dispatch_to_session, read_metadata, session_paths, start_session
```

Update `_resolve_target`:

```python
def _resolve_target(session: str | None, csv: Path | None) -> tuple[str | None, AnalysisState | None]:
    if bool(session) == bool(csv):
        raise typer.BadParameter("Provide exactly one of --session or --csv")
    if session:
        return session, None
    state = AnalysisState()
    state.load(csv)
    return None, state
```

Update `_run_agent_command`:

```python
if session_id:
    result = dispatch_to_session(session_id, command, params)
else:
    result = agent_tools.dispatch(state, command, params)
```

Add these commands inside `register(app)`:

```python
@app.command("session-start", help="Start a named Last.fm analysis daemon session.")
def session_start(
    session_id: str = typer.Option(..., "--session-id", help="Unique session ID."),
    csv: Path = typer.Option(..., "--csv", help="Scrobbles CSV for this session."),
    json_output: bool = typer.Option(True, "--json", help="Emit NDJSON startup events."),
):
    process = start_session(session_id=session_id, csv_path=csv, json_output=json_output)
    if not json_output:
        typer.echo(f"Started session {session_id} with pid {process.pid}")


@app.command("session-status", help="Read metadata for a named daemon session.")
def session_status(
    session: str = typer.Option(..., "--session", help="Session ID."),
    json_output: bool = typer.Option(True, "--json", help="Emit structured JSON."),
):
    print_json(success_envelope("session-status", read_metadata(session), session_id=session))


@app.command("session-stop", help="Stop a named daemon session.")
def session_stop(
    session: str = typer.Option(..., "--session", help="Session ID."),
    json_output: bool = typer.Option(True, "--json", help="Emit structured JSON."),
):
    paths = session_paths(session)
    pid = int(paths.pid.read_text())
    os.kill(pid, 15)
    print_json(success_envelope("session-stop", {"stopped": True, "pid": pid}, session_id=session))
```

Import `os` at the top of `commands_agent.py`.

- [ ] **Step 5: Run tests**

Run:

```bash
python3 -m pytest tests/test_session_client.py tests/test_agent_cli.py tests/test_agent_tools.py tests/test_analysis_state.py tests/test_agent_output.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add lastfm/session_daemon.py lastfm/session_client.py lastfm/commands_agent.py tests/test_session_client.py
git commit -m "feat: add named daemon sessions for agent cli"
```

## Task 8: Finish Session Management Commands

**Files:**
- Modify: `/Users/mattb/Dev/music-2025/lastfm/session_client.py`
- Modify: `/Users/mattb/Dev/music-2025/lastfm/commands_agent.py`
- Modify: `/Users/mattb/Dev/music-2025/tests/test_session_client.py`

- [ ] **Step 1: Add tests for listing and stale cleanup**

Append to `/Users/mattb/Dev/music-2025/tests/test_session_client.py`:

```python
import json

from lastfm.session_client import list_sessions, remove_session_files


def test_list_sessions_reads_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("LASTFM_SESSION_ROOT", str(tmp_path))
    paths = session_paths("a")
    paths.root.mkdir(parents=True)
    paths.metadata.write_text(json.dumps({"session_id": "a", "pid": 123}))
    assert list_sessions() == [{"session_id": "a", "pid": 123}]


def test_remove_session_files_removes_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("LASTFM_SESSION_ROOT", str(tmp_path))
    paths = session_paths("a")
    paths.root.mkdir(parents=True)
    paths.metadata.write_text("{}")
    remove_session_files("a")
    assert not paths.root.exists()
```

- [ ] **Step 2: Implement list and cleanup helpers**

Add to `/Users/mattb/Dev/music-2025/lastfm/session_client.py`:

```python
import shutil


def list_sessions() -> list[dict[str, Any]]:
    root = session_root()
    if not root.exists():
        return []
    sessions = []
    for metadata_path in sorted(root.glob("*/metadata.json")):
        sessions.append(json.loads(metadata_path.read_text()))
    return sessions


def remove_session_files(session_id: str) -> None:
    paths = session_paths(session_id)
    if paths.root.exists():
        shutil.rmtree(paths.root)
```

- [ ] **Step 3: Add CLI commands**

Add imports in `/Users/mattb/Dev/music-2025/lastfm/commands_agent.py`:

```python
from .session_client import list_sessions, remove_session_files
```

Add commands inside `register(app)`:

```python
@app.command("session-list", help="List known Last.fm daemon sessions.")
def session_list(json_output: bool = typer.Option(True, "--json", help="Emit structured JSON.")):
    print_json(success_envelope("session-list", {"sessions": list_sessions()}, session_id=None))


@app.command("session-cleanup", help="Remove files for stopped or stale sessions.")
def session_cleanup(
    session: str | None = typer.Option(None, "--session", help="Clean one session ID."),
    json_output: bool = typer.Option(True, "--json", help="Emit structured JSON."),
):
    if session:
        remove_session_files(session)
        cleaned = [session]
    else:
        cleaned = []
        for item in list_sessions():
            remove_session_files(item["session_id"])
            cleaned.append(item["session_id"])
    print_json(success_envelope("session-cleanup", {"cleaned": cleaned}, session_id=session))
```

- [ ] **Step 4: Run tests**

Run:

```bash
python3 -m pytest tests/test_session_client.py tests/test_agent_cli.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lastfm/session_client.py lastfm/commands_agent.py tests/test_session_client.py
git commit -m "feat: add session listing and cleanup"
```

## Task 9: Add Agent-Friendly Help And Failure Documentation

**Files:**
- Modify: `/Users/mattb/Dev/music-2025/lastfm/cli.py`
- Modify: `/Users/mattb/Dev/music-2025/lastfm/commands_agent.py`
- Modify: `/Users/mattb/Dev/music-2025/tests/test_agent_cli.py`

- [ ] **Step 1: Add help assertions**

Append to `/Users/mattb/Dev/music-2025/tests/test_agent_cli.py`:

```python
def test_listening_stats_help_documents_output_contract():
    result = runner.invoke(app, ["listening-stats", "--help"])
    assert result.exit_code == 0
    assert "Output contract" in result.output
    assert "--session" in result.output
    assert "--csv" in result.output


def test_session_start_help_documents_lifecycle():
    result = runner.invoke(app, ["session-start", "--help"])
    assert result.exit_code == 0
    assert "NDJSON lifecycle events" in result.output
    assert "ready" in result.output
```

- [ ] **Step 2: Update help text**

For each command in `/Users/mattb/Dev/music-2025/lastfm/commands_agent.py`, use help strings that include:

```text
Prerequisites: use either --session for a running daemon or --csv for one-shot mode.
Output contract: --json writes a single JSON envelope to stdout; diagnostics go to stderr.
Failure behavior: non-zero exit with ok=false JSON envelope for runtime failures.
```

For `session-start`, use:

```text
Start a named daemon session.

Workflow: loads CSV, builds cached embeddings, writes metadata, then listens on a Unix socket.
Output contract: --json writes NDJSON lifecycle events including start, load_csv, and ready.
Failure behavior: startup failures exit non-zero before the ready event.
```

- [ ] **Step 3: Run help tests**

Run:

```bash
python3 -m pytest tests/test_agent_cli.py -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add lastfm/cli.py lastfm/commands_agent.py tests/test_agent_cli.py
git commit -m "docs: make agent cli help self-describing"
```

## Task 10: Remove MCP Server

**Files:**
- Delete: `/Users/mattb/Dev/music-2025/lastfm/mcp_server.py`
- Modify: `/Users/mattb/Dev/music-2025/AGENTS.md`
- Modify: `/Users/mattb/Dev/music-2025/pyproject.toml`
- Create: `/Users/mattb/Dev/music-2025/tests/test_no_mcp.py`

- [ ] **Step 1: Add guard test**

Create `/Users/mattb/Dev/music-2025/tests/test_no_mcp.py`:

```python
from pathlib import Path


def test_mcp_server_removed():
    assert not Path("lastfm/mcp_server.py").exists()
```

- [ ] **Step 2: Delete MCP server**

Remove `/Users/mattb/Dev/music-2025/lastfm/mcp_server.py`.

- [ ] **Step 3: Remove FastMCP dependency if unused**

In `/Users/mattb/Dev/music-2025/pyproject.toml`, remove:

```toml
"fastmcp>=2.14.2",
```

Then run:

```bash
rg -n "fastmcp|mcp_server|FastMCP|@mcp" /Users/mattb/Dev/music-2025
```

Expected: no matches outside historical git metadata.

- [ ] **Step 4: Update repository guide**

Modify `/Users/mattb/Dev/music-2025/AGENTS.md` so the MCP bullet is replaced with:

```markdown
- Agent-native CLI: use top-level `lastfm` commands such as `lastfm session-start`, `lastfm listening-stats`, and `lastfm blind-spots`.
- Long-lived agent sessions use daemon metadata and sockets under `~/.cache/lastfm-analysis/sessions/<session-id>/`.
```

- [ ] **Step 5: Run full test suite**

Run:

```bash
python3 -m pytest -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml AGENTS.md tests/test_no_mcp.py
git rm lastfm/mcp_server.py
git commit -m "refactor: remove mcp server in favor of agent cli"
```

## Task 11: Manual Acceptance Pass

**Files:**
- No source changes expected unless commands fail.

- [ ] **Step 1: Verify root help is sufficient for standalone agents**

Run:

```bash
lastfm --help
```

Expected: help includes Agent workflow examples, session lifecycle commands, and top-level analysis commands.

- [ ] **Step 2: Verify one-shot JSON command**

Run from `/Users/mattb/Dev/music-2025` with an existing scrobbles export:

```bash
lastfm listening-stats --csv recenttracks-*.csv --json
```

Expected: a single JSON object with `ok: true`, `command: listening-stats`, and `result.total_plays`.

- [ ] **Step 3: Verify daemon lifecycle**

Run:

```bash
lastfm session-start --session-id acceptance --csv recenttracks-*.csv --json
lastfm session-status --session acceptance --json
lastfm top-artists --session acceptance --limit 5 --json
lastfm session-stop --session acceptance --json
lastfm session-cleanup --session acceptance --json
```

Expected: startup emits a `ready` NDJSON event; status shows metadata; `top-artists` returns a success envelope; stop and cleanup return success envelopes.

- [ ] **Step 4: Verify no ambiguous stdout in JSON mode**

Run:

```bash
lastfm blind-spots --session acceptance --year 2025 --limit 5 --json > /tmp/lastfm-agent.json
python3 -m json.tool /tmp/lastfm-agent.json
```

Expected: `json.tool` succeeds. If diagnostics contaminate stdout, move those diagnostics to stderr.

- [ ] **Step 5: Commit acceptance fixes if needed**

If manual acceptance required fixes:

```bash
git add lastfm tests AGENTS.md pyproject.toml
git commit -m "fix: complete agent cli acceptance pass"
```

## Self-Review

- Spec coverage: top-level CLI commands, daemon sessions with session IDs and multiple concurrent sessions, MCP removal, and the LLM-friendly CLI contract are each covered by tasks.
- Placeholder scan: this plan contains no `TBD`, `TODO`, or intentionally vague implementation steps.
- Type consistency: session IDs are strings throughout; analysis commands dispatch by hyphenated command name; JSON envelopes use `ok`, `command`, `session_id`, and `result` or `error` consistently.
