"""
Re-export of count_tokens from the canonical retrieval.tokenizer module.

The ACOR layer (scorer, ranker, context_builder) imports count_tokens from
app.acor.tokenizer so that the acor/ package is self-contained from a
reader's perspective. The actual implementation lives in
retrieval/tokenizer.py -- see that module's docstring for the tiktoken vs.
heuristic tradeoff.
"""
from app.retrieval.tokenizer import count_tokens  # noqa: F401

__all__ = ["count_tokens"]
