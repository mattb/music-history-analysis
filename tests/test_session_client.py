import json
import socket
import threading
import time
from pathlib import Path

import pytest

import lastfm.session_client as session_client
from lastfm.cli import app
from lastfm.session_client import (
    list_sessions,
    remove_session_files,
    session_paths,
    session_process_matches,
    start_session,
    stop_session,
)
from typer.testing import CliRunner


runner = CliRunner()


def test_session_paths_are_isolated_by_id(tmp_path, monkeypatch):
    monkeypatch.setenv("LASTFM_SESSION_ROOT", str(tmp_path))
    paths = session_paths("music-2025")
    assert paths.root == tmp_path / "music-2025"
    assert paths.socket == tmp_path / "music-2025" / "lastfm.sock"
    assert paths.pid == tmp_path / "music-2025" / "pid"
    assert paths.metadata == tmp_path / "music-2025" / "metadata.json"
    assert paths.restart_lock == tmp_path / "music-2025" / "restart.lock"


def write_session_metadata(tmp_path: Path, session_id: str, csv_path: object) -> None:
    paths = session_paths(session_id)
    paths.root.mkdir(parents=True)
    paths.metadata.write_text(json.dumps({"csv_path": csv_path}))


def test_dispatch_restarts_missing_socket_from_metadata_once(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("LASTFM_SESSION_ROOT", str(tmp_path))
    csv_path = tmp_path / "recenttracks-test.csv"
    csv_path.write_text("uts,artist\n")
    write_session_metadata(tmp_path, "music-2025", str(csv_path))
    attempts = []
    starts = []

    def fake_dispatch(_session_id, _command, _params):
        attempts.append(None)
        if len(attempts) == 1:
            raise FileNotFoundError("missing socket")
        return {"plays": 42}

    monkeypatch.setattr(session_client, "_dispatch_once", fake_dispatch)
    monkeypatch.setattr(session_client, "socket_is_connectable", lambda _path: False)
    monkeypatch.setattr(
        session_client,
        "_start_session_until_ready",
        lambda session_id, source, event_stream=None: starts.append(
            (session_id, source, event_stream)
        ),
    )

    result = session_client.dispatch_to_session("music-2025", "listening_stats", {})

    assert result == {"plays": 42}
    assert len(attempts) == 2
    assert starts == [("music-2025", csv_path, None)]
    assert capsys.readouterr().out == ""


def test_dispatch_does_not_restart_remote_error(monkeypatch):
    error = session_client.RemoteAgentError("BAD_COMMAND", "bad command", False)
    monkeypatch.setattr(
        session_client,
        "_dispatch_once",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )
    monkeypatch.setattr(
        session_client,
        "restart_session",
        lambda _session_id: pytest.fail("remote errors must not restart"),
    )

    with pytest.raises(session_client.RemoteAgentError) as exc_info:
        session_client.dispatch_to_session("music-2025", "bad", {})

    assert exc_info.value is error


def test_dispatch_restarts_only_once_on_repeated_transport_failure(monkeypatch):
    attempts = []
    restarts = []

    def fail_dispatch(*_args, **_kwargs):
        attempts.append(None)
        raise ConnectionResetError("reset")

    monkeypatch.setattr(session_client, "_dispatch_once", fail_dispatch)
    monkeypatch.setattr(session_client, "restart_session", restarts.append)

    with pytest.raises(ConnectionResetError, match="reset"):
        session_client.dispatch_to_session("music-2025", "stats", {})

    assert len(attempts) == 2
    assert restarts == ["music-2025"]


@pytest.mark.parametrize(
    ("metadata", "error_type", "message"),
    [
        (None, FileNotFoundError, "No metadata found for session music-2025"),
        ("{", RuntimeError, "Invalid metadata for session music-2025"),
        ({}, RuntimeError, "metadata has no valid csv_path"),
        ({"csv_path": ""}, RuntimeError, "metadata has no valid csv_path"),
        ({"csv_path": "relative.csv"}, RuntimeError, "csv_path is not absolute"),
        (
            {"csv_path": "/does/not/exist.csv"},
            FileNotFoundError,
            "source CSV is not a file",
        ),
    ],
)
def test_restart_rejects_invalid_metadata_without_spawning(
    tmp_path, monkeypatch, metadata, error_type, message
):
    monkeypatch.setenv("LASTFM_SESSION_ROOT", str(tmp_path))
    paths = session_paths("music-2025")
    paths.root.mkdir(parents=True)
    if metadata is not None:
        paths.metadata.write_text(
            metadata if isinstance(metadata, str) else json.dumps(metadata)
        )
    monkeypatch.setattr(session_client, "socket_is_connectable", lambda _path: False)
    monkeypatch.setattr(
        session_client,
        "_start_session_until_ready",
        lambda *_args, **_kwargs: pytest.fail("invalid metadata must not spawn"),
    )

    with pytest.raises(error_type, match=message):
        session_client.restart_session("music-2025")


@pytest.mark.parametrize(
    "first_error",
    [
        ConnectionRefusedError("refused"),
        socket.timeout("timed out"),
        UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"),
    ],
)
def test_dispatch_recovers_from_stale_socket_transport_errors(monkeypatch, first_error):
    responses = iter([first_error, {"ok": True}])
    restarts = []

    def dispatch(*_args, **_kwargs):
        response = next(responses)
        if isinstance(response, BaseException):
            raise response
        return response

    monkeypatch.setattr(session_client, "_dispatch_once", dispatch)
    monkeypatch.setattr(session_client, "restart_session", restarts.append)

    assert session_client.dispatch_to_session("music-2025", "stats", {}) == {"ok": True}
    assert restarts == ["music-2025"]


def assert_response_transport_recovery(
    tmp_path, monkeypatch, first_response: bytes
) -> None:
    monkeypatch.setenv("LASTFM_SESSION_ROOT", str(tmp_path))
    paths = session_paths("music-2025")
    paths.root.mkdir(parents=True)
    paths.socket.touch()
    responses = iter([first_response, b'{"ok":true,"result":{"plays":42}}'])
    restarts = []

    class FakeSocket:
        def __init__(self, *_args, **_kwargs):
            self.chunks = iter([next(responses), b""])

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def connect(self, _path):
            pass

        def sendall(self, _request):
            pass

        def recv(self, _size):
            return next(self.chunks)

    monkeypatch.setattr(session_client.socket, "socket", FakeSocket)
    monkeypatch.setattr(session_client, "restart_session", restarts.append)

    assert session_client.dispatch_to_session("music-2025", "stats", {}) == {
        "plays": 42
    }
    assert restarts == ["music-2025"]


def test_dispatch_recovers_from_invalid_json_response(tmp_path, monkeypatch):
    assert_response_transport_recovery(tmp_path, monkeypatch, b"not-json")


def test_dispatch_recovers_from_truncated_empty_response(tmp_path, monkeypatch):
    assert_response_transport_recovery(tmp_path, monkeypatch, b"")


def test_concurrent_restarts_spawn_one_daemon(tmp_path, monkeypatch):
    monkeypatch.setenv("LASTFM_SESSION_ROOT", str(tmp_path))
    csv_path = tmp_path / "recenttracks-test.csv"
    csv_path.write_text("uts,artist\n")
    write_session_metadata(tmp_path, "music-2025", str(csv_path))
    spawned = threading.Event()
    starts = []

    def connectable(_path):
        return spawned.is_set()

    def start(*args, **kwargs):
        starts.append((args, kwargs))
        time.sleep(0.1)
        spawned.set()

    monkeypatch.setattr(session_client, "socket_is_connectable", connectable)
    monkeypatch.setattr(session_client, "_start_session_until_ready", start)
    barrier = threading.Barrier(3)
    errors = []

    def restart():
        barrier.wait()
        try:
            session_client.restart_session("music-2025")
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=restart) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=2)

    assert errors == []
    assert all(not thread.is_alive() for thread in threads)
    assert len(starts) == 1


def test_start_session_json_forwards_startup_events_until_ready(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("LASTFM_SESSION_ROOT", str(tmp_path))
    popen_kwargs = {}

    class FakeStdout:
        def __init__(self):
            self.lines = iter(
                [
                    '{"event":"start","session_id":"music-2025"}\n',
                    '{"event":"ready","session_id":"music-2025"}\n',
                    '{"event":"late","session_id":"music-2025"}\n',
                ]
            )
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

    process = start_session(
        "music-2025", tmp_path / "recenttracks-test.csv", json_output=True
    )

    assert process is fake_process
    assert capsys.readouterr().out.splitlines() == [
        '{"event":"start","session_id":"music-2025"}',
        '{"event":"ready","session_id":"music-2025"}',
    ]
    assert fake_process.stdout.closed is True
    assert popen_kwargs["stdout"] == session_client.subprocess.PIPE
    assert popen_kwargs["stderr"] == session_client.subprocess.DEVNULL
    assert popen_kwargs["text"] is True


def test_start_session_non_json_returns_detached_process_immediately(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LASTFM_SESSION_ROOT", str(tmp_path))
    popen_calls = []
    fake_process = object()

    def fake_popen(cmd, **kwargs):
        popen_calls.append((cmd, kwargs))
        return fake_process

    monkeypatch.setattr(session_client.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        session_client,
        "_start_session_until_ready",
        lambda *_args, **_kwargs: pytest.fail(
            "detached startup must not wait for ready"
        ),
    )

    process = start_session(
        "music-2025", tmp_path / "recenttracks-test.csv", json_output=False
    )

    assert process is fake_process
    assert len(popen_calls) == 1
    cmd, kwargs = popen_calls[0]
    assert "--json" not in cmd
    assert kwargs == {
        "stdin": session_client.subprocess.DEVNULL,
        "stdout": session_client.subprocess.DEVNULL,
        "stderr": session_client.subprocess.DEVNULL,
        "start_new_session": True,
    }


def test_start_session_until_ready_is_silent_without_event_stream(
    tmp_path, monkeypatch, capsys
):
    popen_commands = []

    class FakeStdout:
        def __init__(self):
            self.lines = iter(['{"event":"start"}\n', '{"event":"ready"}\n'])
            self.closed = False

        def readline(self):
            return next(self.lines, "")

        def close(self):
            self.closed = True

    class FakeProcess:
        def __init__(self):
            self.stdout = FakeStdout()

        def poll(self):
            return None

    fake_process = FakeProcess()

    def fake_popen(cmd, **_kwargs):
        popen_commands.append(cmd)
        return fake_process

    monkeypatch.setattr(session_client.subprocess, "Popen", fake_popen)

    process = session_client._start_session_until_ready(
        "music-2025", tmp_path / "recenttracks-test.csv", event_stream=None
    )

    assert process is fake_process
    assert "--json" in popen_commands[0]
    assert fake_process.stdout.closed is True
    assert capsys.readouterr().out == ""


def test_start_session_refuses_existing_reachable_session(tmp_path, monkeypatch):
    monkeypatch.setenv("LASTFM_SESSION_ROOT", str(tmp_path))
    monkeypatch.setattr(session_client, "socket_is_connectable", lambda _path: True)

    def fail_popen(*_args, **_kwargs):
        raise AssertionError("duplicate session must not spawn a new daemon")

    monkeypatch.setattr(session_client.subprocess, "Popen", fail_popen)

    try:
        start_session(
            "music-2025", tmp_path / "recenttracks-test.csv", json_output=True
        )
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
    monkeypatch.setattr(
        session_client, "session_process_command", lambda _pid: "/bin/sleep 1000"
    )

    def fail_kill(*_args, **_kwargs):
        raise AssertionError("unverified process must not be killed")

    monkeypatch.setattr(session_client.os, "kill", fail_kill)

    try:
        stop_session("music-2025")
    except RuntimeError as exc:
        assert str(exc) == "Refusing to stop unverified session process 12345"
    else:
        raise AssertionError("expected stale pid failure")


def test_list_sessions_reads_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("LASTFM_SESSION_ROOT", str(tmp_path))
    paths = session_paths("a")
    paths.root.mkdir(parents=True)
    paths.metadata.write_text(json.dumps({"session_id": "a", "pid": 123}))
    assert list_sessions() == [{"session_id": "a", "pid": 123}]


def test_list_sessions_reports_corrupt_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("LASTFM_SESSION_ROOT", str(tmp_path))
    paths = session_paths("bad")
    paths.root.mkdir(parents=True)
    paths.metadata.write_text("{")

    sessions = list_sessions()

    assert sessions[0]["session_id"] == "bad"
    assert "metadata_error" in sessions[0]


def test_remove_session_files_removes_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("LASTFM_SESSION_ROOT", str(tmp_path))
    paths = session_paths("a")
    paths.root.mkdir(parents=True)
    paths.metadata.write_text("{}")
    remove_session_files("a")
    assert not paths.root.exists()


def test_session_cleanup_skips_pid_verified_live_session(tmp_path, monkeypatch):
    monkeypatch.setenv("LASTFM_SESSION_ROOT", str(tmp_path))
    paths = session_paths("live")
    paths.root.mkdir(parents=True)
    paths.metadata.write_text(json.dumps({"session_id": "live", "pid": 12345}))
    paths.pid.write_text("12345")
    monkeypatch.setattr(session_client, "socket_is_connectable", lambda _path: False)
    monkeypatch.setattr(
        session_client,
        "session_process_command",
        lambda _pid: (
            "/path/python -m lastfm.session_daemon --session-id live --csv recenttracks.csv"
        ),
    )

    result = runner.invoke(app, ["session-cleanup", "--session", "live", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["result"]["cleaned"] == []
    assert payload["result"]["skipped"] == [
        {"reason": "live_session", "session_id": "live"}
    ]
    assert paths.root.exists()


def test_session_cleanup_all_removes_stale_corrupt_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("LASTFM_SESSION_ROOT", str(tmp_path))
    paths = session_paths("bad")
    paths.root.mkdir(parents=True)
    paths.metadata.write_text("{")
    monkeypatch.setattr(session_client, "socket_is_connectable", lambda _path: False)
    monkeypatch.setattr(session_client, "session_process_command", lambda _pid: None)

    result = runner.invoke(app, ["session-cleanup", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["result"]["cleaned"] == ["bad"]
    assert payload["result"]["skipped"] == []
    assert payload["result"]["errors"] == []
    assert not paths.root.exists()
