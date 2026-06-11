"""Structured output helpers for agent-facing CLI commands."""

from __future__ import annotations

import json
import sys
from typing import Any


def success_envelope(command: str, result: Any, session_id: str | None = None) -> dict[str, Any]:
    return {
        "ok": True,
        "command": command,
        "session_id": session_id,
        "result": result,
    }


def error_envelope(
    command: str,
    code: str,
    message: str,
    retryable: bool,
    session_id: str | None = None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "command": command,
        "session_id": session_id,
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
        },
    }


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def emit_event(event: str, **fields: Any) -> None:
    payload = {"event": event, **fields}
    sys.stdout.write(json.dumps(payload, sort_keys=True) + "\n")
    sys.stdout.flush()
