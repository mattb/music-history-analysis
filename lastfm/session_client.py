from __future__ import annotations

import fcntl
import errno
import json
import os
import selectors
import shlex
import shutil
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Literal, TextIO


DEFAULT_STARTUP_TIMEOUT_SECONDS = 120.0
PROCESS_STOP_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class SessionPaths:
    root: Path
    socket: Path
    pid: Path
    metadata: Path
    restart_lock: Path


class RemoteAgentError(RuntimeError):
    """An error envelope returned by a daemon session."""

    def __init__(self, code: str, message: str, retryable: bool):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def session_root() -> Path:
    return Path(
        os.environ.get(
            "LASTFM_SESSION_ROOT",
            Path.home() / ".cache" / "lastfm-analysis" / "sessions",
        )
    )


def session_paths(session_id: str) -> SessionPaths:
    sessions_root = session_root()
    root = sessions_root / session_id
    return SessionPaths(
        root=root,
        socket=root / "lastfm.sock",
        pid=root / "pid",
        metadata=root / "metadata.json",
        restart_lock=sessions_root / ".locks" / f"{session_id}.lock",
    )


def socket_is_connectable(socket_path: Path) -> bool:
    if not socket_path.exists():
        return False
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        try:
            sock.connect(str(socket_path))
        except OSError:
            return False
    return True


def session_process_command(pid: int) -> str | None:
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "command="],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    command = result.stdout.strip()
    return command or None


def session_process_matches(command: str | None, session_id: str) -> bool:
    if not command:
        return False
    try:
        parts = shlex.split(command)
    except ValueError:
        return False
    if "lastfm.session_daemon" not in command or "--session-id" not in parts:
        return False
    index = parts.index("--session-id")
    return index + 1 < len(parts) and parts[index + 1] == session_id


def verify_session_process(pid: int, session_id: str) -> bool:
    return session_process_matches(session_process_command(pid), session_id)


def session_process_is_verified(session_id: str) -> bool:
    paths = session_paths(session_id)
    if not paths.pid.exists():
        return False
    try:
        pid = int(paths.pid.read_text())
    except ValueError:
        return False
    return verify_session_process(pid, session_id)


def session_is_live(session_id: str) -> bool:
    paths = session_paths(session_id)
    return socket_is_connectable(paths.socket) or session_process_is_verified(
        session_id
    )


def start_session(
    session_id: str, csv_path: Path, json_output: bool = True
) -> subprocess.Popen:
    paths = session_paths(session_id)
    paths.root.mkdir(parents=True, exist_ok=True)
    if socket_is_connectable(paths.socket):
        raise RuntimeError(f"Session {session_id} is already running")

    if json_output:
        return _start_session_until_ready(session_id, csv_path, event_stream=sys.stdout)

    cmd = [
        sys.executable,
        "-m",
        "lastfm.session_daemon",
        "--session-id",
        session_id,
        "--csv",
        str(csv_path),
    ]
    return subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _start_session_until_ready(
    session_id: str,
    csv_path: Path,
    event_stream: TextIO | None = None,
    startup_timeout_seconds: float = DEFAULT_STARTUP_TIMEOUT_SECONDS,
) -> subprocess.Popen:
    cmd = [
        sys.executable,
        "-m",
        "lastfm.session_daemon",
        "--session-id",
        session_id,
        "--csv",
        str(csv_path),
        "--json",
    ]
    process = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
        start_new_session=True,
    )
    ready = False
    startup_lines: Iterator[str] | None = None
    try:
        if process.stdout is None:
            raise RuntimeError(
                f"Could not read startup events for session {session_id}"
            )

        startup_lines = _startup_lines_until_deadline(
            process.stdout, startup_timeout_seconds
        )
        while True:
            try:
                line = next(startup_lines)
            except TimeoutError as exc:
                raise RuntimeError(
                    f"Session {session_id} timed out waiting for ready after "
                    f"{startup_timeout_seconds:g} seconds"
                ) from exc
            except StopIteration as exc:
                raise RuntimeError(
                    f"Could not read startup events for session {session_id}"
                ) from exc
            if not line:
                returncode = process.poll()
                if returncode is None:
                    raise RuntimeError(
                        f"Session {session_id} closed startup output before ready"
                    )
                raise RuntimeError(
                    f"Session {session_id} exited before ready with code {returncode}"
                )

            if event_stream is not None:
                event_stream.write(line)
                event_stream.flush()

            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"Session {session_id} emitted invalid startup JSON"
                ) from exc
            if event.get("event") == "ready":
                ready = True
                break
    except BaseException:
        _terminate_started_process(process)
        raise
    finally:
        close_startup_lines = getattr(startup_lines, "close", None)
        if close_startup_lines is not None:
            close_startup_lines()
        if process.stdout is not None:
            process.stdout.close()

    if not ready:
        raise RuntimeError(f"Session {session_id} did not become ready")
    return process


def _startup_lines_until_deadline(
    stream: TextIO, timeout_seconds: float
) -> Iterator[str]:
    fd = stream.fileno()
    deadline = time.monotonic() + timeout_seconds
    buffered = b""
    selector = selectors.DefaultSelector()
    selector.register(fd, selectors.EVENT_READ)
    os.set_blocking(fd, False)
    try:
        while True:
            newline = buffered.find(b"\n")
            if newline >= 0:
                line = buffered[: newline + 1]
                buffered = buffered[newline + 1 :]
                yield line.decode()
                continue

            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(remaining):
                raise TimeoutError

            try:
                chunk = os.read(fd, 65536)
            except BlockingIOError:
                continue
            if not chunk:
                if buffered:
                    yield buffered.decode()
                yield ""
                return
            buffered += chunk
    finally:
        selector.close()


def _terminate_started_process(process: subprocess.Popen) -> None:
    if process.poll() is None:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=PROCESS_STOP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def stop_session(session_id: str) -> dict[str, Any]:
    paths = session_paths(session_id)
    pid = int(paths.pid.read_text())
    if not verify_session_process(pid, session_id):
        raise RuntimeError(f"Refusing to stop unverified session process {pid}")
    os.kill(pid, 15)
    for _ in range(20):
        if not verify_session_process(pid, session_id):
            break
        time.sleep(0.05)
    return {"stopped": True, "pid": pid}


def read_metadata(session_id: str) -> dict[str, Any]:
    path = session_paths(session_id).metadata
    if not path.exists():
        raise FileNotFoundError(f"No metadata found for session {session_id}")
    return json.loads(path.read_text())


def read_session_status(session_id: str) -> dict[str, Any]:
    metadata = read_metadata(session_id)
    return {**metadata, "running": session_is_live(session_id)}


def persisted_session_csv(session_id: str) -> Path:
    try:
        metadata = read_metadata(session_id)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid metadata for session {session_id}: {exc}") from exc

    csv_value = metadata.get("csv_path") if isinstance(metadata, dict) else None
    if not isinstance(csv_value, str) or not csv_value:
        raise RuntimeError(f"Session {session_id} metadata has no valid csv_path")

    csv_path = Path(csv_value)
    if not csv_path.is_absolute():
        raise RuntimeError(f"Session {session_id} csv_path is not absolute: {csv_path}")
    if not csv_path.is_file():
        raise FileNotFoundError(
            f"Session {session_id} source CSV is not a file: {csv_path}"
        )
    return csv_path


@contextmanager
def session_restart_lock(session_id: str) -> Iterator[SessionPaths]:
    paths = session_paths(session_id)
    paths.restart_lock.parent.mkdir(parents=True, exist_ok=True)
    with paths.restart_lock.open("a") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        yield paths


def restart_session(session_id: str) -> None:
    with session_restart_lock(session_id) as paths:
        paths.root.mkdir(parents=True, exist_ok=True)
        if socket_is_connectable(paths.socket):
            return
        csv_path = persisted_session_csv(session_id)
        _start_session_until_ready(session_id, csv_path, event_stream=None)


def list_sessions() -> list[dict[str, Any]]:
    root = session_root()
    if not root.exists():
        return []

    sessions = []
    for metadata_path in sorted(root.glob("*/metadata.json")):
        session_id = metadata_path.parent.name
        try:
            sessions.append(read_session_status(session_id))
        except (OSError, json.JSONDecodeError) as exc:
            sessions.append(
                {
                    "session_id": session_id,
                    "running": False,
                    "metadata_error": str(exc),
                }
            )
    return sessions


def remove_session_files(session_id: str) -> Literal["removed", "missing", "live"]:
    with session_restart_lock(session_id) as paths:
        if not paths.root.exists():
            return "missing"
        if session_is_live(session_id):
            return "live"
        shutil.rmtree(paths.root)
        return "removed"


def _dispatch_once(session_id: str, command: str, params: dict[str, Any]) -> Any:
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
        error = response.get("error", {})
        raise RemoteAgentError(
            code=error.get("code", "REMOTE_ERROR"),
            message=error.get("message", "Session command failed"),
            retryable=bool(error.get("retryable", False)),
        )
    return response["result"]


RECOVERABLE_SOCKET_ERRNOS = {
    errno.ENOENT,
    errno.ECONNREFUSED,
    errno.ECONNRESET,
    errno.ECONNABORTED,
    errno.EPIPE,
    errno.ENOTCONN,
}


def _is_recoverable_transport_error(exc: BaseException) -> bool:
    if isinstance(exc, (json.JSONDecodeError, UnicodeDecodeError, socket.timeout)):
        return True
    if isinstance(
        exc,
        (
            FileNotFoundError,
            ConnectionRefusedError,
            ConnectionResetError,
            ConnectionAbortedError,
            BrokenPipeError,
        ),
    ):
        return True
    return isinstance(exc, OSError) and exc.errno in RECOVERABLE_SOCKET_ERRNOS


def dispatch_to_session(session_id: str, command: str, params: dict[str, Any]) -> Any:
    try:
        return _dispatch_once(session_id, command, params)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        if not _is_recoverable_transport_error(exc):
            raise
        restart_session(session_id)
    return _dispatch_once(session_id, command, params)
