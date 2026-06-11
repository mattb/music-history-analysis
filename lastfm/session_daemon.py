from __future__ import annotations

import argparse
import json
import os
import signal
import socketserver
import sys
import threading
from contextlib import redirect_stdout
from pathlib import Path

from . import agent_tools
from .agent_output import emit_event, error_envelope, success_envelope
from .analysis_state import AnalysisState
from .session_client import session_paths, socket_is_connectable


class AgentRequestHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        raw = self.rfile.readline().decode()
        request = json.loads(raw)
        command = request["command"]
        params = request.get("params", {})
        try:
            result = agent_tools.dispatch(self.server.state, command, params)
            payload = success_envelope(command=command, session_id=self.server.session_id, result=result)
        except Exception as exc:
            payload = error_envelope(
                command=command,
                session_id=self.server.session_id,
                code=type(exc).__name__.upper(),
                message=str(exc),
                retryable=False,
            )
        self.wfile.write(json.dumps(payload).encode())


class UnixAgentServer(socketserver.UnixStreamServer):
    def __init__(self, socket_path: str, handler, state: AnalysisState, session_id: str):
        self.state = state
        self.session_id = session_id
        super().__init__(socket_path, handler)


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
        emit_event("load_csv", session_id=args.session_id, path=str(Path(args.csv).resolve()))

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

    server = UnixAgentServer(str(paths.socket), AgentRequestHandler, state, args.session_id)

    def shutdown(_signum, _frame) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    if args.json:
        emit_event("ready", session_id=args.session_id, socket=str(paths.socket))

    try:
        server.serve_forever()
    finally:
        server.server_close()
        if paths.socket.exists():
            paths.socket.unlink()


if __name__ == "__main__":
    main()
