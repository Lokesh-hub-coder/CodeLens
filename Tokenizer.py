"""
Token counting is used in three places in the pipeline -- candidate
preparation (each method chunk needs a "weight" before ranking), the
ranker's budget constraint, and the final assembled-context total reported
back to the user -- so it gets one shared, single-sourced implementation
rather than three slightly-different estimates drifting apart.

Exact-vs-approximate is a real tradeoff worth stating plainly: tiktoken
gives an exact count *for OpenAI's tokenizer specifically*, not for
Llama's or Qwen's (different BPE vocabularies segment code differently,
typically within +/-15% of each other for source code). Since the
LLM_PROVIDER is swappable, treat every count here as "a consistent proxy
good enough to rank and budget against," not a promise of the exact
number the eventually-called model will bill. tiktoken is used when
available because it is the best cheap approximation for code (it is
itself BPE, so it captures the same broad-strokes behavior any modern
tokenizer does: common identifiers become fewer tokens than random ones);
the chars/4 fallback is only there so the pipeline still runs in
environments where installing tiktoken isn't convenient (e.g. fully
offline setup, like the one this scaffold itself was built in).
"""
from __future__ import annotations

from functools import lru_cache


@lru_cache
def _get_tiktoken_encoding():
    try:
        import tiktoken

        return tiktoken.get_encoding("cl100k_base")
    except Exception:
        return None


def count_tokens(text: str) -> int:
    if not text:
        return 0
    encoding = _get_tiktoken_encoding()
    if encoding is not None:
        return len(encoding.encode(text, disallowed_special=()))
    # Heuristic fallback (~4 chars/token is the commonly cited rule of thumb
    # for English and source code alike). Deliberately conservative
    # (rounds up) so the ranker never *under*-estimates cost and blows the
    # budget when tiktoken is unavailable.
    return max(1, -(-len(text) // 4))