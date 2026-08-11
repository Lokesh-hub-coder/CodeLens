"""
ACOR step 3/3: turn a RankedSelection into the actual text block the LLM
sees -- the "Optimized Repository Context" box in the synopsis' pipeline
diagram.

Kept separate from ranker.py because formatting concerns (how to group and
label snippets so the LLM can cite them back accurately) are orthogonal to
selection concerns (which snippets survive the budget), and the team will
likely iterate on formatting/prompt-engineering far more often than on the
selection algorithm during the Week 11-12 evaluation phase.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.acor.ranker import RankedSelection
from app.acor.scorer import ScoredCandidate
from app.acor.tokenizer import count_tokens


@dataclass(frozen=True)
class BuiltContext:
    text: str
    total_tokens: int
    included: list[ScoredCandidate]


def build_context(selection: RankedSelection) -> BuiltContext:
    """Group selected candidates by file (methods from the same file read
    more coherently together than interleaved by score) and render each
    with a header the LLM -- and a developer reading the debug view -- can
    use to locate it back in the real repository.
    """
    by_file: dict[str, list[ScoredCandidate]] = {}
    for candidate in selection.selected:
        by_file.setdefault(candidate.file_path, []).append(candidate)

    blocks: list[str] = []
    for file_path, members in by_file.items():
        members.sort(key=lambda c: c.start_line)
        blocks.append(f"// ===== {file_path} =====")
        for member in members:
            blocks.append(
                f"// {member.name}  (lines {member.start_line}-{member.end_line}, "
                f"relevance={member.score.composite:.2f})"
            )
            blocks.append(member.snippet)
            blocks.append("")  # blank line between snippets in the same file

    text = "\n".join(blocks).strip()
    return BuiltContext(
        text=text,
        # Recomputed rather than reused from selection.total_tokens: the
        # per-candidate token_count summed by the ranker doesn't include
        # the header/separator lines added here, and that overhead is
        # exactly the kind of thing the evaluation chapter should be
        # measuring honestly rather than undercounting.
        total_tokens=count_tokens(text),
        included=selection.selected,
    )


def estimate_naive_baseline_tokens(all_file_paths: set[str], file_token_counts: dict[str, int]) -> int:
    """Approximate how many tokens a naive "just dump every file touched by
    a keyword/semantic hit" baseline would have used, so the API response
    can report the token-reduction ratio the synopsis' evaluation plan
    (Section 7) asks for: total_context_tokens / naive_baseline_tokens.

    file_token_counts should be precomputed once at index time (whole-file
    token counts), not recomputed per query -- see routes.py.
    """
    return sum(file_token_counts.get(path, 0) for path in all_file_paths)