"""
Query Understanding module -- the box right after "Developer Query" in the
synopsis' architecture diagram.

Two jobs, kept in one place because they're both cheap, regex/keyword-level
analyses of the same input string:

1. Intent classification: which flavor of question is this? A "why does X
   throw Y" query benefits from prioritizing call-graph proximity to X
   (trace the failure path); a "where is X implemented" query benefits
   from symbol matching over graph proximity. Feeding intent into ACOR's
   weighting is the "adapting retrieval strategies to different categories
   of developer queries" gap the synopsis' background section calls out
   in RepoCoder/CodeRAG-Bench-style retrieval.
2. Symbol/anchor extraction: pull out identifier-shaped tokens (method
   calls, CamelCase/PascalCase names, exception types) so call_graph.py and
   symbol_index.py have concrete anchors to measure proximity/overlap
   against, instead of only a bag of words.

This is intentionally a rule-based MVP, not a trained classifier -- see the
"extra to think about" note in the project README about upgrading this
with labeled query data once the team has real usage logs.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.api.schemas import QueryIntentLabel
from app.graphs.symbol_index import tokenize_identifier

# Ordered rules: first matching rule wins. Order encodes priority when a
# query matches more than one pattern, e.g. "why is getUser() throwing a
# NullPointerException" matches both bug and root-cause keywords -- root
# cause is checked first because "why" is the stronger signal of the two.
_INTENT_KEYWORDS: list[tuple[QueryIntentLabel, tuple[str, ...]]] = [
    (
        QueryIntentLabel.ROOT_CAUSE_ANALYSIS,
        ("why does", "why is", "why did", "root cause", "what causes", "what's causing"),
    ),
    (
        QueryIntentLabel.BUG_EXPLANATION,
        ("exception", "error", "bug", "fails", "failing", "crash", "stack trace", "traceback"),
    ),
    (
        QueryIntentLabel.REFACTORING,
        ("refactor", "clean up", "simplify", "improve", "restructure", "rename"),
    ),
    (
        QueryIntentLabel.FEATURE_LOCATION,
        ("where is", "where does", "which class", "which file", "find the", "locate"),
    ),
]

# Matches Java-flavored identifiers: PascalCase/camelCase words, optionally
# followed by "(" (a call/definition) -- e.g. "OrderService", "processOrder(",
# "NullPointerException".
_IDENTIFIER_PATTERN = re.compile(r"\b([A-Za-z_$][A-Za-z0-9_$]*)\s*(\()?")

# Common English words that happen to be capitalized at a sentence start, or
# lowercase words with no camel/Pascal structure -- filtered out so we don't
# treat "The" or "throws" as a code symbol.
_STOPWORDS = {
    "the", "a", "an", "is", "are", "why", "what", "where", "which", "when",
    "how", "does", "did", "do", "this", "that", "it", "and", "or", "in",
    "on", "for", "to", "of", "throws", "throwing", "error", "exception",
}


@dataclass(frozen=True)
class QueryAnalysis:
    raw_query: str
    intent: QueryIntentLabel
    anchor_symbols: set[str]  # identifier-shaped tokens found verbatim, e.g. {"processOrder", "OrderService"}
    query_tokens: set[str]  # lowercased subtokens for fuzzy symbol matching, e.g. {"process", "order", "service"}


def classify_intent(query: str) -> QueryIntentLabel:
    lowered = query.lower()
    for intent, keywords in _INTENT_KEYWORDS:
        if any(keyword in lowered for keyword in keywords):
            return intent
    return QueryIntentLabel.GENERAL_QA


def extract_anchor_symbols(query: str) -> set[str]:
    """Pull out tokens that look like Java identifiers rather than English
    prose: CamelCase/PascalCase words, anything immediately followed by
    "(" (so lowercase "process(" is kept even though "process" alone would
    look like an ordinary English word), or anything immediately preceded
    by "." (a qualified reference like "OrderService.validate" or
    "user.getEmail" -- the member name after the dot is meaningful even
    when, by itself, it has neither call syntax nor case structure)."""
    anchors: set[str] = set()
    for match in _IDENTIFIER_PATTERN.finditer(query):
        token, is_call = match.group(1), match.group(2)
        if token.lower() in _STOPWORDS:
            continue
        preceded_by_dot = match.start() > 0 and query[match.start() - 1] == "."
        looks_like_identifier = is_call is not None or _has_case_structure(token) or preceded_by_dot
        if looks_like_identifier and len(token) > 1:
            anchors.add(token)
    return anchors


def _has_case_structure(token: str) -> bool:
    """True for camelCase, PascalCase, or SCREAMING_SNAKE_CASE -- i.e.
    tokens that are very unlikely to be plain English words."""
    has_internal_upper = any(c.isupper() for c in token[1:])
    is_all_caps_with_underscore = token.isupper() and "_" in token
    return has_internal_upper or is_all_caps_with_underscore


def analyze_query(query: str) -> QueryAnalysis:
    anchors = extract_anchor_symbols(query)
    tokens: set[str] = set()
    for anchor in anchors:
        tokens |= tokenize_identifier(anchor)
    # Also fold in plain lowercase content words (minus stopwords) so
    # symbol_index.py's Jaccard matching has something to work with even
    # when the query names no explicit identifier, e.g. "order processing
    # timeout" should still nudge towards a class named OrderProcessor.
    for word in re.findall(r"[A-Za-z]+", query.lower()):
        if word not in _STOPWORDS and len(word) > 2:
            tokens.add(word)

    return QueryAnalysis(
        raw_query=query,
        intent=classify_intent(query),
        anchor_symbols=anchors,
        query_tokens=tokens,
    )