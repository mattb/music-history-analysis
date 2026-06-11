from lastfm.agent_output import error_envelope, success_envelope


def test_success_envelope_contains_command_session_and_result():
    assert success_envelope(
        command="artist-deep-dive",
        result={"artist": "Artist A"},
        session_id="music-2025",
    ) == {
        "ok": True,
        "command": "artist-deep-dive",
        "session_id": "music-2025",
        "result": {"artist": "Artist A"},
    }


def test_error_envelope_contains_stable_error_contract():
    assert error_envelope(
        command="artist-deep-dive",
        code="SESSION_NOT_FOUND",
        message="No running session named music-2025",
        retryable=False,
        session_id="music-2025",
    ) == {
        "ok": False,
        "command": "artist-deep-dive",
        "session_id": "music-2025",
        "error": {
            "code": "SESSION_NOT_FOUND",
            "message": "No running session named music-2025",
            "retryable": False,
        },
    }
