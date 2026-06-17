"""Spotify integration for creating playlists from recommendations."""

import os
import json
from pathlib import Path
from dataclasses import dataclass

import spotipy
from spotipy.oauth2 import SpotifyOAuth
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

console = Console()

# Spotify API scopes needed for playlist creation
SCOPES = "playlist-modify-public playlist-modify-private"

# Cache file for credentials
CACHE_DIR = Path.home() / ".cache" / "music-history-analysis"
CREDENTIALS_FILE = CACHE_DIR / "spotify_credentials.json"
TOKEN_CACHE = CACHE_DIR / ".spotify_token_cache"


@dataclass
class SpotifyCredentials:
    client_id: str
    client_secret: str
    redirect_uri: str = "http://127.0.0.1:1337/auth/spotify/callback"


def get_credentials() -> SpotifyCredentials | None:
    """Load stored Spotify credentials."""
    if CREDENTIALS_FILE.exists():
        try:
            data = json.loads(CREDENTIALS_FILE.read_text())
            return SpotifyCredentials(**data)
        except (json.JSONDecodeError, TypeError):
            return None
    return None


def save_credentials(creds: SpotifyCredentials) -> None:
    """Save Spotify credentials."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CREDENTIALS_FILE.write_text(
        json.dumps(
            {
                "client_id": creds.client_id,
                "client_secret": creds.client_secret,
                "redirect_uri": creds.redirect_uri,
            }
        )
    )


def get_spotify_client() -> spotipy.Spotify | None:
    """Get an authenticated Spotify client."""
    creds = get_credentials()
    if not creds:
        return None

    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    auth_manager = SpotifyOAuth(
        client_id=creds.client_id,
        client_secret=creds.client_secret,
        redirect_uri=creds.redirect_uri,
        scope=SCOPES,
        cache_path=str(TOKEN_CACHE),
        open_browser=True,
    )

    return spotipy.Spotify(auth_manager=auth_manager)


def search_album(sp: spotipy.Spotify, artist: str, album: str) -> dict | None:
    """Search for an album on Spotify."""
    # Try exact search first
    query = f'album:"{album}" artist:"{artist}"'
    results = sp.search(q=query, type="album", limit=5)

    if results["albums"]["items"]:
        return results["albums"]["items"][0]

    # Fall back to less strict search
    query = f"{album} {artist}"
    results = sp.search(q=query, type="album", limit=5)

    if results["albums"]["items"]:
        # Try to find best match
        for item in results["albums"]["items"]:
            album_name = item["name"].lower()
            artist_names = [a["name"].lower() for a in item["artists"]]
            if album.lower() in album_name or any(
                artist.lower() in a for a in artist_names
            ):
                return item
        # Return first result if no good match
        return results["albums"]["items"][0]

    return None


def get_album_tracks(sp: spotipy.Spotify, album_id: str) -> list[str]:
    """Get all track URIs from an album."""
    tracks = []
    results = sp.album_tracks(album_id)

    while results:
        tracks.extend([t["uri"] for t in results["items"]])
        if results["next"]:
            results = sp.next(results)
        else:
            break

    return tracks


def create_playlist(
    sp: spotipy.Spotify,
    name: str,
    description: str = "",
    public: bool = True,
) -> dict:
    """Create a new playlist."""
    user_id = sp.current_user()["id"]
    return sp.user_playlist_create(
        user_id,
        name,
        public=public,
        description=description,
    )


def add_tracks_to_playlist(
    sp: spotipy.Spotify,
    playlist_id: str,
    track_uris: list[str],
) -> None:
    """Add tracks to a playlist (handles batching for large lists)."""
    # Spotify API limits to 100 tracks per request
    for i in range(0, len(track_uris), 100):
        batch = track_uris[i : i + 100]
        sp.playlist_add_items(playlist_id, batch)


def create_playlist_from_albums(
    sp: spotipy.Spotify,
    albums: list[dict],  # List of {"artist": str, "album": str}
    playlist_name: str,
    playlist_description: str = "",
) -> tuple[str, int, int]:
    """
    Create a playlist from a list of albums.

    Returns: (playlist_url, tracks_added, albums_found)
    """
    all_tracks = []
    albums_found = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Searching albums...", total=len(albums))

        for album_info in albums:
            artist = album_info["artist"]
            album = album_info["album"]

            progress.update(task, description=f"Searching: {artist} - {album}...")

            spotify_album = search_album(sp, artist, album)
            if spotify_album:
                tracks = get_album_tracks(sp, spotify_album["id"])
                all_tracks.extend(tracks)
                albums_found += 1

            progress.advance(task)

    if not all_tracks:
        return None, 0, 0

    # Create playlist
    playlist = create_playlist(sp, playlist_name, playlist_description)
    add_tracks_to_playlist(sp, playlist["id"], all_tracks)

    return playlist["external_urls"]["spotify"], len(all_tracks), albums_found
