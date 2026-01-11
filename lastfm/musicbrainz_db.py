"""Local MusicBrainz database from JSON dumps for rich music metadata lookups."""

import json
import sqlite3
import tarfile
from pathlib import Path
from io import BytesIO
from dataclasses import dataclass

import httpx
from rich.console import Console
from rich.progress import Progress, DownloadColumn, TransferSpeedColumn, BarColumn, TextColumn, TimeRemainingColumn

console = Console()

# Local database location
CACHE_DIR = Path.home() / ".cache" / "lastfm-analysis"
MUSICBRAINZ_DB = CACHE_DIR / "musicbrainz_releases.db"

# MusicBrainz JSON dump URL
DUMP_BASE_URL = "https://data.metabrainz.org/pub/musicbrainz/data/json-dumps"


@dataclass
class ReleaseInfo:
    """Rich release information from MusicBrainz."""
    artist: str
    title: str
    year: int
    artist_mbid: str | None = None
    release_type: str | None = None  # album, single, ep, compilation, etc.
    country: str | None = None
    language: str | None = None
    genres: list[str] | None = None
    labels: list[str] | None = None


def database_exists() -> bool:
    """Check if the MusicBrainz database has been downloaded."""
    return MUSICBRAINZ_DB.exists()


def get_connection() -> sqlite3.Connection:
    """Get a connection to the MusicBrainz database.

    Raises FileNotFoundError if the database hasn't been downloaded.
    """
    if not database_exists():
        raise FileNotFoundError(
            f"MusicBrainz database not found at {MUSICBRAINZ_DB}. "
            "Run 'lastfm metadata download' first."
        )
    return sqlite3.connect(MUSICBRAINZ_DB)


def get_latest_dump_url() -> str:
    """Get the URL for the latest release dump."""
    response = httpx.get(f"{DUMP_BASE_URL}/LATEST", follow_redirects=True)
    latest_dir = response.text.strip()
    return f"{DUMP_BASE_URL}/{latest_dir}/release.tar.xz"


def normalize(s: str) -> str:
    """Normalize string for matching."""
    return s.lower().strip() if s else ""


def init_database(db_path: Path) -> sqlite3.Connection:
    """Initialize the SQLite database with full schema."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)

    # Main releases table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS releases (
            id INTEGER PRIMARY KEY,
            artist_credit TEXT NOT NULL,
            title TEXT NOT NULL,
            year INTEGER NOT NULL,
            artist_norm TEXT NOT NULL,
            title_norm TEXT NOT NULL,
            artist_mbid TEXT,
            release_type TEXT,
            country TEXT,
            language TEXT,
            genres TEXT,
            labels TEXT
        )
    """)

    # Genre lookup table (for "find albums by genre" queries)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS release_genres (
            release_id INTEGER NOT NULL,
            genre TEXT NOT NULL,
            count INTEGER DEFAULT 0,
            FOREIGN KEY (release_id) REFERENCES releases(id)
        )
    """)

    # Label lookup table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS release_labels (
            release_id INTEGER NOT NULL,
            label_name TEXT NOT NULL,
            catalog_number TEXT,
            FOREIGN KEY (release_id) REFERENCES releases(id)
        )
    """)

    # Indexes for fast lookups
    conn.execute("CREATE INDEX IF NOT EXISTS idx_artist_title ON releases(artist_norm, title_norm)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_title ON releases(title_norm)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_artist_mbid ON releases(artist_mbid)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_release_type ON releases(release_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_country ON releases(country)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_year ON releases(year)")

    # Genre and label indexes
    conn.execute("CREATE INDEX IF NOT EXISTS idx_genre ON release_genres(genre)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_release_genre ON release_genres(release_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_label ON release_labels(label_name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_release_label ON release_labels(release_id)")

    # Clear for fresh import
    conn.execute("DELETE FROM release_genres")
    conn.execute("DELETE FROM release_labels")
    conn.execute("DELETE FROM releases")
    conn.commit()
    return conn


def extract_release_info(release: dict) -> dict | None:
    """Extract all useful fields from a release JSON object."""
    # Get release date
    date = release.get("date", "")
    if not date or len(date) < 4:
        return None

    try:
        year = int(date[:4])
    except ValueError:
        return None

    if year < 1900 or year > 2030:
        return None

    # Get title
    title = release.get("title", "")
    if not title:
        return None

    # Get artist credit
    artist_credit = release.get("artist-credit", [])
    if not artist_credit:
        return None

    # Build artist string and get primary artist MBID
    artist_parts = []
    artist_mbid = None

    for i, credit in enumerate(artist_credit):
        if isinstance(credit, dict):
            artist = credit.get("artist", {})
            if isinstance(artist, dict):
                name = artist.get("name", "")
                if name:
                    artist_parts.append(name)
                # Get MBID of primary (first) artist
                if i == 0 and not artist_mbid:
                    artist_mbid = artist.get("id")
            joinphrase = credit.get("joinphrase", "")
            if joinphrase:
                artist_parts.append(joinphrase)
        elif isinstance(credit, str):
            artist_parts.append(credit)

    artist = "".join(artist_parts).strip()
    if not artist:
        return None

    # Get release type from release-group
    release_type = None
    release_group = release.get("release-group", {})
    if release_group:
        # Primary type: album, single, ep, broadcast, other
        primary_type = release_group.get("primary-type", "")
        # Secondary types: compilation, soundtrack, live, remix, etc.
        secondary_types = release_group.get("secondary-types", [])

        if secondary_types:
            # Prefer secondary type if it's more specific
            release_type = secondary_types[0].lower()
        elif primary_type:
            release_type = primary_type.lower()

    # Get country
    country = release.get("country", "")

    # Get language from text-representation
    language = None
    text_rep = release.get("text-representation", {})
    if text_rep:
        language = text_rep.get("language", "")

    # Get genres/tags (sorted by count)
    genres = []
    tags = release.get("tags", [])
    if tags:
        # Sort by count descending
        sorted_tags = sorted(tags, key=lambda t: t.get("count", 0), reverse=True)
        genres = [(t.get("name", ""), t.get("count", 0)) for t in sorted_tags if t.get("name")]

    # Get labels
    labels = []
    label_info = release.get("label-info", [])
    for li in label_info:
        label = li.get("label", {})
        if label:
            label_name = label.get("name", "")
            catalog_num = li.get("catalog-number", "")
            if label_name:
                labels.append((label_name, catalog_num))

    return {
        "artist": artist,
        "title": title,
        "year": year,
        "artist_mbid": artist_mbid,
        "release_type": release_type,
        "country": country or None,
        "language": language or None,
        "genres": genres,  # List of (name, count) tuples
        "labels": labels,  # List of (name, catalog_number) tuples
    }


def download_and_build_database(db_path: Path = MUSICBRAINZ_DB, force_download: bool = False) -> int:
    """
    Download the MusicBrainz release dump and build local SQLite database.

    The dump file is cached locally to avoid re-downloading on subsequent runs.
    Use force_download=True to bypass the cache.

    Returns the number of releases imported.
    """
    dump_url = get_latest_dump_url()

    # Extract dump date from URL for cache filename
    # URL format: .../20251231-001001/release.tar.xz
    dump_date = dump_url.split("/")[-2]
    cache_file = CACHE_DIR / f"musicbrainz-release-{dump_date}.tar.xz"

    # Check if we have a cached version
    if cache_file.exists() and not force_download:
        console.print(f"[green]Using cached dump:[/green] {cache_file.name}")
        console.print(f"[dim]Size: {cache_file.stat().st_size / (1024**3):.2f} GB[/dim]")
        console.print(f"[dim]To re-download, delete this file or use --force[/dim]\n")
    else:
        console.print("[bold]Downloading MusicBrainz release dump...[/bold]")
        console.print(f"URL: {dump_url}")
        console.print(f"[dim]Will cache to: {cache_file.name}[/dim]\n")

        # Ensure cache directory exists
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

        # Stream download to file with progress
        with httpx.stream("GET", dump_url, follow_redirects=True, timeout=None) as response:
            total_size = int(response.headers.get("content-length", 0))

            with Progress(
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                DownloadColumn(),
                TransferSpeedColumn(),
                TimeRemainingColumn(),
                console=console,
            ) as progress:
                task = progress.add_task("Downloading...", total=total_size)

                # Write directly to file to avoid memory issues
                with open(cache_file, "wb") as f:
                    for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                        f.write(chunk)
                        progress.advance(task, len(chunk))

        console.print(f"[green]Cached to {cache_file.name}[/green]\n")

    console.print("[bold]Extracting and parsing releases...[/bold]")

    # Read from cached file
    with open(cache_file, "rb") as f:
        compressed_data = f.read()

    # Initialize database
    conn = init_database(db_path)

    # Process the tar.xz archive
    releases_imported = 0
    release_batch = []
    genre_batch = []
    label_batch = []
    batch_size = 10000
    current_id = 0

    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed:,} releases"),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Processing...", total=None)

        with tarfile.open(fileobj=BytesIO(compressed_data), mode="r:xz") as tar:
            for member in tar:
                if member.name.endswith("/release") or member.name == "release":
                    f = tar.extractfile(member)
                    if f is None:
                        continue

                    for line in f:
                        try:
                            release = json.loads(line)
                            info = extract_release_info(release)
                            if info:
                                current_id += 1

                                # Top 5 genres as comma-separated for quick display
                                top_genres = ",".join(g[0] for g in info["genres"][:5]) if info["genres"] else None
                                # Labels as comma-separated
                                top_labels = ",".join(l[0] for l in info["labels"][:3]) if info["labels"] else None

                                release_batch.append((
                                    current_id,
                                    info["artist"],
                                    info["title"],
                                    info["year"],
                                    normalize(info["artist"]),
                                    normalize(info["title"]),
                                    info["artist_mbid"],
                                    info["release_type"],
                                    info["country"],
                                    info["language"],
                                    top_genres,
                                    top_labels,
                                ))

                                # Add all genres to lookup table
                                for genre_name, genre_count in info["genres"]:
                                    genre_batch.append((current_id, genre_name.lower(), genre_count))

                                # Add all labels to lookup table
                                for label_name, catalog_num in info["labels"]:
                                    label_batch.append((current_id, label_name, catalog_num))

                                if len(release_batch) >= batch_size:
                                    conn.executemany(
                                        """INSERT INTO releases
                                           (id, artist_credit, title, year, artist_norm, title_norm,
                                            artist_mbid, release_type, country, language, genres, labels)
                                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                                        release_batch
                                    )
                                    if genre_batch:
                                        conn.executemany(
                                            "INSERT INTO release_genres (release_id, genre, count) VALUES (?, ?, ?)",
                                            genre_batch
                                        )
                                    if label_batch:
                                        conn.executemany(
                                            "INSERT INTO release_labels (release_id, label_name, catalog_number) VALUES (?, ?, ?)",
                                            label_batch
                                        )
                                    conn.commit()
                                    releases_imported += len(release_batch)
                                    progress.update(task, completed=releases_imported)
                                    release_batch = []
                                    genre_batch = []
                                    label_batch = []
                        except json.JSONDecodeError:
                            continue

        # Insert remaining batch
        if release_batch:
            conn.executemany(
                """INSERT INTO releases
                   (id, artist_credit, title, year, artist_norm, title_norm,
                    artist_mbid, release_type, country, language, genres, labels)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                release_batch
            )
            if genre_batch:
                conn.executemany(
                    "INSERT INTO release_genres (release_id, genre, count) VALUES (?, ?, ?)",
                    genre_batch
                )
            if label_batch:
                conn.executemany(
                    "INSERT INTO release_labels (release_id, label_name, catalog_number) VALUES (?, ?, ?)",
                    label_batch
                )
            conn.commit()
            releases_imported += len(release_batch)
            progress.update(task, completed=releases_imported)

    conn.close()
    console.print(f"[green]Imported {releases_imported:,} releases to {db_path}[/green]")
    return releases_imported


def lookup_release(artist: str, album: str, conn: sqlite3.Connection = None) -> ReleaseInfo | None:
    """Look up full release info from local database."""
    close_conn = False
    if conn is None:
        if not MUSICBRAINZ_DB.exists():
            return None
        conn = sqlite3.connect(MUSICBRAINZ_DB)
        close_conn = True

    try:
        # Try exact match first
        cursor = conn.execute(
            """SELECT artist_credit, title, year, artist_mbid, release_type,
                      country, language, genres, labels
               FROM releases
               WHERE artist_norm = ? AND title_norm = ?
               LIMIT 1""",
            (normalize(artist), normalize(album))
        )
        row = cursor.fetchone()

        # Try partial match on artist if no exact match
        if not row:
            cursor = conn.execute(
                """SELECT artist_credit, title, year, artist_mbid, release_type,
                          country, language, genres, labels
                   FROM releases
                   WHERE artist_norm LIKE ? AND title_norm = ?
                   LIMIT 1""",
                (f"%{normalize(artist)}%", normalize(album))
            )
            row = cursor.fetchone()

        if row:
            return ReleaseInfo(
                artist=row[0],
                title=row[1],
                year=row[2],
                artist_mbid=row[3],
                release_type=row[4],
                country=row[5],
                language=row[6],
                genres=row[7].split(",") if row[7] else None,
                labels=row[8].split(",") if row[8] else None,
            )
        return None
    finally:
        if close_conn:
            conn.close()


def lookup_release_year(artist: str, album: str, conn: sqlite3.Connection = None) -> int | None:
    """Look up just release year (for backwards compatibility)."""
    info = lookup_release(artist, album, conn)
    return info.year if info else None


def get_releases_by_genre(genre: str, limit: int = 100, conn: sqlite3.Connection = None) -> list[ReleaseInfo]:
    """Find releases by genre."""
    close_conn = False
    if conn is None:
        if not MUSICBRAINZ_DB.exists():
            return []
        conn = sqlite3.connect(MUSICBRAINZ_DB)
        close_conn = True

    try:
        cursor = conn.execute(
            """SELECT DISTINCT r.artist_credit, r.title, r.year, r.artist_mbid,
                      r.release_type, r.country, r.language, r.genres, r.labels
               FROM releases r
               JOIN release_genres rg ON r.id = rg.release_id
               WHERE rg.genre = ?
               ORDER BY rg.count DESC
               LIMIT ?""",
            (genre.lower(), limit)
        )
        return [
            ReleaseInfo(
                artist=row[0], title=row[1], year=row[2], artist_mbid=row[3],
                release_type=row[4], country=row[5], language=row[6],
                genres=row[7].split(",") if row[7] else None,
                labels=row[8].split(",") if row[8] else None,
            )
            for row in cursor.fetchall()
        ]
    finally:
        if close_conn:
            conn.close()


def get_releases_by_label(label: str, limit: int = 100, conn: sqlite3.Connection = None) -> list[ReleaseInfo]:
    """Find releases by record label."""
    close_conn = False
    if conn is None:
        if not MUSICBRAINZ_DB.exists():
            return []
        conn = sqlite3.connect(MUSICBRAINZ_DB)
        close_conn = True

    try:
        cursor = conn.execute(
            """SELECT DISTINCT r.artist_credit, r.title, r.year, r.artist_mbid,
                      r.release_type, r.country, r.language, r.genres, r.labels
               FROM releases r
               JOIN release_labels rl ON r.id = rl.release_id
               WHERE rl.label_name LIKE ?
               ORDER BY r.year DESC
               LIMIT ?""",
            (f"%{label}%", limit)
        )
        return [
            ReleaseInfo(
                artist=row[0], title=row[1], year=row[2], artist_mbid=row[3],
                release_type=row[4], country=row[5], language=row[6],
                genres=row[7].split(",") if row[7] else None,
                labels=row[8].split(",") if row[8] else None,
            )
            for row in cursor.fetchall()
        ]
    finally:
        if close_conn:
            conn.close()


def get_top_genres(limit: int = 50, conn: sqlite3.Connection = None) -> list[tuple[str, int]]:
    """Get most common genres in the database."""
    close_conn = False
    if conn is None:
        if not MUSICBRAINZ_DB.exists():
            return []
        conn = sqlite3.connect(MUSICBRAINZ_DB)
        close_conn = True

    try:
        cursor = conn.execute(
            """SELECT genre, SUM(count) as total
               FROM release_genres
               GROUP BY genre
               ORDER BY total DESC
               LIMIT ?""",
            (limit,)
        )
        return cursor.fetchall()
    finally:
        if close_conn:
            conn.close()


def get_top_labels(limit: int = 50, conn: sqlite3.Connection = None) -> list[tuple[str, int]]:
    """Get most common labels in the database."""
    close_conn = False
    if conn is None:
        if not MUSICBRAINZ_DB.exists():
            return []
        conn = sqlite3.connect(MUSICBRAINZ_DB)
        close_conn = True

    try:
        cursor = conn.execute(
            """SELECT label_name, COUNT(*) as total
               FROM release_labels
               GROUP BY label_name
               ORDER BY total DESC
               LIMIT ?""",
            (limit,)
        )
        return cursor.fetchall()
    finally:
        if close_conn:
            conn.close()


def get_database_stats() -> dict | None:
    """Get stats about the local database."""
    if not MUSICBRAINZ_DB.exists():
        return None

    conn = sqlite3.connect(MUSICBRAINZ_DB)

    cursor = conn.execute("SELECT COUNT(*) FROM releases")
    count = cursor.fetchone()[0]

    cursor = conn.execute("SELECT MIN(year), MAX(year) FROM releases")
    min_year, max_year = cursor.fetchone()

    # Check if new schema tables exist
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='release_genres'")
    has_new_schema = cursor.fetchone() is not None

    genre_count = 0
    label_count = 0
    type_breakdown = {}

    if has_new_schema:
        cursor = conn.execute("SELECT COUNT(DISTINCT genre) FROM release_genres")
        genre_count = cursor.fetchone()[0]

        cursor = conn.execute("SELECT COUNT(DISTINCT label_name) FROM release_labels")
        label_count = cursor.fetchone()[0]

        # Release type breakdown
        cursor = conn.execute("""
            SELECT release_type, COUNT(*)
            FROM releases
            WHERE release_type IS NOT NULL
            GROUP BY release_type
            ORDER BY COUNT(*) DESC
        """)
        type_breakdown = dict(cursor.fetchall())

    conn.close()

    return {
        "path": str(MUSICBRAINZ_DB),
        "releases": count,
        "year_range": (min_year, max_year),
        "unique_genres": genre_count,
        "unique_labels": label_count,
        "release_types": type_breakdown,
        "size_mb": MUSICBRAINZ_DB.stat().st_size / (1024 * 1024),
        "has_full_schema": has_new_schema,
    }
