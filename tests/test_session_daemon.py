import io
import json
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from lastfm.session_daemon import (
    DEFAULT_IDLE_TIMEOUT_SECONDS,
    AgentRequestHandler,
    IdleTracker,
    UnixAgentServer,
)


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
