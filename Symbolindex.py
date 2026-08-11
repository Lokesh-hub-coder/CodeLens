"""
Symbol index: identifier name -> declaration site(s).

This backs the "symbol matching" term of ACOR's composite score. When a
developer query names something concrete -- an exception type, a method a
stack trace points at, a class mentioned in a Jira ticket -- exact symbol
matches are strong evidence that semantic (embedding) similarity alone can
miss, especially for short or generically-worded queries where cosine
similarity is noisy. CodeRAG-Bench's own findings (Wang et al., 2025) are
part of the motivation here: they report retrievers specifically struggle
when a query and its relevant code have limited lexical overlap, which is
exactly the failure mode exact/fuzzy symbol matching is cheap insurance
against.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from app.parsing.java_bridge import ClassInfo, MethodInfo

# Splits "getUserAccountById" -> ["get", "User", "Account", "By", "Id"] and
# "MAX_RETRY_COUNT" -> ["MAX", "RETRY", "COUNT"], covering the two
# identifier conventions actually used in Java source.
_CAMEL_CASE_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


class SymbolKind(str, Enum):
    CLASS = "class"
    METHOD = "method"


@dataclass(frozen=True)
class SymbolLocation:
    symbol_id: str
    name: str
    kind: SymbolKind
    file_path: str
    start_line: int
    end_line: int


def tokenize_identifier(identifier: str) -> set[str]:
    """Split a camelCase/PascalCase/SNAKE_CASE identifier into lowercase
    subtokens for fuzzy (Jaccard) matching."""
    parts = re.split(r"[_\W]+", identifier)
    subtokens: list[str] = []
    for part in parts:
        if not part:
            continue
        subtokens.extend(_CAMEL_CASE_BOUNDARY.split(part))
    return {t.lower() for t in subtokens if t}


class SymbolIndex:
    def __init__(self) -> None:
        self._exact: dict[str, list[SymbolLocation]] = {}
        self._tokens: dict[str, set[str]] = {}  # symbol_id -> subtoken set

    @classmethod
    def build(cls, classes: list[ClassInfo], methods: list[MethodInfo]) -> "SymbolIndex":
        index = cls()
        for c in classes:
            index._add(
                SymbolLocation(
                    symbol_id=c.id,
                    name=c.name,
                    kind=SymbolKind.CLASS,
                    file_path=c.file_path,
                    start_line=c.start_line,
                    end_line=c.end_line,
                )
            )
        for m in methods:
            index._add(
                SymbolLocation(
                    symbol_id=m.id,
                    name=m.name,
                    kind=SymbolKind.METHOD,
                    file_path=m.file_path,
                    start_line=m.start_line,
                    end_line=m.end_line,
                )
            )
        return index

    def _add(self, location: SymbolLocation) -> None:
        self._exact.setdefault(location.name, []).append(location)
        self._tokens[location.symbol_id] = tokenize_identifier(location.name)

    def exact_lookup(self, name: str) -> list[SymbolLocation]:
        return self._exact.get(name, [])

    def match_score(self, symbol_id: str, symbol_name: str, query_tokens: set[str]) -> float:
        """1.0 for an exact name match against something mentioned in the
        query, else Jaccard overlap between subtoken sets, else 0.0.

        query_tokens should already be lowercased subtokens extracted from
        the developer query by query_understanding.py (candidate
        identifiers it found via regex, camel-case-split the same way).
        """
        if not query_tokens:
            return 0.0
        if symbol_name.lower() in query_tokens or tokenize_identifier(symbol_name) == query_tokens:
            return 1.0

        candidate_tokens = self._tokens.get(symbol_id) or tokenize_identifier(symbol_name)
        if not candidate_tokens:
            return 0.0

        intersection = candidate_tokens & query_tokens
        if not intersection:
            return 0.0
        union = candidate_tokens | query_tokens
        return len(intersection) / len(union)

    def __len__(self) -> int:
        return len(self._tokens)