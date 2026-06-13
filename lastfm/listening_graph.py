"""Deterministic artist co-listening graph analytics."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from itertools import combinations
from typing import Any, Literal

import networkx as nx
import pandas as pd

from . import crossref, data


@dataclass(frozen=True)
class GraphConfig:
    gap_minutes: int = 30
    min_artist_plays: int = 10
    min_shared_sessions: int = 2
    start_year: int | None = None
    end_year: int | None = None
    community_resolution: float = 1.0
    community_seed: int = 0
    betweenness_samples: int = 100


def _validate_config(config: GraphConfig) -> None:
    positive = {
        "gap_minutes": config.gap_minutes,
        "min_artist_plays": config.min_artist_plays,
        "min_shared_sessions": config.min_shared_sessions,
        "community_resolution": config.community_resolution,
        "betweenness_samples": config.betweenness_samples,
    }
    for name, value in positive.items():
        if value <= 0:
            raise ValueError(f"{name} must be positive")
    if config.start_year is not None and config.end_year is not None:
        if config.start_year > config.end_year:
            raise ValueError("start_year must not exceed end_year")


def _validate(config: GraphConfig, hops: int, output_format: str) -> None:
    _validate_config(config)
    if hops <= 0:
        raise ValueError("hops must be positive")
    if output_format not in {"json", "graphml"}:
        raise ValueError("output_format must be json or graphml")


def _identity(name: Any, mbid: Any) -> str | None:
    if pd.notna(mbid) and str(mbid).strip():
        return f"mbid:{str(mbid).strip().lower()}"
    normalized = crossref.normalize_for_matching(str(name)) if pd.notna(name) else ""
    return f"name:{normalized}" if normalized else None


def _display_name(spellings: Counter[str]) -> str:
    highest = max(spellings.values())
    return min(name for name, count in spellings.items() if count == highest)


def build_session_graph(
    df: pd.DataFrame, config: GraphConfig
) -> tuple[nx.Graph, dict[str, int]]:
    """Build a graph after time filtering and whole-stream session detection."""
    _validate_config(config)
    filtered = df.copy()
    if "timestamp" not in filtered:
        filtered["timestamp"] = pd.Series(dtype="datetime64[ns, UTC]")
    if config.start_year is not None:
        filtered = filtered[filtered["timestamp"].dt.year >= config.start_year]
    if config.end_year is not None:
        filtered = filtered[filtered["timestamp"].dt.year <= config.end_year]
    filtered = filtered.sort_values("timestamp", kind="mergesort").copy()
    if "artist_mbid" not in filtered:
        filtered["artist_mbid"] = ""
    if "artist" not in filtered:
        filtered["artist"] = ""
    filtered["node_id"] = [
        _identity(name, mbid)
        for name, mbid in zip(filtered["artist"], filtered["artist_mbid"])
    ]
    empty_artist = int(filtered["node_id"].isna().sum())
    # Sessionize the complete time-filtered stream. Even an unusable artist row
    # is evidence that the listening session remained active at that moment.
    sessionized = data.detect_sessions(filtered, gap_minutes=config.gap_minutes)
    identified = filtered[filtered["node_id"].notna()].copy()

    play_counts = identified["node_id"].value_counts()
    eligible = set(play_counts[play_counts >= config.min_artist_plays].index)
    spellings: dict[str, Counter[str]] = defaultdict(Counter)
    for node_id, name in zip(identified["node_id"], identified["artist"]):
        spellings[node_id][str(name)] += 1

    # Sessionize first: ineligible artists still bridge gaps in the original stream.
    pair_counts: Counter[tuple[str, str]] = Counter()
    artist_sessions: dict[str, set[int]] = defaultdict(set)
    for session_id, group in sessionized.groupby("session_id", sort=True):
        artists = sorted(set(group["node_id"]) & eligible)
        for artist in artists:
            artist_sessions[artist].add(int(session_id))
        pair_counts.update(combinations(artists, 2))

    graph = nx.Graph()
    for node_id in sorted(eligible):
        aliases = sorted(spellings[node_id])
        graph.add_node(
            node_id,
            name=_display_name(spellings[node_id]),
            aliases=aliases,
            plays=int(play_counts[node_id]),
            session_count=len(artist_sessions[node_id]),
        )
    for (source, target), count in sorted(pair_counts.items()):
        if count < config.min_shared_sessions:
            continue
        union = len(artist_sessions[source] | artist_sessions[target])
        graph.add_edge(
            source,
            target,
            shared_sessions=int(count),
            weight=int(count),
            distance=1.0 / count,
            jaccard=count / union,
        )
    diagnostics = {
        "sessions": int(sessionized["session_id"].nunique()),
        "empty_artist": empty_artist,
        "source_plays": len(filtered),
        "artists_before_threshold": int(identified["node_id"].nunique()),
        "artists_below_play_threshold": int(len(play_counts) - len(eligible)),
        "edges_below_shared_session_threshold": sum(
            count < config.min_shared_sessions for count in pair_counts.values()
        ),
    }
    return graph, diagnostics


def graph_metrics(graph: nx.Graph, config: GraphConfig) -> dict[str, dict[str, Any]]:
    """Calculate communities and unlabelled structural measurements."""
    _validate_config(config)
    if not graph:
        return {}
    canonical = nx.Graph()
    canonical.add_nodes_from((node, graph.nodes[node]) for node in sorted(graph))
    canonical.add_edges_from(
        (source, target, graph[source][target])
        for source, target in sorted(tuple(sorted(edge)) for edge in graph.edges)
    )
    if canonical.number_of_edges():
        communities = list(
            nx.community.louvain_communities(
                canonical,
                weight="weight",
                resolution=config.community_resolution,
                seed=config.community_seed,
            )
        )
    else:
        communities = [{node} for node in sorted(canonical)]
    communities.sort(key=lambda members: min(members))
    community_by_node = {
        node: index for index, members in enumerate(communities) for node in members
    }
    sample_count = min(config.betweenness_samples, len(canonical))
    between = nx.betweenness_centrality(
        canonical,
        k=sample_count if sample_count < len(canonical) else None,
        weight="distance",
        seed=config.community_seed,
    )
    closeness = nx.closeness_centrality(canonical, distance="distance")
    articulation = set(nx.articulation_points(canonical))
    degree_centrality = nx.degree_centrality(canonical)
    result = {}
    for node in sorted(canonical):
        strength_by_community: Counter[int] = Counter()
        for neighbor, edge in canonical[node].items():
            strength_by_community[community_by_node[neighbor]] += edge.get("weight", 1)
        strength = sum(strength_by_community.values())
        participation = (
            0.0
            if strength == 0
            else 1.0
            - sum((value / strength) ** 2 for value in strength_by_community.values())
        )
        result[node] = {
            "degree": int(canonical.degree(node)),
            "strength": int(strength),
            "degree_centrality": float(degree_centrality[node]),
            "betweenness_centrality": float(between[node]),
            "closeness_centrality": float(closeness[node]),
            "participation_coefficient": float(participation),
            "articulation_point": node in articulation,
            "community_id": int(community_by_node[node]),
        }
    return result


def _rounded(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 12) if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: _rounded(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_rounded(item) for item in value]
    return value


def _focus_graph(graph: nx.Graph, focus_artist: str | None, hops: int) -> nx.Graph:
    if focus_artist is None:
        return graph.copy()
    matches = [
        node
        for node, attrs in graph.nodes(data=True)
        if attrs["name"].casefold() == focus_artist.casefold()
    ]
    if len(matches) != 1:
        raise ValueError("focus artist not found")
    distances = nx.single_source_shortest_path_length(graph, matches[0], cutoff=hops)
    return graph.subgraph(sorted(distances)).copy()


def _enriched_graph(graph: nx.Graph, metrics: dict[str, dict[str, Any]]) -> nx.Graph:
    enriched = nx.Graph()
    for node in sorted(graph):
        attrs = dict(graph.nodes[node])
        attrs["aliases"] = "|".join(attrs["aliases"])
        attrs.update(metrics[node])
        enriched.add_node(node, **_rounded(attrs))
    for source, target in sorted(tuple(sorted(edge)) for edge in graph.edges):
        enriched.add_edge(source, target, **_rounded(dict(graph[source][target])))
    return enriched


def analyze_listening_graph(
    df: pd.DataFrame,
    config: GraphConfig = GraphConfig(),
    focus_artist: str | None = None,
    hops: int = 1,
    output_format: Literal["json", "graphml"] = "json",
) -> dict[str, Any]:
    """Build, measure, optionally focus, and serialize a listening graph."""
    _validate(config, hops, output_format)
    graph, diagnostics = build_session_graph(df, config)
    metrics = graph_metrics(graph, config)
    selected = _focus_graph(graph, focus_artist, hops)
    enriched = _enriched_graph(selected, metrics)
    if output_format == "graphml":
        content = "\n".join(nx.generate_graphml(enriched, named_key_ids=True))
        return {"schema_version": 1, "format": "graphml", "content": content}

    community_ids = sorted({metrics[node]["community_id"] for node in selected})
    communities = [
        {
            "community_id": community_id,
            "nodes": sorted(
                node
                for node in selected
                if metrics[node]["community_id"] == community_id
            ),
        }
        for community_id in community_ids
    ]
    nodes = []
    for node in sorted(selected):
        nodes.append({"id": node, **selected.nodes[node], **metrics[node]})
    edges = []
    for source, target in sorted(tuple(sorted(edge)) for edge in selected.edges):
        edges.append({"source": source, "target": target, **selected[source][target]})
    source_years = df["timestamp"].dt.year if len(df) and "timestamp" in df else []
    result = {
        "schema_version": 1,
        "graph_type": "artist_session_cooccurrence",
        "parameters": {**asdict(config), "focus_artist": focus_artist, "hops": hops},
        "source": {
            "plays": int(len(df)),
            "start_year": int(min(source_years)) if len(source_years) else None,
            "end_year": int(max(source_years)) if len(source_years) else None,
        },
        "summary": {
            "nodes": len(selected),
            "edges": selected.number_of_edges(),
            "communities": len(community_ids),
        },
        "communities": communities,
        "nodes": nodes,
        "edges": edges,
        "diagnostics": diagnostics,
    }
    return _rounded(result)
