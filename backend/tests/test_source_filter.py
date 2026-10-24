from app.parsing.java_bridge import (
    CallEdge,
    ClassInfo,
    ImportEdge,
    MethodInfo,
    ParsedRepo,
)
from app.parsing.source_filter import filter_test_sources, is_test_path


def test_is_test_path():
    assert is_test_path("src/test/java/a/B.java")
    assert is_test_path("test/x.java")
    assert is_test_path("tests/x.java")
    assert is_test_path("mod/src/test/java/A.java")
    assert not is_test_path("src/main/java/org/test/C.java")


def test_filter_test_sources_removes_test_records_and_cross_edges():
    classes = [
        ClassInfo("main.A", "src/main/java/main/A.java", "A", "main", 1, 5, []),
        ClassInfo("main.B", "src/main/java/main/B.java", "B", "main", 1, 5, []),
        ClassInfo("tests.T", "src/test/java/tests/T.java", "T", "tests", 1, 5, []),
    ]
    methods = [
        MethodInfo("main.A#a()", "main.A", "a", "a()", classes[0].file_path, 1, 2, []),
        MethodInfo("main.B#b()", "main.B", "b", "b()", classes[1].file_path, 1, 2, []),
        MethodInfo("tests.T#t()", "tests.T", "t", "t()", classes[2].file_path, 1, 2, []),
    ]
    parsed = ParsedRepo(
        classes=classes,
        methods=methods,
        call_edges=[
            CallEdge("main.A#a()", "main.B#b()"),
            CallEdge("tests.T#t()", "main.A#a()"),
            CallEdge("main.A#a()", "tests.T#t()"),
        ],
        import_edges=[
            ImportEdge("main.A", "main.B"),
            ImportEdge("tests.T", "main.A"),
            ImportEdge("main.A", "tests.T"),
        ],
        unparsed_files=[
            "src/main/java/main/Broken.java",
            "src/test/java/tests/BrokenTest.java",
        ],
    )

    filtered = filter_test_sources(parsed)

    assert [item.id for item in filtered.classes] == ["main.A", "main.B"]
    assert [item.id for item in filtered.methods] == ["main.A#a()", "main.B#b()"]
    assert filtered.call_edges == [CallEdge("main.A#a()", "main.B#b()")]
    assert filtered.import_edges == [ImportEdge("main.A", "main.B")]
    assert filtered.unparsed_files == ["src/main/java/main/Broken.java"]
