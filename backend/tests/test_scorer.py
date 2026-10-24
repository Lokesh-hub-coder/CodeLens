"""
Run with: pytest backend/tests/test_scorer.py -v
"""
import pytest

from app.acor.scorer import Candidate, _min_max_normalize, _resolve_anchors, score_candidates
from app.config import ACORWeights
from app.graphs.call_graph import CallGraph
from app.graphs.dependency_graph import DependencyGraph
from app.graphs.symbol_index import SymbolIndex
from app.parsing.java_bridge import CallEdge, ClassInfo, ImportEdge, MethodInfo
from app.retrieval.query_understanding import QueryAnalysis, QueryIntentLabel


# ---------------------------------------------------------------------------
# _min_max_normalize
# ---------------------------------------------------------------------------

def test_min_max_normalize_spans_full_range():
    result = _min_max_normalize([0.60, 0.70, 0.80])
    assert result == pytest.approx([0.0, 0.5, 1.0])


def test_min_max_normalize_constant_values_do_not_divide_by_zero():
    # All candidates equally similar -- must not raise, and must not invent
    # a fake spread (everyone gets full credit for the one thing they share).
    result = _min_max_normalize([0.5, 0.5, 0.5])
    assert result == [1.0, 1.0, 1.0]


def test_min_max_normalize_empty_input():
    assert _min_max_normalize([]) == []


# ---------------------------------------------------------------------------
# Fixtures: a tiny 2-class, 2-method synthetic repo
# ---------------------------------------------------------------------------

def _build_fixture():
    classes = [
        ClassInfo(
            id="com.foo.OrderService",
            file_path="com/foo/OrderService.java",
            name="OrderService",
            package="com.foo",
            start_line=1,
            end_line=50,
            imports=["com.foo.PaymentGateway"],
        ),
        ClassInfo(
            id="com.foo.PaymentGateway",
            file_path="com/foo/PaymentGateway.java",
            name="PaymentGateway",
            package="com.foo",
            start_line=1,
            end_line=30,
            imports=[],
        ),
    ]
    methods = [
        MethodInfo(
            id="com.foo.OrderService#processOrder(Order)",
            class_id="com.foo.OrderService",
            name="processOrder",
            signature="processOrder(Order)",
            file_path="com/foo/OrderService.java",
            start_line=10,
            end_line=25,
            modifiers=["public"],
        ),
        MethodInfo(
            id="com.foo.PaymentGateway#charge(double)",
            class_id="com.foo.PaymentGateway",
            name="charge",
            signature="charge(double)",
            file_path="com/foo/PaymentGateway.java",
            start_line=5,
            end_line=15,
            modifiers=["public"],
        ),
    ]
    call_graph = CallGraph.build(
        methods,
        [CallEdge(caller_id="com.foo.OrderService#processOrder(Order)", callee_id="com.foo.PaymentGateway#charge(double)")],
    )
    dependency_graph = DependencyGraph.build(
        classes,
        [ImportEdge(from_class_id="com.foo.OrderService", to_class_id="com.foo.PaymentGateway")],
    )
    symbol_index = SymbolIndex.build(classes, methods)
    return methods, call_graph, dependency_graph, symbol_index


def _candidate(method: MethodInfo, semantic: float) -> Candidate:
    return Candidate(
        method_id=method.id,
        class_id=method.class_id,
        name=method.name,
        kind="method",
        file_path=method.file_path,
        start_line=method.start_line,
        end_line=method.end_line,
        snippet=f"void {method.name}() {{ /* body */ }}",
        token_count=20,
        raw_semantic_score=semantic,
    )


# ---------------------------------------------------------------------------
# _resolve_anchors
# ---------------------------------------------------------------------------

def test_resolve_anchors_maps_method_name_to_method_and_class_ids():
    _, _, _, symbol_index = _build_fixture()
    method_anchors, class_anchors = _resolve_anchors({"processOrder"}, symbol_index)
    assert method_anchors == {"com.foo.OrderService#processOrder(Order)"}
    assert class_anchors == {"com.foo.OrderService"}


def test_resolve_anchors_ignores_unknown_symbols():
    _, _, _, symbol_index = _build_fixture()
    method_anchors, class_anchors = _resolve_anchors({"someUnknownThing"}, symbol_index)
    assert method_anchors == set()
    assert class_anchors == set()


# ---------------------------------------------------------------------------
# score_candidates end to end
# ---------------------------------------------------------------------------

def test_exact_symbol_match_and_call_graph_proximity_boost_the_anchor_method():
    methods, call_graph, dependency_graph, symbol_index = _build_fixture()
    candidates = [_candidate(m, semantic=0.7) for m in methods]  # tie on semantic score

    query = QueryAnalysis(
        raw_query="why does processOrder fail",
        intent=QueryIntentLabel.ROOT_CAUSE_ANALYSIS,
        anchor_symbols={"processOrder"},
        query_tokens={"process", "order"},
    )
    weights = ACORWeights(semantic=0.25, call_graph=0.25, dependency=0.0, symbol=0.5)

    scored = score_candidates(candidates, query, call_graph, dependency_graph, symbol_index, weights, max_hops=4)
    by_id = {c.method_id: c for c in scored}

    process_order = by_id["com.foo.OrderService#processOrder(Order)"]
    charge = by_id["com.foo.PaymentGateway#charge(double)"]

    # processOrder is the exact anchor: symbol match = 1.0, call-graph
    # proximity = 1.0 (it *is* the anchor). charge() is 1 hop away in the
    # call graph and shares no tokens with the query, so it should score
    # strictly lower on a tied semantic component.
    assert process_order.score.symbol == 1.0
    assert process_order.score.call_graph == 1.0
    assert process_order.score.composite > charge.score.composite


def test_weights_of_zero_fully_exclude_a_component():
    methods, call_graph, dependency_graph, symbol_index = _build_fixture()
    candidates = [_candidate(m, semantic=0.7) for m in methods]

    query = QueryAnalysis(
        raw_query="processOrder",
        intent=QueryIntentLabel.GENERAL_QA,
        anchor_symbols={"processOrder"},
        query_tokens={"process", "order"},
    )
    # Symbol weight is zero -- even though processOrder is an exact name
    # match, it must not influence the composite score at all.
    weights = ACORWeights(semantic=0.5, call_graph=0.5, dependency=0.0, symbol=0.0)

    scored = score_candidates(candidates, query, call_graph, dependency_graph, symbol_index, weights, max_hops=4)
    for candidate in scored:
        assert candidate.score.symbol >= 0.0  # still computed and reported...
    # ...but composite must equal the semantic+call_graph terms alone.
    for candidate in scored:
        expected = 0.5 * candidate.score.semantic + 0.5 * candidate.score.call_graph
        assert abs(candidate.score.composite - expected) < 1e-9


def test_source_term_match_reranks_method_containing_specific_bug_behavior():
    class_info = ClassInfo(
        "org.example.CharSequenceTranslator",
        "CharSequenceTranslator.java",
        "CharSequenceTranslator",
        "org.example",
        1,
        30,
        [],
    )
    methods = [
        MethodInfo(
            "org.example.CharSequenceTranslator#translate(CharSequence,int,Writer)",
            class_info.id,
            "translate",
            "translate(CharSequence,int,Writer)",
            class_info.file_path,
            1,
            10,
            ["public"],
        ),
        MethodInfo(
            "org.example.CharSequenceTranslator#translate(CharSequence,Writer)",
            class_info.id,
            "translate",
            "translate(CharSequence,Writer)",
            class_info.file_path,
            11,
            20,
            ["public"],
        ),
        MethodInfo(
            "org.example.CharSequenceTranslator#translate(CharSequence)",
            class_info.id,
            "translate",
            "translate(CharSequence)",
            class_info.file_path,
            21,
            30,
            ["public"],
        ),
    ]
    candidates = [
        Candidate(
            method_id=methods[0].id,
            class_id=class_info.id,
            name="translate",
            kind="method",
            file_path=class_info.file_path,
            start_line=1,
            end_line=10,
            snippet="public int translate(CharSequence input, int index, Writer out) { return 1; }",
            token_count=20,
            raw_semantic_score=0.9,
        ),
        Candidate(
            method_id=methods[1].id,
            class_id=class_info.id,
            name="translate",
            kind="method",
            file_path=class_info.file_path,
            start_line=11,
            end_line=20,
            snippet="public void translate(CharSequence input, Writer out) { // surrogate pairs }",
            token_count=20,
            raw_semantic_score=0.899,
        ),
        Candidate(
            method_id=methods[2].id,
            class_id=class_info.id,
            name="translate",
            kind="method",
            file_path=class_info.file_path,
            start_line=21,
            end_line=30,
            snippet="public String translate(CharSequence input) { return null; }",
            token_count=20,
            raw_semantic_score=0.0,
        ),
    ]
    query = QueryAnalysis(
        raw_query="fails on surrogate pairs",
        intent=QueryIntentLabel.BUG_EXPLANATION,
        anchor_symbols=set(),
        query_tokens={"surrogate", "pairs"},
    )
    call_graph = CallGraph.build(methods, [])
    dependency_graph = DependencyGraph.build([class_info], [])
    symbol_index = SymbolIndex.build([class_info], methods)

    scored = score_candidates(
        candidates,
        query,
        call_graph,
        dependency_graph,
        symbol_index,
        ACORWeights(),
    )

    assert scored[0].method_id == methods[1].id
    assert scored[0].score.symbol > 0.0


@pytest.mark.parametrize(
    "weights",
    [
        {"semantic": -0.1, "call_graph": 0.4, "dependency": 0.3, "symbol": 0.4},
        {"semantic": float("nan"), "call_graph": 0.0, "dependency": 0.0, "symbol": 1.0},
        {"semantic": 0.0, "call_graph": 0.0, "dependency": 0.0, "symbol": 0.0},
    ],
)
def test_invalid_acor_weights_are_rejected(weights):
    with pytest.raises(ValueError):
        ACORWeights(**weights)