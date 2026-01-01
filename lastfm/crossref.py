"""Cross-reference critics' picks with Last.fm listening history."""

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass
class AlbumMatch:
    """An album that appears in both critics' lists and listening history."""
    artist: str
    album: str
    critics_count: int  # How many critics listed it
    your_plays: int  # How many times you played tracks from it
    critics: list[str]  # Which critics listed it


@dataclass
class CriticsAlbum:
    """An album from critics' lists."""
    artist: str
    album: str
    critics_count: int
    critics: list[str]


def normalize_for_matching(s: str) -> str:
    """Normalize a string for fuzzy matching."""
    if not s:
        return ""
    s = s.lower().strip()
    # Remove "the " prefix
    if s.startswith("the "):
        s = s[4:]
    # Normalize quotes and apostrophes
    s = s.replace("'", "'").replace("'", "'").replace(""", '"').replace(""", '"')
    # Remove punctuation
    s = re.sub(r'[^\w\s]', '', s)
    # Collapse whitespace
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def load_critics_data(json_path: Path) -> dict:
    """Load critics data and build lookup structures."""
    with open(json_path) as f:
        data = json.load(f)

    # Build album -> critics mapping
    album_critics = defaultdict(list)  # (norm_artist, norm_album) -> [(artist, album, critic), ...]
    artist_albums = defaultdict(set)  # norm_artist -> set of (artist, album, critics_count)

    for lst in data:
        critic = lst['critic']
        for album in lst['albums']:
            artist = album['artist'] or ''
            title = album['title'] or ''
            if artist and title:
                key = (normalize_for_matching(artist), normalize_for_matching(title))
                album_critics[key].append((artist, title, critic))

    # Consolidate to get counts
    albums = {}  # (norm_artist, norm_album) -> CriticsAlbum
    for key, entries in album_critics.items():
        # Use the most common spelling
        artist_counter = Counter(e[0] for e in entries)
        album_counter = Counter(e[1] for e in entries)
        artist = artist_counter.most_common(1)[0][0]
        album = album_counter.most_common(1)[0][0]
        critics = list(set(e[2] for e in entries))
        albums[key] = CriticsAlbum(
            artist=artist,
            album=album,
            critics_count=len(critics),
            critics=critics,
        )
        artist_albums[key[0]].add((artist, album, len(critics)))

    return {
        'albums': albums,
        'artist_albums': artist_albums,
        'raw': data,
    }


def match_with_history(
    critics_data: dict,
    scrobbles_df: pd.DataFrame,
    year: int = 2025,
) -> dict:
    """Match critics' picks against listening history.

    Returns dict with:
    - matched: Albums you've listened to that critics listed
    - unheard: Highly-rated albums you haven't heard
    - your_artists: Your top artists and their critic-listed albums
    """
    # Filter scrobbles to year
    df = scrobbles_df[scrobbles_df['year'] == year].copy()

    # Build your listening data
    your_artists = set()
    your_albums = set()  # (norm_artist, norm_album)
    artist_plays = Counter()
    album_plays = Counter()

    for _, row in df.iterrows():
        artist = row['artist']
        album = row['album'] if row['album'] else ''

        norm_artist = normalize_for_matching(artist)
        norm_album = normalize_for_matching(album)

        your_artists.add(norm_artist)
        artist_plays[norm_artist] += 1

        if album:
            your_albums.add((norm_artist, norm_album))
            album_plays[(norm_artist, norm_album)] += 1

    critics_albums = critics_data['albums']
    critics_artist_albums = critics_data['artist_albums']

    # Find matches - albums you've listened to that critics listed
    matched = []
    for key, critic_album in critics_albums.items():
        norm_artist, norm_album = key
        if key in your_albums:
            matched.append(AlbumMatch(
                artist=critic_album.artist,
                album=critic_album.album,
                critics_count=critic_album.critics_count,
                your_plays=album_plays[key],
                critics=critic_album.critics,
            ))

    # Sort by critics count
    matched.sort(key=lambda x: (-x.critics_count, -x.your_plays))

    # Find unheard - highly rated albums you haven't listened to
    unheard = []
    for key, critic_album in critics_albums.items():
        norm_artist, norm_album = key
        if key not in your_albums:
            # Check if you've heard the artist at all
            heard_artist = norm_artist in your_artists
            unheard.append({
                'artist': critic_album.artist,
                'album': critic_album.album,
                'critics_count': critic_album.critics_count,
                'critics': critic_album.critics,
                'heard_artist': heard_artist,
                'artist_plays': artist_plays.get(norm_artist, 0),
            })

    # Sort by critics count, prioritize artists you know
    unheard.sort(key=lambda x: (-x['critics_count'], -x['artist_plays']))

    # Your top artists and what critics say about them
    your_top_artists = []
    for norm_artist, plays in artist_plays.most_common(50):
        if norm_artist in critics_artist_albums:
            critic_albums = list(critics_artist_albums[norm_artist])
            your_top_artists.append({
                'artist': critic_albums[0][0],  # Use canonical name
                'your_plays': plays,
                'critic_albums': [(a, t, c) for a, t, c in critic_albums],
            })

    return {
        'matched': matched,
        'unheard': unheard,
        'your_top_artists': your_top_artists,
        'stats': {
            'total_critics_albums': len(critics_albums),
            'your_albums_heard': len(your_albums),
            'matched_count': len(matched),
            'your_artists_in_critics': len(your_top_artists),
        }
    }
