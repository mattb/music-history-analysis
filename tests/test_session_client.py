from pathlib import Path

import lastfm.session_client as session_client
from lastfm.session_client import SessionPaths, session_paths, start_session


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
