from pathlib import Path

import pytest


@pytest.fixture
def sample_csv(tmp_path: Path) -> Path:
    path = tmp_path / "recenttracks-test-1.csv"
    path.write_text(
        "\n".join(
            [
                "uts,utc_time,artist,artist_mbid,album,album_mbid,track,track_mbid",
                "1704067200,01 Jan 2024,Artist A,,Album A,,Track 1,",
                "1704153600,02 Jan 2024,Artist A,,Album A,,Track 2,",
                "1704240000,03 Jan 2024,Artist B,,Album B,,Track 1,",
                "1735689600,01 Jan 2025,Artist C,,Album C,,Track 1,",
            ]
        )
        + "\n"
    )
    return path


@pytest.fixture
def critics_file(tmp_path: Path) -> Path:
    path = tmp_path / "critics-2024.json"
    path.write_text(
        """[
  {
    "critic": "Critic One",
    "publication": "Example Weekly",
    "albums": [
      {"artist": "Artist A", "title": "Album A", "rank": 1},
      {"artist": "Artist D", "title": "Album D", "rank": 2}
    ]
  }
]"""
    )
    return path
