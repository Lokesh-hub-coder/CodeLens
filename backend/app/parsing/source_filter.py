"""Post-parse filters for repository source files."""
from __future__ import annotations

from pathlib import PurePosixPath

from app.parsing.java_bridge import ParsedRepo


def is_test_path(rel_path: str) -> bool:
    """Return whether a relative source path belongs to a test source tree."""
    segments = PurePosixPath(rel_path.replace("\\", "/")).parts
    if segments and segments[0] in {"test", "tests"}:
        return True
    return any(
        left == "src" and right == "test"
        for left, right in zip(segments, segments[1:])
    )


def filter_test_sources(parsed: ParsedRepo) -> ParsedRepo:
    """Drop test-source records and graph edges that touch removed records."""
    classes = [item for item in parsed.classes if not is_test_path(item.file_path)]
    methods = [item for item in parsed.methods if not is_test_path(item.file_path)]
    class_ids = {item.id for item in classes}
    method_ids = {item.id for item in methods}

    return ParsedRepo(
        classes=classes,
        methods=methods,
        call_edges=[
            edge
            for edge in parsed.call_edges
            if edge.caller_id in method_ids and edge.callee_id in method_ids
        ],
        import_edges=[
            edge
            for edge in parsed.import_edges
            if edge.from_class_id in class_ids and edge.to_class_id in class_ids
        ],
        unparsed_files=[
            path for path in parsed.unparsed_files if not is_test_path(path)
        ],
    )
