import io
import json
import shutil
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
import lastfm.session_daemon as session_daemon

from lastfm.session_daemon import (
    DEFAULT_IDLE_TIMEOUT_SECONDS,
    AgentRequestHandler,
    IdleTracker,
    UnixAgentServer,
)
from lastfm.session_client import SessionPaths


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class RecordingIdleTracker(IdleTracker):
    def __init__(self, *, clock):
        super().__init__(clock=clock)
        self.events = []

    def request_started(self):
        self.events.append("started")
        super().request_started()

    def request_finished(self):
        self.events.append("finished")
        super().request_finished()


@pytest.fixture
def socket_path():
    root = Path(tempfile.mkdtemp(prefix="lf-daemon-", dir="/tmp"))
    yield root / "session.sock"
    shutil.rmtree(root)


def make_handler(raw, idle_tracker):
    handler = AgentRequestHandler.__new__(AgentRequestHandler)
    handler.rfile = io.BytesIO(raw)
    handler.wfile = io.BytesIO()
    handler.server = SimpleNamespace(
        state=object(), session_id="test-session", idle_tracker=idle_tracker
    )
    return handler


def test_idle_tracker_expires_exactly_at_default_timeout():
    clock = FakeClock()
    tracker = IdleTracker(clock=clock)

    clock.advance(DEFAULT_IDLE_TIMEOUT_SECONDS - 1)
    assert not tracker.is_expired()

    clock.advance(1)
    assert tracker.is_expired()
    assert DEFAULT_IDLE_TIMEOUT_SECONDS == 30 * 60


def test_idle_tracker_does_not_expire_during_active_request():
    clock = FakeClock()
    tracker = IdleTracker(clock=clock)

    tracker.request_started()
    clock.advance(DEFAULT_IDLE_TIMEOUT_SECONDS + 1)

    assert not tracker.is_expired()


def test_request_completion_restarts_idle_timeout():
    clock = FakeClock()
    tracker = IdleTracker(clock=clock)
    tracker.request_started()
    clock.advance(DEFAULT_IDLE_TIMEOUT_SECONDS + 1)

    tracker.request_finished()
    clock.advance(DEFAULT_IDLE_TIMEOUT_SECONDS - 1)
    assert not tracker.is_expired()

    clock.advance(1)
    assert tracker.is_expired()


def test_failed_request_completion_also_restarts_idle_timeout():
    clock = FakeClock()
    tracker = IdleTracker(clock=clock)
    tracker.request_started()
    clock.advance(DEFAULT_IDLE_TIMEOUT_SECONDS + 1)

    try:
        raise RuntimeError("request failed")
    except RuntimeError:
        tracker.request_finished()

    assert not tracker.is_expired()


def test_handler_finishes_activity_after_malformed_request():
    clock = FakeClock()
    tracker = RecordingIdleTracker(clock=clock)
    handler = make_handler(b"not-json\n", tracker)

    with pytest.raises(json.JSONDecodeError):
        handler.handle()

    assert tracker.events == ["started", "finished"]


def test_handler_finishes_activity_after_failed_dispatch(monkeypatch):
    clock = FakeClock()
    tracker = RecordingIdleTracker(clock=clock)
    handler = make_handler(b'{"command": "broken"}\n', tracker)
    monkeypatch.setattr(
        "lastfm.session_daemon.agent_tools.dispatch",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    handler.handle()

    assert tracker.events == ["started", "finished"]
    assert json.loads(handler.wfile.getvalue())["error"]["message"] == "boom"


def test_unix_agent_server_accepts_idle_tracker_options(socket_path):
    clock = FakeClock()
    server = UnixAgentServer(
        str(socket_path),
        AgentRequestHandler,
        object(),
        "test-session",
        idle_timeout_seconds=5,
        clock=clock,
    )
    try:
        clock.advance(5)
        assert server.idle_tracker.is_expired()
    finally:
        server.server_close()


def test_unix_agent_server_preserves_original_constructor(socket_path):
    server = UnixAgentServer(
        str(socket_path),
        AgentRequestHandler,
        object(),
        "test-session",
    )
    try:
        assert isinstance(server.idle_tracker, IdleTracker)
    finally:
        server.server_close()


def test_idle_watchdog_shuts_down_server_and_exits(socket_path):
    server = UnixAgentServer(
        str(socket_path),
        AgentRequestHandler,
        object(),
        "test-session",
        idle_timeout_seconds=0.05,
    )
    server_thread = threading.Thread(target=server.serve_forever)
    server_thread.start()
    watchdog_thread = server.start_idle_watchdog(check_interval_seconds=0.01)

    server_thread.join(timeout=1)
    watchdog_thread.join(timeout=1)

    try:
        assert not server_thread.is_alive()
        assert not watchdog_thread.is_alive()
    finally:
        server.shutdown()
        server.server_close()


def test_remove_owned_runtime_files_removes_socket_and_owned_pid(tmp_path):
    paths = SessionPaths(
        root=tmp_path,
        socket=tmp_path / "lastfm.sock",
        pid=tmp_path / "pid",
        metadata=tmp_path / "metadata.json",
        restart_lock=tmp_path.parent / ".locks" / "test-session.lock",
    )
    paths.socket.write_text("socket")
    paths.pid.write_text("123")
    paths.metadata.write_text('{"session_id": "test-session"}')

    session_daemon.remove_owned_runtime_files(paths, 123)

    assert not paths.socket.exists()
    assert not paths.pid.exists()
    assert paths.metadata.exists()


def test_remove_owned_runtime_files_preserves_different_pid(tmp_path):
    paths = SessionPaths(
        root=tmp_path,
        socket=tmp_path / "lastfm.sock",
        pid=tmp_path / "pid",
        metadata=tmp_path / "metadata.json",
        restart_lock=tmp_path.parent / ".locks" / "test-session.lock",
    )
    paths.socket.write_text("socket")
    paths.pid.write_text("456")
    paths.metadata.write_text('{"session_id": "test-session"}')

    session_daemon.remove_owned_runtime_files(paths, 123)

    assert paths.socket.exists()
    assert paths.pid.read_text() == "456"
    assert paths.metadata.exists()


@pytest.mark.parametrize("pid_contents", [None, "not-a-pid"])
def test_remove_owned_runtime_files_preserves_socket_without_owned_pid(
    tmp_path, pid_contents
):
    paths = SessionPaths(
        root=tmp_path,
        socket=tmp_path / "lastfm.sock",
        pid=tmp_path / "pid",
        metadata=tmp_path / "metadata.json",
        restart_lock=tmp_path.parent / ".locks" / "test-session.lock",
    )
    paths.socket.write_text("socket")
    if pid_contents is not None:
        paths.pid.write_text(pid_contents)

    session_daemon.remove_owned_runtime_files(paths, 123)

    assert paths.socket.exists()
    if pid_contents is not None:
        assert paths.pid.read_text() == pid_contents


def test_main_removes_runtime_paths_before_closing_server(tmp_path, monkeypatch):
    paths = SessionPaths(
        root=tmp_path,
        socket=tmp_path / "lastfm.sock",
        pid=tmp_path / "pid",
        metadata=tmp_path / "metadata.json",
        restart_lock=tmp_path.parent / ".locks" / "test-session.lock",
    )
    events = []
    remove_runtime_files = session_daemon.remove_owned_runtime_files

    class FakeState:
        def load(self, _path):
            pass

        def metadata(self):
            return {}

    class FakeServer:
        def __init__(self, socket_path, *_args):
            Path(socket_path).touch()

        def start_idle_watchdog(self):
            pass

        def serve_forever(self):
            pass

        def server_close(self):
            events.append("close")

        def shutdown(self):
            pass

    def record_cleanup(cleanup_paths, pid):
        events.append("cleanup")
        remove_runtime_files(cleanup_paths, pid)

    monkeypatch.setattr(
        "sys.argv", ["session-daemon", "--session-id", "test", "--csv", "input.csv"]
    )
    monkeypatch.setattr(session_daemon, "session_paths", lambda _session_id: paths)
    monkeypatch.setattr(session_daemon, "socket_is_connectable", lambda _path: False)
    monkeypatch.setattr(session_daemon, "AnalysisState", FakeState)
    monkeypatch.setattr(session_daemon, "UnixAgentServer", FakeServer)
    monkeypatch.setattr(session_daemon, "remove_owned_runtime_files", record_cleanup)
    monkeypatch.setattr(session_daemon.signal, "signal", lambda *_args: None)

    session_daemon.main()

    assert events == ["cleanup", "close"]


def test_remove_owned_runtime_files_unlinks_pid_before_socket():
    events = []

    class RecordingPath:
        def __init__(self, name, contents=None):
            self.name = name
            self.contents = contents

        def read_text(self):
            return self.contents

        def unlink(self):
            events.append(self.name)

    paths = SimpleNamespace(
        pid=RecordingPath("pid", "123"),
        socket=RecordingPath("socket"),
    )

    session_daemon.remove_owned_runtime_files(paths, 123)

    assert events == ["pid", "socket"]
