import errno
import io
import json
import os
import socket
import threading
import time
from pathlib import Path

import pytest

import lastfm.session_client as session_client
from lastfm.cli import app
from lastfm.session_client import (
    list_sessions,
    read_session_status,
    remove_session_files,
    session_paths,
    session_process_matches,
    start_session,
    stop_session,
)
from typer.testing import CliRunner


runner = CliRunner()


def fake_startup_lines(stream, _timeout_seconds):
    while True:
        line = stream.readline()
        yield line
        if not line:
            return


def test_session_paths_are_isolated_by_id(tmp_path, monkeypatch):
    monkeypatch.setenv("LASTFM_SESSION_ROOT", str(tmp_path))
    paths = session_paths("music-2025")
    assert paths.root == tmp_path / "music-2025"
    assert paths.socket == tmp_path / "music-2025" / "lastfm.sock"
    assert paths.pid == tmp_path / "music-2025" / "pid"
    assert paths.metadata == tmp_path / "music-2025" / "metadata.json"
    assert paths.restart_lock == tmp_path / ".locks" / "music-2025.lock"


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
    if metadata is not None:
        paths.root.mkdir(parents=True)
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
    assert paths.root.exists()


@pytest.mark.parametrize(
    "first_error",
    [
        ConnectionRefusedError("refused"),
        ConnectionAbortedError("aborted"),
        BrokenPipeError("broken pipe"),
        socket.timeout("timed out"),
        UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"),
        OSError(errno.ENOTCONN, "not connected"),
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


@pytest.mark.parametrize(
    "first_error",
    [PermissionError(errno.EACCES, "denied"), OSError(errno.EINVAL, "invalid")],
)
def test_dispatch_does_not_recover_nontransport_os_errors(monkeypatch, first_error):
    monkeypatch.setattr(
        session_client,
        "_dispatch_once",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(first_error),
    )
    monkeypatch.setattr(
        session_client,
        "restart_session",
        lambda _session_id: pytest.fail("nontransport errors must not restart"),
    )

    with pytest.raises(OSError) as exc_info:
        session_client.dispatch_to_session("music-2025", "stats", {})

    assert exc_info.value is first_error


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


def test_relative_csv_is_persisted_absolute_and_stopped_session_restarts(
    tmp_path, monkeypatch
):
    import lastfm.session_daemon as session_daemon

    session_root = tmp_path / "sessions"
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    csv_path = source_dir / "history.csv"
    csv_path.write_text("uts,artist\n")
    monkeypatch.setenv("LASTFM_SESSION_ROOT", str(session_root))
    monkeypatch.chdir(source_dir)
    loaded_paths = []

    class FakeState:
        def load(self, path):
            loaded_paths.append(path)

        def metadata(self):
            return {"csv_path": str(loaded_paths[-1])}

    class FakeServer:
        def __init__(self, socket_path, *_args):
            Path(socket_path).touch()

        def start_idle_watchdog(self):
            pass

        def serve_forever(self):
            pass

        def server_close(self):
            pass

        def shutdown(self):
            pass

    monkeypatch.setattr(
        "sys.argv",
        ["session-daemon", "--session-id", "relative", "--csv", "history.csv"],
    )
    monkeypatch.setattr(session_daemon, "AnalysisState", FakeState)
    monkeypatch.setattr(session_daemon, "UnixAgentServer", FakeServer)
    monkeypatch.setattr(session_daemon.signal, "signal", lambda *_args: None)

    session_daemon.main()

    paths = session_paths("relative")
    metadata = json.loads(paths.metadata.read_text())
    assert loaded_paths == [csv_path.resolve()]
    assert metadata["csv_path"] == str(csv_path.resolve())
    assert Path(metadata["csv_path"]).is_absolute()
    assert not paths.pid.exists()
    assert not paths.socket.exists()

    attempts = []
    starts = []

    def dispatch(*_args):
        attempts.append(None)
        if len(attempts) == 1:
            raise FileNotFoundError("stopped")
        return {"plays": 1}

    monkeypatch.setattr(session_client, "_dispatch_once", dispatch)
    monkeypatch.setattr(session_client, "socket_is_connectable", lambda _path: False)
    monkeypatch.setattr(
        session_client,
        "_start_session_until_ready",
        lambda session_id, source, event_stream=None: starts.append(
            (session_id, source, event_stream)
        ),
    )

    assert session_client.dispatch_to_session("relative", "stats", {}) == {"plays": 1}
    assert starts == [("relative", csv_path.resolve(), None)]


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
    monkeypatch.setattr(
        session_client, "_startup_lines_until_deadline", fake_startup_lines
    )

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
    monkeypatch.setattr(
        session_client, "_startup_lines_until_deadline", fake_startup_lines
    )

    process = session_client._start_session_until_ready(
        "music-2025", tmp_path / "recenttracks-test.csv", event_stream=None
    )

    assert process is fake_process
    assert "--json" in popen_commands[0]
    assert fake_process.stdout.closed is True
    assert capsys.readouterr().out == ""


def test_start_session_until_ready_returns_while_real_ready_child_stays_alive(
    tmp_path, monkeypatch
):
    real_popen = session_client.subprocess.Popen
    children = []

    def spawn_ready_child(*_args, **_kwargs):
        child = real_popen(
            [
                session_client.sys.executable,
                "-c",
                (
                    "import json, time; "
                    "print(json.dumps({'event': 'ready'}), flush=True); "
                    "time.sleep(30)"
                ),
            ],
            stdin=session_client.subprocess.DEVNULL,
            stdout=session_client.subprocess.PIPE,
            stderr=session_client.subprocess.DEVNULL,
            text=True,
        )
        children.append(child)
        return child

    monkeypatch.setattr(session_client.subprocess, "Popen", spawn_ready_child)
    results = []
    errors = []

    def start():
        try:
            results.append(
                session_client._start_session_until_ready(
                    "music-2025",
                    tmp_path / "recenttracks-test.csv",
                    startup_timeout_seconds=1,
                )
            )
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=start, daemon=True)
    thread.start()
    thread.join(timeout=1)
    returned_promptly = not thread.is_alive()
    child_was_running = bool(children) and children[0].poll() is None

    for child in children:
        if child.poll() is None:
            child.terminate()
        child.wait(timeout=2)
    thread.join(timeout=2)

    assert returned_promptly
    assert child_was_running
    assert errors == []
    assert results == children


def test_start_session_until_ready_times_out_and_reaps_real_child(
    tmp_path, monkeypatch
):
    real_popen = session_client.subprocess.Popen
    children = []

    def spawn_silent_child(*_args, **_kwargs):
        child = real_popen(
            [session_client.sys.executable, "-c", "import time; time.sleep(30)"],
            stdin=session_client.subprocess.DEVNULL,
            stdout=session_client.subprocess.PIPE,
            stderr=session_client.subprocess.DEVNULL,
            text=True,
        )
        children.append(child)
        return child

    monkeypatch.setattr(session_client.subprocess, "Popen", spawn_silent_child)

    with pytest.raises(RuntimeError, match="timed out waiting for ready"):
        session_client._start_session_until_ready(
            "music-2025",
            tmp_path / "recenttracks-test.csv",
            startup_timeout_seconds=0.05,
        )

    assert len(children) == 1
    assert children[0].poll() is not None


def test_start_session_until_ready_times_out_and_reaps_process(tmp_path, monkeypatch):
    released = threading.Event()
    calls = []

    class BlockingStdout:
        def readline(self):
            released.wait()
            return ""

        def close(self):
            calls.append("close")
            released.set()

    class FakeProcess:
        stdout = BlockingStdout()

        def poll(self):
            return None

        def terminate(self):
            calls.append("terminate")
            released.set()

        def wait(self, timeout=None):
            calls.append(("wait", timeout))
            return 0

        def kill(self):
            calls.append("kill")

    monkeypatch.setattr(
        session_client.subprocess, "Popen", lambda *_a, **_k: FakeProcess()
    )

    def timeout_lines(*_args):
        raise TimeoutError
        yield

    monkeypatch.setattr(session_client, "_startup_lines_until_deadline", timeout_lines)

    with pytest.raises(RuntimeError, match="timed out waiting for ready"):
        session_client._start_session_until_ready(
            "music-2025",
            tmp_path / "recenttracks-test.csv",
            startup_timeout_seconds=0.02,
        )

    assert "terminate" in calls
    assert any(isinstance(call, tuple) and call[0] == "wait" for call in calls)
    assert "close" in calls
    assert "kill" not in calls


def test_start_session_until_ready_reaps_process_when_event_stream_fails(
    tmp_path, monkeypatch
):
    calls = []

    class FakeStdout:
        def __init__(self):
            self.lines = iter(['{"event":"start"}\n', '{"event":"ready"}\n'])

        def readline(self):
            return next(self.lines, "")

        def close(self):
            calls.append("close")

    class FakeProcess:
        stdout = FakeStdout()

        def poll(self):
            return None

        def terminate(self):
            calls.append("terminate")

        def wait(self, timeout=None):
            calls.append(("wait", timeout))
            return 0

        def kill(self):
            calls.append("kill")

    class BrokenStream:
        def write(self, _line):
            raise OSError("stream failed")

        def flush(self):
            pass

    monkeypatch.setattr(
        session_client.subprocess, "Popen", lambda *_a, **_k: FakeProcess()
    )
    monkeypatch.setattr(
        session_client, "_startup_lines_until_deadline", fake_startup_lines
    )

    with pytest.raises(OSError, match="stream failed"):
        session_client._start_session_until_ready(
            "music-2025",
            tmp_path / "recenttracks-test.csv",
            event_stream=BrokenStream(),
        )

    assert "terminate" in calls
    assert any(isinstance(call, tuple) and call[0] == "wait" for call in calls)
    assert "close" in calls


def test_start_session_until_ready_kills_process_that_ignores_terminate(
    tmp_path, monkeypatch
):
    calls = []

    class FakeProcess:
        stdout = None

        def poll(self):
            return None

        def terminate(self):
            calls.append("terminate")

        def wait(self, timeout=None):
            calls.append(("wait", timeout))
            if timeout is not None:
                raise session_client.subprocess.TimeoutExpired("daemon", timeout)
            return 0

        def kill(self):
            calls.append("kill")

    monkeypatch.setattr(
        session_client.subprocess, "Popen", lambda *_a, **_k: FakeProcess()
    )

    with pytest.raises(RuntimeError, match="Could not read startup events"):
        session_client._start_session_until_ready(
            "music-2025", tmp_path / "recenttracks-test.csv"
        )

    assert calls == [
        "terminate",
        ("wait", session_client.PROCESS_STOP_TIMEOUT_SECONDS),
        "kill",
        ("wait", None),
    ]


def test_start_session_until_ready_reaps_process_on_malformed_output(
    tmp_path, monkeypatch
):
    calls = []

    class FakeStdout:
        def __init__(self):
            self.lines = iter(["not-json\n", ""])

        def readline(self):
            return next(self.lines)

        def close(self):
            calls.append("close")

    class FakeProcess:
        stdout = FakeStdout()

        def poll(self):
            return None

        def terminate(self):
            calls.append("terminate")

        def wait(self, timeout=None):
            calls.append(("wait", timeout))
            return 0

        def kill(self):
            calls.append("kill")

    monkeypatch.setattr(
        session_client.subprocess, "Popen", lambda *_a, **_k: FakeProcess()
    )
    monkeypatch.setattr(
        session_client, "_startup_lines_until_deadline", fake_startup_lines
    )

    with pytest.raises(RuntimeError, match="invalid startup JSON"):
        session_client._start_session_until_ready(
            "music-2025", tmp_path / "recenttracks-test.csv"
        )

    assert "terminate" in calls
    assert "close" in calls


def test_real_daemon_csv_load_failure_is_structured_and_reaped(tmp_path, monkeypatch):
    monkeypatch.setenv("LASTFM_SESSION_ROOT", str(tmp_path / "sessions"))
    invalid_csv = tmp_path / "invalid.csv"
    invalid_csv.write_text("")
    real_popen = session_client.subprocess.Popen
    children = []

    def capture_child(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(session_client.subprocess, "Popen", capture_child)
    events = io.StringIO()

    with pytest.raises(session_client.SessionStartupError) as exc_info:
        session_client._start_session_until_ready(
            "invalid", invalid_csv, event_stream=events, startup_timeout_seconds=10
        )

    assert exc_info.value.code == "CSV_LOAD_FAILED"
    assert str(exc_info.value).startswith("Session invalid failed to start: ")
    lifecycle = [json.loads(line) for line in events.getvalue().splitlines()]
    assert lifecycle[-1]["event"] == "failed"
    assert lifecycle[-1]["code"] == "CSV_LOAD_FAILED"
    assert lifecycle[-1]["message"]
    assert len(children) == 1
    assert children[0].poll() is not None


def test_real_server_start_failure_reports_before_cleanup_lock_and_reaps_child(
    tmp_path, monkeypatch, sample_csv
):
    long_root = tmp_path / ("x" * 90)
    (tmp_path / "sitecustomize.py").write_text(
        "from lastfm.analysis_state import AnalysisState\n"
        "AnalysisState._build_user_embeddings = lambda self: None\n"
        "AnalysisState._build_critics_embeddings = lambda self: None\n"
        "AnalysisState._build_critic_vectors = lambda self: None\n"
    )
    python_path = os.environ.get("PYTHONPATH")
    monkeypatch.setenv(
        "PYTHONPATH",
        str(tmp_path) if not python_path else f"{tmp_path}{os.pathsep}{python_path}",
    )
    monkeypatch.setenv("LASTFM_SESSION_ROOT", str(long_root))
    real_popen = session_client.subprocess.Popen
    children = []

    def capture_child(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(session_client.subprocess, "Popen", capture_child)
    started_at = time.monotonic()

    with session_client.session_restart_lock("overlong"):
        with pytest.raises(session_client.SessionStartupError) as exc_info:
            session_client._start_session_until_ready(
                "overlong", sample_csv, startup_timeout_seconds=10
            )

    elapsed = time.monotonic() - started_at
    assert exc_info.value.code == "DAEMON_START_FAILED"
    assert "AF_UNIX path too long" in str(exc_info.value)
    assert elapsed < 7
    assert len(children) == 1
    assert children[0].poll() is not None
    paths = session_paths("overlong")
    assert not paths.pid.exists()
    assert not paths.socket.exists()
    assert paths.metadata.exists()


def test_failed_child_runtime_cleanup_preserves_foreign_pid_and_socket(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LASTFM_SESSION_ROOT", str(tmp_path))
    paths = session_paths("successor")
    paths.root.mkdir(parents=True)
    paths.pid.write_text("222")
    paths.socket.write_text("successor")
    paths.metadata.write_text('{"session_id":"successor"}')

    session_client._remove_started_process_runtime_files("successor", 111)

    assert paths.pid.read_text() == "222"
    assert paths.socket.read_text() == "successor"
    assert paths.metadata.exists()


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


def test_explicit_start_waits_for_session_lifecycle_lock(tmp_path, monkeypatch):
    monkeypatch.setenv("LASTFM_SESSION_ROOT", str(tmp_path))
    started = threading.Event()
    finished = threading.Event()
    monkeypatch.setattr(session_client, "socket_is_connectable", lambda _path: False)
    monkeypatch.setattr(
        session_client,
        "_start_session_until_ready",
        lambda *_args, **_kwargs: started.set(),
    )

    with session_client.session_restart_lock("music-2025"):
        thread = threading.Thread(
            target=lambda: (
                start_session(
                    "music-2025",
                    tmp_path / "recenttracks-test.csv",
                    json_output=True,
                ),
                finished.set(),
            )
        )
        thread.start()
        assert not started.wait(timeout=0.05)

    thread.join(timeout=2)

    assert started.is_set()
    assert finished.is_set()


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
    monkeypatch.setattr(session_client, "session_is_live", lambda session_id: True)
    assert list_sessions() == [{"session_id": "a", "pid": 123, "running": True}]


def test_read_session_status_reports_false_liveness_without_starting_session(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LASTFM_SESSION_ROOT", str(tmp_path))
    paths = session_paths("sleeping")
    paths.root.mkdir(parents=True)
    paths.metadata.write_text(json.dumps({"session_id": "sleeping", "pid": 123}))
    monkeypatch.setattr(session_client, "session_is_live", lambda session_id: False)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("status reads must not start or restart sessions")

    monkeypatch.setattr(session_client, "start_session", forbidden)
    monkeypatch.setattr(session_client, "restart_session", forbidden)

    assert read_session_status("sleeping") == {
        "session_id": "sleeping",
        "pid": 123,
        "running": False,
    }


def test_read_session_status_preserves_missing_and_corrupt_metadata_errors(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LASTFM_SESSION_ROOT", str(tmp_path))

    with pytest.raises(FileNotFoundError, match="No metadata found"):
        read_session_status("missing")

    paths = session_paths("bad")
    paths.root.mkdir(parents=True)
    paths.metadata.write_text("{")
    with pytest.raises(json.JSONDecodeError):
        read_session_status("bad")


def test_list_sessions_reports_corrupt_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("LASTFM_SESSION_ROOT", str(tmp_path))
    paths = session_paths("bad")
    paths.root.mkdir(parents=True)
    paths.metadata.write_text("{")

    sessions = list_sessions()

    assert sessions[0]["session_id"] == "bad"
    assert sessions[0]["running"] is False
    assert "metadata_error" in sessions[0]


def test_list_sessions_reports_liveness_without_starting_sessions(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LASTFM_SESSION_ROOT", str(tmp_path))
    for session_id in ("awake", "sleeping"):
        paths = session_paths(session_id)
        paths.root.mkdir(parents=True)
        paths.metadata.write_text(json.dumps({"session_id": session_id}))
    monkeypatch.setattr(
        session_client,
        "session_is_live",
        lambda session_id: session_id == "awake",
    )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("session listing must not start or restart sessions")

    monkeypatch.setattr(session_client, "start_session", forbidden)
    monkeypatch.setattr(session_client, "restart_session", forbidden)

    assert list_sessions() == [
        {"session_id": "awake", "running": True},
        {"session_id": "sleeping", "running": False},
    ]


def test_remove_session_files_removes_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("LASTFM_SESSION_ROOT", str(tmp_path))
    paths = session_paths("a")
    paths.root.mkdir(parents=True)
    paths.metadata.write_text("{}")
    remove_session_files("a")
    assert not paths.root.exists()
    assert paths.restart_lock.exists()


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


def test_session_cleanup_skips_session_revived_while_waiting_for_lock(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LASTFM_SESSION_ROOT", str(tmp_path))
    paths = session_paths("race")
    paths.root.mkdir(parents=True)
    paths.metadata.write_text(json.dumps({"session_id": "race"}))
    revived = threading.Event()
    monkeypatch.setattr(
        session_client, "socket_is_connectable", lambda _path: revived.is_set()
    )
    monkeypatch.setattr(
        session_client, "session_process_is_verified", lambda _id: False
    )
    result_holder = []

    with session_client.session_restart_lock("race"):
        thread = threading.Thread(
            target=lambda: result_holder.append(
                runner.invoke(app, ["session-cleanup", "--session", "race", "--json"])
            )
        )
        thread.start()
        time.sleep(0.05)
        revived.set()

    thread.join(timeout=2)

    assert not thread.is_alive()
    result = result_holder[0]
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["result"]["cleaned"] == []
    assert payload["result"]["skipped"] == [
        {"reason": "live_session", "session_id": "race"}
    ]
    assert paths.root.exists()
