"""
Run with: pytest backend/tests/test_call_graph.py -v
(from the backend/ directory, or with backend/ on PYTHONPATH)
"""
from app.graphs.call_graph import CallGraph
from app.parsing.java_bridge import CallEdge, MethodInfo


def _method(id_: str) -> MethodInfo:
    class_id = id_.split("#", 1)[0]
    return MethodInfo(
        id=id_,
        class_id=class_id,
        name=id_.split("#", 1)[1].split("(")[0],
        signature=id_.split("#", 1)[1],
        file_path=f"{class_id.replace('.', '/')}.java",
        start_line=1,
        end_line=10,
        modifiers=["public"],
    )


# A -> B -> C -> D chain, plus an isolated method E with no edges.
_METHOD_IDS = [
    "com.foo.A#a()",
    "com.foo.B#b()",
    "com.foo.C#c()",
    "com.foo.D#d()",
    "com.foo.E#e()",
]


def _build_chain_graph() -> CallGraph:
    methods = [_method(m) for m in _METHOD_IDS]
    edges = [
        CallEdge(caller_id="com.foo.A#a()", callee_id="com.foo.B#b()"),
        CallEdge(caller_id="com.foo.B#b()", callee_id="com.foo.C#c()"),
        CallEdge(caller_id="com.foo.C#c()", callee_id="com.foo.D#d()"),
    ]
    return CallGraph.build(methods, edges)


def test_anchor_itself_scores_one():
    graph = _build_chain_graph()
    score = graph.proximity("com.foo.A#a()", anchor_ids={"com.foo.A#a()"}, max_hops=4)
    assert score == 1.0


def test_proximity_decays_linearly_with_hops():
    graph = _build_chain_graph()
    anchors = {"com.foo.A#a()"}
    # B is 1 hop from A, C is 2 hops, D is 3 hops (undirected shortest path).
    score_b = graph.proximity("com.foo.B#b()", anchors, max_hops=4)
    score_c = graph.proximity("com.foo.C#c()", anchors, max_hops=4)
    score_d = graph.proximity("com.foo.D#d()", anchors, max_hops=4)

    assert score_b == 1.0 - 1 / 4
    assert score_c == 1.0 - 2 / 4
    assert score_d == 1.0 - 3 / 4
    assert score_b > score_c > score_d


def test_proximity_zero_beyond_max_hops():
    graph = _build_chain_graph()
    # D is 3 hops from A; with max_hops=2 it should be unreachable within
    # the cutoff and therefore score 0, not a negative number.
    score = graph.proximity("com.foo.D#d()", anchor_ids={"com.foo.A#a()"}, max_hops=2)
    assert score == 0.0


def test_isolated_method_scores_zero_against_any_anchor():
    graph = _build_chain_graph()
    score = graph.proximity("com.foo.E#e()", anchor_ids={"com.foo.A#a()"}, max_hops=4)
    assert score == 0.0


def test_empty_anchor_set_scores_zero():
    graph = _build_chain_graph()
    score = graph.proximity("com.foo.B#b()", anchor_ids=set(), max_hops=4)
    assert score == 0.0


def test_unknown_method_id_scores_zero_without_raising():
    graph = _build_chain_graph()
    score = graph.proximity("com.foo.Ghost#ghost()", anchor_ids={"com.foo.A#a()"}, max_hops=4)
    assert score == 0.0


def test_call_edges_to_unresolved_methods_are_skipped():
    """A call to something outside the parsed set (e.g. a JDK method the
    symbol solver couldn't map to repo source) must not create a dangling
    node -- see CallGraph.build's docstring."""
    methods = [_method("com.foo.A#a()")]
    edges = [CallEdge(caller_id="com.foo.A#a()", callee_id="java.util.List#add(Object)")]
    graph = CallGraph.build(methods, edges)

    assert len(graph) == 1
    assert graph.num_edges == 0
    assert not graph.contains("java.util.List#add(Object)")


def test_neighbors_within_hops():
    graph = _build_chain_graph()
    neighbors = graph.neighbors_within_hops("com.foo.B#b()", hops=1)
    assert neighbors == {"com.foo.A#a()", "com.foo.C#c()"}


def test_callers_within_hops():
    graph = _build_chain_graph()
    # A -> B -> C -> D
    # Callers of C within 1 hop: only B (not D, because D is a callee)
    callers_1 = graph.callers_within_hops("com.foo.C#c()", hops=1)
    assert callers_1 == {"com.foo.B#b()"}
    # Callers of C within 2 hops: B and A
    callers_2 = graph.callers_within_hops("com.foo.C#c()", hops=2)
    assert callers_2 == {"com.foo.B#b()", "com.foo.A#a()"}