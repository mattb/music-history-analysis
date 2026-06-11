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
