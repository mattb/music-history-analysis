from __future__ import annotations

import argparse
import json
import os
import signal
import socketserver
import sys
import threading
import time
from contextlib import redirect_stdout
from pathlib import Path

from . import agent_tools
from .agent_output import emit_event, error_envelope, success_envelope
from .analysis_state import AnalysisState
from .session_client import SessionPaths, session_paths, socket_is_connectable


DEFAULT_IDLE_TIMEOUT_SECONDS = 30 * 60


class IdleTracker:
    def __init__(
        self,
        timeout_seconds=DEFAULT_IDLE_TIMEOUT_SECONDS,
        clock=time.monotonic,
    ):
        self.timeout_seconds = timeout_seconds
        self.clock = clock
        self.last_activity = clock()
        self.active_requests = 0
        self.lock = threading.Lock()

    def request_started(self) -> None:
        with self.lock:
            self.active_requests += 1

    def request_finished(self) -> None:
        with self.lock:
            self.active_requests -= 1
            self.last_activity = self.clock()

    def is_expired(self) -> bool:
        with self.lock:
            return (
                self.active_requests == 0
                and self.clock() - self.last_activity >= self.timeout_seconds
            )


class AgentRequestHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        self.server.idle_tracker.request_started()
        try:
            raw = self.rfile.readline().decode()
            request = json.loads(raw)
            command = request["command"]
            params = request.get("params", {})
            try:
                result = agent_tools.dispatch(self.server.state, command, params)
                payload = success_envelope(
                    command=command,
                    session_id=self.server.session_id,
                    result=result,
                )
            except Exception as exc:
                payload = error_envelope(
                    command=command,
                    session_id=self.server.session_id,
                    code=type(exc).__name__.upper(),
                    message=str(exc),
                    retryable=False,
                )
            self.wfile.write(json.dumps(payload).encode())
        finally:
            self.server.idle_tracker.request_finished()


class UnixAgentServer(socketserver.UnixStreamServer):
    def __init__(
        self,
        socket_path: str,
        handler,
        state: AnalysisState,
        session_id: str,
        idle_timeout_seconds=DEFAULT_IDLE_TIMEOUT_SECONDS,
        clock=time.monotonic,
    ):
        self.state = state
        self.session_id = session_id
        self.idle_tracker = IdleTracker(idle_timeout_seconds, clock)
        super().__init__(socket_path, handler)

    def start_idle_watchdog(self, check_interval_seconds=1.0) -> threading.Thread:
        def watch_for_expiration() -> None:
            while True:
                time.sleep(check_interval_seconds)
                if self.idle_tracker.is_expired():
                    self.shutdown()
                    return

        thread = threading.Thread(target=watch_for_expiration, daemon=True)
        thread.start()
        return thread


def remove_owned_runtime_files(paths: SessionPaths, pid: int) -> None:
    try:
        paths.socket.unlink()
    except FileNotFoundError:
        pass

    try:
        recorded_pid = int(paths.pid.read_text())
    except (FileNotFoundError, OSError, ValueError):
        return

    if recorded_pid == pid:
        try:
            paths.pid.unlink()
        except FileNotFoundError:
            pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--csv", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    paths = session_paths(args.session_id)
    paths.root.mkdir(parents=True, exist_ok=True)
    if socket_is_connectable(paths.socket):
        if args.json:
            emit_event(
                "failed",
                session_id=args.session_id,
                code="SESSION_ALREADY_RUNNING",
                message=f"Session {args.session_id} is already running",
            )
        else:
            print(f"Session {args.session_id} is already running", file=sys.stderr)
        raise SystemExit(1)
    if paths.socket.exists():
        paths.socket.unlink()

    if args.json:
        emit_event("start", session_id=args.session_id)
        emit_event(
            "load_csv", session_id=args.session_id, path=str(Path(args.csv).resolve())
        )

    state = AnalysisState()
    with redirect_stdout(sys.stderr):
        state.load(Path(args.csv))

    paths.pid.write_text(str(os.getpid()))
    metadata = {
        "session_id": args.session_id,
        "pid": os.getpid(),
        "socket": str(paths.socket),
        **state.metadata(),
    }
    paths.metadata.write_text(json.dumps(metadata, indent=2, sort_keys=True))

    server = UnixAgentServer(
        str(paths.socket), AgentRequestHandler, state, args.session_id
    )

    def shutdown(_signum, _frame) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    if args.json:
        emit_event("ready", session_id=args.session_id, socket=str(paths.socket))

    try:
        server.start_idle_watchdog()
        server.serve_forever()
    finally:
        server.server_close()
        remove_owned_runtime_files(paths, os.getpid())


if __name__ == "__main__":
    main()
