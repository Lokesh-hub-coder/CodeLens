from pathlib import Path
from types import SimpleNamespace

from app.core.candidate_pipeline import generate_root_cause_candidates
from app.graphs.call_graph import CallGraph
from app.graphs.dependency_graph import DependencyGraph
from app.graphs.symbol_index import SymbolIndex
from app.parsing.java_bridge import CallEdge, ClassInfo, MethodInfo
from app.retrieval.query_understanding import analyze_query
from app.config import Settings


class _EmptyVectorStore:
    def search(self, _vector, top_k):
        assert top_k > 0
        return []


class _EmbeddingModel:
    def encode_one(self, _text):
        return [0.0]


def _method(method_id, class_id, name, file_path, start_line):
    return MethodInfo(
        id=method_id,
        class_id=class_id,
        name=name,
        signature=f"{name}()",
        file_path=file_path,
        start_line=start_line,
        end_line=start_line + 2,
        modifiers=["public"],
    )


def test_class_anchor_adds_related_nested_methods_and_call_graph_neighbors(tmp_path):
    source_files = {
        "time/FastDateFormat.java": ["class FastDateFormat {", "  void format() {", "  }", "}"],
        "time/FastDatePrinter.java": ["class FastDatePrinter {", "  void print() {", "  }", "}"],
        "time/TimeZoneNameRule.java": ["class TimeZoneNameRule {", "  void appendTo() {", "  }", "}"],
        "util/TimeZoneHelper.java": ["class TimeZoneHelper {", "  void resolve() {", "  }", "}"],
    }
    for relative_path, lines in source_files.items():
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")

    classes = [
        ClassInfo(
            "org.example.time.FastDateFormat",
            "time/FastDateFormat.java",
            "FastDateFormat",
            "org.example.time",
            1,
            4,
            [],
        ),
        ClassInfo(
            "org.example.time.FastDatePrinter",
            "time/FastDatePrinter.java",
            "FastDatePrinter",
            "org.example.time",
            1,
            4,
            [],
        ),
        ClassInfo(
            "org.example.time.FastDatePrinter.TimeZoneNameRule",
            "time/TimeZoneNameRule.java",
            "TimeZoneNameRule",
            "org.example.time",
            1,
            4,
            [],
        ),
        ClassInfo(
            "org.example.util.TimeZoneHelper",
            "util/TimeZoneHelper.java",
            "TimeZoneHelper",
            "org.example.util",
            1,
            4,
            [],
        ),
    ]
    methods = [
        _method(
            "org.example.time.FastDateFormat#format()",
            "org.example.time.FastDateFormat",
            "format",
            "time/FastDateFormat.java",
            1,
        ),
        _method(
            "org.example.time.FastDatePrinter#print()",
            "org.example.time.FastDatePrinter",
            "print",
            "time/FastDatePrinter.java",
            1,
        ),
        _method(
            "org.example.time.FastDatePrinter.TimeZoneNameRule#appendTo()",
            "org.example.time.FastDatePrinter.TimeZoneNameRule",
            "appendTo",
            "time/TimeZoneNameRule.java",
            1,
        ),
        _method(
            "org.example.util.TimeZoneHelper#resolve()",
            "org.example.util.TimeZoneHelper",
            "resolve",
            "util/TimeZoneHelper.java",
            1,
        ),
    ]
    call_graph = CallGraph.build(
        methods,
        [
            CallEdge(
                "org.example.time.FastDateFormat#format()",
                "org.example.util.TimeZoneHelper#resolve()",
            )
        ],
    )
    repo_index = SimpleNamespace(
        repo_root=Path(tmp_path),
        symbol_index=SymbolIndex.build(classes, methods),
        call_graph=call_graph,
        dependency_graph=DependencyGraph.build(classes, []),
        vector_store=_EmptyVectorStore(),
    )

    candidates = generate_root_cause_candidates(
        analyze_query("FastDateFormat uses a timezone"),
        repo_index,
        _EmbeddingModel(),
        Settings(),
    )
    candidate_ids = {candidate.method_id for candidate in candidates}

    assert "org.example.time.FastDateFormat#format()" in candidate_ids
    assert "org.example.time.FastDatePrinter#print()" in candidate_ids
    assert "org.example.time.FastDatePrinter.TimeZoneNameRule#appendTo()" in candidate_ids
    assert "org.example.util.TimeZoneHelper#resolve()" in candidate_ids
