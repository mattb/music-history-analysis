from pathlib import Path


def test_mcp_server_removed():
    assert not Path("lastfm/mcp_server.py").exists()
