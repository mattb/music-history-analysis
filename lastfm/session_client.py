from __future__ import annotations

import json
import os
import shutil
import shlex
import socket
import subprocess
import sys
import time
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
    return Path(
        os.environ.get(
            "LASTFM_SESSION_ROOT",
            Path.home() / ".cache" / "lastfm-analysis" / "sessions",
        )
    )


def session_paths(session_id: str) -> SessionPaths:
    root = session_root() / session_id
    return SessionPaths(
        root=root,
        socket=root / "lastfm.sock",
        pid=root / "pid",
        metadata=root / "metadata.json",
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


def start_session(session_id: str, csv_path: Path, json_output: bool = True) -> subprocess.Popen:
    paths = session_paths(session_id)
    paths.root.mkdir(parents=True, exist_ok=True)
    if socket_is_connectable(paths.socket):
        raise RuntimeError(f"Session {session_id} is already running")

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

    if not json_output:
        return subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    process = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
        start_new_session=True,
    )
    if process.stdout is None:
        raise RuntimeError(f"Could not read startup events for session {session_id}")

    try:
        while True:
            line = process.stdout.readline()
            if not line:
                returncode = process.poll()
                if returncode is None:
                    raise RuntimeError(f"Session {session_id} closed startup output before ready")
                raise RuntimeError(f"Session {session_id} exited before ready with code {returncode}")

            sys.stdout.write(line)
            sys.stdout.flush()

            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("event") == "ready":
                break
    finally:
        process.stdout.close()

    return process


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
