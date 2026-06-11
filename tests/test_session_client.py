from pathlib import Path

import lastfm.session_client as session_client
from lastfm.session_client import (
    SessionPaths,
    session_paths,
    session_process_matches,
    start_session,
    stop_session,
)


def test_session_paths_are_isolated_by_id(tmp_path, monkeypatch):
    monkeypatch.setenv("LASTFM_SESSION_ROOT", str(tmp_path))
    paths = session_paths("music-2025")
    assert paths.root == tmp_path / "music-2025"
    assert paths.socket == tmp_path / "music-2025" / "lastfm.sock"
    assert paths.pid == tmp_path / "music-2025" / "pid"
    assert paths.metadata == tmp_path / "music-2025" / "metadata.json"


def test_start_session_json_forwards_startup_events_until_ready(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LASTFM_SESSION_ROOT", str(tmp_path))
    popen_kwargs = {}

    class FakeStdout:
        def __init__(self):
            self.lines = iter([
                '{"event":"start","session_id":"music-2025"}\n',
                '{"event":"ready","session_id":"music-2025"}\n',
                '{"event":"late","session_id":"music-2025"}\n',
            ])
            self.closed = False

        def readline(self):
            return next(self.lines, "")

        def close(self):
            self.closed = True

    class FakeProcess:
        def __init__(self):
            self.pid = 12345
            self.stdout = FakeStdout()
            self.returncode = None

        def poll(self):
            return self.returncode

    fake_process = FakeProcess()

    def fake_popen(_cmd, **kwargs):
        popen_kwargs.update(kwargs)
        return fake_process

    monkeypatch.setattr(session_client.subprocess, "Popen", fake_popen)

    process = start_session("music-2025", tmp_path / "recenttracks-test.csv", json_output=True)

    assert process is fake_process
    assert capsys.readouterr().out.splitlines() == [
        '{"event":"start","session_id":"music-2025"}',
        '{"event":"ready","session_id":"music-2025"}',
    ]
    assert fake_process.stdout.closed is True
    assert popen_kwargs["stdout"] == session_client.subprocess.PIPE
    assert popen_kwargs["stderr"] == session_client.subprocess.DEVNULL
    assert popen_kwargs["text"] is True


def test_start_session_refuses_existing_reachable_session(tmp_path, monkeypatch):
    monkeypatch.setenv("LASTFM_SESSION_ROOT", str(tmp_path))
    monkeypatch.setattr(session_client, "socket_is_connectable", lambda _path: True)

    def fail_popen(*_args, **_kwargs):
        raise AssertionError("duplicate session must not spawn a new daemon")

    monkeypatch.setattr(session_client.subprocess, "Popen", fail_popen)

    try:
        start_session("music-2025", tmp_path / "recenttracks-test.csv", json_output=True)
    except RuntimeError as exc:
        assert str(exc) == "Session music-2025 is already running"
    else:
        raise AssertionError("expected duplicate session failure")


def test_session_process_matches_requires_daemon_session_id():
    assert session_process_matches(
        "/path/python -m lastfm.session_daemon --session-id music-2025 --csv recenttracks.csv",
        "music-2025",
    )
    assert not session_process_matches(
        "/bin/sleep 1000 --session-id music-2025",
        "music-2025",
    )
    assert not session_process_matches(
        "/path/python -m lastfm.session_daemon --session-id other --csv recenttracks.csv",
        "music-2025",
    )


def test_stop_session_refuses_unverified_pid(tmp_path, monkeypatch):
    monkeypatch.setenv("LASTFM_SESSION_ROOT", str(tmp_path))
    paths = session_paths("music-2025")
    paths.root.mkdir(parents=True)
    paths.pid.write_text("12345")
    monkeypatch.setattr(session_client, "session_process_command", lambda _pid: "/bin/sleep 1000")

    def fail_kill(*_args, **_kwargs):
        raise AssertionError("unverified process must not be killed")

    monkeypatch.setattr(session_client.os, "kill", fail_kill)

    try:
        stop_session("music-2025")
    except RuntimeError as exc:
        assert str(exc) == "Refusing to stop unverified session process 12345"
    else:
        raise AssertionError("expected stale pid failure")
