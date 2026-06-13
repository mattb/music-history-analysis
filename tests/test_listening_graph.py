import json

import networkx as nx
import pandas as pd
import pytest

from lastfm.listening_graph import (
    GraphConfig,
    analyze_listening_graph,
    build_session_graph,
    graph_metrics,
)


def rows(items):
    frame = pd.DataFrame(items, columns=["timestamp", "artist", "artist_mbid"])
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    return frame


def config(**kwargs):
    values = {"min_artist_plays": 1, "min_shared_sessions": 1, **kwargs}
    return GraphConfig(**values)


def test_session_boundary_and_repeated_pair_count_once():
    frame = rows(
        [
            ("2024-01-01T00:00:00Z", "A", ""),
            ("2024-01-01T00:03:00Z", "A", ""),
            ("2024-01-01T00:30:00Z", "B", ""),
            ("2024-01-01T01:00:01Z", "A", ""),
        ]
    )
    graph, diagnostics = build_session_graph(frame, config())
    assert graph["name:a"]["name:b"]["shared_sessions"] == 1
    assert diagnostics["sessions"] == 2


def test_identity_aliases_thresholds_and_isolates():
    frame = rows(
        [
            ("2024-01-01T00:00:00Z", "The A", "ABC"),
            ("2024-01-01T00:01:00Z", "the a", "abc"),
            ("2024-01-01T00:02:00Z", "THE A", ""),
            ("2024-01-01T00:03:00Z", "Rare", ""),
            ("2024-01-01T01:00:00Z", "The A", "ABC"),
            ("2024-01-01T01:01:00Z", "The A", "ABC"),
            ("2024-01-01T01:02:00Z", "THE A", ""),
        ]
    )
    graph, _ = build_session_graph(
        frame, GraphConfig(min_artist_plays=2, min_shared_sessions=3)
    )
    assert sorted(graph) == ["mbid:abc", "name:a"]
    assert graph.nodes["mbid:abc"]["name"] == "The A"
    assert graph.nodes["mbid:abc"]["aliases"] == ["The A", "the a"]
    assert graph.degree("mbid:abc") == 0


def test_year_filtering_happens_before_sessionization_and_filtering_does_not_split():
    frame = rows(
        [
            ("2023-12-31T23:50:00Z", "A", ""),
            ("2024-01-01T00:00:00Z", "A", ""),
            ("2024-01-01T00:20:00Z", "Rare", ""),
            ("2024-01-01T00:40:00Z", "B", ""),
        ]
    )
    graph, diagnostics = build_session_graph(
        frame,
        GraphConfig(
            gap_minutes=30,
            min_artist_plays=1,
            min_shared_sessions=1,
            start_year=2024,
            end_year=2024,
        ),
    )
    assert diagnostics["source_plays"] == 3
    assert graph.has_edge("name:a", "name:b")


def test_empty_artist_row_does_not_split_original_session():
    frame = rows(
        [
            ("2024-01-01T00:00:00Z", "A", ""),
            ("2024-01-01T00:20:00Z", "", ""),
            ("2024-01-01T00:40:00Z", "B", ""),
        ]
    )
    graph, diagnostics = build_session_graph(frame, config())
    assert graph.has_edge("name:a", "name:b")
    assert diagnostics["empty_artist"] == 1
    assert diagnostics["source_plays"] == 3


def test_shuffled_input_is_deterministic():
    frame = rows(
        [
            ("2024-01-01T00:00:00Z", "A", ""),
            ("2024-01-01T00:01:00Z", "B", ""),
            ("2024-01-01T01:00:00Z", "B", ""),
            ("2024-01-01T01:01:00Z", "C", ""),
        ]
    )
    first = analyze_listening_graph(frame, config())
    second = analyze_listening_graph(frame.sample(frac=1, random_state=4), config())
    assert first == second
    assert json.dumps(first, allow_nan=False, sort_keys=True)


def test_metrics_cover_communities_centrality_and_participation():
    graph = nx.Graph()
    for source, target in [("a", "b"), ("b", "c"), ("c", "d")]:
        graph.add_edge(source, target, weight=1, distance=1.0)
    graph.add_node("z")
    metrics = graph_metrics(graph, config())
    assert metrics["b"]["articulation_point"] is True
    assert (
        metrics["b"]["betweenness_centrality"] > metrics["a"]["betweenness_centrality"]
    )
    assert metrics["z"]["participation_coefficient"] == 0.0
    assert isinstance(metrics["a"]["community_id"], int)
    reverse = nx.Graph()
    reverse.add_nodes_from(reversed(list(graph.nodes)))
    reverse.add_edges_from(reversed(list(graph.edges(data=True))))
    assert graph_metrics(reverse, config()) == metrics


def test_disconnected_cliques_have_distinct_deterministic_communities():
    graph = nx.Graph()
    graph.add_edges_from([("a", "b"), ("a", "c"), ("b", "c")])
    graph.add_edges_from([("d", "e"), ("d", "f"), ("e", "f")])
    nx.set_edge_attributes(graph, 1, "weight")
    nx.set_edge_attributes(graph, 1.0, "distance")
    first = graph_metrics(graph, config())
    assert first == graph_metrics(graph.copy(), config())
    assert {first[node]["community_id"] for node in "abc"} == {0}
    assert {first[node]["community_id"] for node in "def"} == {1}


def test_exact_metrics_and_weighted_distance_behavior():
    path = nx.Graph()
    path.add_edge("a", "b", weight=1, distance=1.0)
    path.add_edge("b", "c", weight=1, distance=1.0)
    metrics = graph_metrics(path, config())
    assert metrics["b"]["degree"] == 2
    assert metrics["b"]["strength"] == 2
    assert metrics["b"]["degree_centrality"] == 1.0
    assert metrics["b"]["closeness_centrality"] == 1.0
    assert metrics["a"]["closeness_centrality"] == pytest.approx(2 / 3)

    weighted = nx.Graph()
    weighted.add_edge("a", "b", weight=10, distance=0.1)
    weighted.add_edge("b", "c", weight=10, distance=0.1)
    weighted.add_edge("a", "c", weight=1, distance=1.0)
    assert graph_metrics(weighted, config())["b"]["betweenness_centrality"] == 1.0


def test_participation_is_nonzero_across_communities():
    graph = nx.Graph()
    for source, target in [
        ("a", "b"),
        ("a", "c"),
        ("b", "c"),
        ("d", "e"),
        ("d", "f"),
        ("e", "f"),
    ]:
        graph.add_edge(source, target, weight=5, distance=0.2)
    graph.add_edge("c", "d", weight=1, distance=1.0)
    metrics = graph_metrics(graph, config())
    assert metrics["c"]["community_id"] != metrics["d"]["community_id"]
    assert metrics["c"]["participation_coefficient"] > 0


def test_json_schema_edges_neighborhood_and_graphml():
    frame = rows(
        [
            ("2024-01-01T00:00:00Z", "A", ""),
            ("2024-01-01T00:01:00Z", "B", ""),
            ("2024-01-01T01:00:00Z", "B", ""),
            ("2024-01-01T01:01:00Z", "C", ""),
        ]
    )
    result = analyze_listening_graph(frame, config(), focus_artist="A", hops=1)
    assert list(result) == [
        "schema_version",
        "graph_type",
        "parameters",
        "source",
        "summary",
        "communities",
        "nodes",
        "edges",
        "diagnostics",
    ]
    assert [node["name"] for node in result["nodes"]] == ["A", "B"]
    assert all(edge["source"] < edge["target"] for edge in result["edges"])
    payload = analyze_listening_graph(frame, config(), output_format="graphml")
    assert payload["format"] == "graphml"
    parsed = nx.parse_graphml(payload["content"])
    assert set(parsed) == {"name:a", "name:b", "name:c"}
    with pytest.raises(ValueError, match="focus artist not found"):
        analyze_listening_graph(frame, config(), focus_artist="missing")


def test_compact_json_is_stable_and_floats_are_rounded_to_twelve_places():
    frame = rows(
        [
            ("2024-01-01T00:00:00Z", "A", ""),
            ("2024-01-01T00:01:00Z", "B", ""),
            ("2024-01-01T01:00:00Z", "A", ""),
            ("2024-01-01T02:00:00Z", "B", ""),
        ]
    )
    first = analyze_listening_graph(frame, config())
    second = analyze_listening_graph(frame.sample(frac=1, random_state=9), config())
    serialized = json.dumps(first, separators=(",", ":"), allow_nan=False)
    assert serialized == json.dumps(second, separators=(",", ":"), allow_nan=False)
    assert first["edges"][0]["jaccard"] == 0.333333333333


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"hops": 0}, "hops must be positive"),
        ({"output_format": "gexf"}, "output_format must be json or graphml"),
    ],
)
def test_invalid_analysis_options(kwargs, message):
    with pytest.raises(ValueError, match=message):
        analyze_listening_graph(rows([]), config(), **kwargs)


@pytest.mark.parametrize("resolution", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_community_resolution_is_rejected(resolution):
    with pytest.raises(
        ValueError, match="community_resolution must be finite and positive"
    ):
        analyze_listening_graph(rows([]), config(community_resolution=resolution))


def test_focus_artist_reports_ambiguous_exact_display_matches():
    frame = rows(
        [
            ("2024-01-01T00:00:00Z", "Same", "ABC"),
            ("2024-01-01T00:01:00Z", "Same", ""),
        ]
    )
    with pytest.raises(
        ValueError,
        match=r"focus artist is ambiguous: mbid:abc, name:same",
    ):
        analyze_listening_graph(frame, config(), focus_artist="same")


def test_public_graph_builders_validate_positive_config():
    with pytest.raises(ValueError, match="gap_minutes must be positive"):
        build_session_graph(rows([]), config(gap_minutes=0))
    with pytest.raises(ValueError, match="betweenness_samples must be positive"):
        graph_metrics(nx.Graph(), config(betweenness_samples=0))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"gap_minutes": 0},
        {"min_artist_plays": 0},
        {"min_shared_sessions": 0},
        {"community_resolution": 0},
        {"betweenness_samples": 0},
        {"start_year": 2025, "end_year": 2024},
    ],
)
def test_invalid_config(kwargs):
    with pytest.raises(ValueError):
        analyze_listening_graph(rows([]), GraphConfig(**kwargs))


def test_empty_and_edgeless_graphs():
    empty = analyze_listening_graph(rows([]), config())
    assert empty["summary"]["nodes"] == 0
    assert empty["communities"] == []
    singleton = rows([("2024-01-01T00:00:00Z", "A", "")])
    result = analyze_listening_graph(singleton, config(min_shared_sessions=2))
    assert result["summary"] == {"nodes": 1, "edges": 0, "communities": 1}
