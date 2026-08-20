"""context_builder - assembles the final text block passed to the LLM.

Groups selected candidates by file, sorts files by max composite score
(most relevant first), then formats each snippet with a header showing
file path, line range, method name, and relevance score.
"""
from __future__ import annotations
from pathlib import Path
from app.acor.scorer import ScoredCandidate


def build_context(
    selected:  list[ScoredCandidate],
    repo_root: Path,
) -> str:
    """Return a formatted multi-file context string for the LLM prompt.

    Format per file:
        // === com/foo/OrderService.java ===
        // Lines 10-25 | OrderService.processOrder | relevance 0.87
        <source snippet>

        // Lines 30-45 | ...
        <source snippet>
    """
    if not selected:
        return ""

    # Group by file, track max composite per file for ordering
    from collections import defaultdict
    by_file: dict[str, list[ScoredCandidate]] = defaultdict(list)
    max_score: dict[str, float] = defaultdict(float)

    for c in selected:
        by_file[c.file_path].append(c)
        if c.score.composite > max_score[c.file_path]:
            max_score[c.file_path] = c.score.composite

    # Sort files: highest max-score first
    sorted_files = sorted(by_file.keys(), key=lambda f: max_score[f], reverse=True)

    parts: list[str] = []
    for file_path in sorted_files:
        chunks = sorted(by_file[file_path], key=lambda c: c.start_line)
        parts.append(f"// === {file_path} ===")
        for c in chunks:
            header = (
                f"// Lines {c.start_line}-{c.end_line} "
                f"| {c.name} "
                f"| relevance {c.score.composite:.3f}"
            )
            parts.append(header)
            parts.append(c.snippet)
            parts.append("")  # blank line between chunks

    return "\n".join(parts)