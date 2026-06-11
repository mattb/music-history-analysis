"""MCP Server for Last.fm Music Analysis.

Exposes music taste analysis tools for LLM agents to explore narratives
around listening history, critic alignment, and recommendations.

Usage:
    # Run with default CSV auto-detection
    python -m lastfm.mcp_server

    # Run with explicit CSV path
    LASTFM_CSV=/path/to/scrobbles.csv python -m lastfm.mcp_server
"""

from typing import Optional

from fastmcp import FastMCP

from . import agent_tools
from .analysis_state import AnalysisState

# Create the MCP server
mcp = FastMCP(name="LastFM Music Analysis")


# Global state instance
_state = AnalysisState()


def _ensure_loaded():
    """Ensure data is loaded, loading lazily if needed."""
    if not _state.is_loaded():
        _state.load()


# =============================================================================
# TOOL AND RESOURCE WRAPPERS
# =============================================================================

@mcp.tool
def explore_taste_evolution(
    start_year: int = 2005,
    end_year: int = 2025,
) -> dict:
    """Analyze how musical taste has evolved over time.

    Returns discovered artists per era, abandoned artists, loyalty patterns,
    and listening intensity. Great for understanding the user's musical journey.

    Args:
        start_year: First year to analyze
        end_year: Last year to analyze"""
    _ensure_loaded()
    return agent_tools.explore_taste_evolution(_state, start_year=start_year, end_year=end_year)


@mcp.tool
def find_musical_bridges(
    artist: str,
    top_n: int = 10,
) -> dict:
    """Find artists that bridge from a known artist to new discoveries.

    Uses both user listening patterns AND critics consensus to find
    artists that connect the user's taste to unexplored territory.
    Identifies "bridge" artists that appear in both spaces.

    Args:
        artist: Artist name to find bridges from
        top_n: Number of similar artists to return per source"""
    _ensure_loaded()
    return agent_tools.find_musical_bridges(_state, artist=artist, top_n=top_n)


@mcp.tool
def discover_blind_spots(
    year: Optional[int] = None,
    min_critics: int = 3,
    limit: int = 20,
) -> dict:
    """Find critically acclaimed albums the user hasn't heard.

    Returns recommendations weighted by critic alignment - albums loved by
    critics who share the user's taste rank higher.

    Args:
        year: Specific year to check (None = all available years)
        min_critics: Minimum critics who listed the album
        limit: Maximum recommendations to return"""
    _ensure_loaded()
    return agent_tools.discover_blind_spots(_state, year=year, min_critics=min_critics, limit=limit)


@mcp.tool
def get_artist_deep_dive(artists: list[str]) -> list:
    """Complete analysis of user's relationship with one or more artists.

    Returns for each artist: first/last play, total plays, albums listened,
    similar artists in user space vs critics space, which critics champion them.

    Args:
        artists: List of artist names to analyze (can be a single-item list)"""
    _ensure_loaded()
    return agent_tools.get_artist_deep_dive(_state, artists=artists)


@mcp.tool
def find_similar_artists(
    artist: str,
    source: str = "user",
    top_n: int = 10,
) -> list:
    """Find artists similar to the given artist.

    Args:
        artist: Artist name to find similar artists for
        source: "user" (your listening patterns) or "critics" (critical consensus)
        top_n: Number of results to return"""
    _ensure_loaded()
    return agent_tools.find_similar_artists(_state, artist=artist, source=source, top_n=top_n)


@mcp.tool
def get_listening_stats(year: Optional[int] = None) -> dict:
    """Get listening statistics for a year or all time.

    Args:
        year: Specific year (None = all time)"""
    _ensure_loaded()
    return agent_tools.get_listening_stats(_state, year=year)


@mcp.tool
def get_top_artists(
    year: Optional[int] = None,
    limit: int = 20,
) -> list:
    """Get top artists by play count.

    Args:
        year: Specific year (None = all time)
        limit: Number of artists to return"""
    _ensure_loaded()
    return agent_tools.get_top_artists(_state, year=year, limit=limit)


@mcp.tool
def get_critic_alignment(limit: int = 20) -> list:
    """Find critics whose taste aligns with the user's.

    Returns critics ranked by how well their picks match your listening,
    with details about overlap.

    Args:
        limit: Number of critics to return"""
    _ensure_loaded()
    return agent_tools.get_critic_alignment(_state, limit=limit)


@mcp.tool
def get_temporal_patterns(year: int | None = None) -> dict:
    """Analyze when listening happens.

    Returns time-of-day distribution, day-of-week patterns,
    and monthly patterns. Useful for understanding listening context.

    Args:
        year: Specific year to analyze (None = all time)"""
    _ensure_loaded()
    return agent_tools.get_temporal_patterns(_state, year=year)


@mcp.tool
def get_period_summary(start_year: int, end_year: int) -> dict:
    """Get aggregated listening stats for a year range.

    Returns total plays, unique artists/albums, top artists across the period,
    year-by-year breakdown, and discovery rate.

    Args:
        start_year: First year of the period
        end_year: Last year of the period (inclusive)"""
    _ensure_loaded()
    return agent_tools.get_period_summary(_state, start_year=start_year, end_year=end_year)


@mcp.tool
def get_year_review(years: list[int] | int = 2025) -> dict | list:
    """Get comprehensive year-in-review data for one or more years.

    Returns listening stats, top artists/albums with context, new discoveries,
    critics alignment, and metadata breakdown (genres, labels, countries).
    This is the richest single view of a user's listening year.

    Args:
        years: Year or list of years to review (default: 2025)"""
    _ensure_loaded()
    return agent_tools.get_year_review(_state, years=years)


@mcp.tool
def get_listening_by_release_era(
    release_start: int,
    release_end: int,
    limit: int = 50,
) -> dict:
    """Get all plays of music released in a specific era.

    Answers: "What's my relationship with music from the 90s?"
    Uses MusicBrainz release years to filter.

    Args:
        release_start: First release year to include
        release_end: Last release year to include (inclusive)
        limit: Maximum albums to return"""
    _ensure_loaded()
    return agent_tools.get_listening_by_release_era(_state, release_start=release_start, release_end=release_end, limit=limit)


@mcp.tool
def get_common_transitions(artist: str, top_n: int = 10) -> dict:
    """Find what typically plays before and after an artist.

    Returns common predecessors and successors based on
    sequential plays within listening sessions.

    Args:
        artist: Artist to analyze transitions for
        top_n: Number of top transitions to return"""
    _ensure_loaded()
    return agent_tools.get_common_transitions(_state, artist=artist, top_n=top_n)


@mcp.tool
def get_discovery_context(artist: str) -> dict:
    """Understand how an artist was discovered.

    Returns: what played in same session as first listen,
    what played in days before/after, any patterns.

    Args:
        artist: Artist to get discovery context for"""
    _ensure_loaded()
    return agent_tools.get_discovery_context(_state, artist=artist)


@mcp.tool
def explore_critics_world(year: Optional[int] = None) -> dict:
    """Your complete relationship with music criticism in one call.

    Returns everything needed to tell the story of how your taste aligns
    with critics: overall stats, taste-twin critics with their picks,
    weighted recommendations, and albums where you matched the critics.

    Args:
        year: Focus on a specific year (None = all years 2011-2025)"""
    _ensure_loaded()
    return agent_tools.explore_critics_world(_state, year=year)


@mcp.tool
def get_album_acclaim(artist: str, album: str, year: Optional[int] = None) -> dict:
    """The critical story of a specific album.

    Returns all critics who listed this album, your listening relationship
    with it, and similar acclaimed albums you might like.

    Args:
        artist: Artist name
        album: Album name
        year: Filter to a specific year's lists (None = all years)"""
    _ensure_loaded()
    return agent_tools.get_album_acclaim(_state, artist=artist, album=album, year=year)


@mcp.tool
def get_my_validated_albums(year: Optional[int] = None, limit: int = 50) -> dict:
    """Albums where your taste matched the critics.

    Returns albums you've listened to that were also critically acclaimed,
    with rich context about your relationship with each.

    Args:
        year: Filter to albums from a specific critics year (None = all years)
        limit: Maximum albums to return"""
    _ensure_loaded()
    return agent_tools.get_my_validated_albums(_state, year=year, limit=limit)


@mcp.tool
def get_critic_profile(critic_name: str, year: Optional[int] = None) -> dict:
    """Deep dive on a specific critic's taste vs yours.

    Returns their picks, your overlap, their recommendations for you,
    and their signature artists.

    Args:
        critic_name: Name of the critic to analyze
        year: Focus on a specific year (None = all years)"""
    _ensure_loaded()
    return agent_tools.get_critic_profile(_state, critic_name=critic_name, year=year)


@mcp.tool
def search_critics_for_artist(artist: str, year: Optional[int] = None) -> dict:
    """An artist's complete critical history.

    Returns all albums by this artist that were critically acclaimed,
    which critics championed them, and your relationship with the artist.

    Args:
        artist: Artist name to search for
        year: Filter to a specific year (None = all years)"""
    _ensure_loaded()
    return agent_tools.search_critics_for_artist(_state, artist=artist, year=year)


@mcp.tool
def get_obsession_tracks(
    year: Optional[int] = None,
    min_plays: int = 20,
) -> dict:
    """Find tracks you obsessed over without exploring their albums.

    Returns tracks with high play counts where album familiarity is low -
    songs you put on repeat but never explored further.

    Args:
        year: Filter to specific year (None = all time)
        min_plays: Minimum plays for a track to be considered (default: 20)"""
    _ensure_loaded()
    return agent_tools.get_obsession_tracks(_state, year=year, min_plays=min_plays)


@mcp.tool
def get_one_track_artists(
    year: Optional[int] = None,
    min_concentration: float = 0.7,
) -> dict:
    """Find artists where one track dominates your listening.

    Returns artists where you've only really engaged with a single song -
    your "one-hit" relationships.

    Args:
        year: Filter to specific year (None = all time)
        min_concentration: Min % of plays on top track (default: 0.7 = 70%)"""
    _ensure_loaded()
    return agent_tools.get_one_track_artists(_state, year=year, min_concentration=min_concentration)


@mcp.tool
def get_ep_single_artists(year: Optional[int] = None) -> dict:
    """Find artists where you mainly listen to EPs/singles, not albums.

    Returns artists who primarily release EPs and singles rather than
    traditional albums - typical for electronic producers and remixers.

    Requires MusicBrainz database to be downloaded.

    Args:
        year: Filter to specific year (None = all time)"""
    _ensure_loaded()
    return agent_tools.get_ep_single_artists(_state, year=year)


@mcp.resource("overview://summary")
def get_overview() -> dict:
    """Full listening overview: stats, top artists, listening timeline."""
    _ensure_loaded()
    return agent_tools.get_overview(_state)


@mcp.resource("artists://discovered/{year}")
def get_discovered_artists(year: int) -> list:
    """Artists first played in a given year."""
    _ensure_loaded()
    return agent_tools.get_discovered_artists(_state, year=year)


@mcp.resource("critics://lists/{year}")
def get_critics_lists(year: int) -> dict:
    """Critics' year-end lists for a given year."""
    _ensure_loaded()
    return agent_tools.get_critics_lists(_state, year=year)

# =============================================================================
# PROMPTS - Agent playbooks
# =============================================================================

@mcp.prompt
def taste_journey() -> str:
    """Guide for exploring a user's 20-year musical journey."""
    return """Explore this user's musical taste evolution:

1. Start with explore_taste_evolution() to understand the arc
2. Identify key "eras" and pivotal discoveries
3. Use find_musical_bridges() on artists from each era
4. Look at get_artist_deep_dive() for the most significant artists
5. Weave a narrative about how their taste developed

Key questions to answer:
- What were the turning points in their musical journey?
- Which artists served as gateways to new genres?
- What patterns emerge in their discoveries vs abandonments?
"""


@mcp.prompt
def recommendation_session(mood: str = "adventurous") -> str:
    """Guide for a personalized recommendation session."""
    return f"""Run a {mood} recommendation session:

1. Use get_critic_alignment() to find taste-aligned critics
2. Use discover_blind_spots() to find unheard acclaimed albums
3. For each recommendation, use find_musical_bridges() to explain WHY
4. Use get_artist_deep_dive() to show connections to known artists

Mood: {mood}
- "adventurous" = prioritize less obvious picks
- "safe" = prioritize highly acclaimed, similar to favorites
- "nostalgic" = focus on artists similar to early discoveries
"""


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    # Pre-load data before starting server
    _ensure_loaded()
    mcp.run()
