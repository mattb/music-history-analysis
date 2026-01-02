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
    import pandas as pd
    if not s or pd.isna(s):
        return ""
    s = str(s).lower().strip()
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


def fuzzy_match_score(s1: str, s2: str) -> float:
    """Calculate fuzzy matching score between two strings using Levenshtein distance.

    Returns a score from 0-100, where 100 is an exact match.
    """
    from rapidfuzz import fuzz
    if not s1 or not s2:
        return 0.0
    return fuzz.ratio(s1, s2)


def find_album_match(your_albums: set, critic_artist: str, critic_album: str, threshold: int = 85) -> tuple | None:
    """Find the best match for a critic's album in your listening history.

    Args:
        your_albums: Set of (normalized_artist, normalized_album) tuples from your history
        critic_artist: Artist name from critics list
        critic_album: Album name from critics list
        threshold: Minimum fuzzy match score (0-100) to consider a match

    Returns:
        Tuple of (artist, album) if match found, None otherwise
    """
    norm_artist = normalize_for_matching(critic_artist)
    norm_album = normalize_for_matching(critic_album)

    # Try exact match first (fast path)
    exact_key = (norm_artist, norm_album)
    if exact_key in your_albums:
        return exact_key

    # Try fuzzy matching (slower, but catches variants)
    best_match = None
    best_score = 0

    for your_artist, your_album in your_albums:
        # Both artist and album must match reasonably well
        artist_score = fuzzy_match_score(norm_artist, your_artist)
        album_score = fuzzy_match_score(norm_album, your_album)

        # Combined score (weighted average: album is more important)
        combined_score = (album_score * 0.7) + (artist_score * 0.3)

        # Must meet threshold on both individually and combined
        if (artist_score >= threshold and album_score >= threshold and
            combined_score > best_score):
            best_score = combined_score
            best_match = (your_artist, your_album)

    return best_match if best_score >= threshold else None


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
    min_familiarity: float | None = None,
) -> dict:
    """Match critics' picks against listening history.

    Args:
        critics_data: Parsed critics data from parse_critics_data()
        scrobbles_df: DataFrame of scrobbles
        year: Year to filter scrobbles to
        min_familiarity: If provided, use continuous familiarity scoring (0-1)
                        instead of binary 5x5 rule. Default None uses 5x5.
                        Suggested values: 0.6 (strict), 0.4 (moderate), 0.2 (loose)

    Returns dict with:
    - matched: Albums you've listened to that critics listed
    - unheard: Highly-rated albums you haven't heard
    - your_artists: Your top artists and their critic-listed albums
    """
    from . import data

    # Filter scrobbles to year
    df = scrobbles_df[scrobbles_df['year'] == year].copy()

    # Get albums you've listened to
    listened_albums = data.get_listened_albums(df, min_familiarity=min_familiarity)

    # Normalize the listened albums for matching with critics
    your_albums = set()
    for artist, album in listened_albums:
        norm_artist = normalize_for_matching(artist)
        norm_album = normalize_for_matching(album)
        your_albums.add((norm_artist, norm_album))

    # Build artist plays and album plays counters
    your_artists = set()
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
            album_plays[(norm_artist, norm_album)] += 1

    critics_albums = critics_data['albums']
    critics_artist_albums = critics_data['artist_albums']

    # Find matches - albums you've listened to that critics listed
    # Uses fuzzy matching to catch variant spellings and editions
    matched = []
    matched_keys = set()  # Track which critic albums we've matched

    for key, critic_album in critics_albums.items():
        norm_artist, norm_album = key

        # Try fuzzy matching (includes exact match as fast path)
        match_key = find_album_match(your_albums, critic_album.artist, critic_album.album)

        if match_key:
            matched_keys.add(key)
            matched.append(AlbumMatch(
                artist=critic_album.artist,
                album=critic_album.album,
                critics_count=critic_album.critics_count,
                your_plays=album_plays.get(match_key, 0),
                critics=critic_album.critics,
            ))

    # Sort by critics count
    matched.sort(key=lambda x: (-x.critics_count, -x.your_plays))

    # Find unheard - highly rated albums you haven't listened to
    unheard = []
    for key, critic_album in critics_albums.items():
        if key not in matched_keys:  # Not matched by fuzzy matching
            norm_artist, _ = key
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
