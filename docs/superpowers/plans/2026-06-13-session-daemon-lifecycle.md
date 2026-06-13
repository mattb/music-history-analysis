# Session Daemon Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make named Last.fm daemons exit after 30 idle minutes and make analysis commands transparently restart expired daemons from persisted metadata.

**Architecture:** The daemon owns a monotonic, active-request-aware idle tracker and a watchdog that performs orderly shutdown. The client owns a single recovery boundary around socket dispatch: on one transport failure it serializes restart with an advisory lock, validates the persisted CSV path, silently waits for a replacement daemon to become ready, and retries once. Status commands derive liveness without starting processes.

**Tech Stack:** Python 3.11+, `socketserver`, Unix domain sockets, `threading`, `fcntl.flock`, Typer, pytest, Ruff.

---

## File Structure

- Modify `lastfm/session_daemon.py`: idle tracker, request activity accounting, watchdog shutdown, runtime-file cleanup.
- Modify `lastfm/session_client.py`: restart-lock path, silent ready-waiting startup, persisted CSV validation, one-retry transport recovery, derived session status.
- Modify `lastfm/commands_agent.py`: use passive status payload and document transparent wake-up in analysis help.
- Modify `tests/test_session_daemon.py`: focused unit and integration tests for idle expiration and cleanup. Create this file because daemon lifecycle currently has no focused test module.
- Modify `tests/test_session_client.py`: recovery, locking, metadata validation, retry limits, and passive liveness tests.
- Modify `tests/test_agent_cli.py`: CLI contract tests for passive status/list and unchanged single-envelope analysis output.
- Modify `AGENTS.md`: repository-level daemon persistence contract.
- Modify `skills/lastfm-cli-journalism/SKILL.md`: operator guidance for persisted-but-sleeping sessions.

### Task 1: Active-request-aware daemon idle expiration

**Files:**
- Create: `tests/test_session_daemon.py`
- Modify: `lastfm/session_daemon.py`

- [ ] **Step 1: Write failing tracker tests**

Create `tests/test_session_daemon.py` with deterministic clock coverage:

```python
from lastfm.session_daemon import DEFAULT_IDLE_TIMEOUT_SECONDS, IdleTracker


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_idle_tracker_expires_after_30_minutes_without_work():
    clock = FakeClock()
    tracker = IdleTracker(clock=clock)

    clock.now = DEFAULT_IDLE_TIMEOUT_SECONDS - 1
    assert not tracker.is_expired()
    clock.now = DEFAULT_IDLE_TIMEOUT_SECONDS
    assert tracker.is_expired()


def test_idle_tracker_never_expires_during_active_request():
    clock = FakeClock()
    tracker = IdleTracker(timeout_seconds=10, clock=clock)

    tracker.request_started()
    clock.now = 100
    assert not tracker.is_expired()

    tracker.request_finished()
    assert not tracker.is_expired()
    clock.now = 110
    assert tracker.is_expired()


def test_failed_request_completion_resets_idle_clock():
    clock = FakeClock()
    tracker = IdleTracker(timeout_seconds=10, clock=clock)

    clock.now = 9
    tracker.request_started()
    clock.now = 20
    tracker.request_finished()
    clock.now = 29
    assert not tracker.is_expired()
    clock.now = 30
    assert tracker.is_expired()
```

- [ ] **Step 2: Run the tracker tests and verify RED**

Run:

```bash
uv run --extra dev python -m pytest tests/test_session_daemon.py -q
```

Expected: collection fails because `DEFAULT_IDLE_TIMEOUT_SECONDS` and `IdleTracker` do not exist.

- [ ] **Step 3: Implement the minimal idle tracker**

In `lastfm/session_daemon.py`, import `time`, `Callable`, and add:

```python
DEFAULT_IDLE_TIMEOUT_SECONDS = 30 * 60


class IdleTracker:
    def __init__(
        self,
        timeout_seconds: float = DEFAULT_IDLE_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self._clock = clock
        self._last_activity = clock()
        self._active_requests = 0
        self._lock = threading.Lock()

    def request_started(self) -> None:
        with self._lock:
            self._active_requests += 1

    def request_finished(self) -> None:
        with self._lock:
            self._active_requests -= 1
            self._last_activity = self._clock()

    def is_expired(self) -> bool:
        with self._lock:
            return (
                self._active_requests == 0
                and self._clock() - self._last_activity >= self.timeout_seconds
            )
```

Update `UnixAgentServer.__init__` to accept `idle_timeout_seconds` and `clock`, and assign `self.idle_tracker = IdleTracker(...)`. Wrap the full body of `AgentRequestHandler.handle`:

```python
self.server.idle_tracker.request_started()
try:
    # Existing decode, dispatch, envelope, and write behavior.
finally:
    self.server.idle_tracker.request_finished()
```

The `finally` must include malformed requests and failed commands so every accepted request releases the active count.

- [ ] **Step 4: Run the tracker tests and verify GREEN**

Run the Task 1 pytest command again. Expected: 3 passed.

- [ ] **Step 5: Commit the tracker**

```bash
git add tests/test_session_daemon.py lastfm/session_daemon.py
git commit -m "feat: track session daemon idle activity"
```

### Task 2: Watchdog shutdown and runtime-file cleanup

**Files:**
- Modify: `tests/test_session_daemon.py`
- Modify: `lastfm/session_daemon.py`

- [ ] **Step 1: Write failing watchdog and cleanup tests**

Add tests that use a real Unix server with a very short injected timeout, plus a pure cleanup ownership test:

```python
import os
import threading
import time

from lastfm.analysis_state import AnalysisState
from lastfm.session_client import session_paths
from lastfm.session_daemon import AgentRequestHandler, UnixAgentServer, remove_owned_runtime_files


def test_watchdog_shuts_down_idle_server(tmp_path, monkeypatch):
    monkeypatch.setenv("LASTFM_SESSION_ROOT", str(tmp_path))
    paths = session_paths("idle")
    paths.root.mkdir(parents=True)
    server = UnixAgentServer(
        str(paths.socket),
        AgentRequestHandler,
        AnalysisState(),
        "idle",
        idle_timeout_seconds=0.05,
    )
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    watchdog = server.start_idle_watchdog(check_interval_seconds=0.01)

    thread.join(timeout=1)
    watchdog.join(timeout=1)
    server.server_close()

    assert not thread.is_alive()


def test_runtime_cleanup_removes_only_current_process_pid(tmp_path, monkeypatch):
    monkeypatch.setenv("LASTFM_SESSION_ROOT", str(tmp_path))
    paths = session_paths("idle")
    paths.root.mkdir(parents=True)
    paths.socket.touch()
    paths.pid.write_text(str(os.getpid()))
    paths.metadata.write_text('{"session_id":"idle"}')

    remove_owned_runtime_files(paths, os.getpid())

    assert not paths.socket.exists()
    assert not paths.pid.exists()
    assert paths.metadata.exists()
```

Also add a test that writes a different PID and asserts `remove_owned_runtime_files` leaves that PID file untouched.

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
uv run --extra dev python -m pytest tests/test_session_daemon.py -q
```

Expected: failures because watchdog and cleanup helpers do not exist.

- [ ] **Step 3: Implement orderly idle shutdown and owned cleanup**

Add to `UnixAgentServer`:

```python
def start_idle_watchdog(self, check_interval_seconds: float = 1.0) -> threading.Thread:
    def watch() -> None:
        while True:
            time.sleep(check_interval_seconds)
            if self.idle_tracker.is_expired():
                self.shutdown()
                return

    thread = threading.Thread(target=watch, daemon=True)
    thread.start()
    return thread
```

Add:

```python
def remove_owned_runtime_files(paths, pid: int) -> None:
    if paths.socket.exists():
        paths.socket.unlink()
    try:
        recorded_pid = int(paths.pid.read_text())
    except (FileNotFoundError, ValueError):
        return
    if recorded_pid == pid:
        paths.pid.unlink()
```

In `main`, start the watchdog immediately before `serve_forever`. In the existing `finally`, call `server.server_close()` and `remove_owned_runtime_files(paths, os.getpid())`; do not remove metadata.

- [ ] **Step 4: Run daemon tests and existing socket parity tests**

Run:

```bash
uv run --extra dev python -m pytest tests/test_session_daemon.py tests/test_agent_cli.py -q
```

Expected: all pass. Existing tests that instantiate `UnixAgentServer` must continue to work through default constructor arguments.

- [ ] **Step 5: Commit daemon lifecycle behavior**

```bash
git add tests/test_session_daemon.py lastfm/session_daemon.py
git commit -m "feat: expire idle session daemons"
```

### Task 3: Silent client restart and one transport retry

**Files:**
- Modify: `tests/test_session_client.py`
- Modify: `lastfm/session_client.py`

- [ ] **Step 1: Write failing persistence and recovery tests**

Extend `SessionPaths` expectations with `restart_lock == root / "restart.lock"`. Add tests using temporary metadata and monkeypatched `_dispatch_once` / `_start_session_until_ready` seams:

```python
def test_dispatch_restarts_missing_daemon_from_persisted_csv(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LASTFM_SESSION_ROOT", str(tmp_path))
    csv = tmp_path / "history.csv"
    csv.write_text("uts,artist,album,track\n")
    paths = session_paths("music")
    paths.root.mkdir(parents=True)
    paths.metadata.write_text(json.dumps({"session_id": "music", "csv_path": str(csv.resolve())}))
    attempts = []
    starts = []

    def fake_dispatch_once(*_args):
        attempts.append(1)
        if len(attempts) == 1:
            raise FileNotFoundError("missing socket")
        return {"plays": 1}

    monkeypatch.setattr(session_client, "_dispatch_once", fake_dispatch_once)
    monkeypatch.setattr(session_client, "socket_is_connectable", lambda _path: False)
    monkeypatch.setattr(
        session_client,
        "_start_session_until_ready",
        lambda session_id, csv_path, event_stream=None: starts.append((session_id, csv_path)),
    )

    result = session_client.dispatch_to_session("music", "listening-stats", {})

    assert result == {"plays": 1}
    assert starts == [("music", csv.resolve())]
    assert len(attempts) == 2
    assert capsys.readouterr().out == ""


def test_remote_command_error_is_not_restarted(monkeypatch):
    error = session_client.RemoteAgentError("INVALID", "bad command", False)
    monkeypatch.setattr(session_client, "_dispatch_once", lambda *_args: (_ for _ in ()).throw(error))
    monkeypatch.setattr(
        session_client,
        "restart_session",
        lambda _session_id: (_ for _ in ()).throw(AssertionError("must not restart")),
    )

    with pytest.raises(session_client.RemoteAgentError):
        session_client.dispatch_to_session("music", "bad", {})


def test_dispatch_retries_transport_failure_only_once(monkeypatch):
    monkeypatch.setattr(
        session_client,
        "_dispatch_once",
        lambda *_args: (_ for _ in ()).throw(ConnectionRefusedError()),
    )
    restarts = []
    monkeypatch.setattr(session_client, "restart_session", lambda session_id: restarts.append(session_id))

    with pytest.raises(ConnectionRefusedError):
        session_client.dispatch_to_session("music", "listening-stats", {})

    assert restarts == ["music"]
```

Add parameterized tests for corrupt metadata, relative `csv_path`, missing `csv_path`, and nonexistent CSV. Each must assert no process spawn.

- [ ] **Step 2: Run recovery tests and verify RED**

Run:

```bash
uv run --extra dev python -m pytest tests/test_session_client.py -q
```

Expected: failures for missing restart path and recovery helpers.

- [ ] **Step 3: Refactor startup into a silent ready-waiting seam**

Extract the current JSON startup loop into:

```python
def _start_session_until_ready(
    session_id: str,
    csv_path: Path,
    event_stream: TextIO | None = None,
) -> subprocess.Popen:
    # Spawn daemon with --json, consume lines until ready, and copy each line
    # only when event_stream is not None. Preserve existing early-exit errors.
```

Keep `start_session(..., json_output=True)` behavior identical by passing `sys.stdout`. Preserve the existing detached, immediate `json_output=False` branch for explicit callers. Transparent restart calls `_start_session_until_ready(..., event_stream=None)` so an analysis command still prints only its final JSON envelope.

- [ ] **Step 4: Implement validated, serialized restart**

Add `restart_lock: Path` to `SessionPaths`. Import `fcntl` and add:

```python
def persisted_session_csv(session_id: str) -> Path:
    metadata = read_metadata(session_id)
    raw_path = metadata.get("csv_path")
    if not isinstance(raw_path, str) or not raw_path:
        raise RuntimeError(f"Session {session_id} metadata has no csv_path")
    csv_path = Path(raw_path)
    if not csv_path.is_absolute():
        raise RuntimeError(f"Session {session_id} metadata csv_path is not absolute")
    if not csv_path.is_file():
        raise FileNotFoundError(f"Session {session_id} CSV no longer exists: {csv_path}")
    return csv_path


def restart_session(session_id: str) -> None:
    paths = session_paths(session_id)
    paths.root.mkdir(parents=True, exist_ok=True)
    with paths.restart_lock.open("a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        if socket_is_connectable(paths.socket):
            return
        csv_path = persisted_session_csv(session_id)
        _start_session_until_ready(session_id, csv_path, event_stream=None)
```

Extract the original socket exchange unchanged into `_dispatch_once`. Make `dispatch_to_session` catch only transport-layer exceptions (`FileNotFoundError`, `ConnectionError`, `socket.timeout`, `json.JSONDecodeError`, `UnicodeDecodeError`), call `restart_session`, then call `_dispatch_once` one final time. Do not catch `RemoteAgentError` or errors from `restart_session`.

- [ ] **Step 5: Add concurrent restart test**

```python
def test_concurrent_restart_spawns_one_daemon(tmp_path, monkeypatch):
    monkeypatch.setenv("LASTFM_SESSION_ROOT", str(tmp_path))
    csv = tmp_path / "history.csv"
    csv.write_text("uts,artist,album,track\n")
    paths = session_paths("music")
    paths.root.mkdir(parents=True)
    paths.metadata.write_text(json.dumps({"session_id": "music", "csv_path": str(csv.resolve())}))
    barrier = threading.Barrier(2)
    state = {"live": False, "starts": 0}

    monkeypatch.setattr(session_client, "socket_is_connectable", lambda _path: state["live"])

    def fake_start(*_args, **_kwargs):
        state["starts"] += 1
        time.sleep(0.05)
        state["live"] = True

    monkeypatch.setattr(session_client, "_start_session_until_ready", fake_start)

    def restart():
        barrier.wait()
        session_client.restart_session("music")

    threads = [threading.Thread(target=restart) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=1)

    assert all(not thread.is_alive() for thread in threads)
    assert state["starts"] == 1
```

- [ ] **Step 6: Run client tests and verify GREEN**

Run the Task 3 pytest command again. Expected: all pass.

- [ ] **Step 7: Commit client recovery**

```bash
git add tests/test_session_client.py lastfm/session_client.py
git commit -m "feat: restart expired sessions on demand"
```

### Task 4: Passive status and list liveness

**Files:**
- Modify: `tests/test_session_client.py`
- Modify: `tests/test_agent_cli.py`
- Modify: `lastfm/session_client.py`
- Modify: `lastfm/commands_agent.py`

- [ ] **Step 1: Write failing status tests**

Add client tests:

```python
def test_read_session_status_reports_persisted_session_as_not_running(tmp_path, monkeypatch):
    monkeypatch.setenv("LASTFM_SESSION_ROOT", str(tmp_path))
    paths = session_paths("sleeping")
    paths.root.mkdir(parents=True)
    paths.metadata.write_text(json.dumps({"session_id": "sleeping", "csv_path": "/tmp/history.csv"}))
    monkeypatch.setattr(session_client, "session_is_live", lambda _session_id: False)

    assert session_client.read_session_status("sleeping")["running"] is False


def test_list_sessions_adds_liveness_without_starting(tmp_path, monkeypatch):
    monkeypatch.setenv("LASTFM_SESSION_ROOT", str(tmp_path))
    paths = session_paths("sleeping")
    paths.root.mkdir(parents=True)
    paths.metadata.write_text(json.dumps({"session_id": "sleeping"}))
    monkeypatch.setattr(session_client, "session_is_live", lambda _session_id: False)

    assert list_sessions() == [{"session_id": "sleeping", "running": False}]
```

Add CLI tests that monkeypatch `start_session` to raise if called, invoke `session-status` and `session-list`, and assert `running` is false.

- [ ] **Step 2: Run status tests and verify RED**

Run:

```bash
uv run --extra dev python -m pytest tests/test_session_client.py tests/test_agent_cli.py -q
```

Expected: failures because passive status does not yet derive `running`.

- [ ] **Step 3: Implement passive status**

Add:

```python
def read_session_status(session_id: str) -> dict[str, Any]:
    return {**read_metadata(session_id), "running": session_is_live(session_id)}
```

In `list_sessions`, merge `running: session_is_live(session_id)` into each valid metadata record. Leave corrupt metadata records as errors with `running: false`. Update `commands_agent.session_status` to call `read_session_status`; do not call recovery or startup helpers.

Update `AGENT_ANALYSIS_HELP_SUFFIX` to state: “Expired named sessions restart automatically from persisted metadata.”

- [ ] **Step 4: Run status and CLI tests and verify GREEN**

Run the Task 4 pytest command again. Expected: all pass.

- [ ] **Step 5: Commit passive management behavior**

```bash
git add tests/test_session_client.py tests/test_agent_cli.py lastfm/session_client.py lastfm/commands_agent.py
git commit -m "feat: report persisted session liveness"
```

### Task 5: Operator documentation and complete verification

**Files:**
- Modify: `AGENTS.md`
- Modify: `skills/lastfm-cli-journalism/SKILL.md`

- [ ] **Step 1: Document lifecycle semantics separately from analysis interpretation**

Add to the session section of `AGENTS.md`:

```markdown
- Named session metadata persists after its daemon exits. Daemons exit after 30 minutes without an active request.
- Analysis commands using `--session` transparently restart an expired daemon from the persisted absolute CSV path and retry once.
- `session-list` and `session-status` are passive and report `running`; they never wake a daemon.
```

Add an “Session Persistence” subsection to `skills/lastfm-cli-journalism/SKILL.md` with the same operational contract. Explicitly say that missing or moved CSV sources require a new `session-start`, and that analysis narratives must not interpret a daemon restart as a gap in listening data.

- [ ] **Step 2: Validate documentation wording**

Run:

```bash
rg -n "30 minutes|restart|running|moved CSV" AGENTS.md skills/lastfm-cli-journalism/SKILL.md
git diff --check -- AGENTS.md skills/lastfm-cli-journalism/SKILL.md
```

Expected: all four lifecycle concepts appear and diff check is clean.

- [ ] **Step 3: Run focused tests**

```bash
uv run --extra dev python -m pytest tests/test_session_daemon.py tests/test_session_client.py tests/test_agent_cli.py -q
```

Expected: all focused tests pass.

- [ ] **Step 4: Run full tests**

```bash
uv run --extra dev python -m pytest -q
```

Expected: full suite passes.

- [ ] **Step 5: Run Ruff on touched Python files**

```bash
uv run --extra dev ruff check lastfm/session_daemon.py lastfm/session_client.py lastfm/commands_agent.py tests/test_session_daemon.py tests/test_session_client.py tests/test_agent_cli.py
uv run --extra dev ruff format --check lastfm/session_daemon.py lastfm/session_client.py lastfm/commands_agent.py tests/test_session_daemon.py tests/test_session_client.py tests/test_agent_cli.py
```

Expected: both commands exit zero.

- [ ] **Step 6: Re-run the end-to-end expiry and restart cases verbosely**

```bash
uv run --extra dev python -m pytest \
  tests/test_session_daemon.py::test_watchdog_shuts_down_idle_server \
  tests/test_session_client.py::test_dispatch_restarts_missing_daemon_from_persisted_csv \
  -vv
```

Expected: both pass. These tests use temporary session roots and never alter the user.s existing session cache.

- [ ] **Step 7: Commit documentation and verification-ready state**

```bash
git add AGENTS.md skills/lastfm-cli-journalism/SKILL.md
git commit -m "docs: explain persistent session lifecycle"
```
